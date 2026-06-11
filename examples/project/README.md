# DAX PROJECT
Project example for VM infra provisioning for launching GPU or CPU based VM, inferencing LLM, cache models and dockers and many else.

## Folder Structure

**config** - contains configuration handled with Hydra config. The `env/dev.yaml` contains the configuration for provisioning. `boot_monitor.yaml` use for capture `error` or add `ignore` into the error monitoring during provisioning.

**dax-cli** - CLI command to connect `tools` with user interface. Custom command can be created inside `dax-cli/dax/commands`. Any new file inside this folder and following the format will be automatically registered in CLI command. Its fully customized to any integration or task.

**gcp_vm_script** - Contains configuration for provisioning in GCP

**tools** - Manage configuration, task and operation for VM provisioning and others infra action. Fully customized and automatically registered using registration. You can create as much as tools as you needed for infra automation or providing tools for AI agents.

```
def register():
    return {
        "handlers": {
            HANDLER_CREATE_DOCKER_VM: launch_vm_cpu,
        }
    }
```

**docker-compose.yaml** - Docker compose for running `Hatchet` services for dashboard and long-task runner. This is default from official hatchet github without any modification.

## Install DAX CLI
Go to folder `dax-cli` and install the packages in your computer with `pip install -e .` or `uv pip install -e .` if you are using uv. 

```
cd dax-cli && pip install -e .
```

This folder contains `README.md` that explain how to use and build your own `cli`.

You can customize, add or remove CLI command that located in `dax-cli/dax/commands`. You can use the example as the base template for vibe-coding to create a new command as you wish. Use `register` function to add the new command into CLI.

Example cpu.py
```
from __future__ import annotations

import click

from dax.cli import add_common_submit_options, resolve_stack_name, submit_capability, load_json_object, load_json_file


def register(run: click.Group) -> None:
    run.add_command(launch_vm_cpu)


@click.command(
    "launch_vm_cpu",
    help="Launch CPU based VM with docker",
    epilog="""\b
Eg:
  dax run launch_vm_cpu --stack-name cpu
  dax run launch_vm_cpu  --stack-name cpu --config '{'machineType': 'n1-standard-8', 'provisioningModel': 'standard'}'
""",
)
@click.option("--stack-name", default="", help="DAX stack name. Auto-generated if empty.")
@click.option("--taskfile", default="", help="Taskfile content/path/string.")
@click.option("--config-json", default=None, help="Inline JSON object for params.config.")
@click.option("--config-file", default=None, help="JSON file for params.config.")
@add_common_submit_options
@click.pass_context
def launch_vm_cpu(
    ctx: click.Context,
    stack_name: str,
    taskfile: str,
    config_json: str | None,
    config_file: str | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    stack_name = resolve_stack_name(stack_name, service="vm_cpu")

    if config_json and config_file:
        raise click.BadParameter("Use only one of --config-json or --config-file.")
    
    config = (
        load_json_object(config_json, option_name="--config-json")
        if config_json
        else load_json_file(config_file, option_name="--config-file")
    )

    submit_capability(
        client=ctx.obj["client"],
        handler="launch_vm_cpu",
        params={
            "stack_name": stack_name,
            "taskfile": taskfile,
            "config": config,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )
```

## DAXRun Container
DAX using Docker-in-docker for execution inside the VM. This is to ensure stability across different VM and OS Distro. By default its using https://hub.docker.com/r/dagploy/daxrun

You can build the docker from scratch located at `docker_build`. Build the `dind` then `daxrun`. You can push the image into artifact registry.


```
gcloud auth configure-docker us-docker.pkg.dev
docker pull dagploy/daxrun
docker tag  dagploy/daxrun:latest us-docker.pkg.dev/YOUR-GCP-PROJECT/YOUR-REPO/daxrun:latest
docker push us-docker.pkg.dev/YOUR-GCP-PROJECT/YOUR-REPO/daxrun:latest
```


## 🔧 Run Local
To run this locally, you need to run Hatchet services and run `start.sh`. Before that, make sure to have correct `.env` and logged `gcloud auth` with right permission. Add the project path, hatchet token`.

.env file example

```
PROJECT_PATH=ABSOLUTE_PATH_TO_THIS_PROJECT
ENVIRONMENT=dev

HATCHET_CLIENT_TOKEN="GET_FROM_DASHBOARD_OR_HATCHET_CLI"
HATCHET_CLIENT_HOST_PORT=localhost:7077
HATCHET_CLIENT_TLS_STRATEGY=none
DAX_HATCHET_WORKER_NAME=dax-worker

PULUMI_CONFIG_PASSPHRASE_FILE=""
```

**Important** 
- Add `service-acccount.json` in the project folder for granting provisioning permission
- Modify `env/dev.yaml` with your project detail, for example


**config/env/dev.yaml**
```
name: dev
project_name: YOUR_GCP_PROJECT
gcp:project: YOUR_GCP_PROJECT
gcp:serviceAccount: YOUR_SERVICE_ACCOUNT_EMAIL_ADDRESS
```


## TOOLS
This contains multiple capabilities that will running into Hatchet as long-run task runner. Each tool is independent, atomic and have its own startup scripts and configuration. For provisioning action, its have three components `main.py`, `executor` and `startup.sh`. 

The taskfile is optional only if you want to run command after the provisioning is completed. Example for taskfile can be found in `vm_inference`.

For main.py, you should use `register` to make this available. Make sure `SERVICE` is match with YAML configuration located at `config/env/dev.yaml`

```
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

    if not merged.get("githubToken"):
        raise ValueError("githubToken is required in config to access project repository")
    
    # load google secret for github project access
    merged["github_project_token"] = read_gcp_secret(
        project_id=merged["project"],
        secret_name=merged["githubToken"]
    )

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
```

Have a look on the `tools` folder and its vibe coding friendly for any customization.

## CONFIG YAML
All the configuration and services can be add or modify in the `dev.yaml`. You can create another file like `staging` or `production` version and modify it on `config.yaml`.

Example of YAML:

```
# config/env/dev.yaml
# @package _
name: dev
project_name: 
gcp:project: 
gcp:serviceAccount: 
gcp:proxy: ""
gcp:zone: us-central1-a
gcp:network: "default"
gcp:networkPublic: "default"
gcp:urlMap: 
gcp:maxWait: 800
gcp:serviceAccountKey: "service-account-key"
gcp:hfToken: "hf-token"
gcp:osLogin: false
gcp:dockerRun: dagploy/daxrun:latest
gcp:errorDestroy: true

services:
  vm_cpu:
    default: ${resources.vms.gcp_vm_cpu}
    overrides: 
      publicMode: true
      provisioningModel: spot
      machineType: e2-standard-4
      openPorts: ["80", "443"]
      bootSize: 100
      alternativeZones:
        - us-central1-a
        - us-central1-b
        - us-central1-c
        - us-east1-a
        - us-east1-b

  vm_gpu:
    default: ${resources.vms.gcp_vm_gpu}
    overrides:
      machineType: g2-standard-8
      publicMode: true
      gpu: 1
      port: 80
      bootSize: 200
      provisioningModel: standard
      openPorts: ["8080", "80", "443"]
      alternativeZones:
        - us-central1-a
        - us-central1-b
        - us-central1-c
        - us-east1-b
        - us-east1-c
        - us-east1-d
        - us-east4-a
        - us-east4-c
        - us-west1-a
        - us-west1-b
        - us-west4-a
        - us-west4-c
```