/* Kioskweergave: overzicht, node-acties met bevestiging en pincode, koppelen. */
(function () {
  var e = PF.esc;
  var data = null;          // /api/fleet
  var ks = null;            // /api/kiosk/state
  var sheet = null;         // { key, stage: 'actions'|'confirm'|'pin', action, ack }
  var pin = '', pinErr = '';
  var view = 'nodes', vmFilter = 'all', vms = null;   // kioskweergave: nodes of VM's & containers
  try { view = localStorage.getItem('pvef_kiosk_view') || 'nodes'; vmFilter = localStorage.getItem('pvef_kiosk_vmfilter') || 'all'; } catch (x) { /* niets */ }
  var DAYS = ['zo', 'ma', 'di', 'wo', 'do', 'vr', 'za'], MON = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
  var LOCK_CLOSED = 'M8 11V8a4 4 0 0 1 8 0v3', LOCK_OPEN = 'M8 11V8a4 4 0 0 1 7.5-2';
  function $(id) { return document.getElementById(id); }

  function tickClock() {
    var d = new Date();
    $('k-clock').textContent = PF.clock(d.getTime() / 1000);
    $('k-date').textContent = DAYS[d.getDay()] + ' ' + d.getDate() + ' ' + MON[d.getMonth()];
  }
  tickClock(); setInterval(tickClock, 5000);

  // ------------------------------------------------------------ cijfertoetsenbord
  function padHtml(backLabel) {
    var keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9'];
    return '<div class="k-pad" role="group" aria-label="Cijfertoetsen">' + keys.map(function (k) { return '<button type="button" data-key="' + k + '">' + k + '</button>'; }).join('') +
      '<button type="button" class="alt" data-key="back">' + e(backLabel || 'Terug') + '</button><button type="button" data-key="0">0</button>' +
      '<button type="button" class="alt" data-key="del" aria-label="Laatste cijfer wissen" style="font-size:22px">⌫</button></div>';
  }
  function dotsHtml(n, filled, err) {
    var s = '';
    for (var i = 0; i < n; i++) s += '<span class="' + (i < filled ? 'f' : '') + '"></span>';
    return '<div class="k-dots' + (err ? ' err' : '') + '" role="img" aria-label="' + filled + ' van ' + n + ' cijfers ingevoerd">' + s + '</div>';
  }

  // ------------------------------------------------------------ koppelen
  var pairCode = '', pairErr = '';
  function showPair() {
    var el = $('k-overlay');
    el.innerHTML = '<div class="k-center"><div class="k-pair"><div class="txt">' +
      '<div class="brand"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#E8A13A" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="6" rx="1.5"></rect><rect x="3" y="14" width="18" height="6" rx="1.5"></rect><path d="M7 7h.01M7 17h.01"></path></svg><span>PVE Fleet</span></div>' +
      '<h1 style="font-size:24px">Dit scherm koppelen</h1>' +
      '<p class="muted" style="margin:0;line-height:1.5">Open het dashboard op je pc, ga naar <strong style="color:var(--text)">Instellingen › Gekoppelde schermen › Scherm koppelen</strong> en voer hier de koppelcode van 6 cijfers in.</p>' +
      dotsHtml(6, pairCode.length, !!pairErr) + '<div role="alert" style="min-height:22px;color:var(--crit-text)">' + e(pairErr) + '</div></div>' +
      padHtml('Wissen') + '</div></div>';
  }
  function pairKey(k) {
    if (k === 'back') { pairCode = ''; pairErr = ''; return showPair(); }
    if (k === 'del') { pairCode = pairCode.slice(0, -1); pairErr = ''; return showPair(); }
    if (pairCode.length >= 6) return;
    pairCode += k; pairErr = '';
    showPair();
    if (pairCode.length === 6) {
      PF.postJSON('/kiosk/pair', { code: pairCode }).then(function () { location.reload(); }, function (err) {
        pairCode = ''; pairErr = err.message; showPair();
      });
    }
  }
  function showMessage(title, text) {
    $('k-overlay').innerHTML = '<div class="k-center"><div style="max-width:520px;display:flex;flex-direction:column;gap:12px"><h1 style="font-size:24px">' + e(title) + '</h1><p class="muted" style="margin:0;line-height:1.5">' + text + '</p></div></div>';
  }

  // ------------------------------------------------------------ overzicht
  function mini(label, p, warn, crit) {
    var l = PF.level(p, warn, crit);
    return '<span class="mini"><span style="width:30px">' + label + '</span><span class="bar"><span class="b-' + l + '" style="width:' + (p || 0) + '%"></span></span><span class="n">' + PF.pct(p) + '</span></span>';
  }
  function tile(g, n) {
    var off = n.status !== 'online';
    var note, noteCls = 'muted';
    if (off) { note = g.reachable ? 'Offline volgens cluster' : 'Onbereikbaar sinds ' + PF.clock(g.since); noteCls = 'c-crit'; }
    else if (n.mem_pct >= 80) { note = 'RAM hoog'; noteCls = 'c-warn'; }
    else if (n.updates && n.updates.count) { note = n.updates.count + ' updates'; noteCls = 'c-warn'; }
    else note = n.guests_running + ' van ' + n.guests_total + ' VM/CT actief';
    return '<button type="button" class="k-tile' + (off ? ' off' : '') + '" data-open="' + e(g.server) + '|' + e(n.name) + '" aria-label="' + e(n.name) + ', acties">' +
      '<span class="thead"><span class="tname">' + e(n.name) + '</span>' + PF.dotStatus(n.status) + '</span>' +
      (off ? '' : mini('CPU', n.cpu, 90, 95) + mini('RAM', n.mem_pct)) +
      '<span class="note ' + noteCls + '">' + e(note) + '</span></button>';
  }
  function groupMeta(g) {
    if (!g.reachable) return '<span class="gmeta c-crit">onbereikbaar</span>';
    if (g.kind !== 'cluster') return '';
    var ok = g.quorate && g.nodes_online === g.nodes_total;
    return '<span class="gmeta ' + (ok ? 'muted' : 'c-warn') + '">' + (g.quorate ? 'quorum OK' : 'GEEN QUORUM') + ' · ' + g.nodes_online + '/' + g.nodes_total + ' online</span>';
  }
  function canAck() { return ks && ks.allow_ack; }

  // ------------------------------------------------------------ VM's & containers
  function tabs(k) {
    var seg = function (id, cur, opts) {
      return '<div class="k-seg" role="group">' + opts.map(function (o) {
        return '<button type="button" data-' + id + '="' + o[0] + '" aria-pressed="' + (cur === o[0]) + '">' + o[1] + '</button>';
      }).join('') + '</div>';
    };
    return '<div class="k-tabsrow">' + seg('view', view, [['nodes', 'Nodes ' + k.nodes_online + '/' + k.nodes_total], ['vms', 'VM\'s &amp; containers ' + k.guests_running + '/' + k.guests_total]]) +
      (view === 'vms' ? seg('vmf', vmFilter, [['all', 'Alle'], ['running', 'Actief'], ['stopped', 'Gestopt']]) : '') + '</div>';
  }
  function vmTile(v) {
    var on = v.status === 'running', mp = v.maxmem ? 100 * (v.mem || 0) / v.maxmem : null;
    return '<button type="button" class="k-vm' + (on ? '' : ' stopped') + '" data-vm="' + e(v.server + '|' + v.type + '|' + v.vmid) + '" aria-label="' + e(v.name) + '">' +
      '<span class="thead"><span class="vname">' + e(v.name) + '</span><span class="dot b-' + PF.statusInfo(v.reachable ? v.status : 'unknown').cls + '" title="' + e(PF.statusInfo(v.reachable ? v.status : 'unknown').label) + '"></span></span>' +
      '<span class="vmeta"><span class="typebadge">' + (v.type === 'qemu' ? 'VM' : 'CT') + '</span> <span class="mono">' + v.vmid + '</span>' + (v.ip ? ' · <span class="mono">' + e(v.ip) + '</span>' : '') +
      (v.upd && ((v.upd.count || 0) > 0 || v.upd.app_update) ? ' · <span class="c-warn" title="updates">↑ ' + ((v.upd.count || 0) + (v.upd.app_update ? 1 : 0)) + '</span>' : '') + '</span>' +
      (on ? mini('CPU', v.cpu, 90, 95) + mini('RAM', mp) : '<span class="note muted">' + (v.reachable ? 'gestopt' : 'onbekend') + '</span>') + '</button>';
  }
  function vmsHtml() {
    if (!vms) return '<div class="muted">Laden…</div>';
    var list = vms.filter(function (v) { return vmFilter === 'all' || (vmFilter === 'running' ? v.status === 'running' : v.status !== 'running'); });
    if (!list.length) return '<div class="muted">Geen VM\'s of containers ' + (vmFilter === 'running' ? 'actief' : (vmFilter === 'stopped' ? 'gestopt' : 'gevonden')) + '.</div>';
    var groups = {}, order = [];
    list.forEach(function (v) {
      var gk = v.server, nk = v.node;
      if (!groups[gk]) { groups[gk] = { name: v.group, kind: v.kind, nodes: {}, norder: [] }; order.push(gk); }
      if (!groups[gk].nodes[nk]) { groups[gk].nodes[nk] = []; groups[gk].norder.push(nk); }
      groups[gk].nodes[nk].push(v);
    });
    return order.map(function (gk) {
      var g = groups[gk];
      return '<section class="k-group"><h2>' + e(g.name) + '</h2>' + g.norder.sort().map(function (nk) {
        var items = g.nodes[nk].sort(function (a, b) { return (a.status === 'running' ? 0 : 1) - (b.status === 'running' ? 0 : 1) || a.vmid - b.vmid; });
        return (g.kind === 'cluster' ? '<div class="k-nodehead mono">' + e(nk) + '</div>' : '') + '<div class="k-vms">' + items.map(vmTile).join('') + '</div>';
      }).join('') + '</section>';
    }).join('');
  }
  function loadVms() {
    if (view !== 'vms') return Promise.resolve();
    return PF.getJSON('/api/kiosk/vms').then(function (r) {
      vms = r.vms || [];
      if (data && view === 'vms') render(data);
      if (sheet && sheet.vm) drawSheet();
    }, function () {});
  }
  function findVm(key) {
    if (!vms) return null;
    return vms.filter(function (v) { return v.server + '|' + v.type + '|' + v.vmid === key; })[0] || null;
  }
  function render(d) {
    data = d;
    if (!d.ready) { setTimeout(reloadFleet, 1500); return; }
    var k = d.kpis, openAlerts = k.alerts_crit + k.alerts_warn;
    var st = $('k-status'), lvl = k.alerts_crit ? 'crit' : (k.alerts_warn ? 'warn' : 'ok');
    st.className = 'k-status ' + lvl;
    st.querySelector('.dot').className = 'dot b-' + lvl;
    $('k-headline').textContent = openAlerts ? (openAlerts === 1 ? '1 open melding' : openAlerts + ' open meldingen') : 'Alles in orde';
    $('k-sub').textContent = k.nodes_online + ' van ' + k.nodes_total + ' nodes online · ' + k.guests_running + ' VM/CT actief';
    var clusters = d.groups.filter(function (g) { return g.kind === 'cluster'; });
    var alone = d.groups.filter(function (g) { return g.kind !== 'cluster'; });
    var html = clusters.map(function (g) {
      return '<section class="k-group"><h2>' + e(g.name) + '</h2>' + groupMeta(g) + '<div class="k-tiles">' + g.nodes.map(function (n) { return tile(g, n); }).join('') + '</div></section>';
    }).join('');
    if (alone.length) {
      var offline = alone.filter(function (g) { return !g.reachable; }).length;
      html += '<section class="k-group"><h2>Losse nodes</h2>' + (offline ? '<span class="gmeta c-crit">' + offline + ' offline</span>' : '') +
        '<div class="k-tiles">' + alone.map(function (g) { return g.nodes.map(function (n) { return tile(g, n); }).join(''); }).join('') + '</div></section>';
    }
    $('k-main').innerHTML = tabs(k) + (view === 'vms' ? vmsHtml() : html);
    $('k-alerts').innerHTML = d.alerts.length ? d.alerts.map(function (a) { return PF.alertItem(a, canAck()); }).join('') : '<div class="muted">Geen meldingen.</div>';
    $('k-open').textContent = openAlerts + ' open';
    var total = k.backups_ok + k.backups_failed, b = $('k-backups');
    b.textContent = total ? k.backups_ok + '/' + total : '—';
    b.className = 'v ' + (k.backups_failed ? 'c-warn' : 'c-ok');
    $('k-backups-sub').textContent = total ? (k.backups_failed ? k.backups_failed + ' mislukt' : 'alles geslaagd') : 'geen taken';
    if (k.updates) {
      $('k-updates').textContent = k.updates;
      $('k-updates-sub').textContent = 'op ' + k.updates_nodes + ' nodes · ' + k.updates_security + ' security';
    } else if (k.update_work) {
      $('k-updates').textContent = k.update_work + (k.update_work === 1 ? ' node' : ' nodes');
      $('k-updates-sub').textContent = 'bijwerken of herstarten nodig';
    } else {
      $('k-updates').textContent = '0';
      $('k-updates-sub').textContent = 'alles bijgewerkt';
    }
    if (sheet && sheet.key && sheet.stage === 'actions') drawSheet();
  }
  function renderLock() {
    var el = $('k-lock');
    if (!ks || ks.readonly) { el.classList.toggle('hidden', !(ks && ks.readonly)); if (ks && ks.readonly) el.textContent = 'Alleen bekijken'; return; }
    el.classList.remove('hidden');
    var label, color, path = LOCK_CLOSED;
    if (!ks.pin_enabled) { label = 'Pincode uit'; color = 'var(--muted)'; path = LOCK_OPEN; }
    else if (ks.locked_for > 0) { label = 'Geblokkeerd'; color = 'var(--crit-text)'; }
    else if (ks.unlocked_for > 0) { label = 'Ontgrendeld · ' + Math.ceil(ks.unlocked_for / 60) + ' min'; color = '#A6E3C4'; path = LOCK_OPEN; }
    else { label = 'Acties vergrendeld'; color = 'var(--text2)'; }
    el.style.color = color;
    el.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"></rect><path d="' + path + '"></path></svg>' + e(label);
  }
  function loadState() {
    return PF.getJSON('/api/kiosk/state').then(function (s) {
      ks = s; renderLock();
      // Alleen een ingelogde beheerder kan terug naar het dashboard; een gekoppeld scherm niet.
      $('k-back').classList.toggle('hidden', !s.is_admin);
      return s;
    }, function (err) {
      if (err.status === 401) {
        if (err.body && err.body.error === 'setup') showMessage('Dashboard nog niet ingesteld', 'Open het dashboard eerst op een pc en maak het beheerdersaccount aan.');
        else { pairCode = ''; pairErr = ''; showPair(); }
      }
      throw err;
    });
  }

  // ------------------------------------------------------------ node-paneel
  function find(key) {
    if (!data || !key) return null;
    var p = key.split('|');
    for (var i = 0; i < data.groups.length; i++) {
      var g = data.groups[i];
      if (g.server !== p[0]) continue;
      for (var j = 0; j < g.nodes.length; j++) if (g.nodes[j].name === p[1]) return { g: g, n: g.nodes[j] };
    }
    return null;
  }
  var LABEL = { node_reboot: 'herstarten', node_shutdown: 'afsluiten', node_wol: 'wekken' };
  function confirmText(action, f) {
    var n = f.n, g = f.g, running = n.guests_running || 0;
    if (action === 'node_reboot') return { t: n.name + ' herstarten?', d: g.kind === 'cluster'
      ? 'Proxmox sluit de ' + running + ' actieve VM\'s & containers af volgens hun instellingen (VM/CT\'s met HA worden verplaatst als HA dat zo regelt). Daarna herstart de node; dat duurt meestal een paar minuten.'
      : 'De ' + running + ' actieve VM\'s & containers worden netjes afgesloten. Na de herstart starten de VM/CT\'s met "Start bij opstarten" weer vanzelf op.', danger: true };
    if (action === 'node_shutdown') return { t: n.name + ' afsluiten?', d: 'De node en alle ' + running + ' actieve VM\'s & containers gaan uit. Weer aanzetten kan via Wake-on-LAN (als dat is ingesteld) of met de aan-knop.', danger: true };
    return { t: 'Wake-on-LAN naar ' + n.name + '?', d: 'Er gaat een wekpakket naar ' + n.name + '. Het kan een paar minuten duren voor hij online is.', danger: false };
  }
  function needsPin(action) {
    return ks && ks.pin_enabled && ks.pin_set && ks.pin_actions.indexOf(action) >= 0 && !(ks.unlocked_for > 0);
  }
  function openSheet(key) { sheet = { key: key, stage: 'actions' }; pin = ''; pinErr = ''; drawSheet(); }
  function closeSheet() { sheet = null; pin = ''; pinErr = ''; $('k-sheet').innerHTML = ''; }
  function svgX() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>'; }

  function drawSheet() {
    var el = $('k-sheet');
    if (!sheet) { el.innerHTML = ''; return; }
    var inner = '';
    if (sheet.vm) {
      var v = findVm(sheet.vm);
      if (!v) { closeSheet(); return; }
      var vs = PF.statusInfo(v.reachable ? v.status : 'unknown'), von = v.status === 'running';
      el.innerHTML = '<div class="k-scrim"><button type="button" class="closer" data-close="1" aria-label="Sluiten"></button><section class="k-sheet" aria-label="' + e(v.name) + '">' +
        '<div class="shead"><h2>' + e(v.name) + '</h2><span class="pill c-' + vs.cls + '" style="border-color:currentColor"><span class="dot b-' + vs.cls + '"></span>' + vs.label + '</span>' +
        '<span class="muted">' + (v.type === 'qemu' ? 'VM' : 'Container') + ' ' + v.vmid + ' · ' + e(v.node) + (v.kind === 'cluster' ? ' · ' + e(v.group) : '') + '</span>' +
        '<button type="button" class="btn ghost x" data-close="1" aria-label="Sluiten">' + svgX() + '</button></div>' +
        '<div class="k-stats"><div><div class="l">CPU</div><div class="v">' + (von ? PF.pct(v.cpu) : '—') + '</div></div>' +
        '<div><div class="l">RAM</div><div class="v">' + (von ? PF.bytes(v.mem) : '—') + '</div><div class="small muted">van ' + PF.bytes(v.maxmem, 0) + '</div></div>' +
        '<div><div class="l">Uptime</div><div class="v">' + (von ? PF.uptime(v.uptime) : '—') + '</div></div>' +
        '<div><div class="l">IP-adres</div><div class="v" style="font-size:17px">' + e(v.ip || '—') + '</div>' + (v.ips && v.ips.length > 1 ? '<div class="small muted">+' + (v.ips.length - 1) + ' meer</div>' : '') + '</div></div>' +
        (v.upd && ((v.upd.count || 0) > 0 || v.upd.app_update) ? '<div class="small c-warn">Updates: ' + (v.upd.count ? v.upd.count + ' pakketten' + (v.upd.security ? ' (' + v.upd.security + ' security)' : '') : '') +
          (v.upd.count && v.upd.app_update ? ' · ' : '') + (v.upd.app_update ? e(v.upd.app) + ' ' + e(v.upd.app_installed || '') + ' → ' + e(v.upd.app_latest || '') : '') + '</div>' : '') +
        (v.tags && v.tags.length ? '<div class="small muted">' + v.tags.map(function (t) { return '#' + e(t); }).join(' ') + '</div>' : '') +
        (ks && ks.is_admin ? '<div class="k-acts"><a class="btn" href="' + PF.guestUrl(v.server, v.node, v.type, v.vmid) + '">Openen in dashboard</a></div>' : '') +
        '<p class="small muted" style="margin:0">Starten en stoppen van VM\'s &amp; containers doe je in het dashboard op je pc.</p></section></div>';
      return;
    }
    var f = sheet.key ? find(sheet.key) : null;
    if (sheet.key && !f) { closeSheet(); return; }
    var head;
    if (f) {
      var s = PF.statusInfo(f.n.status);
      head = '<div class="shead"><h2>' + e(f.n.name) + '</h2><span class="pill c-' + s.cls + '" style="border-color:currentColor"><span class="dot b-' + s.cls + '"></span>' + s.label + '</span>' +
        '<span class="muted">' + e(f.g.kind === 'cluster' ? f.g.name : 'losse node') + '</span>';
    } else {
      head = '<div class="shead"><h2 style="font-family:var(--sans)">Melding bevestigen</h2>';
    }
    head += '<button type="button" class="btn ghost x" data-close="1" aria-label="Sluiten">' + svgX() + '</button></div>';

    if (sheet.stage === 'actions') {
      var n = f.n, off = n.status !== 'online';
      inner = '<div class="k-stats"><div><div class="l">CPU</div><div class="v">' + PF.pct(n.cpu) + '</div></div><div><div class="l">RAM</div><div class="v">' + PF.pct(n.mem_pct) + '</div></div>' +
        '<div><div class="l">VM/CT</div><div class="v">' + n.guests_running + '/' + n.guests_total + '</div></div><div><div class="l">Uptime</div><div class="v">' + PF.uptime(n.uptime) + '</div></div></div>' +
        (off && f.g.error ? '<div class="banner crit"><div class="txt"><div class="desc mono">' + e(f.g.error) + '</div></div></div>' : '');
      var acts = [];
      if (!ks.readonly) {
        if (off) acts.push('<button type="button" class="btn primary" data-act="node_wol">Wake-on-LAN</button>', '<button type="button" class="btn" data-act="refresh">Opnieuw verbinden</button>');
        else acts.push('<button type="button" class="btn" data-act="node_reboot">Herstarten</button>', '<button type="button" class="btn danger" data-act="node_shutdown">Afsluiten</button>');
      }
      if (ks.is_admin) acts.push('<a class="btn" href="' + PF.nodeUrl(f.g.server, n.name) + '">Openen in dashboard</a>');
      inner += acts.length ? '<div class="k-acts">' + acts.join('') + '</div>' : '';
      inner += '<p class="small muted" style="margin:0">' + (ks.readonly ? 'Deze kiosk staat op alleen bekijken.' : 'Acties hier vragen altijd om bevestiging' + (ks.pin_enabled ? ' en, waar ingesteld, om de pincode' : '') + '. Voor alles verder: het dashboard op je pc.') + '</p>';
    } else if (sheet.stage === 'confirm') {
      var c = confirmText(sheet.action, f), lock = needsPin(sheet.action);
      inner = '<div class="k-confirm"><div class="ttl">' + e(c.t) + '</div><div class="desc">' + e(c.d) + '</div></div>' +
        '<div class="k-row2"><button type="button" class="btn" data-cancel="1">Annuleren</button>' +
        '<button type="button" class="btn ' + (c.danger ? 'danger solid' : 'primary') + '" data-go="1">' +
        (lock ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"></rect><path d="' + LOCK_CLOSED + '"></path></svg>' : '') +
        'Ja, ' + LABEL[sheet.action] + '</button></div>';
    } else if (sheet.stage === 'pin') {
      var len = ks.pin_len || 4, locked = ks.locked_for > 0;
      var what = sheet.ack ? 'Melding bevestigen' : confirmText(sheet.action, f).t;
      inner = '<div style="display:flex;flex-wrap:wrap;gap:28px;align-items:flex-start"><div style="flex:1 1 280px;min-width:0;display:flex;flex-direction:column;gap:12px;padding-top:6px">' +
        '<div><div style="font-size:22px;font-weight:600">Voer de pincode in</div><div class="muted" style="font-size:15px;margin-top:6px">' + e(what) + '</div></div>' +
        dotsHtml(len, pin.length, !!pinErr) + '<div role="alert" style="min-height:22px;font-size:14px;color:var(--crit-text)">' + e(pinErr) + '</div>' +
        '<div class="small muted" style="line-height:1.5">' + (ks.unlock_seconds ? 'Na een juiste pincode blijven acties ' + (ks.unlock_seconds >= 60 ? (ks.unlock_seconds / 60) + ' min' : ks.unlock_seconds + ' s') + ' ontgrendeld.' : 'De pincode wordt bij elke actie gevraagd.') +
        ' Aan- of uitzetten doe je in het dashboard onder Instellingen.</div></div>' +
        (locked ? '<div class="muted" style="flex:0 0 300px">Geblokkeerd na te veel foute pogingen.</div>' : padHtml('Terug')) + '</div>';
    }
    el.innerHTML = '<div class="k-scrim"><button type="button" class="closer" data-close="1" aria-label="Sluiten"></button><section class="k-sheet" aria-label="Acties">' + head + inner + '</section></div>';
  }

  function execute(action) {
    var f = find(sheet.key);
    var body = { action: action, server: f.g.server, node: f.n.name };
    if (pin) body.pin = pin;
    return PF.postJSON('/api/kiosk/action', body).then(function (res) {
      closeSheet();
      PFA.toast(res.message || 'Opdracht verstuurd');
      loadState(); setTimeout(reloadFleet, 1500);
    }, function (err) { handleErr(err); });
  }
  function ack(id, acked) {
    var body = { id: id, acked: acked };
    if (pin) body.pin = pin;
    return PF.postJSON('/api/kiosk/ack', body).then(function () {
      if (sheet && sheet.ack) closeSheet();
      loadState(); reloadFleet();
    }, function (err) { handleErr(err); });
  }
  function handleErr(err) {
    var b = err.body || {};
    if (b.code === 'pin' || b.code === 'locked') {
      if (!sheet) return;
      pinErr = b.code === 'pin' && !pin ? '' : err.message;
      pin = '';
      sheet.stage = 'pin';
      if (b.code === 'locked') ks.locked_for = b.locked_for || 60;
      loadState().then(drawSheet, drawSheet);
      drawSheet();
      return;
    }
    closeSheet();
    PFA.toast(err.message, true);
  }
  function submitPin() {
    if (sheet.ack) ack(sheet.ack.id, sheet.ack.acked); else execute(sheet.action);
  }
  function pinKey(k) {
    if (k === 'back') { pin = ''; pinErr = ''; if (sheet.ack) return closeSheet(); sheet.stage = 'confirm'; return drawSheet(); }
    if (k === 'del') { pin = pin.slice(0, -1); pinErr = ''; return drawSheet(); }
    var len = ks.pin_len || 4;
    if (pin.length >= len) return;
    pin += k; pinErr = '';
    drawSheet();
    if (pin.length === len) submitPin();
  }

  document.getElementById('root').addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t.closest) return;
    var b = t.closest('[data-key]');
    if (b) {
      var k = b.getAttribute('data-key');
      if ($('k-overlay').innerHTML) return pairKey(k);
      if (sheet && sheet.stage === 'pin') return pinKey(k);
      return;
    }
    b = t.closest('[data-open]');
    if (b) return openSheet(b.getAttribute('data-open'));
    b = t.closest('[data-vm]');
    if (b) { sheet = { vm: b.getAttribute('data-vm'), stage: 'vm' }; return drawSheet(); }
    b = t.closest('[data-view]');
    if (b) {
      view = b.getAttribute('data-view');
      try { localStorage.setItem('pvef_kiosk_view', view); } catch (x) { /* niets */ }
      if (data) render(data);
      return loadVms();
    }
    b = t.closest('[data-vmf]');
    if (b) {
      vmFilter = b.getAttribute('data-vmf');
      try { localStorage.setItem('pvef_kiosk_vmfilter', vmFilter); } catch (x) { /* niets */ }
      if (data) render(data);
      return;
    }
    if (t.closest('[data-close]')) return closeSheet();
    if (t.closest('[data-cancel]')) { sheet.stage = 'actions'; return drawSheet(); }
    b = t.closest('[data-act]');
    if (b && sheet) {
      var a = b.getAttribute('data-act');
      if (a === 'refresh') {
        var f = find(sheet.key);
        PF.postJSON('/api/kiosk/action', { action: 'refresh', server: f.g.server, node: f.n.name })
          .then(function () { PFA.toast('Opnieuw opgehaald'); reloadFleet(); }, function (err) { PFA.toast(err.message, true); });
        return;
      }
      sheet.action = a; sheet.stage = 'confirm'; return drawSheet();
    }
    if (t.closest('[data-go]') && sheet) {
      if (needsPin(sheet.action)) { sheet.stage = 'pin'; pin = ''; pinErr = ''; return drawSheet(); }
      return execute(sheet.action);
    }
    b = t.closest('[data-ack]');
    if (b) {
      var id = b.getAttribute('data-ack'), acked = !b.getAttribute('data-acked');
      if (needsPin('ack')) { sheet = { key: null, stage: 'pin', ack: { id: id, acked: acked } }; pin = ''; pinErr = ''; return drawSheet(); }
      sheet = null; return ack(id, acked);
    }
  });

  // Toetsenbord (niet-touch): Esc sluit een paneel; Esc zonder paneel gaat als beheerder terug naar het dashboard.
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape') return;
    if (sheet) { closeSheet(); return; }
    if (ks && ks.is_admin && !$('k-overlay').innerHTML) location.href = '/';
  });

  function reloadFleet() { return PF.getJSON('/api/fleet').then(render, function () {}); }
  // Gekoppeld via de QR-code: /kiosk?pair=<sleutel>
  var pairToken = (location.search.match(/[?&]pair=([A-Za-z0-9_-]{10,64})/) || [])[1];
  if (pairToken) {
    showMessage('Scherm koppelen…', 'Even geduld.');
    PF.postJSON('/kiosk/pair', { token: pairToken }).then(function () {
      try { history.replaceState(null, '', '/kiosk'); } catch (x) { /* niets */ }
      location.replace('/kiosk');
    }, function (err) {
      try { history.replaceState(null, '', '/kiosk'); } catch (x) { /* niets */ }
      pairCode = ''; pairErr = err.message + ' Vraag een nieuwe code of voer de cijfercode in.';
      showPair();
    });
    return;
  }
  loadState().then(function () {
    PF.poll('/api/fleet', render);
    loadVms(); setInterval(loadVms, window.PF_POLL_MS || 10000);
    setInterval(function () { loadState().then(function () { if (sheet && sheet.stage === 'confirm') drawSheet(); }, function () {}); }, 15000);
  }, function () { /* koppelscherm of melding staat al */ });
})();
