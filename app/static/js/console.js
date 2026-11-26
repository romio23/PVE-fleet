/* Ingebouwde console: terminal (container / node-shell) via xterm.js, VM-scherm via noVNC. */
(function () {
  var app = document.getElementById('app');
  var server = app.getAttribute('data-server'), node = app.getAttribute('data-node');
  var kind = app.getAttribute('data-kind'), vmid = app.getAttribute('data-vmid');
  var $ = function (id) { return document.getElementById(id); };
  var term = null, fit = null, ws = null, rfb = null, ping = null, closedByUs = false;
  var enc = new TextEncoder();
  var fontSize = 14;
  try { fontSize = parseInt(localStorage.getItem('pvef_console_font') || '14', 10) || 14; } catch (x) { /* niets */ }

  function status(cls, text) {
    $('stat').innerHTML = '<span class="dot b-' + cls + '" aria-hidden="true"></span><span>' + PF.esc(text) + '</span>';
  }
  function overlay(title, desc) {
    $('over-t').textContent = title;
    $('over-d').textContent = desc || '';
    $('over').classList.remove('hidden');
  }
  function hideOverlay() { $('over').classList.add('hidden'); }
  function wsUrl(id) { return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/console/' + encodeURIComponent(id); }

  // ---------------------------------------------------------------- root-sessie (alleen node-shell)
  var rootApi = '/api/console/root/' + encodeURIComponent(server);
  var skipKey = 'pvef_noroot_' + server;
  function skipped() { try { return sessionStorage.getItem(skipKey) === '1'; } catch (x) { return false; } }
  function rootStat(s) {
    var el = $('rootstat'); if (!el) return;
    el.innerHTML = s ? '<span class="pill c-ok" style="border-color:currentColor;padding:2px 10px" title="Root-sessie tot ' + PF.clock(s.expires) + '">root-sessie</span> <button type="button" class="btn ghost" id="root-end" style="height:28px;padding:0 10px;font-size:12px">Beëindigen</button>'
      : '<button type="button" class="btn ghost" id="root-login" style="height:28px;padding:0 10px;font-size:12px">Als root inloggen</button>';
  }
  function askRoot(name) {
    $('root-name').textContent = name || server;
    $('rootbox').classList.remove('hidden');
    status('muted', 'Wacht op inloggen');
    setTimeout(function () { var p = document.querySelector('#rootform [name=password]'); if (p) p.focus(); }, 50);
  }
  function beginShell() {
    if (kind !== 'shell') return start();
    PF.getJSON(rootApi).then(function (r) {
      rootStat(r.session);
      if (r.session || skipped()) start(); else askRoot(r.name);
    }, function () { start(); });
  }
  if (kind === 'shell') {
    $('rootform').addEventListener('submit', function (ev) {
      ev.preventDefault();
      var f = ev.target, err = $('root-err');
      err.style.display = 'none'; $('root-go').disabled = true;
      PF.postJSON(rootApi, { username: f.username.value, password: f.password.value, otp: f.otp.value }).then(function (r) {
        $('root-go').disabled = false;
        if (r.need_tfa) { $('otpfield').classList.remove('hidden'); f.otp.focus(); err.textContent = 'Vul de 2FA-code in.'; err.style.display = 'block'; return; }
        f.password.value = ''; f.otp.value = '';
        $('rootbox').classList.add('hidden');
        try { sessionStorage.removeItem(skipKey); } catch (x) { /* niets */ }
        rootStat(r.session);
        reconnect();
      }, function (ex) { $('root-go').disabled = false; err.textContent = ex.message; err.style.display = 'block'; });
    });
    $('root-skip').addEventListener('click', function () {
      try { sessionStorage.setItem(skipKey, '1'); } catch (x) { /* niets */ }
      $('rootbox').classList.add('hidden');
      start();
    });
    $('rootstat').addEventListener('click', function (ev) {
      if (ev.target.id === 'root-login') { closedByUs = true; try { if (ws) ws.close(); } catch (x) { /* niets */ } askRoot(); }
      if (ev.target.id === 'root-end') {
        PF.send('DELETE', rootApi).then(function () { rootStat(null); }, function () {});
      }
    });
  }

  function start() {
    hideOverlay();
    closedByUs = false;
    status('muted', 'Console aanvragen…');
    PF.postJSON('/api/console/start', { server: server, node: node, type: kind, vmid: vmid, update: !!$('upgrade') && kind === 'lxc' }).then(function (r) {
      $('ttl').textContent = r.title;
      lastRoot = !!r.root;
      var help = $('uphelp');
      if (help && r.via === 'ssh') help.innerHTML = '<strong>Via de node:</strong> <span>PVE Fleet opent de container met <span class="mono">pct enter</span>, dus je hoeft niet in te loggen. Het commando wordt vanzelf ingevoerd.</span>';
      else if (help && r.hint) help.innerHTML += ' <span class="small muted">' + PF.esc(r.hint) + '</span>';
      if (kind === 'shell') PF.getJSON(rootApi).then(function (x) { rootStat(x.session); }, function () {});
      document.title = r.title + ' · Console · PVE Fleet';
      if (r.proto === 'vnc') startVnc(r); else startTerm(r);
    }, function (err) {
      if (err.body && err.body.code === 'root_expired' && kind === 'shell') { rootStat(null); askRoot(); return; }
      status('crit', 'Niet gelukt');
      overlay('Console niet geopend', err.message);
    });
  }
  var lastRoot = false, autoUpgradeDone = false, autoTimer = null, promptTimer = null, tail = '';
  var dec = new TextDecoder();
  var ANSI = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>]|\r/g;
  // Commando meteen invoeren zodra er een root-prompt staat (eindigt op #). Bij 'login:' wachten
  // tot je zelf ingelogd bent; een gewone gebruiker ($) krijgt het commando niet.
  function watchPrompt(chunk) {
    if (!$('upgrade') || autoUpgradeDone) return;
    tail = (tail + chunk.replace(ANSI, '')).slice(-400);
    clearTimeout(promptTimer);
    if (/(^|\n)[^\n]*\S#\s?$/.test(tail)) {
      promptTimer = setTimeout(function () { if (/#\s?$/.test(tail)) autoRun(); }, 350);
    } else if (/(login|Password|Wachtwoord):\s?$/i.test(tail)) {
      var help = $('uphelp');
      if (help) help.innerHTML = '<strong>Eerst inloggen:</strong> <span>Log in als root. Daarna voert PVE Fleet het commando vanzelf in.</span>';
    }
  }
  function autoRun() {
    if (autoUpgradeDone || !$('upgrade')) return;
    autoUpgradeDone = true;
    clearTimeout(autoTimer); clearTimeout(promptTimer);
    var app = $('upgrade').getAttribute('data-app') === '1';
    var help = $('uphelp');
    if (help) help.innerHTML = app
      ? '<strong>App bijwerken:</strong> <span>Het update-commando is ingevoerd; volg de vragen van het update-script.</span> <span>Klaar? Sluit dit venster; PVE Fleet kijkt dan opnieuw.</span>'
      : '<strong>Bijwerken:</strong> <span>Het upgrade-commando is ingevoerd; lees wat er verandert en bevestig met <span class="mono">Y</span>.</span> <span>Klaar? Sluit dit venster; PVE Fleet kijkt dan opnieuw.</span>';
    sendInput($('upgrade').getAttribute('data-cmd') + '\r');
  }

  // ---------------------------------------------------------------- terminal
  function sendInput(d) {
    if (ws && ws.readyState === 1) ws.send('0:' + enc.encode(d).length + ':' + d);
  }
  function sendSize() {
    if (ws && ws.readyState === 1 && term) ws.send('1:' + term.cols + ':' + term.rows + ':');
  }
  function startTerm(r) {
    if (!term) {
      term = new window.Terminal({ cursorBlink: true, fontSize: fontSize, scrollback: 5000,
        fontFamily: "'IBM Plex Mono', ui-monospace, 'Cascadia Mono', Consolas, Menlo, monospace",
        theme: { background: '#050607', foreground: '#D6DCE0', cursor: '#E8A13A', selectionBackground: '#2E4256' } });
      fit = new window.FitAddon.FitAddon();
      term.loadAddon(fit);
      term.open($('term'));
      term.onData(sendInput);
      term.onResize(sendSize);
      window.addEventListener('resize', function () { try { fit.fit(); } catch (x) { /* niets */ } });
    } else {
      term.reset();
    }
    try { fit.fit(); } catch (x) { /* niets */ }
    ws = new WebSocket(wsUrl(r.id));
    ws.binaryType = 'arraybuffer';
    ws.onopen = function () {
      status('ok', 'Verbonden');
      $('paste').disabled = false;
      if ($('upgrade')) $('upgrade').disabled = false;
      sendSize();
      if ($('upgrade') && lastRoot && !autoUpgradeDone) {
        // Root-sessie: geen inlogprompt. Normaal herkent watchPrompt de prompt; lukt dat niet
        // (een bijzondere prompt), dan na een paar seconden toch invoeren.
        clearTimeout(autoTimer);
        autoTimer = setTimeout(function () { autoRun(); }, 4000);
      }
      term.focus();
      clearInterval(ping);
      ping = setInterval(function () { if (ws && ws.readyState === 1) ws.send('2'); }, 30000);
    };
    ws.onmessage = function (ev) {
      if (typeof ev.data === 'string') { term.write(ev.data); watchPrompt(ev.data); }
      else { var u = new Uint8Array(ev.data); term.write(u); watchPrompt(dec.decode(u, { stream: true })); }
    };
    ws.onclose = function (ev) {
      clearInterval(ping);
      $('paste').disabled = true;
      if ($('upgrade')) $('upgrade').disabled = true;
      if (closedByUs) return;
      status('crit', 'Verbinding gesloten');
      overlay('De console is gesloten', ev.reason || (ev.code === 1006 ? 'De verbinding werd onverwacht verbroken.' : 'Je bent afgemeld of de sessie is beëindigd.'));
    };
  }

  // ---------------------------------------------------------------- VM-scherm
  function startVnc(r) {
    if (rfb) { try { rfb.disconnect(); } catch (x) { /* niets */ } rfb = null; }
    $('vnc').innerHTML = '';
    rfb = new window.NoVNC.RFB($('vnc'), wsUrl(r.id), { credentials: { password: r.password }, wsProtocols: ['binary'] });
    rfb.scaleViewport = $('scale').getAttribute('aria-pressed') === 'true';
    rfb.resizeSession = false;
    rfb.focusOnClick = true;
    rfb.addEventListener('connect', function () { status('ok', 'Verbonden'); $('cad').disabled = false; rfb.focus(); });
    rfb.addEventListener('disconnect', function (e) {
      $('cad').disabled = true;
      if (closedByUs) return;
      status('crit', 'Verbinding gesloten');
      overlay('Het VM-scherm is gesloten', e.detail && e.detail.clean ? 'De verbinding is beëindigd.' : 'De verbinding werd onverwacht verbroken.');
    });
    rfb.addEventListener('securityfailure', function (e) {
      status('crit', 'Geweigerd');
      overlay('Proxmox weigerde het VM-scherm', (e.detail && e.detail.reason) || 'Probeer opnieuw te verbinden.');
    });
  }

  // ---------------------------------------------------------------- knoppen
  function reconnect() {
    closedByUs = true;
    try { if (ws) ws.close(); } catch (x) { /* niets */ }
    try { if (rfb) rfb.disconnect(); } catch (x) { /* niets */ }
    setTimeout(start, 150);
  }
  $('again').addEventListener('click', reconnect);
  $('over-again').addEventListener('click', reconnect);
  $('full').addEventListener('click', function () {
    var el = document.documentElement;
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    } else if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  });
  if (kind === 'qemu') {
    $('cad').addEventListener('click', function () { if (rfb) rfb.sendCtrlAltDel(); });
    $('scale').addEventListener('click', function () {
      var on = this.getAttribute('aria-pressed') !== 'true';
      this.setAttribute('aria-pressed', on ? 'true' : 'false');
      this.textContent = on ? 'Passend maken' : 'Ware grootte';
      if (rfb) rfb.scaleViewport = on;
    });
  } else {
    $('paste').addEventListener('click', function () {
      if (navigator.clipboard && navigator.clipboard.readText && window.isSecureContext) {
        navigator.clipboard.readText().then(function (t) { if (t) sendInput(t); term.focus(); }, askPaste);
      } else askPaste();
    });
    function setFont(d) {
      fontSize = Math.max(9, Math.min(28, fontSize + d));
      try { localStorage.setItem('pvef_console_font', String(fontSize)); } catch (x) { /* niets */ }
      if (term) { term.options.fontSize = fontSize; try { fit.fit(); } catch (x) { /* niets */ } }
    }
    if ($('upgrade')) $('upgrade').addEventListener('click', function () {
      autoUpgradeDone = true; clearTimeout(autoTimer); clearTimeout(promptTimer);
      sendInput($('upgrade').getAttribute('data-cmd') + '\r');
      term.focus();
    });
    $('bigger').addEventListener('click', function () { setFont(2); });
    $('smaller').addEventListener('click', function () { setFont(-2); });
  }
  function askPaste() {
    // Klembord lezen mag de browser alleen via https; anders even plakken in een venstertje.
    var t = window.prompt('Plak hier de tekst (Ctrl+V) en klik op OK. Tip: in de terminal werkt ook Ctrl+Shift+V of rechtermuisklik › Plakken.');
    if (t) sendInput(t);
    if (term) term.focus();
  }
  window.addEventListener('beforeunload', function () { closedByUs = true; try { if (ws) ws.close(); } catch (x) { /* niets */ } });
  beginShell();
})();
