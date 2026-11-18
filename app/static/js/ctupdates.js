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

  function serverBlock(name, s) {
    var guests = Object.keys(data.guests).map(function (k) { return data.guests[k]; }).filter(function (x) { return x.server === name; });
    var nodeErr = Object.keys(s.nodes || {}).filter(function (n) { return !s.nodes[n].ok; }).map(function (n) {
      return '<div class="small c-warn" style="margin-top:4px"><span class="mono">' + e(n) + '</span>: ' + e(s.nodes[n].error) + '</div>';
    }).join('');
    var list = guests.filter(function (x) { return !onlyUpd || hasUpd(x); }).sort(function (a, b) { return (hasUpd(b) ? 1 : 0) - (hasUpd(a) ? 1 : 0) || a.vmid - b.vmid; });
    var n = guests.filter(hasUpd).length;
    var head = '<div class="card-head"><h2>' + e(s.group) + '</h2><span class="small muted">' +
      (s.checking ? '<span class="spinner" aria-hidden="true"></span> bezig met controleren…' : (s.checked ? 'gecontroleerd ' + PF.when(s.checked) : 'nog niet gecontroleerd')) +
      ' · ' + guests.length + ' containers' + (n ? ' · <span class="c-warn">' + n + ' met updates</span>' : '') + '</span>' +
      '<div class="end"><button type="button" class="btn ghost" data-ct="check" data-s="' + e(name) + '"' + (s.checking ? ' disabled' : '') + '>Nu controleren</button>' +
      '<button type="button" class="btn ghost" data-ct="setup" data-s="' + e(name) + '">Instellingen</button></div></div>';
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
      (on.length ? '<div class="end"><label class="check" style="margin:0"><input type="checkbox" id="ct-only"' + (onlyUpd ? ' checked' : '') + '>Alleen met updates</label></div>' : '') + '</div>';
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
  function load() { return PF.getJSON('/api/guest-updates').then(render, function () {}); }
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
  setInterval(function () { if (!window.PFA_MODAL_OPEN) load(); }, 10000);
})();
