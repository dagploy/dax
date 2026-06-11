#!/usr/bin/env bash
set -euo pipefail

CURRENT_USER="${SUDO_USER:-$USER}"
CURRENT_HOME="$(eval echo "~$CURRENT_USER")"

APP_USER="dax"
APP_GROUP="dax"
ADMIN_GROUP="dax-admin"
APP_HOME="/home/${APP_USER}"

DAX_DIR="/opt/dax"
PROJECT_DIR="$DAX_DIR/examples/project"

PULUMI_HOME="${APP_HOME}/.pulumi"
PULUMI_INSTALL_ROOT="/opt/pulumi"
PULUMI_BIN="/usr/local/bin/pulumi"

SYSTEMD_UNIT="/etc/systemd/system/dax.service"

say() {
  echo
  echo "==> $*"
}

require_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "This script must be run as root."
    echo "Use: sudo bash install.sh"
    exit 1
  fi
}

require_sudo_user() {
  if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = "root" ]; then
    echo "This script should be run via sudo by a real user."
    echo "Example: sudo bash install.sh"
    exit 1
  fi
}

docker_exists() {
  command -v docker >/dev/null 2>&1
}

install_docker() {
  say "Installing Docker..."
  apt-get update -y
  apt-get install -y ca-certificates curl

  say "Setting up Docker GPG key"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  say "Adding Docker repository"
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list

  say "Installing Docker packages"
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  say "Enabling Docker service"
  systemctl enable docker
  systemctl start docker
}

run_as_login_user() {
  sudo -iu "$CURRENT_USER" bash -s
}

