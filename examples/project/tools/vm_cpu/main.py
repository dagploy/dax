from __future__ import annotations
import tomllib

from pathlib import Path
from typing import Any, Callable

from dagploy_dax.service_lib.build import ConfigSetup
from dagploy_dax.service_lib.launch_cpu import LaunchCPU
from dagploy_dax.service_lib.utils import execute, emit_state, load_default_config
from dagploy_dax.provisioning_lib.vm_private_utils import read_gcp_secret
from dagploy_dax.service_lib.tool_utils import PULUMI_YAML_DIR

from .cpu_executor import Deployment as Executor

# service name should be the same as the one defined in config/env/dev.yaml
SERVICE = "vm_cpu"

HANDLER_CREATE_DOCKER_VM = "launch_vm_cpu"


def launch_vm_cpu(
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
    VM CPU capability.
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

    try:
        execute(
            cfg=cfg,
            merged=merged,
            deploy_fn=lambda: Executor().run(),
            provisioning_function_dir=PULUMI_YAML_DIR,
            launch_config=LaunchCPU,
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
            HANDLER_CREATE_DOCKER_VM: launch_vm_cpu,
        }
    }
