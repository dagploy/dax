import yaml
import json

from pathlib import Path
from typing import Any, Dict, Tuple, Optional
from dataclasses import dataclass, field

from dagploy_dax.service_lib.utils import configure_pulumi_config, load_project_path

@dataclass
class ConfigSetup:
    """
    Dataclass replacement for BuildConfig — keeps the same argument structure.
    Handles merging of cfg, yaml_config, and runtime metadata.

    Example:
        builder = ConfigBuilder(
            cfg={"gpu": 1},
            stack_name="vm-stack",
            provisioning_function_dir="/app/pulumi",
            service="gcp_vm",
            profile="default",
            resources={"disk": "pd-ssd"},
            yaml_config="",
        )
        default_config, config, extra = builder()
    """

    cfg: Dict[str, Any]
    stack_name: str
    provisioning_function_dir: str
    service: str
    profile: str
    resources: Dict[str, Any] = field(default_factory=dict)
    config: Optional[Any] = None
    on_output: Any = print

    def normalize_config(self):
        """Normalize self.config into a real dict if needed."""

        # Already dict → nothing to do
        if isinstance(self.config, dict):
            return

        # If it's a string representing a dict → try JSON/YAML
        if isinstance(self.config, str):
            cfg_text = self.config.strip()

            # Case 1: JSON-like dict string
            if cfg_text.startswith("{") and cfg_text.endswith("}"):
                try:
                    self.config = json.loads(cfg_text)
                    self.on_output("🔄 Parsed config string → dict (JSON)")
                    return
                except Exception:
                    pass  # fall through to YAML

            # Case 2: YAML-like dict string
            try:
                parsed = yaml.safe_load(cfg_text)
                if isinstance(parsed, dict):
                    self.config = parsed
                    self.on_output("🔄 Parsed config string → dict (YAML)")
                    return
            except Exception:
                pass

        # Otherwise it's a profile name (string)
        # Leave it as-is → handled later
        self.on_output(f"Treating '{self.config}' as a config profile name")

    def __call__(self) -> Tuple[dict, dict, dict]:
        """Prepare and return (default_config, config, extra_configs)."""
        self.on_output(f"🏽 Preparing configuration for {self.service}")

        # --- Base config from arguments ---
        env_config = self.cfg.copy()

        # 1️⃣ Normalize config up front
        self.normalize_config()

        # --- Load YAML (if provided) ---
        if isinstance(self.config, str):
            self.on_output(f"✅ Received config : {self.config}")

            yaml_data = {}

            config_yaml_path = Path(load_project_path()) / 'config_yaml'
            if not config_yaml_path.exists():
                self.on_output(f'❎ No config_yaml folder found! Please create "{config_yaml_path}".')
                raise KeyError(f"No config_yaml folder found at {config_yaml_path}")

            # Auto check file
            auto_file_convert = config_yaml_path / f"{self.config}.yaml"

            if auto_file_convert.exists():
                yaml_data = yaml.safe_load(auto_file_convert.read_text())
                self.on_output(f"✨ Loaded YAML config: {auto_file_convert}")

            if not yaml_data:
                if not 'dax_config' in env_config:
                    self.on_output(f"❎ YAML at config/env/ missing required 'dax_config' section. Please setup a new one.")
                    raise KeyError("YAML at config/env/ missing required 'dax_config' section")

                if self.config not in env_config['dax_config']:
                    self.on_output(f'❎ No {self.config} found in dax_config that located in config/env')
                    raise KeyError("YAML at config/env/ missing required 'dax_config' section")

                file_config = env_config['dax_config'][self.config]
                file_config_path = config_yaml_path / file_config

                if not file_config_path.exists():
                    self.on_output(f'❎ No {file_config_path} found in config_yaml at project folder')
                    raise KeyError(f"No {file_config_path} found")

                yaml_data = yaml.safe_load(file_config_path.read_text())
                self.on_output(f"✨ Loaded YAML config: {file_config_path}")

            # Update with real dict config
            self.config = yaml_data

        # --- 3️⃣ Normalize config with metadata ---
        default_config, config = configure_pulumi_config(
            cfg=env_config,
            stack_name=self.stack_name,
            infra_yaml_path=self.provisioning_function_dir,
            service=self.service,
            profile=self.profile,
            resources=self.resources,
        )

        # create and override extra_configs
        extra_configs = {"service": self.service}

        if isinstance(self.config, dict):
            extra_configs.update({k: v for k, v in self.config.items() if v is not None})

        self.on_output("🏽  Build config completed.")

        return default_config, config, extra_configs

