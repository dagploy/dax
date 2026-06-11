from __future__ import annotations

import re 
from pathlib import Path
from typing import Any, Callable

from dagploy_dax.service_lib.build import ConfigSetup
from dagploy_dax.service_lib.launch_cpu_termination import LaunchConfigCPUTermination
from dagploy_dax.service_lib.utils import (
    execute,
    measure_duration,
    generate_stack_name,
    emit_state,
)

from dagploy_dax.service_lib.tool_utils import PULUMI_YAML_DIR
from dagploy_dax.utils.gcp import get_hf_or_gcp_disk_image, normalize_image_name

from .hf_image_executor import Deployment as HFImageExecutor
from .hf_image_executor import Deployment as HFImageMultiExecutor

from .utils import load_default_config


HANDLER_DOWNLOAD_HF = "download_hf"
HANDLER_DOWNLOAD_HF_MULTI = "download_hf_multi"


def normalize_list(value):
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def validate_hf_repo(repo: str | None) -> None:
    if not repo or not re.match(r"^[^/\s]+/[^/\s]+$", repo):
        raise ValueError(
            f"Repo is not the right format. eg: meta-llama/Meta-Llama-3-8B. Given {repo} instead."
        )


def validate_hf_repo_type(repo_type: str | None) -> None:
    if repo_type and repo_type not in ["model", "dataset"]:
        raise ValueError(
            f"Repo type only 'model' and 'dataset'. Given input: {repo_type} instead."
        )


def run_hf_download(
    *,
    service: str,
    handler: str,
    is_multi: bool = False,
    executor_class,
    repo: str | None = None,
    repo_type: str | None = None,
    repo_urls: list[str] | str | None = None,
    images: list[str] | str | None = None,
    image_size: str | None = None,
    branch: str | None = None,
    stack_name: str = "",
    profile: str = "",
    config: str | None = None,
    on_output: Callable[[str], None] = print,
    job_id: str | None = None,
    update_state=None,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg, resources = load_default_config()

    images = normalize_list(images)
    repo_urls = normalize_list(repo_urls)
    branch = branch or "main"
    repo_type = repo_type or "model"

    if not stack_name:
        stack_name = generate_stack_name(service=service)

    # send to hatchet that we are starting the process
    emit_state(update_state, "running", handler, job_id=job_id, service=service, stack_name=stack_name)
    on_output(f"{service} started job_id={job_id}")
    on_output(f"stack_name={stack_name}")

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

    extra_configs = {
        "tools_dirpath": str(Path(__file__).parent),
        "service": service,
        "modelRepoType": repo_type,
        "branch": branch,
    }

    if is_multi:
        extra_configs["repoUrls"] = repo_urls
        extra_configs["modelRepoType"] = repo_type
        extra_configs["branch"] = "main"

        if not images and not input_config.get("images"):
            raise ValueError("Image name must be provided")

    else:
        validate_hf_repo(repo)
        validate_hf_repo_type(repo_type)

        extra_configs["modelRepo"] = repo

        image_name, is_local_image = get_hf_or_gcp_disk_image(repo, branch)

        if not images and not input_config.get("images"):
            image_name = f"{repo_type}s--{image_name}"
            extra_configs["modelImage"] = image_name
            extra_configs["images"] = [image_name]

        elif images and not is_local_image:
            image_name = f"{repo_type}s--{image_name}"
            extra_configs["modelImage"] = image_name
            extra_configs["images"] = [image_name]

        elif images and is_local_image:
            raise ValueError(f"{repo} is invalid HF model/dataset format.")

    if images:
        images = [normalize_image_name(img) for img in images]
        extra_configs["images"] = images

    if image_size:
        extra_configs["imageSize"] = image_size

    merged = {**default_config, **input_config, **extra_configs}

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
def download_hf(
    repo: str | None = None,
    repo_type: str | None = None,
    images: list[str] | str | None = None,
    image_size: str | None = None,
    branch: str | None = None,
    stack_name: str = "",
    profile: str = "",
    config: str | None = None,
    on_output: Callable[[str], None] = print,
    job_id: str | None = None,
    update_state=None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Hugging Face model/dataset download capability.
    """
    return run_hf_download(
        service="download_hf",
        handler=HANDLER_DOWNLOAD_HF,
        is_multi=False,
        executor_class=HFImageExecutor,
        repo=repo or kwargs.get("repo"),
        repo_type=repo_type or kwargs.get("repo_type"),
        images=images or kwargs.get("images"),
        image_size=image_size or kwargs.get("image_size"),
        branch=branch or kwargs.get("branch"),
        stack_name=stack_name,
        profile=profile,
        config=config,
        on_output=on_output,
        job_id=job_id,
        update_state=update_state,
        kwargs=kwargs,
    )


@measure_duration
def download_hf_multi(
    repo_urls: list[str] | str | None = None,
    images: list[str] | str | None = None,
    image_size: str | None = None,
    stack_name: str = "",
    profile: str = "",
    config: str | None = None,
    on_output: Callable[[str], None] = print,
    job_id: str | None = None,
    update_state=None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Multi Hugging Face model/dataset download capability.
    """
    return run_hf_download(
        service="download_hf_multi",
        handler=HANDLER_DOWNLOAD_HF_MULTI,
        is_multi=True,
        executor_class=HFImageMultiExecutor,
        repo_urls=repo_urls or kwargs.get("repo_urls"),
        images=images or kwargs.get("images"),
        image_size=image_size or kwargs.get("image_size"),
        stack_name=stack_name,
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
            HANDLER_DOWNLOAD_HF: download_hf,
            HANDLER_DOWNLOAD_HF_MULTI: download_hf_multi,
        }
    }