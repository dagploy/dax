from __future__ import annotations

import subprocess
from typing import Any, Callable

from dagploy_dax.service_lib.utils import emit_state, measure_duration

from .common import get_pulumi_program_dir


@measure_duration
def pulumi_ls(
    service: str = "",
    on_output: Callable[[str], None] = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    emit_state(
        update_state,
        "running",
        "pulumi_ls",
        job_id=job_id,
        service=service,
    )

    program_dir = get_pulumi_program_dir()
    cmd = ["pulumi", "stack", "ls"]

    on_output(f"⩥ Running: {' '.join(cmd)}")
    on_output(f"pulumi_program_dir={program_dir}")

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(program_dir),
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        emit_state(
            update_state,
            "error",
            "pulumi_ls.error",
            job_id=job_id,
            service=service,
        )

        if exc.stdout:
            on_output(exc.stdout)

        if exc.stderr:
            on_output(exc.stderr)

        raise

    output = completed.stdout.strip()

    if output:
        on_output(output)

    result: dict[str, Any] = {
        "message": "Pulumi stacks listed successfully",
        "output": output,
    }

    if job_id:
        result["job_id"] = job_id

    emit_state(
        update_state,
        "finished",
        "pulumi_ls.finished",
        job_id=job_id,
        result=result,
    )

    return result


def rich_output(message: str) -> None:
    from rich.console import Console

    console = Console()
    console.print(message)


def register() -> dict[str, Any]:
    return {
        "handlers": {
            "pulumi_ls": pulumi_ls,
        }
    }

