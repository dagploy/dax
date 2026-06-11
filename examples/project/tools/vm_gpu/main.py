from __future__ import annotations
import tomllib

from pathlib import Path
from typing import Any, Callable

from dagploy_dax.service_lib.build import ConfigSetup
from dagploy_dax.service_lib.launch_gpu import LaunchGPU
from dagploy_dax.service_lib.utils import execute, emit_state, load_pulumi_path, load_default_config
from dagploy_dax.provisioning_lib.vm_private_utils import read_gcp_secret
from dagploy_dax.service_lib.tool_utils import PULUMI_YAML_DIR

from .gpu_executor import Deployment as Executor

# service name should be the same as the one defined in config/env/dev.yaml
SERVICE = "vm_gpu" 

HANDLER_CREATE_DOCKER_VM = "launch_vm_gpu"


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


def launch_vm_gpu(
    stack_name: str = "",
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
    cfg, resources = load_default_config()
    service = SERVICE

    # send to hatchet that we are starting the vm creation process
    emit_state(update_state, "running", HANDLER_CREATE_DOCKER_VM, job_id=job_id, service=service, stack_name=stack_name)
    on_output(f"{HANDLER_CREATE_DOCKER_VM} started job_id={job_id}")
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

    if not taskfile:
        taskfile = "taskfile.yaml"
  
    # ==========================================
    # Custom user logic
    # ===========================================
    extra_configs.update({
        "stackName": stack_name,
        "service": service,
        "tools_dirpath": Path(__file__).parent,
        "taskfile": taskfile,
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
            HANDLER_CREATE_DOCKER_VM: launch_vm_gpu,
        }
    }