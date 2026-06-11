#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
  echo "❌ This script must be run with bash, not sh."
  echo
  echo "Use:"
  echo "  curl -fsSL https://raw.githubusercontent.com/dagploy/dax/refs/heads/main/scripts/gcp_install_local.sh | bash"
  exit 1
fi

set -euo pipefail

echo "🚀 DAX Local Setup"
echo

# -----------------------------
# Helpers
# -----------------------------
ask() {
  local prompt="$1"
  local default="${2:-}"
  local value

  if [ ! -r /dev/tty ]; then
    echo "❌ Interactive input requires a terminal." >&2
    echo "Run this instead:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/dagploy/dax/refs/heads/main/scripts/gcp_install_local.sh -o gcp_install_local.sh" >&2
    echo "  bash gcp_install_local.sh" >&2
    exit 1
  fi

  if [ -n "$default" ]; then
    printf "%s [%s]: " "$prompt" "$default" > /dev/tty
    read -r value < /dev/tty
    echo "${value:-$default}"
  else
    printf "%s: " "$prompt" > /dev/tty
    read -r value < /dev/tty
    echo "$value"
  fi
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "❌ Required command not found: $1"
    exit 1
  fi
}

absolute_path() {
  python3 -c "import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))" "$1"
}

read_json_value() {
  local file="$1"
  local key="$2"

  python3 -c "
import json
import sys

with open(sys.argv[1], 'r') as f:
    data = json.load(f)

value = data.get(sys.argv[2], '')
print(value)
" "$file" "$key"
}

# -----------------------------
# Check requirements
# -----------------------------
require_command python3
require_command curl
require_command docker
require_command git

if ! docker compose version >/dev/null 2>&1; then
  echo "❌ Docker Compose is required"
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "❌ uv is required"
  echo "Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# -----------------------------
# Service account JSON input
# -----------------------------
while true; do
  SERVICE_ACCOUNT_SOURCE_PATH="$(ask "Path to your service account JSON file" "~/service-account.json")"
  SERVICE_ACCOUNT_SOURCE_PATH="$(absolute_path "$SERVICE_ACCOUNT_SOURCE_PATH")"

  if [ ! -f "$SERVICE_ACCOUNT_SOURCE_PATH" ]; then
    echo "❌ Service account JSON file not found:"
    echo "   $SERVICE_ACCOUNT_SOURCE_PATH"
    echo
    continue
  fi

  if [ "${SERVICE_ACCOUNT_SOURCE_PATH##*.}" != "json" ]; then
    echo "❌ Service account file must be a .json file:"
    echo "   $SERVICE_ACCOUNT_SOURCE_PATH"
    echo
    continue
  fi

  break
done

# -----------------------------
# Read project and service account from JSON
# -----------------------------
GCP_PROJECT_NAME="$(read_json_value "$SERVICE_ACCOUNT_SOURCE_PATH" "project_id")"
SERVICE_ACCOUNT_EMAIL="$(read_json_value "$SERVICE_ACCOUNT_SOURCE_PATH" "client_email")"

if [ -z "$GCP_PROJECT_NAME" ]; then
  echo "❌ project_id not found in service account JSON."
  exit 1
fi

if [ -z "$SERVICE_ACCOUNT_EMAIL" ]; then
  echo "❌ client_email not found in service account JSON."
  exit 1
fi

# -----------------------------
# Root path = current directory
# -----------------------------
ROOT_PATH="$(absolute_path ".")"
DAX_REPO_DIR="$ROOT_PATH/dax"

echo
echo "Root path              : $ROOT_PATH"
echo "DAX repo path          : $DAX_REPO_DIR"
echo "Service account JSON   : $SERVICE_ACCOUNT_SOURCE_PATH"
echo "GCP project            : $GCP_PROJECT_NAME"
echo "Service account email  : $SERVICE_ACCOUNT_EMAIL"
echo

cd "$ROOT_PATH"

# -----------------------------
# Clone DAX project
# -----------------------------
echo "📦 Preparing DAX project"

if [ ! -d "$DAX_REPO_DIR" ]; then
  git clone https://github.com/dagploy/dax.git "$DAX_REPO_DIR"
else
  echo "✅ DAX repository already exists at $DAX_REPO_DIR"
fi

# -----------------------------
# Use examples/project as main project path
# -----------------------------
APP_DIR="$DAX_REPO_DIR/examples/project"

if [ ! -d "$APP_DIR" ]; then
  echo "❌ DAX example project folder not found:"
  echo "   $APP_DIR"
  exit 1
fi

PROJECT_PATH="$APP_DIR"

cd "$APP_DIR"

echo "📁 Using DAX project folder:"
echo "   $APP_DIR"

# -----------------------------
# Python environment + DAX install
# -----------------------------
echo "🐍 Creating Python 3.11 virtual environment in APP_DIR"

if [ ! -d "$APP_DIR/.venv" ]; then
  uv venv --python=3.11 "$APP_DIR/.venv"
fi

# shellcheck disable=SC1091
source "$APP_DIR/.venv/bin/activate"

echo "📦 Installing DAX from source"
uv pip install -e "$DAX_REPO_DIR"


# -----------------------------
# Pulumi setup
# -----------------------------
echo "🧱 Setting up Pulumi"

