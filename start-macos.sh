#!/bin/bash
# Argos — profile-aware quick start for macOS.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# Machine-local settings are loaded first; the branch-owned profile supplies
# product identity and safe defaults after that.
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

RUNTIME_PROFILE_FILE="${ARGOS_RUNTIME_PROFILE_FILE:-$REPO_DIR/config/runtime-profile.env}"
if [ ! -f "$RUNTIME_PROFILE_FILE" ]; then
    echo "✗ Missing runtime profile: $RUNTIME_PROFILE_FILE"
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

# The profile, not the Git branch, determines persistent state. Ignore a legacy
# ODYSSEUS_DATA_DIR input so it cannot silently point this product at another
# runtime's users, database, keys, uploads, gallery, or vector store.
ARGOS_DATA_ROOT="${ARGOS_DATA_ROOT:-$HOME/Library/Application Support/Argos/runtimes}"
if [ -z "${ARGOS_DATA_DIR:-}" ] && [ -n "${ODYSSEUS_DATA_DIR:-}" ]; then
    echo "⚠ Ignoring legacy ODYSSEUS_DATA_DIR; use ARGOS_DATA_DIR only for verified matching data."
fi
ARGOS_DATA_DIR="${ARGOS_DATA_DIR:-$ARGOS_DATA_ROOT/$ARGOS_STORAGE_SLUG}"
export ARGOS_DATA_DIR
export ODYSSEUS_DATA_DIR="$ARGOS_DATA_DIR"
mkdir -p "$ARGOS_DATA_DIR"

RUNTIME_MANIFEST="$ARGOS_DATA_DIR/runtime-manifest.json"
if [ -f "$RUNTIME_MANIFEST" ]; then
    manifest_runtime="$(sed -nE 's/^[[:space:]]*"runtime_id"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' "$RUNTIME_MANIFEST" | head -n 1 || true)"
    manifest_slug="$(sed -nE 's/^[[:space:]]*"storage_slug"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' "$RUNTIME_MANIFEST" | head -n 1 || true)"
    if [ -z "$manifest_runtime" ] || [ -z "$manifest_slug" ] || { [ "$manifest_runtime" != "$ARGOS_RUNTIME_ID" ] || [ "$manifest_slug" != "$ARGOS_STORAGE_SLUG" ]; }; then
        if [ "${ARGOS_ALLOW_RUNTIME_MISMATCH:-}" != "1" ]; then
            echo "✗ Runtime/data mismatch at $ARGOS_DATA_DIR"
            echo "  Profile: $ARGOS_RUNTIME_ID ($ARGOS_STORAGE_SLUG)"
            echo "  Use a separate data root or an explicit migration."
            exit 1
        fi
    fi
elif [ -n "$(find "$ARGOS_DATA_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ] && [ "${ARGOS_ADOPT_RUNTIME_DATA:-}" != "1" ]; then
    echo "✗ Refusing to adopt unmanifested data at $ARGOS_DATA_DIR"
    echo "  Verify ownership, then set ARGOS_ADOPT_RUNTIME_DATA=1 once."
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

PORT="${ARGOS_PORT:-${ODYSSEUS_PORT:-${APP_PORT:-$ARGOS_DEFAULT_PORT}}}"
HOST="${ARGOS_HOST:-${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}}"
PROBE_HOST="$HOST"
if [ "$PROBE_HOST" = "0.0.0.0" ] || [ "$PROBE_HOST" = "::" ]; then
    PROBE_HOST="127.0.0.1"
fi
trap 'echo; echo "✗ Setup failed above. It is safe to re-run ./start-macos.sh."; exit 1' ERR

echo "▶ $ARGOS_PRODUCT_NAME quick start for macOS"
echo "  Runtime: $ARGOS_RUNTIME_ID"
echo "  Data:    $ARGOS_DATA_DIR"
if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
    echo "✗ Port $PORT is already in use on $PROBE_HOST. Pick another with:"
    echo "    ARGOS_PORT=7900 ./start-macos.sh"
    exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required. Install it, then re-run ./start-macos.sh."
    exit 1
fi

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

