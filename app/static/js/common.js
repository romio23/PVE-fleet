/* Gedeelde hulpfuncties. Bewust zonder ?. en ?? zodat oudere Safari (iPad) meedoet. */
var PF = (function () {
  var COLORS = { ok: '#4CC38A', warn: '#E8A13A', crit: '#F06A5E', info: '#6CA6E8', muted: '#9AA3AA' };

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function bytes(b, digits) {
    if (b === null || b === undefined) return '—';
    var u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'], i = 0;
    b = Number(b);
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    var d = digits !== undefined ? digits : (b >= 100 || i === 0 ? 0 : 1);
    return b.toFixed(d).replace('.', ',') + ' ' + u[i];
  }
  function rate(bps) {
    if (bps === null || bps === undefined) return '—';
    var mb = bps * 8 / 1e6;
    return (mb >= 100 ? mb.toFixed(0) : mb.toFixed(1)).replace('.', ',') + ' Mb/s';
  }
  function pct(p) { return (p === null || p === undefined) ? '—' : Math.round(p) + '%'; }
  function uptime(s) {
    if (!s) return '—';
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d > 0) return d + ' d ' + h + ' ' + HU;
    if (h > 0) return h + ' ' + HU + ' ' + m + ' m';
    return m + ' m';
  }
  function clock(ts) {
    var d = new Date(ts * 1000);
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }
  var EN = !!(window.I18N && window.I18N.lang === 'en'), HU = EN ? 'h' : 'u';
  var MONTHS = EN ? ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] : ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
  function when(ts) {
    if (!ts) return '—';
    var d = new Date(ts * 1000), now = new Date();
    if (d.toDateString() === now.toDateString()) return clock(ts);
    var y = new Date(now.getTime() - 86400000);
    if (d.toDateString() === y.toDateString()) return (EN ? 'yesterday ' : 'gisteren ') + clock(ts);
    return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + clock(ts);
  }
  function since(ts) {
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return Math.round(s) + ' s';
    if (s < 5400) return Math.round(s / 60) + ' min';
    if (s < 172800) return Math.floor(s / 3600) + ' ' + HU + ' ' + Math.round((s % 3600) / 60) + ' m';
    return Math.floor(s / 86400) + ' d';
  }
  function level(p, warn, crit) {
    if (p === null || p === undefined) return 'muted';
    if (p >= (crit || 90)) return 'crit';
    if (p >= (warn || 80)) return 'warn';
    return 'info';
  }
  function statusInfo(status) {
    if (status === 'online' || status === 'running') return { cls: 'ok', label: status === 'running' ? 'Actief' : 'Online' };
    if (status === 'stopped') return { cls: 'muted', label: 'Gestopt' };
    if (status === 'paused') return { cls: 'warn', label: 'Gepauzeerd' };
    if (status === 'unknown') return { cls: 'muted', label: 'Node offline' };
    return { cls: 'crit', label: 'Offline' };
  }
  function seenNote(row) {
    if (!row || !row.last_seen) return '';
    return '<div class="small muted" title="Gegevens van ' + esc(when(row.last_seen)) + ', toen de node nog online was">laatst gezien ' + esc(when(row.last_seen)) + '</div>';
  }
  function dotStatus(status) {
    var s = statusInfo(status);
    return '<span class="st c-' + s.cls + '"><span class="dot b-' + s.cls + '" aria-hidden="true"></span>' + s.label + '</span>';
  }
  function barCell(p, warn, crit) {
    var l = level(p, warn, crit);
    var w = p === null || p === undefined ? 0 : Math.max(0, Math.min(100, p));
    return '<div class="barcell"><div class="bar"><span class="b-' + l + '" style="width:' + w + '%"></span></div><span class="num">' + pct(p) + '</span></div>';
  }
  function spark(values, cls, label) {
    var v = (values || []).filter(function (x) { return x !== null && x !== undefined && !isNaN(x); });
    if (v.length < 2) return '<svg viewBox="0 0 200 44" preserveAspectRatio="none" aria-hidden="true"></svg>';
    var max = Math.max.apply(null, v), min = Math.min.apply(null, v);
    if (max === min) { max += 1; min -= 1; }
    var pts = v.map(function (x, i) {
      return (i * 200 / (v.length - 1)).toFixed(1) + ',' + (40 - (x - min) / (max - min) * 36).toFixed(1);
    }).join(' ');
    return '<svg viewBox="0 0 200 44" preserveAspectRatio="none" role="img" aria-label="' + esc(label || 'verloop') + '">' +
      '<polyline points="' + pts + '" fill="none" stroke="' + COLORS[cls || 'info'] + '" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke"></polyline></svg>';
  }
  function last(arr) {
    if (!arr) return null;
    for (var i = arr.length - 1; i >= 0; i--) if (arr[i] !== null && arr[i] !== undefined) return arr[i];
    return null;
  }
  function _handle(r) {
    return r.json().catch(function () { return {}; }).then(function (b) {
      if (r.status === 401 && (b.error === 'login' || b.error === 'setup') && !window.PF_KIOSK) {
        window.location.href = b.error === 'setup' ? '/setup' : '/login?next=' + encodeURIComponent(location.pathname);
      }
      if (!r.ok) {
        var err = new Error(b.error === 'login' ? 'Log opnieuw in' : (b.error || ('HTTP ' + r.status)));
        err.status = r.status; err.body = b;
        throw err;
      }
      return b;
    });
  }
  function getJSON(url) {
    return fetch(url, { cache: 'no-store', credentials: 'same-origin' }).then(_handle);
  }
  /* Schrijfverzoek; de header X-PVEF is verplicht voor de server (CSRF-bescherming). */
  function send(method, url, body) {
    return fetch(url, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-PVEF': '1' },
      body: body === undefined ? undefined : JSON.stringify(body) }).then(_handle);
  }
  function postJSON(url, body) { return send('POST', url, body || {}); }
  /* Haal periodiek op en render; toont status 'live' rechtsboven. */
  function poll(url, render, ms, onError) {
    var liveEl = document.getElementById('live');
    var ok = false;
    function tick() {
      getJSON(typeof url === 'function' ? url() : url).then(function (data) {
        ok = true;
        render(data);
        if (liveEl) { liveEl.classList.remove('stale'); liveEl.querySelector('.txt').textContent = 'Live · bijgewerkt ' + clock(Date.now() / 1000); }
      }).catch(function (e) {
        if (liveEl) { liveEl.classList.add('stale'); liveEl.querySelector('.txt').textContent = 'Fout: ' + e.message; }
        if (!ok && onError) onError(e);
      });
    }
    tick();
    return setInterval(tick, ms || window.PF_POLL_MS || 10000);
  }
  function nodeUrl(server, node) { return '/node/' + encodeURIComponent(server) + '/' + encodeURIComponent(node); }
  function guestUrl(server, node, type, vmid) {
    return '/guest/' + encodeURIComponent(server) + '/' + encodeURIComponent(node) + '/' + type + '/' + vmid;
  }
  var LVL = { crit: (window.I18N && window.I18N.lang === 'en') ? 'CRIT' : 'KRIT', warn: (window.I18N && window.I18N.lang === 'en') ? 'WARN' : 'WAARS', info: 'INFO' };
  function alertItem(a, withAck) {
    var cls = a.acked ? 'muted' : (a.level === 'crit' ? 'crit' : 'warn');
    return '<div class="alert' + (a.acked ? ' acked' : '') + '"><span class="lvl c-' + cls + '">' + (a.acked ? 'GEZIEN' : LVL[a.level]) + '</span>' +
      '<div style="min-width:0;flex:1"><div style="font-size:13px;line-height:1.4">' + esc(a.text) + '</div>' +
      '<div class="small muted" style="margin-top:3px">sinds ' + clock(a.since) + ' · ' + since(a.since) + '</div></div>' +
      (withAck ? '<button type="button" class="btn ghost iconbtn" data-ack="' + esc(a.id) + '" data-acked="' + (a.acked ? '1' : '') + '" aria-label="' + (a.acked ? 'Markering ongedaan maken' : 'Als gezien markeren') + '" title="' + (a.acked ? 'Gezien' : 'Als gezien markeren') + '">' +
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg></button>' : '') +
      '</div>';
  }
  function bindAcks(root, after) {
    root.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-ack]') : null;
      if (!b) return;
      postJSON(window.PF_ACK_URL || '/api/alerts/ack', { id: b.getAttribute('data-ack'), acked: !b.getAttribute('data-acked') }).then(after, function (err) { if (window.PFA) PFA.toast(err.message, true); });
    });
  }
  var TASKS = { vzdump: 'Back-up', qmigrate: 'Migratie VM', vzmigrate: 'Migratie CT', qmstart: 'Start VM', qmstop: 'Stop VM',
    qmshutdown: 'Afsluiten VM', qmreboot: 'Herstart VM', vzstart: 'Start CT', vzstop: 'Stop CT', vzshutdown: 'Afsluiten CT',
    aptupdate: 'Pakketlijst bijwerken', qmsnapshot: 'Snapshot VM', vzsnapshot: 'Snapshot CT', qmrollback: 'Rollback VM',
    vzrollback: 'Rollback CT', qmclone: 'Klonen VM', vzclone: 'Klonen CT', startall: 'Alles starten', stopall: 'Alles stoppen',
    srvreload: 'Dienst herladen', imgdel: 'Schijf verwijderen', qmcreate: 'VM aanmaken', vzcreate: 'CT aanmaken',
    qmdelsnapshot: 'Snapshot verwijderen VM', vzdelsnapshot: 'Snapshot verwijderen CT', qmdestroy: 'VM verwijderen', vzdestroy: 'CT verwijderen',
    qmrestore: 'Herstellen VM', vzrestore: 'Herstellen CT', qmsuspend: 'Pauzeren VM', qmresume: 'Hervatten VM', qmpause: 'Pauzeren VM',
    qmreset: 'Reset VM', qmconfig: 'Configuratie VM', vzreboot: 'Herstart CT', vzsuspend: 'Pauzeren CT', vzresume: 'Hervatten CT',
    qmmove: 'Schijf verplaatsen VM', move_volume: 'Schijf verplaatsen CT', qmtemplate: 'Template maken VM', vztemplate: 'Template maken CT',
    resize: 'Schijf vergroten', download: 'Downloaden', aptupgrade: 'Pakketten bijwerken', migrateall: 'Alles migreren',
    hamigrate: 'HA-migratie', hastart: 'HA-start', hastop: 'HA-stop', hashutdown: 'HA-afsluiten', imgcopy: 'Schijf kopiëren',
    zfscreate: 'ZFS aanmaken', lvmcreate: 'LVM aanmaken', dircreate: 'Map aanmaken', reboot: 'Node herstarten', shutdown: 'Node afsluiten',
    wakeonlan: 'Wake-on-LAN', acmerenew: 'Certificaat vernieuwen', acmenewcert: 'Certificaat aanvragen', pull_file: 'Bestand ophalen',
    vncproxy: 'Console VM', termproxy: 'Console CT', vncshell: 'Shell', spiceproxy: 'SPICE-console VM', spiceshell: 'SPICE-shell',
    qmigrate_storage: 'Opslag migreren', diskinit: 'Schijf initialiseren', wipedisk: 'Schijf wissen', cephcreateosd: 'Ceph OSD aanmaken' };
  var CONSOLE_TASKS = { vncproxy: 1, termproxy: 1, vncshell: 1, spiceproxy: 1, spiceshell: 1 };
  function isConsoleTask(t) { return !!CONSOLE_TASKS[t.type]; }
  function taskName(t) {
    // console- en shelltaken: met id een VM/CT, zonder id de shell van de node
    if (isConsoleTask(t) && !t.id) return (t.type === 'spiceshell' ? 'SPICE-shell ' : 'Shell ') + (t.node || '');
    if (t.type === 'termproxy' && t.id) return 'Console CT ' + t.id;
    return (TASKS[t.type] || t.type) + (t.id ? ' ' + t.id : '');
  }
  function showConsoleTasks() { try { return localStorage.getItem('pvef_tasks_console') === '1'; } catch (x) { return false; } }
  function toggleConsoleTasks() { try { localStorage.setItem('pvef_tasks_console', showConsoleTasks() ? '0' : '1'); } catch (x) { /* niets */ } }
  /* Takenlijst: consoletaken standaard verborgen. Geeft {list, hidden, toggle (html-knop)}. */
  function visibleTasks(tasks, limit) {
    var show = showConsoleTasks();
    var con = tasks.filter(isConsoleTask).length;
    var list = show ? tasks : tasks.filter(function (t) { return !isConsoleTask(t); });
    var toggle = con ? '<button type="button" class="btn ghost" data-contasks="1" style="height:24px;padding:0 8px;font-size:11.5px;margin-left:auto" title="Console- en shelltaken (vncproxy, termproxy) tonen of verbergen">' +
      (show ? 'Consoletaken verbergen' : 'Consoletaken tonen (' + con + ')') + '</button>' : '';
    return { list: limit ? list.slice(0, limit) : list, hidden: show ? 0 : con, toggle: toggle };
  }
  function taskResult(t) {
    if (t.running) return '<span class="c-info">Bezig</span>';
    return t.ok ? '<span class="c-ok">OK</span>' : '<span class="c-crit" title="' + esc(t.status) + '">Mislukt</span>';
  }
  /* Vertaal een verbindingsfout naar een concrete tip. */
  function errorHint(msg) {
    msg = String(msg || '');
    if (/aanmelden bij Proxmox mislukt/.test(msg)) return 'Controleer de token-ID (vorm: gebruiker@pve!tokennaam) en het token-geheim (de lange code die Proxmox één keer toont). Aanpassen kan bij Servers & verbinding › Bewerken.';
    if (/geen rechten/.test(msg)) return 'Het token mist leesrechten. Geef gebruiker en token de rol PVEAuditor op pad / (Datacenter › Permissions), of maak het token met Privilege Separation uit.';
    if (/time-out/.test(msg)) return 'Deze computer krijgt geen antwoord. Klopt het IP-adres? Open ter controle https://<ip>:8006 in je browser; een firewall of ander netwerk/VLAN kan de oorzaak zijn.';
    if (/verbinding geweigerd/.test(msg)) return 'De server is bereikbaar, maar poort 8006 is dicht. Klopt het adres, en draait Proxmox op deze machine?';
    if (/SSL|certificate|CERTIFICATE/.test(msg)) return 'Certificaatprobleem: zet verify_ssl: false in config.yml (standaard bij Proxmox).';
    return '';
  }
  /* Aantallen in het zijmenu (Nodes, VM's & containers, Opslag). */
  function navCounts(d) {
    if (!d || !d.ready || !d.kpis) return;
    var k = d.kpis, n = document.getElementById('nav-nodes'), v = document.getElementById('nav-vms'), s = document.getElementById('nav-storage');
    if (n) {
      var off = k.nodes_total - k.nodes_online;
      n.innerHTML = off ? '<span class="c-crit" title="' + off + ' offline">' + off + ' off</span> · ' + k.nodes_total : String(k.nodes_total);
    }
    if (v) v.textContent = k.guests_running + '/' + k.guests_total;
    var u = document.getElementById('nav-updates'), b = document.getElementById('nav-backups');
    var uw = k.update_work !== undefined ? k.update_work : k.updates_nodes, gw = k.guest_update_work || 0;
    var ut = [];
    if (uw) ut.push(uw + (uw === 1 ? ' node' : ' nodes'));
    if (gw) ut.push(gw + (gw === 1 ? ' container' : ' containers'));
    if (u) u.innerHTML = uw + gw ? '<span class="navbadge" title="Bijwerken nodig: ' + ut.join(', ') + '">' + (uw + gw) + '</span>' : '';
    if (b) b.innerHTML = k.backup_failed ? '<span class="c-crit" title="mislukte back-ups (24 uur)">' + k.backup_failed + ' fout</span>' : '';
    if (s) s.innerHTML = k.storage >= 90 ? '<span class="c-crit">' + Math.round(k.storage) + '%</span>' : (k.storage >= 80 ? '<span class="c-warn">' + Math.round(k.storage) + '%</span>' : '');
  }
  function navAuto() {
    if (!document.getElementById('nav-nodes') || window.PF_NAV_MANUAL) return;
    function tick() { getJSON('/api/fleet').then(navCounts, function () {}); }
    tick(); setInterval(tick, 30000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', navAuto); else setTimeout(navAuto, 0);
  /* Ingebouwde console in een eigen venster openen. */
  function consoleUrl(server, node, type, vmid, extra) {
    return '/console/' + encodeURIComponent(server) + '/' + encodeURIComponent(node) + '?type=' + type + (vmid ? '&vmid=' + vmid : '') + (extra || '');
  }
  function openConsole(server, node, type, vmid, extra) {
    var name = 'pvef-console-' + (server + '-' + node + '-' + type + '-' + (vmid || '')).replace(/[^A-Za-z0-9_-]/g, '_');
    var w = window.open(consoleUrl(server, node, type, vmid, extra), name, 'width=1024,height=700');
    if (!w) location.href = consoleUrl(server, node, type, vmid, extra);   // pop-ups geblokkeerd: dan in dit tabblad
    else try { w.focus(); } catch (x) { /* niets */ }
    return w;
  }
  /* Console met upgrade-hulp; roept onClosed aan zodra het venster dicht is. */
  function openUpgrade(server, node, onClosed, vmid, cmd, win) {
    var w;
    if (win && !win.closed) {   // venster is al open (bijv. tijdens het maken van een snapshot)
      win.location.href = consoleUrl(server, node, vmid ? 'lxc' : 'shell', vmid, vmid ? '&cmd=' + encodeURIComponent(cmd || 'apt') : '&upgrade=1');
      w = win;
    } else {
      w = vmid ? openConsole(server, node, 'lxc', vmid, '&cmd=' + encodeURIComponent(cmd || 'apt'))
        : openConsole(server, node, 'shell', null, '&upgrade=1');
    }
    if (!w) return;
    var t = setInterval(function () {
      var closed = true;
      try { closed = w.closed; } catch (x) { closed = true; }
      if (closed) { clearInterval(t); if (onClosed) onClosed(); }
    }, 1500);
  }
  /* IP-adres in een tabel: eerste adres, met de rest in de tooltip. */
  function ipCell(ips, src) {
    ips = ips || [];
    if (!ips.length) return '<span class="muted">—</span>';
    var tip = ips.join(', ') + (src === 'config' ? ' (vast adres uit de configuratie)' : (src === 'cache' ? ' (laatst bekende adres; de node is offline)' : ''));
    return '<span class="mono ipaddr" title="' + esc(tip) + '">' + esc(ips[0]) + '</span>' +
      (ips.length > 1 ? ' <span class="small muted" title="' + esc(tip) + '">+' + (ips.length - 1) + '</span>' : '');
  }
  return { openUpgrade: openUpgrade, ipCell: ipCell, seenNote: seenNote, COLORS: COLORS, errorHint: errorHint, navCounts: navCounts, consoleUrl: consoleUrl, openConsole: openConsole, esc: esc, bytes: bytes, rate: rate, pct: pct, uptime: uptime, clock: clock, when: when, since: since,
    level: level, statusInfo: statusInfo, dotStatus: dotStatus, barCell: barCell, spark: spark, last: last, getJSON: getJSON,
    postJSON: postJSON, send: send, poll: poll, nodeUrl: nodeUrl, guestUrl: guestUrl, alertItem: alertItem, bindAcks: bindAcks,
    taskName: taskName, taskResult: taskResult, isConsoleTask: isConsoleTask, visibleTasks: visibleTasks, toggleConsoleTasks: toggleConsoleTasks };
})();
