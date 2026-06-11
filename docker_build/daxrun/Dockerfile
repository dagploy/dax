FROM dind-base:latest
ENV DEBIAN_FRONTEND=noninteractive

# Minimal runtime deps for gcloud/gsutil
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-crcmod libmagic1 git dnsutils ca-certificates curl yq iputils-ping net-tools nmap vim jq tmux nvtop btop bpytop zip unzip \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# (optional) Taskfile install
RUN sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

WORKDIR /app/
RUN python3 -m pip install --no-cache-dir --break-system-packages uv

# Install Google Cloud SDK manually into /usr/lib/google-cloud-sdk
RUN mkdir -p /usr/lib/google-cloud-sdk \
 && curl -sSL https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz \
    | tar -xz -C /usr/lib \
 && ln -s /usr/lib/google-cloud-sdk/bin/gcloud /usr/local/bin/gcloud \
 && ln -s /usr/lib/google-cloud-sdk/bin/gsutil /usr/local/bin/gsutil \
 && gcloud --version

# Set path
ENV CLOUDSDK_ROOT_DIR="/usr/lib/google-cloud-sdk" \
    CLOUDSDK_PYTHON="python3" \
    PATH="/usr/lib/google-cloud-sdk/bin:/usr/local/nvidia/bin:${PATH}"

COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]