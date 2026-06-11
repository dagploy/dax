APP_USER="dax"
APP_GROUP="dax"
ADMIN_GROUP="dax-admin"
APP_HOME="/home/${APP_USER}"
PROJECT_DIR="/opt/project"
OPT_DIR="/tmp/opt"

SERVICE_ACCOUNT="service-account.json"
SERVICE_ACCOUNT_FILE="${APP_HOME}/${SERVICE_ACCOUNT}"


auto_reload_after_stop_cos() {
  cat >/etc/systemd/system/google-startup-scripts.service.d/no-proxy.conf <<'EOF'
  [Service]
  Environment="NO_PROXY=169.254.169.254,metadata,metadata.google.internal,localhost,127.0.0.1"
  Environment="no_proxy=169.254.169.254,metadata,metadata.google.internal,localhost,127.0.0.1"
EOF
}

auto_reload_after_stop() {
  sudo mkdir -p /etc/systemd/system/google-startup-scripts.service.d

  sudo tee /etc/systemd/system/google-startup-scripts.service.d/no-proxy.conf >/dev/null <<'EOF'
  [Service]
  Environment="NO_PROXY=169.254.169.254,metadata,metadata.google.internal,metadata.google.internal.,localhost,127.0.0.1"
  Environment="no_proxy=169.254.169.254,metadata,metadata.google.internal,metadata.google.internal.,localhost,127.0.0.1"
EOF
}

load_secret_required() {
  local env_name="$1"
  local secret_name="$2"

  if [[ -z "${secret_name:-}" ]]; then
    echo "❌ Missing required secret name for ${env_name}" >&2
    return 1
  fi

  echo "==> Loading required secret ${env_name} from Secret Manager: ${secret_name}"

  set +x

  local value
  local err
  local rc

  err="$(mktemp)"

  if ! value="$(gcloud secrets versions access latest \
      --secret="${secret_name}" \
      --quiet 2>"$err")"; then

    rc=$?

    echo "❌ Failed to load required secret: ${secret_name}" >&2
    echo "Reason:" >&2
    sed 's/^/  /' "$err" >&2

    rm -f "$err"
    return "$rc"
  fi

  rm -f "$err"

  if [[ -z "$value" ]]; then
    echo "❌ Required secret is empty: ${secret_name}" >&2
    return 1
  fi

  export "${env_name}=${value}"
  unset value
}

load_secret_optional() {
  local env_name="$1"
  local secret_name="$2"

  if [[ -z "${secret_name:-}" ]]; then
    echo "==> Optional secret name missing for ${env_name}, skipping"
    export "${env_name}="
    return 0
  fi

  echo "==> Loading optional secret ${env_name} from Secret Manager: ${secret_name}"

  set +x

  local value
  if value="$(gcloud secrets versions access latest --secret="${secret_name}" --quiet 2>/dev/null)"; then
    export "${env_name}=${value}"
  else
    echo "==> Optional secret not found or not accessible: ${secret_name}"
    export "${env_name}="
  fi

  unset value
}


create_service_account_key() {
  load_secret_required "SERVICE_ACCOUNT_KEY" "$SERVICE_ACCOUNT_KEY"

  echo "$SERVICE_ACCOUNT_KEY" > "$SERVICE_ACCOUNT_FILE"

  chown "$APP_USER:$APP_USER" "$SERVICE_ACCOUNT_FILE"
  chmod 755 "$SERVICE_ACCOUNT_FILE"

  echo "✅ Service account key written to $SERVICE_ACCOUNT_FILE with correct permissions."
}



prepare_system_user() {
  echo "Preparing system user and groups"

  : "${APP_USER:?APP_USER is required}"
  : "${APP_GROUP:?APP_GROUP is required}"
  : "${APP_HOME:?APP_HOME is required}"
  : "${ADMIN_GROUP:?ADMIN_GROUP is required}"

  # ----------------------------
  # Ensure group exists
  # ----------------------------
  if ! getent group "$APP_GROUP" >/dev/null 2>&1; then
    echo "==> Creating group: $APP_GROUP"
    groupadd --system "$APP_GROUP"
  else
    echo "==> Group exists: $APP_GROUP"
  fi

  # ----------------------------
  # Ensure user exists
  # ----------------------------
  if ! id -u "$APP_USER" >/dev/null 2>&1; then
    echo "==> Creating user: $APP_USER"
    useradd \
      --system \
      --gid "$APP_GROUP" \
      --home-dir "$APP_HOME" \
      --create-home \
      --shell /bin/bash \
      "$APP_USER"
  else
    echo "==> User exists: $APP_USER"
  fi

  # ----------------------------
  # Lock password (safe to rerun)
  # ----------------------------
  passwd -l "$APP_USER" >/dev/null 2>&1 || true

  # ----------------------------
  # Ensure admin group exists
  # ----------------------------
  if ! getent group "$ADMIN_GROUP" >/dev/null 2>&1; then
    echo "==> Creating admin group: $ADMIN_GROUP"
    groupadd "$ADMIN_GROUP"
  else
    echo "==> Admin group exists: $ADMIN_GROUP"
  fi

  # ----------------------------
  # Ensure user in admin group
  # ----------------------------
  if ! id -nG "$APP_USER" | grep -qw "$ADMIN_GROUP"; then
    echo "==> Adding $APP_USER to $ADMIN_GROUP"
    usermod -aG "$ADMIN_GROUP" "$APP_USER"
  fi

  # ----------------------------
  # Ensure home exists + ownership
  # ----------------------------
  mkdir -p "$APP_HOME"
  chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"

  echo "==> Prepared system user"
  echo "    APP_USER=$APP_USER"
  echo "    APP_GROUP=$APP_GROUP"
  echo "    ADMIN_GROUP=$ADMIN_GROUP"
  echo "    APP_HOME=$APP_HOME"

  # set service account key
  create_service_account_key
}

