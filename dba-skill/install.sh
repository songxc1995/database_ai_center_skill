#!/usr/bin/env bash
# Install dba-skill into an AI host, and prove it works before walking away.
#
# The last step is the point. Handing someone a skill directory and trusting that it works is
# how a batch rollout turns into a week of "it says 401" — and a 401 alone cannot tell them
# whether the key is wrong, expired, or simply not being found. This verifies end to end and
# prints who the platform thinks you are.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HOME/.dba-skill/config"
MODE="symlink"
HOSTS=()

usage() {
  cat <<'USAGE'
usage: install.sh [--host claude|workbuddy|<path>]... [--copy]

  --host   Where to install. Repeatable. Defaults to every known host found on this machine.
             claude     → ~/.claude/skills/dba-skill
             workbuddy  → ~/.workbuddy-ai/skills/dba-skill
             <path>     → that directory (its parent must exist)
  --copy   Copy instead of symlinking. Symlinks are the default so one `git pull` updates
           every host at once; copies drift, and a drifted copy of a prompt is worse than no
           copy at all.

A symlink install points every host at THIS checkout, so keep the repository somewhere
permanent — a clone under /tmp or a scratch directory takes all hosts down with it when it is
cleaned up. Use --copy if the source cannot be kept around.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOSTS+=("$2"); shift 2 ;;
    --copy) MODE="copy"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

resolve_host() {
  case "$1" in
    claude)    echo "$HOME/.claude/skills/dba-skill" ;;
    workbuddy) echo "$HOME/.workbuddy-ai/skills/dba-skill" ;;
    *)         echo "$1" ;;
  esac
}

if [ ${#HOSTS[@]} -eq 0 ]; then
  for h in claude workbuddy; do
    parent="$(dirname "$(resolve_host "$h")")"
    [ -d "$(dirname "$parent")" ] && HOSTS+=("$h")
  done
  [ ${#HOSTS[@]} -eq 0 ] && { echo "no known host found; pass --host <path>" >&2; exit 1; }
fi

# ── 1. credentials ────────────────────────────────────────────────────────────
if [ ! -f "$CONFIG" ]; then
  # Bail with an explanation before prompting, not during. Under `set -e` a `read` that hits
  # EOF exits the script on that line, so a run from a pipe or a deployment tool produced
  # exit 1 with an empty stderr and half a sentence on stdout — the reader is left to guess.
  if [ ! -t 0 ]; then
    cat >&2 <<EOF
No $CONFIG, and stdin is not a terminal, so there is nothing to prompt.
Create it first (chmod 600), then re-run:

  PROJECT_API_BASE_URL=http://databaseai.fosun.com/api/v2
  PROJECT_API_KEY=<your key>

Or run this script from a terminal.
EOF
    exit 1
  fi
  echo "No $CONFIG yet — creating it."
  read -r -p "  API base URL [http://databaseai.fosun.com/api/v2]: " base || true
  base="${base:-http://databaseai.fosun.com/api/v2}"
  read -r -s -p "  Your personal API key (input hidden): " key || true
  echo
  [ -n "${key:-}" ] || { echo "  no key given, aborting" >&2; exit 1; }
  mkdir -p "$(dirname "$CONFIG")"
  umask 177
  printf 'PROJECT_API_BASE_URL=%s\nPROJECT_API_KEY=%s\n' "$base" "$key" > "$CONFIG"
  chmod 600 "$CONFIG" 2>/dev/null || true
  echo "  wrote $CONFIG"
else
  echo "Using existing $CONFIG"
fi

# ── 2. install ────────────────────────────────────────────────────────────────
for h in "${HOSTS[@]}"; do
  dest="$(resolve_host "$h")"
  mkdir -p "$(dirname "$dest")"
  rm -rf "$dest"
  if [ "$MODE" = "symlink" ]; then ln -s "$SRC" "$dest"; else cp -R "$SRC" "$dest"; fi
  echo "Installed ($MODE): $dest"
done

# ── 3. prove it ───────────────────────────────────────────────────────────────
PY="python"; command -v python >/dev/null 2>&1 || PY="python3"
echo
echo "Verifying against the platform…"
if ! "$PY" "$SRC/scripts/dba_api_client.py" whoami; then
  cat >&2 <<'FAIL'

Verification failed. The output above carries `credentials.per_key_source` — which file each
value came from. That is the difference between "the key is wrong" and "the key was never read".
FAIL
  exit 1
fi
echo
echo "Done. Restart the host so it picks up the skill."