install_system_packages() {
  say "Installing base packages"
  apt-get update

  apt-get install -y --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    lsof \
    tmux \
    git \
    ca-certificates \
    jq \
    gnupg \
    curl \
    zip \
    unzip \
    wget \
    vim \
    tmux \
    lsb-release \
    bash \
    python3 \
    python3-pip \
    python3-venv
  rm -rf /var/lib/apt/lists/*
}

install_gcloud() {
  say "Setting up Google Cloud SDK repository"
  mkdir -p /usr/share/keyrings
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg

  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list

  say "Installing Google Cloud CLI"
  apt-get update
  apt-get install -y --no-install-recommends \
    google-cloud-cli \
    google-cloud-sdk-gke-gcloud-auth-plugin
  rm -rf /var/lib/apt/lists/*
}

prepare_system_dirs() {
  say "Preparing system users, groups, and directories"

  # Create service group only if missing
  if getent group "$APP_GROUP" >/dev/null 2>&1; then
    echo "==> Group already exists, skipping: $APP_GROUP"
  else
    echo "==> Creating group: $APP_GROUP"
    groupadd --system "$APP_GROUP"
  fi

  # Create service user only if missing
  if id -u "$APP_USER" >/dev/null 2>&1; then
    echo "==> User already exists, skipping: $APP_USER"
  else
    echo "==> Creating user: $APP_USER"
    useradd \
      --system \
      --gid "$APP_GROUP" \
      --home-dir "$APP_HOME" \
      --create-home \
      --shell /bin/bash \
      "$APP_USER"
  fi

  # Allow sudo -iu dax, but block password login
  passwd -l "$APP_USER" >/dev/null 2>&1 || true

  # Shared editor group for humans
  if getent group "$ADMIN_GROUP" >/dev/null 2>&1; then
    echo "==> Admin group already exists, skipping: $ADMIN_GROUP"
  else
    echo "==> Creating admin group: $ADMIN_GROUP"
    groupadd "$ADMIN_GROUP"
  fi

  # Optional: let dax also access files via shared editor group
  usermod -aG "$ADMIN_GROUP" "$APP_USER" || true

  mkdir -p \
    "$APP_HOME" \
    "$DAX_DIR"

  # Service-owned runtime/home
  chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"
  chmod 755 "$APP_HOME"

  # Editable app directory:
  # root owns it, dax-admin can edit it
  chown -R root:"$ADMIN_GROUP" "$DAX_DIR"

  # Directories: rwxrwsr-x
  find "$DAX_DIR" -type d -exec chmod 2775 {} +

  # Files: rw-rw-r--
  find "$DAX_DIR" -type f -exec chmod 0664 {} +

  say "System users, groups, and directories are ready"
}

install_uv() {
  say "Installing uv system-wide"

  if ! command -v uv >/dev/null 2>&1; then
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' RETURN

    (
      export HOME="$tmpdir"
      curl -LsSf https://astral.sh/uv/install.sh | sh
    )

    install -m 0755 "$tmpdir/.local/bin/uv" /usr/local/bin/uv
  fi

  uv --version
}

install_pulumi() {
  say "Installing Pulumi system-wide"

  mkdir -p "$PULUMI_INSTALL_ROOT"

  if ! [ -x "$PULUMI_BIN" ] || ! "$PULUMI_BIN" version >/dev/null 2>&1; then
    curl -fL https://get.pulumi.com | /bin/bash -s -- --install-root "$PULUMI_INSTALL_ROOT" --no-edit-path
    
    if [ ! -x "${PULUMI_INSTALL_ROOT}/bin/pulumi" ]; then
      echo "ERROR: Pulumi install failed"
      find "$PULUMI_INSTALL_ROOT" -type f -name pulumi 2>/dev/null || true
      exit 1
    fi

    ln -sf "${PULUMI_INSTALL_ROOT}/bin/pulumi" "$PULUMI_BIN"
  fi

  mkdir -p "$PULUMI_HOME"
  chown -R "$APP_USER:$APP_GROUP" "$PULUMI_HOME"

  sudo -u "$APP_USER" env HOME="$APP_HOME" PULUMI_HOME="$PULUMI_HOME" "$PULUMI_BIN" login --local

  sudo -u "$APP_USER" bash -lc '
    export PULUMI_HOME="'"$PULUMI_HOME"'"
    '"$PULUMI_BIN"' plugin install resource command
  '
}

install_hatchet_cli() {
  say "Installing Hatchet system-wide"
  curl -fsSL https://install.hatchet.run/install.sh | bash
  hatchet --version
}

setup_env_file() {
  ENV_FILE="$PROJECT_DIR/.env"
  TENANT_ID="${HATCHET_TENANT_ID:-707d0855-80ab-4e1f-a156-f1c4546cbf52}"

  cd "$PROJECT_DIR"

  echo "Creating Hatchet client token..."

  # Capture both stdout/stderr because Docker Compose warnings can appear mixed.
  RAW_OUTPUT="$(
    docker compose run --rm --no-deps setup-config \
      /hatchet/hatchet-admin token create \
      --config /hatchet/config \
      --tenant-id "$TENANT_ID" 2>&1
  )"

  # Extract JWT-looking token from noisy output.
  # This ignores docker warnings and lines like "cleaning up server config".
  HATCHET_CLIENT_TOKEN="$(
    printf '%s\n' "$RAW_OUTPUT" \
      | grep -Eo 'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+' \
      | tail -n 1
  )"

  if [ -z "$HATCHET_CLIENT_TOKEN" ]; then
    echo "❌ Failed to extract Hatchet token."
    echo
    echo "Raw output:"
    echo "$RAW_OUTPUT"
    exit 1
  fi

  echo "Writing $ENV_FILE..."

  cat > "$ENV_FILE" <<EOF
# DAX Configuration
PROJECT_PATH=$PROJECT_DIR
ENVIRONMENT=dev

HATCHET_CLIENT_TOKEN="$HATCHET_CLIENT_TOKEN"
HATCHET_CLIENT_HOST_PORT=localhost:7077
HATCHET_CLIENT_TLS_STRATEGY=none
DAX_HATCHET_WORKER_NAME=dax-worker

PULUMI_CONFIG_PASSPHRASE_FILE=""
EOF

  chmod 755 "$ENV_FILE"

  echo "✅ Hatchet token exported to $ENV_FILE"
}


validate_gcloud_context() {
  say "Validating GCP context"

  CURRENT_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
  CURRENT_SA="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"

  if [ -z "$CURRENT_PROJECT" ] || [ "$CURRENT_PROJECT" = "(unset)" ]; then
    echo "❌ No active GCP project found. Run: gcloud config set project <PROJECT_ID>"
    exit 1
  fi

  if [ -z "$CURRENT_SA" ]; then
    echo "❌ No active GCP account found. Run: gcloud auth login or activate-service-account"
    exit 1
  fi

  echo "➡️  Project: $CURRENT_PROJECT"
  echo "➡️  Active Account: $CURRENT_SA"
}


deploy_archive() {
  if [ -z "${DAX_DIR:-}" ] || [ "$DAX_DIR" = "/" ]; then
    echo "❌ Refusing to deploy: invalid DAX_DIR='$DAX_DIR'"
    exit 1
  fi

  say "Preparing clean project root: ${DAX_DIR}"
  rm -rf "$DAX_DIR"
  mkdir -p "$(dirname "$DAX_DIR")"

  say "Cloning example DAX project into ${DAX_DIR}"

  if ! git clone "https://github.com/dagploy/dax.git" "$DAX_DIR"; then
    echo "❌ git clone failed"
    echo "DAX_DIR=$DAX_DIR"
    echo "Parent dir=$(dirname "$DAX_DIR")"
    echo "Current user=$(whoami)"
    echo "Current pwd=$(pwd)"
    echo "Proxy env:"
    env | grep -i proxy || true
    exit 1
  fi

  say "Applying ownership and permissions"

  # Editable app tree: root owns, dax-admin can edit
  chown -R "$APP_USER:$ADMIN_GROUP" "$DAX_DIR"

  # Runtime dirs stay service-owned
  if [ -d "$DAX_DIR/.venv" ]; then
    chown -R "$APP_USER:$APP_GROUP" "$DAX_DIR/.venv"
  fi

  # Directories inherit dax-admin group
  find "$DAX_DIR" -type d -exec chmod 2775 {} +

  # Files are group-writable by editors
  find "$DAX_DIR" -type f -exec chmod 0664 {} +
}

start_hatchet() {
  say "Starting Hatchet server"

  if [ ! -f "$PROJECT_DIR/docker-compose.yaml" ]; then
    echo "❌ No docker-compose.yaml found in $PROJECT_DIR"
    exit 1
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "❌ docker is not installed"
    exit 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    echo "❌ docker compose plugin is not installed"
    exit 1
  fi

  if ! sudo -u "$APP_USER" bash -lc "cd '$PROJECT_DIR' && docker compose up -d"; then
    echo "❌ docker compose up -d failed"
    echo "PROJECT_DIR=$PROJECT_DIR"
    echo "Current user=$(whoami)"
    echo "Docker status:"
    systemctl status docker --no-pager || true
    echo "Docker compose config:"
    sudo -u "$APP_USER" bash -lc "cd '$PROJECT_DIR' && docker compose config" || true
    exit 1
  fi

  echo "➡️  DAX services started with Docker Compose"
}

setup_python_env() {
  say "Syncing Python environment with uv"

  sudo -iu "$APP_USER" bash <<EOF
set -euo pipefail

cd "$DAX_DIR"

uv sync --python 3.12 --reinstall
EOF
}

copy_service_account() {
  say "Creating service account JSON from Secret Manager into ${DAX_DIR}"

  SECRET_NAME="dax-service-account-key"
  DEST_PATH="${DAX_DIR}/service-account.json"
  ENV_FILE="${DAX_DIR}/.env"

  if ! command -v gcloud >/dev/null 2>&1; then
    echo "❌ gcloud CLI not found"
    exit 1
  fi

  mkdir -p "$DAX_DIR"

  # Read secret and write directly to file
  if ! gcloud secrets versions access latest \
    --secret="$SECRET_NAME" \
    > "$DEST_PATH"; then
    echo "❌ Failed to read secret: $SECRET_NAME"
    rm -f "$DEST_PATH"
    exit 1
  fi

  # Validate JSON
  if ! python3 -m json.tool "$DEST_PATH" >/dev/null 2>&1; then
    echo "❌ Secret content is not valid JSON"
    rm -f "$DEST_PATH"
    exit 1
  fi

  # Secure service account file
  chown "$APP_USER:$APP_GROUP" "$DEST_PATH"
  chmod 755 "$DEST_PATH"

  # Create .env if missing, otherwise keep existing content
  if [ ! -f "$ENV_FILE" ]; then
    echo "➡️  .env not found. Creating: $ENV_FILE"
    touch "$ENV_FILE"
    chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
    chmod 755 "$ENV_FILE"
  else
    echo "➡️  .env found. Updating: $ENV_FILE"
  fi

  # Remove old GOOGLE_APPLICATION_CREDENTIALS entries if they exist
  sed -i '/^export[[:space:]]\+GOOGLE_APPLICATION_CREDENTIALS=/d' "$ENV_FILE"
  sed -i '/^GOOGLE_APPLICATION_CREDENTIALS=/d' "$ENV_FILE"

  # Ensure file ends with newline before appending
  if [ -s "$ENV_FILE" ] && [ "$(tail -c 1 "$ENV_FILE")" != "" ]; then
    echo >> "$ENV_FILE"
  fi

  # Add fresh exported env var
  echo "GOOGLE_APPLICATION_CREDENTIALS=\"$DEST_PATH\"" >> "$ENV_FILE"

  # Secure .env
  chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
  chmod 755 "$ENV_FILE"

  # Export immediately for the current installer process
  export GOOGLE_APPLICATION_CREDENTIALS="$DEST_PATH"

  echo "➡️  Created service account JSON at $DEST_PATH from secret: $SECRET_NAME"
  echo "➡️  Created/updated env file: $ENV_FILE"
  echo "➡️  Exported GOOGLE_APPLICATION_CREDENTIALS=$DEST_PATH"
}

set_project() {
  echo "Creating Pulumi.yaml"
  DEST_PATH="${DAX_DIR}/service-account.json"

  PULUMI_DIR="$PROJECT_DIR/pulumi_yaml"
  PULUMI_FILE="$PULUMI_DIR/Pulumi.yaml"
  PULUMI_MAIN="$PROJECT_DIR"

  if ! command -v gcloud >/dev/null 2>&1; then
    echo "❌ gcloud CLI not found"
    exit 1
  fi

  mkdir -p "$PULUMI_DIR"
  chmod 0777 "$PULUMI_DIR"

  cat > "$PULUMI_FILE" <<EOF
name: ${CURRENT_PROJECT}
main: ${PROJECT_DIR}/pulumi_yaml
runtime: python
EOF

  chown -R "$APP_USER:$APP_GROUP" "$PULUMI_DIR"

  if [ ! -f "$DEST_PATH" ]; then
    echo "❌ Service account file not found: $DEST_PATH"
    exit 1
  fi

  cp "$DEST_PATH" "$PROJECT_DIR/service-account.json"
  chmod 0664 "$PROJECT_DIR/service-account.json"

  say "Pulumi.yaml created at ${PULUMI_FILE}"
  say "Service account copied to ${PROJECT_DIR}/service-account.json"
}


install_dax() {
  echo "Installing DAX as dax user"

  if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found"
    echo "Install uv first: https://docs.astral.sh/uv/"
    exit 1
  fi

  if ! id -u dax >/dev/null 2>&1; then
    echo "❌ User dax does not exist"
    exit 1
  fi

  if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ Project directory not found: $PROJECT_DIR"
    exit 1
  fi

  if [ ! -d "$DAX_DIR" ]; then
    echo "❌ DAX directory not found: $DAX_DIR"
    exit 1
  fi

  chown -R dax:dax "$PROJECT_DIR/.venv" 2>/dev/null || true

  sudo -u dax -H bash -lc "
    set -e
    cd '$PROJECT_DIR'
    uv venv --python=3.12
    source '$PROJECT_DIR/.venv/bin/activate'
    cd '$DAX_DIR'
    uv pip install dagploy-dax
  "

  say "DAX installed into ${PROJECT_DIR}/.venv as dax user"
}

configure_dev_yaml() {
  echo "Configuring dev.yaml"

  DEV_YAML="$PROJECT_DIR/config/env/dev.yaml"

  if ! id -u "$APP_USER" >/dev/null 2>&1; then
    echo "❌ User does not exist: $APP_USER"
    exit 1
  fi

  if [ -z "${CURRENT_PROJECT:-}" ]; then
    echo "❌ CURRENT_PROJECT is empty"
    exit 1
  fi

  if [ -z "${CURRENT_SA:-}" ]; then
    echo "❌ CURRENT_SA is empty"
    exit 1
  fi

  if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ Project directory not found: $PROJECT_DIR"
    exit 1
  fi

  sudo -u "$APP_USER" -H bash -lc "
    set -e

    PROJECT_DIR='$PROJECT_DIR'
    DEV_YAML='$DEV_YAML'
    CURRENT_PROJECT='$CURRENT_PROJECT'
    CURRENT_SA='$CURRENT_SA'

    mkdir -p \"\$(dirname \"\$DEV_YAML\")\"
    touch \"\$DEV_YAML\"

    update_yaml_value() {
      local key=\"\$1\"
      local value=\"\$2\"
      local file=\"\$3\"

      if grep -q \"^\${key}:\" \"\$file\"; then
        sed -i \"s|^\${key}:.*|\${key}: \${value}|\" \"\$file\"
      else
        printf '%s: %s\n' \"\$key\" \"\$value\" >> \"\$file\"
      fi
    }

    update_yaml_value 'project_name' \"\$CURRENT_PROJECT\" \"\$DEV_YAML\"
    update_yaml_value 'gcp:project' \"\$CURRENT_PROJECT\" \"\$DEV_YAML\"
    update_yaml_value 'gcp:serviceAccount' \"\$CURRENT_SA\" \"\$DEV_YAML\"
  "

  say "Configured ${DEV_YAML}"
}


run_dax() {
  echo "Starting DAX"

  TMUX_SESSION="dax"

  if ! command -v tmux >/dev/null 2>&1; then
    echo "❌ tmux not found"
    echo "Install it first: sudo apt-get install -y tmux"
    exit 1
  fi

  if ! id -u "$APP_USER" >/dev/null 2>&1; then
    echo "❌ User does not exist: $APP_USER"
    exit 1
  fi

  if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ Project directory not found: $PROJECT_DIR"
    exit 1
  fi

  if [ ! -f "$PROJECT_DIR/start.sh" ]; then
    echo "❌ start.sh not found: $PROJECT_DIR/start.sh"
    exit 1
  fi

  chmod +x "$PROJECT_DIR/start.sh"

# Create tmux config as dax user
  runuser -u "$APP_USER" -- bash -lc '
    set -e

    cat > "$HOME/.tmux.conf" <<EOF
# Enable mouse scroll
set -g mouse on

# Bigger scrollback history
set -g history-limit 50000

# Better terminal support
set -g default-terminal "screen-256color"

# Start window numbering at 1
set -g base-index 1
setw -g pane-base-index 1
EOF

    chmod 0644 "$HOME/.tmux.conf"
  '

  # If tmux session already exists, skip creating a new one
  if sudo -u "$APP_USER" -H tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "==> tmux session already exists, skipping: $TMUX_SESSION"
    echo "Attach with: sudo -u $APP_USER tmux attach -t $TMUX_SESSION"
    return 0
  fi

  sudo -u "$APP_USER" -H bash -lc "
    set -e
    cd '$PROJECT_DIR'
    tmux new-session -d -s '$TMUX_SESSION' 'bash start.sh'
  "

  say "DAX started in tmux session: ${TMUX_SESSION}"
  echo "Attach with: sudo -u $APP_USER tmux attach -t $TMUX_SESSION"
}

installation() {
    require_root
    validate_gcloud_context
    prepare_system_dirs

    if docker_exists; then
     say "Docker already installed: $(docker --version)"
    else
     install_docker
     say "Docker installation complete"
    fi

    # add docker permission
    sudo usermod -aG docker $APP_USER
    sudo usermod -aG docker $CURRENT_USER
    sudo systemctl restart docker

    install_uv
    deploy_archive
    install_pulumi
    setup_python_env
    start_hatchet
    install_hatchet_cli
    setup_env_file
    copy_service_account
    set_project
    install_dax
    configure_dev_yaml
    run_dax

    say "Installation Completed!"
    echo "journalctl -u dax.service -f"
}

write_usage_note() {
  cat <<EOF
  - App updates use the user home path
EOF
}


write_usage_note() {
  cat <<EOF

Layout:
  App:    $DAX_DIR
  Pulumi: $PULUMI_HOME

Notes:
  - System packages were installed as root.

Notes:
  - Login as the dax user: sudo -iu dax
  - The project location located at: "$PROJECT_DIR"
  - Connect to tmux with: sudo -iu dax -- tmux attach -t dax
EOF
}

main() {
  require_root
  require_sudo_user
  install_system_packages
  install_gcloud

  installation

  # grant permission
  sudo usermod -aG dax-admin $CURRENT_USER
  sudo usermod -aG dax-admin $APP_USER

  write_usage_note

}


main "$@"