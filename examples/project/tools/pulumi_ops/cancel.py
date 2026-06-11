from __future__ import annotations

from typing import Any, Callable

from pulumi.automation import Stack

from dagploy_dax.service_lib.utils import emit_state, measure_duration

from .common import get_pulumi_program_dir, get_stack


def _safe_call(
    label: str,
    fn,
    *,
    on_output: Callable[[str], None],
    raise_error: bool = False,
) -> bool:
    try:
        fn()
        return True
    except Exception as e:
        on_output(f"[pulumi_cancel] {label} failed: {e}")

        if raise_error:
            raise

        return False


def _remove_stack_if_requested(
    stack: Stack,
    stack_name: str,
    *,
    remove_stack: bool,
    on_output: Callable[[str], None],
) -> None:
    if not remove_stack:
        return

    _safe_call(
        "remove_stack",
        lambda: stack.workspace.remove_stack(stack_name),
        on_output=on_output,
        raise_error=False,
    )


@measure_duration
def pulumi_cancel(
    service: str = "",
    stack_name: str = "",
    profile: str = "",
    config: str = "",
    refresh: bool = True,
    destroy: bool = False,
    remove_stack: bool = False,
    on_output: Callable[[str], None] = print,
    cfg: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
    job_id: str = "",
    update_state=None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Emergency Pulumi cancellation.

    Use this when provisioning must be interrupted immediately, for example
    when a dangerous startup script was submitted.

    Behavior:
    - cancel the active Pulumi operation
    - optionally refresh the stack
    - optionally destroy known resources
    - optionally remove the stack

    Notes:
    - Pulumi cancel is not a safe rollback.
    - It can leave cloud resources orphaned if they were created but not written
      into Pulumi state yet.
    - For VM emergency cancellation, caller should also stop/delete the VM
      directly by GCP labels/job_id if possible.
    """
    cfg = cfg or {}
    resources = resources or {}

    if not stack_name:
        raise ValueError("Provide stack_name")

    project = cfg.get("gcp:project", "")
    program_dir = get_pulumi_program_dir()

    emit_state(
        update_state,
        "running",
        "pulumi_cancel",
        job_id=job_id,
        service=service,
        stack_name=stack_name,
    )

    on_output(f"[pulumi_cancel] started job_id={job_id}")
    on_output(f"[pulumi_cancel] project_name={project}")
    on_output(f"[pulumi_cancel] stack_name={stack_name}")
    on_output(f"[pulumi_cancel] pulumi_program_dir={program_dir}")
    on_output(f"[pulumi_cancel] refresh={refresh}")
    on_output(f"[pulumi_cancel] destroy={destroy}")
    on_output(f"[pulumi_cancel] remove_stack={remove_stack}")

    try:
        stack = get_stack(stack_name, project, program_dir)
    except Exception:
        emit_state(
            update_state,
            "error",
            "pulumi_cancel.get_stack.error",
            job_id=job_id,
            service=service,
            stack_name=stack_name,
        )
        raise

    cancelled = _safe_call(
        "cancel",
        stack.cancel,
        on_output=on_output,
        raise_error=False,
    )

    refreshed = False
    destroyed = False

    if refresh:
        emit_state(
            update_state,
            "running",
            "pulumi_cancel.refresh",
            job_id=job_id,
            service=service,
            stack_name=stack_name,
        )

        refreshed = _safe_call(
            "refresh",
            lambda: stack.refresh(on_output=on_output, suppress_outputs=True),
            on_output=on_output,
            raise_error=False,
        )

    if destroy:
        emit_state(
            update_state,
            "running",
            "pulumi_cancel.destroy",
            job_id=job_id,
            service=service,
            stack_name=stack_name,
        )

        destroyed = _safe_call(
            "destroy",
            lambda: stack.destroy(
                on_output=on_output,
                suppress_outputs=True,
                target_dependents=True,
            ),
            on_output=on_output,
            raise_error=True,
        )

        _remove_stack_if_requested(
            stack,
            stack_name,
            remove_stack=remove_stack,
            on_output=on_output,
        )

    result = {
        "message": f"Stack {stack_name} cancellation requested",
        "service": service,
        "stack_name": stack_name,
        "profile": profile,
        "config": config,
        "cfg": cfg,
        "resources": resources,
        "job_id": job_id,
        "cancelled": cancelled,
        "refreshed": refreshed,
        "destroyed": destroyed,
        "remove_stack": remove_stack,
        "kwargs": kwargs,
    }

    emit_state(
        update_state,
        "finished",
        "pulumi_cancel.finished",
        job_id=job_id,
        result=result,
    )

    return result


def rich_output(message: str) -> None:
    from rich.console import Console

    console = Console()
    console.print(message)


def register():
    return {
        "handlers": {
            "pulumi_cancel": pulumi_cancel,
        }
    }