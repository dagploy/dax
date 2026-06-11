import pulumi
from pathlib import Path

from dagploy_dax.provisioning_lib.vm_utils import load_pulumi_config, convert_strings_to_list, set_gpu_from_machine_type, convert_to_boolean, zone_to_region
from dagploy_dax.provisioning_lib.config_schema import PulumiConfigModel, DockerVMValidation

class ConfigBuilder:
    def __init__(self, pulumi_config: pulumi.Config):
        self.config = pulumi_config
        self.config_dict = load_pulumi_config(self.config)
        self.cfg = PulumiConfigModel.from_pulumi_config(self.config_dict)
        self.config_path = Path(self.config.require("config_path"))
        self.gcp_script_path = Path(self.config.require("gcp_script_path"))
        self.service = self.config.require("service")
        self.matched_entry = None

    def process(self):
        self._convert_config()
        self._apply_gpu_settings()

        return self.cfg, self.service, self.matched_entry, self.config_dict

    def resolve(self, key, default_key, static_default=""):
        """
        Resolve value from config[key], then config[default_key], or static default.
        """
        v = self.config.get(key, "")
        if v:
            return v

        v2 = self.config.get(default_key, "")
        if v2:
            return v2

        return static_default

    def _convert_config(self):
        """
        This contains translation from default and merged variables
        Any variables need to be passed from config yaml into executor can be done here.
        """
        cfg = self.cfg

        cfg['region'] = zone_to_region(cfg['zone'])

        cfg['network'] = self.config.get('network', "default")

        # change subnetwork according to network value
        # for private vpc, the subnetwork is same with region
        if cfg['network'].lower().strip() == "default":
            cfg['subnetwork'] = "default"
        else:
            cfg['subnetwork'] = cfg['region']

        cfg['config_path'] = str(self.config_path)
        cfg['images'] = convert_strings_to_list(self.config.get('images', []))
        cfg['public_mode'] = convert_to_boolean(self.config.get('publicMode', "false"))
        cfg['open_ports'] = convert_strings_to_list(self.config.get('openPorts', "[]"))
        cfg['iap_login'] = convert_to_boolean(self.config.get('iapLogin', "false"))
        cfg['docker_run'] = self.config.get('dockerRun', "dagploy/daxrun:latest")
        cfg['error_destroy'] = convert_to_boolean(self.config.get('errorDestroy', "true"))

        # These to read the default variable or default config "gcp:" prefixed variables in config yaml 
        # if the variable is not set in config yaml, then it will use the default variable with "gcp:" prefix.
        # Add more variable in config yaml, then add here with "gcp:" prefix. it will automatically load the default variable.

        # Then add `service_lib/utils.py`
        # DEFAULT_KEYS = {
        #     "networkPublicDefault": ("gcp:networkPublic", ""),
        #     "networkDefault": ("gcp:network", ""),

        required_params = ["proxy", "os_login", "network_public", "network", 
                           "service_account_key", "hf_token", "iap_login", "docker_run", "error_destroy"]

        additional_params = []

        # load this default internal if exists
        if cfg.get("internal_domain", ""):
            additional_params.extend(['dns_zone', 'healthcheck_path', 'healthcheck_port'])

        # load this if public, domain and iap activated, add into load balancer with IAP
        if cfg['public_mode'] and cfg.get("domain", "") and cfg.get("iap_login", False):
            additional_params.extend(["healthcheck_path", "dns_zone_public", 
                                      "healthcheck_port", "iap_user",
                                      "oauth_client", "oauth_secret", "url_map"])
        elif cfg['public_mode'] and cfg.get("domain", "") and not cfg.get("iap_login", False):
            additional_params.extend(["dns_zone_public"])

        update_params = list(set(additional_params))
        required_params.extend(update_params)

        # Iterate through the keys. the default already converted into lowercase.
        for key in required_params:
            cfg[key] = (
                    cfg.get(key) or
                    cfg.get(f"{key}_default")
            )

        cfg['iap_user'] = convert_strings_to_list(self.config.get('iapUser', []))

        # If public_mode is not given, make it false by default.
        if not cfg.get("public_mode", ""):
            cfg['public_mode'] = False

        # Manage the load balancer logic between public mode true or false
        # public mode True means its can be accessible from outside, whether via IP or IAP.
        # this condition is not related with "internal" domain, as you can have both public and internal running.
        #
        # TWO CONDITION: 
        # 
        # if internal domain required, then use private network: vpc
        # L7 Load balancer for external domain is not attached to network, so it will be fine
        #
        # if no internal domain, then use default network and subnetwork, and attach LB to VM if IAP login is activated.

        if cfg['public_mode'] and not cfg.get("internal_domain", ""):
            # if no internal domain set, then it will use IP address. Use default network
            cfg['network'] = cfg.get('network_public_default')
            cfg['subnetwork'] = cfg.get('network_public_default')

            # if no external domain provided, then just public IP address
            if cfg.get("domain", "") and not cfg['port']:
                # add default port for health check if domain provided when port is not provided.
                cfg['port'] = cfg['healthcheck_port']

                # validate the config
                DockerVMValidation.from_pulumi_config(cfg)

        self.cfg = cfg

    def _apply_gpu_settings(self):
        gpu_mapping_toml = self.gcp_script_path / "gpu_mapping.toml"

        self.cfg = set_gpu_from_machine_type(
            self.cfg,
            mapping_path=str(gpu_mapping_toml),
            default_gpu=0,
        )

        if "gpu" in self.cfg:
            if self.cfg['gpu'] == 0:
                raise ValueError(f"❌ GPU not found for serving machine {self.cfg['machine_type']}.")

            # overwrite from YAML
            gpu_count = self.config.require("gpu")
            if gpu_count:
                self.cfg["gpu"] = gpu_count