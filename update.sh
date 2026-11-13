#!/bin/sh
# PVE Fleet bijwerken zonder je instellingen te verliezen (data/, docker-compose.yml en .env blijven staan).
#
# Gebruik, in de map van PVE Fleet (waar docker-compose.yml staat):
#   sh update.sh                  -> haalt de nieuwste release (of tag) van GitHub op en bouwt de container opnieuw
#   sh update.sh --check          -> alleen kijken: wat staat erop en wat is de nieuwste versie op GitHub
#   sh update.sh v0.8.0           -> een bepaalde release of tag van GitHub (ook een tak, bijvoorbeeld main)
#   sh update.sh pad/naar.zip     -> een zip van je eigen schijf (of een uitgepakte map)
#   REPO=eigenaar/naam sh update.sh   -> andere GitHub-repository (standaard romio23/PVE-fleet)
#   GITHUB_TOKEN=... sh update.sh     -> voor een privé-repository
set -e
cd "$(dirname "$0")"
REPO="${REPO:-romio23/PVE-fleet}"
CHECK=0; ARG=""
for a in "$@"; do case "$a" in --check|-c) CHECK=1 ;; *) ARG="$a" ;; esac; done

ver() { sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$1/app/__init__.py" 2>/dev/null | head -n 1; }
fetch() {  # fetch URL BESTAND
  if command -v curl >/dev/null 2>&1; then
    if [ -n "$GITHUB_TOKEN" ]; then curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "User-Agent: pve-fleet-update" -o "$2" "$1"; else curl -fsSL -H "User-Agent: pve-fleet-update" -o "$2" "$1"; fi
  elif command -v wget >/dev/null 2>&1; then
    if [ -n "$GITHUB_TOKEN" ]; then wget -q --header="Authorization: Bearer $GITHUB_TOKEN" -O "$2" "$1"; else wget -q -O "$2" "$1"; fi
  else echo "curl of wget is nodig (sudo apt-get install -y curl)"; exit 1; fi
}
latest_tag() {  # nieuwste release, anders nieuwste tag
  T="$TMP/api.json"
  if fetch "https://api.github.com/repos/$REPO/releases/latest" "$T" 2>/dev/null; then
    tag=$(sed -n 's/.*"tag_name" *: *"\([^"]*\)".*/\1/p' "$T" | head -n 1)
    [ -n "$tag" ] && { echo "$tag"; return; }
  fi
  if fetch "https://api.github.com/repos/$REPO/tags?per_page=1" "$T" 2>/dev/null; then
    tag=$(sed -n 's/.*"name" *: *"\([^"]*\)".*/\1/p' "$T" | head -n 1)
    [ -n "$tag" ] && { echo "$tag"; return; }
  fi
  return 1
}

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
NOW=$(ver .)
echo "Huidige versie: ${NOW:-onbekend}"

if [ -z "$ARG" ] || { [ ! -e "$ARG" ] && ! echo "$ARG" | grep -q '\.zip$'; }; then
  REF="$ARG"
  if [ -z "$REF" ]; then
    REF=$(latest_tag) || { echo "Geen release of tag gevonden bij $REPO. Maak er een op GitHub (bijvoorbeeld v0.8.0) of geef een tak op: sh update.sh main"; exit 1; }
    echo "Nieuwste op GitHub ($REPO): $REF"
  else
    echo "Gekozen op GitHub ($REPO): $REF"
  fi
  [ "$CHECK" = 1 ] && { echo "(alleen gecontroleerd; er is niets gewijzigd)"; exit 0; }
  echo "Ophalen…"
  fetch "https://api.github.com/repos/$REPO/zipball/$REF" "$TMP/in.zip" || { echo "Ophalen mislukt. Bestaat $REF in $REPO (en is de repository openbaar, of geef GITHUB_TOKEN)?"; exit 1; }
  SRC="$TMP/in.zip"
else
  [ "$CHECK" = 1 ] && { echo "--check werkt alleen met GitHub (zonder pad)."; exit 0; }
  SRC="$ARG"
fi

mkdir "$TMP/new"
if [ -d "$SRC" ]; then cp -R "$SRC"/. "$TMP/new"/
elif command -v unzip >/dev/null 2>&1; then unzip -q "$SRC" -d "$TMP/new"
elif command -v python3 >/dev/null 2>&1; then python3 -m zipfile -e "$SRC" "$TMP/new"
else echo "unzip of python3 is nodig"; exit 1; fi
NEW="$TMP/new"
[ -f "$NEW/run.py" ] || NEW=$(dirname "$(find "$TMP/new" -name run.py | head -n 1)")
[ -f "$NEW/run.py" ] && [ -f "$NEW/app/__init__.py" ] || { echo "Dit lijkt geen PVE Fleet (run.py of app/ ontbreekt)."; exit 1; }
NEWV=$(ver "$NEW")
echo "Nieuwe versie: ${NEWV:-onbekend}"
if [ -n "$NOW" ] && [ "$NOW" = "$NEWV" ]; then echo "Dezelfde versie staat er al; ik werk toch bij (handig bij een herstel)."; fi

# back-up van je gegevens, de laatste drie blijven bewaard
if [ -d data ]; then
  B="../pve-fleet-data-$(date +%Y%m%d-%H%M%S).tgz"
  tar -czf "$B" data && echo "Back-up van data/: $B"
  ls -1t ../pve-fleet-data-*.tgz 2>/dev/null | tail -n +4 | while read -r old; do rm -f "$old"; done
fi

# programmabestanden vervangen, je eigen bestanden niet
for item in "$NEW"/* "$NEW"/.dockerignore; do
  [ -e "$item" ] || continue
  n=$(basename "$item")
  case "$n" in data|.env|config.yml|__pycache__) continue ;; esac
  if [ "$n" = docker-compose.yml ]; then  # jouw aanpassingen (poorten, tijdzone) blijven; de nieuwe komt ernaast
    if [ -f docker-compose.yml ] && ! cmp -s "$item" docker-compose.yml; then cp "$item" docker-compose.yml.new; echo "Let op: docker-compose.yml is niet vervangen. De nieuwe staat in docker-compose.yml.new (vergelijk ze als er iets in veranderd is)."; fi
    [ -f docker-compose.yml ] || cp "$item" docker-compose.yml
    continue
  fi
  if [ "$n" = update.sh ]; then cp "$item" ./update.sh.new && mv ./update.sh.new ./update.sh; continue; fi  # niet midden in het draaien overschrijven
  rm -rf "./$n"; cp -R "$item" "./$n"
done
mkdir -p data

if docker compose version >/dev/null 2>&1; then DC="docker compose"; elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"; else
  echo "Bestanden zijn bijgewerkt. Docker niet gevonden: start PVE Fleet zelf opnieuw."; exit 0; fi
$DC up -d --build --remove-orphans
docker image prune -f >/dev/null 2>&1 || true
echo "Klaar: PVE Fleet ${NEWV:-} draait. Open het dashboard opnieuw in je browser (Ctrl+F5)."
