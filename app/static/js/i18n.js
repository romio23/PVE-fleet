// Translation layer. Dutch is the source language; for English the text on the page itself is replaced (including text, tooltips and
// placeholders the app adds later). The dictionary lives in i18n-en.js (generated from i18n/en.tsv by tools/build_i18n.py).
// Language: the NL | EN switch, otherwise the browser language (Dutch browsers get Dutch, everything else English).
(function () {
  const EN = window.I18N_EN || {exact: {}, phrases: [], patterns: []};
  const pats = EN.patterns.map(([src, flags, rep]) => [new RegExp(src, (flags || '') + 'g'), rep]);
  const post = (EN.post || []).map(([src, flags, rep]) => [new RegExp(src, (flags || '') + 'g'), rep]);
  const saved = (() => { try { return localStorage.getItem('pvef_lang'); } catch (e) { return null; } })();
  const nav = (typeof navigator !== 'undefined' && navigator.language) || 'nl';
  const lang = saved === 'nl' || saved === 'en' ? saved : (/^nl\b/i.test(nav) ? 'nl' : 'en');
  const LOC = lang === 'en' ? 'en-GB' : 'nl-NL';

  // Vertaalt één tekst naar het Engels (l = 'en'); alles wat niet in de woordenlijst staat blijft ongewijzigd (bijv. namen van modellen).
  function translate(s, l) {
    if (l !== 'en' || typeof s !== 'string' || !s) return s;
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(s), core = m[2];
    if (!core) return s;
    const hit = EN.exact[core];
    if (hit !== undefined) return m[1] + hit + m[3];
    const sym = /^([^A-Za-zÀ-ÿ0-9(]+)([\s\S]+)$/.exec(core);  // voorafgaand symbool, bijv. ✓ ★ ⚠
    if (sym && EN.exact[sym[2].trim()] !== undefined) return m[1] + sym[1] + EN.exact[sym[2].trim()] + m[3];
    let out = core;
    for (const [re, rep] of pats) out = out.replace(re, rep);
    for (const [nl, en] of EN.phrases) if (out.includes(nl)) out = out.split(nl).join(en);
    for (const [re, rep] of post) out = out.replace(re, rep);  // opruiming na de zinsdelen (bijv. VM's → VMs)
    return out === core ? s : m[1] + out + m[3];
  }
  const tr = s => translate(s, lang);
  function setLang(l) { try { localStorage.setItem('pvef_lang', l); } catch (e) { /* geen opslag: dan blijft de browsertaal gelden */ } location.reload(); }

  const SKIP = /^(SCRIPT|STYLE|TEXTAREA|NOSCRIPT)$/, ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  const seen = typeof WeakMap !== 'undefined' ? new WeakMap() : null;
  function textNode(n) {
    const p = n.parentNode;
    if (p && SKIP.test(p.nodeName || '')) return;
    if (seen && seen.get(n) === n.nodeValue) return;  // al vertaald: niet opnieuw (voorkomt een eindeloze lus met de observer)
    const t = tr(n.nodeValue);
    if (t !== n.nodeValue) n.nodeValue = t;
    if (seen) seen.set(n, n.nodeValue);
  }
  function element(e) {
    for (const a of ATTRS) { const v = e.getAttribute && e.getAttribute(a); if (v) { const t = tr(v); if (t !== v) e.setAttribute(a, t); } }
  }
  function walk(n) {
    if (n.nodeType === 3) textNode(n);
    else if (n.nodeType === 1 && !SKIP.test(n.nodeName)) { element(n); for (const c of Array.from(n.childNodes || [])) walk(c); }
  }
  function start() {
    if (lang !== 'en' || typeof MutationObserver === 'undefined' || !document.body) return;
    document.documentElement.lang = 'en';
    walk(document.body);
    new MutationObserver(list => {
      for (const r of list) {
        if (r.type === 'childList') r.addedNodes.forEach(walk);
        else if (r.type === 'characterData') textNode(r.target);
        else if (r.type === 'attributes') element(r.target);
      }
    }).observe(document.body, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS});
  }
  if (typeof document !== 'undefined') { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start(); }
  if (lang === 'en' && typeof window !== 'undefined') {  // ook de meldingen van de browser zelf (bevestigen, invoeren)
    for (const f of ['alert', 'confirm', 'prompt']) { const o = window[f]; if (typeof o === 'function') window[f] = (m, ...rest) => o.call(window, tr(String(m)), ...rest); }
  }
  function bindSwitch() {  // knoppen NL | EN (data-lang): kies de taal, de pagina laadt opnieuw
    for (const b of document.querySelectorAll('[data-lang]')) {
      b.classList.toggle('on', b.getAttribute('data-lang') === lang);
      b.setAttribute('aria-pressed', b.getAttribute('data-lang') === lang ? 'true' : 'false');
    }
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-lang]'); if (b && b.getAttribute('data-lang') !== lang) setLang(b.getAttribute('data-lang')); });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindSwitch); else bindSwitch();
  }
  window.I18N = {lang, LOC, tr, translate, setLang, walk};
})();
