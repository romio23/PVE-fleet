/* Apps: nieuwe versie uploaden, controleren, en meteen of later in een container zetten. */
(function () {
  var e = PF.esc;
  var root = document.getElementById('apps');
  var data = null;
  var chosen = {};          // app -> gekozen versie (blijft staan bij verversen)
  var TYPES = [{ value: 'systemd', label: 'Dienst (systemd), bijvoorbeeld Python/Flask' },
    { value: 'compose', label: 'Docker Compose' },
    { value: 'static', label: 'Websitebestanden (PHP/HTML)' }];
  var STATUS = { pending: 'Gepland', running: 'Bezig', ok: 'Gelukt', fail: 'Mislukt', rolledback: 'Teruggezet', cancelled: 'Geannuleerd' };

  function vparts(v) { return String(v || '').replace(/^[vV]/, '').split(/[.+_-]/); }
  function vcmp(a, b) {
    var x = vparts(a), y = vparts(b);
    for (var i = 0; i < Math.max(x.length, y.length); i++) {
      var p = x[i] === undefined ? '' : x[i], q = y[i] === undefined ? '' : y[i];
      var pn = /^\d+$/.test(p), qn = /^\d+$/.test(q);
      if (pn && qn && +p !== +q) return +p < +q ? -1 : 1;
      if (pn !== qn) return pn ? 1 : -1;
      if (!pn && p !== q) return p < q ? -1 : 1;
    }
    return 0;
  }
  function stamp(ts) {
    if (!ts) return '—';
    var d = new Date(ts * 1000), now = new Date();
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    var t = p(d.getHours()) + ':' + p(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return 'vandaag ' + t;
    var tm = new Date(now.getTime() + 86400000);
    if (d.toDateString() === tm.toDateString()) return 'morgen ' + t;
    var ys = new Date(now.getTime() - 86400000);
    if (d.toDateString() === ys.toDateString()) return 'gisteren ' + t;
    return p(d.getDate()) + '-' + p(d.getMonth() + 1) + '-' + d.getFullYear() + ' ' + t;
  }
  function badge(s) { return '<span class="st ' + e(s) + '">' + (s === 'running' ? '<span class="spinner" style="width:10px;height:10px;display:inline-block;vertical-align:-1px;margin-right:4px"></span>' : '') + e(STATUS[s] || s) + '</span>'; }
  function ctLabel(c) { return c ? c.name + ' (' + c.vmid + ')' : '—'; }
  function mb(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' kB'; }
  function nextNight() {
    var d = new Date(); d.setHours(3, 0, 0, 0);
    if (d.getTime() < Date.now() + 5 * 60000) d.setDate(d.getDate() + 1);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
  }
  function copyText(text, btn) {
    function done(ok) { btn.textContent = ok ? 'Gekopieerd ✓' : 'Selecteer en kopieer met Ctrl+C'; setTimeout(function () { btn.textContent = 'Kopiëren'; }, 2500); }
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); }); return; }
    var ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    var ok = false; try { ok = document.execCommand('copy'); } catch (x) { ok = false; }
    document.body.removeChild(ta); done(ok);
  }

  // ------------------------------------------------------------ tekenen
  function setupCard(d) {
    return '<section class="card pad"><h2 class="small-title">Eenmalig instellen</h2>' +
      '<p class="muted" style="margin:0 0 12px;line-height:1.55">PVE Fleet zet een nieuwe versie via SSH op de node in de container, met een eigen sleutel die alleen het deploy-script mag starten. Dat stel je één keer in met een commando op één node van het cluster.</p>' +
      '<button type="button" class="btn primary" data-a="setup">Instellen</button></section>';
  }
  var lastRepo = (function () { try { return localStorage.getItem('pvef_gh_repo') || ''; } catch (x) { return ''; } })();
  function uploadCard() {
    return '<section class="card pad"><h2 class="small-title">Nieuwe versie uploaden</h2>' +
      '<label class="drop" id="drop"><input type="file" id="file" accept=".zip,application/zip" hidden>' +
      '<div class="big">Sleep een zip hierheen of klik om te kiezen</div>' +
      '<div class="small muted">Bijvoorbeeld <span class="mono">modelarr-1.4.0.zip</span>. Staat er een <span class="mono">pvefleet-app.json</span> in, dan herkent PVE Fleet de app vanzelf.</div>' +
      '<div class="prog" id="prog"><div></div></div><div class="small muted" id="upl-st" style="margin-top:6px"></div></label>' +
      '<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center"><span class="small muted">Of haal de nieuwste versie op van GitHub:</span>' +
      '<input class="input mono" id="gh-repo" placeholder="eigenaar/repository, bijvoorbeeld romio23/modelarr" style="min-width:280px;flex:1" value="' + e(lastRepo) + '">' +
      '<button type="button" class="btn ghost" data-a="ghfetch" id="gh-go">Ophalen</button></div>' +
      '<div class="small muted" id="gh-st" style="margin-top:6px"></div></section>';
  }
  function ghNewer(a, rels) {
    if (!a.github_latest) return false;
    var have = rels[0] ? rels[0].manifest.version : (a.installed || '');
    return !have || vcmp(String(a.github_latest).replace(/^v/i, ''), String(have).replace(/^v/i, '')) > 0;
  }
  function appsCard(d) {
    var names = Object.keys(d.apps).sort(function (a, b) { return (d.apps[a].title || a).localeCompare(d.apps[b].title || b); });
    if (!names.length) return '<section class="card pad"><h2 class="small-title">Apps</h2><p class="muted" style="margin:0">Nog geen apps. Upload hierboven een eerste versie.</p></section>';
    var rows = names.map(function (n) {
      var a = d.apps[n];
      var rels = d.releases.filter(function (r) { return r.manifest.name === n; }).sort(function (x, y) { return vcmp(y.manifest.version, x.manifest.version) || y.uploaded - x.uploaded; });
      var sel = chosen[n] && rels.some(function (r) { return r.id === chosen[n]; }) ? chosen[n] : (rels[0] ? rels[0].id : '');
      var newer = rels.length && a.installed && vcmp(rels[0].manifest.version, a.installed) > 0;
      var opts = rels.map(function (r) {
        var v = r.manifest.version;
        var tag = a.installed ? (vcmp(v, a.installed) > 0 ? ' · nieuwer' : (vcmp(v, a.installed) === 0 ? ' · staat erop' : ' · ouder')) : '';
        return '<option value="' + e(r.id) + '"' + (r.id === sel ? ' selected' : '') + '>' + e(v + tag) + '</option>';
      }).join('');
      var ct = a.container;
      return '<tr><td><strong>' + e(a.title || n) + '</strong><div class="small muted mono">' + e(n) + '</div></td>' +
        '<td>' + (ct ? '<a href="' + PF.guestUrl(ct.server, ct.node, 'lxc', ct.vmid) + '">' + e(ctLabel(ct)) + '</a><div class="small muted">' + e(ct.group) + ' · ' + e(ct.node) + (ct.status !== 'running' ? ' · <span class="c-warn">' + e(ct.status) + '</span>' : '') + '</div>' : '<span class="muted">nog niet gekozen</span>') + '</td>' +
        '<td>' + (a.installed ? '<span class="mono">' + e(a.installed) + '</span>' + (newer ? ' <span class="c-warn small">update klaar</span>' : '') + '<div class="small muted">' + e(stamp(a.installed_at)) + '</div>' : '<span class="muted">—</span>') + '</td>' +
        '<td>' + (a.last_status ? badge(a.last_status) : '<span class="muted">—</span>') + '</td>' +
        '<td>' + (rels.length ? '<select class="input" data-ver="' + e(n) + '" style="min-width:150px;padding:6px 8px">' + opts + '</select>' : '<span class="muted small">geen versie bewaard</span>') + '</td>' +
        '<td class="r" style="white-space:nowrap">' + (a.github ? '<button type="button" class="btn ' + (ghNewer(a, rels) ? 'primary' : 'ghost') + '" data-a="ghfetch" data-repo="' + e(a.github) + '" title="Nieuwste release van ' + e(a.github) + ' ophalen">' + (a.github_latest ? 'GitHub ' + e(a.github_latest) + (ghNewer(a, rels) ? ' · nieuw' : '') + ' ophalen' : 'Van GitHub ophalen') + '</button> ' : '') + (rels.length ? '<button type="button" class="btn ' + (newer ? 'primary' : 'ghost') + '" data-a="deploy" data-n="' + e(n) + '"' + (d.enabled ? '' : ' disabled title="Stel eerst de app-sleutel in"') + '>Bijwerken…</button> ' : '') +
        '<button type="button" class="btn ghost" data-a="forget" data-n="' + e(n) + '" title="Uit de lijst halen (in de container verandert niets)">✕</button></td></tr>';
    }).join('');
    return '<section class="card"><div style="padding:16px 18px 6px"><h2 class="small-title" style="margin:0">Apps</h2></div><div class="tablewrap"><table class="tbl" style="min-width:820px"><thead><tr><th>App</th><th>Container</th><th>Geïnstalleerd</th><th>Laatste update</th><th>Versie</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<p class="small muted" style="margin:0;padding:10px 18px 14px">PVE Fleet bewaart de laatste 5 versies per app; een oudere versie kies je om terug te gaan.</p></section>';
  }
  function jobsCard(d) {
    if (!d.jobs.length) return '';
    var rows = d.jobs.slice(0, 25).map(function (j) {
      var acts = '<button type="button" class="btn ghost" data-a="log" data-j="' + e(j.id) + '">Log</button>';
      if (j.status === 'pending') acts = '<button type="button" class="btn ghost" data-a="now" data-j="' + e(j.id) + '">Nu starten</button> <button type="button" class="btn ghost" data-a="cancel" data-j="' + e(j.id) + '">Annuleren</button> ' + acts;
      return '<tr><td style="white-space:nowrap">' + e(stamp(j.status === 'pending' ? j.when : (j.started || j.when))) + '</td><td>' + e(j.title) + '</td><td class="mono">' + e(j.version) + '</td>' +
        '<td>' + e((j.guest || '') + ' (' + j.vmid + ')') + '</td><td>' + badge(j.status) + (j.error && j.status !== 'cancelled' ? '<div class="small muted" style="max-width:320px">' + e(j.error) + '</div>' : '') + '</td><td class="r" style="white-space:nowrap">' + acts + '</td></tr>';
    }).join('');
    return '<section class="card"><div style="padding:16px 18px 6px"><h2 class="small-title" style="margin:0">Gepland en geschiedenis</h2></div><div class="tablewrap"><table class="tbl" style="min-width:760px"><thead><tr><th>Wanneer</th><th>App</th><th>Versie</th><th>Container</th><th>Status</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' + rows + '</tbody></table></div></section>';
  }
  function footer(d) {
    if (!d.enabled) return '';
    return '<p class="small muted" style="margin:4px 2px">App-sleutel actief' + (d.tested ? ' (getest ' + e(stamp(d.tested)) + ')' : '') + ' · <a href="#" data-a="setup">Opnieuw instellen of uitzetten</a></p>';
  }

  var drawnTop = false;
  function render(d) {
    if (!drawnTop) {
      root.innerHTML = '<div style="display:flex;flex-direction:column;gap:16px"><div id="a-setup"></div><div id="a-upload">' + uploadCard() + '</div><div id="a-list"></div><div id="a-jobs"></div><div id="a-foot"></div></div>';
      bindDrop();
      drawnTop = true;
    }
    document.getElementById('a-setup').innerHTML = d.enabled ? '' : setupCard(d);
    document.getElementById('a-list').innerHTML = appsCard(d);
    document.getElementById('a-jobs').innerHTML = jobsCard(d);
    document.getElementById('a-foot').innerHTML = footer(d);
  }
  function load() {
    return PF.getJSON('/api/apps').then(function (d) { data = d; render(d); }, function (err) {
      if (!data) root.innerHTML = '<div class="card pad errbox">' + e(err.message) + '</div>';
    });
  }

  // ------------------------------------------------------------ instellen
  function setup() {
    PF.getJSON('/api/apps/setup').then(function (r) {
      var body = '<ol style="margin:0 0 10px;padding-left:20px;line-height:1.7">' +
        '<li>Open in Proxmox de shell van <strong>één node</strong> van het cluster (als root). Heb je losse nodes, doe het dan op elke losse node.</li>' +
        '<li>Plak dit commando. Het zet het deploy-script in <span class="mono">/etc/pve/pvefleet/</span> (gedeeld in het cluster) en voegt een tweede sleutel van PVE Fleet toe, beperkt tot dat script.</li></ol>' +
        '<pre class="log" style="max-height:200px;user-select:all" id="ap-cmd">' + e(r.command) + '</pre>' +
        '<button type="button" class="btn ghost" id="ap-copy" style="margin:6px 0 10px">Kopiëren</button>' +
        '<p class="small muted" style="margin:0 0 6px;line-height:1.5">Het eindigt met <span class="mono">PVE Fleet apps: klaar op &lt;node&gt;</span>. Klik daarna op <strong>Inschakelen en testen</strong>.</p>' +
        '<p class="small muted" style="margin:0 0 6px;line-height:1.5"><strong>Wat mag deze sleutel?</strong> Alleen het deploy-script starten: bestanden zetten in een map onder <span class="mono">/opt</span>, <span class="mono">/srv</span>, <span class="mono">/var/www</span> of <span class="mono">/home</span> van een container, en daar de app herstarten. Geen shell op de node.</p>' +
        '<div id="ap-res"></div>' +
        (data && data.enabled ? '<details style="margin-top:8px"><summary class="small" style="cursor:pointer">Uitzetten en sleutel verwijderen</summary><p class="small muted" style="margin:6px 0">Klik op <strong>Uitzetten</strong> en plak dit op een node:</p><pre class="log" style="user-select:all">' + e(r.remove) + '</pre></details>' : '');
      var extra = data && data.enabled ? [{ label: 'Uitzetten', onClick: function () {
        PF.postJSON('/api/apps/enable', { enabled: false }).then(function () { PFA.toast('Uitgezet'); load(); });
      } }] : [];
      PFA.modal({ title: 'Apps · eenmalig instellen', wide: true, body: body, extra: extra, confirm: 'Inschakelen en testen', kind: 'primary',
        onRender: function (el) { var b = el.querySelector('#ap-copy'); b.addEventListener('click', function () { copyText(r.command, b); }); },
        submit: function () {
          return PF.postJSON('/api/apps/enable', {}).then(function (res) {
            var html = '<div class="kv" style="margin-top:8px">' + res.results.map(function (x) {
              return '<div class="row"><span class="k">' + e(x.node) + '</span><span class="v ' + (x.ok ? 'c-ok' : (x.ok === false ? 'c-crit' : 'muted')) + '">' + e(x.msg) + '</span></div>';
            }).join('') + '</div>';
            load();
            if (!res.ok) { var el = document.getElementById('ap-res'); if (el) el.innerHTML = html; throw new Error('Niet elke node accepteert de sleutel; zie hierboven.'); }
            PFA.toast('Ingeschakeld op ' + res.results.filter(function (x) { return x.ok; }).length + ' node(s)');
          });
        } });
    }, function (err) { PFA.toast(err.message, true); });
  }

  // ------------------------------------------------------------ uploaden
  function bindDrop() {
    var drop = document.getElementById('drop'), file = document.getElementById('file');
    ['dragenter', 'dragover'].forEach(function (t) { drop.addEventListener(t, function (ev) { ev.preventDefault(); drop.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (t) { drop.addEventListener(t, function (ev) { ev.preventDefault(); drop.classList.remove('over'); }); });
    drop.addEventListener('drop', function (ev) { var f = ev.dataTransfer && ev.dataTransfer.files[0]; if (f) upload(f); });
    file.addEventListener('change', function () { var f = file.files && file.files[0]; if (f) upload(f); file.value = ''; });
  }
  function upload(f) {
    var st = document.getElementById('upl-st'), prog = document.getElementById('prog'), bar = prog.firstChild;
    if (!/\.zip$/i.test(f.name)) { st.innerHTML = '<span class="c-crit">Kies een .zip-bestand</span>'; return; }
    var xhr = new XMLHttpRequest();
    var fd = new FormData(); fd.append('file', f);
    prog.style.display = 'block'; bar.style.width = '0'; st.textContent = 'Uploaden: ' + f.name + ' (' + mb(f.size) + ')…';
    xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) bar.style.width = Math.round(ev.loaded / ev.total * 100) + '%'; };
    xhr.onload = function () {
      prog.style.display = 'none';
      var b = {}; try { b = JSON.parse(xhr.responseText); } catch (x) { b = {}; }
      if (xhr.status === 413) { st.innerHTML = '<span class="c-crit">Het bestand is te groot (maximaal 1 GB)</span>'; return; }
      if (xhr.status !== 200) { st.innerHTML = '<span class="c-crit">' + e(b.error || ('Upload mislukt (HTTP ' + xhr.status + ')')) + '</span>'; return; }
      st.textContent = '';
      describe(b);
    };
    xhr.onerror = function () { prog.style.display = 'none'; st.innerHTML = '<span class="c-crit">Upload mislukt: geen verbinding</span>'; };
    xhr.open('POST', '/api/apps/upload');
    xhr.setRequestHeader('X-PVEF', '1');
    xhr.send(fd);
  }

  function ghFetch(repo, btn) {
    repo = String(repo || '').trim();
    var st = document.getElementById('gh-st');
    if (!repo) { if (st) st.innerHTML = '<span class="c-crit">Vul eerst de repository in (eigenaar/naam)</span>'; return; }
    var old = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Ophalen…'; }
    if (st) st.textContent = 'Ophalen van ' + repo + '…';
    PF.postJSON('/api/apps/github', { repo: repo }).then(function (b) {
      if (btn) { btn.disabled = false; btn.textContent = old; }
      if (st) st.textContent = '';
      try { localStorage.setItem('pvef_gh_repo', repo); } catch (x) { /* optioneel */ }
      describe(b);
    }, function (err) {
      if (btn) { btn.disabled = false; btn.textContent = old; }
      if (st) st.innerHTML = '<span class="c-crit">' + e(err.message) + '</span>';
      PFA.toast(err.message, true);
    });
  }

  function describe(u) {
    var m = u.manifest || {};
    var h = m.health || {};
    var intro = u.has_manifest
      ? '<p class="muted" style="margin:0 0 4px">Gevonden in <span class="mono">pvefleet-app.json</span>. Controleer en klik op <strong>Opslaan</strong>.</p>'
      : (u.known ? '<p class="muted" style="margin:0 0 4px">Geen <span class="mono">pvefleet-app.json</span> in de zip; ingevuld zoals bij de vorige versie. Controleer vooral de versie.</p>'
        : '<p class="muted" style="margin:0 0 4px">Geen <span class="mono">pvefleet-app.json</span> in de zip. Vul één keer in hoe deze app in de container staat; PVE Fleet onthoudt het voor de volgende versie.</p>');
    if (u.github) intro += '<p class="small" style="margin:0 0 4px">Opgehaald van GitHub: <span class="mono">' + e(u.github.repo) + '</span> · ' + e(u.github.ref) + '</p>';
    intro += '<p class="small muted" style="margin:0">' + e(u.filename) + ' · ' + u.files + ' bestanden · ' + mb(u.size) + (u.folder ? ' · bovenste map <span class="mono">' + e(u.folder) + '/</span> wordt weggelaten' : '') + '</p>';
    PFA.modal({ title: 'Nieuwe versie controleren', wide: true, body: intro,
      fields: [
        { name: 'title', label: 'Naam van de app', value: m.title || '' },
        { name: 'name', label: 'Technische naam', value: m.name || '', mono: true, hint: 'kleine letters, bijvoorbeeld modelarr' },
        { name: 'version', label: 'Versie', value: m.version || '', mono: true },
        { name: 'type', label: 'Soort app', type: 'select', value: m.type || 'systemd', options: TYPES },
        { name: 'dir', label: 'Map in de container', value: m.dir || (m.name ? '/opt/' + m.name : ''), mono: true, hint: 'onder /opt, /srv, /var/www of /home' },
        { name: 'keep', label: 'Nooit overschrijven (één per regel)', type: 'textarea', value: (m.keep || ['data/', 'config.yml', '.env']).join('\n'), mono: true, hint: 'je gegevens en instellingen' },
        { name: 'service', label: 'Dienst om te herstarten', value: m.service || '', mono: true, hint: 'systemd: verplicht; websitebestanden: optioneel, bijvoorbeeld apache2' },
        { name: 'venv', label: 'Python-omgeving (optioneel)', value: m.venv || '', mono: true, hint: 'bijvoorbeeld venv: dan worden de pakketten uit requirements.txt bijgewerkt' },
        { name: 'owner', label: 'Eigenaar van de bestanden (optioneel)', value: m.owner || '', mono: true, hint: 'bijvoorbeeld www-data:www-data' },
        { name: 'post_install', label: 'Script na het neerzetten (optioneel)', value: m.post_install || '', mono: true, hint: 'pad in de app, bijvoorbeeld scripts/na-update.sh' },
        { name: 'health_port', label: 'Controle: poort (optioneel)', value: h.port ? String(h.port) : '', mono: true, hint: 'reageert de app daarna niet, dan gaat de container terug naar de snapshot' },
        { name: 'health_path', label: 'Controle: pad', value: h.path || '/', mono: true }
      ],
      confirm: 'Opslaan', kind: 'primary',
      submit: function (v) {
        var man = { name: v.name, title: v.title, version: v.version, type: v.type, dir: v.dir, keep: v.keep.split(/\n/), service: v.service,
          venv: v.venv, owner: v.owner, post_install: v.post_install };
        if (m.requirements) man.requirements = m.requirements;
        if (m.description) man.description = m.description;
        if (m.github) man.github = m.github;
        if (v.health_port) man.health = { port: v.health_port, path: v.health_path || '/', scheme: h.scheme || 'http', timeout: h.timeout || 90 };
        return PF.postJSON('/api/apps/upload/' + encodeURIComponent(u.upload) + '/save', { manifest: man }).then(function (r) {
          chosen[man.name.toLowerCase()] = r.release;
          return load().then(function () { setTimeout(function () { deploy(man.name.toLowerCase(), r.release); }, 50); });
        });
      } });
  }

  // ------------------------------------------------------------ bijwerken
  function deploy(name, rid) {
    var rel = null;
    data.releases.forEach(function (r) { if (r.id === rid) rel = r; });
    if (!rel) return;
    var m = rel.manifest, a = data.apps[name] || {};
    var cts = data.containers;
    var cur = a.container ? a.container.server + '/' + a.container.vmid : '';
    if (!cur) {
      var guess = cts.filter(function (c) { return c.name.toLowerCase().indexOf(name) >= 0 || name.indexOf(c.name.toLowerCase()) >= 0; })[0];
      if (guess) cur = guess.server + '/' + guess.vmid;
    }
    var opts = [{ value: '', label: 'Kies een container…' }].concat(cts.map(function (c) {
      return { value: c.server + '/' + c.vmid, label: c.group + ' · ' + c.vmid + ' ' + c.name + ' (' + c.node + ')' + (c.status !== 'running' ? ' · ' + c.status : '') };
    }));
    var body = '<div class="kv" style="margin-bottom:6px">' +
      '<div class="row"><span class="k">App</span><span class="v"><strong>' + e(m.title) + '</strong> <span class="mono">' + e(m.version) + '</span></span></div>' +
      '<div class="row"><span class="k">Map</span><span class="v mono">' + e(m.dir) + '</span></div>' +
      '<div class="row"><span class="k">Bewaard</span><span class="v mono small">' + e(m.keep.join(', ') || '—') + '</span></div>' +
      '<div class="row"><span class="k">Daarna</span><span class="v small">' + e(m.type === 'systemd' ? 'dienst ' + m.service + ' herstarten' + (m.venv ? ', Python-pakketten bijwerken' : '') : (m.type === 'compose' ? 'docker compose up -d --build' : (m.service ? m.service + ' herladen' : 'niets (websitebestanden)'))) +
        (m.post_install ? ', eerst ' + e(m.post_install) : '') + '</span></div>' +
      '<div class="row"><span class="k">Nu in de container</span><span class="v" id="dp-inst"><span class="muted">kies een container</span></span></div></div>';
    PFA.modal({ title: 'Bijwerken: ' + m.title + ' ' + m.version, wide: true, body: body,
      fields: [
        { name: 'ct', label: 'Container', type: 'select', value: cur, options: opts },
        { name: 'when', label: 'Wanneer', type: 'radio', value: 'now', options: [{ value: 'now', label: 'Nu' }, { value: 'later', label: 'Op een tijdstip' }] },
        { name: 'at', label: 'Tijdstip (als je "Op een tijdstip" kiest)', type: 'datetime-local', value: nextNight() },
        { name: 'snapshot', label: 'Eerst een snapshot van de container maken', type: 'checkbox', value: true, hint: 'PVE Fleet bewaart de laatste 2 van deze snapshots per container' },
        { name: 'rollback', label: 'Automatisch terug naar de snapshot als het misgaat', type: 'checkbox', value: true }
      ],
      confirm: 'Bijwerken', kind: 'primary',
      onRender: function (el) {
        var sel = el.querySelector('[name="ct"]');
        function info() {
          var out = document.getElementById('dp-inst');
          if (!sel.value) { out.innerHTML = '<span class="muted">kies een container</span>'; return; }
          var p = sel.value.split('/');
          out.innerHTML = '<span class="spinner" style="display:inline-block;width:12px;height:12px;vertical-align:-2px"></span> <span class="muted">kijken…</span>';
          PF.getJSON('/api/apps/installed?server=' + encodeURIComponent(p[0]) + '&vmid=' + encodeURIComponent(p[1]) + '&dir=' + encodeURIComponent(m.dir)).then(function (r) {
            if (sel.value !== p.join('/')) return;
            var im = r.manifest;
            if (r.status !== 'running') out.innerHTML = '<span class="c-warn">container draait niet (' + e(r.status || '?') + ')</span>';
            else if (!im) out.innerHTML = '<span class="muted">geen pvefleet-app.json in ' + e(m.dir) + ' (eerste keer via PVE Fleet)</span>';
            else if (im.name && im.name !== m.name) out.innerHTML = '<span class="c-crit">hier staat een andere app: ' + e(im.title || im.name) + ' ' + e(im.version || '') + '</span>';
            else {
              var c = vcmp(m.version, im.version);
              out.innerHTML = '<span class="mono">' + e(im.version) + '</span> → <span class="mono">' + e(m.version) + '</span> ' + (c > 0 ? '<span class="c-ok small">nieuwer</span>' : (c === 0 ? '<span class="small muted">dezelfde versie (opnieuw neerzetten)</span>' : '<span class="c-warn small">ouder: terug naar een vorige versie</span>'));
            }
          }, function (err) { if (sel.value === p.join('/')) out.innerHTML = '<span class="c-crit">' + e(err.message) + '</span>'; });
        }
        sel.addEventListener('change', info);
        info();
      },
      validate: function (v) {
        if (!v.ct) return 'Kies een container';
        if (v.when === 'later' && !v.at) return 'Kies een tijdstip';
        if (v.when === 'later' && new Date(v.at).getTime() < Date.now()) return 'Dat tijdstip is al voorbij';
        return null;
      },
      submit: function (v) {
        var p = v.ct.split('/');
        var body2 = { release: rid, server: p[0], vmid: +p[1], snapshot: v.snapshot, rollback: v.rollback && v.snapshot };
        if (v.when === 'later') body2.when = Math.floor(new Date(v.at).getTime() / 1000);
        return PF.postJSON('/api/apps/jobs', body2).then(function (r) {
          load();
          if (body2.when) PFA.toast('Ingepland voor ' + stamp(body2.when));
          else setTimeout(function () { follow(r.job.id); }, 50);
        });
      } });
  }

  function follow(jid) {
    var timer = null;
    PFA.modal({ title: 'App bijwerken', wide: true, body: '<pre class="log" id="aj-log" style="max-height:55vh">Bezig…</pre><div id="aj-st" class="small" style="margin-top:8px"></div>', confirm: null, cancelLabel: 'Sluiten',
      onRender: function () {
        function tick() {
          PF.getJSON('/api/apps/jobs/' + encodeURIComponent(jid)).then(function (j) {
            var el = document.getElementById('aj-log'); if (!el) { clearInterval(timer); return; }
            var atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
            el.textContent = (j.log || []).join('\n') || 'Wachten om te starten…';
            if (atEnd) el.scrollTop = el.scrollHeight;
            var st = document.getElementById('aj-st');
            if (j.status === 'running') st.innerHTML = '<span class="spinner" style="display:inline-block;vertical-align:-3px"></span> Bezig. Je mag dit venster sluiten; het bijwerken gaat door.';
            else if (j.status === 'pending') st.innerHTML = 'Gepland voor ' + e(stamp(j.when)) + '.';
            else { clearInterval(timer); st.innerHTML = j.status === 'ok' ? '<span class="c-ok">Gelukt.</span>' : (j.status === 'rolledback' ? '<span class="c-warn">Mislukt en teruggezet naar de snapshot: ' + e(j.error || '') + '</span>' : (j.status === 'cancelled' ? 'Geannuleerd.' : '<span class="c-crit">Mislukt: ' + e(j.error || '') + '</span>')); load(); }
          }, function () {});
        }
        tick(); timer = setInterval(tick, 1500);
      } }).then(function () { clearInterval(timer); load(); });
  }

  // ------------------------------------------------------------ klikken
  root.addEventListener('change', function (ev) {
    var s = ev.target.closest ? ev.target.closest('[data-ver]') : null;
    if (s) chosen[s.getAttribute('data-ver')] = s.value;
  });
  root.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-a]') : null;
    if (!b || !data) return;
    var a = b.getAttribute('data-a');
    if (b.tagName === 'A') ev.preventDefault();
    if (a === 'setup') return setup();
    if (a === 'ghfetch') return ghFetch(b.getAttribute('data-repo') || (document.getElementById('gh-repo') || {}).value || '', b);
    if (a === 'deploy') {
      var n = b.getAttribute('data-n');
      var s = root.querySelector('[data-ver="' + n + '"]');
      return deploy(n, s ? s.value : null);
    }
    if (a === 'log') return follow(b.getAttribute('data-j'));
    if (a === 'cancel') return PF.postJSON('/api/apps/jobs/' + encodeURIComponent(b.getAttribute('data-j')) + '/cancel').then(function () { PFA.toast('Geannuleerd'); load(); }, function (err) { PFA.toast(err.message, true); });
    if (a === 'now') return PF.postJSON('/api/apps/jobs/' + encodeURIComponent(b.getAttribute('data-j')) + '/now').then(function () { load(); follow(b.getAttribute('data-j')); }, function (err) { PFA.toast(err.message, true); });
    if (a === 'forget') {
      var nm = b.getAttribute('data-n');
      return PFA.modal({ title: 'App uit de lijst halen?', body: '<p style="margin:0;line-height:1.55"><strong>' + e((data.apps[nm] || {}).title || nm) + '</strong> en de bewaarde versies verdwijnen uit PVE Fleet. In de container verandert niets.</p>', confirm: 'Uit de lijst halen', kind: 'danger' }).then(function (ok) {
        if (!ok) return;
        PF.send('DELETE', '/api/apps/' + encodeURIComponent(nm)).then(function () { load(); }, function (err) { PFA.toast(err.message, true); });
      });
    }
  });

  load();
  setInterval(function () { if (!window.PFA_MODAL_OPEN) load(); }, 5000);
})();
