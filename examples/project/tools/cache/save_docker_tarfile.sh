

# --- PREP MOUNT PATH ---
TARGET_DIRNAME="${MNT_PATHS[0]}/tar"

mkdir -p "$TARGET_DIRNAME"
echo "[INFO] Using mount target: $TARGET_DIRNAME"
echo "[INFO] Docker : ${DOCKER_IMAGES[@]}"

# --- PULL, SAVE, AND MOVE EACH IMAGE ---
for docker_image in "${DOCKER_IMAGES[@]}"; do
    SANITIZED_NAME=$(echo "$docker_image" | sed 's#[/:]#_#g')
    TAR_PATH="${TARGET_DIRNAME}/${SANITIZED_NAME}.tar"

    echo "[INFO] Pulling image: $docker_image"
    success=false
    for i in $(seq 1 3); do
        if docker pull "$docker_image"; then
            echo "✅ Successfully pulled $docker_image"
            success=true
            break
        fi
        echo "[WARN] Pull failed for $docker_image (attempt $i); retrying in $((i*5))s..."
        sleep $((i*5))
    done

    if [ "$success" = false ]; then
        echo "❌ Failed to pull $docker_image after 3 attempts."
        continue
    fi

    echo "[INFO] Saving $docker_image into $TAR_PATH ..."
    docker save -o "$TAR_PATH" "$docker_image"
    echo "✅ Cached → $TAR_PATH"
done

echo "[INFO] ✅ All Docker images pulled and saved successfully!"