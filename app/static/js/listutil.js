/* Gedeelde hulp voor lijstpagina's: filters in de URL en sorteren op kolomkoppen. */
var PL = (function () {
  function params() {
    var out = {};
    new URLSearchParams(location.search).forEach(function (v, k) { out[k] = v; });
    return out;
  }
  function save(state, defaults) {
    var q = Object.keys(state).filter(function (k) { return state[k] !== '' && state[k] !== null && state[k] !== defaults[k]; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(state[k]); }).join('&');
    try { history.replaceState(null, '', location.pathname + (q ? '?' + q : '')); } catch (x) { /* niets */ }
  }
  function seg(el, value) {
    Array.prototype.forEach.call(el.querySelectorAll('button'), function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-f') === value ? 'true' : 'false'); });
  }
  function sortHeads(state) {
    Array.prototype.forEach.call(document.querySelectorAll('th.sortable'), function (th) {
      var k = th.getAttribute('data-sort');
      var label = th.getAttribute('data-label') || th.textContent;
      th.setAttribute('data-label', label);
      th.setAttribute('aria-sort', k === state.sort ? (state.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      th.innerHTML = label + (k === state.sort ? '<span class="dir">' + (state.dir === 'asc' ? '▲' : '▼') + '</span>' : '');
    });
  }
  function cmp(a, b, dir) {
    if (a === b) return 0;
    if (a === null || a === undefined) return 1;
    if (b === null || b === undefined) return -1;
    var r = typeof a === 'string' ? a.localeCompare(b, 'nl', { numeric: true }) : (a < b ? -1 : 1);
    return dir === 'asc' ? r : -r;
  }
  return { params: params, save: save, seg: seg, sortHeads: sortHeads, cmp: cmp };
})();
