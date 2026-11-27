/* Terugzetten: een back-up kiezen en als nieuwe VM/CT terugzetten, op dezelfde of een andere server. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var e = PF.esc;
  var targets = [], backups = [], srcServer = '', query = '', loadSeq = 0;
  var params = new URLSearchParams(location.search);

  function typeName(t) { return t === 'qemu' ? 'VM' : 'CT'; }
  function tgtOf(server) { return targets.filter(function (t) { return t.server === server; })[0]; }

  // ------------------------------------------------------------ lijst met back-ups
  function renderList(d) {
    var box = $('r-list');
    if (!d) return;
    var qq = query.trim().toLowerCase();
    var rows = backups.filter(function (b) {
      return !qq || String(b.vmid).indexOf(qq) >= 0 || (b.name || '').toLowerCase().indexOf(qq) >= 0;
    });
    var errs = Object.keys(d.errors || {}).map(function (k) {
      return '<div class="small c-warn" style="padding:0 0 8px"><span>Opslag</span> <span class="mono">' + e(k) + '</span>: <span class="mono">' + e(d.errors[k]) + '</span></div>';
    }).join('');
    if (!backups.length) {
      box.innerHTML = errs + '<div class="card pad muted">Geen back-ups gevonden op de back-upopslag van deze server.</div>';
      return;
    }
    if (!rows.length) { box.innerHTML = errs + '<div class="card pad muted">Geen back-ups die bij je zoekopdracht passen.</div>'; return; }
    box.innerHTML = errs + '<div class="card"><div class="tablewrap"><table class="tbl" style="min-width:760px"><thead><tr><th>ID</th><th>Naam</th><th>Gemaakt</th><th>Grootte</th><th>Opslag</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' +
      rows.map(function (b, i) {
        return '<tr><td class="mono muted">' + b.vmid + '</td>' +
          '<td><span style="font-weight:500">' + e(b.name || '—') + '</span> <span class="typebadge">' + typeName(b.type) + '</span>' +
          (b.exists ? ' <span class="small muted">bestaat nog</span>' : '') + (b.protected ? ' <span class="small muted">beveiligd</span>' : '') + '</td>' +
          '<td>' + (b.ctime ? PF.when(b.ctime) : '—') + '</td><td class="mono">' + (b.size ? PF.bytes(b.size) : '—') + '</td>' +
          '<td class="mono small">' + e(b.storage) + '</td>' +
          '<td><div class="rowacts"><button type="button" class="btn ghost" data-i="' + backups.indexOf(b) + '">Zet terug…</button></div></td></tr>';
      }).join('') + '</tbody></table></div></div>';
  }

  function loadBackups() {
    var seq = ++loadSeq;
    $('r-list').innerHTML = '<div class="card pad muted">Laden… (de inhoud van de back-upopslag ophalen kan even duren)</div>';
    $('r-reload').disabled = true;
    PF.getJSON('/api/restore/backups/' + encodeURIComponent(srcServer)).then(function (d) {
      if (seq !== loadSeq) return;
      backups = d.backups || [];
      $('r-reload').disabled = false;
      renderList(d);
      renderList.last = d;
    }, function (err) {
      if (seq !== loadSeq) return;
      $('r-reload').disabled = false;
      $('r-list').innerHTML = '<div class="card pad"><span class="c-crit">' + e(err.message) + '</span></div>';
    });
  }

  // ------------------------------------------------------------ terugzetvenster
  function setOptions(sel, list, value) {
    sel.innerHTML = list.map(function (o) {
      return '<option value="' + e(o.value) + '"' + (String(o.value) === String(value) ? ' selected' : '') + '>' + e(o.label) + '</option>';
    }).join('');
  }

  function showCmds(root, cmds) {
    var box = root.querySelector('.minfo');
    box.innerHTML = '<pre class="log" style="user-select:all;margin:8px 0 6px;white-space:pre-wrap">' + e(cmds) + '</pre><button type="button" class="btn ghost" data-copycmd style="padding:4px 10px;font-size:12px">Kopiëren</button>';
  }
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('[data-copycmd]') : null;
    if (!btn) return;
    var pre = btn.previousElementSibling, cmds = pre ? pre.textContent : '';
    function done(ok) { btn.textContent = ok ? 'Gekopieerd ✓' : 'Selecteer en kopieer met Ctrl+C'; setTimeout(function () { btn.textContent = 'Kopiëren'; }, 2500); }
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(cmds).then(function () { done(true); }, function () { done(false); }); return; }
    var r = document.createRange(); r.selectNodeContents(pre); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); done(false);
  });

  function nicRow(n, bridges) {
    var have = bridges && bridges.indexOf(n.bridge) >= 0;
    var sel;
    if (bridges) {
      var opts = [];
      if (!have) opts.push('<option value="">— kies een bridge —</option>');
      bridges.forEach(function (b) { opts.push('<option value="' + e(b) + '"' + (b === n.bridge ? ' selected' : '') + '>' + e(b) + '</option>'); });
      sel = '<select class="input nic-br" data-nic="' + e(n.key) + '" aria-label="Bridge voor ' + e(n.key) + '">' + opts.join('') + '</select>';
    } else {
      sel = '<input class="input mono nic-br" data-nic="' + e(n.key) + '" value="' + e(n.bridge) + '" aria-label="Bridge voor ' + e(n.key) + '" autocomplete="off">';
    }
    var facts = [];
    if (n.name) facts.push(e(n.name));
    if (n.ip) facts.push('IP ' + e(n.ip));
    if (n.mac) facts.push('MAC ' + e(n.mac));
    return '<div class="nicrow" style="display:grid;grid-template-columns:minmax(0,1fr) 120px;gap:8px;align-items:end;margin-bottom:10px">' +
      '<div><div class="small" style="margin-bottom:4px"><span class="mono">' + e(n.key) + '</span> <span class="muted">' + facts.join(' · ') + '</span></div>' + sel +
      (bridges && !have ? '<div class="small c-warn" style="margin-top:4px"><span>De bridge</span> <span class="mono">' + e(n.bridge || '—') + '</span> <span>uit de back-up bestaat niet op deze node</span></div>' : '') + '</div>' +
      '<div><div class="small muted" style="margin-bottom:4px">VLAN-tag</div><input class="input mono nic-tag" data-nic="' + e(n.key) + '" data-orig="' + e(n.tag) + '" value="' + e(n.tag) + '" placeholder="geen" aria-label="VLAN-tag voor ' + e(n.key) + '" autocomplete="off"></div></div>';
  }

  function openRestore(b) {
    var avail = targets.filter(function (t) { return t.reachable && t.nodes.length; });
    if (!avail.length) { PFA.toast('Geen server met een online node om naar terug te zetten', true); return; }
    var first = tgtOf(srcServer) && tgtOf(srcServer).nodes.length ? tgtOf(srcServer) : avail[0];
    var ctx = { prep: null, seq: 0, touched: {} };
    var fields = [
      { name: 'target', label: 'Terugzetten op', type: 'select', value: first.server,
        options: avail.map(function (t) { return { value: t.server, label: t.name + (t.kind === 'cluster' ? ' (cluster)' : '') }; }) },
      { name: 'node', label: 'Node', type: 'select', value: first.nodes[0], options: first.nodes.map(function (n) { return { value: n, label: n }; }) },
      { name: 'vmid', label: 'ID van de nieuwe ' + typeName(b.type), type: 'number', value: b.vmid, hint: 'moet vrij zijn' },
      { name: 'storage', label: 'Opslag voor de schijven', type: 'select', value: '', options: [{ value: '', label: 'Laden…' }] },
      { name: 'unique', label: 'Nieuw MAC-adres geven', type: 'checkbox', value: true },
      { name: 'start', label: 'Starten na het terugzetten', type: 'checkbox', value: false }
    ];
    var body = '<div class="small"><span>Back-up:</span> <strong>' + e(b.name || '—') + '</strong> <span class="typebadge">' + typeName(b.type) + '</span> <span class="mono muted">' + b.vmid + '</span> <span class="muted">· ' + PF.when(b.ctime) + '</span></div>' +
      '<div id="rs-info" class="small muted" style="margin-top:8px" role="status" aria-live="polite"></div>';
    return PFA.modal({ title: 'Back-up terugzetten', wide: true, body: body, fields: fields, confirm: 'Terugzetten', kind: 'primary',
      onRender: function (root) {
        var form = root.querySelector('form');
        var fT = root.querySelector('[name="target"]'), fN = root.querySelector('[name="node"]'), fV = root.querySelector('[name="vmid"]');
        var fS = root.querySelector('[name="storage"]'), fU = root.querySelector('[name="unique"]'), fSt = root.querySelector('[name="start"]');
        var info = root.querySelector('#rs-info');
        var nets = document.createElement('div'); nets.id = 'rs-nets';
        var warn = document.createElement('div'); warn.id = 'rs-warn'; warn.className = 'small'; warn.setAttribute('role', 'status');
        var idnote = document.createElement('div'); idnote.className = 'small'; idnote.style.marginTop = '-6px';
        fV.closest('label').parentNode.insertBefore(idnote, fV.closest('label').nextSibling);
        form.appendChild(nets); form.appendChild(warn);
        root.querySelector('.modal').addEventListener('input', function (ev) { if (ev.target && ev.target.name) ctx.touched[ev.target.name] = true; });
        root.querySelector('.modal').addEventListener('change', function (ev) { if (ev.target && ev.target.name) ctx.touched[ev.target.name] = true; });

        function checkId() {
          var u = ctx.prep && ctx.prep.used_ids ? ctx.prep.used_ids[String(parseInt(fV.value, 10))] : null;
          idnote.innerHTML = u ? '<span class="c-crit"><span>Dit ID is al in gebruik door</span> <strong>' + e(u) + '</strong></span>' : '';
        }
        fV.addEventListener('input', checkId);

        function prepare() {
          var seq = ++ctx.seq;
          ctx.prep = null;
          info.innerHTML = '<span>Back-up controleren op</span> <span class="mono">' + e(fN.value) + '</span>';
          nets.replaceChildren(); warn.replaceChildren();
          setOptions(fS, [{ value: '', label: 'Laden…' }], '');
          PF.getJSON('/api/restore/prepare?src=' + encodeURIComponent(srcServer) + '&volid=' + encodeURIComponent(b.volid) + '&type=' + b.type +
            '&vmid=' + b.vmid + '&target=' + encodeURIComponent(fT.value) + '&node=' + encodeURIComponent(fN.value)).then(function (r) {
            if (seq !== ctx.seq) return;
            ctx.prep = r;
            fill(r);
          }, function (err) {
            if (seq !== ctx.seq) return;
            info.innerHTML = '<span class="c-crit">' + e(err.message) + '</span>';
            setOptions(fS, [{ value: '', label: '—' }], '');
          });
        }

        function fill(r) {
          var c = r.config, parts = [];
          if (c) {
            if (c.cores) parts.push(e(c.cores) + ' cores');
            if (c.memory) parts.push(PF.bytes(c.memory * 1048576) + ' RAM');
            c.disks.forEach(function (d) { parts.push('<span class="mono">' + e(d.key) + '</span> ' + e(d.size || '?')); });
            info.innerHTML = '<span>Gevonden in de back-up:</span> ' + parts.join(' · ');
          } else {
            info.innerHTML = '<span class="c-warn"><span>De instellingen in de back-up konden niet gelezen worden.</span> <span class="mono">' + e(r.config_error || '') + '</span> <span>Het netwerk blijft zoals in de back-up.</span></span>';
          }
          setOptions(fS, r.storages.length ? r.storages.map(function (s) {
            return { value: s.name, label: s.name + ' (' + PF.bytes(s.free) + ' vrij)' };
          }) : [{ value: '', label: '— geen opslag voor schijven op deze node —' }], r.storages.length ? r.storages[0].name : '');
          var taken = r.used_ids[String(b.vmid)];
          if (!ctx.touched.vmid) fV.value = taken ? r.nextid : b.vmid;
          if (!ctx.touched.unique) fU.checked = !!r.original;
          if (!ctx.touched.start) fSt.checked = !r.original;
          checkId();
          nets.innerHTML = c && c.nets.length ? '<div class="small muted" style="text-transform:uppercase;letter-spacing:.6px;margin:4px 0 8px">Netwerk</div>' + c.nets.map(function (n) { return nicRow(n, r.bridges); }).join('') : '';
          var w = [];
          if (r.original) {
            var staticIp = c && c.nets.some(function (n) { return n.ip && n.ip !== 'dhcp' && n.ip !== 'manual'; });
            w.push('<div class="c-warn"><span>De originele</span> <strong>' + e(r.original.name) + '</strong> <span>bestaat nog op</span> <span class="mono">' + e(r.original.node) + '</span> <span class="mono">' + e(r.original.status) + '</span>. ' +
              (staticIp ? '<span>De kopie krijgt hetzelfde vaste IP-adres. Start hem pas nadat je dat hebt aangepast of het origineel is uitgezet.</span>' : '<span>Zet de kopie pas aan als dat geen botsing geeft.</span>') + '</div>');
          }
          if (c && c.bind_mounts && c.bind_mounts.length) w.push('<div class="c-warn"><span>' + (r.type === 'qemu' ? 'Deze VM heeft bind mounts:' : 'Deze CT heeft bind mounts:') + '</span> <span class="mono">' + e(c.bind_mounts.join(', ')) + '</span>. <span>Proxmox laat die mogelijk alleen root@pam terugzetten.</span></div>');
          if (c && c.unprivileged === false) w.push('<div class="c-warn"><span>Dit is een privileged container.</span> <span>Proxmox laat die mogelijk alleen root@pam terugzetten.</span></div>');
          warn.innerHTML = w.join('');
        }
        function onTarget() {
          var t = tgtOf(fT.value);
          setOptions(fN, t.nodes.map(function (n) { return { value: n, label: n }; }), t.nodes[0]);
          prepare();
        }
        fT.addEventListener('change', onTarget);
        fN.addEventListener('change', prepare);
        prepare();
      },
      validate: function (v) {
        if (!ctx.prep) return 'Wacht tot de back-up gecontroleerd is, of kies een node die de back-up kan zien';
        var id = parseInt(v.vmid, 10);
        if (!(id >= 100)) return 'Kies een geldig ID (een getal vanaf 100)';
        if (ctx.prep.used_ids[String(id)]) return 'Dit ID is al in gebruik door ' + ctx.prep.used_ids[String(id)];
        if (!v.storage) return 'Kies een opslag voor de schijven';
        var bad = Array.prototype.filter.call(document.querySelectorAll('.modal .nic-br'), function (s) { return !s.value.trim(); })[0];
        if (bad) return 'Kies een bridge voor ' + bad.getAttribute('data-nic');
        return null;
      },
      submit: function (v) {
        var nets = {};
        Array.prototype.forEach.call(document.querySelectorAll('.modal .nic-br'), function (s) {
          var k = s.getAttribute('data-nic'), n = ((ctx.prep.config || {}).nets || []).filter(function (x) { return x.key === k; })[0];
          var tagEl = document.querySelector('.modal .nic-tag[data-nic="' + k + '"]');
          var spec = {};
          if (n && s.value.trim() !== n.bridge) spec.bridge = s.value.trim();
          if (tagEl && tagEl.value.trim() !== tagEl.getAttribute('data-orig')) spec.tag = tagEl.value.trim() || 'none';
          if (spec.bridge !== undefined || spec.tag !== undefined) nets[k] = spec;
        });
        document.querySelector('.modal .minfo').replaceChildren();
        return PF.postJSON('/api/restore/start', { src: srcServer, volid: b.volid, type: b.type, src_vmid: b.vmid, name: b.name, target: v.target,
          node: v.node, vmid: parseInt(v.vmid, 10), storage: v.storage, unique: v.unique, start: v.start, nets: nets }).then(function (res) {
          PFA.track(res);
          PFA.toast(res.message);
          loadJobs();
        }, function (ex) {
          if (ex.body && ex.body.commands) showCmds(document.querySelector('.modal-back'), ex.body.commands);
          throw ex;
        });
      } });
  }

  // ------------------------------------------------------------ recent terugzetten
  var jobTimer = null;
  var STATES = { restoring: 'Bezig met terugzetten…', network: 'Netwerk instellen…', starting: 'Starten…', done: 'Teruggezet', warn: 'Teruggezet, maar let op', error: 'Mislukt' };
  function loadJobs() {
    clearTimeout(jobTimer);
    PF.getJSON('/api/restore/jobs').then(function (d) {
      var jobs = d.jobs || [], busy = false;
      $('jobs-card').style.display = jobs.length ? '' : 'none';
      $('jobs').innerHTML = jobs.map(function (j) {
        var active = j.state === 'restoring' || j.state === 'network' || j.state === 'starting';
        busy = busy || active;
        var cls = j.state === 'done' ? 'c-ok' : (j.state === 'error' ? 'c-crit' : (j.state === 'warn' ? 'c-warn' : ''));
        var t = tgtOf(j.server);
        return '<div style="padding:8px 0;border-top:1px solid var(--line2)"><div><span class="typebadge">' + typeName(j.type) + '</span> <span class="mono">' + j.vmid + '</span> ' + e(j.name || '') +
          ' <span class="muted small">op <span>' + e(t ? t.name : j.server) + '</span> / <span class="mono">' + e(j.node) + '</span></span></div>' +
          '<div class="small ' + cls + '"><span>' + e(STATES[j.state] || j.state) + '</span>' +
          (j.state === 'done' ? (Object.keys(j.nets || {}).length ? ' · <span>netwerk aangepast</span>' : '') + (j.start ? ' · <span>gestart</span>' : '') : (j.message ? ' <span>' + e(j.message) + '</span>' : '')) +
          (j.state === 'done' || j.state === 'warn' ? ' <a href="' + PF.guestUrl(j.server, j.node, j.type, j.vmid) + '">Openen</a>' : '') + '</div></div>';
      }).join('');
      jobTimer = setTimeout(loadJobs, busy ? 3000 : 20000);
    }, function () { jobTimer = setTimeout(loadJobs, 20000); });
  }

  // ------------------------------------------------------------ opstarten
  $('r-list').addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('[data-i]') : null;
    if (btn) openRestore(backups[+btn.getAttribute('data-i')]);
  });
  $('r-q').addEventListener('input', function () { query = this.value; renderList(renderList.last); });
  $('r-reload').addEventListener('click', loadBackups);
  $('r-src').addEventListener('change', function () { srcServer = this.value; loadBackups(); });

  PF.getJSON('/api/restore/targets').then(function (d) {
    targets = d.targets || [];
    var ok = targets.filter(function (t) { return t.reachable; });
    if (!ok.length) { $('r-list').innerHTML = '<div class="card pad muted">Geen bereikbare server.</div>'; return; }
    $('r-src').innerHTML = ok.map(function (t) { return '<option value="' + e(t.server) + '">' + e(t.name) + '</option>'; }).join('');
    var want = params.get('server');
    srcServer = ok.filter(function (t) { return t.server === want; })[0] ? want : ok[0].server;
    $('r-src').value = srcServer;
    if (params.get('vmid')) { query = params.get('vmid'); $('r-q').value = query; }
    loadBackups();
    loadJobs();
  }, function (err) { $('r-list').innerHTML = '<div class="card pad"><span class="c-crit">' + e(err.message) + '</span></div>'; });
})();
