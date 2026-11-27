(function () {
  var e = PF.esc;
  var conf = null, fleet = null;
  function $(id) { return document.getElementById(id); }

  function statusFor(name) {
    if (!fleet || conf.demo) return null;
    return (fleet.groups || []).filter(function (g) { return g.server === name; })[0] || null;
  }

  function render() {
    if (!conf) return;
    $('cfgpath').textContent = conf.config_path;
    $('cfgerr').innerHTML = conf.config_error ? '<div class="banner crit" role="alert"><div class="txt"><div class="ttl">Probleem in config.yml</div><div class="desc">' + e(conf.config_error) +
      '<br>Servers met een fout worden overgeslagen. Bewerk ze hier opnieuw, of pas het bestand aan en herstart het dashboard.</div></div></div>' : '';
    if (conf.demo) {
      $('demo').innerHTML = '<div class="banner warn"><div class="txt"><div class="ttl">Demomodus staat aan</div><div class="desc">Het dashboard toont voorbeelddata. ' +
        (conf.servers.length ? 'Je hebt al servers ingesteld; zet de demo uit om die te gebruiken.' : 'Voeg een server toe; de demo gaat dan vanzelf uit.') + '</div></div>' +
        (conf.servers.length ? '<button type="button" class="btn lg primary" data-demo="0">Demomodus uitzetten</button>' : '') + '</div>';
    } else {
      $('demo').replaceChildren();
    }
    var rows = conf.servers.map(function (s) {
      var g = statusFor(s.name);
      var kind = g && g.reachable ? (g.kind === 'cluster' ? 'Cluster <span class="muted">(' + e(g.name) + ')</span>' : 'Losse node')
        : (s.type === 'cluster' ? 'Cluster' : (s.type === 'standalone' ? 'Losse node' : '<span class="muted">nog onbekend</span>'));
      var st = conf.demo ? '<span class="muted">— (demo)</span>' : (!g ? '<span class="muted">Verbinden…</span>'
        : (g.reachable ? '<span class="st c-ok"><span class="dot b-ok"></span>Verbonden · ' + g.nodes_online + '/' + g.nodes_total + ' nodes</span>' : '<span class="st c-crit"><span class="dot b-crit"></span>Onbereikbaar</span>'));
      var msg = g && g.error ? e(g.error) + (PF.errorHint(g.error) ? '<div style="color:var(--text);margin-top:4px">' + e(PF.errorHint(g.error)) + '</div>' : '')
        : (g && g.reachable ? 'OK · ' + PF.clock(g.polled) + (g.active_host ? ' via ' + e(g.active_host) : '') : '');
      return '<tr><td class="mono" style="font-weight:500">' + e(s.name) + '</td><td>' + kind + '</td>' +
        '<td class="mono small">' + s.hosts.map(e).join('<br>') + (s.port !== 8006 ? '<br><span class="muted">poort ' + s.port + '</span>' : '') + '</td>' +
        '<td>' + st + '</td><td class="small ' + (g && g.error ? 'err' : 'muted') + '" style="max-width:340px">' + msg + '</td>' +
        '<td class="r"><div class="rowacts"><button type="button" class="btn ghost" data-edit="' + e(s.name) + '">Bewerken</button>' +
        '<button type="button" class="btn danger" data-del="' + e(s.name) + '">Verwijderen</button></div></td></tr>';
    }).join('');
    $('rows').innerHTML = rows || '<tr><td colspan="6"><div class="empty">Nog geen servers. Klik op <strong>+ Server toevoegen</strong>.</div></td></tr>';
    if (!conf.demo) {
      $('rows').insertAdjacentHTML('afterend', '');
    }
    var foot = document.getElementById('demo-on');
    if (!foot) {
      foot = document.createElement('p');
      foot.id = 'demo-on'; foot.className = 'small muted'; foot.style.margin = '0';
      $('rows').closest('section').insertAdjacentElement('afterend', foot);
    }
    foot.innerHTML = conf.demo ? '' : 'Wil je het dashboard even met voorbeelddata bekijken? <button type="button" class="btn ghost" data-demo="1" style="height:30px;margin-left:6px">Demomodus aanzetten</button> <span>(je servers blijven bewaard)</span>';
  }

  function load() {
    return Promise.all([PF.getJSON('/api/servers'), PF.getJSON('/api/fleet').catch(function () { return null; })]).then(function (r) {
      conf = r[0]; fleet = r[1]; render();
    }, function (err) { $('rows').innerHTML = '<tr><td colspan="6" class="err">Kan servers niet laden: ' + e(err.message) + '</td></tr>'; });
  }

  // ------------------------------------------------------------ testen
  function testHtml(r) {
    var h = '<div style="display:flex;flex-direction:column;gap:8px;padding:12px 14px;border-radius:10px;border:1px solid ' + (r.ok ? '#2E5A43' : 'var(--crit-line)') + ';background:' + (r.ok ? '#13241B' : 'var(--crit-bg)') + '">';
    if (r.ok) {
      h += '<div style="font-weight:600;color:#A6E3C4">Verbinding gelukt' + (r.version ? ' · Proxmox VE ' + e(r.version) : '') + '</div>';
      h += '<div style="color:var(--text)">' + (r.kind === 'cluster' ? 'Cluster <strong class="mono">' + e(r.cluster_name) + '</strong> met ' + r.nodes.length + ' nodes' + (r.quorate === false ? ' <span class="c-warn">(geen quorum)</span>' : '') : 'Losse node <strong class="mono">' + e((r.nodes[0] || {}).name || '') + '</strong>') +
        (r.guests !== undefined ? ' · ' + r.guests + ' VM\'s & containers zichtbaar' : '') + '</div>';
      if (r.nodes.length > 1) h += '<div class="small mono" style="color:var(--text2)">' + r.nodes.map(function (n) { return (n.online ? '● ' : '○ ') + e(n.name) + (n.ip ? ' (' + e(n.ip) + ')' : ''); }).join(' &nbsp; ') + '</div>';
    } else {
      h += '<div style="font-weight:600;color:var(--crit-text)">Verbinding mislukt</div><div class="mono small" style="color:var(--crit-text);overflow-wrap:anywhere">' + e(r.error || '') + '</div>';
      var hint = PF.errorHint(r.error);
      if (hint) h += '<div style="color:var(--text)">' + e(hint) + '</div>';
    }
    if (r.hosts && r.hosts.length > 1) {
      h += '<div class="small" style="display:flex;flex-wrap:wrap;gap:4px 14px">' + r.hosts.map(function (x) {
        return '<span class="mono ' + (x.ok ? 'c-ok' : 'c-crit') + '" title="' + e(x.error || 'OK') + '">' + (x.ok ? '✓ ' : '✗ ') + e(x.host) + '</span>';
      }).join('') + '</div>';
    }
    (r.warnings || []).forEach(function (w) { h += '<div class="small c-warn">' + e(w) + '</div>'; });
    if (r.perms) {
      var anyMissing = r.perms.some(function (x) { return !x.ok && !x.optional; });
      h += '<div style="margin-top:4px;padding-top:8px;border-top:1px solid ' + (r.ok ? '#2E5A43' : 'var(--crit-line)') + '"><div style="font-weight:600;color:var(--text);margin-bottom:6px">Rechten van het token' +
        (r.privsep === true ? ' <span class="small muted" style="font-weight:400">· Privilege Separation aan</span>' : (r.privsep === false ? ' <span class="small muted" style="font-weight:400">· Privilege Separation uit</span>' : '')) + '</div>' +
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:4px 16px">' + r.perms.map(function (x) {
          return '<div class="small" title="' + e(x.privs.join(', ')) + '"><span class="' + (x.ok ? 'c-ok' : (x.optional ? 'muted' : 'c-crit')) + '">' + (x.ok ? '✓' : (x.optional ? '○' : '✗')) + '</span> ' + e(x.label) +
            (x.ok ? '' : ' <span class="muted mono">(mist ' + e(x.missing.join(', ')) + ')</span>') + '</div>';
        }).join('') + '</div>';
      if (anyMissing && r.commands) {
        h += '<div class="small" style="margin:10px 0 6px;color:var(--text)">Plak dit in de shell van één node (Datacenter › node › Shell). De rechten gelden daarna voor het hele cluster en werken meteen:</div>' +
          '<pre class="log" style="max-height:none;user-select:all" id="perm-cmds">' + e(r.commands) + '</pre>' +
          '<button type="button" class="btn ghost" data-copy="perm-cmds" style="margin-top:6px">Kopiëren</button>';
      } else if (!anyMissing) {
        h += '<div class="small c-ok" style="margin-top:6px">Alles in orde: het dashboard kan alle acties uitvoeren.</div>';
      }
      if (r.optional_commands) {
        var om = r.perms.filter(function (x) { return x.optional && !x.ok; }).map(function (x) { return x.key; });
        var jobsM = om.indexOf('jobs') >= 0, restM = om.indexOf('restore') >= 0;
        var sum = jobsM && restM ? 'Optioneel: updates lezen, back-upjobs aanpassen en back-ups terugzetten' : (restM ? 'Optioneel: back-ups terugzetten' : 'Optioneel: openstaande updates lezen en back-upjobs aanpassen');
        h += '<details style="margin-top:10px"><summary class="small" style="cursor:pointer;color:var(--text2)">' + e(sum) + '</summary>' +
          (jobsM ? '<div class="small muted" style="margin:8px 0 6px;line-height:1.5">Proxmox vraagt hiervoor het recht <span class="mono">Sys.Modify</span> op het hele datacenter. Dat recht mag ook andere instellingen wijzigen (bijv. netwerk). Zonder dit recht ziet PVE Fleet niet welke updates er klaarstaan (wel of een node achterloopt op de andere nodes), en kun je back-upjobs alleen bekijken en nu uitvoeren.</div>' : '') +
          (restM ? '<div class="small muted" style="margin:8px 0 6px;line-height:1.5">Terugzetten als nieuwe VM of container vraagt onder meer <span class="mono">VM.Allocate</span> en, voor een andere bridge, <span class="mono">SDN.Use</span>. Zonder deze rechten kun je back-ups wel bekijken en maken, maar niet terugzetten.</div>' : '') +
          '<pre class="log" style="max-height:none;user-select:all" id="perm-opt">' + e(r.optional_commands) + '</pre>' +
          '<button type="button" class="btn ghost" data-copy="perm-opt" style="margin-top:6px">Kopiëren</button></details>';
      }
      h += '</div>';
    } else if (r.perms_error) {
      h += '<div class="small c-warn">Kon de rechten niet uitlezen: ' + e(r.perms_error) + '</div>';
    }
    if (r.suggest_hosts) h += '<div class="small" style="color:var(--text)">Het cluster heeft ook nodes op andere adressen. <button type="button" class="btn ghost" data-usehosts="1" style="height:30px">Alle node-IP\'s overnemen</button></div>';
    return h + '</div>';
  }

  // ------------------------------------------------------------ formulier
  function form(existing) {
    var s = existing || { name: '', type: null, hosts: [], port: 8006, token_id: 'dashboard@pve!fleet', has_secret: false, verify_ssl: false, wol_mac: '', wol: {} };
    var lastTest = null;
    var wolLines = Object.keys(s.wol || {}).map(function (k) { return k + '=' + s.wol[k]; }).join('\n');
    PFA.modal({
      title: existing ? s.name + ' bewerken' : 'Server toevoegen', wide: true,
      body: 'Vul de gegevens in en klik op <strong>Verbinding testen</strong>. Het dashboard herkent zelf of het een cluster of een losse node is.',
      fields: [
        { name: 'name', label: 'Naam in het dashboard', value: s.name, placeholder: 'bijv. cluster-home of pve-garage', maxlength: 40, mono: true },
        { name: 'type', label: 'Soort', type: 'radio', value: s.type || 'cluster', options: [
          { value: 'cluster', label: 'Cluster', meta: 'meerdere nodes, één token' },
          { value: 'standalone', label: 'Losse node', meta: 'één server zonder cluster' }] },
        { name: 'hosts', label: 'IP-adressen of hostnamen', type: 'textarea', value: s.hosts.join('\n'), mono: true, placeholder: '192.168.1.90\n192.168.1.91',
          hint: 'één per regel; bij een cluster liefst alle nodes, zodat het dashboard doorschakelt als er een uitvalt' },
        { name: 'port', label: 'Poort', value: String(s.port || 8006), mono: true, maxlength: 5 },
        { name: 'token_id', label: 'Token-ID', value: s.token_id, mono: true, placeholder: 'dashboard@pve!fleet', hint: 'vorm: gebruiker@realm!tokennaam' },
        { name: 'token_secret', label: 'Token-geheim', type: 'password', mono: true, value: '',
          placeholder: s.has_secret ? '•••••••• (leeg laten = ongewijzigd)' : 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' },
        { name: 'verify_ssl', label: 'Certificaat controleren (alleen als je een geldig certificaat hebt)', type: 'checkbox', value: s.verify_ssl },
        { name: 'wol_mac', label: 'Wake-on-LAN: MAC-adres (losse node, optioneel)', value: s.wol_mac || '', mono: true, placeholder: 'AA:BB:CC:DD:EE:FF' },
        { name: 'wol', label: 'Wake-on-LAN per clusternode (optioneel)', type: 'textarea', value: wolLines, mono: true, placeholder: 'pve-01=AA:BB:CC:DD:EE:01',
          hint: 'meestal niet nodig: Proxmox wekt clusternodes zelf als het MAC-adres op de node is ingesteld' }
      ],
      extra: [{ label: 'Verbinding testen', onClick: function (v, api) {
        api.button.disabled = true;
        api.info('<div class="muted" style="display:flex;gap:8px;align-items:center"><span class="spinner"></span>Verbinden… (max. ±10 s)</div>');
        PF.postJSON('/api/servers/test', { original: existing ? existing.name : null, server: toServer(v) }).then(function (r) {
          api.button.disabled = false;
          lastTest = r;
          api.info(testHtml(r));
          if (r.ok) {
            api.set('type', r.kind);
            if (!v.name) api.set('name', r.kind === 'cluster' ? (r.cluster_name || '') : ((r.nodes[0] || {}).name || ''));
          }
          var b = document.querySelector('[data-usehosts]');
          if (b) b.addEventListener('click', function () { api.set('hosts', r.suggest_hosts.join('\n')); b.parentNode.textContent = 'Overgenomen: ' + r.suggest_hosts.join(', '); });
        }, function (err) { api.button.disabled = false; api.info('<div class="errbox">' + e(err.message) + '</div>'); });
      } }],
      confirm: existing ? 'Opslaan' : 'Toevoegen',
      validate: function (v) {
        if (!v.hosts.trim()) return 'Vul minstens één IP-adres of hostnaam in.';
        if (!/^[^@!\s]+@[^@!\s]+![^@!\s]+$/.test(v.token_id)) return 'Token-ID moet de vorm gebruiker@realm!tokennaam hebben, bijv. dashboard@pve!fleet.';
        if (!v.token_secret && !(existing && existing.has_secret)) return 'Vul het token-geheim in.';
        if (v.name && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(v.name)) return 'Naam: letters, cijfers, punt, - of _ (max. 40 tekens).';
        if (!/^\d{1,5}$/.test(v.port)) return 'Ongeldige poort.';
        return null;
      },
      submit: function (v) {
        return PF.postJSON('/api/servers/save', { original: existing ? existing.name : null, server: toServer(v) }).then(function (r) {
          PFA.toast(r.demo_turned_off ? 'Opgeslagen. Demomodus is uitgezet; het dashboard verbindt nu met je eigen servers.' : 'Opgeslagen in config.yml');
          setTimeout(load, 300); setTimeout(load, 4000); setTimeout(load, 12000);
        });
      }
    });
  }
  function copyText(text, btn) {
    function done(ok) { btn.textContent = ok ? 'Gekopieerd ✓' : 'Selecteer en kopieer met Ctrl+C'; setTimeout(function () { btn.textContent = 'Kopiëren'; }, 2500); }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      return;
    }
    // http in je eigen netwerk: oude manier
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (x) { ok = false; }
    document.body.removeChild(ta);
    done(ok);
  }
  function toServer(v) {
    return { name: v.name || '', type: v.type, hosts: v.hosts, port: v.port, token_id: v.token_id, token_secret: v.token_secret,
      verify_ssl: v.verify_ssl, wol_mac: v.wol_mac, wol: v.wol };
  }

  document.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t.closest) return;
    var cp = t.closest('[data-copy]');
    if (cp) return copyText(document.getElementById(cp.getAttribute('data-copy')).textContent, cp);
    if (t.closest('#add')) return form(null);
    var b = t.closest('[data-edit]');
    if (b) return form(conf.servers.filter(function (s) { return s.name === b.getAttribute('data-edit'); })[0]);
    b = t.closest('[data-del]');
    if (b) {
      var name = b.getAttribute('data-del');
      return PFA.modal({ title: name + ' verwijderen?', body: 'De server verdwijnt uit het dashboard en uit config.yml. Op de server zelf verandert niets. (De vorige config blijft bewaard als config.yml.bak.)',
        confirm: 'Verwijderen', kind: 'danger',
        submit: function () { return PF.send('DELETE', '/api/servers/' + encodeURIComponent(name)); } }).then(function (v) { if (v) { PFA.toast('Verwijderd'); load(); } });
    }
    b = t.closest('[data-demo]');
    if (b) {
      var on = b.getAttribute('data-demo') === '1';
      PF.postJSON('/api/servers/demo', { demo: on }).then(function () {
        PFA.toast(on ? 'Demomodus staat aan' : 'Demomodus staat uit');
        setTimeout(function () { location.reload(); }, 600);
      }, function (err) { PFA.toast(err.message, true); });
    }
  });

  var live = document.getElementById('live');
  load();
  setInterval(function () { if (!window.PFA_MODAL_OPEN) load(); }, 10000);
  if (live) live.querySelector('.txt').textContent = '';
})();
