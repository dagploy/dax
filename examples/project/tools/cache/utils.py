from dagploy_dax.service_lib.utils import load_config_path

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf


def load_default_config():
    """Loads default config from PROJECT_PATH/config."""
    default_config_path = load_config_path()

    if not default_config_path.exists():
        raise FileNotFoundError(f"Config folder not found: {default_config_path}")

    with initialize_config_dir(config_dir=str(default_config_path), version_base=None):
        raw_cfg: DictConfig = compose(config_name="config")

    cfg = OmegaConf.to_container(raw_cfg.env, resolve=True)
    resources = OmegaConf.to_container(raw_cfg.resources, resolve=True)

    return cfg, resources