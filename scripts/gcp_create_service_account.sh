#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
DEFAULT_NAME="executor"
DEFAULT_DISPLAY_NAME="DAX Service Account"

prompt() {
  local message="$1"
  local default="${2:-}"
  local value=""

  if [ -t 0 ]; then
    # Normal execution: bash script.sh
    read -r -p "${message}" value
  elif [ -r /dev/tty ]; then
    # curl ... | bash
    read -r -p "${message}" value < /dev/tty
  else
    die "Interactive input requires a terminal. Run this script from a shell."
  fi

  printf '%s' "${value:-$default}"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

retry() {
  local attempts="$1"
  local sleep_seconds="$2"
  shift 2

  local n=1
  while true; do
    if "$@"; then
      return 0
    fi

    if [ "$n" -ge "$attempts" ]; then
      return 1
    fi

    echo "Retry $n/$attempts failed. Sleeping ${sleep_seconds}s..."
    sleep "$sleep_seconds"
    n=$((n + 1))
  done
}

require_project() {
  if [ -z "${PROJECT}" ] || [ "${PROJECT}" = "(unset)" ]; then
    echo "No active gcloud project found."
    PROJECT="$(prompt "Enter GCP project ID: " "")"
  fi

  [ -n "${PROJECT}" ] || die "Project ID is required."

  gcloud projects describe "${PROJECT}" >/dev/null 2>&1 || \
    die "Project does not exist or you do not have access: ${PROJECT}"
}

wait_for_service_account() {
  local sa_email="$1"
  local project="$2"

  echo "Waiting for service account to become visible..."
  local i
  for i in $(seq 1 18); do
    if gcloud iam service-accounts describe "${sa_email}" --project="${project}" >/dev/null 2>&1; then
      echo "Service account is now visible: ${sa_email}"
      return 0
    fi
    sleep 5
  done

  die "Service account still not visible after waiting: ${sa_email}"
}

grant_project_role() {
  local project="$1"
  local sa_email="$2"
  local role="$3"

  echo "→ Granting ${role}..."

  # Apply binding silently
  if retry 5 5 \
    gcloud projects add-iam-policy-binding "${project}" \
      --member="serviceAccount:${sa_email}" \
      --role="${role}" \
      --condition=None \
      --quiet >/dev/null 2>&1
  then
    # Verify binding exists
    if gcloud projects get-iam-policy "${project}" \
      --flatten="bindings[].members" \
      --filter="bindings.role=${role} AND bindings.members=serviceAccount:${sa_email}" \
      --format="value(bindings.role)" | grep -q "${role}"
    then
      echo "✔ Granted ${role} to ${sa_email}"
    else
      echo "❌ Failed to verify ${role} for ${sa_email}"
      return 1
    fi
  else
    echo "❌ Failed to grant ${role} to ${sa_email}"
    return 1
  fi
}

echo "== Create GCP Service Account =="

require_project

INPUT_PROJECT="$(prompt "Project ID [${PROJECT}]: " "${PROJECT}")"
PROJECT="${INPUT_PROJECT}"
[ -n "${PROJECT}" ] || die "Project ID is required."

SA_NAME="$(prompt "Service account name [${DEFAULT_NAME}]: " "${DEFAULT_NAME}")"

DISPLAY_NAME="$(prompt "Display name [${DEFAULT_DISPLAY_NAME}]: " "${DEFAULT_DISPLAY_NAME}")"

if ! [[ "${SA_NAME}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  die "Service account name must be 6-30 chars, start with a lowercase letter, end with a lowercase letter or digit, and contain only lowercase letters, digits, and hyphens."
fi

SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

echo
echo "Project           : ${PROJECT}"
echo "Service account   : ${SA_EMAIL}"
echo "Display name      : ${DISPLAY_NAME}"
echo

CONFIRM="$(prompt "Proceed? [y/N]: " "N")"
case "${CONFIRM}" in
  y|Y|yes|YES) ;;
  *) echo "Cancelled."; exit 0 ;;
esac

echo
echo "== Creating service account =="
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "Service account already exists: ${SA_EMAIL}"
else
  gcloud iam service-accounts create "${SA_NAME}" \
    --project="${PROJECT}" \
    --display-name="${DISPLAY_NAME}"
  echo "Created: ${SA_EMAIL}"
fi

wait_for_service_account "${SA_EMAIL}" "${PROJECT}"

echo
echo "== Granting project-level roles =="
for ROLE in \
  "roles/compute.instanceAdmin.v1" \
  "roles/compute.securityAdmin" \
  "roles/artifactregistry.writer" \
  "roles/iam.serviceAccountUser" \
  "roles/compute.loadBalancerAdmin" \
  "roles/dns.admin" \
  "roles/storage.objectUser" \
  "roles/secretmanager.secretAccessor"
do
  grant_project_role "${PROJECT}" "${SA_EMAIL}" "${ROLE}"
done

echo "→ Granting serviceAccountUser on itself..."

if retry 5 5 \
  gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --project="${PROJECT}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/iam.serviceAccountUser" \
    --condition=None \
    --quiet >/dev/null 2>&1
then
  if gcloud iam service-accounts get-iam-policy "${SA_EMAIL}" \
    --project="${PROJECT}" \
    --flatten="bindings[].members" \
    --filter="bindings.role=roles/iam.serviceAccountUser AND bindings.members=serviceAccount:${SA_EMAIL}" \
    --format="value(bindings.role)" | grep -q "roles/iam.serviceAccountUser"
  then
    echo "✔ Granted roles/iam.serviceAccountUser to ${SA_EMAIL}"
  else
    echo "❌ Failed to verify roles/iam.serviceAccountUser"
    exit 1
  fi
else
  echo "❌ Failed to grant roles/iam.serviceAccountUser"
  exit 1
fi

echo "== Creating service account key =="

DEFAULT_KEY_PATH="${HOME}/${SA_NAME}-key.json"

KEY_PATH="$(prompt "Key output path [${DEFAULT_KEY_PATH}]: " "${DEFAULT_KEY_PATH}")"

[ -n "${KEY_PATH}" ] || die "Key output path is required."

# Expand leading ~ manually, because quotes prevent shell expansion from user input
case "${KEY_PATH}" in
  "~")
    KEY_PATH="${HOME}"
    ;;
  "~/"*)
    KEY_PATH="${HOME}/${KEY_PATH#~/}"
    ;;
