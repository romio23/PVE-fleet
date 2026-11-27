(function () {
  var app = document.getElementById('app');
  var server = app.getAttribute('data-server'), nodeName = app.getAttribute('data-node');
  var e = PF.esc;
  var filter = 'all';
  var sel = {};            // geselecteerde VM/CT's: vmid -> true
  var lastData = null;
  var isCluster = app.getAttribute('data-cluster') === '1';

  function metrics(d) {
    var r = d.rrd, n = d.node, st = d.status || {};
    if (!r) return '';
    var mem = r.memused.map(function (v, i) { return r.memtotal[i] ? 100 * v / r.memtotal[i] : null; });
    var cpu = r.cpu.map(function (v) { return v === null ? null : v * 100; });
    var net = r.netin.map(function (v, i) { return v === null ? null : v + (r.netout[i] || 0); });
    var io = r.iowait.map(function (v) { return v === null ? null : v * 100; });
    var la = st.loadavg ? st.loadavg.join(' · ') : '—';
    var items = [
      ['CPU', PF.pct(n.cpu), 'load ' + la, PF.level(n.cpu, 90, 95), cpu],
      ['RAM', PF.pct(n.mem_pct), PF.bytes(n.mem_used) + ' / ' + PF.bytes(n.mem_total), PF.level(n.mem_pct), mem],
      ['Netwerk', PF.rate(PF.last(net)), 'in ' + PF.rate(PF.last(r.netin)) + ' · uit ' + PF.rate(PF.last(r.netout)), 'info', net],
      ['IO-wait', PF.last(io) === null ? '—' : PF.last(io).toFixed(1).replace('.', ',') + '%', 'wachten op schijf', PF.level(PF.last(io), 10, 25), io]
    ];
    return '<section class="metrics" aria-label="Belasting laatste uur">' + items.map(function (m) {
      return '<div class="metric"><div class="top"><span class="label">' + m[0] + '</span><span class="small muted">laatste uur</span></div>' +
        '<div class="value c-' + (m[3] === 'info' ? '' : m[3]) + '">' + m[1] + '</div><div class="small muted">' + e(m[2]) + '</div>' + PF.spark(m[4], m[3], m[0] + ' laatste uur') + '</div>';
    }).join('') + '</section>';
  }

  function gobj(g) { return { server: server, node: nodeName, type: g.type, vmid: g.vmid, name: g.name, status: g.status }; }
  function selected(d) { return d.guests.filter(function (g) { return sel[g.vmid]; }).map(gobj); }

  function guests(d) {
    var list = d.guests.filter(function (g) { return filter === 'all' || g.type === filter; });
    var running = d.guests.filter(function (g) { return g.status === 'running'; }).length;
    var off = d.node.status !== 'online';
    var head = '<div class="card-head"><h2>VM\'s &amp; containers</h2><span class="muted" style="font-size:13px">' + running + ' actief van ' + d.guests.length + '</span>' +
      '<div class="end" role="group" aria-label="Filter">' + [['all', 'Alle'], ['qemu', 'VM'], ['lxc', 'LXC']].map(function (f) {
        return '<button type="button" class="btn ' + (filter === f[0] ? '' : 'ghost') + '" data-filter="' + f[0] + '" aria-pressed="' + (filter === f[0]) + '">' + f[1] + '</button>';
      }).join('') + '</div></div>';
    if (!list.length) return '<section class="card">' + head + '<div class="empty">Geen VM\'s of containers op deze node.</div></section>';
    var chosen = selected(d);
    var bar = '';
    if (chosen.length && !off) {
      bar = '<div class="selbar"><span class="cnt">' + chosen.length + ' geselecteerd</span>' +
        '<button type="button" class="btn ghost" data-bulk="start">Starten</button>' +
        '<button type="button" class="btn ghost" data-bulk="shutdown">Afsluiten</button>' +
        '<button type="button" class="btn ghost" data-bulk="reboot">Herstarten</button>' +
        '<button type="button" class="btn ghost" data-bulk="snapshot">Snapshot</button>' +
        '<button type="button" class="btn ghost" data-bulk="backup">Back-up nu</button>' +
        (isCluster ? '<button type="button" class="btn blue" data-bulk="migrate">Migreren…</button>' : '') +
        '<button type="button" class="btn ghost" data-bulk="clear" style="margin-left:auto">Selectie wissen</button></div>';
    }
    var allSel = list.every(function (g) { return sel[g.vmid]; });
    var rows = list.map(function (g) {
      var mem = g.maxmem ? PF.bytes(g.mem) + ' / ' + PF.bytes(g.maxmem, 0) : '—';
      var on = g.status === 'running';
      var url = PF.guestUrl(server, nodeName, g.type, g.vmid);
      return '<tr class="' + (sel[g.vmid] ? 'sel' : '') + '">' +
        '<td style="width:28px"><input type="checkbox" class="cb" data-sel="' + g.vmid + '"' + (sel[g.vmid] ? ' checked' : '') + (off ? ' disabled' : '') + ' aria-label="Selecteer ' + e(g.name) + '"></td>' +
        '<td class="mono muted">' + g.vmid + '</td>' +
        '<td><a href="' + url + '" style="font-weight:500">' + e(g.name) + '</a>' +
        (g.tags.length ? ' <span class="small muted">' + g.tags.map(function (t) { return '#' + e(t); }).join(' ') + '</span>' : '') + '</td>' +
        '<td><span class="typebadge">' + (g.type === 'qemu' ? 'VM' : 'LXC') + '</span></td>' +
        '<td>' + PF.dotStatus(off ? 'unknown' : g.status) + (off ? PF.seenNote(g) : '') + '</td>' +
        '<td>' + PF.ipCell(g.ips, g.ip_src) + '</td>' +
        '<td class="mono">' + (on ? PF.pct(g.cpu) : '—') + '</td>' +
        '<td class="mono">' + (on ? mem : '— / ' + PF.bytes(g.maxmem, 0)) + '</td>' +
        '<td class="mono muted">' + PF.uptime(g.uptime) + '</td>' +
        '<td><div class="rowacts">' + (off ? '' : '<button type="button" class="btn ghost iconbtn" data-one="' + (on ? 'shutdown' : 'start') + '" data-vmid="' + g.vmid + '" aria-label="' + (on ? 'Afsluiten: ' : 'Starten: ') + e(g.name) + '" title="' + (on ? 'Afsluiten' : 'Starten') + '" style="color:' + (on ? 'var(--text2)' : 'var(--ok)') + '">' +
          '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' + (on ? '<path d="M12 3v9M6.3 6.8a8 8 0 1 0 11.4 0"></path>' : '<path d="M7 5l12 7-12 7z"></path>') + '</svg></button>') +
        '<a class="btn ghost" href="' + url + '">Details</a></div></td></tr>';
    }).join('');
    return '<section class="card" style="overflow:hidden">' + head + bar + '<div class="tablewrap"><table class="tbl" style="min-width:940px"><thead><tr>' +
      '<th><input type="checkbox" class="cb" data-selall="1"' + (allSel ? ' checked' : '') + (off ? ' disabled' : '') + ' aria-label="Alles selecteren"></th>' +
      '<th>ID</th><th>Naam</th><th>Type</th><th>Status</th><th>IP-adres</th><th>CPU</th><th>RAM</th><th>Uptime</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' + rows + '</tbody></table></div></section>';
  }

  function storage(d) {
    if (!d.storage.length) return '';
    return '<section class="card pad"><h2 class="small-title">Opslag</h2><div style="display:flex;flex-direction:column;gap:14px">' + d.storage.map(function (s) {
      var l = PF.level(s.pct);
      return '<div><div style="display:flex;justify-content:space-between;gap:8px;font-size:13px"><span><span class="mono">' + e(s.name) + '</span> <span class="muted">· ' + e(s.type) + (s.shared ? ' · gedeeld' : '') + '</span></span>' +
        '<span class="mono c-' + (l === 'info' ? '' : l) + '">' + PF.pct(s.pct) + '</span></div>' +
        '<div class="bar" style="margin-top:6px"><span class="b-' + l + '" style="width:' + s.pct + '%"></span></div>' +
        '<div class="small muted" style="margin-top:4px">' + PF.bytes(s.used) + ' / ' + PF.bytes(s.total) + '</div></div>';
    }).join('') + '</div></section>';
  }

  function disks(d) {
    if (d.disks === null) return '';
    var body = d.disks.length ? d.disks.map(function (x) {
      var ok = (x.health || '').toUpperCase() === 'PASSED' || (x.health || '').toUpperCase() === 'OK';
      return '<div class="row"><div style="min-width:0"><div class="mono">' + e(x.dev) + '</div><div class="small muted" style="margin-top:2px">' + e(x.model || '') + ' · ' + PF.bytes(x.size, 0) +
        (x.wearout !== null && x.wearout !== undefined && x.wearout !== 'N/A' ? ' · wearout ' + e(x.wearout) + '%' : '') + '</div></div>' +
        '<span class="' + (ok ? 'c-ok' : 'c-crit') + '">' + e(x.health || '?') + '</span></div>';
    }).join('') : '<div class="muted">' + (d.errors.disks ? 'Niet beschikbaar: ' + e(d.errors.disks) : 'Geen schijven gevonden.') + '</div>';
    return '<section class="card pad"><h2 class="small-title">Schijven &amp; SMART</h2><div class="list">' + body + '</div></section>';
  }

  function packages(d) {
    if (d.packages === null && !d.errors.packages) return '';
    var body;
    if (d.packages === null) body = '<div class="muted" style="font-size:13px">Kan updates niet ophalen: ' + e(d.errors.packages) + '</div>';
    else if (!d.packages.length) body = d.apt_error ? '<div class="c-warn" style="font-size:13px"><strong>Pakketlijst verversen mislukt.</strong> <span>' + e(d.apt_error) + '</span></div><div class="small muted" style="margin-top:6px">Oplossing op de node: <span class="mono">echo \'Acquire::ForceIPv4 "true";\' &gt; /etc/apt/apt.conf.d/99force-ipv4; apt update</span></div>' : '<div class="c-ok" style="font-size:13px">Up-to-date</div>';
    else body = '<div class="list">' + d.packages.map(function (p) {
      var sec = /security/i.test(p.origin || '');
      return '<div class="row"><div style="min-width:0"><div class="mono">' + e(p.name) + '</div><div class="small muted mono" style="margin-top:2px">' + e(p.old || '') + ' → ' + e(p.new) + '</div></div>' +
        (sec ? '<span class="c-crit small">security</span>' : '<span class="muted small">' + e(p.origin || '') + '</span>') + '</div>';
    }).join('') + '</div>';
    return '<section class="card pad"><h2 class="small-title">Updates' + (d.packages && d.packages.length ? ' (' + d.packages.length + ')' : '') + '</h2>' + body +
      '<p class="small muted" style="margin:10px 0 0">Installeren: <a href="/updates">Updates</a> › Bijwerken.</p></section>';
  }

  function tasks(d) {
    if (!d.tasks.length) return '';
    var vt = PF.visibleTasks(d.tasks, 12);
    return '<section class="card pad"><div style="display:flex;align-items:center;gap:8px;margin-bottom:12px"><h2 class="small-title" style="margin:0">Recente taken</h2>' + vt.toggle + '</div><div class="list">' +
      (vt.list.length ? '' : '<div class="muted small">Alleen consoletaken.</div>') + vt.list.map(function (t) {
      return '<div class="row"><div><div>' + e(PF.taskName(t)) + '</div><div class="small muted mono" style="margin-top:2px">' + PF.when(t.start) + ' · ' + e(t.user || '') + '</div></div>' + PF.taskResult(t) + '</div>';
    }).join('') + '</div></section>';
  }

  function render(d) {
    var n = d.node, g = d.group;
    document.getElementById('crumb-group').textContent = g.name;
    var off = n.status !== 'online';
    var member = g.kind === 'cluster' ? g.name + ' · ' + g.nodes_online + '/' + g.nodes_total + ' online' : 'losse node';
    var meta = [n.ip, n.version ? 'PVE ' + n.version : null, n.kernel ? 'kernel ' + n.kernel : null, n.uptime ? 'uptime ' + PF.uptime(n.uptime) : null,
      n.model ? n.model + (n.cpus ? ', ' + n.cpus + ' threads' : '') : null, n.mem_total ? PF.bytes(n.mem_total, 0) : null].filter(Boolean).map(e).join(' · ');
    var html = '<section class="pagehead"><div><div class="title"><h1 class="mono">' + e(n.name) + '</h1>' +
      '<span class="pill c-' + PF.statusInfo(n.status).cls + '" style="border-color:currentColor"><span class="dot b-' + PF.statusInfo(n.status).cls + '"></span>' + PF.statusInfo(n.status).label + '</span>' +
      '<span class="pill">' + e(member) + '</span></div><p class="meta">' + (meta || '&nbsp;') + '</p></div>' +
      '<div class="toolbar" role="toolbar" aria-label="Node-acties">' +
      (!off ? '<a class="btn lg" href="' + PF.consoleUrl(server, nodeName, 'shell') + '" data-console="shell">Shell</a>' : '') +
      (off ? (d.wol_possible ? '<button type="button" class="btn lg primary" data-nodeact="node_wol">Wake-on-LAN</button>' : '') +
        '<button type="button" class="btn lg" data-nodeact="refresh">Opnieuw verbinden</button>'
        : '<button type="button" class="btn lg" data-nodeact="node_reboot">Herstarten</button><button type="button" class="btn lg danger" data-nodeact="node_shutdown">Afsluiten</button>') +
      (g.web_url ? '<a class="btn lg ghost" href="' + e(g.web_url) + '" target="_blank" rel="noopener">Proxmox ↗</a>' : '') + '</div></section>';
    if (off) {
      html += '<section class="banner crit" role="alert"><div class="txt"><div class="ttl">' + (g.reachable ? 'Node is offline volgens het cluster' : 'Geen verbinding sinds ' + PF.clock(g.since) + ' (' + PF.since(g.since) + ')') + '</div>' +
        '<div class="desc">' + (g.error ? '<span class="mono">' + e(g.error) + '</span><br>' : '') + (n.stale ? 'Hieronder staan de laatst bekende gegevens.' : (g.reachable ? '' : 'Sinds de start van het dashboard is deze server nog niet bereikt.')) + '</div></div></section>';
    }
    if (n.version && /^8\./.test(n.version)) {
      html += '<section class="banner warn"><div class="txt"><div class="ttl">Deze node draait Proxmox VE ' + e(n.version) + '</div><div class="desc">Er is een nieuwere hoofdversie (9.x). Draai eerst <span class="mono">pve8to9 --full</span> en lees de upgrade-instructies van Proxmox.</div></div></section>';
    }
    html += metrics(d) + '<div class="cols"><div class="col-main">' + guests(d) + '</div><div class="col-side">' + storage(d) + disks(d) + packages(d) + tasks(d) + '</div></div>';
    app.innerHTML = html;
  }

  function rerender() { if (lastData) render(lastData); }
  app.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t.closest) return;
    var cb = t.closest('[data-console]');
    if (cb) { ev.preventDefault(); return PF.openConsole(server, nodeName, 'shell'); }
    var b = t.closest('[data-filter]');
    if (b) { filter = b.getAttribute('data-filter'); return rerender(); }
    if (!lastData) return;
    var d = lastData;
    b = t.closest('[data-nodeact]');
    if (b) {
      var running = d.guests.filter(function (x) { return x.status === 'running'; }).length;
      return PFA.nodePower(server, nodeName, b.getAttribute('data-nodeact'), { cluster: isCluster, running: running });
    }
    b = t.closest('[data-one]');
    if (b) {
      var g = d.guests.filter(function (x) { return String(x.vmid) === b.getAttribute('data-vmid'); })[0];
      if (g) PFA.power([gobj(g)], b.getAttribute('data-one'));
      return;
    }
    b = t.closest('[data-bulk]');
    if (b) {
      var act = b.getAttribute('data-bulk'), list = selected(d);
      if (act === 'clear') { sel = {}; return rerender(); }
      var p;
      if (act === 'start') p = PFA.power(list.filter(function (x) { return x.status !== 'running'; }), 'start');
      else if (act === 'shutdown' || act === 'reboot') p = PFA.power(list.filter(function (x) { return x.status === 'running'; }), act);
      else if (act === 'snapshot') p = PFA.snapshot(list);
      else if (act === 'backup') p = PFA.backup(list);
      else if (act === 'migrate') p = PFA.migrate(list);
      if ((act === 'start' || act === 'shutdown' || act === 'reboot') && !list.filter(function (x) { return act === 'start' ? x.status !== 'running' : x.status === 'running'; }).length) {
        PFA.toast(act === 'start' ? 'De geselecteerde VM/CT\'s draaien al' : 'Geen van de geselecteerde VM/CT\'s draait', true);
      }
      if (p) p.then(function () { sel = {}; rerender(); });
    }
  });
  app.addEventListener('change', function (ev) {
    var t = ev.target;
    if (!lastData) return;
    if (t.hasAttribute('data-sel')) {
      if (t.checked) sel[t.getAttribute('data-sel')] = true; else delete sel[t.getAttribute('data-sel')];
      rerender();
    } else if (t.hasAttribute('data-selall')) {
      lastData.guests.filter(function (g) { return filter === 'all' || g.type === filter; }).forEach(function (g) {
        if (t.checked) sel[g.vmid] = true; else delete sel[g.vmid];
      });
      rerender();
    }
  });
  var url = '/api/node/' + encodeURIComponent(server) + '/' + encodeURIComponent(nodeName);
  document.addEventListener('click', function (ev) {
    if (ev.target.closest && ev.target.closest('[data-contasks]')) { PF.toggleConsoleTasks(); if (lastData) render(lastData); }
  });
  function onData(d) {
    lastData = d;
    var present = {};
    d.guests.forEach(function (g) { present[g.vmid] = true; });
    Object.keys(sel).forEach(function (k) { if (!present[k]) delete sel[k]; });
    // niet opnieuw tekenen terwijl iemand in een dialoog bezig is: dat wist de focus niet, maar wel zo rustig
    render(d);
  }
  PF.poll(url, onData, Math.max(10000, window.PF_POLL_MS),
    function (err) { app.innerHTML = '<div class="banner crit"><div class="txt"><div class="ttl">Kan deze node niet laden</div><div class="desc">' + e(err.message) + ' · <a href="/">terug naar overzicht</a></div></div></div>'; });
  window.addEventListener('pf:refresh', function () { PF.getJSON(url).then(onData, function () {}); });
})();