mount_and_prepare_disks() {
  mkdir -p $OPT_DIR/cache_docker

  found_docker=""

  echo "INFO : Disk device name : ${DISK_DEVICE_NAMES[@]}"

  for disk in "${DISK_DEVICE_NAMES[@]}"; do
    dev_path="/dev/disk/by-id/google-$disk"
    mnt_point="$OPT_DIR/$disk"
    mkdir -p "$mnt_point"

    # Wait until device appears (max 150s)
    dev_ready=0
    for i in {1..30}; do
      if lsblk "$dev_path" &>/dev/null; then
        echo "[ $(date) ] $dev_path is ready"
        dev_ready=1
        break
      fi
      echo "[ $(date) ] Waiting for $dev_path..."
      sleep 10
    done

    # If still not ready, skip this disk
    if [ "$dev_ready" -ne 1 ]; then
      echo "[ $(date) ] ERROR: FAILED MOUNT DISK. $dev_path did not appear after waiting, skipping mount."
      continue
    fi

    # Now it's safe to mount
    if ! mount "$dev_path" "$mnt_point"; then
      echo "[ $(date) ] ERROR: FAILED MOUNT DISK. Failed to mount $dev_path on $mnt_point"
      continue
    fi

    # Use first docker folder found
    if [ -d "$mnt_point/docker" ] && [ -z "$found_docker" ]; then
      found_docker="$mnt_point/docker"
      ln -sfn "$found_docker" $OPT_DIR/cache_docker

      echo "INFO : Found Docker : $found_docker and mount into $OPT_DIR/cache_docker"
      echo "INFO: Docker cache successfully activated"
    fi

    if [ -d "$mnt_point/$MODEL_DIRNAME" ]; then
      MODEL_SOURCES+=("$mnt_point/$MODEL_DIRNAME")
    fi

    echo "INFO : MODEL SOURCES: ${MODEL_SOURCES[@]}"

  done
}

mount_and_prepare_disks_cos() {
  mkdir -p $OPT_DIR/cache_docker

  found_docker=""

  echo "INFO : Disk device name : ${DISK_DEVICE_NAMES[@]}"

  for disk in "${DISK_DEVICE_NAMES[@]}"; do
    dev_path="/dev/disk/by-id/google-$disk"
    mnt_point="$OPT_DIR/$disk"
    mkdir -p "$mnt_point"

    # Wait until device appears (max 150s)
    dev_ready=0
    for i in {1..30}; do
      if lsblk "$dev_path" &>/dev/null; then
        echo "[ $(date) ] $dev_path is ready"
        dev_ready=1
        break
      fi
      echo "[ $(date) ] Waiting for $dev_path..."
      sleep 10
    done

    # If still not ready, skip this disk
    if [ "$dev_ready" -ne 1 ]; then
      echo "[ $(date) ] ERROR: FAILED MOUNT DISK. $dev_path did not appear after waiting, skipping mount."
      continue
    fi

    # Now it's safe to mount
    if ! mount "$dev_path" "$mnt_point"; then
      echo "[ $(date) ] ERROR: FAILED MOUNT DISK. Failed to mount $dev_path on $mnt_point"
      continue
    fi

    # Use first docker folder found
    if [ -d "$mnt_point/docker" ] && [ -z "$found_docker" ]; then
      found_docker="$mnt_point/docker"
      ln -sfn "$found_docker" $OPT_DIR/cache_docker

      echo "🔧 INFO: Docker image is found! $found_docker and mount into $OPT_DIR/cache_docker"
      echo "🔧 INFO: Docker cache successfully activated"
    fi

    if [ -d "$mnt_point/$MODEL_DIRNAME" ]; then
      MODEL_SOURCES+=("$mnt_point/$MODEL_DIRNAME")
    fi

    echo "🔧 INFO : MODEL SOURCES: ${MODEL_SOURCES[@]}"

  done
}


