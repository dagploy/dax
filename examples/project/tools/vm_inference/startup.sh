OPT_DIR="/tmp/opt"
CONTAINER_NAME="dax"
DOCKER_METADATA_HOSTS="--add-host=metadata.google.internal:169.254.169.254 --add-host=metadata:169.254.169.254"
DOCKER_DATA_DIR="$OPT_DIR/cache_docker/docker"

APP_USER="dax"
APP_GROUP="dax"
ADMIN_GROUP="dax-admin"
APP_HOME="/home/${APP_USER}"

export HOME=/tmp
mkdir -p /tmp/.docker

# Setup GPU
GPU_VAL="${GPU:-0}"

pull_dax_container() {
  echo "==> Configuring Docker authentication for Artifact Registry..."

  # restart docker just in case
  if systemctl list-unit-files | grep -q '^docker.service'; then
    echo "[INFO] Docker service found. Restarting..."
    sudo systemctl daemon-reload
    sudo systemctl restart docker
    echo "[INFO] Docker restarted successfully."
  else
    echo "[WARN] docker.service not found. Skipping restart."
  fi

  # Check if user exists
  if id $APP_USER >/dev/null 2>&1; then
    echo "[INFO] User $APP_USER found. Preparing home directories..."
    sudo mkdir -p "$APP_HOME/.config/gcloud" "$APP_HOME/.docker"
    sudo chown -R $APP_USER:$ADMIN_GROUP "$APP_HOME/.config" "$APP_HOME/.docker"
  else
    echo "[WARN] User $APP_USER not found. Creating directories for root only..."
    sudo mkdir -p "$APP_HOME/.config/gcloud" "$APP_HOME/.docker"
  fi

  # --- Configure Docker credential helper for root (safe overwrite) ---
  if command -v docker-credential-gcr >/dev/null 2>&1; then
    echo "[INFO] docker-credential-gcr found — configuring Docker helper..."
    docker-credential-gcr configure-docker --registries us-docker.pkg.dev
    docker-credential-gcr configure-docker --registries=asia-southeast1-docker.pkg.dev
    docker-credential-gcr configure-docker --overwrite || true
  else
    echo "[WARN] docker-credential-gcr not found — skipping Docker credential helper setup."
  fi

  if command -v gcloud >/dev/null 2>&1; then
    echo "[INFO] gcloud found - configuring Docker auth for dev user..."
    runuser -l $APP_USER -c '
      if command -v gcloud >/dev/null 2>&1; then
        gcloud auth configure-docker us-docker.pkg.dev,asia-southeast1-docker.pkg.dev --quiet || true
      fi

      if command -v docker-credential-gcr >/dev/null 2>&1; then
        docker-credential-gcr configure-docker --registries us-docker.pkg.dev
        docker-credential-gcr configure-docker --registries=asia-southeast1-docker.pkg.dev
      fi
    '
  else
    echo "[WARN] gcloud not found — skipping dev user Docker auth configuration."
  fi

  echo "==> Pulling DAX container..."
  docker pull -q ${DOCKER_RUN}
  echo "✅ Completed pulling DAX image."
}