if ! command -v pulumi >/dev/null 2>&1; then
  curl -fsSL https://get.pulumi.com | sh
  export PATH="$HOME/.pulumi/bin:$PATH"

  if ! command -v pulumi >/dev/null 2>&1; then
    echo "❌ Pulumi installed but not found in PATH"
    echo "Run:"
    echo "  export PATH=\"\$HOME/.pulumi/bin:\$PATH\""
    exit 1
  fi
fi

pulumi login "file://$PROJECT_PATH"

PULUMI_YAML_DIR="$PROJECT_PATH/pulumi_yaml"
mkdir -p "$PULUMI_YAML_DIR"

cat > "$PULUMI_YAML_DIR/Pulumi.yaml" <<EOF
name: $GCP_PROJECT_NAME
main: $PULUMI_YAML_DIR
runtime: python
EOF

echo "✅ Pulumi.yaml created at $PULUMI_YAML_DIR/Pulumi.yaml"

# -----------------------------
# Start Hatchet
# -----------------------------
if [ -f "docker-compose.yaml" ] || [ -f "docker-compose.yml" ]; then
  echo "🎩 Starting Hatchet with Docker Compose"
  docker compose up -d
else
  echo "⚠️ docker-compose.yaml not found in $APP_DIR"
  echo "Skipping Hatchet startup."
fi

# -----------------------------
# Hatchet token
# -----------------------------
echo "🔑 Creating Hatchet client token"

HATCHET_CLIENT_TOKEN=""

if [ -f "docker-compose.yaml" ] || [ -f "docker-compose.yml" ]; then
  set +e
  HATCHET_CLIENT_TOKEN="$(
    docker compose run --rm --no-deps setup-config \
      /hatchet/hatchet-admin token create \
      --config /hatchet/config \
      --tenant-id 707d0855-80ab-4e1f-a156-f1c4546cbf52 2>/dev/null
  )"
  set -e
fi

if [ -z "$HATCHET_CLIENT_TOKEN" ]; then
  echo "⚠️ Could not auto-create Hatchet token."
  HATCHET_CLIENT_TOKEN="$(ask "Paste Hatchet client token manually")"
fi

# -----------------------------
# Create .env
# -----------------------------
echo "⚙️ Creating .env"

cat > ".env" <<EOF
# DAX Configuration
PROJECT_PATH=$PROJECT_PATH
ENVIRONMENT=dev

HATCHET_CLIENT_TOKEN="$HATCHET_CLIENT_TOKEN"
HATCHET_CLIENT_HOST_PORT=localhost:7077
HATCHET_CLIENT_TLS_STRATEGY=none
DAX_HATCHET_WORKER_NAME=dax-worker

PULUMI_CONFIG_PASSPHRASE_FILE=""
EOF

echo "✅ .env created at $APP_DIR/.env"

# -----------------------------
# Copy service account JSON
# -----------------------------
echo

SERVICE_ACCOUNT_DEST_PATH="$APP_DIR/service-account.json"

cp "$SERVICE_ACCOUNT_SOURCE_PATH" "$SERVICE_ACCOUNT_DEST_PATH"
chmod 600 "$SERVICE_ACCOUNT_DEST_PATH"

echo "✅ Service account JSON copied to:"
echo "   $SERVICE_ACCOUNT_DEST_PATH"

# -----------------------------
# Config YAML
# -----------------------------
CONFIG_DIR="$APP_DIR/config/env"
CONFIG_FILE="$CONFIG_DIR/dev.yaml"

mkdir -p "$CONFIG_DIR"

echo "📝 Updating config/env/dev.yaml"

if [ ! -f "$CONFIG_FILE" ]; then
  cat > "$CONFIG_FILE" <<EOF
name: dev
project_name:
gcp:project:
gcp:serviceAccount:
EOF
fi

update_yaml_key() {
  local file="$1"
  local key="$2"
  local value="$3"

  if grep -qE "^${key}:" "$file"; then
    sed -i "s|^${key}:.*|${key}: ${value}|" "$file"
  else
    echo "${key}: ${value}" >> "$file"
  fi
}

update_yaml_key "$CONFIG_FILE" "name" "dev"
update_yaml_key "$CONFIG_FILE" "project_name" "$GCP_PROJECT_NAME"
update_yaml_key "$CONFIG_FILE" "gcp:project" "$GCP_PROJECT_NAME"
update_yaml_key "$CONFIG_FILE" "gcp:serviceAccount" "$SERVICE_ACCOUNT_EMAIL"

echo "✅ Config updated at $CONFIG_FILE"

# -----------------------------
# Final instruction
# -----------------------------
echo
echo "✅ DAX local setup completed."
echo
echo "DAX repository:"
echo "  $DAX_REPO_DIR"
echo
echo "DAX project folder:"
echo "  $APP_DIR"
echo
echo "Service account JSON:"
echo "  $SERVICE_ACCOUNT_DEST_PATH"
echo
echo "Next steps:"
echo
echo "1. Start DAX service:"
echo "   cd $APP_DIR"
echo "   source .venv/bin/activate"
echo "   bash start.sh"
echo
echo "2. Open Hatchet dashboard:"
echo "   http://localhost:8080"
echo
echo "Default login:"
echo "  username: admin@example.com"
echo "  password: Admin123!!"
echo