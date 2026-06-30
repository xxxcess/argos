#!/bin/bash
# Argos — one-command, profile-aware quick start for macOS (Apple Silicon).
#
#   ./start-macos.sh
#
# The committed config/runtime-profile.env selects the product runtime for this
# checkout. The launcher derives isolated persistence and service defaults from
# that profile rather than from the current Git branch.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# Load local machine overrides first. Shell values take priority over .env.
if [ -f .env ]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${key// }" ]] && continue
        value="${value%%#*}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        [ -n "$key" ] && [ -z "${!key+x}" ] && export "$key=$value"
    done < .env
fi

# Each product line commits its runtime identity. Do not discover it from `git
# branch`: detached HEADs, worktrees, and deployed artifacts make that unsafe.
RUNTIME_PROFILE_FILE="${ARGOS_RUNTIME_PROFILE_FILE:-$REPO_DIR/config/runtime-profile.env}"
if [ ! -f "$RUNTIME_PROFILE_FILE" ]; then
    echo "✗ Missing runtime profile: $RUNTIME_PROFILE_FILE"
    echo "  Expected config/runtime-profile.env in this checkout."
    exit 1
fi
# shellcheck disable=SC1090
. "$RUNTIME_PROFILE_FILE"

for required_var in ARGOS_RUNTIME_ID ARGOS_PRODUCT_NAME ARGOS_STORAGE_SLUG ARGOS_DEFAULT_PORT ARGOS_DEFAULT_CHROMADB_PORT; do
    if [ -z "${!required_var:-}" ]; then
        echo "✗ Runtime profile is missing $required_var: $RUNTIME_PROFILE_FILE"
        exit 1
    fi
done

# Data isolation. A legacy ODYSSEUS_DATA_DIR value is intentionally ignored as
# an input here: it may point at another product line. It is exported below only
# as a compatibility alias for existing Python modules.
ARGOS_DATA_ROOT="${ARGOS_DATA_ROOT:-$HOME/Library/Application Support/Argos/runtimes}"
if [ -z "${ARGOS_DATA_DIR:-}" ] && [ -n "${ODYSSEUS_DATA_DIR:-}" ]; then
    echo "⚠ Ignoring legacy ODYSSEUS_DATA_DIR for profile isolation."
    echo "  Set ARGOS_DATA_DIR explicitly only for a verified matching runtime."
fi
ARGOS_DATA_DIR="${ARGOS_DATA_DIR:-$ARGOS_DATA_ROOT/$ARGOS_STORAGE_SLUG}"
export ARGOS_DATA_DIR
export ODYSSEUS_DATA_DIR="$ARGOS_DATA_DIR"
mkdir -p "$ARGOS_DATA_DIR"

# Bind a data root to the runtime that created it. This prevents accidentally
# launching Venture against Odysseus data (or the reverse) after switching code.
RUNTIME_MANIFEST="$ARGOS_DATA_DIR/runtime-manifest.json"
if [ -f "$RUNTIME_MANIFEST" ]; then
    manifest_runtime="$(sed -nE 's/^[[:space:]]*"runtime_id"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' "$RUNTIME_MANIFEST" | head -n 1 || true)"
    manifest_slug="$(sed -nE 's/^[[:space:]]*"storage_slug"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' "$RUNTIME_MANIFEST" | head -n 1 || true)"
    if [ -z "$manifest_runtime" ] || [ -z "$manifest_slug" ]; then
        echo "✗ Cannot validate runtime manifest: $RUNTIME_MANIFEST"
        echo "  Set ARGOS_ALLOW_RUNTIME_MISMATCH=1 only for deliberate maintenance."
        exit 1
    fi
    if { [ "$manifest_runtime" != "$ARGOS_RUNTIME_ID" ] || [ "$manifest_slug" != "$ARGOS_STORAGE_SLUG" ]; } \
        && [ "${ARGOS_ALLOW_RUNTIME_MISMATCH:-}" != "1" ]; then
        echo "✗ Runtime/data mismatch detected."
        echo "  Profile: $ARGOS_RUNTIME_ID ($ARGOS_STORAGE_SLUG)"
        echo "  Data root belongs to: $manifest_runtime ($manifest_slug)"
        echo "  Choose a separate ARGOS_DATA_DIR or use an explicit migration process."
        exit 1
    fi
elif [ -n "$(find "$ARGOS_DATA_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ] \
    && [ "${ARGOS_ADOPT_RUNTIME_DATA:-}" != "1" ]; then
    echo "✗ Refusing to adopt existing unmanifested data at: $ARGOS_DATA_DIR"
    echo "  Verify it belongs to $ARGOS_RUNTIME_ID, then re-run with ARGOS_ADOPT_RUNTIME_DATA=1."
    exit 1
else
    cat > "$RUNTIME_MANIFEST" <<EOF
{
  "runtime_id": "$ARGOS_RUNTIME_ID",
  "storage_slug": "$ARGOS_STORAGE_SLUG",
  "product_name": "$ARGOS_PRODUCT_NAME"
}
EOF
fi

# Shell overrides take priority, followed by local .env values, followed by the
# committed product profile. Profile defaults keep two product lines runnable at
# once without port collisions.
PORT="${ARGOS_PORT:-${ODYSSEUS_PORT:-${APP_PORT:-$ARGOS_DEFAULT_PORT}}}"
HOST="${ARGOS_HOST:-${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}}"
PROBE_HOST="$HOST"
if [ "$PROBE_HOST" = "0.0.0.0" ] || [ "$PROBE_HOST" = "::" ]; then
    PROBE_HOST="127.0.0.1"
fi

# Friendly message on any failure — re-running is safe (every step is idempotent).
trap 'echo; echo "✗ Setup failed above. It is safe to re-run ./start-macos.sh."; exit 1' ERR

echo "▶ $ARGOS_PRODUCT_NAME quick start for macOS"
echo "  Runtime: $ARGOS_RUNTIME_ID"
echo "  Data:    $ARGOS_DATA_DIR"

# Fail fast if the port is already taken (e.g. a previous run still running).
if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
    echo "✗ Port $PORT is already in use on $PROBE_HOST. Stop what's using it, or pick another port:"
    echo "    ARGOS_PORT=7900 ./start-macos.sh"
    exit 1
fi

# 1. Homebrew — the macOS package manager. We can't safely auto-install it
#    (it wants its own interactive confirmation), so point the user at it.
if ! command -v brew >/dev/null 2>&1; then
    echo
    echo "Homebrew is required but not installed. Install it (one command), then re-run this script:"
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo
    echo "More info: https://brew.sh"
    exit 1
fi

# 2. Find a Python 3.11+ to build the environment with.
#    On Apple Silicon we require an arm64 interpreter (Homebrew's, under
#    /opt/homebrew). On Intel (or non-mac) use any compatible Python on PATH.
PY=""
if [ "$(uname -m)" = "arm64" ]; then
    cands="/opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11"
else
    cands="python3 python3.13 python3.12 python3.11"
fi
for cand in $cands; do
    p="$(command -v "$cand" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)' 2>/dev/null; then
        PY="$p"; break
    fi
done

# System dependencies. Cookbook needs tmux and llama.cpp; the core app can
# still launch when either optional install fails.
brew_ensure() {
    if command -v "$1" >/dev/null 2>&1; then
        echo "  ✓ $2 already installed"
        return 0
    fi
    echo "  installing $2…"
    if ! brew install "$2"; then
        echo "  ⚠ Couldn't install $2 right now — Cookbook may be limited."
        echo "    You can install it later with: brew install $2"
    fi
}

echo "▶ Checking dependencies (Homebrew)…"
if [ -n "$PY" ]; then
    echo "  (using $("$PY" --version 2>&1) at $PY)"
else
    echo "  installing python@3.11…"
    brew install python@3.11 || true
    PY="$(command -v /opt/homebrew/bin/python3.11 || command -v python3.11 || true)"
fi
brew_ensure tmux tmux
brew_ensure llama-server llama.cpp
brew_ensure apfel apfel

if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    echo "✗ Couldn't find a Python 3.11+ to build the environment with."
    echo "  Check: ls /opt/homebrew/bin/python3*  (or install one: brew install python@3.11)"
    exit 1
fi

# 3. Python environment + dependencies (kept inside the repo, in venv/).
VENV_PY="./venv/bin/python3"
if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    [ -d venv ] && { echo "▶ Existing venv is incomplete (no working pip) — rebuilding…"; rm -rf venv; }
    echo "▶ Creating Python environment…"
    "$PY" -m venv venv
fi
REQ_HASH="$(md5 -q requirements.txt 2>/dev/null || md5sum requirements.txt | cut -d' ' -f1)"
REQ_HASH_FILE="venv/.requirements_hash"
if [ ! -f "$REQ_HASH_FILE" ] || [ "$REQ_HASH" != "$(cat "$REQ_HASH_FILE" 2>/dev/null)" ]; then
  echo "▶ Installing Python packages (first run downloads a few — can take a few minutes)…"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install -r requirements.txt
  echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
  echo "▶ Python packages up to date — skipping install"
fi

# chromadb-client (HTTP-only) conflicts with the full chromadb package.
if "$VENV_PY" -m pip show chromadb-client >/dev/null 2>&1; then
    echo "▶ Cleaning up conflicting chromadb-client package…"
    "$VENV_PY" -m pip uninstall -y chromadb-client
    "$VENV_PY" -m pip install --force-reinstall chromadb
    "$VENV_PY" -m pip install --force-reinstall "setuptools<82" "tokenizers==0.22.2"
fi

# 4. First-run setup: creates profile-scoped data dirs and prints the first
# admin password on initial use. Suppress its manual run hint.
echo "▶ Preparing $ARGOS_PRODUCT_NAME…"
ODYSSEUS_SKIP_RUN_HINT=1 ./venv/bin/python setup.py

# Local provider bootstrap.
MACHINE_ARCH="$(uname -m)"
APFEL_PID=""
APFEL_PORT="${APFEL_PORT:-${ARGOS_DEFAULT_APFEL_PORT:-11435}}"
if [ "$MACHINE_ARCH" = "arm64" ]; then
    if command -v apfel >/dev/null 2>&1; then
        APFEL_LOG="${TMPDIR:-/tmp}/${ARGOS_STORAGE_SLUG}-apfel.log"
        echo "▶ Starting Apfel server in the background on port $APFEL_PORT…"
        echo "  logging to $APFEL_LOG"
        nohup apfel --serve --port "$APFEL_PORT" >"$APFEL_LOG" 2>&1 &
        APFEL_PID=$!
    else
        echo "▶ Apfel is not installed (brew formula missing); skipping Apfel server bootstrap."
    fi
else
    echo "▶ Non-ARM macOS detected; skipping Apfel server bootstrap."
fi

# ChromaDB backs the tool index and vector RAG. Its persistence and default
# port are profile-scoped so independent product lines never share a store.
CHROMA_PID=""
CHROMA_HOST="${CHROMADB_HOST:-localhost}"
CHROMA_PORT="${CHROMADB_PORT:-${ARGOS_DEFAULT_CHROMADB_PORT:-8100}}"
export CHROMADB_HOST="$CHROMA_HOST"
export CHROMADB_PORT="$CHROMA_PORT"
CHROMA_BIN="$(dirname "$VENV_PY")/chroma"
case "$CHROMA_HOST" in
    localhost|127.0.0.1) CHROMA_BIND="127.0.0.1" ;;
    0.0.0.0)             CHROMA_BIND="0.0.0.0" ;;
    *)                   CHROMA_BIND="" ;;
