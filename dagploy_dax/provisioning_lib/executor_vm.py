from __future__ import annotations

import textwrap
import base64
import pulumi

from dagploy_dax.provisioning_lib.builder import ConfigBuilder
from dagploy_dax.provisioning_lib.vm import VMProvisioning
from dagploy_dax.provisioning_lib.vm_utils import (
    convert_to_boolean,
    prepare_disk_from_images,
    prepare_utils,
    sanitize_gcp_tag,
    to_bash_vars,
)

class Executor:
    """
    Default VM provisioning executor.
    It only receives the final rendered shell block.
    """

    def __init__(self):
        self.proxy = ""
        self.util_tmpl = ""
        self.startup_tmpl = ""
        self.tool_script = ""

        self.task_file_template = ""
        self.disk_executions = None
        self.disk_device_names = None
        self.variables = ""
        self.startup_script = ""

        self.raw_config = {}
        self.cfg = {}
        self.matched_entry = ""
        self.config_dict = {}
        self.service = ""
        self.cache_disks = []
        self.os_login = "TRUE"

        self.compute_instance = None
        self.depends_on = []
        self.log = pulumi.log
        self.log_startup = False

    def set_config(self):
        self.raw_config = pulumi.Config()

        processor = ConfigBuilder(self.raw_config)
        self.cfg, self.service, self.matched_entry, self.config_dict = processor.process()

        self.log_startup = convert_to_boolean(
            self.raw_config.get("logStartup", "false")
        )

    def startup_builder(self):
        """
        Build startup parts.
        """
        tool_script_b64 = self.cfg.get("tool_script_b64", "")

        if tool_script_b64:
            self.tool_script = base64.b64decode(
                tool_script_b64.encode("ascii")
            ).decode("utf-8")
        else:
            self.tool_script = self.cfg.get("tool_script", "")

        startup_tmpl = ""
        if not self.tool_script.strip():
            raise ValueError(f"tool_script is required in the config. {self.cfg}")

        self.util_tmpl, self.startup_tmpl = prepare_utils(
            self.cfg,
            self.matched_entry,
            startup_tmpl,
        )

    def disk_setup(self):
        self.disk_executions, self.disk_device_names = prepare_disk_from_images(self.cfg)
        self.cfg["disk_device_names"] = self.disk_device_names

    def pre_provisioning(self):
        pass

    def post_provisioning(self):
        # Add LB if public mode and IAP login enabled. 
        attach_vm_to_iap_lb(self.cfg, self.compute_instance, self.depends_on)

        # Add VM to internal domain if internal domain provided.
        attach_vm_to_internal_domain(
            self.cfg,
            self.compute_instance,
            self.depends_on,
        )

    def run(self):
        self.set_config()
        self.startup_builder()
        self.disk_setup()
        self.generate_variables()
        self.generate_startup_script()
        self.execute_startup_script()

        self.pre_provisioning()

        provisioner = VMProvisioning(
            self.cfg,
            self.disk_executions,
            self.disk_device_names,
            self.cache_disks,
        )

        self.compute_instance, self.cache_disks, self.depends_on = provisioner.execute()


    def generate_variables(self):
        self.variables = textwrap.dedent(to_bash_vars(self.cfg)).strip()

    def generate_startup_script(self):
        self.startup_script = textwrap.dedent(
            f"""\
            #!/bin/bash
            set -euo pipefail
            echo "STARTUP_SCRIPT_START"

            # Default mount variables
            MNT_BASE="/tmp"
            MNT_PATHS=()
            MODEL_SOURCES=()
            MODEL_DIRNAME="hf"
            MODEL_MOUNT_OPTS=()
            PROJECT="{self.cfg.get('project')}"

            # Proxy & Environment
            {self.proxy}

            # Inject config variables
            {self.variables}

            # Utility functions
            {self.util_tmpl}

            # Tool script
            {self.tool_script}

            # Legacy startup template fallback
            {self.startup_tmpl}

            echo "STARTUP_SCRIPT_COMPLETE"

            # Give delay for monitoring script to capture STARTUP_SCRIPT_COMPLETE
            sleep 15
            """
        )

    def execute_startup_script(self):
        numbered_script = "\n".join(
            f"{i:3d}: {line}"
            for i, line in enumerate(self.startup_script.splitlines(), start=1)
        )

        if self.log_startup:
            pulumi.log.info(f"\nSTARTUP SCRIPT PREVIEW:\n{numbered_script}")

        self.cfg.update(
            {
                "startup_script": self.startup_script,
                "tags": [
                    sanitize_gcp_tag(self.service),
                    sanitize_gcp_tag(self.cfg.get("stack_name", "default")),
                    "vm",
                ],
            }
        )
