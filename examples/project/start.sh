#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

export PROJECT_PATH="$PROJECT_DIR"
export DAGPLOY_CONFIG_DIR="$PROJECT_DIR/config"

export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$PROJECT_PATH/service-account.json}"
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"

if [ ! -d "$DAGPLOY_CONFIG_DIR" ]; then
  echo "❌ Missing config folder: $DAGPLOY_CONFIG_DIR"
  exit 1
fi

if [ ! -f "$DAGPLOY_CONFIG_DIR/config.yaml" ]; then
  echo "❌ Missing Hydra config: $DAGPLOY_CONFIG_DIR/config.yaml"
  exit 1
fi

if [ ! -f ".venv/bin/activate" ]; then
  echo "❌ Missing .venv/bin/activate"
  exit 1
fi

source .venv/bin/activate

LOG_FILE="$(mktemp)"
WORKER_PGID=""
API_PGID=""

kill_group() {
  local pgid="${1:-}"

  if [ -z "$pgid" ]; then
    return 0
  fi

  kill -TERM "-$pgid" 2>/dev/null || true
  sleep 1
  kill -KILL "-$pgid" 2>/dev/null || true
}

cleanup() {
  local exit_code=$?

  echo ""
  echo "🧹 Shutting down..."

  kill_group "$API_PGID"
  kill_group "$WORKER_PGID"

  rm -f "$LOG_FILE"

  exit "$exit_code"
}

trap cleanup EXIT INT TERM HUP

echo "🚀 Starting dagploy_dax.manager.worker..."

setsid bash -c '
  set -euo pipefail
  python3 -m dagploy_dax.manager.worker 2>&1 | tee "$1"
' _ "$LOG_FILE" &

WORKER_PGID=$!

echo "⏳ Waiting for worker to print: starting runner"

while true; do
  if ! kill -0 "$WORKER_PGID" 2>/dev/null; then
    echo "❌ dagploy_dax.manager.worker exited before starting runner appeared"
    exit 1
  fi

  if grep -qi "starting runner" "$LOG_FILE"; then
    echo "✅ Worker is ready"
    break
  fi

  sleep 0.5
done

echo "🚀 Starting dagploy_dax.manager.api_server..."

setsid python3 -m dagploy_dax.manager.api_server &
API_PGID=$!

wait "$API_PGID"