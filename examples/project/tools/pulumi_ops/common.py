from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import pulumi.automation as auto

from dagploy_dax.service_lib.utils import load_pulumi_path


def reset_pulumi_local_login(state_dir: str | Path, on_output: Callable[[str], None] = print) -> None:
    cmd = ["pulumi", "login", f"file://{state_dir}"]
    on_output(f"⩥ Running: {' '.join(cmd)}")

    subprocess.run(cmd, check=True)
    on_output(f"✅ Pulumi now using local backend at {state_dir}")


def get_stack(
    stack_name: str,
    project_name: str,
    work_dir: str | Path,
    program=None,
) -> auto.Stack:
    try:
        return auto.select_stack(
            stack_name=stack_name,
            project_name=project_name,
            program=program or (lambda: None),
            opts=auto.LocalWorkspaceOptions(
                work_dir=str(work_dir),
                env_vars={
                    "PULUMI_K8S_DELETE_UNREACHABLE": "true",
                },
            ),
        )
    except Exception:
        raise ValueError(f"Stack {stack_name} is not found") from None


def get_pulumi_program_dir() -> Path:
    return load_pulumi_path().resolve()