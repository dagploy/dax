#!/bin/sh

set -e

# Run docker daemon
/usr/local/bin/dockerd-entrypoint.sh --tls=false > /tmp/docker.log 2>&1 &

echo "Waiting for docker daemon to start..." >&2

i=0
while true;
do
    test -S /var/run/docker.sock && echo "ok!" >&2 && break
    echo ... >&2
    sleep .5
    i=$((i+1))

    if [ $i -gt 60 ];
    then
    echo === Unable to start docker daemon === >&2
    cat /tmp/docker.log >&2
    echo "====================================" >&2
    echo "Unable to start docker daemon. Make sure your service has the privileged flag set." >&2
    exit 1
    fi
done

# Configure permission for usage : docker run dax python main.py
if curl -s -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/id > /dev/null; then
    echo "✔ Running on Google Compute Engine"

    METADATA="http://metadata.google.internal/computeMetadata/v1"
    HEADER="Metadata-Flavor: Google"

    SERVICE_ACCOUNT=$(curl -s -H "${HEADER}" \
        "${METADATA}/instance/service-accounts/" \
        | head -n 1 | sed 's:/$::')

    echo "✔ Service Account: ${SERVICE_ACCOUNT}"

    ACCESS_TOKEN=$(
      curl -s -H "${HEADER}" \
        "${METADATA}/instance/service-accounts/default/token" \
        | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
    )

    export CLOUDSDK_AUTH_ACCESS_TOKEN="$ACCESS_TOKEN"
else
    echo "⚠ Not running on Google Cloud. Skipping metadata auth."
fi

# --------------------------------------------------------------------
# CONFIGURE DOCKER CREDENTIAL HELPER
# --------------------------------------------------------------------
mkdir -p /root/.docker

cat >/root/.docker/config.json <<EOF
{
  "credHelpers": {
    "gcr.io": "gcloud",
    "us-docker.pkg.dev": "gcloud",
    "asia-docker.pkg.dev": "gcloud",
    "eu-docker.pkg.dev": "gcloud",
    "us-central1-docker.pkg.dev": "gcloud",
    "us-east1-docker.pkg.dev": "gcloud",
    "asia-southeast1-docker.pkg.dev": "gcloud"
  }
}
EOF

exec "$@"
