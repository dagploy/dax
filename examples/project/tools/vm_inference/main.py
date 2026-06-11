from __future__ import annotations
import tomllib

from pathlib import Path
from typing import Any, Callable

from dagploy_dax.service_lib.build import ConfigSetup
from dagploy_dax.service_lib.launch_gpu import LaunchGPU
from dagploy_dax.service_lib.utils import execute, measure_duration, load_default_config, load_app_env, emit_state, load_pulumi_path
from .inference_executor import Deployment as Executor

from dagploy_dax.provisioning_lib.vm_private_utils import read_gcp_secret

from dagploy_dax.utils.cli import image_exists
from dagploy_dax.utils.gcp import get_hf_or_gcp_disk_image
from dagploy_dax.service_lib.tool_utils import PULUMI_YAML_DIR

from hydra import initialize_config_dir, compose
from omegaconf import DictConfig, OmegaConf

# service name should be the same as the one defined in config/env/dev.yaml
SERVICE = "vm_gpu_inference" 
HANDLER_CREATE_VM_INFERENCE = "create_vm_inference"


def load_gpu_count_for_machine(machine_type: str, on_output=print) -> int:
    gpu_list_path = Path(__file__).parent / "gpu_list.toml"

    if not gpu_list_path.exists():
        raise FileNotFoundError(f"GPU list TOML not found: {gpu_list_path}")

    with gpu_list_path.open("rb") as f:
        gpu_list = tomllib.load(f)

    gpu_count = gpu_list.get(machine_type, 0)

    if gpu_count:
        on_output(f"Detected GPU machineType={machine_type}, gpu={gpu_count}")
    else:
        on_output(f"No GPU mapping found for machineType={machine_type}")

    return gpu_count


def apply_domain_stack_suffix(merged: dict[str, Any]) -> dict[str, Any]:
    """
    If domain exists, append the first domain segment to stackName
    """
    domain_value = merged.get("domain")

    if isinstance(domain_value, str) and domain_value.strip():
        domain = domain_value.split(".")[0]
        merged["stackName"] = f'{merged["stackName"]}-{domain}'
        merged["vmName"] = merged["stackName"]

    return merged


def create_vm_inference(
    stack_name: str = "",
    model: str = "",
    profile: str = "",
    taskfile: str = "",
    config: str | None = None,
    on_output: Callable[[str], None] = print,
    job_id: str | None = None,
    update_state=None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    VM docker GPU capability.
    """
    # Load default config and resources
    cfg, resources = load_default_config()
    service = SERVICE

    # send to hatchet that we are starting the vm creation process
    emit_state(update_state, "running", HANDLER_CREATE_VM_INFERENCE, job_id=job_id, service=service, stack_name=stack_name)
    on_output(f"{HANDLER_CREATE_VM_INFERENCE} started job_id={job_id}")
    on_output(f"service={service}")
    on_output(f"stack_name={stack_name}")
    
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
    # Custom user logic
    # ===========================================
    
    if model:
        extra_configs['model'] = model

    # identify if the model is google disk image or huggingface model 
    image_name, is_local_image = get_hf_or_gcp_disk_image(extra_configs['model'])

    # convert huggingface model name to gcp disk image name automatically
    # use the hf model naming convention in gcp disk
    if not is_local_image:
        image_name = f'models--{image_name}'

    extra_configs['modelImage'] = image_name

    # identify if the model is google disk image or huggingface model 
    image_name, is_local_image = get_hf_or_gcp_disk_image(extra_configs['model'])

    # convert huggingface model name to gcp disk image name automatically
    # use the hf model naming convention in gcp disk
    if not is_local_image:
        image_name = f'models--{image_name}'

    extra_configs['modelImage'] = image_name

    # check if image already created in the project
    is_image_exists = image_exists(image_name, default_config['project'])

    on_output(f"Checking image: {image_name}, exists: {is_image_exists}, is_local_image: {is_local_image}")

    # if the disk is not exists, raise error
    if not is_image_exists:
        raise ValueError(f"❌ Image {image_name} not found. Cache the model with: dax cache hf {model} --size 50 (you can adjust the size)")

    # setup the model repo name (google disk image name)
    extra_configs['modelRepo'] = ""

    # Custom user logic
    extra_configs["model"] = model
    extra_configs["modelRepo"] = f"models--{model}"
    extra_configs["modelRepoExist"] = True
    on_output(f"Model ready: {extra_configs['modelRepo']}")

    extra_configs.update({
        "stackName": stack_name,
        "service": service,
        "tools_dirpath": Path(__file__).parent,
        "taskfile": taskfile if taskfile else "taskfile.yaml",
    })

    # merging all configs together, with priority: extra_configs > input_config > default_config
    merged = {**default_config, **input_config, **extra_configs}
    merged = apply_domain_stack_suffix(merged)

    # Read gpu count from gpu_list.toml based on machineType
    machine_type = merged.get("machineType", "")
    merged["gpu"] = load_gpu_count_for_machine(machine_type, on_output=on_output)

    # if GPU is enabled, then use different images and executor
    if not merged.get("gpu", 0):
        on_output("GPU is not enabled.")
        raise ValueError("GPU not enabled, cannot proceed with create_docker_vm. Please enable GPU in config.")

    try:
        execute(
            cfg=cfg,
            merged=merged,
            deploy_fn=lambda: Executor().run(),
            provisioning_function_dir=PULUMI_YAML_DIR,
            launch_config=LaunchGPU,
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
        "job_id": job_id,
        "kwargs": kwargs or {},
    }


def register():
    return {
        "handlers": {
            HANDLER_CREATE_VM_INFERENCE: create_vm_inference,
        }
    }


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m tools.vm_inference.main",
    )

    parser.add_argument("--service", default=SERVICE)
    parser.add_argument("--stack-name", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--config", default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--cfg", default="{}")
    parser.add_argument("--resources", default="{}")

    args = parser.parse_args()

    result = create_vm_inference(
        service=args.service,
        model=args.model,
        stack_name=args.stack_name,
        profile=args.profile,
        config=args.config,
        cfg=json.loads(args.cfg or "{}"),
        resources=json.loads(args.resources or "{}"),
        job_id=args.job_id,
    )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()