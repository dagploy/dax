# Build Docker in Docker Image for NVIDIA GPU

Enable running docker inside a docker. Run this command in this `dind` folder to build this image.

```commandline
docker build -t dind-base -f Dockerfile .
```