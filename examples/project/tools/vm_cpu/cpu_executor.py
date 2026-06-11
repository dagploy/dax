import textwrap
from pathlib import Path
from string import Template

from dagploy_dax.provisioning_lib.executor_vm import Executor
from dagploy_dax.service_lib.utils import read_required_file, prepare_proxy


class Deployment(Executor):

    def startup_builder(self):
        cfg = self.cfg.copy()

        # add proxy
        self.proxy = prepare_proxy(cfg)

        # --- Load startup template ---
        startup_tmpl = read_required_file(Path(cfg["tools_dirpath"]) / "startup.sh")

        # --- Load taskfile template ---
        taskfile_tmpl = read_required_file(Path(cfg["tools_dirpath"]) / "taskfile.yaml")

        default_task = {
            "stack_name": cfg["stack_name"],
        }

        default_task["extra_proxy"] = ""  # default empty, will be set if proxy is configured

        if self.proxy:
            # add proxy in config in Taskfile docker run command
            default_task["extra_proxy"] = (
                '-e HTTP_PROXY=${HTTP_PROXY} '
                '-e HTTPS_PROXY=${HTTPS_PROXY} '
                '-e http_proxy=${HTTP_PROXY} '
                '-e https_proxy=${HTTPS_PROXY} '
                '-e NO_PROXY=${NO_PROXY} '
                '-e no_proxy=${NO_PROXY} '
            )

        rendered_taskfile = Template(taskfile_tmpl).substitute(**default_task)
        self.task_file_template = rendered_taskfile

        rendered_startup = startup_tmpl.replace(
            "__TASK_FILE__",
            rendered_taskfile,
        )

        # --- Save final startup template ---
        self.startup_tmpl = rendered_startup


    def generate_startup_script(self):
        self.startup_script = textwrap.dedent(f"""\
#!/bin/bash
set -ex

echo "STARTUP_SCRIPT_START"

# Proxy & Environment
# Give internet access to GPU VMs for downloading model weights, etc.
{self.proxy}

# Default mount variables
MNT_BASE="/tmp"
MNT_PATHS=()
MODEL_SOURCES=()
MODEL_DIRNAME="hf"
MODEL_MOUNT_OPTS=()
PROJECT="{self.cfg.get('gcp:project')}"

# Inject config variables
{self.variables}

# Utility functions
{self.util_tmpl}

# Startup Template
{self.startup_tmpl}

# IMPORTANT! unique monitoring flag
echo "STARTUP_SCRIPT_COMPLETE"
        """)


def deploy_compute_program():
    """
    Pulumi deploy program that builds and provisions inference service.
    """
    # Base Pulumi config setup handled inside Executor._prepare()
    # Instantiate the Inference Executor (it runs _prepare internally)
    deployment = Deployment()
    deployment.run()