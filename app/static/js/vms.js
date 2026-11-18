(function () {
  var e = PF.esc;
  var p = PL.params();
  var DEF = { q: '', status: 'all', type: 'all', group: '', sort: 'name', dir: 'asc' };
  var st = { q: p.q || '', status: p.status || 'all', type: p.type || 'all', group: p.group || '', sort: p.sort || 'name',
    dir: p.dir || (p.sort && ['cpu', 'ram', 'uptime'].indexOf(p.sort) >= 0 ? 'desc' : 'asc') };
  var data = null, sel = {};
  var $ = function (id) { return document.getElementById(id); };
  $('q').value = st.q;
  function k(v) { return v.server + '|' + v.type + '|' + v.vmid; }
  function gobj(v) { return { server: v.server, node: v.node, type: v.type, vmid: v.vmid, name: v.name, status: v.status }; }
  function hasUpd(v) { return !!(v.upd && ((v.upd.count || 0) > 0 || v.upd.app_update)); }
  function updCell(v) {
    var u = v.upd;
    if (!u) return '<span class="muted small" title="' + (v.type === 'qemu' ? 'VM\'s worden nog niet gecontroleerd' : 'Nog niet gecontroleerd (Updates › Updates in VM\'s & containers)') + '">—</span>';
    var parts = [];
    if (u.count) parts.push('<span class="c-' + (u.security ? 'crit' : 'warn') + '" title="' + u.count + ' pakketupdates' + (u.security ? ', ' + u.security + ' security' : '') + '">' + u.count + (u.security ? ' (' + u.security + ' sec.)' : '') + '</span>');
    if (u.app_update) parts.push('<span class="pill c-info" style="border-color:currentColor;padding:1px 7px;font-size:11.5px" title="' + PF.esc(u.app + ' ' + (u.app_installed || '') + ' → ' + (u.app_latest || '')) + '">' + PF.esc(u.app) + ' ↑</span>');
    if (!parts.length) parts.push('<span class="c-ok small">✓</span>');
    return parts.join(' ') + (u.stale ? ' <span class="small muted" title="Gestopt; laatst bekende uitslag">·</span>' : '');
  }
  function memPct(v) { return v.maxmem ? 100 * (v.mem || 0) / v.maxmem : null; }

  function filtered() {
    var q = st.q.toLowerCase();
    return data.vms.filter(function (v) {
      if (st.status === 'running' && v.status !== 'running') return false;
      if (st.status === 'stopped' && v.status === 'running') return false;
      if (st.status === 'updates' && !hasUpd(v)) return false;
      if (st.type !== 'all' && v.type !== st.type) return false;
      if (st.group) {
        var g = st.group.split('|');
        if (v.server !== g[0] || (g[1] && v.node !== g[1])) return false;
      }
      if (q && (v.vmid + ' ' + v.name + ' ' + v.node + ' ' + v.group + ' ' + v.tags.join(' ') + ' ' + (v.ips || []).join(' ')).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }
  function key(v) {
    switch (st.sort) {
      case 'vmid': return v.vmid;
      case 'status': return v.status;
      case 'ip': return v.ip || null;
      case 'upd': return v.upd ? (v.upd.count || 0) + (v.upd.app_update ? 1000 : 0) : null;
      case 'node': return v.group + ' ' + v.node;
      case 'cpu': return v.status === 'running' ? v.cpu : null;
      case 'ram': return v.status === 'running' ? memPct(v) : null;
      case 'uptime': return v.uptime || null;
      default: return (v.name || '').toLowerCase();
    }
  }
  function groupOptions() {
    var seen = {}, opts = [];
    data.vms.forEach(function (v) {
      if (!seen[v.server]) { seen[v.server] = {}; opts.push({ v: v.server, l: v.group + (v.kind === 'cluster' ? ' (cluster)' : '') }); }
      if (v.kind === 'cluster' && !seen[v.server][v.node]) { seen[v.server][v.node] = 1; opts.push({ v: v.server + '|' + v.node, l: '   ' + v.node }); }
    });
    return opts;
  }
  function selected() { return data.vms.filter(function (v) { return sel[k(v)]; }); }
  function renderBar() {
    var list = selected();
    if (!list.length) { $('bar').innerHTML = ''; return; }
    var nodes = {}, servers = {};
    list.forEach(function (v) { nodes[v.server + '|' + v.node] = 1; servers[v.server] = 1; });
    var oneNode = Object.keys(nodes).length === 1, cluster = list[0].kind === 'cluster';
    $('bar').innerHTML = '<div class="selbar"><span class="cnt">' + list.length + ' geselecteerd' + (oneNode ? '' : ' · op ' + Object.keys(nodes).length + ' nodes') + '</span>' +
      '<button type="button" class="btn ghost" data-bulk="start">Starten</button>' +
      '<button type="button" class="btn ghost" data-bulk="shutdown">Afsluiten</button>' +
      '<button type="button" class="btn ghost" data-bulk="reboot">Herstarten</button>' +
      '<button type="button" class="btn ghost" data-bulk="snapshot">Snapshot</button>' +
      '<button type="button" class="btn ghost" data-bulk="backup"' + (oneNode ? '' : ' disabled title="Back-up in één keer kan alleen voor VM/CT\'s op dezelfde node"') + '>Back-up nu</button>' +
      (cluster && oneNode ? '<button type="button" class="btn blue" data-bulk="migrate">Migreren…</button>' : '') +
      '<button type="button" class="btn ghost" data-bulk="clear" style="margin-left:auto">Selectie wissen</button></div>';
  }
  function render() {
    if (!data) return;
    var cur = st.group;
    $('grp').innerHTML = '<option value="">Alle clusters en nodes</option>' + groupOptions().map(function (o) {
      return '<option value="' + e(o.v) + '"' + (o.v === cur ? ' selected' : '') + '>' + e(o.l) + '</option>';
    }).join('');
    PL.seg($('f-status'), st.status); PL.seg($('f-type'), st.type); PL.sortHeads(st);
    var present = {};
    data.vms.forEach(function (v) { present[k(v)] = 1; });
    Object.keys(sel).forEach(function (x) { if (!present[x]) delete sel[x]; });
    var list = filtered();
    list.sort(function (a, b) { return PL.cmp(key(a), key(b), st.dir) || PL.cmp(a.vmid, b.vmid, 'asc'); });
    var run = data.vms.filter(function (v) { return v.status === 'running'; }).length;
    $('sub').textContent = data.vms.length + ' VM\'s & containers · ' + run + ' actief' + (list.length !== data.vms.length ? ' · ' + list.length + ' getoond' : '');
    $('selall').checked = list.length > 0 && list.every(function (v) { return sel[k(v)]; });
    renderBar();
    $('rows').innerHTML = list.length ? list.map(function (v) {
      var on = v.status === 'running', url = PF.guestUrl(v.server, v.node, v.type, v.vmid), mp = memPct(v);
      return '<tr class="' + (sel[k(v)] ? 'sel' : '') + '">' +
        '<td><input type="checkbox" class="cb" data-sel="' + e(k(v)) + '"' + (sel[k(v)] ? ' checked' : '') + (v.reachable ? '' : ' disabled') + ' aria-label="Selecteer ' + e(v.name) + '"></td>' +
        '<td class="mono muted">' + v.vmid + '</td>' +
        '<td><a href="' + url + '" style="font-weight:500' + (v.cached ? ';opacity:.75' : '') + '">' + e(v.name) + '</a>' + (v.tags.length ? ' <span class="small muted">' + v.tags.map(function (t) { return '#' + e(t); }).join(' ') + '</span>' : '') + '</td>' +
        '<td><span class="typebadge">' + (v.type === 'qemu' ? 'VM' : 'CT') + '</span></td>' +
        '<td>' + PF.dotStatus(v.status) + PF.seenNote(v) + '</td>' +
        '<td>' + PF.ipCell(v.ips, v.ip_src) + '</td>' +
        '<td class="mono small">' + updCell(v) + '</td>' +
        '<td><a class="mono" href="' + PF.nodeUrl(v.server, v.node) + '">' + e(v.node) + '</a>' + (v.kind === 'cluster' ? ' <span class="small muted">' + e(v.group) + '</span>' : '') + '</td>' +
        '<td class="mono">' + (on ? PF.pct(v.cpu) : '—') + '</td>' +
        '<td class="mono" title="' + (v.maxmem ? PF.bytes(v.mem) + ' / ' + PF.bytes(v.maxmem, 0) : '') + '">' + (on ? PF.pct(mp) + ' <span class="small muted">van ' + PF.bytes(v.maxmem, 0) + '</span>' : '<span class="muted">— / ' + PF.bytes(v.maxmem, 0) + '</span>') + '</td>' +
        '<td class="mono muted">' + PF.uptime(v.uptime) + '</td>' +
        '<td><div class="rowacts">' + (v.reachable ? '<button type="button" class="btn ghost iconbtn" data-one="' + (on ? 'shutdown' : 'start') + '" data-key="' + e(k(v)) + '" aria-label="' + (on ? 'Afsluiten: ' : 'Starten: ') + e(v.name) + '" title="' + (on ? 'Afsluiten' : 'Starten') + '" style="color:' + (on ? 'var(--text2)' : 'var(--ok)') + '">' +
          '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' + (on ? '<path d="M12 3v9M6.3 6.8a8 8 0 1 0 11.4 0"></path>' : '<path d="M7 5l12 7-12 7z"></path>') + '</svg></button>' : '') +
        '<a class="btn ghost" href="' + url + '">Details</a></div></td></tr>';
    }).join('') : '<tr><td colspan="12"><div class="empty">' + (data.vms.length ? 'Geen VM\'s of containers die bij dit filter passen.' : 'Nog geen VM\'s of containers gevonden.') + '</div></td></tr>';
  }
  function set(key_, v) { st[key_] = v; PL.save(st, DEF); render(); }
  $('q').addEventListener('input', function () { set('q', this.value.trim()); });
  $('grp').addEventListener('change', function () { set('group', this.value); });
  $('f-status').addEventListener('click', function (ev) { var b = ev.target.closest('[data-f]'); if (b) set('status', b.getAttribute('data-f')); });
  $('f-type').addEventListener('click', function (ev) { var b = ev.target.closest('[data-f]'); if (b) set('type', b.getAttribute('data-f')); });
  document.querySelector('thead').addEventListener('click', function (ev) {
    var th = ev.target.closest('th.sortable'); if (!th) return;
    var key_ = th.getAttribute('data-sort');
    if (st.sort === key_) st.dir = st.dir === 'asc' ? 'desc' : 'asc'; else { st.sort = key_; st.dir = ['cpu', 'ram', 'uptime', 'upd'].indexOf(key_) >= 0 ? 'desc' : 'asc'; }
    PL.save(st, DEF); render();
  });
  document.getElementById('app').addEventListener('change', function (ev) {
    var t = ev.target;
    if (t.id === 'selall') { filtered().forEach(function (v) { if (!v.reachable) return; if (t.checked) sel[k(v)] = 1; else delete sel[k(v)]; }); render(); }
    else if (t.hasAttribute('data-sel')) { if (t.checked) sel[t.getAttribute('data-sel')] = 1; else delete sel[t.getAttribute('data-sel')]; render(); }
  });
  document.getElementById('app').addEventListener('click', function (ev) {
    if (!data || !ev.target.closest) return;
    var b = ev.target.closest('[data-one]');
    if (b) {
      var v = data.vms.filter(function (x) { return k(x) === b.getAttribute('data-key'); })[0];
      if (v) PFA.power([gobj(v)], b.getAttribute('data-one'));
      return;
    }
    b = ev.target.closest('[data-bulk]');
    if (!b) return;
    var act = b.getAttribute('data-bulk'), list = selected().map(gobj), p_;
    if (act === 'clear') { sel = {}; return render(); }
    if (act === 'start' || act === 'shutdown' || act === 'reboot') {
      var fit = list.filter(function (x) { return act === 'start' ? x.status !== 'running' : x.status === 'running'; });
      if (!fit.length) return PFA.toast(act === 'start' ? 'De geselecteerde VM/CT\'s draaien al' : 'Geen van de geselecteerde VM/CT\'s draait', true);
      p_ = PFA.power(fit, act);
    } else if (act === 'snapshot') p_ = PFA.snapshot(list);
    else if (act === 'backup') p_ = PFA.backup(list);
    else if (act === 'migrate') p_ = PFA.migrate(list);
    if (p_) p_.then(function () { sel = {}; render(); });
  });
  function load() { return PF.getJSON('/api/vms').then(function (d) { data = d; if (!d.ready) setTimeout(load, 1500); render(); }); }
  PF.poll('/api/vms', function (d) { data = d; if (!d.ready) setTimeout(load, 1500); render(); }, Math.max(10000, window.PF_POLL_MS));
  window.addEventListener('pf:refresh', function () { load().catch(function () {}); });
})();