esac
if (exec 3<>"/dev/tcp/127.0.0.1/$CHROMA_PORT") 2>/dev/null; then
    echo "▶ ChromaDB already running on 127.0.0.1:$CHROMA_PORT - using it."
elif [ -z "$CHROMA_BIND" ]; then
    echo "▶ CHROMADB_HOST=$CHROMA_HOST is remote - not starting a local ChromaDB."
elif [ -x "$CHROMA_BIN" ]; then
    CHROMA_LOG="${TMPDIR:-/tmp}/${ARGOS_STORAGE_SLUG}-chromadb.log"
    echo "▶ Starting ChromaDB in the background on $CHROMA_BIND:$CHROMA_PORT…"
    echo "  logging to $CHROMA_LOG"
    nohup "$CHROMA_BIN" run --host "$CHROMA_BIND" --port "$CHROMA_PORT" --path "$ARGOS_DATA_DIR/chroma" >"$CHROMA_LOG" 2>&1 &
    CHROMA_PID=$!
else
    echo "▶ ChromaDB CLI not found in venv; skipping (tool index will be degraded)."
fi

# 5. Launch. Bind to loopback by default; opt into LAN/Tailscale through
# ARGOS_HOST=0.0.0.0, ODYSSEUS_HOST=0.0.0.0, or APP_BIND=0.0.0.0.
URL_HOST="$HOST"
if [ "$URL_HOST" = "0.0.0.0" ] || [ "$URL_HOST" = "::" ]; then
    URL_HOST="127.0.0.1"
