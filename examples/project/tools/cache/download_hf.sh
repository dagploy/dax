# -----------------------------------------------------------------------------
# Install HF CLI and dependencies
# -----------------------------------------------------------------------------
apt-get update -y -qq
apt-get install -y -qq python3-pip git
pip install -U "huggingface_hub" hf_transfer


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


load_provisioning_secrets() {
  echo "==> Loading provisioning secrets"

  # Required secrets: fail startup if missing or empty
  load_secret_required "HF_TOKEN" "$HF_TOKEN"

  echo "✅ Provisioning secrets loaded"
}

load_provisioning_secrets

# -----------------------------------------------------------------------------
# Hugging Face authentication
# -----------------------------------------------------------------------------
echo "==> Authenticating to Hugging Face..."

# Set HOME path as its required for git config --global credential.helper store
if [ "$(id -u)" -eq 0 ]; then
  export HOME="${HOME:-/root}"
fi

git config --global credential.helper store

hf auth login --token "${HF_TOKEN}" --add-to-git-credential

# -----------------------------------------------------------------------------
# Setup HF environment variables
# -----------------------------------------------------------------------------
export HF_HUB_ENABLE_EMERGENCY_RETRY=True
export HF_HUB_DISABLE_PROGRESS_BARS=0
export HF_HUB_DOWNLOAD_TIMEOUT=300
export HF_HUB_DISABLE_XET=1
export HF_HUB_ENABLE_HF_TRANSFER=1

TARGET_DIRNAME="${MNT_PATHS[0]}/hf"
mkdir -p "$TARGET_DIRNAME"

echo "==> Starting model downloads to $TARGET_DIRNAME ..."

# Check if REPO_URLS exists AND has at least one non-empty value
if [ -n "${REPO_URLS+x}" ] && [ "${#REPO_URLS[@]}" -gt 0 ]; then
  echo "MODELS DOWNLOAD: ${REPO_URLS[@]}"

  for repo_url in "${REPO_URLS[@]}"; do
    repo_url="$(echo "$repo_url" | xargs)"   # trim spaces

    if [ -z "$repo_url" ]; then
      echo "[WARN] Empty repo URL, skipping."
      continue
    fi

    echo "==> Downloading from $repo_url ..."

    safe_repo_name=$(echo "$repo_url" | sed 's|/|--|g' | tr '[:upper:]' '[:lower:]')
    local_dir="${TARGET_DIRNAME}/${safe_repo_name}"

    mkdir -p "$local_dir"

    hf download \
      "$repo_url" \
      --repo-type "${MODEL_REPO_TYPE}" \
      --revision "${BRANCH}" \
      --local-dir "$local_dir"

    echo "✅ Downloaded $repo_url → $local_dir"

  done

  echo "==> All repositories downloaded successfully."

else
  echo "[INFO] Using single MODEL_REPO fallback..."

  hf download \
    "${MODEL_REPO}" \
    --repo-type "${MODEL_REPO_TYPE}" \
    --revision "${BRANCH}" \
    --local-dir "${TARGET_DIRNAME}/${MODEL_IMAGE}"

  echo "[INFO] ✅ Model ${MODEL_REPO} pulled and saved successfully!"
fi

echo "==> Task completed."