esac

if [ -e "${KEY_PATH}" ]; then
  OVERWRITE="$(prompt "File exists at ${KEY_PATH}. Overwrite? [y/N]: " "N")"
  case "${OVERWRITE}" in
    y|Y|yes|YES) ;;
    *) echo "Cancelled key creation."; exit 0 ;;
  esac
fi

mkdir -p "$(dirname "${KEY_PATH}")"

# Create service account key locally first
gcloud iam service-accounts keys create "${KEY_PATH}" \
  --iam-account="${SA_EMAIL}" \
  --project="${PROJECT}"

chmod 600 "${KEY_PATH}"

SECRET_NAME="dax-service-account-key"

gcloud services enable secretmanager.googleapis.com \
  --project="${PROJECT}"

# Create the secret if it does not exist
if ! gcloud secrets describe "${SECRET_NAME}" \
  --project="${PROJECT}" >/dev/null 2>&1; then

  gcloud secrets create "${SECRET_NAME}" \
    --project="${PROJECT}" \
    --replication-policy="automatic"
fi

# Add/update the latest secret version from the key file
gcloud secrets versions add "${SECRET_NAME}" \
  --project="${PROJECT}" \
  --data-file="${KEY_PATH}"

echo "➡️  Service account key stored in Secret Manager: ${SECRET_NAME}"

echo
echo "Done."
echo "Service account: ${SA_EMAIL}"
echo "Key file: ${KEY_PATH}"
echo
echo "Export it with:"
echo "export GOOGLE_APPLICATION_CREDENTIALS=\"${KEY_PATH}\""

