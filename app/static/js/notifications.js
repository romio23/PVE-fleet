/* Meldingen: Prowl, ntfy, Pushover en een algemene webhook. Eén formulier; testen kan met wat er nu ingevuld staat. */
(function () {
  var e = PF.esc;
  var root = document.getElementById('notify');
  var cfg = null;

  var EVENTS = [
    ['updates', 'Nieuwe updates', 'Voor een node of container komen updates beschikbaar (ook als er meer bij komen).'],
    ['reboot', 'Herstart nodig', 'Een node moet herstarten, bijvoorbeeld voor een nieuwe kernel.'],
    ['rolling', 'Rolling update klaar, gestopt of mislukt', 'Bij een automatische rolling update.'],
    ['offline', 'Node of cluster onbereikbaar', 'Een node valt weg, een cluster verliest quorum, en een bericht als het weer goed is.'],
    ['backup', 'Back-up mislukt', 'Een back-uptaak is niet gelukt.']
  ];
  var CH = {
    prowl: { title: 'Prowl', sub: 'iPhone en iPad. API-sleutel aanmaken op prowlapp.com › API Keys.' },
    ntfy: { title: 'ntfy', sub: 'iPhone, Android en browser. Gratis via ntfy.sh, of je eigen server. Abonneer je in de ntfy-app op hetzelfde topic.' },
    pushover: { title: 'Pushover', sub: 'iPhone, Android en computer. De gebruikerssleutel staat op pushover.net; maak daar ook een applicatie aan voor het token.' },
    webhook: { title: 'Algemene webhook', sub: 'PVE Fleet doet een POST met JSON naar dit adres. Werkt met Home Assistant, Discord, Slack, Mattermost, Matrix-bruggen en eigen scripts.' }
  };

  function field(ch, name, label, opts) {
    opts = opts || {};
    var c = cfg.channels[ch];
    var secret = !!opts.secret;
    var val = secret ? '' : (c[name] || '');
    var set = secret && c[name + '_set'];
    return '<label class="field">' + e(label) + (set ? ' <span class="hint">opgeslagen; leeg laten om te houden</span>' : '') +
      '<input class="input mono" data-ch="' + ch + '" data-k="' + name + '" type="' + (secret ? 'password' : 'text') + '" value="' + e(val) + '"' +
      (opts.ph ? ' placeholder="' + e(opts.ph) + '"' : '') + ' autocomplete="' + (secret ? 'new-password' : 'off') + '" spellcheck="false"></label>';
  }
  function clearBox(ch, name) {
    return cfg.channels[ch][name + '_set'] ? '<label class="check small"><input type="checkbox" data-ch="' + ch + '" data-clear="' + name + '">Opgeslagen waarde wissen</label>' : '';
  }

  function fields(ch) {
    if (ch === 'prowl') return field(ch, 'apikey', 'API-sleutel', { secret: 1 }) + clearBox(ch, 'apikey');
    if (ch === 'ntfy') return field(ch, 'server', 'Server', { ph: 'https://ntfy.sh' }) + field(ch, 'topic', 'Topic', { ph: 'bijv. pvefleet-mijn-geheime-naam' }) +
      field(ch, 'token', 'Toegangstoken (alleen als je server login vraagt)', { secret: 1 }) + clearBox(ch, 'token') +
      '<div class="small muted" style="grid-column:1/-1">Een topic op ntfy.sh is voor iedereen te lezen die de naam weet. Kies dus een lange, onraadbare naam.</div>';
    if (ch === 'pushover') return field(ch, 'user', 'Gebruikerssleutel', { secret: 1 }) + clearBox(ch, 'user') +
      field(ch, 'token', 'Applicatietoken', { secret: 1 }) + clearBox(ch, 'token') +
      field(ch, 'device', 'Alleen naar dit apparaat (naam, optioneel)', {});
    return field(ch, 'url', 'Adres (URL)', { ph: 'https://…' }) + field(ch, 'secret', 'Geheim in de header X-PVEFleet-Secret (optioneel)', { secret: 1 }) + clearBox(ch, 'secret') +
      '<label class="check" style="grid-column:1/-1"><input type="checkbox" data-ch="webhook" data-k="verify_tls"' + (cfg.channels.webhook.verify_tls ? ' checked' : '') + '>Certificaat van het adres controleren (zet uit bij een eigen, zelfondertekend certificaat)</label>';
  }

  function lastLine(ch) {
    var l = cfg.last && cfg.last[ch];
    if (!l) return '';
    return l.ok ? '<span class="c-ok">✓ laatste bericht verstuurd ' + PF.when(l.time) + '</span>'
      : '<span class="c-crit">✗ laatste poging mislukt ' + PF.when(l.time) + ': ' + e(l.error || '') + '</span>';
  }

  function chCard(ch) {
    var c = cfg.channels[ch];
    return '<section class="card pad" data-card="' + ch + '" role="group" aria-labelledby="h-' + ch + '"><div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap">' +
      '<div style="flex:1 1 320px;min-width:0"><h2 class="small-title" id="h-' + ch + '" style="margin-bottom:4px">' + CH[ch].title + '</h2><p class="muted small" style="margin:0;line-height:1.5">' + e(CH[ch].sub) + '</p></div>' +
      '<label class="check" style="margin:0"><input type="checkbox" data-ch="' + ch + '" data-k="enabled"' + (c.enabled ? ' checked' : '') + '>Aan</label></div>' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px 16px;margin-top:12px">' + fields(ch) + '</div>' +
      '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px"><button type="button" class="btn" data-test="' + ch + '">Testmelding sturen</button>' +
      '<span class="small" id="res-' + ch + '" role="status" aria-live="polite">' + lastLine(ch) + '</span></div></section>';
  }

  function render() {
    var ev = cfg.events;
    root.innerHTML =
      '<div style="display:flex;flex-direction:column;gap:20px">' +
      '<section class="card pad"><h2 class="small-title" id="h-events">Waarover wil je een melding?</h2><div role="group" aria-labelledby="h-events" style="display:grid;gap:10px;margin-top:8px">' +
      EVENTS.map(function (x) {
        return '<label class="check" style="align-items:flex-start"><input type="checkbox" data-ev="' + x[0] + '"' + (ev[x[0]] ? ' checked' : '') + '><span><strong>' + e(x[1]) + '</strong><br><span class="small muted">' + e(x[2]) + '</span></span></label>';
      }).join('') + '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px 16px;margin-top:14px">' +
      '<label class="field">Taal van de berichten<select class="input" id="n-lang"><option value="nl"' + (cfg.lang === 'nl' ? ' selected' : '') + '>Nederlands</option><option value="en"' + (cfg.lang === 'en' ? ' selected' : '') + '>English</option></select></label>' +
      '<label class="field">Onbereikbaar pas melden na<select class="input" id="n-delay">' +
      [[0, 'direct'], [60, '1 minuut'], [180, '3 minuten'], [600, '10 minuten']].map(function (o) {
        return '<option value="' + o[0] + '"' + (Number(cfg.offline_delay) === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
      }).join('') + '</select></label></div>' +
      '<p class="small muted" style="margin:12px 0 0;line-height:1.5">Je krijgt een melding als iets verandert, niet bij elke controle. Na een herstart van PVE Fleet worden bestaande situaties niet opnieuw gemeld. Tijdens een automatische rolling update blijven de berichten over updates en herstarten stil; je krijgt dan één bericht als hij klaar is of stopt.</p></section>' +
      ['prowl', 'ntfy', 'pushover', 'webhook'].map(chCard).join('') +
      '<section class="card pad"><div style="display:flex;gap:10px;align-items:center;justify-content:flex-end;flex-wrap:wrap"><span class="small c-ok" id="n-saved" role="status" aria-live="polite"></span>' +
      '<button type="button" class="btn lg primary" id="n-save">Wijzigingen opslaan</button></div>' +
      '<p class="small muted" style="margin:10px 0 0;line-height:1.5">De sleutels staan in <span class="mono">data/notify.json</span>, net als je andere geheimen. Bewaar een back-up van <span class="mono">data/</span> dus veilig.</p></section>' +
      '</div>';
  }

  function chValues(ch) {
    var out = {};
    var els = root.querySelectorAll('[data-ch="' + ch + '"]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i], k = el.getAttribute('data-k'), cl = el.getAttribute('data-clear');
      if (cl) { if (el.checked) out[cl + '_clear'] = true; continue; }
      out[k] = el.type === 'checkbox' ? el.checked : el.value;
    }
    return out;
  }

  function collect() {
    var body = { lang: document.getElementById('n-lang').value, offline_delay: Number(document.getElementById('n-delay').value), events: {}, channels: {} };
    var evs = root.querySelectorAll('[data-ev]');
    for (var i = 0; i < evs.length; i++) body.events[evs[i].getAttribute('data-ev')] = evs[i].checked;
    ['prowl', 'ntfy', 'pushover', 'webhook'].forEach(function (ch) { body.channels[ch] = chValues(ch); });
    return body;
  }

  root.addEventListener('click', function (ev) {
    var t = ev.target.closest ? ev.target.closest('[data-test]') : null;
    if (t) {
      var ch = t.getAttribute('data-test'), out = document.getElementById('res-' + ch);
      out.className = 'small muted'; out.textContent = 'Versturen…'; t.disabled = true;
      PF.postJSON('/api/notify/test/' + ch, chValues(ch)).then(function (r) {
        t.disabled = false;
        out.className = 'small ' + (r.ok ? 'c-ok' : 'c-crit');
        out.textContent = r.ok ? '✓ Verstuurd. Kijk op je telefoon of in je kanaal.' : '✗ Mislukt: ' + (r.error || 'onbekende fout');
      }, function (err) { t.disabled = false; out.className = 'small c-crit'; out.textContent = '✗ ' + err.message; });
      return;
    }
    if (ev.target.id === 'n-save') {
      var b = ev.target; b.disabled = true;
      PF.postJSON('/api/notify', collect()).then(function (r) {
        b.disabled = false; cfg = r.config; render();
        var s = document.getElementById('n-saved'); s.textContent = 'Opgeslagen';
        setTimeout(function () { var x = document.getElementById('n-saved'); if (x) x.textContent = ''; }, 3000);
      }, function (err) { b.disabled = false; PFA.toast(err.message, true); });
    }
  });

  PF.getJSON('/api/notify').then(function (d) { cfg = d; render(); },
    function (err) { root.innerHTML = '<div class="card pad c-crit">Kan de instellingen niet laden: ' + e(err.message) + '</div>'; });
})();
