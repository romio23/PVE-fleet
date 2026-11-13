
/* Notities: Proxmox gebruikt Markdown (met HTML erin). Opgemaakt tonen, veilig gemaakt met DOMPurify. */
var NOTES_RAW = false;
function notesHtml(src) {
  if (!window.marked || !window.DOMPurify || !window.marked.parse) return null;
  try {
    var html = window.marked.parse(String(src), { gfm: true, breaks: true });
    return window.DOMPurify.sanitize(html, {
      FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select', 'iframe', 'object', 'embed'],
      FORBID_ATTR: ['id', 'class']
    });
  } catch (err) { return null; }
}
function notesView(src) {
  var h = NOTES_RAW ? null : notesHtml(src);
  if (h === null) return '<div class="notes-raw">' + PF.esc(src) + '</div>';
  return h;
}
function notesFixLinks(root) {
  if (!root) return;
  var a = root.querySelectorAll('a[href]');
  for (var i = 0; i < a.length; i++) { a[i].setAttribute('target', '_blank'); a[i].setAttribute('rel', 'noopener noreferrer'); }
  var im = root.querySelectorAll('img');
  for (var j = 0; j < im.length; j++) { im[j].setAttribute('loading', 'lazy'); im[j].setAttribute('referrerpolicy', 'no-referrer'); }
}
document.addEventListener('click', function (ev) {
  var b = ev.target && ev.target.closest ? ev.target.closest('#notes-toggle') : null;
  if (!b) return;
  NOTES_RAW = !NOTES_RAW;
  b.textContent = NOTES_RAW ? 'Toon opgemaakt' : 'Toon als tekst';
  var box = document.getElementById('notes');
  if (box && window.PF_NOTES_SRC !== undefined) { box.innerHTML = notesView(window.PF_NOTES_SRC); notesFixLinks(box); }
});
(function () {
  var app = document.getElementById('app');
  var server = app.getAttribute('data-server'), nodeName = app.getAttribute('data-node');
  var kind = app.getAttribute('data-kind'), vmid = app.getAttribute('data-vmid');
  var e = PF.esc;

  function metrics(d) {
    var r = d.rrd, g = d.row;
    if (!r || g.status !== 'running') return '';
    var cpu = r.cpu.map(function (v) { return v === null ? null : v * 100; });
    var mem = r.mem.map(function (v, i) { return r.maxmem[i] ? 100 * v / r.maxmem[i] : null; });
    var net = r.netin.map(function (v, i) { return v === null ? null : v + (r.netout[i] || 0); });
    var io = r.diskread.map(function (v, i) { return v === null ? null : v + (r.diskwrite[i] || 0); });
    var memPct = g.maxmem ? 100 * g.mem / g.maxmem : null;
    var items = [
      ['CPU', PF.pct(g.cpu), (g.cpus || '?') + ' cores toegewezen', PF.level(g.cpu, 90, 95), cpu],
      ['RAM', PF.pct(memPct), PF.bytes(g.mem) + ' / ' + PF.bytes(g.maxmem, 0), PF.level(memPct, 90, 95), mem],
      ['Netwerk', PF.rate(PF.last(net)), 'in ' + PF.rate(PF.last(r.netin)) + ' · uit ' + PF.rate(PF.last(r.netout)), 'info', net],
      ['Schijf-IO', PF.bytes(PF.last(io)) + '/s', 'lezen ' + PF.bytes(PF.last(r.diskread)) + '/s · schrijven ' + PF.bytes(PF.last(r.diskwrite)) + '/s', 'info', io]
    ];
    return '<section class="metrics" aria-label="Belasting laatste uur">' + items.map(function (m) {
      return '<div class="metric"><div class="top"><span class="label">' + m[0] + '</span><span class="small muted">laatste uur</span></div>' +
        '<div class="value c-' + (m[3] === 'info' ? '' : m[3]) + '">' + m[1] + '</div><div class="small muted">' + e(m[2]) + '</div>' + PF.spark(m[4], m[3], m[0]) + '</div>';
    }).join('') + '</section>';
  }

  function updCard(d, g) {
    if (kind === 'qemu') return '';
    var u = d.gupd;
    if (!u) return '';
    var body;
    if (!u.enabled) body = '<div class="small muted">Nog niet ingesteld. <a href="/updates">Updates › Updates in VM\'s &amp; containers</a></div>';
    else if (!u.result) body = '<div class="small muted">' + (u.checking ? 'Bezig met controleren…' : (g.status === 'running' ? 'Nog niet gecontroleerd.' : 'Draait niet; wordt gecontroleerd zodra hij draait.')) + '</div>';
    else {
      var r = u.result, on = g.status === 'running';
      var rows = '<div class="row"><span class="k">Systeem</span><span class="v">' + e(r.os || '—') + '</span></div>';
      rows += '<div class="row"><span class="k">Pakketten</span><span class="v">' + (r.count === null || r.count === undefined ? '—' : (r.count ? '<span class="c-warn">' + r.count + ' updates</span>' + (r.security ? ' <span class="c-crit">(' + r.security + ' security)</span>' : '') : '<span class="c-ok">bijgewerkt</span>')) + '</span></div>';
      (r.apps || []).forEach(function (a) {
        rows += '<div class="row"><span class="k mono">' + e(a.name) + '</span><span class="v mono">' + e(a.installed || '?') +
          (a.update ? ' <span class="c-warn">→ ' + e(a.latest) + '</span>' : (a.latest ? ' <span class="c-ok">✓</span>' : ' <span class="muted" title="Nieuwste versie onbekend">?</span>')) + '</span></div>';
      });
      var main = (r.apps || []).filter(function (a) { return a.update; })[0] || (r.apps || []).filter(function (a) { return a.main; })[0] || (r.apps || [])[0];
      body = '<div class="kv">' + rows + '</div>' +
        (r.error ? '<div class="small c-warn" style="margin-top:6px">' + e(r.error) + '</div>' : '') +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">' +
        (on && r.count ? '<button type="button" class="btn primary" data-upd="os">Bijwerken</button>' : '') +
        (on && main && main.update ? '<button type="button" class="btn blue" data-upd="app">App bijwerken</button>' : '') + '</div>' +
        '<div class="small muted" style="margin-top:8px">Gecontroleerd ' + PF.when(r.checked) + (r.stale ? ' (laatst bekend)' : '') + '</div>';
    }
    body += '<div class="small" style="margin-top:6px"><a href="#" data-upd="repo">' + (u.repo_override ? 'App-repository: <span class="mono">' + e(u.repo_override) + '</span>' : 'App-repository instellen') + '</a></div>';
    return '<section class="card pad"><h2 class="small-title">Updates</h2>' + body + '</section>';
  }

  function render(d) {
    var g = d.row;
    document.getElementById('crumb-group').textContent = d.group.name;
    document.getElementById('crumb-guest').textContent = g.vmid + ' ' + g.name;
    document.title = g.vmid + ' ' + g.name + ' · PVE Fleet';
    var nodeDown = d.node_status !== 'online' || !d.group.reachable;
    var st = PF.statusInfo(nodeDown ? 'unknown' : g.status);
    var meta = [(d.ips && d.ips.length ? d.ips.join(', ') : d.ip), 'op ' + nodeName, g.status === 'running' ? 'uptime ' + PF.uptime(g.uptime) : null, d.ostype ? 'ostype ' + d.ostype : null].filter(Boolean).map(e).join(' · ');
    var html = '<section class="pagehead"><div><div class="title"><span class="mono muted" style="font-size:15px">' + g.vmid + '</span><h1>' + e(g.name) + '</h1>' +
      '<span class="typebadge" style="font-size:12px;padding:3px 8px">' + (kind === 'qemu' ? 'VM · QEMU' : 'LXC-container') + '</span>' +
      '<span class="pill c-' + st.cls + '" style="border-color:currentColor"><span class="dot b-' + st.cls + '"></span>' + st.label + '</span>' +
      g.tags.map(function (t) { return '<span class="tag">#' + e(t) + '</span>'; }).join('') + '</div><p class="meta">' + meta + '</p></div>' +
      '<a class="btn lg ghost" href="' + PF.nodeUrl(server, nodeName) + '">← ' + e(nodeName) + '</a></section>';
    var on = g.status === 'running';
    if (!nodeDown) {
      html += '<div class="toolbar" role="toolbar" aria-label="Acties voor deze VM/CT">' +
        (on ? '<button type="button" class="btn lg" data-act="shutdown">Afsluiten</button><button type="button" class="btn lg" data-act="reboot">Herstarten</button>'
          : '<button type="button" class="btn lg go" data-act="start">Starten</button>') +
        (on ? '<a class="btn lg" href="' + PF.consoleUrl(server, nodeName, kind, vmid) + '" data-console="1">Console</a>' : '') +
        '<button type="button" class="btn lg" data-act="snapshot">Snapshot maken</button>' +
        '<button type="button" class="btn lg" data-act="backup">Back-up nu</button>' +
        (d.group.kind === 'cluster' ? '<button type="button" class="btn lg blue" data-act="migrate">Migreren…</button>' : '') +
        (on ? '<button type="button" class="btn lg danger" data-act="stop">Geforceerd stoppen</button>' : '') + '</div>';
    }

    if (nodeDown) {
      html += '<section class="banner crit"><div class="txt"><div class="ttl">Node ' + e(nodeName) + ' is niet bereikbaar</div><div class="desc">De status van deze VM/CT is onbekend tot de node weer antwoordt.</div></div></section>';
    }
    html += metrics(d);

    var cfg = d.config.length ? '<div class="kv">' + d.config.map(function (r) {
      return '<div class="row"><span class="k">' + e(r.label) + '</span><span class="v">' + e(r.value) + '</span></div>';
    }).join('') + '</div>' : '<div class="muted">' + (d.errors.config ? 'Niet beschikbaar: ' + e(d.errors.config) : 'Geen gegevens.') + '</div>';

    var consoleHtml = g.status === 'running' && !nodeDown
      ? '<p style="margin:0 0 12px;color:var(--text2);line-height:1.5">De console opent in een eigen venster van PVE Fleet (' + (kind === 'qemu' ? 'het scherm van de VM' : 'een terminal') + '). Je hoeft niet apart bij Proxmox in te loggen.</p>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn lg primary" href="' + PF.consoleUrl(server, nodeName, kind, vmid) + '" data-console="1">Console openen</a>' +
        (d.console_url ? '<a class="btn lg ghost" href="' + e(d.console_url) + '" target="_blank" rel="noopener" title="Hiervoor moet je in deze browser ingelogd zijn op hetzelfde adres van Proxmox">In Proxmox openen ↗</a>' : '') + '</div>'
      : '<p class="muted" style="margin:0">' + (nodeDown ? 'Console niet beschikbaar: node onbereikbaar.' : 'Deze VM/CT is gestopt. Start hem met de knop Starten hierboven.') + '</p>';

    var snaps = d.snapshots.length ? '<div class="list">' + d.snapshots.map(function (s) {
      return '<div class="row snaprow"><div style="min-width:0"><div class="mono">' + e(s.name) + '</div><div class="small muted" style="margin-top:2px">' + PF.when(s.time) + (s.description ? ' · ' + e(s.description) : '') + '</div></div>' +
        (nodeDown ? '' : '<div class="acts"><button type="button" class="btn ghost" data-rollback="' + e(s.name) + '">Terugdraaien</button>' +
        '<button type="button" class="btn ghost iconbtn" data-delsnap="' + e(s.name) + '" aria-label="Snapshot ' + e(s.name) + ' verwijderen" title="Verwijderen" style="color:var(--crit)">' +
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"></path></svg></button></div>') + '</div>';
    }).join('') + '</div>' : '<div class="muted" style="font-size:13px">' + (d.errors.snapshots ? 'Niet beschikbaar: ' + e(d.errors.snapshots) : 'Geen snapshots.') + '</div>';

    var backups = d.backups.length ? '<div class="list">' + d.backups.map(function (b) {
      return '<div class="row"><div><div class="mono">' + PF.when(b.time) + '</div><div class="small muted" style="margin-top:2px">' + e(b.storage) + ' · ' + PF.bytes(b.size) + '</div></div></div>';
    }).join('') + '</div>' : '<div class="muted" style="font-size:13px">' + (d.errors.backups ? 'Niet beschikbaar: ' + e(d.errors.backups) : 'Geen back-ups gevonden op de back-upopslag van deze node.') + '</div>';

    function yesno(v) { return v === null || v === undefined ? '<span class="muted">—</span>' : (v ? '<span class="c-ok">Aan</span>' : '<span class="muted">Uit</span>'); }

    html += '<div class="cols"><div class="col-main">' +
      '<section class="card pad"><h2 class="small-title">Console</h2>' + consoleHtml + '</section>' +
      '<section class="card pad"><h2 class="small-title">Hardware &amp; configuratie</h2>' +
        (d.ip_hint ? '<div class="small muted" style="margin:-2px 0 10px">IP-adres: ' + e(d.ip_hint) + '</div>' : '') + cfg + '</section>' +
      (d.description ? '<section class="card pad"><div class="notes-head"><h2 class="small-title">Notities</h2>' +
        (notesHtml(d.description) !== null ? '<button class="btn ghost" style="padding:3px 10px;font-size:12px" type="button" id="notes-toggle">' + (NOTES_RAW ? 'Toon opgemaakt' : 'Toon als tekst') + '</button>' : '') +
        '</div><div class="notes" id="notes">' + notesView(d.description) + '</div></section>' : '') +
      '</div><div class="col-side">' +
      '<section class="card pad"><h2 class="small-title">Snapshots</h2>' + snaps + '</section>' +
      '<section class="card pad"><h2 class="small-title">Back-ups</h2>' + backups + '</section>' +
      updCard(d, g) +
      '<section class="card pad"><h2 class="small-title">Opties</h2><div class="kv">' +
      '<div class="row"><span class="k">Start bij opstarten</span><span class="v">' + yesno(d.onboot) + '</span></div>' +
      '<div class="row"><span class="k">High availability</span><span class="v">' + yesno(d.ha) + '</span></div>' +
      '<div class="row"><span class="k">Bescherming</span><span class="v">' + yesno(d.protection) + '</span></div></div></section>' +
      '</div></div>';
    app.innerHTML = html;
    window.PF_NOTES_SRC = d.description || '';
    notesFixLinks(document.getElementById('notes'));
  }

  var last = null;
  function gobj() { var g = last.row; return { server: server, node: nodeName, type: kind, vmid: g.vmid, name: g.name, status: g.status }; }
  app.addEventListener('click', function (ev) {
    if (!last || !ev.target.closest) return;
    if (ev.target.closest('[data-console]')) { ev.preventDefault(); return PF.openConsole(server, nodeName, kind, vmid); }
    var ub = ev.target.closest('[data-upd]');
    if (ub) {
      ev.preventDefault();
      var w = ub.getAttribute('data-upd'), u = last.gupd || {}, r = u.result || {};
      var main = (r.apps || []).filter(function (a) { return a.update; })[0] || (r.apps || []).filter(function (a) { return a.main; })[0] || (r.apps || [])[0];
      var recheck = function () { PF.postJSON('/api/guest-updates/' + encodeURIComponent(server) + '/check').then(function () { setTimeout(function () { PF.getJSON(url).then(onData, onErr); }, 4000); }, function () {}); };
      if (w === 'os') return PFA.guestUpdate(gobj(), 'os', { pm: r.pm, count: r.count, security: r.security }, recheck);
      if (w === 'app') return PFA.guestUpdate(gobj(), 'app', { app: main && main.name, installed: main && main.installed, latest: main && main.latest }, recheck);
      if (w === 'repo') return PFA.modal({ title: 'App-repository voor ' + last.row.name,
        body: '<p style="margin:0 0 8px">PVE Fleet vergelijkt de app-versie met de nieuwste release op GitHub. Voor community-scripts-containers gaat dat vanzelf. Herkent PVE Fleet de app niet, of wil je een andere repository, vul die dan hier in.</p>' +
          '<p class="small muted" style="margin:0">Leeg laten = automatisch.</p>',
        fields: [{ name: 'repo', label: 'GitHub-repository', value: u.repo_override || (main && main.repo) || '', placeholder: 'bijv. jellyfin/jellyfin', mono: true }],
        confirm: 'Opslaan', kind: 'primary',
        submit: function (v) { return PF.postJSON('/api/guest-updates/repo', { server: server, vmid: vmid, repo: v.repo }).then(function () { PFA.toast('Opgeslagen; PVE Fleet kijkt opnieuw'); setTimeout(function () { PF.getJSON(url).then(onData, onErr); }, 4000); }); } });
    }
    var b = ev.target.closest('[data-act],[data-rollback],[data-delsnap]');
    if (!b) return;
    var g = gobj();
    if (b.hasAttribute('data-rollback')) return PFA.rollback(g, b.getAttribute('data-rollback'));
    if (b.hasAttribute('data-delsnap')) return PFA.delSnapshot(g, b.getAttribute('data-delsnap'));
    var a = b.getAttribute('data-act');
    if (a === 'snapshot') return PFA.snapshot([g]);
    if (a === 'backup') return PFA.backup([g]);
    if (a === 'migrate') return PFA.migrate([g], { followTo: true });
    return PFA.power([g], a);
  });
  var url = '/api/guest/' + encodeURIComponent(server) + '/' + encodeURIComponent(nodeName) + '/' + kind + '/' + vmid;
  function onData(d) { last = d; render(d); }
  function onErr(err) {
    if (err.body && err.body.moved_to) {   // net gemigreerd: meeverhuizen
      location.replace(PF.guestUrl(server, err.body.moved_to, kind, vmid));
      return;
    }
    app.innerHTML = '<div class="banner crit"><div class="txt"><div class="ttl">Kan deze VM/CT niet laden</div><div class="desc">' + e(err.message) + ' · <a href="' + PF.nodeUrl(server, nodeName) + '">terug naar ' + e(nodeName) + '</a></div></div></div>';
  }
  PF.poll(url, onData, Math.max(10000, window.PF_POLL_MS), onErr);
  window.addEventListener('pf:refresh', function () { PF.getJSON(url).then(onData, onErr); });
})();
