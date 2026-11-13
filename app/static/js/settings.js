(function () {
  var e = PF.esc;
  var data = null;
  var pinEnabled = true;
  var LABELS = { node_reboot: 'Node herstarten', node_shutdown: 'Node afsluiten', node_wol: 'Wake-on-LAN', ack: 'Meldingen bevestigen' };
  function $(id) { return document.getElementById(id); }

  function setSwitch(on) {
    pinEnabled = on;
    var sw = $('pin-switch');
    sw.setAttribute('aria-checked', on ? 'true' : 'false');
    sw.style.background = on ? '#E8A13A' : '#3A4248';
    sw.style.justifyContent = on ? 'flex-end' : 'flex-start';
    $('pin-on').classList.toggle('hidden', !on);
    $('pin-off').classList.toggle('hidden', on);
  }

  function render() {
    var k = data.kiosk;
    $('username').textContent = data.username;
    setSwitch(k.pin_enabled);
    $('pin-changed').textContent = k.pin_set ? (k.pin_changed ? 'gewijzigd ' + PF.when(k.pin_changed) : '') : 'nog niet ingesteld';
    $('unlock').value = String(k.unlock_seconds);
    $('lockout').value = String(k.lockout_seconds);
    $('pin-actions').innerHTML = data.pin_actions_all.map(function (a) {
      return '<label class="check"><input type="checkbox" data-pa="' + a + '"' + (k.pin_actions.indexOf(a) >= 0 ? ' checked' : '') + '>' + LABELS[a] + '</label>';
    }).join('');
    $('readonly').checked = k.readonly;
    $('allow_ack').checked = k.allow_ack;
    $('localhost_auto').checked = k.localhost_auto;
    $('locks').classList.toggle('hidden', !data.locked);
    $('locks-txt').textContent = data.locked ? data.locked + ' scherm' + (data.locked === 1 ? ' is' : 'en zijn') + ' geblokkeerd na foute pincodes.' : '';
    renderDevices();
  }

  function renderDevices() {
    var list = data.devices;
    $('devices').innerHTML = list.length ? list.map(function (d) {
      var recent = Date.now() / 1000 - (d.last_seen || 0) < 300;
      return '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;padding:14px 20px;border-top:1px solid var(--line2)">' +
        '<span class="dot b-' + (recent ? 'ok' : 'muted') + '" aria-hidden="true"></span>' +
        '<div style="flex:1 1 260px;min-width:0"><div style="font-weight:500">' + e(d.name) + '</div>' +
        '<div class="small muted mono" style="margin-top:3px">' + (d.last_ip ? e(d.last_ip) + ' · ' : '') + 'laatst gezien ' + PF.when(d.last_seen) + ' · gekoppeld ' + PF.when(d.created) + '</div></div>' +
        '<button type="button" class="btn ghost" data-rename="' + e(d.id) + '">Naam wijzigen</button>' +
        '<button type="button" class="btn danger" data-unpair="' + e(d.id) + '">Ontkoppelen</button></div>';
    }).join('') : '<div class="empty">Nog geen schermen gekoppeld. Een browser op deze computer zelf mag de kiosk ' + (data.kiosk.localhost_auto ? 'zonder koppelen' : 'alleen na koppelen') + ' tonen.</div>';
  }

  function load() {
    return PF.getJSON('/api/settings').then(function (d) {
      data = d;
      $('loading').classList.add('hidden');
      $('sections').classList.remove('hidden');
      render();
    }, function (err) { $('loading').textContent = 'Kan instellingen niet laden: ' + err.message; });
  }
  function loadLog() {
    PF.getJSON('/api/actions-log').then(function (d) {
      $('log').innerHTML = d.entries.length ? d.entries.map(function (x) {
        var what = x.label || (x.action === 'pin' ? 'Pincode ingevoerd' : [x.action, x.node, x.vmid].filter(Boolean).join(' '));
        return '<tr><td class="mono muted" style="white-space:nowrap">' + PF.when(x.time) + '</td><td>' + e(x.who || '') + '</td><td>' + e(what) + '</td>' +
          '<td>' + (x.ok ? '<span class="c-ok">OK</span>' : '<span class="c-crit">' + e(x.error || 'mislukt') + '</span>') + '</td></tr>';
      }).join('') : '<tr><td colspan="4" class="muted">Nog geen acties uitgevoerd.</td></tr>';
    }, function () {});
  }

  $('pin-switch').addEventListener('click', function () {
    var next = !pinEnabled;
    PF.postJSON('/api/settings/kiosk', { pin_enabled: next }).then(function (r) {
      data.kiosk = r.kiosk; setSwitch(next);
      PFA.toast(next ? 'Pincode staat aan' : 'Pincode staat uit');
    }, function (err) { PFA.toast(err.message, true); });
  });

  $('pin-change').addEventListener('click', function () {
    PFA.modal({ title: 'Pincode wijzigen', body: 'Kies een nieuwe pincode van 4 tot 6 cijfers. Je bent al ingelogd als beheerder, dus de oude pincode is niet nodig.',
      fields: [{ name: 'pin', label: 'Nieuwe pincode', type: 'password', mono: true, maxlength: 6 },
        { name: 'pin2', label: 'Herhaal nieuwe pincode', type: 'password', mono: true, maxlength: 6 }],
      confirm: 'Pincode opslaan',
      validate: function (v) {
        if (!/^\d{4,6}$/.test(v.pin)) return 'De pincode moet uit 4 tot 6 cijfers bestaan.';
        if (v.pin !== v.pin2) return 'De pincodes zijn niet gelijk.';
        return null;
      } }).then(function (v) {
      if (!v) return;
      PF.postJSON('/api/settings/pin', v).then(function () { PFA.toast('Pincode gewijzigd'); load(); }, function (err) { PFA.toast(err.message, true); });
    });
  });

  $('save-kiosk').addEventListener('click', function () {
    var acts = Array.prototype.map.call(document.querySelectorAll('[data-pa]:checked'), function (c) { return c.getAttribute('data-pa'); });
    PF.postJSON('/api/settings/kiosk', {
      unlock_seconds: parseInt($('unlock').value, 10), lockout_seconds: parseInt($('lockout').value, 10), pin_actions: acts,
      readonly: $('readonly').checked, allow_ack: $('allow_ack').checked, localhost_auto: $('localhost_auto').checked
    }).then(function (r) {
      data.kiosk = r.kiosk; render();
      var s = $('saved'); s.classList.remove('hidden'); setTimeout(function () { s.classList.add('hidden'); }, 2500);
    }, function (err) { PFA.toast(err.message, true); });
  });

  $('unblock').addEventListener('click', function () {
    PF.postJSON('/api/settings/unblock').then(function (r) { PFA.toast(r.released + ' blokkade(s) opgeheven'); load(); }, function (err) { PFA.toast(err.message, true); });
  });

  $('pair').addEventListener('click', function () {
    PFA.modal({ title: 'Scherm koppelen', body: 'Geef het scherm een naam, dan krijg je een koppelcode van 6 cijfers.',
      fields: [{ name: 'name', label: 'Naam van het scherm', value: 'iPad woonkamer', maxlength: 40 }], confirm: 'Koppelcode maken',
      validate: function (v) { return v.name ? null : 'Vul een naam in'; } }).then(function (v) {
      if (!v) return;
      PF.postJSON('/api/settings/pair-code', { name: v.name }).then(function (r) {
        var until = Date.now() + r.expires_in * 1000, timer = null;
        var base = r.base || (location.protocol + '//' + location.host);
        var qr = '';
        if (window.qrcode && r.qr_url) {
          try { var q = window.qrcode(0, 'M'); q.addData(r.qr_url); q.make(); qr = q.createSvgTag({ cellSize: 5, margin: 3, scalable: true }); } catch (x) { qr = ''; }
        }
        PFA.modal({ title: 'Scherm koppelen: ' + r.name, wide: !!qr,
          body: (r.host_warning ? '<div class="banner warn" style="margin-bottom:12px"><div class="txt"><div class="desc" style="margin:0">' + e(r.host_warning) + '</div></div></div>' : '') +
            '<div style="display:flex;flex-wrap:wrap;gap:24px;align-items:center">' +
            (qr ? '<div style="flex:0 0 auto;text-align:center"><div class="qrbox">' + qr + '</div>' +
              '<div class="small" style="margin-top:8px;color:var(--text)"><strong>Scan met de camera</strong> van de iPad</div>' +
              '<div class="small muted">Safari opent de kiosk; het scherm is dan gekoppeld.</div></div>' : '') +
            '<div style="flex:1 1 240px;min-width:0">' + (qr ? '<div class="small muted" style="text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px">Of met de hand</div>' : '') +
            '<ol style="margin:0 0 10px;padding-left:20px;line-height:1.8">' +
            '<li>Open op het scherm: <span class="mono" style="color:var(--text)">' + e(base) + '/kiosk</span></li>' +
            '<li>Voer daar deze code in:</li></ol>' +
            '<div class="mono" style="font-size:40px;letter-spacing:10px;text-align:center;color:var(--text);padding:4px 0">' + r.code.slice(0, 3) + ' ' + r.code.slice(3) + '</div></div></div>' +
            '<p class="small muted" style="text-align:center;margin:10px 0 0" id="pair-left"></p>' +
            (qr ? '<p class="small muted" style="margin:6px 0 0">Daarna in Safari: deelknop › <strong>Zet op beginscherm</strong>, zodat de kiosk schermvullend opent.</p>' : ''),
          confirm: null, cancelLabel: 'Klaar',
          onRender: function () {
            function tick() {
              var left = Math.max(0, Math.round((until - Date.now()) / 1000));
              var el = document.getElementById('pair-left');
              if (!el) { clearInterval(timer); return; }
              el.textContent = left ? 'Geldig nog ' + Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2) + ' minuten, één keer te gebruiken (QR-code of cijfercode).' : 'Verlopen. Maak een nieuwe code.';
              if (!left) clearInterval(timer);
            }
            tick(); timer = setInterval(tick, 1000);
          } }).then(function () { clearInterval(timer); load(); });
      }, function (err) { PFA.toast(err.message, true); });
    });
  });

  $('devices').addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-rename],[data-unpair]') : null;
    if (!b) return;
    var id = b.getAttribute('data-rename') || b.getAttribute('data-unpair');
    var dev = data.devices.filter(function (d) { return d.id === id; })[0];
    if (!dev) return;
    if (b.hasAttribute('data-rename')) {
      PFA.modal({ title: 'Naam wijzigen', fields: [{ name: 'name', label: 'Naam', value: dev.name, maxlength: 40 }], confirm: 'Opslaan',
        validate: function (v) { return v.name ? null : 'Vul een naam in'; } }).then(function (v) {
        if (v) PF.postJSON('/api/settings/devices/' + encodeURIComponent(id), { name: v.name }).then(load, function (err) { PFA.toast(err.message, true); });
      });
    } else {
      PFA.modal({ title: dev.name + ' ontkoppelen?', body: 'Dit scherm toont daarna het koppelscherm en moet opnieuw gekoppeld worden.', confirm: 'Ontkoppelen', kind: 'danger' })
        .then(function (v) { if (v) PF.send('DELETE', '/api/settings/devices/' + encodeURIComponent(id)).then(load, function (err) { PFA.toast(err.message, true); }); });
    }
  });

  $('pwform').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var f = ev.target;
    var body = { current: f.current.value, new: f['new'].value, new2: f.new2.value };
    PF.postJSON('/api/settings/password', body).then(function () {
      f.reset(); $('pwmsg').innerHTML = '<div class="okbox">Wachtwoord gewijzigd. Andere apparaten waarop je was ingelogd, moeten opnieuw inloggen.</div>';
    }, function (err) { $('pwmsg').innerHTML = '<div class="errbox" role="alert">' + e(err.message) + '</div>'; });
  });

  var live = document.getElementById('live');
  if (live) { live.querySelector('.txt').textContent = ''; live.style.display = 'none'; }
  load(); loadLog();
  setInterval(loadLog, 15000);
})();
