#!/usr/bin/env python3
"""Genereert app/static/js/i18n-en.js uit i18n/en.tsv (Nederlands ||| Engels per regel).
Run: python3 tools/build_i18n.py"""
import json, os, re, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = os.path.join(root, 'i18n', 'en.tsv')
pats_src = os.path.join(root, 'i18n', 'patterns.json')
exact, phrases = {}, {}
for ln in open(src, encoding='utf-8'):
    ln = ln.rstrip('\n')
    if not ln.strip() or ln.startswith('#') or '|||' not in ln:
        continue
    nl, en = ln.split('|||', 1)
    nl = nl.strip(); en = en.strip()
    if not nl or nl == en:
        continue
    exact[nl] = en
    if len(nl) >= 12 and ' ' in nl:
        phrases[nl] = en
patterns = json.load(open(pats_src, encoding='utf-8')) if os.path.exists(pats_src) else []
post_src = os.path.join(root, 'i18n', 'post.json')
post = json.load(open(post_src, encoding='utf-8')) if os.path.exists(post_src) else []
ph = sorted(phrases.items(), key=lambda kv: -len(kv[0]))
out = '// Gegenereerd door tools/build_i18n.py uit i18n/en.tsv: niet met de hand bewerken.\nwindow.I18N_EN = ' + json.dumps({'exact': exact, 'phrases': ph, 'patterns': patterns, 'post': post}, ensure_ascii=False, separators=(',', ':')) + ';\n'
open(os.path.join(root, 'app', 'static', 'js', 'i18n-en.js'), 'w', encoding='utf-8').write(out)
print(len(exact), 'teksten,', len(ph), 'zinsdelen,', len(patterns), 'patronen')