fi
URL="http://$URL_HOST:$PORT"
TAILSCALE_URL=""
if [ "$HOST" = "0.0.0.0" ] && command -v tailscale >/dev/null 2>&1; then
    TS_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
    if [ -n "$TS_IP" ]; then
        TAILSCALE_URL="http://$TS_IP:$PORT"
    fi
fi

# Open the browser automatically once the server is accepting connections.
NO_OPEN="${ARGOS_NO_OPEN:-${ODYSSEUS_NO_OPEN:-}}"
POLLER_PID=""
if [ -z "$NO_OPEN" ] && command -v open >/dev/null 2>&1; then
    (
        for _ in $(seq 1 90); do
            if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
                printf '\n'
                printf '  ┌────────────────────────────────────────────┐\n'
                printf '  │  ✓ %-40s │\n' "$ARGOS_PRODUCT_NAME is ready"
                printf '  │     %-40s │\n' "$URL"
                printf '  │     (Press Ctrl+C in this window to stop)    │\n'
                printf '  └────────────────────────────────────────────┘\n\n'
                open "$URL"
                break
            fi
            sleep 1
        done
    ) &
    POLLER_PID=$!
fi

# Setup is done — drop the setup-failure handler, and clean up background work.
trap - ERR
trap '[ -n "$POLLER_PID" ] && kill "$POLLER_PID" 2>/dev/null; [ -n "$APFEL_PID" ] && kill "$APFEL_PID" 2>/dev/null; [ -n "$CHROMA_PID" ] && kill "$CHROMA_PID" 2>/dev/null' EXIT INT TERM

echo
echo "▶ Starting $ARGOS_PRODUCT_NAME — it will open in your browser at $URL"
if [ -n "$TAILSCALE_URL" ]; then
    echo "  Tailscale/LAN URL: $TAILSCALE_URL"
fi
echo "  (this takes a few seconds; press Ctrl+C here to stop)"
echo
"$VENV_PY" -m uvicorn app:app --host "$HOST" --port "$PORT"
