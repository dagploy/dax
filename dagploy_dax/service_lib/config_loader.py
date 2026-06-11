# dagploy_dax/service_lib/config_loader.py

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf


def get_project_path(project_path: str | Path | None = None) -> Path:
    """
    Resolve the external user project path.
    """
    if project_path:
        return Path(project_path).expanduser().resolve()

    env_project_path = os.getenv("PROJECT_PATH")
    if env_project_path:
        return Path(env_project_path).expanduser().resolve()

    return Path.cwd().resolve()


def get_config_dir(
    config_dir: str | Path | None = None,
    project_path: str | Path | None = None,
) -> Path:
    """
    Resolve the external Hydra config directory.
    """
    if config_dir:
        path = Path(config_dir).expanduser().resolve()
    else:
        path = get_project_path(project_path) / "config"

    if not path.exists():
        raise FileNotFoundError(f"Hydra config directory not found. Checked: {path}")

    if not path.is_dir():
        raise NotADirectoryError(f"Hydra config path is not a directory: {path}")

    return path


def load_hydra_config(
    config_name: str = "config",
    config_dir: str | Path | None = None,
    project_path: str | Path | None = None,
    overrides: list[str] | None = None,
) -> DictConfig:
    """
    Load Hydra config from the user's project config folder.
    """
    resolved_config_dir = get_config_dir(
        config_dir=config_dir,
        project_path=project_path,
    )

    with initialize_config_dir(
        config_dir=str(resolved_config_dir),
        version_base=None,
    ):
        return compose(
            config_name=config_name,
            overrides=overrides or [],
        )


def load_hydra_config_dict(
    config_name: str = "config",
    config_dir: str | Path | None = None,
    project_path: str | Path | None = None,
    overrides: list[str] | None = None,
) -> dict[str, Any]:
    """
    Load Hydra config and convert it into a normal Python dict.
    """
    cfg = load_hydra_config(
        config_name=config_name,
        config_dir=config_dir,
        project_path=project_path,
        overrides=overrides,
    )

    resolved = OmegaConf.to_container(cfg, resolve=True)

    if not isinstance(resolved, dict):
        raise TypeError("Hydra config must resolve to a dictionary")

    return resolved


def load_runtime_config(
    config_name: str = "config",
    config_dir: str | Path | None = None,
    project_path: str | Path | None = None,
    overrides: list[str] | None = None,
) -> dict[str, Any]:
    """
    Load DAX runtime config from the external user project.
   """
    resolved_project_path = get_project_path(project_path)
    resolved_config_dir = get_config_dir(
        config_dir=config_dir,
        project_path=resolved_project_path,
    )

    raw_cfg = load_hydra_config_dict(
        config_name=config_name,
        config_dir=resolved_config_dir,
        project_path=resolved_project_path,
        overrides=overrides,
    )

    env_cfg = raw_cfg.get("env", {})
    resources_cfg = raw_cfg.get("resources", {})

    if not isinstance(env_cfg, dict):
        raise TypeError("Hydra config key 'env' must be a dictionary")

    if not isinstance(resources_cfg, dict):
        raise TypeError("Hydra config key 'resources' must be a dictionary")

    return {
        "project_path": str(resolved_project_path),
        "config_dir": str(resolved_config_dir),
        "cfg": env_cfg,
        "resources": resources_cfg,
        "raw_cfg": raw_cfg,
    }