/* Acties in het dashboard: dialogen, meldingen en het takenpaneel.
   Bewust zonder ?. en ?? zodat oudere Safari meedoet. */
var PFA = (function () {
  var e = PF.esc;

  // ------------------------------------------------------------ melding
  var toastTimer = null;
  function toast(msg, isErr) {
    var el = document.getElementById('pf-toast');
    if (!el) { el = document.createElement('div'); el.id = 'pf-toast'; document.body.appendChild(el); }
    el.className = 'toast ' + (isErr ? 'err' : 'ok');
    el.setAttribute('role', isErr ? 'alert' : 'status');
    el.textContent = msg;
    el.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.style.display = 'none'; }, isErr ? 8000 : 4000);
  }

  // ------------------------------------------------------------ dialoog
  /* opts: title, body (html), fields [{name,label,type,value,options,hint,placeholder,required}],
     confirm, kind ('primary'|'danger'|'blue'|'go'), wide, validate(values) -> foutmelding of null,
     cancel (false = geen annuleerknop). Geeft een Promise met de waarden of null. */
  function modal(opts) {
    return new Promise(function (resolve) {
      var prevFocus = document.activeElement;
      var back = document.createElement('div');
      back.className = 'modal-back';
      var id = 'm' + Math.random().toString(36).slice(2, 8);
      var fields = (opts.fields || []).map(function (f, i) {
        var fid = id + '-' + i;
        if (f.type === 'checkbox') {
          return '<label class="check"><input type="checkbox" name="' + e(f.name) + '"' + (f.value ? ' checked' : '') + (f.disabled ? ' disabled' : '') + '>' + e(f.label) + '</label>' +
            (f.hint ? '<div class="small muted" style="margin:-6px 0 0 28px">' + f.hint + '</div>' : '');
        }
        if (f.type === 'radio') {
          return '<fieldset style="border:0;margin:0;padding:0"><legend class="small muted" style="text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">' + e(f.label) + '</legend><div class="radiolist">' +
            f.options.map(function (o) {
              var sel = String(o.value) === String(f.value);
              return '<label class="' + (sel ? 'sel' : '') + (o.disabled ? ' dis' : '') + '"><input type="radio" name="' + e(f.name) + '" value="' + e(o.value) + '"' + (sel ? ' checked' : '') + (o.disabled ? ' disabled' : '') + '>' +
                '<span class="mono">' + e(o.label) + '</span>' + (o.meta ? '<span class="meta">' + e(o.meta) + '</span>' : '') + '</label>';
            }).join('') + '</div>' + (f.hint ? '<div class="small muted" style="margin-top:6px">' + f.hint + '</div>' : '') + '</fieldset>';
        }
        if (f.type === 'select') {
          return '<label class="field" for="' + fid + '">' + e(f.label) + '<select class="input" id="' + fid + '" name="' + e(f.name) + '">' +
            f.options.map(function (o) { return '<option value="' + e(o.value) + '"' + (String(o.value) === String(f.value) ? ' selected' : '') + '>' + e(o.label) + '</option>'; }).join('') +
            '</select>' + (f.hint ? '<span class="hint">' + f.hint + '</span>' : '') + '</label>';
        }
        var tag = f.type === 'textarea' ? 'textarea' : 'input';
        return '<label class="field" for="' + fid + '">' + e(f.label) + (f.hint ? ' <span class="hint">' + f.hint + '</span>' : '') +
          '<' + tag + ' class="input' + (f.mono ? ' mono' : '') + '" id="' + fid + '" name="' + e(f.name) + '"' + (tag === 'input' ? ' type="' + (f.type || 'text') + '" value="' + e(f.value || '') + '"' : '') +
          (f.placeholder ? ' placeholder="' + e(f.placeholder) + '"' : '') + (f.maxlength ? ' maxlength="' + f.maxlength + '"' : '') + ' autocomplete="off" autocapitalize="none">' +
          (tag === 'textarea' ? e(f.value || '') + '</textarea>' : '') + '</label>';
      }).join('');
      back.innerHTML = '<div class="modal' + (opts.wide ? ' wide' : '') + '" role="dialog" aria-modal="true" aria-labelledby="' + id + '-t">' +
        '<h2 id="' + id + '-t">' + e(opts.title) + '</h2>' + (opts.body ? '<div class="body">' + opts.body + '</div>' : '') +
        (fields ? '<form class="mform" style="display:flex;flex-direction:column;gap:12px">' + fields + '</form>' : '') +
        '<div class="errbox mfail" role="alert" style="display:none"></div>' +
        '<div class="minfo" aria-live="polite"></div>' +
        '<div class="actions">' + (opts.extra || []).map(function (x, i) {
          return '<button type="button" class="btn lg ghost" data-mx="' + i + '" style="margin-right:auto">' + e(x.label) + '</button>';
        }).join('') + (opts.cancel === false ? '' : '<button type="button" class="btn lg ghost" data-m="cancel">' + e(opts.cancelLabel || 'Annuleren') + '</button>') +
        (opts.confirm ? '<button type="button" class="btn lg ' + (opts.kind === 'danger' ? 'danger solid' : (opts.kind || 'primary')) + '" data-m="ok">' + e(opts.confirm) + '</button>' : '') + '</div></div>';
      document.body.appendChild(back);
      var form = back.querySelector('form');
      var hasFields = !!(opts.fields && opts.fields.length);
      var initial = null;   // ingevuld na het tekenen
      var escArmed = 0;
      function dirty() { return hasFields && initial !== null && JSON.stringify(values()) !== initial; }
      function guardedClose() {
        // Formulier met wijzigingen: niet zomaar weggooien.
        if (dirty() && !escArmed) {
          escArmed = Date.now();
          var f = back.querySelector('.mfail');
          f.textContent = 'Je hebt wijzigingen die nog niet zijn opgeslagen. Druk nogmaals op Esc of klik op Annuleren om ze weg te gooien, of klik op Opslaan.';
          f.style.display = 'block';
          return;
        }
        close(null);
      }

      function values() {
        var v = {};
        (opts.fields || []).forEach(function (f) {
          if (f.type === 'checkbox') { var c = back.querySelector('[name="' + f.name + '"]'); v[f.name] = !!(c && c.checked); }
          else if (f.type === 'radio') { var r = back.querySelector('[name="' + f.name + '"]:checked'); v[f.name] = r ? r.value : null; }
          else { var i = back.querySelector('[name="' + f.name + '"]'); v[f.name] = i ? i.value.trim() : ''; }
        });
        return v;
      }
      function close(result) {
        document.removeEventListener('keydown', onKey);
        window.PFA_MODAL_OPEN = Math.max(0, (window.PFA_MODAL_OPEN || 1) - 1);
        back.parentNode.removeChild(back);
        if (prevFocus && prevFocus.focus) { try { prevFocus.focus(); } catch (x) { /* niets */ } }
        resolve(result);
      }
      function ok() {
        var v = values();
        var err = opts.validate ? opts.validate(v) : null;
        var fail = back.querySelector('.mfail');
        if (err) { fail.textContent = err; fail.style.display = 'block'; return; }
        if (!opts.submit) return close(v);
        // dialoog blijft open tot het opslaan gelukt is
        var btn = back.querySelector('[data-m="ok"]');
        btn.disabled = true; fail.style.display = 'none';
        opts.submit(v).then(function () { close(v); }, function (ex) {
          btn.disabled = false; fail.textContent = ex.message || String(ex); fail.style.display = 'block';
        });
      }
      function onKey(ev) {
        if (ev.key === 'Escape') guardedClose();
        if (ev.key === 'Tab') {   // focus binnen de dialoog houden
          var f = back.querySelectorAll('button, input, select, textarea');
          if (!f.length) return;
          if (ev.shiftKey && document.activeElement === f[0]) { ev.preventDefault(); f[f.length - 1].focus(); }
          else if (!ev.shiftKey && document.activeElement === f[f.length - 1]) { ev.preventDefault(); f[0].focus(); }
        }
      }
      var downOnBack = false;
      back.addEventListener('mousedown', function (ev) { downOnBack = ev.target === back; });
      back.addEventListener('click', function (ev) {
        if (ev.target === back) {
          if (downOnBack && !hasFields) close(null);   // formulieren sluit je alleen met Annuleren/Esc
          downOnBack = false;
          return;
        }
        var b = ev.target.closest ? ev.target.closest('[data-m]') : null;
        if (b) { if (b.getAttribute('data-m') === 'ok') ok(); else close(null); return; }
        var x = ev.target.closest ? ev.target.closest('[data-mx]') : null;
        if (x) {
          var spec = opts.extra[+x.getAttribute('data-mx')];
          var fail = back.querySelector('.mfail'); fail.style.display = 'none';
          spec.onClick(values(), {
            button: x,
            info: function (html) {
              var el = back.querySelector('.minfo');
              el.innerHTML = html || '';
              if (html && el.scrollIntoView) { try { el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (x) { el.scrollIntoView(false); } }
            },
            set: function (name, value) {
              var el = back.querySelector('[name="' + name + '"]');
              if (!el) return;
              if (el.type === 'checkbox') el.checked = !!value;
              else if (el.type === 'radio') {
                Array.prototype.forEach.call(back.querySelectorAll('[name="' + name + '"]'), function (r) {
                  r.checked = r.value === String(value);
                  var l = r.closest('label'); if (l) l.classList.toggle('sel', r.checked);
                });
              } else el.value = value;
            }
          });
        }
      });
      back.addEventListener('change', function (ev) {
        if (ev.target.type === 'radio') {
          var list = ev.target.closest('.radiolist');
          Array.prototype.forEach.call(list.querySelectorAll('label'), function (l) { l.classList.toggle('sel', l.contains(ev.target)); });
        }
      });
      if (form) form.addEventListener('submit', function (ev) { ev.preventDefault(); ok(); });
      document.addEventListener('keydown', onKey);
      window.PFA_MODAL_OPEN = (window.PFA_MODAL_OPEN || 0) + 1;
      if (opts.onRender) opts.onRender(back);
      if (hasFields) initial = JSON.stringify(values());
      var first = back.querySelector('input[type=text], textarea, select') || back.querySelector('[data-m="ok"]') || back.querySelector('[data-m="cancel"]');
      if (first) first.focus();
    });
  }

  // ------------------------------------------------------------ takenpaneel
  var KEY = 'pvef_tasks';
  var tasks = [];
  try { tasks = JSON.parse(sessionStorage.getItem(KEY) || '[]'); } catch (x) { tasks = []; }
  tasks = tasks.filter(function (t) { return Date.now() - t.added < 3600000; });
  function save() { try { sessionStorage.setItem(KEY, JSON.stringify(tasks)); } catch (x) { /* privémodus */ } }
  function emitRefresh() {
    var ev;
    try { ev = new Event('pf:refresh'); } catch (x) { ev = document.createEvent('Event'); ev.initEvent('pf:refresh', true, true); }
    window.dispatchEvent(ev);
  }
  function renderTasks() {
    var root = document.getElementById('pf-tasks');
    if (!root) { root = document.createElement('div'); root.id = 'pf-tasks'; root.className = 'tasks'; root.setAttribute('aria-live', 'polite'); document.body.appendChild(root); }
    root.innerHTML = tasks.map(function (t) {
      var icon = t.state === 'running' ? '<span class="spinner" aria-hidden="true"></span>'
        : '<span class="dot b-' + (t.state === 'ok' || t.state === 'sent' ? 'ok' : 'crit') + '" style="width:10px;height:10px" aria-hidden="true"></span>';
      var sub = t.state === 'running' ? '<span class="c-info">Bezig…</span>' : (t.state === 'ok' ? '<span class="c-ok">Klaar</span>'
        : (t.state === 'sent' ? '<span class="c-ok">' + e(t.msg || 'Verstuurd') + '</span>' : '<span class="c-crit">Mislukt: ' + e(t.exit || '') + '</span>'));
      return '<div class="task">' + icon + '<div class="t"><div class="l" title="' + e(t.label) + '">' + e(t.label) + '</div><div class="s">' + sub + ' <span class="muted">· ' + e(t.node) + '</span></div></div>' +
        (t.upid ? '<button type="button" class="btn ghost" data-tlog="' + t.id + '">Log</button>' : '') +
        (t.state !== 'running' ? '<button type="button" class="btn ghost iconbtn" data-tclose="' + t.id + '" aria-label="Sluiten">×</button>' : '') + '</div>';
    }).join('');
  }
  function taskUrl(t) { return '/api/task/' + encodeURIComponent(t.server) + '/' + encodeURIComponent(t.node) + '/' + encodeURIComponent(t.upid); }
  function pollTasks() {
    tasks.forEach(function (t) {
      if (t.state !== 'running' || t.busy) return;
      t.busy = true;
      PF.getJSON(taskUrl(t)).then(function (r) {
        t.busy = false;
        if (!r.running) {
          t.state = r.ok ? 'ok' : 'fail'; t.exit = r.exitstatus; t.done = Date.now();
          save(); renderTasks();
          PF.postJSON('/api/refresh/' + encodeURIComponent(t.server)).then(emitRefresh, emitRefresh);
          if (!r.ok) toast('Mislukt: ' + t.label + (r.exitstatus ? ' (' + r.exitstatus + ')' : ''), true);
          if (t.after) { var a = t.after; t.after = null; save(); if (r.ok) handleAfter(a); }
        }
      }).catch(function (err) {
        t.busy = false;
        if (err.status === 404 || err.status === 502) { t.fails = (t.fails || 0) + 1; if (t.fails > 15) { t.state = 'fail'; t.exit = 'status onbekend'; save(); renderTasks(); } }
      });
    });
    var now = Date.now();
    var before = tasks.length;
    tasks = tasks.filter(function (t) { return !((t.state === 'ok' || t.state === 'sent') && now - (t.done || t.added) > 8000); });
    if (tasks.length !== before) { save(); renderTasks(); }
  }
  function handleAfter(a) {
    // na een migratie op de pagina van een VM/CT: meeverhuizen naar de nieuwe node
    if (a.type === 'goto' && location.pathname === a.from) location.href = a.to;
  }
  function track(res, after) {
    var t = { id: Math.random().toString(36).slice(2, 9), server: res.server, node: res.node, upid: res.upid, label: res.label,
      added: Date.now(), state: res.upid ? 'running' : 'sent', msg: res.message, after: after || null };
    if (!res.upid) t.done = Date.now();
    tasks.unshift(t);
    tasks = tasks.slice(0, 4);
    save(); renderTasks();
  }
  function showLog(t) {
    var timer = null;
    modal({ title: t.label, wide: true, body: '<pre class="log" id="pf-log">Laden…</pre>', confirm: null, cancelLabel: 'Sluiten',
      onRender: function () {
        function load() {
          PF.getJSON(taskUrl(t)).then(function (r) {
            var el = document.getElementById('pf-log');
            if (!el) { clearInterval(timer); return; }
            el.textContent = (r.log && r.log.length ? r.log.join('\n') : '(nog geen uitvoer)') + (r.running ? '\n…' : '');
            el.scrollTop = el.scrollHeight;
            if (!r.running) clearInterval(timer);
          }).catch(function (err) { var el = document.getElementById('pf-log'); if (el) el.textContent = 'Kan log niet ophalen: ' + err.message; });
        }
        load(); timer = setInterval(load, 2000);
      } }).then(function () { clearInterval(timer); });
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-tlog],[data-tclose]') : null;
    if (!b) return;
    var id = b.getAttribute('data-tlog') || b.getAttribute('data-tclose');
    var t = tasks.filter(function (x) { return x.id === id; })[0];
    if (!t) return;
    if (b.hasAttribute('data-tlog')) showLog(t);
    else { tasks = tasks.filter(function (x) { return x.id !== id; }); save(); renderTasks(); }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderTasks); else renderTasks();
  setInterval(pollTasks, 2000);

  // ------------------------------------------------------------ uitvoeren
  function run(body, after) {
    return PF.postJSON('/api/action', body).then(function (res) {
      toast(res.message);
      track(res, after);
      setTimeout(emitRefresh, 1800);
      return res;
    }, function (err) { toast(err.message, true); throw err; });
  }
  function runMany(bodies) {
    // na elkaar versturen; Proxmox zet ze zelf in de wachtrij
    var p = Promise.resolve(), okc = 0, fail = 0;
    bodies.forEach(function (b) {
      p = p.then(function () { return run(b).then(function () { okc++; }, function () { fail++; }); });
    });
    return p.then(function () { if (bodies.length > 1) toast(okc + ' van ' + bodies.length + ' opdrachten verstuurd' + (fail ? ', ' + fail + ' mislukt' : ''), fail > 0); });
  }

  function gname(g) { return (g.type === 'qemu' ? 'VM ' : 'CT ') + g.vmid + ' ' + g.name; }
  function base(g, action, params) { return { action: action, server: g.server, node: g.node, type: g.type, vmid: g.vmid, params: params || {} }; }
  function listHtml(list) {
    return '<ul style="margin:8px 0 0;padding-left:18px;max-height:160px;overflow:auto">' + list.map(function (g) { return '<li class="mono">' + e(gname(g)) + '</li>'; }).join('') + '</ul>';
  }

  var POWER = {
    start: { t: 'starten', c: 'Starten', k: 'go', b: '' },
    shutdown: { t: 'afsluiten', c: 'Afsluiten', k: 'primary', b: 'Stuurt een nette afsluitopdracht (ACPI voor een VM, via het besturingssysteem voor een container). Reageert de VM/CT niet, gebruik dan <strong>Geforceerd stoppen</strong>.' },
    reboot: { t: 'herstarten', c: 'Herstarten', k: 'primary', b: 'De VM/CT wordt netjes afgesloten en weer gestart.' },
    stop: { t: 'geforceerd stoppen', c: 'Geforceerd stoppen', k: 'danger', b: 'Dit is als de stekker eruit trekken: niet-opgeslagen gegevens gaan verloren. Gebruik dit alleen als afsluiten niet werkt.' }
  };
  function power(list, action) {
    if (!list.length) return Promise.resolve();
    var p = POWER[action];
    var one = list.length === 1;
    return modal({ title: one ? gname(list[0]) + ' ' + p.t + '?' : list.length + ' VM\'s & containers ' + p.t + '?',
      body: (p.b || '') + (one ? '' : listHtml(list)), confirm: p.c, kind: p.k }).then(function (v) {
      if (!v) return;
      return runMany(list.map(function (g) { return base(g, action); }));
    });
  }

  function stamp() {
    var d = new Date();
    function z(n) { return ('0' + n).slice(-2); }
    return 'snap-' + d.getFullYear() + z(d.getMonth() + 1) + z(d.getDate()) + '-' + z(d.getHours()) + z(d.getMinutes());
  }
  var SNAP_RE = /^[A-Za-z][A-Za-z0-9_-]{1,39}$/;
  function snapshot(list) {
    if (!list.length) return Promise.resolve();
    var one = list.length === 1;
    var anyVmRunning = list.some(function (g) { return g.type === 'qemu' && g.status === 'running'; });
    var fields = [{ name: 'snapname', label: 'Naam', value: stamp(), mono: true, maxlength: 40, hint: 'begint met een letter; letters, cijfers, - en _' },
      { name: 'description', label: 'Omschrijving (optioneel)', type: 'textarea', placeholder: 'bijv. voor de update van vanavond' }];
    if (anyVmRunning) fields.push({ name: 'vmstate', label: 'Werkgeheugen (RAM) meenemen', type: 'checkbox', value: false,
      hint: 'Alleen voor draaiende VM\'s. Duurt langer en kost schijfruimte, maar je kunt terug naar de draaiende toestand.' });
    return modal({ title: one ? 'Snapshot van ' + gname(list[0]) : 'Snapshot van ' + list.length + ' VM\'s & containers', body: one ? '' : listHtml(list),
      fields: fields, confirm: 'Snapshot maken', kind: 'primary',
      validate: function (v) { return SNAP_RE.test(v.snapname) ? null : 'Ongeldige naam: begin met een letter, daarna letters, cijfers, - of _ (2 tot 40 tekens).'; }
    }).then(function (v) {
      if (!v) return;
      return runMany(list.map(function (g) { return base(g, 'snapshot', { snapname: v.snapname, description: v.description, vmstate: v.vmstate }); }));
    });
  }
  function rollback(g, snap) {
    return modal({ title: 'Terugdraaien naar \'' + snap + '\'?',
      body: '<strong>' + e(gname(g)) + '</strong> gaat terug naar de toestand van deze snapshot. Alles wat daarna is veranderd, gaat verloren. ' +
        (g.status === 'running' ? 'De VM/CT wordt daarvoor gestopt.' : ''),
      confirm: 'Terugdraaien', kind: 'danger' }).then(function (v) { if (v) return run(base(g, 'rollback', { snapname: snap })); });
  }
  function delSnapshot(g, snap) {
    return modal({ title: 'Snapshot \'' + snap + '\' verwijderen?', body: 'Van ' + e(gname(g)) + '. Dit kun je niet ongedaan maken.',
      confirm: 'Verwijderen', kind: 'danger' }).then(function (v) { if (v) return run(base(g, 'delsnapshot', { snapname: snap })); });
  }

  function backup(list) {
    if (!list.length) return Promise.resolve();
    var g0 = list[0];
    return PF.getJSON('/api/backup-storages/' + encodeURIComponent(g0.server) + '/' + encodeURIComponent(g0.node)).then(function (r) {
      var st = r.storages || [];
      if (!st.length) {
        return modal({ title: 'Geen back-upopslag', body: 'Op ' + e(g0.node) + ' is geen opslag gevonden waarop back-ups mogen (inhoudstype "backup"). Stel dat in via Proxmox › Datacenter › Storage.', confirm: null, cancelLabel: 'Sluiten' });
      }
      return modal({ title: list.length === 1 ? 'Back-up van ' + gname(g0) : 'Back-up van ' + list.length + ' VM\'s & containers',
        body: list.length === 1 ? '' : listHtml(list),
        fields: [{ name: 'storage', label: 'Opslag', type: 'radio', value: st[0].name,
          options: st.map(function (s) { return { value: s.name, label: s.name, meta: s.type + ' · ' + Math.round(s.pct) + '% vol' + (s.shared ? ' · gedeeld' : '') }; }) },
          { name: 'mode', label: 'Modus', type: 'select', value: 'snapshot', options: [
            { value: 'snapshot', label: 'Snapshot: VM/CT blijft draaien (aanbevolen)' },
            { value: 'suspend', label: 'Suspend: VM/CT pauzeert kort' },
            { value: 'stop', label: 'Stop: VM/CT gaat uit tijdens de back-up (meest consistent)' }] }],
        confirm: 'Back-up starten', kind: 'primary',
        validate: function (v) { return v.storage ? null : 'Kies een opslag'; } }).then(function (v) {
        if (!v) return;
        return runMany(list.map(function (g) { return base(g, 'backup', { storage: v.storage, mode: v.mode }); }));
      });
    }, function (err) { toast(err.message, true); });
  }

  function migrate(list, opts) {
    if (!list.length) return Promise.resolve();
    var g0 = list[0];
    var one = list.length === 1;
    var infoP = PF.getJSON('/api/migrate-info/' + encodeURIComponent(g0.server) + '/' + encodeURIComponent(g0.node) + '/' + g0.type + '/' + g0.vmid);
    return infoP.then(function (info) {
      var targets = info.targets || [];
      if (!targets.length) {
        return modal({ title: 'Migreren niet mogelijk', body: 'Er is geen andere node in dit cluster online.', confirm: null, cancelLabel: 'Sluiten' });
      }
      var firstOk = targets.filter(function (t) { return t.allowed; })[0];
      var fields = [{ name: 'target', label: 'Doel-node', type: 'radio', value: firstOk ? firstOk.name : '',
        options: targets.map(function (t, i) {
          return { value: t.name, label: t.name, disabled: !t.allowed,
            meta: t.allowed ? ((firstOk && t.name === firstOk.name ? 'aanbevolen · ' : '') + 'RAM ' + PF.pct(t.mem_pct) + ' · CPU ' + PF.pct(t.cpu)) : (t.reason || 'niet toegestaan') };
        }) }];
      var anyRunningVm = list.some(function (g) { return g.type === 'qemu' && g.status === 'running'; });
      var anyRunningCt = list.some(function (g) { return g.type === 'lxc' && g.status === 'running'; });
      if (anyRunningVm) fields.push({ name: 'online', label: 'Online migratie (VM blijft draaien)', type: 'checkbox', value: true });
      var local = one ? (info.local_disks || []) : [];
      if (local.length || !one) fields.push({ name: 'with_local_disks', label: 'Lokale schijven meekopiëren', type: 'checkbox', value: true,
        hint: local.length ? 'Nodig voor: <span class="mono">' + local.map(e).join(', ') + '</span>. Dit kan even duren.' : 'Nodig als een VM op lokale opslag (bijv. local-zfs) staat.' });
      var notes = [];
      if (anyRunningCt) notes.push('Een draaiende container wordt kort gestopt en op de nieuwe node weer gestart.');
      if (info.local_resources && info.local_resources.length) notes.push('<span class="c-warn">Deze VM gebruikt lokale hardware (' + info.local_resources.map(e).join(', ') + '); migreren kan daardoor mislukken.</span>');
      if (info.warning) notes.push('<span class="muted">' + e(info.warning) + '</span>');
      return modal({ title: one ? gname(g0) + ' migreren' : list.length + ' VM\'s & containers migreren', body: (one ? '' : listHtml(list)) + (notes.length ? '<p style="margin:8px 0 0">' + notes.join('<br>') + '</p>' : ''),
        fields: fields, confirm: 'Migratie starten', kind: 'blue',
        validate: function (v) { return v.target ? null : 'Kies een doel-node'; } }).then(function (v) {
        if (!v) return;
        var after = one && opts && opts.followTo ? { type: 'goto', from: location.pathname, to: PF.guestUrl(g0.server, v.target, g0.type, g0.vmid) } : null;
        if (one) return run(base(g0, 'migrate', { target: v.target, online: v.online !== false, with_local_disks: !!v.with_local_disks }), after);
        return runMany(list.map(function (g) { return base(g, 'migrate', { target: v.target, online: v.online !== false, with_local_disks: v.with_local_disks !== false }); }));
      });
    }, function (err) { toast(err.message, true); });
  }

  function nodePower(server, node, action, ctx) {
    ctx = ctx || {};
    var running = ctx.running || 0;
    if (action === 'node_reboot') {
      var body = ctx.cluster
        ? 'Proxmox sluit de ' + running + ' actieve VM\'s & containers op ' + e(node) + ' af volgens hun instellingen (VM/CT\'s met HA worden verplaatst als HA dat zo regelt). Daarna herstart de node; dat duurt meestal een paar minuten.'
        : 'De ' + running + ' actieve VM\'s & containers worden netjes afgesloten. Na de herstart starten de VM/CT\'s met "Start bij opstarten" weer vanzelf op.';
      body += '<p style="margin:8px 0 0" class="muted">Tip: wil je VM\'s & containers laten doordraaien, migreer ze dan eerst naar een andere node.</p>';
      return modal({ title: node + ' herstarten?', body: body, confirm: 'Herstarten', kind: 'danger' })
        .then(function (v) { if (v) return run({ action: action, server: server, node: node }); });
    }
    if (action === 'node_shutdown') {
      return modal({ title: node + ' afsluiten?',
        body: 'De node en alle ' + running + ' actieve VM\'s & containers gaan uit. Weer aanzetten kan met Wake-on-LAN (als dat is ingesteld) of met de aan-knop.',
        fields: [{ name: 'confirmname', label: 'Typ de naam van de node ter bevestiging', placeholder: node, mono: true }],
        confirm: 'Afsluiten', kind: 'danger',
        validate: function (v) { return v.confirmname === node ? null : 'Typ precies "' + node + '"'; } })
        .then(function (v) { if (v) return run({ action: action, server: server, node: node }); });
    }
    if (action === 'node_wol') {
      return run({ action: 'node_wol', server: server, node: node });
    }
    if (action === 'refresh') {
      return PF.postJSON('/api/action', { action: 'refresh', server: server, node: node }).then(function () { toast('Opnieuw opgehaald'); emitRefresh(); },
        function (err) { toast(err.message, true); });
    }
  }

  /* VM/CT bijwerken: pakketten ('os') of de app ('app'). Optioneel eerst een snapshot. */
  function guestUpdate(g, what, info, onDone) {
    info = info || {};
    var app = what === 'app';
    var cmd = app ? 'app' : (info.pm || 'apt');
    var body = app
      ? '<p style="margin:0 0 8px"><strong>' + e(info.app || 'De app') + '</strong> ' + (info.installed ? e(info.installed) + ' → ' : '') + e(info.latest || 'nieuwste versie') + '.</p>' +
        '<p style="margin:0 0 8px">Er opent een console van ' + e(gname(g)) + '. PVE Fleet typt <span class="mono">update</span>: het update-script van community-scripts. Volg daarna de vragen van het script.</p>'
      : '<p style="margin:0 0 8px">' + (info.count ? info.count + ' pakketupdates' + (info.security ? ', waarvan ' + info.security + ' security' : '') : 'Pakketten bijwerken') + ' in ' + e(gname(g)) + '.</p>' +
        '<p style="margin:0 0 8px">Er opent een console. PVE Fleet typt <span class="mono">' + (cmd === 'apk' ? 'apk update &amp;&amp; apk upgrade' : (cmd === 'dnf' ? 'dnf upgrade' : 'apt -o Acquire::ForceIPv4=true update; apt -o Acquire::ForceIPv4=true dist-upgrade')) + '</span>; jij bevestigt.</p>';
    body += '<p class="small muted" style="margin:0">Is de app-sleutel ingesteld (pagina Apps), dan opent PVE Fleet de container via de node en hoef je niet in te loggen. Anders: zie je een inlogprompt, log dan in als root in de container.</p>';
    return modal({ title: (app ? 'App bijwerken: ' : 'Bijwerken: ') + gname(g), body: body,
      fields: [{ name: 'snap', label: 'Eerst een snapshot maken (aanbevolen)', type: 'checkbox', value: true,
        hint: 'Gaat er iets mis, dan kun je terug. Verwijder de snapshot later via de pagina van de VM/CT.' }],
      confirm: 'Console openen', kind: 'primary' }).then(function (v) {
      if (!v) return;
      if (!v.snap) return PF.openUpgrade(g.server, g.node, onDone, g.vmid, cmd);
      // Venster nu al openen (anders blokkeert de browser de pop-up), en vullen zodra de snapshot klaar is.
      var win = null;
      try {
        win = window.open('', 'pvef-console-update-' + g.vmid, 'width=1024,height=700');
        if (win) win.document.write('<body style="background:#050607;color:#B8C0C6;font:15px system-ui;padding:24px">Snapshot maken… de console opent zo.</body>');
      } catch (x) { win = null; }
      var go = function () { PF.openUpgrade(g.server, g.node, onDone, g.vmid, cmd, win); };
      var d = new Date(), z = function (n) { return ('0' + n).slice(-2); };
      var name = (window.I18N && window.I18N.lang === 'en' ? 'before-update-' : 'voor-update-') + d.getFullYear() + z(d.getMonth() + 1) + z(d.getDate()) + '-' + z(d.getHours()) + z(d.getMinutes());
      return PF.postJSON('/api/action', base(g, 'snapshot', { snapname: name, description: 'PVE Fleet, voor ' + (app ? 'app-update' : 'pakketupdate') })).then(function (res) {
        track(res);
        toast('Snapshot ' + name + ' wordt gemaakt; daarna opent de console');
        if (!res.upid) return go();
        var tries = 0;
        (function wait() {
          PF.getJSON('/api/task/' + encodeURIComponent(res.server) + '/' + encodeURIComponent(res.node) + '/' + encodeURIComponent(res.upid)).then(function (r) {
            if (r.running && tries++ < 90) return setTimeout(wait, 2000);
            if (!r.ok) { toast('Snapshot mislukt (' + (r.exitstatus || '?') + '); de console opent niet. Probeer zonder snapshot of kijk in de log.', true); try { if (win) win.close(); } catch (x) { /* niets */ } return; }
            go();
          }, function () { setTimeout(wait, 3000); });
        })();
      }, function (err) { toast(err.message, true); try { if (win) win.close(); } catch (x) { /* niets */ } });
    });
  }

  return { guestUpdate: guestUpdate, toast: toast, modal: modal, run: run, track: track, power: power, snapshot: snapshot, rollback: rollback,
    delSnapshot: delSnapshot, backup: backup, migrate: migrate, nodePower: nodePower, gname: gname };
})();
