/* Rolling update: node voor node verplaatsen, bijwerken, herstarten en terugzetten.
   De stappen worden afgeleid uit de live toestand; alleen wat er verplaatst is, wordt
   op de server bewaard (data/rolling.json). */
(function () {
  var e = PF.esc;
  var $ = function (id) { return document.getElementById(id); };
  var server = $('app').getAttribute('data-server');
  var API = '/api/rolling/' + encodeURIComponent(server);
  var data = null;
  var job = null;      // lopende reeks migraties: {node, label, items:[{g, to, state, msg}], idx}
  var waitReboot = {}; // node -> tijdstip van herstartopdracht
  var checking = {};
  var AUTO = API + '/auto';
  var auto = null;       // {run, enabled, demo}: automatische rolling update op de server
  var autoSkipped = {};  // stap voor stap: nodes zonder werk die al vanzelf zijn overgeslagen
  var logOpen = {};      // welke logboeken open staan (per node)
  var bannerOpen = false; // staat "Wat er gebeurd is" open
  var autoSig = '';       // laatste antwoord van de server: alleen opnieuw tekenen als er iets veranderd is

  function save(body) { return PF.postJSON(API, body); }
  function load() { return PF.getJSON(API).then(render, function (err) { $('nodes').innerHTML = '<div class="banner crit"><div class="txt">' + e(err.message) + '</div></div>'; }); }

  function gname(g) { return (g.type === 'qemu' ? 'VM ' : 'CT ') + g.vmid + ' ' + g.name; }
  function online(n) { return n.status === 'online'; }
  function others(n) { return data.nodes.filter(function (x) { return x.name !== n.name && online(x); }); }
  function running(n) { return n.guests.filter(function (g) { return g.status === 'running'; }); }
  function needsWork(n) { return !!(n.count || n.reboot_needed || (n.behind && n.behind.length)); }
  function pendingBack(n) { return n.moved.filter(function (m) { return !m.returned && m.now && m.now !== n.name; }); }
  function movePhaseDone(n) { return n.moved.length > 0 && n.moved.every(function (m) { return m.returned; }); }
  function stayKey(g) { return g.type + '/' + g.vmid; }
  function stays(n) { var o = {}; (n.stay || []).forEach(function (m) { o[stayKey(m)] = 1; }); return o; }
  function toMove(n) { var st = stays(n); return running(n).filter(function (g) { return !st[stayKey(g)]; }); }
  function stayedDown(n) { return (n.stay || []).filter(function (m) { return m.now === n.name && m.status !== 'running'; }); }

  /* Stappen van één node: elk {key, title, state: 'done'|'todo'|'busy'|'wait'|'skip', html} */
  function steps(n) {
    var run = running(n), back = pendingBack(n), isJob = job && job.node === n.name;
    var rebootWait = waitReboot[n.name] && (Date.now() - waitReboot[n.name] < 15 * 60000);
    var s = [];
    // 1 leegmaken
    var s1 = { key: 'move', title: 'VM\'s & containers verplaatsen' };
    var mv = toMove(n), nstay = (n.stay || []).length;
    if (isJob && job.kind === 'move') { s1.state = 'busy'; s1.html = jobHtml(); }
    else if (!run.length || movePhaseDone(n)) { s1.state = 'done'; s1.html = n.moved.length ? n.moved.length + ' verplaatst' : 'Er draait niets op deze node.'; }
    else if (!mv.length && nstay) {
      s1.state = 'done';
      s1.html = (n.moved.length ? n.moved.length + ' verplaatst; ' : '') + nstay + ' blijven hier: <span class="mono small">' + n.stay.slice(0, 6).map(function (m) { return e(m.vmid + ' ' + (m.name || '')); }).join(', ') + (nstay > 6 ? ' …' : '') + '</span>. ' +
        '<span class="small muted">Ze gaan bij de herstart even uit; daarna zet PVE Fleet ze weer aan.</span> <button type="button" class="btn ghost" data-do="unskip" data-n="' + e(n.name) + '">Toch verplaatsen</button>';
    }
    else if (n.skip_move) { s1.state = 'done'; s1.html = 'Overgeslagen: ' + run.length + ' VM/CT\'s blijven hier en gaan bij de herstart even uit. <button type="button" class="btn ghost" data-do="unskip" data-n="' + e(n.name) + '">Toch verplaatsen</button>'; }
    else {
      run = mv;
      s1.state = 'todo';
      s1.html = run.length + ' actief: <span class="mono small">' + run.slice(0, 6).map(function (g) { return e(g.vmid + ' ' + g.name); }).join(', ') + (run.length > 6 ? ' …' : '') + '</span>' +
        '<div class="stepacts"><button type="button" class="btn primary" data-do="move" data-n="' + e(n.name) + '"' + (others(n).length ? '' : ' disabled') + '>Verplaatsen…</button>' +
        '<button type="button" class="btn ghost" data-do="skip" data-n="' + e(n.name) + '">Overslaan</button>' +
        '<span class="small muted">Overslaan: ze blijven hier en gaan bij een herstart even uit.</span></div>';
    }
    s.push(s1);
    // 2 bijwerken
    var s2 = { key: 'upgrade', title: 'Bijwerken' };
    if (checking[n.name]) { s2.state = 'busy'; s2.html = '<span class="spinner" aria-hidden="true"></span> ' + e(checking[n.name]); }
    else if (!online(n)) { s2.state = 'wait'; s2.html = 'Node is offline.'; }
    else if (n.count) {
      s2.state = 'todo';
      s2.html = '<span class="c-warn">' + n.count + ' updates</span>' + (n.security ? ' <span class="c-crit small">(' + n.security + ' security)</span>' : '') +
        '<div class="stepacts"><button type="button" class="btn primary" data-do="upgrade" data-n="' + e(n.name) + '">Console openen</button>' +
        '<button type="button" class="btn ghost" data-do="check" data-n="' + e(n.name) + '">Opnieuw kijken</button></div>';
    } else if ((n.count === null || n.count === undefined) && n.behind && n.behind.length) {
      s2.state = 'todo';
      s2.html = '<span class="c-warn">Loopt achter: ' + n.behind.map(e).join(', ') + '</span> <span class="small muted">(de lijst met updates is niet te zien zonder het recht Sys.Modify)</span>' +
        '<div class="stepacts"><button type="button" class="btn primary" data-do="upgrade" data-n="' + e(n.name) + '">Console openen</button>' +
        '<button type="button" class="btn ghost" data-do="check" data-n="' + e(n.name) + '">Opnieuw kijken</button></div>';
    } else if (n.count === null || n.count === undefined) {
      s2.state = 'done';
      s2.html = 'Zelfde versie als de andere nodes. <span class="small muted">De lijst met updates is niet te zien; werk voor de zekerheid bij via de console.</span>' +
        '<div class="stepacts"><button type="button" class="btn ghost" data-do="upgrade" data-n="' + e(n.name) + '">Console openen</button></div>';
    } else { s2.state = 'done'; s2.html = 'Geen openstaande updates' + (n.checked ? ' <span class="muted small">(gecontroleerd ' + PF.when(n.checked) + ')</span>' : '') + '.'; }
    s.push(s2);
    // 3 herstarten
    var s3 = { key: 'reboot', title: 'Herstarten' };
    if (!online(n)) { s3.state = 'busy'; s3.html = '<span class="spinner" aria-hidden="true"></span> Node start opnieuw op… dit duurt meestal een paar minuten.'; }
    else if (rebootWait && n.uptime !== null && n.uptime > (Date.now() - waitReboot[n.name]) / 1000 + 30) { s3.state = 'busy'; s3.html = '<span class="spinner" aria-hidden="true"></span> Herstart is verstuurd, wachten tot de node uitgaat…'; }
    else if (n.reboot_needed) {
      s3.state = s2.state === 'done' ? 'todo' : 'wait';
      s3.html = 'Nieuwe kernel <span class="mono">' + e(n.kernel_latest || '') + '</span> wordt pas gebruikt na een herstart (nu <span class="mono">' + e(n.kernel || '?') + '</span>).' +
        (s2.state === 'done' ? '<div class="stepacts"><button type="button" class="btn ' + (run.length ? 'ghost' : 'primary') + '" data-do="reboot" data-n="' + e(n.name) + '">Herstarten</button>' +
          (run.length ? '<span class="small c-warn">Er draaien nog ' + run.length + ' VM/CT\'s; die gaan dan even uit.</span>' : '') + '</div>' : '');
    } else { s3.state = 'done'; s3.html = rebootWait ? 'Opnieuw opgestart, kernel <span class="mono">' + e(n.kernel || '') + '</span>.' : 'Niet nodig.'; }
    s.push(s3);
    // 3b weer starten wat hier bleef
    if (nstay) {
      var down = stayedDown(n), s3b = { key: 'restart', title: 'Weer starten' };
      if (isJob && job.kind === 'start') { s3b.state = 'busy'; s3b.html = jobHtml(); }
      else if (!online(n) || s3.state === 'busy') { s3b.state = 'wait'; s3b.html = 'Na de herstart: ' + nstay + ' VM/CT\'s die hier bleven.'; }
      else if (down.length) {
        s3b.state = 'todo';
        s3b.html = down.length + ' staan nog uit: <span class="mono small">' + down.slice(0, 6).map(function (m) { return e(m.vmid + ' ' + (m.name || '')); }).join(', ') + (down.length > 6 ? ' …' : '') + '</span>' +
          '<div class="stepacts"><button type="button" class="btn primary" data-do="restart" data-n="' + e(n.name) + '">Weer starten</button></div>';
      } else { s3b.state = 'done'; s3b.html = 'Draaien.'; }
      s.push(s3b);
    }
    // 4 terugzetten
    var s4 = { key: 'back', title: 'Terugzetten' };
    if (isJob && job.kind === 'back') { s4.state = 'busy'; s4.html = jobHtml(); }
    else if (!n.moved.length) { s4.state = 'done'; s4.html = 'Niets terug te zetten.'; }
    else if (!back.length) {
      var kept = n.moved.filter(function (m) { return m.kept; }).length;
      s4.state = 'done'; s4.html = kept ? kept + ' blijven op hun nieuwe node staan.' : 'Alles staat weer op ' + e(n.name) + '.';
    }
    else {
      var ready = online(n) && s3.state === 'done';
      s4.state = ready ? 'todo' : 'wait';
      s4.html = back.length + ' staan nog elders: <span class="mono small">' + back.slice(0, 6).map(function (m) { return e(m.vmid + ' ' + (m.name || '') + ' → ' + m.now); }).join(', ') + (back.length > 6 ? ' …' : '') + '</span>' +
        (ready ? '<div class="stepacts"><button type="button" class="btn primary" data-do="back" data-n="' + e(n.name) + '">Terugzetten</button>' +
          '<button type="button" class="btn ghost" data-do="keep" data-n="' + e(n.name) + '">Laten staan</button></div>' : '');
    }
    s.push(s4);
    return s;
  }

  function jobHtml() {
    return '<div class="small" style="display:flex;flex-direction:column;gap:3px">' + job.items.map(function (it) {
      var icon = it.state === 'run' ? '<span class="spinner" aria-hidden="true"></span>' : (it.state === 'ok' ? '<span class="c-ok">✓</span>' : (it.state === 'fail' ? '<span class="c-crit">✗</span>' : '<span class="muted">•</span>'));
      return '<div>' + icon + ' <span class="mono">' + e(gname(it.g)) + '</span>' + (it.action === 'start' ? ' starten' : ' → ' + e(it.to)) + (it.msg ? ' <span class="' + (it.state === 'fail' ? 'c-crit' : 'muted') + '">' + e(it.msg) + '</span>' : '') + '</div>';
    }).join('') + '</div>';
  }

  function idle(n) { return !needsWork(n) && !n.moved.length && !waitReboot[n.name] && online(n); }
  function nodeState(n, st) {
    if (n.done) return 'done';
    if (idle(n)) return 'ready';
    if (st.every(function (x) { return x.state === 'done'; })) return 'ready';
    return 'todo';
  }

  function render(d) {
    data = d;
    $('title').textContent = 'Rolling update · ' + d.name;
    var order = (d.order || []).concat(d.nodes.map(function (n) { return n.name; }).filter(function (x) { return d.order.indexOf(x) < 0; }));
    var nodes = order.map(function (name) { return d.nodes.filter(function (n) { return n.name === name; })[0]; }).filter(Boolean);
    // controles
    var warn = [];
    if (d.kind !== 'cluster') warn.push(['crit', 'Dit is geen cluster. Gebruik de pagina Updates.']);
    if (d.quorate === false) warn.push(['crit', 'Het cluster heeft geen quorum. Los dat eerst op voordat je een node herstart.']);
    var busy = autoRunning() ? auto.run.current : null;   // de node die de automatische update nu herstart
    var off = nodes.filter(function (n) { return !online(n) && !waitReboot[n.name] && n.name !== busy; });
    if (off.length) warn.push(['warn', 'Offline: ' + off.map(function (n) { return n.name; }).join(', ') + '. Herstart niet nog een node zolang deze niet terug is.']);
    if (nodes.length === 2) warn.push(['warn', 'Een cluster met 2 nodes verliest quorum zodra er één herstart (tenzij je een QDevice hebt). VM\'s & containers blijven dan draaien, maar starten, stoppen en verplaatsen lukt pas weer als de node terug is.']);
    $('checks').innerHTML = warn.map(function (w) { return '<div class="banner ' + w[0] + '" style="margin-bottom:10px"><div class="txt"><div class="desc" style="margin:0">' + e(w[1]) + '</div></div></div>'; }).join('');
    if (autoRunning()) { renderAuto(nodes); return; }

    if (!d.started) {
      var todo = nodes.filter(needsWork);
      $('headacts').replaceChildren();
      $('nodes').innerHTML = autoBanner() + '<section class="card pad"><h2 class="small-title">Zo gaat het</h2>' +
        '<ol style="margin:0 0 14px;padding-left:20px;line-height:1.8;color:var(--text2)">' +
        '<li><strong>Verplaatsen:</strong> de draaiende VM\'s &amp; containers gaan naar een andere node (VM\'s live, containers worden kort herstart).</li>' +
        '<li><strong>Bijwerken:</strong> automatisch via de app-sleutel (apt-get dist-upgrade), of stap voor stap zelf in de console. Eigen configuratiebestanden blijven staan.</li>' +
        '<li><strong>Herstarten:</strong> alleen als er een nieuwe kernel is.</li>' +
        '<li><strong>Terugzetten:</strong> alles wat verplaatst is, gaat terug naar de eigen node.</li></ol>' +
        '<p class="muted" style="margin:0 0 14px">Steeds één node tegelijk. <strong>Automatisch</strong>: PVE Fleet doet alle nodes na elkaar en stopt bij de eerste fout. <strong>Stap voor stap</strong>: jij klikt elke stap zelf en werkt bij in de console. Nodes zonder werk worden overgeslagen. Je kunt deze pagina tussendoor sluiten.</p>' +
        '<div class="list">' + nodes.map(function (n) {
          return '<div class="row"><span class="mono">' + e(n.name) + '</span><span>' + (needsWork(n) ? (n.count ? '<span class="c-warn">' + n.count + ' updates</span> ' : '') + (n.reboot_needed ? '<span class="c-info">herstart nodig</span>' : '') : '<span class="c-ok">up-to-date</span>') +
            ' <span class="muted small">· ' + running(n).length + ' VM/CT actief</span></span></div>';
        }).join('') + '</div>' +
        '<div class="stepacts" style="margin-top:16px"><button type="button" class="btn primary lg" data-do="auto"' + (d.kind === 'cluster' && d.quorate !== false ? '' : ' disabled') + '>Alles automatisch bijwerken…</button>' +
        '<button type="button" class="btn ghost lg" data-do="start"' + (d.kind === 'cluster' && d.quorate !== false ? '' : ' disabled') + '>Stap voor stap</button>' +
        (todo.length ? '<span class="small muted">' + todo.length + ' van ' + nodes.length + ' nodes hebben werk.</span>' : '<span class="small c-ok">Alle nodes zijn al bijgewerkt.</span>') + '</div></section>';
      return;
    }
    $('headacts').innerHTML = '<button type="button" class="btn ghost" data-do="auto" style="margin-right:8px">Automatisch verder…</button><button type="button" class="btn ghost" data-do="reset">Rolling update afsluiten</button>';
    var activeSet = false, skipNow = null;
    var allDone = true;
    $('nodes').innerHTML = nodes.map(function (n, i) {
      var st = steps(n), ns = nodeState(n, st);
      if (ns !== 'done') allDone = false;
      var active = !activeSet && ns !== 'done';
      if (active) activeSet = true;
      // stap voor stap: een node zonder werk vanzelf overslaan (alleen als de updatestatus bekend is)
      if (active && idle(n) && n.checked && !n.error && !job && !autoSkipped[n.name]) skipNow = n.name;
      var badge = ns === 'done' ? '<span class="pill c-ok" style="border-color:currentColor">✓ Klaar</span>'
        : (active ? '<span class="pill c-info" style="border-color:currentColor">Nu aan de beurt</span>' : (needsWork(n) ? '<span class="pill c-warn" style="border-color:currentColor">Te doen</span>' : '<span class="pill">Up-to-date</span>'));
      var head = '<div class="card-head"><span class="mono muted">' + (i + 1) + '</span><h2 class="mono">' + e(n.name) + '</h2>' + badge +
        '<span class="small muted">' + (n.version ? 'PVE ' + e(n.version) + ' · ' : '') + running(n).length + ' VM/CT actief</span>' +
        '<div class="end">' + (ns === 'done' ? '<button type="button" class="btn ghost" data-do="undone" data-n="' + e(n.name) + '">Opnieuw openen</button>' : '') + '</div></div>';
      if (!active && ns === 'done') return '<section class="card" style="opacity:.75">' + head + '</section>';
      if (!active) return '<section class="card">' + head + '</section>';
      if (idle(n)) return '<section class="card" style="border-color:var(--info-line)">' + head + '<div class="card-foot"><span class="c-ok">Deze node is al bijgewerkt; hier is niets te doen.</span>' +
        '<button type="button" class="btn go lg" data-do="done" data-n="' + e(n.name) + '">Overslaan, door naar de volgende</button></div></section>';
      return '<section class="card" style="border-color:var(--info-line)">' + head + '<div class="steps">' + st.map(function (x, j) {
        var mark = x.state === 'done' ? '<span class="sdot ok">✓</span>' : (x.state === 'busy' ? '<span class="sdot busy"><span class="spinner" aria-hidden="true"></span></span>' : '<span class="sdot ' + (x.state === 'todo' ? 'todo' : '') + '">' + (j + 1) + '</span>');
        return '<div class="step ' + x.state + '">' + mark + '<div class="sbody"><div class="stitle">' + e(x.title) + '</div><div class="stext">' + x.html + '</div></div></div>';
      }).join('') + '</div>' +
        '<div class="card-foot">' + (ns === 'ready' ? '<button type="button" class="btn go lg" data-do="done" data-n="' + e(n.name) + '">' + e(n.name) + ' is klaar, door naar de volgende</button>'
          : '<span class="small muted">Rond de stappen hierboven af. Je kunt een stap ook in Proxmox zelf doen; deze pagina ziet dat vanzelf.</span>') +
        '</div></section>';
    }).join('');
    $('nodes').insertAdjacentHTML('afterbegin', autoBanner());
    if (skipNow) {
      autoSkipped[skipNow] = 1;
      save({ op: 'done', node: skipNow }).then(function () {
        PFA.toast(skipNow + ' is al bijgewerkt en is overgeslagen', false);
        return load();
      }, function () { /* knop blijft staan */ });
    }
    if (allDone) {
      $('nodes').insertAdjacentHTML('afterbegin', '<div class="banner info"><div class="txt"><div class="ttl">Rolling update klaar</div><div class="desc">Alle nodes zijn bijgewerkt. Sluit de rolling update af om opnieuw te beginnen bij de volgende updates.</div></div>' +
        '<button type="button" class="btn primary" data-do="reset">Afsluiten</button></div>');
    }
  }

  // ------------------------------------------------------------ automatisch
  var STEP = { move: 'Verplaatsen', upgrade: 'Bijwerken', reboot: 'Herstarten', restart: 'Weer starten', back: 'Terugzetten' };
  function selButtons(attr) {
    return '<div style="margin:0 0 6px;display:flex;gap:8px"><button type="button" class="btn ghost" style="padding:3px 10px;font-size:12px" data-selall="' + attr + '" data-val="1">Alles aanvinken</button>' +
      '<button type="button" class="btn ghost" style="padding:3px 10px;font-size:12px" data-selall="' + attr + '" data-val="0">Alles uitvinken</button></div>';
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-selall]') : null;
    if (!b) return;
    var on = b.getAttribute('data-val') === '1';
    Array.prototype.forEach.call(document.querySelectorAll('.modal [' + b.getAttribute('data-selall') + ']'), function (c) { c.checked = on; });
  });
  function autoRunning() { return !!(auto && auto.run && auto.run.status === 'running'); }
  function hms(t) { var d = new Date(t * 1000); return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2) + ':' + ('0' + d.getSeconds()).slice(-2); }
  function loadAuto() {
    return PF.getJSON(AUTO).then(function (r) {
      var was = autoRunning();
      var sig = JSON.stringify(r);
      if (sig === autoSig) return;
      autoSig = sig;
      auto = r;
      if (was && !autoRunning()) return load();     // net klaar: het plan opnieuw ophalen
      if (data) render(data);
    }, function () { /* volgende keer */ });
  }
  function logLine(l) {
    var apt = l.msg.charAt(0) === '│';    // uitvoer van apt: niet vertalen, wel als code
    return '<div style="white-space:pre-wrap"><span class="mono muted">' + e(hms(l.t)) + '</span>  <span' + (apt ? ' class="mono"' : '') + '>' + e(l.msg) + '</span></div>';
  }
  function nodeLine(n, r) {
    var icon = n.state === 'busy' ? '<span class="spinner" aria-hidden="true"></span>' : (n.state === 'ok' ? '<span class="c-ok">✓</span>'
      : (n.state === 'skip' ? '<span class="c-ok">–</span>' : (n.state === 'fail' ? '<span class="c-crit">✗</span>' : (n.state === 'stopped' ? '<span class="c-warn">■</span>' : '<span class="muted">•</span>'))));
    var txt = n.state === 'busy' ? '<span class="c-info">' + e(STEP[n.step] || 'Bezig') + '…</span>'
      : (n.state === 'ok' ? '<span class="c-ok">klaar</span>' : (n.state === 'skip' ? '<span class="muted">al bijgewerkt, overgeslagen</span>'
      : (n.state === 'fail' ? '<span class="c-crit">mislukt</span> <span class="small">' + e(n.msg || '') + '</span>'
      : (n.state === 'stopped' ? '<span class="c-warn">gestopt</span>' : '<span class="muted">wacht</span>'))));
    var lines = r.log.filter(function (l) { return l.node === n.name; });
    var open = logOpen[n.name] !== undefined ? logOpen[n.name] : (n.state === 'busy' || n.state === 'fail');
    return '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;padding:9px 0;border-bottom:1px solid var(--line)"><span style="width:18px;text-align:center">' + icon + '</span><span class="mono" style="min-width:90px">' + e(n.name) + '</span><span>' + txt + '</span>' +
      (lines.length ? '<details data-log="' + e(n.name) + '" style="flex-basis:100%"' + (open ? ' open' : '') + '><summary class="small muted" style="cursor:pointer">Logboek (<span>' + lines.length + '</span> regels)</summary>' +
        '<div class="small" data-logbox="' + e(n.name) + '" style="max-height:260px;overflow:auto;margin:6px 0 0;padding:8px 10px;background:rgba(0,0,0,.22);border-radius:6px;line-height:1.5">' +
        lines.map(logLine).join('') + '</div></details>' : '') + '</div>';
  }
  function autoNodes(r, withGeneral) {
    var gen = r.log.filter(function (l) { return !l.node; });
    return '<div>' + r.nodes.map(function (n) { return nodeLine(n, r); }).join('') + '</div>' +
      (withGeneral && gen.length ? '<div class="small muted" style="margin-top:10px">' + gen.map(logLine).join('') + '</div>' : '');
  }
  function keepScroll(fn) {
    var at = {};
    Array.prototype.forEach.call(document.querySelectorAll('[data-logbox]'), function (b) {
      at[b.getAttribute('data-logbox')] = b.scrollHeight - b.scrollTop - b.clientHeight < 30 ? -1 : b.scrollTop;
    });
    fn();
    Array.prototype.forEach.call(document.querySelectorAll('[data-logbox]'), function (b) {
      var k = b.getAttribute('data-logbox');
      b.scrollTop = (at[k] === undefined || at[k] === -1) ? b.scrollHeight : at[k];
    });
  }
  function renderAuto() {
    var r = auto.run;
    var cur = r.nodes.filter(function (n) { return n.state === 'busy'; })[0];
    $('headacts').replaceChildren();
    keepScroll(function () {
      $('nodes').innerHTML = '<section class="card" style="border-color:var(--info-line)"><div class="card-head"><span class="spinner" aria-hidden="true"></span><h2>Automatische rolling update loopt</h2>' +
        '<span class="small muted">' + (cur ? '<span class="mono">' + e(cur.name) + '</span> · <span>' + e(STEP[cur.step] || 'Voorbereiden') + '</span>' : 'Voorbereiden') + '</span>' +
        '<div class="end">' + (r.stop ? '<span class="small c-warn">Stopt na deze stap…</span>' : '<button type="button" class="btn danger" data-do="autostop">Stoppen</button>') + '</div></div>' +
        '<div style="padding:6px 18px 16px">' + autoNodes(r, true) +
        '<p class="small muted" style="margin:12px 0 0">Je kunt deze pagina sluiten; PVE Fleet gaat door. Bij een fout stopt PVE Fleet bij die node en gaat niet verder.</p></div></section>';
    });
  }
  function autoBanner() {
    var r = auto && auto.run;
    if (!r || r.status === 'running') return '';
    var cls = r.status === 'done' ? 'info' : (r.status === 'stopped' ? 'warn' : 'crit');
    var ttl = r.status === 'done' ? 'Automatische rolling update klaar' : (r.status === 'stopped' ? 'Automatische rolling update gestopt' : (r.status === 'interrupted' ? 'Automatische rolling update onderbroken' : 'Automatische rolling update gestopt door een fout'));
    var desc = r.status === 'done' ? 'Alle nodes zijn na elkaar bijgewerkt.'
      : '<span>' + e(r.error || 'Gestopt op verzoek.') + '</span><br>' + (data && data.started ? 'Hieronder kun je stap voor stap verder; wat er verplaatst is, staat erbij.' : 'Er is niets verplaatst of bijgewerkt.');
    return '<div class="banner ' + cls + '" style="margin-bottom:12px"><div class="txt" style="flex:1"><div class="ttl">' + e(ttl) + '</div><div class="desc">' + desc + '</div>' +
      '<details data-banner="1" style="margin-top:8px"' + (bannerOpen ? ' open' : '') + '><summary class="small" style="cursor:pointer">Wat er gebeurd is</summary><div style="margin-top:8px">' + autoNodes(r, true) + '</div></details></div>' +
      '<button type="button" class="btn ghost" data-do="autoclear">Melding sluiten</button></div>';
  }
  function autoStart() {
    if (auto && !auto.enabled && !auto.demo) {
      return PFA.modal({ title: 'Eerst de app-sleutel instellen',
        body: '<p style="margin:0 0 8px">PVE Fleet werkt de nodes bij via SSH met de app-sleutel. Die staat nog niet aan.</p>' +
          '<p class="small muted" style="margin:0">Ga naar <a href="/apps">Apps</a> › Instellen, plak het commando op één node en druk op Inschakelen en testen. Daarna kun je hier starten.</p>',
        confirm: 'Naar Apps', kind: 'primary' }).then(function (v) { if (v) location.href = '/apps'; });
    }
    var order = (data.order || []).concat(data.nodes.map(function (n) { return n.name; }).filter(function (x) { return (data.order || []).indexOf(x) < 0; }));
    var nodes = order.map(function (name) { return data.nodes.filter(function (n) { return n.name === name; })[0]; }).filter(Boolean);
    var off = nodes.filter(function (n) { return !online(n); });
    if (off.length) return PFA.toast('Offline: ' + off.map(function (n) { return n.name; }).join(', ') + '. Eerst alle nodes online.', true);
    var stay = [];
    return PFA.modal({ title: 'Alle nodes automatisch bijwerken', wide: true,
      body: '<p style="margin:0 0 8px">PVE Fleet doet de nodes na elkaar, in deze volgorde: <span class="mono">' + e(order.join(' → ')) + '</span>. Per node: verplaatsen, bijwerken (apt-get dist-upgrade), herstarten als er een nieuwe kernel is, weer starten wat bleef staan, terugzetten. Nodes zonder werk worden overgeslagen.</p>' +
        '<p class="small muted" style="margin:0 0 8px">Aangevinkt: gaat tijdelijk naar een andere node (VM\'s live, containers kort uit). Uitgevinkt: blijft staan, gaat bij de herstart even uit en wordt daarna weer gestart.</p>' +
        selButtons('data-am') + '<div style="max-height:300px;overflow:auto;padding:2px;margin:0 0 8px">' + nodes.map(function (n) {
          var run = running(n), st = stays(n);
          return '<div style="margin:0 0 8px"><div class="small" style="margin:0 0 4px"><strong class="mono">' + e(n.name) + '</strong> <span class="muted">· ' + (needsWork(n) ? (n.count ? '<span>' + n.count + ' updates</span>' : '<span>herstart nodig</span>') : '<span>up-to-date</span>') + '</span></div>' +
            (run.length ? run.map(function (g) {
              return '<label class="check" style="margin:0 0 3px 12px"><input type="checkbox" data-am="' + e(stayKey(g)) + '"' + (st[stayKey(g)] ? '' : ' checked') + '><span class="mono">' + e(gname(g)) + '</span> <span class="small muted">' + (g.type === 'qemu' ? 'VM' : 'container') + '</span></label>';
            }).join('') : '<div class="small muted" style="margin-left:12px">Niets actief.</div>') + '</div>';
        }).join('') + '</div>' +
        '<p class="small muted" style="margin:0">Bij een fout (verplaatsen mislukt, apt-fout, node komt niet terug) stopt PVE Fleet bij die node en gaat niet verder. Je kunt altijd op Stoppen drukken. ' +
        'Heb je PVE Fleet net bijgewerkt? Plak dan eerst het installatiecommando op de pagina Apps (Instellen) opnieuw; hiervoor is een nieuwere versie van het script op de nodes nodig.</p>',
      confirm: 'Starten', kind: 'primary',
      submit: function () {
        stay = Array.prototype.filter.call(document.querySelectorAll('.modal [data-am]'), function (c) { return !c.checked; })
          .map(function (c) { return c.getAttribute('data-am'); });
        return PF.postJSON(AUTO, { order: order, stay: stay });
      } }).then(function (v) { if (v) return loadAuto(); });
  }

  // ------------------------------------------------------------ migraties na elkaar
  function waitTask(srv, node, upid) {
    return new Promise(function (resolve) {
      function tick() {
        PF.getJSON('/api/task/' + encodeURIComponent(srv) + '/' + encodeURIComponent(node) + '/' + encodeURIComponent(upid)).then(function (r) {
          if (r.running) setTimeout(tick, 2000); else resolve(r);
        }, function () { setTimeout(tick, 4000); });
      }
      tick();
    });
  }
  function runJob(kind, node, items, onEach) {
    job = { kind: kind, node: node, items: items };
    render(data);
    var i = 0;
    function next() {
      if (i >= items.length) {
        var fails = items.filter(function (x) { return x.state === 'fail'; }).length;
        PFA.toast((kind === 'move' ? 'Verplaatsen klaar' : (kind === 'start' ? 'Starten klaar' : 'Terugzetten klaar')) + (fails ? ', ' + fails + ' mislukt' : ''), fails > 0);
        job = null;
        return PF.postJSON('/api/refresh/' + encodeURIComponent(server)).then(load, load);
      }
      var it = items[i++];
      it.state = 'run'; render(data);
      var body = it.action === 'start' ? { action: 'start', server: server, node: it.from, type: it.g.type, vmid: it.g.vmid }
        : { action: 'migrate', server: server, node: it.from, type: it.g.type, vmid: it.g.vmid,
          params: { target: it.to, online: true, with_local_disks: true } };
      return Promise.resolve(onEach ? onEach(it) : null).then(function () {
        return PF.postJSON('/api/action', body);
      }).then(function (res) {
        it.msg = 'bezig…'; render(data);
        if (!res.upid) return { ok: true };
        return waitTask(server, it.from, res.upid);
      }).then(function (r) {
        it.state = r.ok ? 'ok' : 'fail'; it.msg = r.ok ? '' : (r.exitstatus || 'mislukt');
        if (!r.ok && it.onFail) it.onFail();
        render(data); return next();
      }, function (err) {
        it.state = 'fail'; it.msg = err.message;
        if (it.onFail) it.onFail();
        render(data); return next();
      });
    }
    return next();
  }

  function move(n) {
    var run = toMove(n), targets = others(n).sort(function (a, b) { return (a.mem_pct || 0) - (b.mem_pct || 0); });
    if (!targets.length) return PFA.toast('Er is geen andere node online', true);
    var need = run.reduce(function (s, g) { return s + (g.maxmem || 0); }, 0);
    var keepHere = [];
    var opts = targets.map(function (t) { return { value: t.name, label: t.name, meta: 'RAM ' + PF.pct(t.mem_pct) + ' · ' + running(t).length + ' VM/CT actief' }; });
    opts.unshift({ value: '*', label: 'Verdelen', meta: 'over de andere nodes, steeds naar de node met het meeste vrije RAM' });
    return PFA.modal({ title: 'VM\'s & containers van ' + n.name + ' verplaatsen', wide: true,
      body: '<p style="margin:0 0 6px">Deze ' + run.length + ' VM\'s &amp; containers draaien op ' + e(n.name) + ' (samen tot ' + PF.bytes(need, 0) + ' RAM). Vink uit wat hier mag blijven:</p>' +
        selButtons('data-mv') + '<div style="margin:0 0 6px;max-height:190px;overflow:auto;padding:2px">' + run.map(function (g) {
          return '<label class="check" style="margin:0 0 4px"><input type="checkbox" data-mv="' + e(stayKey(g)) + '" checked><span class="mono">' + e(gname(g)) + '</span> <span class="small muted">' + (g.type === 'qemu' ? 'VM · blijft draaien tijdens verplaatsen' : 'container · kort uit tijdens verplaatsen') + '</span></label>';
        }).join('') + '</div>' +
        '<p class="small muted" style="margin:0">Aangevinkt: gaat één voor één naar een andere node en na de herstart terug. VM\'s blijven draaien (live migratie), containers worden kort gestopt en op de andere node weer gestart. ' +
        'Uitgevinkt: blijft hier, gaat bij de herstart even uit en wordt daarna door PVE Fleet weer gestart. Gestopte VM/CT\'s blijven staan.</p>',
      fields: [{ name: 'target', label: 'Naar', type: 'radio', value: '*', options: opts }],
      confirm: 'Doorgaan', kind: 'primary',
      submit: function () {
        keepHere = run.filter(function (g) { var c = document.querySelector('.modal [data-mv="' + stayKey(g) + '"]'); return c && !c.checked; });
        return Promise.resolve();
      } }).then(function (v) {
      if (!v) return;
      run = run.filter(function (g) { return keepHere.indexOf(g) < 0; });
      var stayList = (n.stay || []).concat(keepHere.map(function (g) { return { vmid: g.vmid, type: g.type, name: g.name }; }));
      var pre = keepHere.length ? save({ op: 'stay', node: n.name, stay: stayList }) : Promise.resolve();
      if (!run.length) return pre.then(load);
      // doel per VM/CT
      var load_ = {};
      targets.forEach(function (t) { load_[t.name] = t.mem_pct || 0; });
      var mem = {};
      targets.forEach(function (t) { mem[t.name] = 0; });
      var items = run.toSorted(function (a, b) { return (b.maxmem || 0) - (a.maxmem || 0); }).map(function (g) {
        var to = v.target;
        if (to === '*') {
          to = targets.toSorted(function (a, b) { return (load_[a.name] + mem[a.name]) - (load_[b.name] + mem[b.name]); })[0].name;
          var t = targets.filter(function (x) { return x.name === to; })[0];
          mem[to] += 100 * (g.maxmem || 0) / Math.max(1, (t && t.mem_total) || 64 * 1073741824);
        }
        return { g: g, from: n.name, to: to, state: 'wait' };
      });
      return pre.then(function () {
        return runJob('move', n.name, items, function (it) {
          return save({ op: 'moved', node: n.name, moved: [{ vmid: it.g.vmid, type: it.g.type, name: it.g.name, to: it.to }] });
        });
      });
    });
  }

  function restart(n) {
    var items = stayedDown(n).map(function (m) { return { g: { vmid: m.vmid, type: m.type, name: m.name || '' }, from: n.name, to: n.name, state: 'wait', action: 'start' }; });
    if (!items.length) return;
    return runJob('start', n.name, items).then(load);
  }

  function back(n) {
    var list = pendingBack(n);
    var items = list.map(function (m) { return { g: { vmid: m.vmid, type: m.type, name: m.name || '' }, from: m.now, to: n.name, state: 'wait' }; });
    return PFA.modal({ title: 'Terugzetten naar ' + n.name, body: list.length + ' VM\'s &amp; containers gaan één voor één terug naar ' + e(n.name) + '.',
      confirm: 'Terugzetten', kind: 'primary' }).then(function (v) {
      if (!v) return;
      return runJob('back', n.name, items).then(function () {
        var ok = items.filter(function (x) { return x.state === 'ok'; }).map(function (x) { return { vmid: x.g.vmid, type: x.g.type }; });
        if (ok.length) return save({ op: 'returned', node: n.name, moved: ok }).then(load);
      });
    });
  }

  function check(n) {
    checking[n.name] = 'Opnieuw kijken naar updates…';
    render(data);
    return PF.postJSON('/api/updates/' + encodeURIComponent(server) + '/' + encodeURIComponent(n.name) + '/check', {}).then(function (r) {
      delete checking[n.name];
      if (r.note) PFA.toast(r.note, false);
      return load();
    }, function (err) { delete checking[n.name]; PFA.toast(err.message, true); return load(); });
  }

  $('app').addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-do]') : null;
    if (!b || !data) return;
    var what = b.getAttribute('data-do'), name = b.getAttribute('data-n');
    var n = name ? data.nodes.filter(function (x) { return x.name === name; })[0] : null;
    if (job && ['move', 'back', 'reboot'].indexOf(what) >= 0) return PFA.toast('Wacht tot de lopende migraties klaar zijn', true);
    if (what === 'auto') { if (autoRunning()) return; return autoStart(); }
    if (what === 'autostop') return PFA.modal({ title: 'Automatische rolling update stoppen?', body: 'PVE Fleet maakt de lopende stap af (een migratie of herstart breekt PVE Fleet niet halverwege af) en stopt dan. Daarna kun je stap voor stap verder.', confirm: 'Stoppen', kind: 'danger' })
      .then(function (v) { if (v) return PF.postJSON(AUTO + '/stop').then(loadAuto, function (err) { PFA.toast(err.message, true); }); });
    if (what === 'autoclear') return PF.postJSON(AUTO + '/clear').then(loadAuto, function (err) { PFA.toast(err.message, true); });
    if (autoRunning()) return PFA.toast('De automatische rolling update loopt nog', true);
    if (what === 'start') save({ op: 'start', order: data.nodes.map(function (x) { return x.name; }) }).then(load, function (err) { PFA.toast(err.message, true); });
    else if (what === 'reset') PFA.modal({ title: 'Rolling update afsluiten?', body: 'PVE Fleet vergeet welke VM\'s &amp; containers er verplaatst zijn. Er wordt niets in Proxmox veranderd.', confirm: 'Afsluiten', kind: 'primary' })
      .then(function (v) { if (v) save({ op: 'reset' }).then(function () { location.href = '/updates'; }); });
    else if (what === 'move') move(n);
    else if (what === 'back') back(n);
    else if (what === 'keep') PFA.modal({ title: 'Laten staan?', body: 'De verplaatste VM\'s &amp; containers blijven op hun huidige node. PVE Fleet zet ze niet meer terug.', confirm: 'Laten staan', kind: 'primary' })
      .then(function (v) { if (v) save({ op: 'returned', node: n.name, kept: true, moved: pendingBack(n) }).then(load); });
    else if (what === 'upgrade') PF.openUpgrade(server, n.name, function () { check(n); });
    else if (what === 'check') check(n);
    else if (what === 'reboot') PFA.nodePower(server, n.name, 'node_reboot', { cluster: true, running: running(n).length }).then(function (r) {
      if (r !== undefined) { waitReboot[n.name] = Date.now(); render(data); }
    }, function () { /* melding is al getoond */ });
    else if (what === 'restart') restart(n);
    else if (what === 'skip') save({ op: 'skipmove', node: n.name, stay: running(n).map(function (g) { return { vmid: g.vmid, type: g.type, name: g.name }; }) }).then(load);
    else if (what === 'unskip') save({ op: 'unskipmove', node: n.name }).then(load);
    else if (what === 'done' || what === 'undone') save({ op: what, node: n.name }).then(load);
  });
  $('app').addEventListener('toggle', function (ev) {
    var d = ev.target;
    if (d && d.getAttribute && d.getAttribute('data-log')) logOpen[d.getAttribute('data-log')] = d.open;
    if (d && d.getAttribute && d.getAttribute('data-banner')) bannerOpen = d.open;
  }, true);
  window.addEventListener('pf:refresh', load);
  loadAuto();
  setInterval(function () { if (!document.hidden) loadAuto(); }, 3000);
  PF.poll(API, render, 5000);
})();
