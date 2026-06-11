# USING GOOGLE COS IMAGE, NOT UBUNTU. DO NOT ASSUME UBUNTU PACKAGES ARE AVAILABLE.
# This script is run as root. 
# Only tmp that writeable.

OPT_DIR="/tmp/opt"
CONTAINER_NAME="dax"
DOCKER_METADATA_HOSTS="--add-host=metadata.google.internal:169.254.169.254 --add-host=metadata:169.254.169.254"
DOCKER_DATA_DIR="$OPT_DIR/cache_docker/docker"

export HOME=/tmp
mkdir -p /tmp/.docker

docker-credential-gcr configure-docker --registries us-docker.pkg.dev
docker-credential-gcr configure-docker --registries asia-southeast1-docker.pkg.dev

# Prepare target mount points
mkdir -p "$OPT_DIR/cache_docker"

found_docker=""

# DISK_DEVICE_NAMES must be a bash array, for example:
# DISK_DEVICE_NAMES=(test-disk-0)
disk_names=("${DISK_DEVICE_NAMES[@]}")

# Mount each disk, check for "docker" and "model" directories
for disk in "${disk_names[@]}"; do
  dev_path="/dev/disk/by-id/google-$disk"

  for i in {1..30}; do
    if [ -e "$dev_path" ]; then
      echo "[$(date)] Found $dev_path"
      break
    fi

    echo "[$(date)] Waiting for $disk attempt $i..."
    sleep 5
  done

  if [ ! -e "$dev_path" ]; then
    echo "[$(date)] Disk device not found after waiting: $dev_path"
    continue
  fi

  mnt_point="$OPT_DIR/$disk"
  mkdir -p "$mnt_point"

  if ! mountpoint -q "$mnt_point"; then
    mount "$dev_path" "$mnt_point" || {
      echo "[$(date)] Failed to mount $dev_path to $mnt_point"
      continue
    }
  fi

  # If this disk has a docker folder and we have not found one yet, use it.
  if [ -d "$mnt_point/docker" ] && [ -z "$found_docker" ]; then
    found_docker="$dev_path"

    if ! mountpoint -q "$OPT_DIR/cache_docker"; then
      mount "$found_docker" "$OPT_DIR/cache_docker" || {
        echo "[$(date)] Failed to mount docker cache disk to $OPT_DIR/cache_docker"
        continue
      }
    fi

    echo "[$(date)] Docker cache disk mounted from $found_docker"
  fi
done

systemctl start docker

until docker info >/dev/null 2>&1; do
  echo "⏳ Waiting for Docker to be ready..."
  sleep 2
done

# Retry pull
for i in 1 2 3; do
  if docker pull ${DOCKER_RUN}; then
    echo "✅ Pulled image successfully"
    break
  fi

  echo "⚠️ Retry $i..."
  sleep 5
done

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


# HOTFIX: Increase vm.max_map_count for COS
echo "[INFO] Current vm.max_map_count:"
cat /proc/sys/vm/max_map_count || true

echo "[INFO] Setting vm.max_map_count=262144 on COS host"
sysctl -w vm.max_map_count=262144

echo "[INFO] New vm.max_map_count:"
cat /proc/sys/vm/max_map_count

echo "Starting container without GPU support"

docker rm -f dax >/dev/null 2>&1 || true

PROXY_ARGS=()

if [[ -n "${HTTP_PROXY:-}" ]]; then
  PROXY_ARGS+=(-e "HTTP_PROXY=${HTTP_PROXY}")
fi

if [[ -n "${HTTPS_PROXY:-}" ]]; then
  PROXY_ARGS+=(-e "HTTPS_PROXY=${HTTPS_PROXY}")
fi

if [[ -n "${NO_PROXY:-}" ]]; then
  PROXY_ARGS+=(-e "NO_PROXY=${NO_PROXY}")
fi

METADATA_ARGS=()
if [[ -n "${DOCKER_METADATA_HOSTS:-}" ]]; then
  # shellcheck disable=SC2206
  METADATA_ARGS=(${DOCKER_METADATA_HOSTS})
fi

docker run -d -it --privileged \
  --name "$CONTAINER_NAME" \
  --shm-size=1g \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  "${METADATA_ARGS[@]}" \
  --network="host" \
  --restart unless-stopped \
  -e MODE="remote" \
  -e STACK="${STACK_NAME}" \
  -v "${DOCKER_DATA_DIR}:/var/lib/docker" \
  -v "${OPT_DIR}:/opt" \
  -e DOCKER_TLS_CERTDIR="" \
  "${PROXY_ARGS[@]}" \
  ${DOCKER_RUN}

sleep 5

# Wait for dax container to be running
until docker ps \
  --filter name=dax \
  --filter status=running \
  --format '{{.Names}}' | grep -qx dax; do
  echo "[$(date)] Waiting for dax container..."
  sleep 5
done

# Push the per-mode Taskfile into /app/Taskfile.yaml
docker exec dax bash -c 'cat > /app/Taskfile.yaml << "EOF"
__TASK_FILE__
EOF
'

# Run command from Taskfile
echo "Executing Taskfile Run ..."
docker exec dax bash -c "task run" &

echo "Taskfile run finished!"

sleep 10

echo "STARTUP_SCRIPT_COMPLETE"

# Give delay for monitoring script to capture STARTUP_SCRIPT_COMPLETE
sleep 10