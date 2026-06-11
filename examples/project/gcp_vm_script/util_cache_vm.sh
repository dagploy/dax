create_disk_images() {
  echo "Preparing disk images..."

  for disk in "${DISK_DEVICE_NAMES[@]}"; do
    mnt_point="$MNT_BASE/$disk"
    MNT_PATHS+=("$mnt_point")

    dev_path="/dev/disk/by-id/google-$disk"

    # Wait until the device exists (max 60s)
    for attempt in {1..30}; do
      if [ -e "$dev_path" ]; then
        echo "[$(date)] Found $dev_path"
        break
      fi
      echo "[$(date)] Waiting for $dev_path..."
      sleep 2
    done

    if [ ! -e "$dev_path" ]; then
      echo "❌ Device $dev_path not found after waiting. Skipping."
      continue
    fi

    # Create filesystem if not already present
    if ! blkid "$dev_path" >/dev/null 2>&1; then
      echo "[$(date)] No filesystem found on $dev_path — creating ext4..."
      mkfs.ext4 -F "$dev_path"
    else
      echo "[$(date)] Filesystem already exists on $dev_path — skipping format."
    fi

    mkdir -p "$mnt_point"

    # Mount safely, only if not already mounted
    if ! mountpoint -q "$mnt_point"; then
      echo "[$(date)] Mounting $dev_path → $mnt_point"
      mount "$dev_path" "$mnt_point"
    else
      echo "[$(date)] $mnt_point already mounted."
    fi
  done
}

disk_replication() {
  # Copy the model to other disks if any
  if [ "${#MNT_PATHS[@]}" -le 1 ]; then
    echo "Only one disk mounted; skipping replication."
    return 0
  fi

  for i in "${!MNT_PATHS[@]}"; do
    # Skip the first path (source)
    if [ "$i" -eq 0 ]; then
      continue
    fi

    src_path="${MNT_PATHS[0]}/$TARGET_DIRNAME"
    dest_path="${MNT_PATHS[$i]}/$TARGET_DIRNAME"

    echo "Replicating model from ${src_path} → ${dest_path}"

    # Ensure source exists
    if [ ! -d "$src_path" ]; then
      echo "❌ Source directory not found: $src_path"
      continue
    fi

    # Ensure destination directory exists
    mkdir -p "$dest_path"

    # Copy contents recursively (-a preserves attributes)
    cp -a "$src_path/." "$dest_path/"
  done
}


create_image_from_disk() {
  # Create new image for each disk
  for i in "${!DISK_DEVICE_NAMES[@]}"; do
    disk_name="${DISK_DEVICE_NAMES[$i]}"
    image_name="${IMAGES[$i]}"

    echo "Unmounting ${MNT_PATHS[$i]} ..."
    umount "${MNT_PATHS[$i]}"

    echo "Detaching disk: ${disk_name} ..."
    gcloud compute instances detach-disk "${VM_NAME}" \
      --disk="${disk_name}" \
      --zone="${ZONE}" \
      --quiet

    # Check if the image exists
    if gcloud compute images describe "${image_name}" --project="${PROJECT}" >/dev/null 2>&1; then
      echo "Image ${image_name} exists. Deleting..."
      gcloud compute images delete "${image_name}" --project="${PROJECT}" --quiet
    else
      echo "Image ${image_name} does not exist. Proceeding to create."
    fi

    # Create a new image from the disk
    echo "Creating image ${image_name} from ${disk_name} ..."
    gcloud compute images create "${image_name}" \
      --project="${PROJECT}" \
      --source-disk="${disk_name}" \
      --source-disk-zone="${ZONE}" \
      --storage-location="us" \
      --family=${FAMILY}

    echo "✅ Image ${image_name} created successfully from disk: ${disk_name}"
  done
}
