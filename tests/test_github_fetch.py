"""Test: Apps - nieuwste versie ophalen van GitHub (zonder echt netwerk). Gebruik: python tests/test_github_fetch.py"""
import io, json, os, shutil, sys, tempfile, zipfile
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
D = os.path.join(tempfile.gettempdir(), 'pvef-ghtest'); shutil.rmtree(D, ignore_errors=True); os.makedirs(D)
os.environ['PVEF_DATA'] = D
from app import create_app, appdeploy as AD
bad = []
def check(n, ok, extra=''): print('OK  ' if ok else 'FOUT', n, extra); (None if ok else bad.append(n))

def make_zip(version):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:   # zoals GitHub: één bovenste map met de commit in de naam
        z.writestr('romio23-modelarr-abc123/app.py', 'VERSION = "%s"\n' % version)
        z.writestr('romio23-modelarr-abc123/pvefleet-app.json', json.dumps(dict(name='modelarr', version=version, type='systemd', dir='/opt/modelarr', service='modelarr', venv='venv', github='romio23/modelarr')))
    return b.getvalue()

class R:
    def __init__(s, code, js=None, data=b''): s.status_code, s._js, s._d = code, js, data
    def json(s): return s._js
    def iter_content(s, n): yield s._d

CALLS = []
state = dict(release=None, tags=[], zip=make_zip('1.4.1'), zip_code=200)
def fake_get(url, **kw):
    CALLS.append(url)
    if url.endswith('/releases/latest'):
        return R(200, dict(tag_name=state['release'])) if state['release'] else R(404, {})
    if url.endswith('/tags'): return R(200, [dict(name=t) for t in state['tags']])
    if '/zipball/' in url: return R(state['zip_code'], None, state['zip'])
    return R(404, {})
AD.requests.get = fake_get

app = create_app(start_collector=False); app.testing = True
AD._demo = lambda a: False
c = app.test_client(); H = {'X-PVEF': '1'}
c.post('/setup', data=dict(username='beheer', password='wachtwoord1', password2='wachtwoord1', pin='1234', pin2='1234'))   # eerste beheerder, zoals in de browser
post = lambda body: c.post('/api/apps/github', json=body, headers=H)

r = post({'repo': 'niet goed'}); check('ongeldige repository wordt geweigerd', r.status_code == 400, r.get_json())
r = post({'repo': 'romio23/modelarr', 'ref': '../x'}); check('ongeldige tag wordt geweigerd', r.status_code == 400)
r = post({'repo': 'romio23/modelarr'}); check('geen release en geen tag: duidelijke melding', r.status_code == 502 and 'release of tag' in r.get_json()['error'], r.get_json())
state['tags'] = ['v1.4.1', 'v1.4.0']
r = post({'repo': 'https://github.com/romio23/modelarr.git'}); j = r.get_json()
check('nieuwste tag opgehaald (url mag als adres)', r.status_code == 200 and j['github'] == dict(repo='romio23/modelarr', ref='v1.4.1', source='tag') and any('/zipball/v1.4.1' in u for u in CALLS), j)
check('  manifest uit de zip, map van GitHub genegeerd, github-adres erin', j['has_manifest'] and j['manifest']['version'] == '1.4.1' and j['manifest']['github'] == 'romio23/modelarr' and j['folder'] == 'romio23-modelarr-abc123', j)
rs = c.post(f"/api/apps/upload/{j['upload']}/save", json={'manifest': j['manifest']}, headers=H); rel = rs.get_json()
check('  opslaan geeft een bewaarde versie', rs.status_code == 200 and rel.get('ok'), rel)
a = c.get('/api/apps').get_json()
check('  Apps-lijst kent het GitHub-adres van de app', a['apps']['modelarr']['github'] == 'romio23/modelarr' and a['releases'][0]['manifest']['version'] == '1.4.1', a['apps'].get('modelarr'))
state['release'] = 'v1.5.0'; state['zip'] = make_zip('1.5.0')
j = post({'repo': 'romio23/modelarr'}).get_json()
check('release gaat voor tag', j['github']['ref'] == 'v1.5.0' and j['github']['source'] == 'release' and j['manifest']['version'] == '1.5.0', j.get('github'))
j = post({'repo': 'romio23/modelarr', 'ref': 'main'}).get_json()
check('zelf een tak opgeven (main) werkt ook', j['github']['ref'] == 'main' and any('/zipball/main' in u for u in CALLS))
state['zip_code'] = 404
r = post({'repo': 'romio23/modelarr', 'ref': 'bestaat-niet'}); check('onbekende tak: melding', r.status_code == 404 and 'bestaat niet' in r.get_json()['error'])
state['zip_code'] = 200; state['zip'] = b'dit is geen zip'
r = post({'repo': 'romio23/modelarr', 'ref': 'main'}); check('kapotte download: melding, geen crash', r.status_code == 400 and 'zip' in r.get_json()['error'], r.get_json())
try:
    AD.validate_manifest(dict(name='x', version='1', type='static', dir='/srv/x', github='geen adres')); ok = False
except ValueError: ok = True
check('manifest met een ongeldig github-adres wordt geweigerd', ok)
print('\nAlles goed.' if not bad else f'\n{len(bad)} fout(en)'); sys.exit(1 if bad else 0)
