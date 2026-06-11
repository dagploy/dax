# Docker in Docker
This image to run and execute tasks inside VM. Its requires dependencies with `dind` image.

## Pre-requisites
```
gcloud services enable artifactregistry.googleapis.com
```

## Build Interactive

You can run interactive script with :

`bash build_and_submit.sh`

## Build via CLI

Build the `daxrun` docker

```commandline
DOCKER_BUILDKIT=1 docker build --no-cache -t daxrun -f Dockerfile .

docker tag daxrun:latest us-docker.pkg.dev/YOUR_GCP_PROJECT/dagploy/daxrun:latest

docker push us-docker.pkg.dev/YOUR_GCP_PROJECT/dagploy/daxrun:latest
```
