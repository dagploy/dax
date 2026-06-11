from __future__ import annotations

from typing import Any, Callable

from dagploy_dax.service_lib.utils import emit_state, measure_duration

from .common import get_pulumi_program_dir, get_stack


@measure_duration
def pulumi_destroy(
    service: str = "",
    stack_name: str = "",
    profile: str = "",
    config: str = "",
    destroy_all: bool = False,
    on_output: Callable[[str], None] = print,
    cfg: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
    job_id: str = "",
    update_state=None,
    **kwargs: Any,
) -> dict[str, Any]:
    cfg = cfg or {}
    resources = resources or {}

    emit_state(update_state, "running", "pulumi_destroy", job_id=job_id, service=service, stack_name=stack_name)

    if not stack_name and not destroy_all:
        raise ValueError("Provide stack_name or set destroy_all=True")

    project = cfg.get("gcp:project", "")
    program_dir = get_pulumi_program_dir()

    on_output(f"pulumi_destroy started job_id={job_id}")
    on_output(f"project_name={project}")
    on_output(f"stack_name={stack_name}")
    on_output(f"pulumi_program_dir={program_dir}")

    try:
        stack = get_stack(stack_name, project, program_dir)
    except Exception:
        emit_state(update_state, "error", "pulumi_destroy.error", job_id=job_id, service=service, stack_name=stack_name)
        raise

    stack.destroy(on_output=on_output, suppress_outputs=True, target_dependents=True)
    stack.workspace.remove_stack(stack_name)

    result = {
        "message": f"Stack {stack_name} destroyed successfully",
        "service": service,
        "stack_name": stack_name,
        "profile": profile,
        "config": config,
        "cfg": cfg,
        "resources": resources,
        "job_id": job_id,
        "kwargs": kwargs,
    }

    emit_state(update_state, "finished", "pulumi_destroy.finished", job_id=job_id, result=result)

    return result


def rich_output(message: str) -> None:
    from rich.console import Console
    console = Console()
    console.print(message)

def register():
    return {
        "handlers": {
            "pulumi_destroy": pulumi_destroy,
        }
    }