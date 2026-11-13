#!/bin/sh
# Update PVE Fleet without losing your settings (data/, docker-compose.yml and .env are kept).
#
# Usage, in the PVE Fleet folder (where docker-compose.yml is):
#   sh update.sh                  -> fetches the latest release (or tag) from GitHub and rebuilds the container
#   sh update.sh --check          -> only look: what is installed and what is the latest version on GitHub
#   sh update.sh v0.8.0           -> a specific release or tag from GitHub (also a branch, for example main)
#   sh update.sh path/to.zip      -> a zip from your own disk (or an extracted folder)
#   REPO=owner/name sh update.sh   -> another GitHub repository (default romio23/PVE-fleet)
#   GITHUB_TOKEN=... sh update.sh     -> for a private repository
set -e
cd "$(dirname "$0")"
REPO="${REPO:-romio23/PVE-fleet}"
CHECK=0; ARG=""
for a in "$@"; do case "$a" in --check|-c) CHECK=1 ;; *) ARG="$a" ;; esac; done

ver() { sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$1/app/__init__.py" 2>/dev/null | head -n 1; }
fetch() {  # fetch URL FILE
  if command -v curl >/dev/null 2>&1; then
    if [ -n "$GITHUB_TOKEN" ]; then curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "User-Agent: pve-fleet-update" -o "$2" "$1"; else curl -fsSL -H "User-Agent: pve-fleet-update" -o "$2" "$1"; fi
  elif command -v wget >/dev/null 2>&1; then
    if [ -n "$GITHUB_TOKEN" ]; then wget -q --header="Authorization: Bearer $GITHUB_TOKEN" -O "$2" "$1"; else wget -q -O "$2" "$1"; fi
  else echo "curl or wget is required (sudo apt-get install -y curl)"; exit 1; fi
}
latest_tag() {  # latest release, otherwise latest tag
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
echo "Current version: ${NOW:-unknown}"

if [ -z "$ARG" ] || { [ ! -e "$ARG" ] && ! echo "$ARG" | grep -q '\.zip$'; }; then
  REF="$ARG"
  if [ -z "$REF" ]; then
    REF=$(latest_tag) || { echo "No release or tag found in $REPO. Create one on GitHub (for example v0.9.0) or specify a branch: sh update.sh main"; exit 1; }
    echo "Latest on GitHub ($REPO): $REF"
  else
    echo "Chosen on GitHub ($REPO): $REF"
  fi
  [ "$CHECK" = 1 ] && { echo "(checked only; nothing was changed)"; exit 0; }
  echo "Fetching…"
  fetch "https://api.github.com/repos/$REPO/zipball/$REF" "$TMP/in.zip" || { echo "Fetching failed. Does $REF exist in $REPO (and is the repository public, or provide GITHUB_TOKEN)?"; exit 1; }
  SRC="$TMP/in.zip"
else
  [ "$CHECK" = 1 ] && { echo "--check only works with GitHub (without a path)."; exit 0; }
  SRC="$ARG"
fi

mkdir "$TMP/new"
if [ -d "$SRC" ]; then cp -R "$SRC"/. "$TMP/new"/
elif command -v unzip >/dev/null 2>&1; then unzip -q "$SRC" -d "$TMP/new"
elif command -v python3 >/dev/null 2>&1; then python3 -m zipfile -e "$SRC" "$TMP/new"
else echo "unzip or python3 is required"; exit 1; fi
NEW="$TMP/new"
[ -f "$NEW/run.py" ] || NEW=$(dirname "$(find "$TMP/new" -name run.py | head -n 1)")
[ -f "$NEW/run.py" ] && [ -f "$NEW/app/__init__.py" ] || { echo "This does not look like PVE Fleet (run.py or app/ is missing)."; exit 1; }
NEWV=$(ver "$NEW")
echo "New version: ${NEWV:-unknown}"
if [ -n "$NOW" ] && [ "$NOW" = "$NEWV" ]; then echo "The same version is already installed; updating anyway (handy for a repair)."; fi

# backup of your data, the last three are kept
if [ -d data ]; then
  B="../pve-fleet-data-$(date +%Y%m%d-%H%M%S).tgz"
  tar -czf "$B" data && echo "Backup of data/: $B"
  ls -1t ../pve-fleet-data-*.tgz 2>/dev/null | tail -n +4 | while read -r old; do rm -f "$old"; done
fi

# replace program files, not your own files
for item in "$NEW"/* "$NEW"/.dockerignore; do
  [ -e "$item" ] || continue
  n=$(basename "$item")
  case "$n" in data|.env|config.yml|__pycache__) continue ;; esac
  if [ "$n" = docker-compose.yml ]; then  # your changes (ports, timezone) stay; the new one is placed next to it
    if [ -f docker-compose.yml ] && ! cmp -s "$item" docker-compose.yml; then cp "$item" docker-compose.yml.new; echo "Note: docker-compose.yml was not replaced. The new one is in docker-compose.yml.new (compare them if anything changed)."; fi
    [ -f docker-compose.yml ] || cp "$item" docker-compose.yml
    continue
  fi
  if [ "$n" = update.sh ]; then cp "$item" ./update.sh.new && mv ./update.sh.new ./update.sh; continue; fi  # do not overwrite while running
  rm -rf "./$n"; cp -R "$item" "./$n"
done
mkdir -p data

if docker compose version >/dev/null 2>&1; then DC="docker compose"; elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"; else
  echo "Files updated. Docker not found: restart PVE Fleet yourself."; exit 0; fi
$DC up -d --build --remove-orphans
docker image prune -f >/dev/null 2>&1 || true
echo "Done: PVE Fleet ${NEWV:-} is running. Reload the dashboard in your browser (Ctrl+F5)."
