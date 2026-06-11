import textwrap
from pathlib import Path

from dagploy_dax.provisioning_lib.executor_vm import Executor
from dagploy_dax.provisioning_lib.vm_utils import create_image_disks, convert_strings_to_list
from dagploy_dax.provisioning_lib.config_schema import HFValidation
from dagploy_dax.service_lib.utils import read_required_file, prepare_proxy


class Deployment(Executor):
    """Provision VM that downloads HF model/dataset into cache disk."""

    def set_config(self):
        super().set_config()

        cfg = self.cfg.copy()

        if cfg["service"] not in ["download_hf", "download_hf_multi"]:
            raise ValueError(
                f"This service is not supported. "
                f"Only download_hf and download_hf_multi are supported. "
                f"Given {cfg['service']}"
            )

        # Set extra common settings
        cfg['repo_urls'] = convert_strings_to_list(self.raw_config.get("repoUrls", []))

        if not cfg.get('repo_urls', ''):
            HFValidation.from_pulumi_config(self.config_dict)

        if not self.raw_config.get('family', ''):
            cfg['family'] = 'custom'

        self.cfg = cfg

    def startup_builder(self):
        cfg = self.cfg

        # add proxy
        self.proxy = prepare_proxy(cfg)

        # setup utils script
        util_cache = read_required_file(Path(cfg["tools_dirpath"]) / "util_cache_vm.sh")
        self.util_tmpl = util_cache

        startup_tmpl = read_required_file(Path(cfg["tools_dirpath"]) / "startup_cache_disk.sh")
        download_hf = read_required_file(Path(cfg["tools_dirpath"]) / "download_hf.sh")

        if "__EXECUTION_SCRIPT__" not in startup_tmpl:
            raise ValueError("startup_cache_disk.sh is missing __EXECUTION_SCRIPT__ placeholder")

        execution_script = "# === HF DOWNLOAD ====\n"
        execution_script += download_hf.rstrip() + "\n\n"

        self.startup_tmpl = startup_tmpl.replace(
            "__EXECUTION_SCRIPT__",
            f"\n# === BEGIN EXECUTION ({cfg['service']}) ===\n"
            f"{execution_script}\n"
            f"# === END EXECUTION ===\n",
        )

    def disk_setup(self):
        self.cache_disks, self.disk_device_names = create_image_disks(self.cfg)
        self.cfg["disk_device_names"] = self.disk_device_names

    def generate_startup_script(self):
        self.startup_script = textwrap.dedent(f"""\
#!/bin/bash
set -ex
echo "STARTUP_SCRIPT_START"

# default mount variable
MNT_BASE="/tmp"
MNT_PATHS=()

# Proxy & Environment
{self.proxy}

# Inject config variables
{self.variables}

# Utility functions
{self.util_tmpl}

# Startup Template
{self.startup_tmpl}

# IMPORTANT! unique monitoring flag
echo "STARTUP_SCRIPT_COMPLETE"

# Give delay for monitoring script to capture STARTUP_SCRIPT_COMPLETE
sleep 15

shutdown -h now
    """)