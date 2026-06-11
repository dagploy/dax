#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="daxrun"
DEFAULT_REGION="us"
DEFAULT_REPO="dagploy"

say() {
  echo "==> $*"
}

err() {
  echo "❌ $*" >&2
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "$1 command not found"
    exit 1
  fi
}

need_cmd docker
need_cmd gcloud

# Get default project from current gcloud shell config
CURRENT_PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"

echo ""
echo "Artifact Registry Docker Push Setup"
echo "Press enter for using default value"
echo ""

read -rp "Project ID [${CURRENT_PROJECT_ID:-required}]: " PROJECT_ID
PROJECT_ID="${PROJECT_ID:-$CURRENT_PROJECT_ID}"

if [ -z "${PROJECT_ID}" ]; then
  err "Project ID is required"
  exit 1
fi

read -rp "Region [${DEFAULT_REGION}]: " REGION
REGION="${REGION:-$DEFAULT_REGION}"

read -rp "Repository name [${DEFAULT_REPO}]: " REPO
REPO="${REPO:-$DEFAULT_REPO}"

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_NAME}:latest"

echo ""
say "Using:"
echo "  PROJECT_ID=${PROJECT_ID}"
echo "  REGION=${REGION}"
echo "  REPO=${REPO}"
echo "  IMAGE_NAME=${IMAGE_NAME}"
echo "  IMAGE_URI=${IMAGE_URI}"
echo ""

# Set project for this command context
say "Setting gcloud project to ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" >/dev/null

# Check local Docker image exists
if ! docker image inspect "${IMAGE_NAME}:latest" >/dev/null 2>&1; then
  err "Local Docker image not found: ${IMAGE_NAME}:latest"
  echo "Build it first, for example:"
  echo "  docker build -t ${IMAGE_NAME}:latest ."
  exit 1
fi

# Enable Artifact Registry API if needed
say "Ensuring Artifact Registry API is enabled"
gcloud services enable artifactregistry.googleapis.com \
  --project="${PROJECT_ID}" \
  >/dev/null

# Check if repository exists
if gcloud artifacts repositories describe "${REPO}" \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  >/dev/null 2>&1; then
  say "Artifact Registry repository already exists: ${REPO}"
else
  say "Repository does not exist. Creating: ${REPO}"

  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="DAX Run Image" \
    --project="${PROJECT_ID}"
fi

# Authenticate Docker with Artifact Registry
say "Configuring Docker auth for ${REGION}-docker.pkg.dev"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# Tag image
say "Tagging Docker image"
docker tag "${IMAGE_NAME}:latest" "${IMAGE_URI}"

# Push image
say "Pushing Docker image"
docker push "${IMAGE_URI}"

echo ""
echo "✅ Image pushed successfully:"
echo "${IMAGE_URI}"