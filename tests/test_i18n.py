"""i18n: the dictionary builds, the generated file is current and the JS translation layer works.
Run: python tests/test_i18n.py"""
import json, os, re, shutil, subprocess, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
bad = []
def check(n, ok, extra=''):
    print('OK  ' if ok else 'FAIL', n, extra); (None if ok else bad.append(n))

# 1. en.tsv: well-formed and no conflicting duplicates
seen = {}
for i, ln in enumerate(open(os.path.join(ROOT, 'i18n', 'en.tsv'), encoding='utf-8'), 1):
    ln = ln.rstrip('\n')
    if not ln.strip() or ln.startswith('#'):
        continue
    if '|||' not in ln:
        check(f'en.tsv line {i} has |||', False, ln[:60]); continue
    nl, en = [x.strip() for x in ln.split('|||', 1)]
    if nl in seen and seen[nl] != en:
        check(f'en.tsv duplicate with different translation: {nl[:50]}', False, f'{seen[nl][:30]} / {en[:30]}')
    seen[nl] = en
check('en.tsv is well-formed and consistent', not bad)
check('dictionary has enough entries', len(seen) > 1000, str(len(seen)))

# 2. generated file is current
gen = os.path.join(ROOT, 'app', 'static', 'js', 'i18n-en.js')
before = open(gen, encoding='utf-8').read()
subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'build_i18n.py')], check=True, stdout=subprocess.DEVNULL)
after = open(gen, encoding='utf-8').read()
check('i18n-en.js is current (run tools/build_i18n.py)', before == after)

# 3. JS translation layer (needs node)
node = shutil.which('node')
if node:
    js = r"""
global.window = {}; global.document = undefined;
const fs = require('fs'); const R = process.argv[1];
eval(fs.readFileSync(R + '/app/static/js/i18n-en.js', 'utf8'));
global.localStorage = {getItem: () => 'en'}; global.navigator = {language: 'en'};
eval(fs.readFileSync(R + '/app/static/js/i18n.js', 'utf8'));
const t = s => window.I18N.tr(s);
const out = [t('Opslaan'), t('  Annuleren '), t('3 VM/CT actief'), t('Geen servers ingesteld.'), t('elke dag om 02:00'), t('Menu voor beheer'), t('Naam in het dashboard'), t('onbekende tekst die er niet in staat'), t("Alle VM's & containers bekijken")];
console.log(JSON.stringify(out));
"""
    r = subprocess.run([node, '-e', js, ROOT], capture_output=True, text=True)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = []; print(r.stdout, r.stderr)
    want = ['Save', '  Cancel ', '3 VM/CT running', 'No servers configured.', 'every day at 02:00', 'Menu for beheer', 'Name in the dashboard', 'onbekende tekst die er niet in staat', 'View all VMs & containers']
    check('translation layer: exact, whitespace, patterns, unknown text left alone', o == want, str(o))
else:
    print('SKIP translation layer (node not installed)')
sys.exit(1 if bad else 0)
