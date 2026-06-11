from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from dagploy_dax.service_lib.utils import emit_state, measure_duration


def run(cmd: list[str], on_output: Callable[[str], None] = print) -> str:
    on_output(f"⩥ {' '.join(cmd)}")
    p = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if p.stdout.strip(): on_output(p.stdout.strip())
    if p.stderr.strip(): on_output(p.stderr.strip())
    return p.stdout


def short_name(x: str) -> str:
    return x.rstrip("/").split("/")[-1]


def find_disk(disk: str, on_output: Callable[[str], None] = print) -> tuple[str, str, list[str]]:
    out = run(["gcloud", "compute", "disks", "list", "--filter", f"name={disk}", "--format=json"], on_output)
    rows = [x for x in json.loads(out or "[]") if x.get("name") == disk]

    if not rows:
        raise RuntimeError(f"Disk not found: {disk}")
    if len(rows) > 1:
        raise RuntimeError(f"Multiple disks named {disk}: {[short_name(x['zone']) for x in rows]}")

    row = rows[0]
    return disk, short_name(row["zone"]), row.get("users") or []


@measure_duration
def vm_delete_disk(
    disk: str = "test-model",
    force: bool = False,
    service: str = "",
    on_output: Callable[[str], None] = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    emit_state(update_state, "running", "vm_delete_disk", job_id=job_id, service=service, disk=disk, force=force)

    try:
        disk, zone, users = find_disk(disk, on_output)

        if users and not force:
            raise RuntimeError(f"Disk is still attached to instance(s): {[short_name(x) for x in users]}")

        if users and force:
            for user in users:
                instance = short_name(user)
                run(["gcloud", "compute", "instances", "detach-disk", instance, "--zone", zone, "--disk", disk], on_output)

        run(["gcloud", "compute", "disks", "delete", disk, "--zone", zone, "--quiet"], on_output)

        result = {"message": "Disk deleted successfully", "disk": disk, "zone": zone, "force": force}
        emit_state(update_state, "finished", "vm_delete_disk.finished", job_id=job_id, service=service, result=result)
        return result

    except Exception as exc:
        emit_state(update_state, "error", "vm_delete_disk.error", job_id=job_id, service=service, error=str(exc), disk=disk)
        raise


def register() -> dict[str, Any]:
    return {"handlers": {"vm_delete_disk": vm_delete_disk}}


if __name__ == "__main__":
    vm_delete_disk()