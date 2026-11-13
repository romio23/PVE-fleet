/* Updates: per node openstaande pakketten, kernel en herstart; bijwerken via de console. */
(function () {
  var e = PF.esc;
  var $ = function (id) { return document.getElementById(id); };
  var data = null;
  var busy = {};   // server|node -> tekst terwijl er iets loopt

  try { if (localStorage.getItem('pvef_updates_howto') === '1') $('howto').style.display = 'none'; } catch (x) { /* niets */ }
  $('howto-x').addEventListener('click', function () {
    $('howto').style.display = 'none';
    try { localStorage.setItem('pvef_updates_howto', '1'); } catch (x) { /* niets */ }
  });

  function k(server, node) { return server + '|' + node; }
  function find(server, node) {
    var g = data.groups.filter(function (x) { return x.server === server; })[0];
    return g ? { g: g, n: g.nodes.filter(function (x) { return x.name === node; })[0] } : null;
  }

  function statusCell(n) {
    if (n.status !== 'online') return '<span class="pill"><span class="dot b-crit"></span>Offline</span>';
    var parts = [];
    if (n.error && n.behind && n.behind.length) parts.push('<span class="pill c-warn" style="border-color:currentColor" title="' + e(n.behind.join(', ')) + '">Loopt achter</span>');
    else if (n.error && !n.reboot_needed) parts.push('<span class="pill c-ok" style="border-color:currentColor" title="' + e(n.error) + '. Deze node heeft dezelfde of een nieuwere versie dan de andere nodes.">✓ Nieuwste in cluster</span>');
    if (n.count) parts.push('<span class="pill c-warn" style="border-color:currentColor">' + n.count + ' update' + (n.count === 1 ? '' : 's') + '</span>');
    if (!n.reboot_needed && n.kernel_after_upgrade && n.status === 'online') parts.push('<span class="pill" style="color:var(--info-text);border-color:var(--info-line)" title="Er zit een nieuwe kernel bij de updates. Na het bijwerken moet deze node herstarten.">↻ Herstart na bijwerken</span>');
    if (n.reboot_needed) parts.push('<span class="pill c-info" style="border-color:currentColor" title="Nieuwe kernel ' + e(n.kernel_latest || '') + ' is geïnstalleerd">↻ Herstart nodig</span>');
    if (!parts.length) parts.push('<span class="pill c-ok" style="border-color:currentColor">✓ Up-to-date</span>');
    if (n.behind && n.behind.length) parts.push('<div class="small c-warn" style="margin-top:4px">' + n.behind.map(e).join('<br>') + '</div>');
    return parts.join(' ');
  }

  function row(g, n) {
    var key = k(g.server, n.name), on = n.status === 'online';
    var kern = n.kernel ? e(n.kernel) : '—';
    if (n.reboot_needed && n.kernel_latest) kern += '<div class="small c-info" style="margin-top:2px">→ ' + e(n.kernel_latest) + ' na herstart</div>';
    var upd = n.count === null || n.count === undefined ? (n.no_access ? '<span class="muted" title="' + e(n.error || '') + '">geen rechten</span>' : '<span class="muted" title="' + e(n.error || '') + '">—</span>')
      : (n.count ? '<span class="c-warn">' + n.count + '</span>' + (n.security ? ' <span class="c-crit small">(' + n.security + ' security)</span>' : '') : '<span class="muted">0</span>');
    var acts = busy[key] ? '<span class="small c-info"><span class="spinner" aria-hidden="true"></span> ' + e(busy[key]) + '</span>'
      : (on ? (n.count ? '<button type="button" class="btn ghost" data-act="pkgs" data-s="' + e(g.server) + '" data-n="' + e(n.name) + '">Pakketten</button>' : '') +
        '<button type="button" class="btn ' + (n.count || (n.behind && n.behind.length) ? 'primary' : 'ghost') + '" data-act="upgrade" data-s="' + e(g.server) + '" data-n="' + e(n.name) + '">Bijwerken</button>' +
        (g.kind !== 'cluster' || n.reboot_needed ? '<button type="button" class="btn ' + (n.reboot_needed ? 'blue' : 'ghost') + '" data-act="reboot" data-s="' + e(g.server) + '" data-n="' + e(n.name) + '">Herstarten</button>' : '') +
        '<button type="button" class="btn ghost iconbtn" data-act="check" data-s="' + e(g.server) + '" data-n="' + e(n.name) + '" title="Opnieuw zoeken naar updates" aria-label="Opnieuw zoeken naar updates op ' + e(n.name) + '">⟳</button>' : '');
    return '<tr><td><a class="mono" style="font-weight:500" href="' + PF.nodeUrl(g.server, n.name) + '">' + e(n.name) + '</a>' +
      (n.guests_running ? '<div class="small muted" style="margin-top:2px">' + n.guests_running + ' VM/CT actief</div>' : '') + '</td>' +
      '<td>' + statusCell(n) + '</td>' +
      '<td class="mono">' + (n.version ? e(n.version) : '—') + '</td>' +
      '<td class="mono small">' + kern + '</td>' +
      '<td class="mono">' + upd + '</td>' +
      '<td class="small muted">' + (n.checked ? PF.when(n.checked) : '—') + '</td>' +
      '<td><div class="rowacts">' + acts + '</div></td></tr>';
  }

  function render(d) {
    data = d;
    if (!d.ready) return;
    var nodes = [], sec = 0, reb = 0, withUpd = 0, unknown = 0, noAccess = 0, pend = 0;
    d.groups.forEach(function (g) { g.nodes.forEach(function (n) {
      nodes.push(n);
      if (n.count || (n.behind && n.behind.length)) withUpd++;
      if (n.status === 'online' && (n.count === null || n.count === undefined)) unknown++;
      if (n.no_access) noAccess++;
      sec += n.security || 0; if (n.reboot_needed) reb++; else if (n.status === 'online' && n.kernel_after_upgrade) pend++;
    }); });
    $('kpis').innerHTML = [
      ['Nodes met updates', withUpd, 'van ' + nodes.length + ' nodes' + (unknown ? ' · bij ' + unknown + ' alleen te zien of ze achterlopen' : ''), withUpd ? 'c-warn' : (unknown ? '' : 'c-ok')],
      ['Security-updates', unknown ? (sec ? sec + '+' : '?') : sec, unknown ? 'niet te zien zonder leesrecht' : (sec ? 'zo snel mogelijk installeren' : 'geen openstaand'), sec ? 'c-crit' : (unknown ? '' : 'c-ok')],
      ['Herstart nodig', reb + (pend ? ' + ' + pend : ''),
        (reb ? reb + (reb === 1 ? ' node heeft' : ' nodes hebben') + ' de nieuwe kernel al; herstart om die te gebruiken' : '') +
        (reb && pend ? ' · ' : '') + (pend ? pend + (pend === 1 ? ' node krijgt' : ' nodes krijgen') + ' bij bijwerken een nieuwe kernel en moet' + (pend === 1 ? '' : 'en') + ' daarna herstarten' : '') +
        (!reb && !pend ? (unknown ? 'niet zeker: van ' + unknown + ' nodes is de lijst niet te zien' : 'alle nodes draaien de nieuwste kernel') : ''),
        reb || pend ? 'c-info' : (unknown ? '' : 'c-ok')]
    ].map(function (i) {
      return '<div class="kpi"><span class="label">' + e(i[0]) + '</span><span class="value ' + i[3] + '">' + i[1] + '</span><span class="sub">' + e(i[2]) + '</span></div>';
    }).join('');
    var permBox = $('permbox');
    if (!permBox) { permBox = document.createElement('div'); permBox.id = 'permbox'; $('groups').parentNode.insertBefore(permBox, $('groups')); }
    var first = null;
    d.groups.forEach(function (g) { if (!first && g.nodes.some(function (n) { return n.no_access; })) first = g; });
    permBox.innerHTML = noAccess ? '<div class="banner warn"><div class="txt"><div class="ttl">PVE Fleet mag de lijst met updates niet lezen</div>' +
      '<div class="desc">Proxmox vraagt daarvoor het recht <span class="mono">Sys.Modify</span>. Zonder dat recht ziet PVE Fleet alleen of een node <strong>achterloopt</strong> op de nieuwste node in het cluster (versie en kernel), niet welke pakketten er klaarstaan. ' +
      'Bijwerken via de console werkt wel gewoon. Het commando om het recht toe te voegen staat bij <a href="/servers">Servers &amp; verbinding</a> › Bewerken › Verbinding testen › <em>Optioneel</em>.</div></div></div>' : '';
    $('groups').innerHTML = d.groups.map(function (g) {
      var work = g.nodes.some(function (n) { return n.count || n.reboot_needed || (n.behind && n.behind.length); });
      var head = '<div class="card-head"><h2>' + e(g.name) + '</h2><span class="small muted">' + (g.kind === 'cluster' ? 'cluster · ' + g.nodes.length + ' nodes' : 'losse node') + '</span>' +
        (g.kind === 'cluster' && g.quorate === false ? '<span class="pill c-crit" style="border-color:currentColor">geen quorum</span>' : '') +
        '<div class="end">' + (g.kind === 'cluster' && g.nodes.length > 1 && g.reachable
          ? '<a class="btn ' + (g.rolling ? 'blue' : (work ? 'primary' : 'ghost')) + '" href="/updates/rolling/' + encodeURIComponent(g.server) + '">' + (g.rolling ? 'Rolling update hervatten' : 'Rolling update') + '</a>' : '') + '</div></div>';
      if (!g.reachable) return '<section class="card warnline">' + head + '<div class="pad small c-crit" style="padding:14px 18px">Niet bereikbaar: ' + e(g.error || '') + '</div></section>';
      return '<section class="card" style="overflow:hidden">' + head + '<div class="tablewrap"><table class="tbl" style="min-width:900px"><thead><tr>' +
        '<th>Node</th><th>Status</th><th>PVE</th><th>Kernel</th><th>Updates</th><th>Gecontroleerd</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' +
        g.nodes.map(function (n) { return row(g, n); }).join('') + '</tbody></table></div></section>';
    }).join('') || '<div class="card pad muted">Geen servers ingesteld.</div>';
  }

  function load() { return PF.getJSON('/api/updates').then(render); }
  function check(server, node, refreshList) {
    busy[k(server, node)] = refreshList ? 'Pakketlijst vernieuwen…' : 'Opnieuw kijken…';
    if (data) render(data);
    return PF.postJSON('/api/updates/' + encodeURIComponent(server) + '/' + encodeURIComponent(node) + '/check', { refresh_list: !!refreshList })
      .then(function (r) {
        delete busy[k(server, node)];
        var u = r.updates || {};
        PFA.toast(node + ': ' + (u.count ? u.count + ' updates' : 'geen updates') + (u.reboot_needed ? ', herstart nodig' : '') + (r.note ? '. ' + r.note : ''), false);
        return load();
      }, function (err) { delete busy[k(server, node)]; PFA.toast(err.message, true); return load(); });
  }

  function packages(server, node) {
    return PF.getJSON('/api/updates/' + encodeURIComponent(server) + '/' + encodeURIComponent(node) + '/packages').then(function (r) {
      var list = r.packages || [];
      var body = list.length ? '<div class="tablewrap" style="max-height:52vh;overflow:auto"><table class="tbl"><thead><tr><th>Pakket</th><th>Nu</th><th>Nieuw</th><th>Bron</th></tr></thead><tbody>' +
        list.map(function (p) {
          var sec = /security/i.test(p.origin || '');
          return '<tr><td class="mono">' + e(p.name) + '</td><td class="mono small muted">' + e(p.old || '') + '</td><td class="mono small">' + e(p.new || '') + '</td>' +
            '<td class="small ' + (sec ? 'c-crit' : 'muted') + '">' + e(sec ? 'security' : (p.origin || '')) + '</td></tr>';
        }).join('') + '</tbody></table></div>' : '<p class="c-ok">Geen openstaande updates.</p>';
      return PFA.modal({ title: 'Updates voor ' + node + ' (' + list.length + ')', wide: true, body: body, confirm: list.length ? 'Bijwerken…' : null, kind: 'primary', cancelLabel: 'Sluiten' })
        .then(function (v) { if (v) upgrade(server, node); });
    }, function (err) { PFA.toast(err.message, true); });
  }

  function upgrade(server, node) {
    var f = find(server, node);
    var body = '<p style="margin:0 0 8px">Er opent een console van <strong>' + e(node) + '</strong>. Daarin:</p><ol style="margin:0;padding-left:20px;line-height:1.7">' +
      '<li>Log in als <span class="mono">root</span> met het wachtwoord van de node.</li>' +
      '<li>Klik bovenin op <em>Upgrade-commando invoeren</em>.</li>' +
      '<li>Lees wat er verandert en bevestig met <span class="mono">Y</span>.</li>' +
      '<li>Sluit het venster als het klaar is. PVE Fleet kijkt dan opnieuw.</li></ol>' +
      (f && f.g.kind === 'cluster' ? '<p class="small muted" style="margin:10px 0 0">Tip: werk in een cluster alle nodes bij naar dezelfde versie. De <strong>Rolling update</strong> helpt daarbij.</p>' : '') +
      '<p class="small muted" style="margin:8px 0 0">VM\'s &amp; containers blijven tijdens het bijwerken gewoon draaien. Alleen een nieuwe kernel vraagt daarna om een herstart.</p>';
    return PFA.modal({ title: node + ' bijwerken', body: body, confirm: 'Console openen', kind: 'primary' }).then(function (v) {
      if (!v) return;
      busy[k(server, node)] = 'Console open…';
      render(data);
      PF.openUpgrade(server, node, function () { check(server, node, false); });
    });
  }

  function reboot(server, node) {
    var f = find(server, node);
    if (!f) return;
    PFA.nodePower(server, node, 'node_reboot', { cluster: f.g.kind === 'cluster', running: f.n.guests_running });
  }

  $('groups').addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-act]') : null;
    if (!b) return;
    var s = b.getAttribute('data-s'), n = b.getAttribute('data-n'), a = b.getAttribute('data-act');
    if (a === 'pkgs') packages(s, n);
    else if (a === 'upgrade') upgrade(s, n);
    else if (a === 'reboot') reboot(s, n);
    else if (a === 'check') check(s, n, true);
  });
  window.addEventListener('pf:refresh', load);
  PF.poll('/api/updates', render, 15000);
})();