run_docker() {
  echo "[INFO] Starting pipeline container: $CONTAINER_NAME ..."

  # disable ECC
  /var/lib/nvidia/bin/nvidia-smi -e 0

  # HOTFIX https://github.com/NVIDIA/libnvidia-container/issues/176
  sysctl -w net.core.bpf_jit_harden=1

  # HOTFIX : Make port accessible from external IP for COS
  iptables -I INPUT -p tcp --dport ${PORT} -j ACCEPT

  __EXTRA_CONFIG__

  # Check if container already running
  if docker ps --filter "name=$CONTAINER_NAME" | grep -q "$CONTAINER_NAME"; then
    echo "[WARN] Container '$CONTAINER_NAME' already running."
    exit 0
  fi

  # Detect OS
  if [ -f /etc/os-release ]; then
    . /etc/os-release
  else
    ID="unknown"
  fi

  if [[ "$ID" == "cos" ]]; then
    echo "[INFO] Detected COS. Enable ports as hotfix for COS host firewall (https://cloud.google.com/container-optimized-os/docs/how-to/configure-firewall)."

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


    # COS has host firewall; open service ports explicitly.
    open_cos_host_firewall
  else
    echo "[INFO] Non-COS OS detected. Assuming no host firewall configuration needed."
  fi


  if [ "$GPU_VAL" -gt 0 ]; then
    echo "Run Docker with GPU for ${GPU} GPU(s)..."

    # Default GPU flag disabled
    GPU_FLAG=""

    if [[ "$ID" == "cos" ]]; then
      echo "[INFO] Detected Google COS. Skipping '--gpus all' (not supported)."

      # Fix bugs NVIDIA runtime in COS IMAGE by restarting docker and reconfiguring nvidia-ctk
      nvidia-ctk runtime configure --runtime=docker
      systemctl restart docker
    else
      # For Ubuntu or other images, check GPU support
      if command -v nvidia-smi >/dev/null 2>&1; then
        GPU_FLAG="--gpus all"
        echo "[INFO] Detected $PRETTY_NAME with GPU — enabling '--gpus all'."
      else
        echo "[WARN] No GPU detected — skipping '--gpus all'."
      fi
    fi

    model_mount_opts=()

    # Check if MODEL_SOURCES is defined and non-empty
    if [ ${#MODEL_SOURCES[@]} -eq 0 ]; then
      echo "[WARN] MODEL_SOURCES is empty. Skipping model mounts."
    else
      echo "[INFO] Preparing model volume mounts..."

      for model_dir in "${MODEL_SOURCES[@]}"; do
        if [ -d "$model_dir" ]; then
          for sub in "$model_dir"/*; do
            [ -d "$sub" ] || continue
            name=$(basename "$sub")
            resolved=$(readlink -f "$sub")
            model_mount_opts+=( "-v" "${resolved}:/app/${MODEL_DIRNAME}/${name}" )
            echo "[LINKED] $resolved -> /app/${MODEL_DIRNAME}/${name}"
          done
        else
          echo "[WARN] Skipping missing model source: $model_dir"
        fi
      done
    fi

    echo "[INFO] Models mount: ${model_mount_opts[@]}"
    echo "[INFO] Extra Volume Docker: ${EXTRA_VOLUME_DOCKER}"
    echo "[INFO] Proxy: ${EXTRA_PROXY}"
    echo "[INFO] Docker metadata hosts: ${DOCKER_METADATA_HOSTS}"
    echo "[INFO] OPTIONAL_DOCKER_MOUNT: ${OPTIONAL_DOCKER_MOUNT}"

    # Dont pass GPU --gpus all
    # Note: This will takes around 5-10 seconds to be completely ready
    # Make sure any docker exec after this to respect this delay
    docker run -d -it --privileged $GPU_FLAG --name $CONTAINER_NAME \
      --shm-size=1g --ulimit memlock=-1 --ulimit stack=67108864 \
      --device /dev/nvidia-uvm:/dev/nvidia-uvm \
      --device /dev/nvidiactl:/dev/nvidiactl \
      -v /var/lib/nvidia/lib64:/usr/local/nvidia/lib64 \
      -e LD_LIBRARY_PATH=/usr/local/nvidia/lib64 \
      -v /var/lib/nvidia/bin:/usr/local/nvidia/bin \
      --network="host" \
      --restart unless-stopped -e MODE="remote" -e STACK="${STACK_NAME}" \
      "${model_mount_opts[@]}" \
       ${EXTRA_VOLUME_DOCKER} \
       ${EXTRA_PROXY} \
       ${DOCKER_METADATA_HOSTS} \
       ${OPTIONAL_DOCKER_MOUNT} \
      -e DOCKER_TLS_CERTDIR="" \
      ${DOCKER_RUN}

    echo "✅ Completed executing DAX container ..."

  else
    echo "❌ NO GPU params being passed. Docker not running!"
  fi

  # adding delay to ensure the docker is ready
  sleep 5

  until docker ps --filter "name=${CONTAINER_NAME}" --filter "status=running" | grep -q "${CONTAINER_NAME}"; do
    echo "[WAIT] Waiting for container '${CONTAINER_NAME}' to start..."
    sleep 5
  done
  echo "[INFO] Container '${CONTAINER_NAME}' is now running."

  # Ensure Docker daemon/socket is definitely healthy (covers daemon restart)
  echo "[INFO] Verifying Docker daemon is available..."
  unset DOCKER_HOST DOCKER_TLS_VERIFY DOCKER_CERT_PATH  # avoid wrong socket/remote config
  until [ -S /var/run/docker.sock ] && docker info >/dev/null 2>&1; do
    echo "[WAIT] Docker daemon not ready yet, waiting..."
    sleep 5
  done
  echo "[INFO] Docker daemon confirmed healthy."

  # Ensure /app exists inside the container
  echo "[INFO] Waiting for /app to be ready inside container..."
  until docker exec "${CONTAINER_NAME}" bash -c "test -d /app" >/dev/null 2>&1; do
    echo "[WAIT] /app not present yet, retrying..."
    sleep 3
  done
  echo "[INFO] /app is available in container."

  # Push the per-mode Taskfile into /app/Taskfile.yaml
  # Make sure the indentation without spaces at beginning to avoid crash
  docker exec $CONTAINER_NAME bash -c 'cat > /app/Taskfile.yaml << "EOF"
__TASK_FILE__
EOF
'

  # Metadata base URL
  METADATA="http://metadata.google.internal/computeMetadata/v1"
  HDR="Metadata-Flavor: Google"

  # Try to get EXTERNAL IP
  EXTERNAL_IP=$(curl -fs -H "$HDR" \
    "$METADATA/instance/network-interfaces/0/access-configs/0/external-ip" || true)

  LLM_IP="$EXTERNAL_IP"

  # If empty, get INTERNAL IP
  if [ -z "$LLM_IP" ]; then
    INTERNAL_IP=$(curl -fs -H "$HDR" \
      "$METADATA/instance/network-interfaces/0/ip" || true)

    if [ -z "$INTERNAL_IP" ]; then
      echo "[ERROR] Could not fetch external or internal IP."
      exit 1
    else
      LLM_IP="$INTERNAL_IP"
      echo "[INFO] Using INTERNAL IP: $LLM_IP for openwebui"
    fi
  else
    echo "[INFO] Using EXTERNAL IP: $LLM_IP for openwebui"
  fi

  # Pass LLM_IP explicitly into the container
  docker exec -e LLM_IP="$LLM_IP" "$CONTAINER_NAME" \
    bash -lc 'nohup task openwebui >/tmp/openwebui.log 2>&1 &'

  # Push the per-mode Taskfile into /app/Taskfile.yaml
  docker exec "$CONTAINER_NAME" bash -lc 'task run'
}


force_ipv4

mount_and_prepare_disks_cos
model_setup_cos
prepare_model_mounts
pull_dax_container
set_docker_volume
run_docker