brew_ensure() {
    if command -v "$1" >/dev/null 2>&1; then
        echo "  ✓ $2 already installed"
        return 0
    fi
    echo "  installing $2…"
    if ! brew install "$2"; then
        echo "  ⚠ Couldn't install $2; related optional features may be limited."
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
    echo "✗ Couldn't find a compatible Python 3.11+ interpreter."
    exit 1
fi

VENV_PY="./venv/bin/python3"
if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    [ -d venv ] && { echo "▶ Existing venv is incomplete — rebuilding…"; rm -rf venv; }
    echo "▶ Creating Python environment…"
    "$PY" -m venv venv
fi
REQ_HASH="$(md5 -q requirements.txt 2>/dev/null || md5sum requirements.txt | cut -d' ' -f1)"
REQ_HASH_FILE="venv/.requirements_hash"
if [ ! -f "$REQ_HASH_FILE" ] || [ "$REQ_HASH" != "$(cat "$REQ_HASH_FILE" 2>/dev/null)" ]; then
    echo "▶ Installing Python packages…"
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install -r requirements.txt
    echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
    echo "▶ Python packages up to date — skipping install"
fi
if "$VENV_PY" -m pip show chromadb-client >/dev/null 2>&1; then
    echo "▶ Cleaning up conflicting chromadb-client package…"
    "$VENV_PY" -m pip uninstall -y chromadb-client
    "$VENV_PY" -m pip install --force-reinstall chromadb
    "$VENV_PY" -m pip install --force-reinstall "setuptools<82" "tokenizers==0.22.2"
fi

echo "▶ Preparing $ARGOS_PRODUCT_NAME…"
ODYSSEUS_SKIP_RUN_HINT=1 "$VENV_PY" setup.py

MACHINE_ARCH="$(uname -m)"
APFEL_PID=""
APFEL_PORT="${APFEL_PORT:-${ARGOS_DEFAULT_APFEL_PORT:-11435}}"
if [ "$MACHINE_ARCH" = "arm64" ] && command -v apfel >/dev/null 2>&1; then
    APFEL_LOG="${TMPDIR:-/tmp}/${ARGOS_STORAGE_SLUG}-apfel.log"
    echo "▶ Starting Apfel server in the background on port $APFEL_PORT…"
    nohup apfel --serve --port "$APFEL_PORT" >"$APFEL_LOG" 2>&1 &
    APFEL_PID=$!
fi

CHROMA_PID=""
CHROMA_HOST="${CHROMADB_HOST:-localhost}"
CHROMA_PORT="${CHROMADB_PORT:-${ARGOS_DEFAULT_CHROMADB_PORT:-8100}}"
export CHROMADB_HOST="$CHROMA_HOST"
export CHROMADB_PORT="$CHROMA_PORT"
CHROMA_BIN="$(dirname "$VENV_PY")/chroma"
case "$CHROMA_HOST" in
    localhost|127.0.0.1) CHROMA_BIND="127.0.0.1" ;;
    0.0.0.0) CHROMA_BIND="0.0.0.0" ;;
    *) CHROMA_BIND="" ;;
esac
if (exec 3<>"/dev/tcp/127.0.0.1/$CHROMA_PORT") 2>/dev/null; then
    echo "▶ ChromaDB already running on 127.0.0.1:$CHROMA_PORT - using it."
elif [ -z "$CHROMA_BIND" ]; then
    echo "▶ CHROMADB_HOST=$CHROMA_HOST is remote - not starting a local ChromaDB."
elif [ -x "$CHROMA_BIN" ]; then
    CHROMA_LOG="${TMPDIR:-/tmp}/${ARGOS_STORAGE_SLUG}-chromadb.log"
    echo "▶ Starting ChromaDB in the background on $CHROMA_BIND:$CHROMA_PORT…"
    nohup "$CHROMA_BIN" run --host "$CHROMA_BIND" --port "$CHROMA_PORT" --path "$ARGOS_DATA_DIR/chroma" >"$CHROMA_LOG" 2>&1 &
    CHROMA_PID=$!
fi

URL_HOST="$HOST"
if [ "$URL_HOST" = "0.0.0.0" ] || [ "$URL_HOST" = "::" ]; then
    URL_HOST="127.0.0.1"
fi
URL="http://$URL_HOST:$PORT"
TAILSCALE_URL=""
if [ "$HOST" = "0.0.0.0" ] && command -v tailscale >/dev/null 2>&1; then
    TS_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
    [ -n "$TS_IP" ] && TAILSCALE_URL="http://$TS_IP:$PORT"
fi

NO_OPEN="${ARGOS_NO_OPEN:-${ODYSSEUS_NO_OPEN:-}}"
POLLER_PID=""
if [ -z "$NO_OPEN" ] && command -v open >/dev/null 2>&1; then
    (
        for _ in $(seq 1 90); do
            if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
                echo "▶ $ARGOS_PRODUCT_NAME is ready — opening $URL"
                open "$URL"
                break
            fi
            sleep 1
        done
    ) &
    POLLER_PID=$!
fi

trap - ERR
trap '[ -n "$POLLER_PID" ] && kill "$POLLER_PID" 2>/dev/null; [ -n "$APFEL_PID" ] && kill "$APFEL_PID" 2>/dev/null; [ -n "$CHROMA_PID" ] && kill "$CHROMA_PID" 2>/dev/null' EXIT INT TERM

echo "▶ Starting $ARGOS_PRODUCT_NAME at $URL"
[ -n "$TAILSCALE_URL" ] && echo "  Tailscale/LAN URL: $TAILSCALE_URL"
"$VENV_PY" -m uvicorn app:app --host "$HOST" --port "$PORT"
