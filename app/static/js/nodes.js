(function () {
  var e = PF.esc;
  var p = PL.params();
  var DEF = { q: '', filter: 'all', group: '', sort: 'name', dir: 'asc' };
  var st = { q: p.q || '', filter: p.filter || 'all', group: p.group || '', sort: p.sort || 'name', dir: p.dir || (p.sort && p.sort !== 'name' && p.sort !== 'group' ? 'desc' : 'asc') };
  var data = null;
  var $ = function (id) { return document.getElementById(id); };
  $('q').value = st.q;

  function rows() {
    var out = [];
    data.groups.forEach(function (g) {
      g.nodes.forEach(function (n) { out.push({ g: g, n: n }); });
    });
    return out;
  }
  function key(r) {
    var n = r.n;
    switch (st.sort) {
      case 'group': return r.g.name + ' ' + n.name;
      case 'ip': return n.ip || null;
      case 'cpu': return n.cpu;
      case 'ram': return n.mem_pct;
      case 'vms': return n.guests_running;
      case 'uptime': return n.uptime;
      case 'updates': return (n.updates && n.updates.count) || 0;
      default: return n.name;
    }
  }
  function render() {
    if (!data || !data.ready) return;
    PF.navCounts(data);
    var sel = $('grp'), cur = st.group;
    sel.innerHTML = '<option value="">Alle clusters en losse nodes</option>' + data.groups.map(function (g) {
      return '<option value="' + e(g.server) + '"' + (g.server === cur ? ' selected' : '') + '>' + e(g.name) + (g.kind === 'cluster' ? ' (cluster)' : '') + '</option>';
    }).join('');
    PL.seg($('f-state'), st.filter);
    PL.sortHeads(st);
    var q = st.q.toLowerCase();
    var list = rows().filter(function (r) {
      if (st.group && r.g.server !== st.group) return false;
      if (st.filter === 'offline' && r.n.status === 'online') return false;
      if (st.filter === 'updates' && !(r.n.updates && r.n.updates.count)) return false;
      if (q && (r.n.name + ' ' + (r.n.ip || '') + ' ' + r.g.name).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    list.sort(function (a, b) { return PL.cmp(key(a), key(b), st.dir) || PL.cmp(a.n.name, b.n.name, 'asc'); });
    var all = rows(), off = all.filter(function (r) { return r.n.status !== 'online'; }).length;
    $('sub').textContent = all.length + ' nodes in ' + data.groups.length + (data.groups.length === 1 ? ' cluster/server' : ' clusters/servers') + (off ? ' · ' + off + ' offline' : ' · alles online');
    $('rows').innerHTML = list.length ? list.map(function (r) {
      var n = r.n, g = r.g, url = PF.nodeUrl(g.server, n.name);
      var upd = n.updates && n.updates.count ? '<span class="c-warn">' + n.updates.count + (n.updates.security ? ' <span class="c-crit">(' + n.updates.security + ' sec.)</span>' : '') + '</span>' : (n.updates && n.updates.error ? '<span class="muted" title="' + e(n.updates.error) + '">?</span>' : '<span class="muted">—</span>');
      return '<tr><td><a class="mono" style="font-weight:500" href="' + url + '">' + e(n.name) + '</a>' + (n.version ? '<div class="small muted" style="margin-top:2px">PVE ' + e(n.version) + '</div>' : '') + '</td>' +
        '<td>' + e(g.name) + ' <span class="small muted">' + (g.kind === 'cluster' ? 'cluster' : 'losse node') + '</span></td>' +
        '<td>' + PF.dotStatus(n.status) + '</td>' +
        '<td>' + PF.ipCell(n.ip ? [n.ip] : []) + '</td>' +
        '<td style="width:140px">' + PF.barCell(n.cpu, 90, 95) + '</td><td style="width:140px">' + PF.barCell(n.mem_pct) + '</td>' +
        '<td class="mono">' + n.guests_running + ' / ' + n.guests_total + '</td><td class="mono muted">' + PF.uptime(n.uptime) + '</td><td class="small">' + upd + '</td>' +
        '<td class="r"><a class="btn ghost" href="' + url + '">Details</a></td></tr>';
    }).join('') : '<tr><td colspan="10"><div class="empty">Geen nodes die bij dit filter passen.</div></td></tr>';
  }
  function set(k, v) { st[k] = v; PL.save(st, DEF); render(); }
  $('q').addEventListener('input', function () { set('q', this.value.trim()); });
  $('grp').addEventListener('change', function () { set('group', this.value); });
  $('f-state').addEventListener('click', function (ev) { var b = ev.target.closest('[data-f]'); if (b) set('filter', b.getAttribute('data-f')); });
  document.querySelector('thead').addEventListener('click', function (ev) {
    var th = ev.target.closest('th.sortable'); if (!th) return;
    var k = th.getAttribute('data-sort');
    if (st.sort === k) st.dir = st.dir === 'asc' ? 'desc' : 'asc'; else { st.sort = k; st.dir = (k === 'name' || k === 'group') ? 'asc' : 'desc'; }
    PL.save(st, DEF); render();
  });
  PF.poll('/api/fleet', function (d) { data = d; render(); });
})();
