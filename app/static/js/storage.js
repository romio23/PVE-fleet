(function () {
  var e = PF.esc;
  var p = PL.params();
  var DEF = { q: '', filter: 'all', sort: 'pct', dir: 'desc' };
  var st = { q: p.q || '', filter: p.filter || 'all', sort: p.sort || 'pct', dir: p.dir || (p.sort && p.sort !== 'pct' && p.sort !== 'free' ? 'asc' : 'desc') };
  var data = null;
  var $ = function (id) { return document.getElementById(id); };
  $('q').value = st.q;
  var CONTENT = { images: 'VM-schijven', rootdir: 'CT-schijven', backup: 'back-ups', iso: 'ISO\'s', vztmpl: 'CT-templates', snippets: 'snippets', import: 'import' };
  function key(s) {
    switch (st.sort) {
      case 'name': return s.name; case 'where': return s.where; case 'type': return s.type;
      case 'free': return s.total ? s.total - s.used : null; default: return s.total ? s.pct : null;
    }
  }
  function render() {
    if (!data) return;
    PL.seg($('f-state'), st.filter); PL.sortHeads(st);
    var q = st.q.toLowerCase();
    var list = data.storage.filter(function (s) {
      if (st.filter === 'full' && !(s.pct >= 80)) return false;
      if (st.filter === 'backup' && (s.content || '').indexOf('backup') < 0) return false;
      if (q && (s.name + ' ' + s.where + ' ' + s.type + ' ' + s.group).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    list.sort(function (a, b) { return PL.cmp(key(a), key(b), st.dir) || PL.cmp(a.name, b.name, 'asc'); });
    var full = data.storage.filter(function (s) { return s.pct >= 80; }).length;
    $('sub').textContent = data.storage.length + ' opslaglocaties' + (full ? ' · ' + full + ' boven 80%' : ' · niets boven 80%');
    $('rows').innerHTML = list.length ? list.map(function (s) {
      var l = PF.level(s.pct);
      var content = (s.content || '').split(',').filter(Boolean).map(function (c) { return CONTENT[c] || c; }).join(', ');
      var whereLink = s.shared ? e(s.where) + ' <span class="small muted">gedeeld</span>'
        : '<a class="mono" href="' + PF.nodeUrl(s.server, s.node) + '">' + e(s.node) + '</a>' + (s.kind === 'cluster' ? ' <span class="small muted">' + e(s.group) + '</span>' : '');
      return '<tr><td class="mono" style="font-weight:500">' + e(s.name) + (s.status && s.status !== 'available' ? ' <span class="small c-warn">(' + e(s.status) + ')</span>' : '') + '</td>' +
        '<td>' + whereLink + '</td><td class="muted">' + e(s.type) + '</td>' +
        '<td>' + (s.total ? '<div class="barcell"><div class="bar"><span class="b-' + l + '" style="width:' + s.pct + '%"></span></div><span class="num c-' + (l === 'info' ? '' : l) + '">' + PF.pct(s.pct) + '</span></div>' +
          '<div class="small muted" style="margin-top:3px">' + PF.bytes(s.used) + ' / ' + PF.bytes(s.total) + '</div>' : '<span class="muted">—</span>') + '</td>' +
        '<td class="mono">' + (s.total ? PF.bytes(s.total - s.used) : '—') + '</td><td class="small muted">' + e(content) + '</td></tr>';
    }).join('') : '<tr><td colspan="6"><div class="empty">Geen opslag die bij dit filter past.</div></td></tr>';
  }
  function set(k, v) { st[k] = v; PL.save(st, DEF); render(); }
  $('q').addEventListener('input', function () { set('q', this.value.trim()); });
  $('f-state').addEventListener('click', function (ev) { var b = ev.target.closest('[data-f]'); if (b) set('filter', b.getAttribute('data-f')); });
  document.querySelector('thead').addEventListener('click', function (ev) {
    var th = ev.target.closest('th.sortable'); if (!th) return;
    var k = th.getAttribute('data-sort');
    if (st.sort === k) st.dir = st.dir === 'asc' ? 'desc' : 'asc'; else { st.sort = k; st.dir = (k === 'pct' || k === 'free') ? 'desc' : 'asc'; }
    PL.save(st, DEF); render();
  });
  PF.poll('/api/storage', function (d) { data = d; if (!d.ready) setTimeout(function () { PF.getJSON('/api/storage').then(function (x) { data = x; render(); }); }, 1500); render(); }, 30000);
})();
