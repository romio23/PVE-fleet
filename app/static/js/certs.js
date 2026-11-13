/* Certificaten: DNS-provider, https voor PVE Fleet, certificaten voor de Proxmox-nodes. */
(function () {
  var e = PF.esc;
  var root = document.getElementById('certs');
  var data = null, base = '';
  function setBase(v) { base = v; PF.postJSON('/api/certs/settings', { base_domain: v }).then(null, function (err) { PFA.toast(err.message, true); }); }

  function days(ts) { return ts ? Math.floor((ts * 1000 - Date.now()) / 86400000) : null; }
  function validity(ts) {
    var d = days(ts);
    if (d === null) return '<span class="muted">—</span>';
    var cls = d < 0 ? 'c-crit' : (d < 21 ? 'c-warn' : 'c-ok');
    return '<span class="' + cls + '">' + (d < 0 ? 'verlopen' : 'nog ' + d + ' dagen') + '</span> <span class="small muted">(' + PF.when(ts) + ')</span>';
  }
  function cleanName(s) { var m = /CN=([^/,]+)/.exec(s || ''); return m ? m[1] : (s || '—'); }

  // ------------------------------------------------------------ provider
  function providerCard(d) {
    var p = d.provider || {};
    var t = p.type || '';
    return '<section class="card pad"><h2 class="small-title">1 · DNS-provider voor Let\'s Encrypt</h2>' +
      '<p class="muted" style="margin:0 0 12px;line-height:1.55">Let\'s Encrypt controleert met een tijdelijk DNS-record dat het domein van jou is (DNS-01). Daarvoor zet PVE Fleet via de API van je DNS-provider even een record neer. Er hoeft niets van je netwerk open naar internet.</p>' +
      '<form id="prov" class="mform" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px 16px;align-items:start">' +
      '<label class="field">DNS-provider<select class="input" name="type">' +
      [['', 'Kies…'], ['transip', 'TransIP'], ['cloudflare', 'Cloudflare']].concat(d.demo ? [['demo', 'Demo (geen echte aanvraag)']] : []).map(function (o) {
        return '<option value="' + o[0] + '"' + (o[0] === t ? ' selected' : '') + '>' + o[1] + '</option>';
      }).join('') + '</select></label>' +
      '<label class="field">E-mailadres (voor meldingen van Let\'s Encrypt)<input class="input" name="email" type="email" value="' + e(d.email || '') + '" placeholder="jij@voorbeeld.nl"></label>' +
      '<div class="pv pv-transip" style="grid-column:1/-1;display:' + (t === 'transip' ? 'grid' : 'none') + ';grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px 16px">' +
        '<label class="field">TransIP-gebruikersnaam<input class="input mono" name="login" value="' + e(p.login || '') + '" autocomplete="off"></label>' +
        '<label class="check" style="align-self:end"><input type="checkbox" name="global_key"' + (p.global_key === false ? '' : ' checked') + '>Sleutel werkt vanaf elk IP-adres (geen whitelist)</label>' +
        '<label class="field" style="grid-column:1/-1">Private key' + (p.has_key ? ' <span class="hint">opgeslagen; leeg laten om te houden</span>' : '') +
          '<textarea class="input mono" name="private_key" rows="4" placeholder="-----BEGIN PRIVATE KEY-----" style="font-size:12px"></textarea></label>' +
        '<div class="small muted" style="grid-column:1/-1;line-height:1.5">TransIP-controlepaneel › <em>Account</em> › <em>API</em>: zet de API aan en maak een <em>Key Pair</em> aan. Laat <em>Alleen whitelisted IP-adressen</em> uit, of zet het IP-adres van je internetverbinding erin. Plak de hele sleutel hierboven.</div></div>' +
      '<div class="pv pv-cloudflare" style="grid-column:1/-1;display:' + (t === 'cloudflare' ? 'grid' : 'none') + ';gap:8px">' +
        '<label class="field">API-token' + (p.has_token ? ' <span class="hint">opgeslagen; leeg laten om te houden</span>' : '') + '<input class="input mono" name="token" type="password" autocomplete="off"></label>' +
        '<div class="small muted" style="line-height:1.5">Cloudflare › <em>My Profile</em> › <em>API Tokens</em> › <em>Create Token</em> › sjabloon <strong>Edit zone DNS</strong>, en kies bij <em>Zone Resources</em> je domein.</div></div>' +
      '<label class="check" style="grid-column:1/-1"><input type="checkbox" name="staging"' + (d.staging ? ' checked' : '') + '>Testomgeving van Let\'s Encrypt gebruiken (geen geldig certificaat; om eerst te oefenen zonder limieten)</label>' +
      '<div style="grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap;align-items:center"><button type="submit" class="btn primary">Opslaan</button><button type="button" class="btn ghost" data-c="ptest">Verbinding testen</button><span class="small" id="ptest-res"></span></div>' +
      '</form></section>';
  }

  // ------------------------------------------------------------ PVE Fleet
  function fleetCard(d) {
    var f = d.fleet, i = f.info;
    var host = (f.domains && f.domains[0]) || '';
    var url = host ? 'https://' + host + (f.https_port === 443 ? '' : ':' + f.https_port) : null;
    var status = i && !i.error
      ? '<div class="kv">' +
        '<div class="row"><span class="k">Domein</span><span class="v mono">' + e((i.sans || []).join(', ') || i.subject) + '</span></div>' +
        '<div class="row"><span class="k">Uitgegeven door</span><span class="v">' + e(i.issuer) + (f.source === 'upload' ? ' <span class="small muted">(eigen certificaat)</span>' : '') + '</span></div>' +
        '<div class="row"><span class="k">Geldig</span><span class="v">' + validity(i.not_after) + '</span></div>' +
        '<div class="row"><span class="k">https</span><span class="v">' + (f.running ? '<span class="c-ok">actief op poort ' + f.https_port + '</span>' + (url ? ' · <a href="' + e(url) + '" target="_blank" rel="noopener">' + e(url) + '</a>' : '')
          : '<span class="c-crit">' + e(f.listen_error || 'niet actief (start PVE Fleet opnieuw)') + '</span>') + '</span></div>' +
        (f.source === 'letsencrypt' ? '<div class="row"><span class="k">Automatisch vernieuwen</span><span class="v"><label class="check" style="margin:0"><input type="checkbox" data-fs="auto_renew"' + (f.auto_renew ? ' checked' : '') + '>30 dagen voor het verloopt</label></span></div>' : '') +
        '</div>'
      : '<p class="muted" style="margin:0 0 10px">Nog geen certificaat. PVE Fleet is nu alleen bereikbaar via http.</p>';
    return '<section class="card pad"><h2 class="small-title">2 · https voor PVE Fleet</h2>' + status +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">' +
      '<button type="button" class="btn primary" data-c="fleet-le">' + (i ? 'Opnieuw aanvragen' : 'Aanvragen bij Let\'s Encrypt') + '</button>' +
      '<button type="button" class="btn ghost" data-c="fleet-up">Eigen certificaat inlezen</button>' +
      (i ? '<button type="button" class="btn ghost" data-c="fleet-del" style="margin-left:auto">Certificaat verwijderen</button>' : '') + '</div>' +
      '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-top:14px;padding-top:12px;border-top:1px solid var(--line2)">' +
      '<label class="field" style="flex:0 0 160px">https-poort<input class="input mono" id="f-port" value="' + f.https_port + '" inputmode="numeric"></label>' +
      '<label class="check" style="margin-top:18px"><input type="checkbox" data-fs="redirect"' + (f.redirect ? ' checked' : '') + (f.running ? '' : ' disabled') + '>http doorsturen naar https</label>' +
      '<button type="button" class="btn ghost" data-c="fleet-port" style="margin-top:18px">Poort opslaan</button></div>' +
      '<p class="small muted" style="margin:10px 0 0;line-height:1.5">Laat de naam in je eigen netwerk naar de Pi wijzen (DNS-regel in je router, Pi-hole of AdGuard), bijvoorbeeld <span class="mono">fleet.' + e(base || 'jouwdomein.nl') + '</span> → het IP-adres van de Pi. ' +
      'http op poort 8080 blijft werken; wil je een iPad via https gebruiken, koppel hem dan opnieuw terwijl je PVE Fleet via die naam open hebt. De kiosk op de Pi zelf (localhost) wordt nooit doorgestuurd.</p></section>';
  }

  // ------------------------------------------------------------ nodes
  function nodesCard(d) {
    var rows = d.nodes.map(function (n) {
      var c = n.cert, st = n.settings || {};
      var dom = st.domain || (base ? n.node + '.' + base : '');
      var cur = n.error ? '<span class="small c-warn" title="' + e(n.error) + '">kan niet lezen</span>'
        : (!c ? '<span class="muted small">' + (n.status === 'online' ? '—' : 'offline') + '</span>'
          : (c.self_signed ? '<span class="pill" style="padding:1px 8px;font-size:12px">zelfondertekend</span> ' : '<span class="small">' + e(cleanName(c.issuer)) + '</span><br>') + validity(c.not_after));
      return '<tr><td class="mono">' + e(n.node) + '<div class="small muted">' + e(n.group) + '</div></td><td>' + cur + '</td>' +
        '<td><input class="input mono" data-dom="' + e(n.server + '/' + n.node) + '" value="' + e(dom) + '" placeholder="' + e(n.node) + '.jouwdomein.nl" style="height:34px;min-width:220px"></td>' +
        '<td><label class="check" style="margin:0" title="Automatisch vernieuwen (Let\'s Encrypt)"><input type="checkbox" data-nar="' + e(n.server + '/' + n.node) + '"' + (st.auto_renew === false ? '' : ' checked') + '>auto</label></td>' +
        '<td><div class="rowacts">' + (n.status === 'online' ? '<button type="button" class="btn primary" data-c="node-le" data-k="' + e(n.server + '/' + n.node) + '">Aanvragen</button>' +
          '<button type="button" class="btn ghost" data-c="node-up" data-k="' + e(n.server + '/' + n.node) + '">Inlezen</button>' : '') + '</div></td></tr>';
    }).join('');
    return '<section class="card" style="overflow:hidden"><div class="card-head"><h2>3 · Proxmox-nodes</h2><span class="small muted">certificaat van de webinterface (poort 8006)</span>' +
      '<div class="end"><label class="small muted" for="base" style="align-self:center">Basisdomein</label><input class="input mono" id="base" value="' + e(base) + '" placeholder="romioinc.nl" style="height:34px;width:180px">' +
      '<button type="button" class="btn ghost" data-c="node-all">Alle online nodes aanvragen</button></div></div>' +
      '<div class="tablewrap"><table class="tbl" style="min-width:900px"><thead><tr><th>Node</th><th>Huidig certificaat</th><th>Naam</th><th>Vernieuwen</th><th class="r"><span class="sr">Acties</span></th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<p class="small muted" style="margin:0;padding:12px 18px;line-height:1.5">PVE Fleet vraagt het certificaat aan en zet het op de node. De webinterface van de node herstart dan een paar seconden; VM\'s &amp; containers merken niets. ' +
      'Daarvoor heeft het token het recht <span class="mono">Sys.Modify</span> nodig (Servers &amp; verbinding › Verbinding testen › Optioneel), of een actieve root-sessie. ' +
      'Tip: zet daarna bij <em>Servers &amp; verbinding</em> de namen in plaats van de IP-adressen en zet <em>Certificaat controleren</em> aan.</p></section>';
  }

  function render(d, full) {
    data = d;
    if (d.base_domain !== undefined && (full || !document.getElementById('c-prov'))) base = d.base_domain || base;
    // Het providerformulier alleen de eerste keer tekenen: zo raak je niets kwijt wat je aan het invullen bent.
    if (full || !document.getElementById('c-prov')) {
      root.innerHTML = '<div style="display:flex;flex-direction:column;gap:16px">' + (d.demo ? '<div class="banner info"><div class="txt"><div class="desc" style="margin:0">Demomodus: er worden geen echte certificaten aangevraagd. "Aanvragen" maakt een demo-certificaat.</div></div></div>' : '') +
        '<div id="c-prov">' + providerCard(d) + '</div><div id="c-fleet"></div><div id="c-nodes"></div></div>';
    }
    document.getElementById('c-fleet').innerHTML = fleetCard(d);
    var doms = {};
    Array.prototype.forEach.call(root.querySelectorAll('[data-dom]'), function (el) { if (el.getAttribute('data-touched')) doms[el.getAttribute('data-dom')] = el.value; });
    document.getElementById('c-nodes').innerHTML = nodesCard(d);
    Object.keys(doms).forEach(function (k) { var el = root.querySelector('[data-dom="' + k + '"]'); if (el) { el.value = doms[k]; el.setAttribute('data-touched', '1'); } });
  }
  function load() { return PF.getJSON('/api/certs').then(render, function (err) { root.innerHTML = '<div class="banner crit"><div class="txt">' + e(err.message) + '</div></div>'; }); }

  // ------------------------------------------------------------ taak volgen
  function follow(jobId, title) {
    var timer = null;
    return PFA.modal({ title: title, wide: true, body: '<pre class="log" id="job-log" style="max-height:50vh">Bezig…</pre><div id="job-st" class="small" style="margin-top:8px"></div>', confirm: null, cancelLabel: 'Sluiten',
      onRender: function () {
        function tick() {
          PF.getJSON('/api/certs/jobs/' + jobId).then(function (j) {
            var el = document.getElementById('job-log'); if (!el) { clearInterval(timer); return; }
            el.textContent = j.log.join('\n') || 'Bezig…'; el.scrollTop = el.scrollHeight;
            var stt = document.getElementById('job-st');
            if (j.status === 'running') stt.innerHTML = '<span class="spinner" aria-hidden="true"></span> Bezig. Wachten op DNS kan een paar minuten duren; je mag dit venster sluiten.';
            else { clearInterval(timer); stt.innerHTML = j.status === 'ok' ? '<span class="c-ok">Gelukt.</span>' : '<span class="c-crit">Mislukt: ' + e(j.error || '') + '</span>'; load(); }
          }, function () {});
        }
        tick(); timer = setInterval(tick, 2000);
      } }).then(function () { clearInterval(timer); load(); });
  }

  function uploadModal(title, onSubmit) {
    return PFA.modal({ title: title, wide: true,
      body: '<p class="muted" style="margin:0 0 6px;line-height:1.5">Plak het certificaat en de sleutel als PEM-tekst, of kies de bestanden. Heb je een tussencertificaat (chain / CA-bundle), plak die dan ook.</p>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:4px"><label class="btn ghost" style="cursor:pointer">Certificaat-bestand…<input type="file" data-f="cert" accept=".crt,.pem,.cer" hidden></label>' +
        '<label class="btn ghost" style="cursor:pointer">Chain-bestand…<input type="file" data-f="chain" accept=".crt,.pem,.cer,.ca-bundle" hidden></label>' +
        '<label class="btn ghost" style="cursor:pointer">Sleutel-bestand…<input type="file" data-f="key" accept=".key,.pem" hidden></label></div>',
      fields: [{ name: 'cert', label: 'Certificaat', type: 'textarea', placeholder: '-----BEGIN CERTIFICATE-----', mono: true },
        { name: 'chain', label: 'Tussencertificaat (optioneel)', type: 'textarea', placeholder: '-----BEGIN CERTIFICATE-----', mono: true },
        { name: 'key', label: 'Sleutel (onversleuteld)', type: 'textarea', placeholder: '-----BEGIN PRIVATE KEY-----', mono: true }],
      confirm: 'Inlezen', kind: 'primary',
      onRender: function (rootEl) {
        Array.prototype.forEach.call(rootEl.querySelectorAll('input[type=file]'), function (inp) {
          inp.addEventListener('change', function () {
            var file = inp.files && inp.files[0]; if (!file) return;
            var rd = new FileReader();
            rd.onload = function () { var ta = rootEl.querySelector('[name="' + inp.getAttribute('data-f') + '"]'); if (ta) ta.value = String(rd.result).trim(); };
            rd.readAsText(file);
          });
        });
      },
      validate: function (v) {
        if (v.cert.indexOf('BEGIN CERTIFICATE') < 0) return 'Plak het certificaat (begint met -----BEGIN CERTIFICATE-----)';
        if (v.key.indexOf('PRIVATE KEY') < 0) return 'Plak de sleutel (begint met -----BEGIN ... PRIVATE KEY-----)';
        if (/ENCRYPTED/.test(v.key)) return 'De sleutel is versleuteld met een wachtwoord. Haal het wachtwoord eraf: openssl pkey -in sleutel.key -out sleutel-open.key';
        return null;
      },
      submit: onSubmit });
  }

  function saveProvider(form) {
    var v = { type: form.type.value, login: form.login.value, private_key: form.private_key.value, global_key: form.global_key.checked, token: form.token.value };
    return PF.postJSON('/api/certs/settings', { email: form.email.value, staging: form.staging.checked, provider: v });
  }

  root.addEventListener('change', function (ev) {
    var t = ev.target;
    if (t.name === 'type' && t.form && t.form.id === 'prov') {
      Array.prototype.forEach.call(root.querySelectorAll('.pv'), function (el) { el.style.display = el.classList.contains('pv-' + t.value) ? 'grid' : 'none'; });
    }
    if (t.id === 'base') { setBase(t.value.trim().toLowerCase()); if (data) render(data); }
    if (t.hasAttribute('data-dom')) t.setAttribute('data-touched', '1');
    if (t.hasAttribute('data-fs')) {
      var body = {}; body[t.getAttribute('data-fs')] = t.checked;
      PF.postJSON('/api/certs/fleet/settings', body).then(function () { PFA.toast('Opgeslagen'); setTimeout(load, 500); }, function (err) { PFA.toast(err.message, true); });
    }
    if (t.hasAttribute('data-nar')) {
      var k = t.getAttribute('data-nar').split('/');
      PF.postJSON('/api/certs/node/settings', { server: k[0], node: k.slice(1).join('/'), auto_renew: t.checked }).then(function () { PFA.toast('Opgeslagen'); });
    }
  });
  root.addEventListener('submit', function (ev) {
    if (ev.target.id !== 'prov') return;
    ev.preventDefault();
    saveProvider(ev.target).then(function () { PFA.toast('Opgeslagen'); PF.getJSON('/api/certs').then(function (d) { render(d, true); }); }, function (err) { PFA.toast(err.message, true); });
  });
  root.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-c]') : null;
    if (!b || !data) return;
    var a = b.getAttribute('data-c');
    if (a === 'ptest') {
      var res = document.getElementById('ptest-res'); res.textContent = 'Testen…'; res.className = 'small muted';
      return saveProvider(document.getElementById('prov')).then(function () { return PF.postJSON('/api/certs/provider-test'); }).then(function (r) {
        res.className = 'small c-ok'; res.textContent = '✓ Verbonden. Domeinen: ' + (r.zones.join(', ') || '(geen)');
        if (!base && r.zones.length) { setBase(r.zones[0]); load(); }
      }, function (err) { res.className = 'small c-crit'; res.textContent = err.message; });
    }
    if (a === 'fleet-le') {
      var def = (data.fleet.domains || []).join(', ') || ('fleet.' + (base || 'jouwdomein.nl'));
      return PFA.modal({ title: 'Certificaat voor PVE Fleet aanvragen', body: '<p class="muted" style="margin:0 0 8px;line-height:1.5">Let\'s Encrypt controleert de naam via ' + e(data.provider.type || 'je DNS-provider') + '. Dat duurt meestal 1 tot 5 minuten. Meer namen? Scheid ze met een komma.</p>',
        fields: [{ name: 'domains', label: 'Domeinnaam', value: def, mono: true }], confirm: 'Aanvragen', kind: 'primary' }).then(function (v) {
        if (!v) return;
        PF.postJSON('/api/certs/fleet/issue', { domains: v.domains }).then(function (r) { follow(r.job, 'Let\'s Encrypt voor PVE Fleet'); }, function (err) { PFA.toast(err.message, true); });
      });
    }
    if (a === 'fleet-up') return uploadModal('Eigen certificaat voor PVE Fleet', function (v) {
      return PF.postJSON('/api/certs/fleet/upload', v).then(function () { PFA.toast('Certificaat ingelezen; https is actief'); setTimeout(load, 800); });
    });
    if (a === 'fleet-del') return PFA.modal({ title: 'Certificaat van PVE Fleet verwijderen?', body: 'https gaat uit; PVE Fleet blijft bereikbaar via http op poort 8080.', confirm: 'Verwijderen', kind: 'danger' })
      .then(function (v) { if (v) PF.send('DELETE', '/api/certs/fleet').then(function () { PFA.toast('Verwijderd'); load(); }); });
    if (a === 'fleet-port') {
      return PF.postJSON('/api/certs/fleet/settings', { https_port: document.getElementById('f-port').value }).then(function () { PFA.toast('Poort opgeslagen'); setTimeout(load, 800); }, function (err) { PFA.toast(err.message, true); });
    }
    var k = b.getAttribute('data-k');
    var n = k ? data.nodes.filter(function (x) { return x.server + '/' + x.node === k; })[0] : null;
    var domOf = function (key) { var el = root.querySelector('[data-dom="' + key + '"]'); return el ? el.value.trim().toLowerCase() : ''; };
    if (a === 'node-le' && n) {
      var dom = domOf(k);
      if (!dom) return PFA.toast('Vul eerst de naam van de node in (bijvoorbeeld ' + n.node + '.romioinc.nl)', true);
      return PFA.modal({ title: 'Certificaat voor ' + n.node, body: '<p style="margin:0 0 8px">Naam: <span class="mono">' + e(dom) + '</span>. PVE Fleet vraagt het certificaat aan bij Let\'s Encrypt en zet het op ' + e(n.node) + '. De webinterface van de node herstart even.</p>',
        confirm: 'Aanvragen', kind: 'primary' }).then(function (v) {
        if (!v) return;
        PF.postJSON('/api/certs/node/issue', { server: n.server, node: n.node, domain: dom }).then(function (r) { follow(r.job, 'Let\'s Encrypt voor ' + n.node); }, function (err) { PFA.toast(err.message, true); });
      });
    }
    if (a === 'node-up' && n) return uploadModal('Eigen certificaat voor ' + n.node, function (v) {
      return PF.postJSON('/api/certs/node/upload', { server: n.server, node: n.node, cert: v.cert, chain: v.chain, key: v.key }).then(function () { PFA.toast('Certificaat geplaatst op ' + n.node); setTimeout(load, 3000); });
    });
    if (a === 'node-all') {
      var list = data.nodes.filter(function (x) { return x.status === 'online' && domOf(x.server + '/' + x.node); });
      if (!list.length) return PFA.toast('Vul eerst het basisdomein of de namen in', true);
      return PFA.modal({ title: 'Certificaten voor ' + list.length + ' nodes aanvragen?', body: '<ul style="margin:0;padding-left:18px">' + list.map(function (x) { return '<li class="mono">' + e(domOf(x.server + '/' + x.node)) + '</li>'; }).join('') + '</ul><p class="small muted" style="margin:8px 0 0">Na elkaar; per node duurt het een paar minuten.</p>',
        confirm: 'Aanvragen', kind: 'primary' }).then(function (v) {
        if (!v) return;
        var i = 0;
        (function next() {
          if (i >= list.length) return;
          var x = list[i++];
          PF.postJSON('/api/certs/node/issue', { server: x.server, node: x.node, domain: domOf(x.server + '/' + x.node) }).then(function (r) {
            follow(r.job, 'Let\'s Encrypt voor ' + x.node + ' (' + i + ' van ' + list.length + ')').then(next);
          }, function (err) { PFA.toast(x.node + ': ' + err.message, true); next(); });
        })();
      });
    }
  });
  load();
})();