prepare_model_mounts() {
  for model_dir in "${MODEL_SOURCES[@]}"; do
    for sub in "$model_dir"/*; do
      [ -d "$sub" ] || continue
      name=$(basename "$sub")
      MODEL_MOUNT_OPTS+=( "-v" "$sub:/app/$MODEL_DIRNAME/$name" )
    done
  done

  echo "INFO : MODEL MOUNT OPTS: ${MODEL_MOUNT_OPTS[@]}"

}

wait_for_apt() {
  echo "===> Waiting for APT to be ready..."

  local i=1

  while fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
        fuser /var/lib/apt/lists/lock >/dev/null 2>&1 || \
        fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do

    echo "Lock detected. Sleeping 10s (attempt $i/30)..."

    if [ "$i" -ge 30 ]; then
      echo "APT lock still held after 30 attempts. Continuing anyway..."
      break
    fi

    i=$((i + 1))
    sleep 10
  done

  sudo dpkg --configure -a >/dev/null 2>&1 || true
  sudo apt-get update -y -qq >/dev/null 2>&1
}


force_ipv4() {
  echo "🌐 Forcing apt to use IPv4..."

  # If this system doesn't have APT, skip entirely
  if [ ! -d /etc/apt/apt.conf.d ]; then
    echo "⚠️ APT not available on this system — skipping IPv4 forcing."
    return 0
  fi

  # Write APT IPv4 preference file
  cat <<'EOF' >/etc/apt/apt.conf.d/99force-ipv4
Acquire::ForceIPv4 "true";
EOF

  echo "✅ Configured apt to prefer IPv4 only."

  # Prefer IPv4 globally (skip if gai.conf doesn't exist)
  if [ -f /etc/gai.conf ] && ! grep -q '::ffff:0:0/96' /etc/gai.conf; then
    echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
  fi

  echo "🌐 IPv4 preference applied. Testing apt..."

  # Skip update if apt-get is not present
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "⚠️ apt-get not available — skipping apt update."
    return 0
  fi

  # Try apt update
  if ! apt-get update -y -qq; then
    echo "❌ apt-get failed: No Internet or proxy access issue."
    return 1
  else
    echo "✅ apt-get succeeded — Internet OK."
  fi
}

model_setup_cos() {
  # Ensure base directory exists
  mkdir -p $OPT_DIR/models

  if [ ${#MODEL_SOURCES[@]} -eq 0 ]; then
    echo "[WARN] No model sources found. Skipping symbolic link setup."
    return
  fi

  echo "[INFO] Setting up symbolic links for models ${MODEL_SOURCES[@]}"
  for model_path in "${MODEL_SOURCES[@]}"; do
    if [ -d "$model_path" ]; then
      for item in "$model_path"/*; do
        # Skip if no models exist
        [ -e "$item" ] || continue
        base_name=$(basename "$item")
        target="$OPT_DIR/models/$base_name"

        if [ -L "$target" ] || [ -e "$target" ]; then
          echo "[WARN] Skipping existing model link: $target"
          continue
        fi

        ln -s "$item" "$target"
        echo "[LINKED] $item -> $target"
      done
    else
      echo "[WARN] Model path not found: $model_path"
    fi
  done
}


model_setup() {
  # Ensure base directory exists
  mkdir -p $OPT_DIR/models

  if [ ${#MODEL_SOURCES[@]} -eq 0 ]; then
    echo "[WARN] No model sources found. Skipping symbolic link setup."
    return
  fi

  echo "[INFO] Setting up symbolic links for models ${MODEL_SOURCES[@]}"
  for model_path in "${MODEL_SOURCES[@]}"; do
    if [ -d "$model_path" ]; then
      for item in "$model_path"/*; do
        # Skip if no models exist
        [ -e "$item" ] || continue
        base_name=$(basename "$item")
        target="$OPT_DIR/models/$base_name"

        if [ -L "$target" ] || [ -e "$target" ]; then
          echo "[WARN] Skipping existing model link: $target"
          continue
        fi

        ln -s "$item" "$target"
        echo "[LINKED] $item -> $target"
      done
    else
      echo "[WARN] Model path not found: $model_path"
    fi
  done
}


set_docker_volume() {
  # Docker data dir mount
  DOCKER_DATA_DIR="$OPT_DIR/cache_docker/docker"

  if [ -d "$DOCKER_DATA_DIR" ]; then
    OPTIONAL_DOCKER_MOUNT="-v ${DOCKER_DATA_DIR}:/var/lib/docker"
  else
    OPTIONAL_DOCKER_MOUNT=""
  fi

  echo "INFO: DOCKER MOUNT : $OPTIONAL_DOCKER_MOUNT "
}

open_cos_host_firewall() {
  if [[ ${#OPEN_PORTS[@]} -eq 0 ]]; then
    echo "[INFO] No host firewall ports configured."
    return 0
  fi

  echo "[INFO] Opening COS host firewall ports: ${OPEN_PORTS[*]}"

  for port in "${OPEN_PORTS[@]}"; do
    sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null || \
      sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
  done
}
