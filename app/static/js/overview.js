(function () {
  var app = document.getElementById('app');
  var e = PF.esc;

  function kpis(k) {
    var off = k.nodes_total - k.nodes_online;
    var items = [
      ['Nodes online', k.nodes_online + ' / ' + k.nodes_total, !k.nodes_total ? 'nog geen nodes gevonden' : (off ? off + ' offline' : 'alles bereikbaar'), off ? 'c-crit' : '', off ? '/nodes?filter=offline' : '/nodes', 'Alle nodes bekijken'],
      ['VM\'s & containers', k.guests_running + ' / ' + k.guests_total, k.vm_running + ' VM · ' + k.lxc_running + ' CT actief', '', '/vms', 'Alle VM\'s & containers bekijken'],
      ['CPU fleet', PF.pct(k.cpu), k.cores + ' threads totaal', '', '/nodes?sort=cpu', 'Nodes gesorteerd op CPU'],
      ['RAM fleet', PF.pct(k.mem), PF.bytes(k.mem_used, 0) + ' / ' + PF.bytes(k.mem_total, 0), '', '/nodes?sort=ram', 'Nodes gesorteerd op RAM'],
      ['Opslag', PF.pct(k.storage), PF.bytes(k.storage_used) + ' / ' + PF.bytes(k.storage_total), k.storage >= 90 ? 'c-crit' : (k.storage >= 80 ? 'c-warn' : ''), '/storage', 'Alle opslag bekijken'],
      ['Alerts', k.alerts_crit + ' · ' + k.alerts_warn, 'kritiek · waarschuwing', k.alerts_crit ? 'c-crit' : (k.alerts_warn ? 'c-warn' : 'c-ok'), '#alerts-card', 'Naar de meldingen']
    ];
    return '<section class="kpis" aria-label="Kerncijfers">' + items.map(function (i) {
      return '<a class="kpi kpi-link" href="' + i[4] + '" title="' + e(i[5]) + '"><span class="label">' + e(i[0]) + '<span class="arrow" aria-hidden="true">›</span></span><span class="value ' + i[3] + '">' + i[1] + '</span><span class="sub">' + e(i[2]) + '</span></a>';
    }).join('') + '</section>';
  }

  function nodeRow(g, n) {
    var url = PF.nodeUrl(g.server, n.name);
    var upd = n.updates && n.updates.count ? '<span class="c-warn" title="openstaande pakketten">' + n.updates.count + ' upd.</span>' : '';
    return '<tr>' +
      '<td><a class="mono" style="font-weight:500" href="' + url + '">' + e(n.name) + '</a>' +
      '<div class="small muted" style="margin-top:2px">' + e(n.ip || '') + (n.version ? ' · PVE ' + e(n.version) : '') + '</div></td>' +
      '<td>' + PF.dotStatus(n.status) + '</td>' +
      '<td style="width:150px">' + PF.barCell(n.cpu, 90, 95) + '</td>' +
      '<td style="width:150px">' + PF.barCell(n.mem_pct) + '</td>' +
      '<td class="mono">' + n.guests_running + ' / ' + n.guests_total + '</td>' +
      '<td class="mono muted">' + PF.uptime(n.uptime) + '</td>' +
      '<td class="small">' + upd + '</td>' +
      '<td class="r"><a class="btn ghost" href="' + url + '">Details</a></td></tr>';
  }

  function cluster(g) {
    var ok = g.reachable && g.quorate && g.nodes_online === g.nodes_total;
    var cls = !g.reachable || !g.quorate ? 'crit' : (ok ? 'ok' : 'warn');
    var label = !g.reachable ? 'Onbereikbaar' : (!g.quorate ? 'Geen quorum' : (ok ? 'Gezond' : 'Let op'));
    var meta = (!g.reachable && !g.nodes.length) ? 'nog niet verbonden' : g.nodes_total + ' nodes · ' + g.nodes_online + '/' + g.nodes_total + ' online' + (g.reachable ? ' · via ' + e(g.active_host) : '');
    var hint = g.reachable ? '' : PF.errorHint(g.error);
    var err = g.reachable ? '' : '<div class="banner crit" style="margin:12px 18px ' + (g.nodes.length ? '0' : '16px') + '"><div class="txt"><div class="ttl">Geen verbinding met dit cluster sinds ' + PF.clock(g.since) + '</div><div class="desc mono">' + e(g.error) + '</div>' +
      (hint ? '<div class="desc" style="margin-top:8px;color:var(--text)">' + e(hint) + '</div>' : '') + '</div></div>';
    return '<section class="card" style="overflow:hidden">' +
      '<div class="card-head"><span class="dot b-' + cls + '" style="width:10px;height:10px" aria-hidden="true"></span>' +
      '<h3 class="mono">' + e(g.name) + '</h3><span class="pill c-' + cls + '" style="border-color:currentColor">' + label + '</span>' +
      '<span class="muted" style="font-size:13px">' + meta + '</span>' +
      (g.web_url ? '<div class="end"><a class="btn ghost" href="' + e(g.web_url) + '" target="_blank" rel="noopener">Proxmox ↗</a></div>' : '') +
      '</div>' + err + (g.nodes.length ? '' : '<!--') +
      '<div class="tablewrap"><table class="tbl" style="min-width:760px"><thead><tr><th>Node</th><th>Status</th><th>CPU</th><th>RAM</th><th>VM/CT</th><th>Uptime</th><th><span class="sr-only">Acties</span></th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' +
      g.nodes.map(function (n) { return nodeRow(g, n); }).join('') + '</tbody></table></div>' + (g.nodes.length ? '' : '-->') + '</section>';
  }

  function standalone(g) {
    var n = g.nodes[0];
    var off = !g.reachable || n.status !== 'online';
    var hint = g.reachable ? '' : PF.errorHint(g.error);
    var note = off ? '<p class="c-crit" style="margin:0;font-size:13px">Onbereikbaar sinds ' + PF.clock(g.since || Date.now() / 1000) + ' (' + PF.since(g.since || Date.now() / 1000) + ')</p>' +
        (g.error ? '<p class="mono small" style="margin:6px 0 0;color:var(--crit-text);overflow-wrap:anywhere">' + e(g.error) + '</p>' : '') +
        (hint ? '<p class="small" style="margin:6px 0 0">' + e(hint) + '</p>' : '')
      : (n.updates && n.updates.count ? '<p class="muted" style="margin:0;font-size:13px">' + n.updates.count + ' updates beschikbaar</p>' : (n.updates && n.updates.count === 0 ? '<p class="muted" style="margin:0;font-size:13px">Up-to-date</p>' : ''));
    var url = PF.nodeUrl(g.server, n.name);
    return '<article class="card pad standalone" style="' + (off ? 'border-color:var(--crit-line)' : '') + '">' +
      '<div class="head"><div><a class="mono" style="font-weight:600;font-size:15px" href="' + url + '">' + e(n.name) + '</a>' +
      '<div class="small muted" style="margin-top:3px">' + e(n.ip || g.server) + (n.version ? ' · PVE ' + e(n.version) : '') + '</div></div>' + PF.dotStatus(n.status) + '</div>' +
      '<div class="trio"><div><div class="l">CPU</div><div class="v">' + PF.pct(n.cpu) + '</div></div><div><div class="l">RAM</div><div class="v">' + PF.pct(n.mem_pct) + '</div></div>' +
      '<div><div class="l">VM/CT</div><div class="v">' + n.guests_running + ' / ' + n.guests_total + '</div></div></div>' + note +
      '<div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap"><a class="btn ghost" href="' + url + '">Details</a>' +
      (g.web_url && !off ? '<a class="btn ghost" href="' + e(g.web_url) + '" target="_blank" rel="noopener">Proxmox ↗</a>' : '') + '</div></article>';
  }

  function side(d) {
    var k = d.kpis;
    var alerts = d.alerts.length ? d.alerts.map(function (a) { return PF.alertItem(a, true); }).join('') : '<div class="muted" style="font-size:13px">Geen meldingen. Alles in orde.</div>';
    var total = k.backups_ok + k.backups_failed;
    var strip = '';
    for (var i = 0; i < Math.min(total, 50); i++) strip += '<span class="' + (i < k.backups_failed ? 'b-crit' : 'b-ok') + '"></span>';
    var vt = PF.visibleTasks(d.tasks, 10);
    var tasks = vt.list.length ? vt.list.map(function (t) {
      return '<div class="row"><div style="min-width:0"><div>' + e(PF.taskName(t)) + '</div><div class="small muted mono" style="margin-top:2px">' + e(t.node) + ' · ' + PF.when(t.start) + '</div></div>' + PF.taskResult(t) + '</div>';
    }).join('') : '<div class="muted">Geen recente taken.</div>';
    return '<aside class="col-side" aria-label="Meldingen en activiteit">' +
      '<section class="card pad" id="alerts-card" tabindex="-1"><h2 class="small-title">Alerts</h2><div class="alerts" id="alerts">' + alerts + '</div></section>' +
      '<section class="card pad"><h2 class="small-title">Back-uptaken laatste 24 u</h2>' +
      (total ? '<div style="display:flex;align-items:baseline;gap:8px"><span class="mono ' + (k.backups_failed ? 'c-warn' : 'c-ok') + '" style="font-size:28px">' + k.backups_ok + '/' + total + '</span><span class="muted" style="font-size:13px">geslaagd' + (k.backups_failed ? ' · ' + k.backups_failed + ' mislukt' : '') + '</span></div><div class="backupstrip" aria-hidden="true">' + strip + '</div>'
        : '<div class="muted" style="font-size:13px">Geen back-uptaken gezien in de laatste 24 uur.</div>') + '</section>' +
      '<section class="card pad"><div style="display:flex;align-items:center;gap:8px;margin-bottom:12px"><h2 class="small-title" style="margin:0">Recente taken</h2>' + vt.toggle + '</div><div class="list">' + tasks + '</div></section></aside>';
  }

  var lastFleet = null;
  document.addEventListener('click', function (ev) {
    if (ev.target.closest && ev.target.closest('[data-contasks]')) { PF.toggleConsoleTasks(); if (lastFleet) render(lastFleet); }
  });
  function render(d) {
    lastFleet = d;
    if (!d.ready) { setTimeout(function () { PF.getJSON('/api/fleet').then(render, function () {}); }, 1500); app.innerHTML = '<div class="loading">Eerste ronde langs de servers… Is een server niet bereikbaar, dan kan dit zo\'n 15 seconden duren.</div>'; return; }
    var clusters = d.groups.filter(function (g) { return g.kind === 'cluster'; });
    var alone = d.groups.filter(function (g) { return g.kind !== 'cluster'; });
    PF.navCounts(d);
    var html = '<div class="pagehead"><div><h1>Overzicht</h1><p class="muted" style="margin:6px 0 0">' +
      clusters.length + ' cluster' + (clusters.length === 1 ? '' : 's') + ' en ' + alone.length + ' losse node' + (alone.length === 1 ? '' : 's') +
      ' · ' + d.kpis.nodes_total + ' nodes totaal</p></div></div>' + kpis(d.kpis) +
      '<div class="cols"><div class="col-main">' +
      (clusters.length ? '<h2 class="section-label" id="clusters">Clusters</h2>' + clusters.map(cluster).join('') : '') +
      (alone.length ? '<h2 class="section-label" id="losse">Losse nodes</h2><div class="cards2">' + alone.map(standalone).join('') + '</div>' : '') +
      (d.groups.length ? '' : '<div class="card pad">Nog geen servers ingesteld. Zie <a href="/servers">Servers &amp; verbinding</a>.</div>') +
      '</div>' + side(d) + '</div>';
    app.innerHTML = html;
  }

  var timer = PF.poll('/api/fleet', render);
  PF.bindAcks(app, function () { PF.getJSON('/api/fleet').then(render); });
})();
