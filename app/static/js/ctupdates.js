/* Updates in VM's & containers (pakketten en app-versies), op de pagina Updates. */
(function () {
  var e = PF.esc;
  var root = document.getElementById('ctupd');
  var data = null, onlyUpd = true;
  try { onlyUpd = localStorage.getItem('pvef_ctupd_all') !== '1'; } catch (x) { /* niets */ }

  function hasUpd(x) { return (x.count || 0) > 0 || (x.apps || []).some(function (a) { return a.update; }); }
  function mainApp(x) { var a = (x.apps || []).filter(function (a) { return a.update; })[0] || (x.apps || []).filter(function (a) { return a.main; })[0]; return a || (x.apps || [])[0] || null; }
  function g(x) { return { server: x.server, node: x.node, type: 'lxc', vmid: x.vmid, name: x.name, status: x.status }; }

  function pkgCell(x) {
    if (x.error) return '<span class="small c-warn" title="' + e(x.error) + '">fout</span>';
    if (x.count === null || x.count === undefined) return '<span class="muted small" title="Geen bekende pakketbeheerder">—</span>';
    if (!x.count) return '<span class="c-ok small">✓ bijgewerkt</span>';
    return '<span class="c-warn">' + x.count + '</span>' + (x.security ? ' <span class="c-crit small">(' + x.security + ' security)</span>' : '');
  }
  function appCell(x) {
    var a = mainApp(x);
    if (!a) return '<span class="muted small">—</span>';
    var more = (x.apps || []).length > 1 ? ' <span class="small muted" title="' + e(x.apps.map(function (y) { return y.name + ' ' + (y.installed || '?') + (y.latest ? ' → ' + y.latest : ''); }).join('\n')) + '">+' + (x.apps.length - 1) + '</span>' : '';
    if (a.update) return '<span class="mono small">' + e(a.name) + '</span> <span class="c-warn mono small">' + e(a.installed || '?') + ' → ' + e(a.latest) + '</span>' + more;
    if (a.latest === null || a.latest === undefined) return '<span class="mono small">' + e(a.name) + ' ' + e(a.installed || '') + '</span> <span class="muted small" title="Kon de nieuwste versie niet ophalen bij GitHub">?</span>' + more;
    return '<span class="mono small">' + e(a.name) + ' ' + e(a.installed || '') + '</span> <span class="c-ok small">✓</span>' + more;
  }

  // Tijdstip van de laatste controle: vandaag "om 18:12", anders met datum
  function checkedText(t) {
    var d = new Date(t * 1000), now = new Date(), hm = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    if (d.toDateString() === now.toDateString()) return 'om ' + hm;
    var y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return 'gisteren om ' + hm;
    return d.getDate() + ' ' + ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'][d.getMonth()] + ' om ' + hm;
  }
  function etaText(p) {
    if (!p.done || !p.total || p.done >= p.total || p.phase !== 'containers') return '';
    var per = ((p.now || Date.now() / 1000) - p.started) / p.done, left = Math.round(per * (p.total - p.done));
    return ' <span class="ctp-dot">·</span> nog ± ' + (left < 60 ? 'minder dan een minuut' : Math.round(left / 60) + ' min');
  }
  function progressBlock(p) {
    var pct = p && p.total ? Math.round(100 * p.done / p.total) : 0;
    var now = !p ? 'Voorbereiden…'
      : p.phase === 'apps' ? 'App-versies vergelijken met GitHub…'
      : p.current ? 'Nu bezig: <b>' + e(p.current.name) + '</b> <span class="mono">' + p.current.vmid + '</span> op <span class="mono">' + e(p.current.node) + '</span> — pakketlijst ophalen'
      : (p.done ? 'Volgende container…' : 'Verbinden met de node…');
    return '<div class="ctp-bar' + (p && p.total ? '' : ' indet') + '"><i style="width:' + (p && p.total ? pct : 30) + '%"></i></div>' +
      '<div class="ctp-now small muted">' + now + '</div>';
  }

  function serverBlock(name, s) {
    var guests = Object.keys(data.guests).map(function (k) { return data.guests[k]; }).filter(function (x) { return x.server === name; });
    var nodeErr = Object.keys(s.nodes || {}).filter(function (n) { return !s.nodes[n].ok; }).map(function (n) {
      return '<div class="small c-warn" style="margin-top:4px"><span class="mono">' + e(n) + '</span>: ' + e(s.nodes[n].error) + '</div>';
    }).join('');
    var list = guests.filter(function (x) { return !onlyUpd || hasUpd(x); }).sort(function (a, b) { return (hasUpd(b) ? 1 : 0) - (hasUpd(a) ? 1 : 0) || a.vmid - b.vmid; });
    var n = guests.filter(hasUpd).length;
    var p = s.checking ? s.progress : null;
    var bad = (s.errors || 0) + Object.keys(s.nodes || {}).filter(function (k) { return !s.nodes[k].ok; }).length;
    var dot = ' <span class="ctp-dot">·</span> ';
    var status = s.checking
      ? '<span class="ctp-pill"><span class="spinner" aria-hidden="true"></span>Controleren' + (p && p.total ? ' · ' + Math.min(p.done + (p.current ? 1 : 0), p.total) + ' van ' + p.total : '…') + '</span>'
      : (s.checked ? '<span class="c-ok">✓ Gecontroleerd ' + e(checkedText(s.checked)) + '</span>' : '<span class="muted">Nog niet gecontroleerd</span>');
    var head = '<div class="card-head' + (s.checking ? ' ctp-head' : '') + '"><h2>' + e(s.group) + '</h2><span class="small muted ctp-meta">' + status + dot +
      guests.length + ' containers' + (n ? dot + '<span class="c-warn">' + n + ' met updates</span>' : '') +
      (!s.checking && bad ? dot + '<span class="c-crit">' + bad + ' niet bereikbaar</span>' : '') +
      (p ? etaText(p) : '') + '</span>' +
      '<div class="end"><button type="button" class="btn ghost" data-ct="check" data-s="' + e(name) + '"' + (s.checking ? ' disabled' : '') + '>' + (s.checking ? 'Bezig…' : 'Nu controleren') + '</button>' +
      '<button type="button" class="btn ghost" data-ct="setup" data-s="' + e(name) + '">Instellingen</button></div></div>' +
      (s.checking ? progressBlock(p) : '');
    var rows = list.map(function (x) {
      var a = mainApp(x);
      return '<tr><td><a href="' + PF.guestUrl(x.server, x.node, 'lxc', x.vmid) + '" style="font-weight:500">' + e(x.name) + '</a> <span class="mono muted small">' + x.vmid + '</span>' +
        (x.stale ? '<div class="small muted">gestopt · laatst bekend</div>' : '') + '</td>' +
        '<td class="mono small">' + e(x.node) + '</td><td class="small muted">' + e(x.os || '—') + '</td>' +
        '<td>' + pkgCell(x) + '</td><td>' + appCell(x) + '</td>' +
        '<td><div class="rowacts">' +
        ((x.count || 0) > 0 && x.status === 'running' ? '<button type="button" class="btn primary" data-ct="os" data-k="' + e(x.server + '/' + x.vmid) + '">Bijwerken</button>' : '') +
        (a && a.update && x.status === 'running' ? '<button type="button" class="btn blue" data-ct="app" data-k="' + e(x.server + '/' + x.vmid) + '">App bijwerken</button>' : '') +
        '</div></td></tr>';
    }).join('');
    var body = list.length ? '<div class="tablewrap"><table class="tbl" style="min-width:860px"><thead><tr><th>Container</th><th>Node</th><th>Systeem</th><th>Pakketten</th><th>App</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      : '<div class="small muted" style="padding:12px 18px">' + (guests.length ? 'Alle containers zijn bijgewerkt.' : (s.checking ? 'Bezig…' : 'Nog geen uitslag.')) + '</div>';
    return '<section class="card" style="overflow:hidden">' + head + (nodeErr ? '<div style="padding:8px 18px 0">' + nodeErr + '</div>' : '') + body + '</section>';
  }

  function render(d) {
    data = d;
    var names = Object.keys(d.servers);
    var on = names.filter(function (n) { return d.servers[n].enabled; });
    var off = names.filter(function (n) { return !d.servers[n].enabled; });
    var html = '<div class="pagehead" style="margin-top:10px"><div><h2 style="font-size:20px">Updates in VM\'s &amp; containers</h2>' +
      '<p class="muted" style="margin:4px 0 0">Pakketupdates in de containers en nieuwe versies van de apps (community-scripts): <span>' + schedText(d) + '</span>' +
      (d.next ? ' <span class="small">(volgende: ' + e(PF.when(d.next)) + ')</span>' : '') + '.' +
      (d.admin ? ' <a href="#" data-ct="schedule">Wijzigen</a>' : '') + '</p></div>' +
      (on.length ? '<div class="end" style="flex-wrap:wrap;gap:8px">' + bulkButtons(d) + '<label class="check" style="margin:0"><input type="checkbox" id="ct-only"' + (onlyUpd ? ' checked' : '') + '>Alleen met updates</label></div>' : '') + '</div>';
    html += on.map(function (n) { return serverBlock(n, d.servers[n]); }).join('');
    if (off.length) {
      html += '<section class="card pad"><h2 class="small-title">' + (on.length ? 'Nog niet ingesteld' : 'Eenmalig instellen') + '</h2>' +
        '<p class="muted" style="margin:0 0 10px;line-height:1.55">Proxmox weet niet wat er in een container geïnstalleerd is. PVE Fleet kijkt daarom via SSH op de nodes, met een sleutel die alleen één controlescript mag starten. Dat script installeert niets: het kijkt alleen wat er bijgewerkt kan worden.</p>' +
        '<div class="list">' + off.map(function (n) {
          return '<div class="row"><span>' + e(d.servers[n].group) + ' <span class="small muted">' + (d.servers[n].kind === 'cluster' ? 'cluster' : 'losse node') + '</span></span><button type="button" class="btn primary" data-ct="setup" data-s="' + e(n) + '">Instellen</button></div>';
        }).join('') + '</div></section>';
    }
    html += '<p class="small muted" style="margin:0">VM\'s worden nog niet gecontroleerd. Docker-containers binnen een VM of container ook niet.</p>';
    root.innerHTML = '<div style="display:flex;flex-direction:column;gap:16px;margin-top:6px">' + html + '</div>';
  }
  var bulkRunning = false;
  function load() {
    return PF.getJSON('/api/guest-updates').then(function (d) {
      render(d);
      if (d.admin) PF.getJSON('/api/guest-updates/upgrade-all').then(function (r) {
        var run = !!(r.job && r.job.status === 'running');
        if (run !== bulkRunning) { bulkRunning = run; render(data); }
      }, function () {});
    }, function () {});
  }

  // ------------------------------------------------------------ alles bijwerken
  function pkgTargets(d) {
    return Object.keys(d.guests).map(function (k) { return d.guests[k]; }).filter(function (x) {
      return (x.count || 0) > 0 && x.status === 'running' && d.servers[x.server] && d.servers[x.server].enabled;
    }).sort(function (a, b) { return a.vmid - b.vmid; });
  }
  function appTargets(d) {
    return Object.keys(d.guests).map(function (k) { return d.guests[k]; }).filter(function (x) {
      var m = mainApp(x);
      return m && m.update && x.status === 'running' && d.servers[x.server] && d.servers[x.server].enabled;
    }).sort(function (a, b) { return a.vmid - b.vmid; });
  }
  function bulkButtons(d) {
    if (!d.admin) return '';
    if (bulkRunning) return '<button type="button" class="btn primary" data-ct="bulkview"><span class="spinner" style="display:inline-block;width:11px;height:11px;vertical-align:-1px;margin-right:6px"></span>Containers worden bijgewerkt · bekijken</button>';
    var p = pkgTargets(d).length, a = appTargets(d).length;
    return (p ? '<button type="button" class="btn primary" data-ct="allpkg">Alle containers bijwerken (' + p + ')</button>' : '') +
      (a ? '<button type="button" class="btn blue" data-ct="allapps">Alle apps bijwerken (' + a + ')</button>' : '');
  }
  function allPackages() {
    var list = pkgTargets(data);
    if (!list.length) return;
    var rows = list.map(function (x) {
      return '<label class="check" style="margin:0 0 6px"><input type="checkbox" data-bulk="' + e(x.server + '/' + x.vmid) + '" checked>' +
        '<span><span class="mono">' + x.vmid + '</span> ' + e(x.name) + ' <span class="small muted">· ' + x.count + ' pakket' + (x.count === 1 ? '' : 'ten') +
        (x.security ? ', ' + x.security + ' security' : '') + '</span></span></label>';
    }).join('');
    PFA.modal({ title: 'Alle containers bijwerken', wide: true,
      body: '<p style="margin:0 0 8px;line-height:1.55">PVE Fleet werkt deze containers één voor één bij, zonder console: <span class="mono">apt-get dist-upgrade</span> (of apk/dnf). Je eigen configuratiebestanden blijven staan. Vink uit wat je wilt overslaan.</p>' +
        '<div style="max-height:260px;overflow:auto;padding:4px 2px;margin-bottom:6px">' + rows + '</div>' +
        '<p class="small muted" style="margin:0">Hiervoor gebruikt PVE Fleet de app-sleutel (pagina Apps). App-updates zitten hier niet in: die stellen soms vragen; gebruik daarvoor <em>Alle apps bijwerken</em>.</p>',
      fields: [{ name: 'snap', label: 'Per container eerst een snapshot maken (aanbevolen)', type: 'checkbox', value: true }],
      confirm: 'Bijwerken', kind: 'primary',
      validate: function () { return document.querySelectorAll('.modal [data-bulk]:checked').length ? null : 'Kies minstens één container'; },
      submit: function (v) {
        var targets = Array.prototype.map.call(document.querySelectorAll('.modal [data-bulk]:checked'), function (c) {
          var p = c.getAttribute('data-bulk').split('/'); return { server: p[0], vmid: +p[1] };
        });
        return PF.postJSON('/api/guest-updates/upgrade-all', { targets: targets, snapshot: v.snap }).then(function () {
          setTimeout(followBulk, 50);
        });
      } });
  }
  var BSTATE = { wait: 'wacht', busy: 'bezig…', ok: 'gelukt', fail: 'mislukt', skip: 'overgeslagen' };
  function followBulk() {
    var timer = null;
    PFA.modal({ title: 'Containers bijwerken', wide: true,
      body: '<div id="bk-items" class="list" style="margin-bottom:8px"></div><pre class="log" id="bk-log" style="max-height:40vh">Bezig…</pre><div id="bk-st" class="small" style="margin-top:8px"></div>',
      confirm: null, cancelLabel: 'Sluiten',
      onRender: function () {
        function tick() {
          PF.getJSON('/api/guest-updates/upgrade-all').then(function (r) {
            var j = r.job, el = document.getElementById('bk-log');
            if (!el || !j) { clearInterval(timer); return; }
            document.getElementById('bk-items').innerHTML = j.items.map(function (i) {
              var cls = i.state === 'ok' ? 'c-ok' : (i.state === 'fail' ? 'c-crit' : (i.state === 'busy' ? '' : 'muted'));
              return '<div class="row"><span><span class="mono">' + i.vmid + '</span> ' + e(i.name || '') + '</span><span class="small ' + cls + '">' +
                e(BSTATE[i.state] || i.state) + (i.msg && i.state !== 'ok' ? ': ' + e(i.msg) : (i.reboot ? ' · herstart nodig' : '')) + '</span></div>';
            }).join('');
            var atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
            el.textContent = (j.log || []).join('\n');
            if (atEnd) el.scrollTop = el.scrollHeight;
            var st = document.getElementById('bk-st');
            if (j.status === 'running') st.innerHTML = '<span class="spinner" style="display:inline-block;vertical-align:-3px"></span> Bezig. Je mag dit venster sluiten; het bijwerken gaat door.';
            else {
              clearInterval(timer);
              var ok = j.items.filter(function (i) { return i.state === 'ok'; }).length, bad = j.items.filter(function (i) { return i.state === 'fail'; }).length;
              st.innerHTML = '<span class="' + (bad ? 'c-warn' : 'c-ok') + '">Klaar: ' + ok + ' bijgewerkt' + (bad ? ', ' + bad + ' mislukt' : '') + '.</span> PVE Fleet kijkt nu opnieuw welke updates er nog zijn.';
              setTimeout(load, 2500);
            }
          }, function () {});
        }
        tick(); timer = setInterval(tick, 1500);
      } }).then(function () { clearInterval(timer); load(); });
  }
  function allApps() {
    var list = appTargets(data);
    if (!list.length) return;
    PFA.modal({ title: 'Alle apps bijwerken', wide: true,
      body: '<p style="margin:0 0 8px;line-height:1.55">De update-scripts van community-scripts stellen soms vragen; daarom gaan deze één voor één, elk in een eigen console. PVE Fleet voert <span class="mono">update</span> vanzelf in. Sluit je een console, dan volgt de volgende app.</p>' +
        '<ol style="margin:0;padding-left:20px;line-height:1.7">' + list.map(function (x) { var m = mainApp(x); return '<li><span class="mono">' + x.vmid + '</span> ' + e(x.name) + ': ' + e(m.name) + ' ' + e(m.installed || '') + ' → ' + e(m.latest || '') + '</li>'; }).join('') + '</ol>',
      confirm: 'Beginnen', kind: 'primary' }).then(function (ok) {
      if (!ok) return;
      var i = 0;
      (function next() {
        if (i >= list.length) { PFA.toast('Alle apps zijn langsgeweest'); load(); return; }
        var x = list[i++], m = mainApp(x);
        PFA.toast('App ' + i + ' van ' + list.length + ': ' + x.name);
        PFA.guestUpdate(g(x), 'app', { app: m && m.name, installed: m && m.installed, latest: m && m.latest }, function () {
          PF.postJSON('/api/guest-updates/' + encodeURIComponent(x.server) + '/check').then(function () {}, function () {});
          setTimeout(next, 600);
        });
      })();
    });
  }
  function schedText(d) {
    var sc = d.schedule || { mode: 'interval', hours: d.hours };
    var t = sc.mode === 'daily' ? 'elke dag om ' + sc.at + ' gecontroleerd' : (sc.hours === 1 ? 'elk uur gecontroleerd' : 'elke ' + sc.hours + ' uur gecontroleerd');
    return t;
  }
  function scheduleModal() {
    var sc = (data && data.schedule) || { mode: 'interval', hours: 6 };
    var hrs = [1, 2, 3, 6, 12, 24].map(function (h) { return { value: String(h), label: h === 1 ? 'elk uur' : 'elke ' + h + ' uur' }; });
    PFA.modal({ title: 'Hoe vaak controleren?', body: '<p class="muted" style="margin:0;line-height:1.55">Zo vaak kijkt PVE Fleet in de containers welke updates er klaarstaan. Met <strong>Nu controleren</strong> kan het altijd tussendoor.</p>',
      fields: [
        { name: 'mode', label: 'Wanneer', type: 'radio', value: sc.mode === 'daily' ? 'daily' : 'interval', options: [{ value: 'daily', label: 'Elke dag op een vast tijdstip' }, { value: 'interval', label: 'Om de zoveel uur' }] },
        { name: 'at', label: 'Tijdstip (elke dag)', type: 'time', value: sc.at || '05:00' },
        { name: 'hours', label: 'Om de zoveel uur', type: 'select', value: String(sc.hours || 6), options: hrs }
      ],
      confirm: 'Opslaan', kind: 'primary',
      submit: function (v) {
        return PF.postJSON('/api/guest-updates/schedule', v.mode === 'daily' ? { mode: 'daily', at: v.at } : { mode: 'interval', hours: +v.hours }).then(function () { PFA.toast('Opgeslagen'); load(); });
      } });
  }

  function setup(server) {
    var s = data.servers[server];
    return PF.getJSON('/api/guest-updates/setup').then(function (r) {
      var body = '<ol style="margin:0 0 10px;padding-left:20px;line-height:1.7">' +
        '<li>Open in Proxmox de shell van <strong>één node</strong> van ' + e(s.group) + ' (als root).</li>' +
        '<li>Plak dit commando. Het zet het controlescript in <span class="mono">/etc/pve/pvefleet/</span> (gedeeld in het cluster) en voegt de sleutel van PVE Fleet toe, beperkt tot dat script.</li></ol>' +
        '<pre class="log" style="max-height:200px;user-select:all" id="ct-cmd">' + e(r.command) + '</pre>' +
        '<button type="button" class="btn ghost" data-ctcopy="ct-cmd" style="margin:6px 0 10px">Kopiëren</button>' +
        '<p class="small muted" style="margin:0 0 6px">Klik daarna op <strong>Inschakelen en testen</strong>. PVE Fleet maakt verbinding met elke node. Weigert een node de sleutel, plak het commando dan ook in de shell van die node.</p>' +
        (s.enabled ? '<details style="margin-top:8px"><summary class="small" style="cursor:pointer">Uitzetten en sleutel verwijderen</summary><p class="small muted" style="margin:6px 0">Klik op <strong>Uitzetten</strong> en plak dit op elke node:</p><pre class="log" style="user-select:all">' + e(r.remove) + '</pre></details>' : '');
      var extra = s.enabled ? [{ label: 'Uitzetten', onClick: function () {
        PF.postJSON('/api/guest-updates/' + encodeURIComponent(server) + '/settings', { enabled: false }).then(function () { PFA.toast('Uitgezet'); load(); });
      } }] : [];
      return PFA.modal({ title: 'Updates in containers · ' + s.group, wide: true, body: body,
        fields: [{ name: 'port', label: 'SSH-poort van de nodes', value: '22', mono: true }],
        extra: extra, confirm: 'Inschakelen en testen', kind: 'primary',
        submit: function (v) {
          return PF.postJSON('/api/guest-updates/' + encodeURIComponent(server) + '/settings', { enabled: true, port: v.port }).then(function () {
            PFA.toast('Ingeschakeld; PVE Fleet controleert nu de containers'); setTimeout(load, 800);
          });
        } });
    }, function (err) { PFA.toast(err.message, true); });
  }

  root.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-ct]') : null;
    if (!b || !data) return;
    var a = b.getAttribute('data-ct');
    if (a === 'schedule') { ev.preventDefault(); return scheduleModal(); }
    if (a === 'allpkg') return allPackages();
    if (a === 'bulkview') return followBulk();
    if (a === 'allapps') return allApps();
    if (a === 'setup') return setup(b.getAttribute('data-s'));
    if (a === 'check') {
      b.disabled = true;
      return PF.postJSON('/api/guest-updates/' + encodeURIComponent(b.getAttribute('data-s')) + '/check').then(function () { PFA.toast('Controle gestart'); setTimeout(load, 500); },
        function (err) { b.disabled = false; PFA.toast(err.message, true); });
    }
    var x = data.guests[b.getAttribute('data-k')];
    if (!x) return;
    var m = mainApp(x);
    var after = function () { PF.postJSON('/api/guest-updates/' + encodeURIComponent(x.server) + '/check').then(function () { setTimeout(load, 1500); }, function () {}); };
    if (a === 'os') PFA.guestUpdate(g(x), 'os', { pm: x.pm, count: x.count, security: x.security }, after);
    if (a === 'app') PFA.guestUpdate(g(x), 'app', { app: m && m.name, installed: m && m.installed, latest: m && m.latest }, after);
  });
  root.addEventListener('change', function (ev) {
    if (ev.target.id !== 'ct-only') return;
    onlyUpd = ev.target.checked;
    try { localStorage.setItem('pvef_ctupd_all', onlyUpd ? '0' : '1'); } catch (x) { /* niets */ }
    render(data);
  });
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-ctcopy]') : null;
    if (!b) return;
    var text = document.getElementById(b.getAttribute('data-ctcopy')).textContent;
    var done = function (ok) { b.textContent = ok ? 'Gekopieerd ✓' : 'Selecteer en kopieer met Ctrl+C'; setTimeout(function () { b.textContent = 'Kopiëren'; }, 2500); };
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); }); return; }
    var ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    var ok = false; try { ok = document.execCommand('copy'); } catch (x) { ok = false; }
    document.body.removeChild(ta); done(ok);
  });
  load();
  // Tijdens een controle elke 2 seconden verversen (voortgang), anders elke 10 seconden
  var lastSlow = 0;
  setInterval(function () {
    if (window.PFA_MODAL_OPEN) return;
    var busy = data && Object.keys(data.servers).some(function (k) { return data.servers[k].checking; });
    if (busy || Date.now() - lastSlow > 10000) { lastSlow = Date.now(); load(); }
  }, 2000);
})();
