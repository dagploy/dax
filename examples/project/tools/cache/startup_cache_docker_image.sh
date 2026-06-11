APP_USER="dax"
APP_GROUP="dax"
ADMIN_GROUP="dax-admin"
APP_HOME="/home/${APP_USER}"

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
}

prepare_system_user

export HOME=/tmp
mkdir -p /tmp/.docker

docker-credential-gcr configure-docker --registries us-docker.pkg.dev
docker-credential-gcr configure-docker --registries=asia-southeast1-docker.pkg.dev

# Prepare target mount points
DOCKER_MOUNT_PATH="$APP_HOME/cache_docker"
mkdir -p $DOCKER_MOUNT_PATH

DOCKER_DIRNAME="docker"

found_docker=""
disk_name=""

# Mount each disk, check for "docker" and "model" directories
for disk in "${DISK_DEVICE_NAMES[@]}"; do
  dev_path="/dev/disk/by-id/google-$disk"

  # format if unformatted
  if ! blkid "/dev/disk/by-id/google-$disk"; then
    mkfs.ext4 -F "$dev_path"
  fi

  # Create a mount point for the disk
  mnt_point="$APP_HOME/$disk"
  mkdir -p "$mnt_point"

  # NOTE: Disk attachment takes time, wait until device exists (max 60s)
  for i in {1..30}; do
    if [ -e "$dev_path" ]; then
      echo "[$(date)] Found $dev_path"
      break
    fi
    echo "[$(date)] Waiting for $dev_path..."
    sleep 10
  done

  mount "$dev_path" "$mnt_point" || true

  # If this disk has a docker folder and we haven't found one yet, use it
  if [ -d "$mnt_point/docker" ] && [ -z "$found_docker" ]; then
    found_docker="$dev_path"
    disk_name="$disk"
    mount "$found_docker" "$DOCKER_MOUNT_PATH"
  fi
done

fail() {
  echo "❌ ERROR: $1" >&2
  echo "Docker containers:" >&2
  docker ps -a >&2 || true
  echo "Docker logs:" >&2
  docker logs dax --tail=100 >&2 || true
  exit 1
}

# If no Docker folder was found, use the temporary disk
if [ -z "$found_docker" ]; then
  disk_name="${DISK_DEVICE_NAMES[0]}"
  dev_path="/dev/disk/by-id/google-$disk_name"

  mount "$dev_path" "$DOCKER_MOUNT_PATH" \
    || fail "Failed to mount $dev_path to $DOCKER_MOUNT_PATH"

  mkdir -p "$DOCKER_MOUNT_PATH/$DOCKER_DIRNAME" \
    || fail "Failed to create $DOCKER_MOUNT_PATH/$DOCKER_DIRNAME"

  echo "Create docker folder at $DOCKER_MOUNT_PATH/$DOCKER_DIRNAME"
fi

echo "Pulling Docker image: ${DOCKER_RUN}"

docker pull "${DOCKER_RUN}" \
  || fail "Failed to pull Docker image: ${DOCKER_RUN}"

# Common docker args
COMMON_ARGS=(
  --privileged
  --shm-size=1g
  --ulimit memlock=-1
  --ulimit stack=67108864
  --network="host"
  --restart unless-stopped
  -e MODE="remote"
  -e STACK="${STACK_NAME}"
  -v "$DOCKER_MOUNT_PATH/$DOCKER_DIRNAME:/var/lib/docker"
  -e DOCKER_TLS_CERTDIR=""
)

if [[ "${PUBLIC_MODE}" == "False" && -n "${PROXY:-}" ]]; then
  echo "[INFO] Running in private mode → using proxy..."
  docker run -d -it --name dax \
    "${COMMON_ARGS[@]}" \
    -e HTTP_PROXY="http://$PROXY" \
    -e HTTPS_PROXY="http://$PROXY" \
    -e NO_PROXY="localhost,127.0.0.1,::1,metadata.google.internal" \
    ${DOCKER_RUN}
else
  echo "[INFO] Running in public mode → direct internet access..."
  docker run -d -it --name dax \
    "${COMMON_ARGS[@]}" \
    ${DOCKER_RUN}
fi

sleep 5

# Wait for dax container to be running
until docker ps --filter name=dax --filter status=running \
      --format '{{.Names}}' | grep -qx dax; do
  sleep 5
done

__EXECUTION_SCRIPT__

# stop docker
docker stop dax

sleep 2

# Create snapshot and then image from it
docker run --rm --network host \
  google/cloud-sdk:slim \
  bash -c "
    gcloud config set project ${PROJECT}

    # detach the disk
    gcloud compute instances detach-disk "${VM_NAME}" --disk="$disk_name" --zone="${ZONE}"

    # Remove old image if it exists
    if gcloud compute images describe "${FIRST_IMAGE}" --project="${PROJECT}" >/dev/null 2>&1; then
      gcloud compute images delete "${FIRST_IMAGE}" --project="${PROJECT}" --quiet
    fi

    # Create image directly from the disk
    gcloud compute images create "${FIRST_IMAGE}" \
      --project="${PROJECT}" \
      --source-disk="$disk_name" \
      --source-disk-zone="${ZONE}" \
      --storage-location=us \
      --family="docker-lib"
  "

echo "Image ${FIRST_IMAGE} created successfully from disk: $disk_name "
