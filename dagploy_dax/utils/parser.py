from omegaconf import OmegaConf
from copy import deepcopy

def get_project_name(cfg):
    return cfg.get("gcp:project")

def list_service_names(cfg):
    return list(cfg["services"].keys())

def get_service_spec(cfg, service_name):
    try:
        return cfg["services"][service_name]
    except KeyError:
        raise ValueError(f"No service '{service_name}' in services")

def get_resource_profile(resources, profile_name):
    try:
        return resources["vms"][profile_name]
    except KeyError:
        raise ValueError(f"No resource profile '{profile_name}' in resources")

def to_plain_dict(obj):
    """
    Recursively convert OmegaConf configs to Python dicts/lists.
    """
    return OmegaConf.to_container(obj, resolve=True)

def merged_profile(cfg, service_name, profile=None, resources=None):
    """
    Returns merged config dict for this service (profile + overrides).
    Always returns native dict/lists (never OmegaConf types).
    """
    spec = get_service_spec(cfg, service_name)

    # Get profile configuration
    if profile:
        base = deepcopy(get_resource_profile(resources, profile))
    else:
        base = spec.get("default")

    overrides = spec.get("overrides") or {}

    # Apply overrides (assume they are plain Python values)
    for key, value in overrides.items():

        try:
            base[key] = value
        except Exception as e:
            raise ValueError(f'Error on {key} {value} with detail: {overrides}')

    return base
