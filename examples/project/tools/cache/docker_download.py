from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, List

from dagploy_dax.service_lib.build import ConfigSetup
from dagploy_dax.service_lib.launch_cpu_termination import LaunchConfigCPUTermination
from dagploy_dax.service_lib.utils import (
    execute,
    measure_duration,
    generate_stack_name,
    emit_state,
    validate_docker_urls,
)

from dagploy_dax.service_lib.tool_utils import PULUMI_YAML_DIR
from dagploy_dax.utils.gcp import normalize_docker_image_name, normalize_image_name

from .docker_image_executor import Deployment as DockerImageExecutor
from .docker_tar_executor import Deployment as DockerTarExecutor

from .utils import load_default_config

HANDLER_DOWNLOAD_IMAGE_DOCKER = "download_image_docker"
HANDLER_DOWNLOAD_TAR_DOCKER = "download_tar_docker"


def normalize_list(value):
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def run_docker_download(
    *,
    service: str,
    handler: str,
    image_suffix: str,
    executor_class,
    docker_images: list[str] | str | None = None,
    images: list[str] | str | None = None,
    image_size: str | None = None,
    stack_name: str = "",
    zone: str = "",
    profile: str = "",
    config: str | None = None,
    on_output: Callable[[str], None] = print,
    job_id: str | None = None,
    update_state=None,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg, resources = load_default_config()

    # send to hatchet that we are starting the process
    emit_state(update_state, "running", handler, job_id=job_id, service=service, stack_name=stack_name)
    on_output(f"{service} started job_id={job_id}")
    on_output(f"stack_name={stack_name}")

    # ==========================================
    # custom pre-processing user logic implementation starts here
    # ===========================================
    docker_images = normalize_list(docker_images)
    images = normalize_list(images)

    validate_docker_urls(docker_images)

    if not stack_name:
        stack_name = generate_stack_name(service=service)

    if config:
        on_output(f"Using config: {config}")

    # Load and merge configs
    default_config, input_config, extra_configs = ConfigSetup(
        cfg=cfg,
        stack_name=stack_name,
        service=service,
        profile=profile,
        resources=resources or {},
        config=config,
        provisioning_function_dir=PULUMI_YAML_DIR,
        on_output=on_output,
    )()

    # ==========================================
    # Custom user logic implementation starts here
    # ===========================================
    extra_configs = {
        "tools_dirpath": str(Path(__file__).parent),
        "zone": zone or cfg.get("zone", "us-central1-a"),
        "service": service,
    }

    if docker_images:
        extra_configs["dockerImages"] = docker_images

    if images:
        images = [normalize_image_name(img) for img in images]
        extra_configs["images"] = images
    elif not input_config.get("images"):
        if not docker_images:
            raise ValueError("docker_images is required when images is not provided")

        try:
            images = [normalize_docker_image_name(docker_images[0], image_suffix)]
        except Exception as e:
            raise ValueError(f"Problem when retrieving docker images. {e}") from e

        extra_configs["images"] = images

    if image_size:
        extra_configs["imageSize"] = image_size

    final_images = extra_configs.get("images") or input_config.get("images") or []

    if len(final_images) > 1:
        raise ValueError("Only one image disk allowed to download docker images")

    # merging all configs together, with priority: extra_configs > input_config > default_config
    merged = {**default_config, **input_config, **extra_configs}

    # execute the provisioning with the merged config and launch config. 
    try:
        execute(
            cfg=cfg,
            merged=merged,
            deploy_fn=lambda: executor_class().run(),
            provisioning_function_dir=PULUMI_YAML_DIR,
            launch_config=LaunchConfigCPUTermination,
            on_output=on_output,
            job_id=job_id,
            update_state=update_state,
        )
    except Exception as e:
        on_output(f"Error during execution: {e}")
        raise

    on_output(f"finished job_id={job_id}")

    return {
        "message": f"Stack {stack_name} ran successfully",
        "service": service,
        "stack_name": stack_name,
        "profile": profile,
        "job_id": job_id,
        "kwargs": kwargs or {},
    }


@measure_duration
def download_image_docker(
    docker_images: List[str] = None,
    images: List[str] = None,
    image_size: str = None,
    stack_name: str = "",
    zone: str = "",
    profile: str = "",
    config: str | None = None,
    on_output: Callable[[str], None] = print,
    job_id: str | None = None,
    update_state=None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Download docker and move /var/lib/docker into disk image,
    which later can be mounted with /var/lib/docker.
    """
    return run_docker_download(
        service="download_docker_image",
        handler=HANDLER_DOWNLOAD_IMAGE_DOCKER,
        image_suffix="docker-lib",
        executor_class=DockerImageExecutor,
        docker_images=docker_images,
        images=images,
        image_size=image_size,
        stack_name=stack_name,
        zone=zone,
        profile=profile,
        config=config,
        on_output=on_output,
        job_id=job_id,
        update_state=update_state,
        kwargs=kwargs,
    )


@measure_duration
def download_tar_docker(
    docker_images: list[str] | str | None = None,
    images: list[str] | str | None = None,
    image_size: str = None,
    stack_name: str = "",
    zone: str = "",
    profile: str = "",
    config: str | None = None,
    on_output: Callable[[str], None] = print,
    job_id: str | None = None,
    update_state=None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Download docker image and convert it into tar files.
    """
    return run_docker_download(
        service="download_docker",
        handler=HANDLER_DOWNLOAD_TAR_DOCKER,
        image_suffix="docker-tar",
        executor_class=DockerTarExecutor,
        docker_images=docker_images,
        images=images,
        image_size=image_size,
        stack_name=stack_name,
        zone=zone,
        profile=profile,
        config=config,
        on_output=on_output,
        job_id=job_id,
        update_state=update_state,
        kwargs=kwargs,
    )


def register():
    return {
        "handlers": {
            HANDLER_DOWNLOAD_IMAGE_DOCKER: download_image_docker,
            HANDLER_DOWNLOAD_TAR_DOCKER: download_tar_docker,
        }
    }
