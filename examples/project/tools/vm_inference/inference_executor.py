import yaml
import textwrap
from pathlib import Path

from string import Template

from dagploy_dax.provisioning_lib.executor_vm import Executor
from dagploy_dax.provisioning_lib.vm_utils import image_exists, prepare_utils, load_utils_script
from dagploy_dax.service_lib.tool_utils import read_required_file


class Deployment(Executor):
    """Override base _prepare() to include inference task updates."""

    def set_config(self):
        """Override base _prepare() to include inference task updates."""
        super().set_config()  # Get cfg, service, tmpl, startup_tmpl ready

        # Start customization
        cfg = self.cfg

        # check if images exists. Then combined with yaml config if found
        if image_exists(cfg['model_image'], cfg['project']):
            if cfg['images']:
                cfg['images'] = list(set(cfg['images'] + [cfg['model_image']]))
            else:
                cfg['images'] = [cfg['model_image']]


        # loop to check if image is exists
        for img_name in cfg['images']:
            if not image_exists(img_name, cfg['project']):
                raise ValueError(f"❌ Image '{img_name}' is not found in the project {cfg['project']}.")

        # Update changes
        self.cfg = cfg


    def startup_builder(self):
        cfg = self.cfg
        service = self.service

        default_task = {
            'port': cfg['port'],
            'model_image': cfg['model_image'],
            'gpu': cfg['gpu']
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

        # --- Load startup template ---
        startup_tmpl = read_required_file(Path(cfg["tools_dirpath"]) / "startup.sh")

        # --- Load taskfile template ---
        taskfile_tmpl = Template(read_required_file(Path(cfg["tools_dirpath"]) / "taskfile.yaml"))

        if service == 'vllm':            
            default_task['model_image'] = cfg['model_image']

        self.task_file_template = taskfile_tmpl.substitute(**default_task)
        task_file_startup_tmpl = startup_tmpl.replace("__TASK_FILE__", self.task_file_template)

        self.log.info(f"Taskfile Template: \n {self.task_file_template}")

        # Load exl2-config.yaml if needed
        extra_config_block = ""       

        # Insert the extra config block
        llm_startup_tmpl = task_file_startup_tmpl.replace("__EXTRA_CONFIG__", extra_config_block)
        self.startup_tmpl = llm_startup_tmpl

        # load utils script and add it into generated startup script
        self.util_tmpl = load_utils_script(cfg)


    def generate_startup_script(self):
        self.startup_script = textwrap.dedent(f"""\
#!/bin/bash
set -ex

# Install COS NVIDIA Driver
cos-extensions install gpu -- -version=latest
mount --bind /var/lib/nvidia /var/lib/nvidia
mount -o remount,exec /var/lib/nvidia

echo "STARTUP_SCRIPT_START"

# Default mount variables
MNT_BASE="/tmp"
MNT_PATHS=()
MODEL_SOURCES=()
MODEL_DIRNAME="hf"
MODEL_MOUNT_OPTS=()
PROJECT="{self.cfg.get('gcp:project')}"

# Proxy & Environment
{self.proxy}

# Inject config variables
{self.variables}

# Utility functions
{self.util_tmpl}

# Startup Template
{self.startup_tmpl}

echo "STARTUP_SCRIPT_COMPLETE"  # unique, easy to match
        """)

def deploy_llm_program():
    """
    Pulumi deploy program that builds and provisions inference service.
    """
    # Base Pulumi config setup handled inside Executor._prepare()
    # Instantiate the Inference Executor (it runs _prepare internally)
    deployment = Deployment()
    deployment.run()