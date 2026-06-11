#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"

if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  echo "❌ No active gcloud project found."
  echo "Run:"
  echo "  gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

DEFAULT_REGION="us-central1"
DEFAULT_VPC_NAME="default"

echo "Project: $PROJECT_ID"
echo

read -r -p "Region [$DEFAULT_REGION]: " REGION
REGION="${REGION:-$DEFAULT_REGION}"

read -r -p "VPC network [$DEFAULT_VPC_NAME]: " VPC_NAME
VPC_NAME="${VPC_NAME:-$DEFAULT_VPC_NAME}"

ROUTER_NAME="default-router-${REGION}"
NAT_NAME="default-nat-${REGION}"

echo
echo "Project: $PROJECT_ID"
echo "Region : $REGION"
echo "VPC    : $VPC_NAME"
echo "Router : $ROUTER_NAME"
echo "NAT    : $NAT_NAME"
echo

# 1) Verify network exists
if ! gcloud compute networks describe "$VPC_NAME" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "❌ VPC network not found: $VPC_NAME"
  exit 1
fi

echo "✅ VPC exists: $VPC_NAME"

# 2) Enable Private Google Access on the regional subnet if it exists
# This helps private-only VMs reach Google APIs / GCR / Artifact Registry.
if gcloud compute networks subnets describe "$VPC_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" >/dev/null 2>&1; then

  echo "Enabling Private Google Access on subnet: $VPC_NAME"

  gcloud compute networks subnets update "$VPC_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --enable-private-ip-google-access
else
  echo "⚠️ Subnet not found with same name as VPC: $VPC_NAME in $REGION"
  echo "Skipping Private Google Access."
fi

# 3) Ensure egress firewall allows outbound web + DNS
FIREWALL_RULE="${VPC_NAME}-allow-egress-web-dns"

if ! gcloud compute firewall-rules describe "$FIREWALL_RULE" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then

  echo "Creating egress firewall rule: $FIREWALL_RULE"

  gcloud compute firewall-rules create "$FIREWALL_RULE" \
    --project="$PROJECT_ID" \
    --network="$VPC_NAME" \
    --direction=EGRESS \
    --action=ALLOW \
    --rules=tcp:80,tcp:443,udp:53,tcp:53 \
    --destination-ranges=0.0.0.0/0 \
    --priority=1000
else
  echo "✅ Firewall rule already exists: $FIREWALL_RULE"
fi

# 4) Ensure Cloud Router exists
if ! gcloud compute routers describe "$ROUTER_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then

  echo "Creating Cloud Router: $ROUTER_NAME"

  gcloud compute routers create "$ROUTER_NAME" \
    --project="$PROJECT_ID" \
    --network="$VPC_NAME" \
    --region="$REGION"
else
  echo "✅ Cloud Router already exists: $ROUTER_NAME"
fi

# 5) Ensure Cloud NAT exists
if ! gcloud compute routers nats describe "$NAT_NAME" \
  --router="$ROUTER_NAME" \
  --router-region="$REGION" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then

  echo "Creating Cloud NAT: $NAT_NAME"

  gcloud compute routers nats create "$NAT_NAME" \
    --project="$PROJECT_ID" \
    --router="$ROUTER_NAME" \
    --router-region="$REGION" \
    --nat-all-subnet-ip-ranges \
    --auto-allocate-nat-external-ips \
    --enable-logging
else
  echo "✅ Cloud NAT already exists: $NAT_NAME"
fi

echo
echo "✅ Done."
echo
echo "Check NAT:"
gcloud compute routers nats describe "$NAT_NAME" \
  --router="$ROUTER_NAME" \
  --router-region="$REGION" \
  --project="$PROJECT_ID"