/* Back-ups: jobs, laatste back-up per VM/CT, wat ontbreekt. */
(function () {
  var e = PF.esc;
  var $ = function (id) { return document.getElementById(id); };
  var data = null, show = 'all';
  var OLD_H = 48;   // ouder dan dit = aandachtspunt
  try { show = localStorage.getItem('pvef_backups_show') || 'all'; } catch (x) { /* niets */ }

  var DAYS = { mon: 'ma', tue: 'di', wed: 'wo', thu: 'do', fri: 'vr', sat: 'za', sun: 'zo' };
  var DAYS_LONG = { mon: 'maandag', tue: 'dinsdag', wed: 'woensdag', thu: 'donderdag', fri: 'vrijdag', sat: 'zaterdag', sun: 'zondag' };
  function human(s) {
    if (!s) return '—';
    var m = /^\s*(?:([a-z]{3})(?:\.\.([a-z]{3}))?(?:,([a-z,]+))?\s+)?(\d{1,2}):(\d{2})\s*$/.exec(s);
    if (!m || (m[1] && !DAYS[m[1]]) || (m[2] && !DAYS[m[2]])) return '<span class="mono">' + e(s) + '</span>';
    var t = ('0' + m[4]).slice(-2) + ':' + m[5];
    if (!m[1]) return 'elke dag om ' + t;
    if (m[2]) return (m[1] === 'mon' && m[2] === 'fri' ? 'werkdagen' : DAYS[m[1]] + ' t/m ' + DAYS[m[2]]) + ' om ' + t;
    if (m[3]) return [m[1]].concat(m[3].split(',')).map(function (d) { return DAYS[d] || d; }).join(', ') + ' om ' + t;
    return 'elke ' + DAYS_LONG[m[1]] + ' om ' + t;
  }
  function nextWhen(ts) {
    if (!ts) return '—';
    var d = new Date(ts * 1000), now = new Date(), tm = new Date(now.getTime() + 86400000);
    if (d.toDateString() === now.toDateString()) return 'vandaag ' + PF.clock(ts);
    if (d.toDateString() === tm.toDateString()) return 'morgen ' + PF.clock(ts);
    return PF.when(ts);
  }
  function ago(h) {
    if (h === null || h === undefined) return '—';
    if (h < 1) return 'minder dan een uur';
    if (h < 48) return Math.round(h) + ' uur';
    return Math.round(h / 24) + ' dagen';
  }
  function prune(p) {
    var names = { 'keep-last': 'laatste', 'keep-hourly': 'per uur', 'keep-daily': 'dagen', 'keep-weekly': 'weken', 'keep-monthly': 'maanden', 'keep-yearly': 'jaren' };
    var parts = ['keep-last', 'keep-hourly', 'keep-daily', 'keep-weekly', 'keep-monthly', 'keep-yearly'].filter(function (k) { return p && p[k]; }).map(function (k) { return k === 'keep-last' ? 'laatste ' + p[k] : p[k] + ' ' + names[k]; });
    return parts.length ? 'bewaar ' + parts.join(', ') : 'bewaren volgens de opslag';
  }
  function selText(j, g) {
    if (j.selection === 'all') return 'alle VM/CT\'s' + (j.exclude.length ? ' behalve ' + j.exclude.join(', ') : '');
    if (j.selection === 'pool') return 'pool ' + e(j.pool || '');
    var names = j.vmids.map(function (id) { var x = g.guests.filter(function (y) { return y.vmid === id; })[0]; return x ? id + ' ' + x.name : String(id); });
    return names.length <= 4 ? names.map(e).join(', ') : names.length + ' VM/CT\'s';
  }
  function inJobs(g, vmid) {
    return g.jobs.filter(function (j) {
      if (j.selection === 'all') return j.exclude.indexOf(vmid) < 0;
      return j.vmids.indexOf(vmid) >= 0;
    });
  }

  function jobRow(g, j) {
    return '<div class="row" style="align-items:center"><div style="min-width:0;flex:1">' +
      '<div><strong>' + human(j.schedule) + '</strong> <span class="muted">naar</span> <span class="mono">' + e(j.storage || '') + '</span>' +
      (j.enabled ? '' : ' <span class="pill" style="padding:1px 8px;font-size:12px">uit</span>') + '</div>' +
      '<div class="jobmeta"><span>' + selText(j, g) + '</span><span>' + e(j.mode) + '</span><span>' + e(prune(j.prune)) + '</span>' +
      (j.node ? '<span>alleen op ' + e(j.node) + '</span>' : '') +
      (j.enabled ? '<span>volgende: ' + nextWhen(j.next_run) + '</span>' : '') + (j.comment ? '<span>' + e(j.comment) + '</span>' : '') + '</div></div>' +
      '<div class="rowacts" style="flex:none">' +
      '<label class="check" style="margin:0" title="Job aan of uit"><input type="checkbox" data-toggle="' + e(j.id) + '" data-s="' + e(g.server) + '"' + (j.enabled ? ' checked' : '') + '>Aan</label>' +
      '<button type="button" class="btn ghost" data-act="run" data-s="' + e(g.server) + '" data-j="' + e(j.id) + '">Nu uitvoeren</button>' +
      '<button type="button" class="btn ghost" data-act="edit" data-s="' + e(g.server) + '" data-j="' + e(j.id) + '">Bewerken</button>' +
      '<button type="button" class="btn ghost iconbtn" data-act="del" data-s="' + e(g.server) + '" data-j="' + e(j.id) + '" title="Verwijderen" aria-label="Job verwijderen">×</button></div></div>';
  }

  function guestRows(g) {
    var notIn = {};
    (g.not_backed_up || []).forEach(function (x) { notIn[x.vmid] = 1; });
    var rows = g.guests.map(function (x) {
      var issue = notIn[x.vmid] ? 'none' : (x.last === null ? 'never' : (x.age_h > OLD_H ? 'old' : ''));
      return { x: x, issue: issue };
    });
    if (show === 'problems') rows = rows.filter(function (r) { return r.issue; });
    if (!rows.length) return '<div class="muted small" style="padding:12px 18px">' + (show === 'problems' ? 'Geen aandachtspunten. Elke VM/CT zit in een job en heeft een recente back-up.' : 'Geen VM\'s of containers.') + '</div>';
    return '<div class="tablewrap"><table class="tbl" style="min-width:820px"><thead><tr><th>ID</th><th>Naam</th><th>Node</th><th>Laatste back-up</th><th>Aantal</th><th>Opslag</th><th>Jobs</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' +
      rows.map(function (r) {
        var x = r.x, jobs = inJobs(g, x.vmid);
        var last = x.last ? '<span class="' + (x.age_h > OLD_H ? 'c-warn' : '') + '">' + PF.when(x.last) + '</span> <span class="small muted">(' + ago(x.age_h) + ' geleden)</span>' : '<span class="c-crit">nooit</span>';
        var jobTxt = notIn[x.vmid] ? '<span class="c-crit small">in geen enkele job</span>' : (jobs.length ? '<span class="small muted">' + jobs.map(function (j) { return human(j.schedule).replace(/<[^>]+>/g, '') + (j.enabled ? '' : ' (uit)'); }).map(e).join('; ') + '</span>' : '<span class="small muted">—</span>');
        return '<tr><td class="mono muted">' + x.vmid + '</td><td><a href="' + PF.guestUrl(g.server, x.node, x.type, x.vmid) + '" style="font-weight:500">' + e(x.name) + '</a> <span class="typebadge">' + (x.type === 'qemu' ? 'VM' : 'CT') + '</span></td>' +
          '<td class="mono small">' + e(x.node) + '</td><td>' + last + '</td><td class="mono">' + (x.count || 0) + '</td>' +
          '<td class="mono small">' + e(x.storage || '—') + (x.size ? ' <span class="muted">' + PF.bytes(x.size) + '</span>' : '') + '</td><td>' + jobTxt + '</td>' +
          '<td><div class="rowacts"><button type="button" class="btn ghost" data-act="now" data-s="' + e(g.server) + '" data-v="' + x.vmid + '">Back-up nu</button></div></td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  function render(d) {
    data = d;
    PL_seg();
    var jobsOn = 0, jobsAll = 0, none = 0, old = 0, failed = 0;
    d.groups.forEach(function (g) {
      jobsAll += g.jobs.length; jobsOn += g.jobs.filter(function (j) { return j.enabled; }).length;
      none += (g.not_backed_up || []).length;
      old += g.guests.filter(function (x) { return x.last && x.age_h > OLD_H; }).length + g.guests.filter(function (x) { return !x.last; }).length;
      failed += (g.tasks || []).filter(function (t) { return t.end && !t.ok && Date.now() / 1000 - t.start < 86400; }).length;
    });
    $('kpis').innerHTML = [
      ['Jobs actief', jobsOn + '/' + jobsAll, jobsAll ? 'back-upjobs aan' : 'nog geen jobs', jobsOn ? '' : 'c-warn'],
      ['Zonder job', none, none ? 'VM/CT\'s in geen enkele job' : 'alles valt onder een job', none ? 'c-crit' : 'c-ok'],
      ['Oud of nooit', old, 'laatste back-up > ' + OLD_H + ' uur of nooit', old ? 'c-warn' : 'c-ok'],
      ['Mislukt (24 u)', failed, failed ? 'zie de taken onderaan' : 'geen fouten', failed ? 'c-crit' : 'c-ok']
    ].map(function (i) {
      return '<div class="kpi"><span class="label">' + e(i[0]) + '</span><span class="value ' + i[3] + '">' + i[1] + '</span><span class="sub">' + e(i[2]) + '</span></div>';
    }).join('');
    $('groups').innerHTML = d.groups.map(function (g) {
      var head = '<div class="card-head"><h2>' + e(g.name) + '</h2><span class="small muted">' + (g.kind === 'cluster' ? 'cluster' : 'losse node') + '</span>' +
        '<div class="end">' + (g.reachable ? '<button type="button" class="btn primary" data-act="new" data-s="' + e(g.server) + '">Nieuwe job</button>' : '') + '</div></div>';
      if (!g.reachable) return '<section class="card warnline">' + head + '<div class="small c-crit" style="padding:14px 18px">Niet bereikbaar: ' + e((g.errors || {}).server || '') + '</div></section>';
      var errs = Object.keys(g.errors || {}).map(function (k) { return '<div class="small c-warn">' + e(k.replace('storage:', 'Opslag ').replace('jobs', 'Jobs').replace('not_backed_up', 'Niet-geback-upte VM/CT\'s')) + ': ' + e(g.errors[k]) + '</div>'; }).join('');
      var jobs = g.jobs.length ? '<div class="list" style="padding:4px 18px 8px">' + g.jobs.map(function (j) { return jobRow(g, j); }).join('') + '</div>'
        : '<div class="small muted" style="padding:12px 18px">Nog geen back-upjobs. Maak er een met <strong>Nieuwe job</strong>.</div>';
      var stores = g.storages.length ? '<div class="jobmeta" style="padding:0 18px 12px">Back-upopslag: ' + g.storages.map(function (s) {
        return '<span><span class="mono">' + e(s.name) + '</span>' + (s.node ? ' (' + e(s.node) + ')' : '') + ' ' + PF.bytes(s.used) + ' / ' + PF.bytes(s.total, 0) + ' · <span class="' + (s.pct >= 90 ? 'c-crit' : (s.pct >= 80 ? 'c-warn' : '')) + '">' + Math.round(s.pct) + '%</span></span>';
      }).join('') + '</div>' : '';
      var tasks = (g.tasks || []).slice(0, 8);
      var taskHtml = tasks.length ? '<div class="list" style="padding:4px 18px 10px">' + tasks.map(function (t) {
        return '<div class="row"><div><div>' + e(PF.taskName(t)) + ' <span class="muted small">op ' + e(t.node) + '</span></div><div class="small muted mono" style="margin-top:2px">' + PF.when(t.start) + '</div></div>' + PF.taskResult(t) + '</div>';
      }).join('') + '</div>' : '<div class="small muted" style="padding:10px 18px">Geen recente back-uptaken.</div>';
      return '<section class="card" style="overflow:hidden">' + head +
        (errs ? '<div style="padding:10px 18px 0">' + errs + '</div>' : '') +
        '<h3 class="small-title" style="padding:12px 18px 0;font-size:14px">Jobs</h3>' + jobs + stores +
        '<h3 class="small-title" style="padding:12px 18px 6px;font-size:14px;border-top:1px solid var(--line)">VM\'s &amp; containers</h3>' + guestRows(g) +
        '<h3 class="small-title" style="padding:12px 18px 0;font-size:14px;border-top:1px solid var(--line)">Recente back-uptaken</h3>' + taskHtml + '</section>';
    }).join('') || '<div class="card pad muted">Geen servers ingesteld.</div>';
  }
  function PL_seg() {
    Array.prototype.forEach.call($('f-show').querySelectorAll('[data-f]'), function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-f') === show ? 'true' : 'false'); });
  }

  function load(force) {
    if (force) $('reload').disabled = true;
    return PF.getJSON('/api/backups' + (force ? '?force=1' : '')).then(function (d) {
      $('reload').disabled = false; render(d);
      var l = $('live'); if (l) { l.classList.remove('stale'); l.querySelector('.txt').textContent = 'Bijgewerkt ' + PF.clock(Date.now() / 1000); }
    },
      function (err) { $('reload').disabled = false; PFA.toast(err.message, true); });
  }
  function group(server) { return data.groups.filter(function (g) { return g.server === server; })[0]; }
  function jobOf(g, id) { return g.jobs.filter(function (j) { return j.id === id; })[0]; }

  // ------------------------------------------------------------ job maken / bewerken
  function editor(g, j) {
    var isNew = !j;
    j = j || { enabled: true, schedule: '02:00', storage: (g.storages[0] || {}).name, node: '', selection: 'all', vmids: [], exclude: [], mode: 'snapshot',
      compress: 'zstd', prune: { 'keep-daily': 7, 'keep-weekly': 4 }, comment: '', notes_template: '{{guestname}}' };
    if (j.selection === 'pool') return PFA.modal({ title: 'Job bewerken', body: 'Deze job gebruikt een pool (' + e(j.pool) + '). Bewerk hem in Proxmox (Datacenter › Back-up).', confirm: null, cancelLabel: 'Sluiten' });
    var picked = {};
    (j.selection === 'all' ? j.exclude : j.vmids).forEach(function (v) { picked[v] = 1; });
    var storeNames = [];
    g.storages.forEach(function (s) { if (storeNames.indexOf(s.name) < 0) storeNames.push(s.name); });
    if (j.storage && storeNames.indexOf(j.storage) < 0) storeNames.push(j.storage);
    var picker = '<div id="vmpick-wrap"><div class="small muted" id="vmpick-label" style="margin-bottom:6px"></div><div class="vmpick">' + g.guests.map(function (x) {
      return '<label><input type="checkbox" class="cb" data-vm="' + x.vmid + '"' + (picked[x.vmid] ? ' checked' : '') + '><span><span class="mono">' + x.vmid + '</span> ' + e(x.name) + ' <span class="muted small">' + e(x.node) + '</span></span></label>';
    }).join('') + '</div></div>';
    var p = j.prune || {};
    var fields = [
      { name: 'enabled', label: 'Job staat aan', type: 'checkbox', value: j.enabled },
      { name: 'schedule', label: 'Wanneer', value: j.schedule, mono: true, hint: 'bijv. 02:00 (elke dag) · sun 03:00 · mon..fri 21:00 · sat 04:00' },
      { name: 'storage', label: 'Opslag', type: 'select', value: j.storage, options: storeNames.map(function (n) { return { value: n, label: n }; }) },
      { name: 'node', label: 'Node', type: 'select', value: j.node || '', options: [{ value: '', label: 'Alle nodes' }].concat(g.nodes.map(function (n) { return { value: n.name, label: 'Alleen ' + n.name }; })) },
      { name: 'selection', label: 'Welke VM\'s & containers', type: 'select', value: j.selection, options: [
        { value: 'all', label: 'Alle (met uitzonderingen hieronder)' }, { value: 'include', label: 'Alleen de gekozen hieronder' }] },
      { name: 'mode', label: 'Modus', type: 'select', value: j.mode, options: [
        { value: 'snapshot', label: 'Snapshot: VM/CT blijft draaien (aanbevolen)' }, { value: 'suspend', label: 'Suspend: pauzeert kort' }, { value: 'stop', label: 'Stop: gaat uit tijdens de back-up' }] },
      { name: 'compress', label: 'Compressie', type: 'select', value: j.compress || 'zstd', options: [
        { value: 'zstd', label: 'ZSTD (snel en goed, aanbevolen)' }, { value: 'lzo', label: 'LZO (snelst)' }, { value: 'gzip', label: 'GZIP' }, { value: '0', label: 'Geen' }] },
      { name: 'keep_last', label: 'Bewaar laatste', type: 'number', value: p['keep-last'] || '', placeholder: 'leeg = niet', hint: 'aantal' },
      { name: 'keep_daily', label: 'Bewaar dagen', type: 'number', value: p['keep-daily'] || '', placeholder: 'leeg = niet' },
      { name: 'keep_weekly', label: 'Bewaar weken', type: 'number', value: p['keep-weekly'] || '', placeholder: 'leeg = niet' },
      { name: 'keep_monthly', label: 'Bewaar maanden', type: 'number', value: p['keep-monthly'] || '', placeholder: 'leeg = niet', hint: 'alles leeg = de instelling van de opslag' },
      { name: 'comment', label: 'Omschrijving (optioneel)', value: j.comment },
      { name: 'notes_template', label: 'Notitie bij elke back-up', value: j.notes_template, mono: true, hint: 'bijv. {{guestname}}' }
    ];
    function pickedIds() {
      return Array.prototype.map.call(document.querySelectorAll('.modal [data-vm]:checked'), function (c) { return +c.getAttribute('data-vm'); });
    }
    var timer = null;
    return PFA.modal({ title: isNew ? 'Nieuwe back-upjob · ' + g.name : 'Back-upjob bewerken', wide: true, body: picker, fields: fields,
      confirm: isNew ? 'Job maken' : 'Opslaan', kind: 'primary',
      onRender: function (root) {
        var sel = root.querySelector('[name="selection"]');
        var wrap = root.querySelector('#vmpick-wrap');
        var field = sel.closest('label');
        field.parentNode.insertBefore(wrap, field.nextSibling);
        function lbl() { root.querySelector('#vmpick-label').textContent = sel.value === 'all' ? 'Vink aan wat je wilt overslaan:' : 'Vink aan wat in deze job moet:'; }
        sel.addEventListener('change', lbl); lbl();
        var sch = root.querySelector('[name="schedule"]');
        var prev = document.createElement('div'); prev.className = 'small muted'; prev.style.marginTop = '-6px';
        sch.closest('label').parentNode.insertBefore(prev, sch.closest('label').nextSibling);
        function preview() {
          clearTimeout(timer);
          timer = setTimeout(function () {
            var v = sch.value.trim();
            if (!v) { prev.textContent = ''; return; }
            PF.getJSON('/api/backups/' + encodeURIComponent(g.server) + '/schedule?schedule=' + encodeURIComponent(v)).then(function (r) {
              prev.innerHTML = 'Volgende keren: ' + (r.next || []).map(nextWhen).map(e).join(' · ');
            }, function () { prev.innerHTML = '<span class="c-crit">Proxmox begrijpt dit schema niet</span>'; });
          }, 350);
        }
        sch.addEventListener('input', preview); preview();
      },
      validate: function (v) {
        if (!v.schedule) return 'Vul in wanneer de job draait';
        if (v.selection === 'include' && !pickedIds().length) return 'Kies minstens één VM of container';
        return null;
      },
      submit: function (v) {
        var body = { id: isNew ? '' : j.id, enabled: v.enabled, schedule: v.schedule, storage: v.storage, node: v.node, selection: v.selection,
          vmids: pickedIds(), mode: v.mode, compress: v.compress, comment: v.comment, notes_template: v.notes_template,
          prune: { 'keep-last': v.keep_last, 'keep-daily': v.keep_daily, 'keep-weekly': v.keep_weekly, 'keep-monthly': v.keep_monthly } };
        return PF.postJSON('/api/backups/' + encodeURIComponent(g.server) + '/job', body).then(function (r) { PFA.toast(r.message); });
      } }).then(function (v) { clearTimeout(timer); if (v) load(true); });
  }

  function runNow(g, j) {
    return PFA.modal({ title: 'Job nu uitvoeren?', body: 'Back-up van ' + selText(j, g) + ' naar <span class="mono">' + e(j.storage) + '</span>, net als bij ' + human(j.schedule) + '. De VM\'s &amp; containers blijven draaien' + (j.mode === 'stop' ? ', <strong>behalve</strong>: deze job gebruikt de modus <em>stop</em>' : '') + '.',
      confirm: 'Nu uitvoeren', kind: 'primary' }).then(function (v) {
      if (!v) return;
      return PF.postJSON('/api/backups/' + encodeURIComponent(g.server) + '/job/' + encodeURIComponent(j.id) + '/run').then(function (r) {
        (r.started || []).forEach(function (s) { PFA.track({ server: g.server, node: s.node, upid: s.upid, label: 'Back-upjob op ' + s.node, message: r.message }); });
        PFA.toast(r.message + (r.errors && r.errors.length ? '. Fouten: ' + r.errors.join('; ') : '') + (r.skipped && r.skipped.length ? '. Overgeslagen: ' + r.skipped.join(', ') : ''), !!(r.errors && r.errors.length));
      }, function (err) { PFA.toast(err.message, true); });
    });
  }

  $('groups').addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-act]') : null;
    if (!b || !data) return;
    var g = group(b.getAttribute('data-s')), a = b.getAttribute('data-act');
    if (!g) return;
    var j = b.getAttribute('data-j') ? jobOf(g, b.getAttribute('data-j')) : null;
    if (a === 'new') editor(g, null);
    else if (a === 'edit' && j) editor(g, j);
    else if (a === 'run' && j) runNow(g, j);
    else if (a === 'del' && j) PFA.modal({ title: 'Back-upjob verwijderen?', body: human(j.schedule) + ' naar <span class="mono">' + e(j.storage) + '</span>. Bestaande back-ups blijven gewoon staan.', confirm: 'Verwijderen', kind: 'danger' })
      .then(function (v) {
        if (!v) return;
        PF.send('DELETE', '/api/backups/' + encodeURIComponent(g.server) + '/job/' + encodeURIComponent(j.id)).then(function (r) { PFA.toast(r.message); load(true); }, function (err) { PFA.toast(err.message, true); });
      });
    else if (a === 'now') {
      var vmid = +b.getAttribute('data-v');
      var x = g.guests.filter(function (y) { return y.vmid === vmid; })[0];
      if (x) PFA.backup([{ server: g.server, node: x.node, type: x.type, vmid: x.vmid, name: x.name, status: x.status }]);
    }
  });
  $('groups').addEventListener('change', function (ev) {
    var t = ev.target;
    if (!t.hasAttribute('data-toggle')) return;
    var server = t.getAttribute('data-s'), id = t.getAttribute('data-toggle');
    t.disabled = true;
    PF.postJSON('/api/backups/' + encodeURIComponent(server) + '/job/' + encodeURIComponent(id) + '/enabled', { enabled: t.checked }).then(function (r) {
      PFA.toast(r.message); load(true);
    }, function (err) { t.checked = !t.checked; t.disabled = false; PFA.toast(err.message, true); });
  });
  $('f-show').addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-f]'); if (!b) return;
    show = b.getAttribute('data-f');
    try { localStorage.setItem('pvef_backups_show', show); } catch (x) { /* niets */ }
    if (data) render(data);
  });
  $('reload').addEventListener('click', function () { load(true); });
  window.addEventListener('pf:refresh', function () { load(true); });
  load(false);
  setInterval(function () { if (!window.PFA_MODAL_OPEN) load(false); }, 60000);
})();
