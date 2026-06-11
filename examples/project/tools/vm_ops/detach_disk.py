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


def find_disk_attachment(disk: str, on_output: Callable[[str], None] = print) -> tuple[str, str, str]:
    out = run(["gcloud", "compute", "disks", "list", "--filter", f"name={disk}", "--format=json"], on_output)
    disks = [x for x in json.loads(out or "[]") if x.get("name") == disk]

    if not disks:
        raise RuntimeError(f"Disk not found: {disk}")
    if len(disks) > 1:
        raise RuntimeError(f"Multiple disks named {disk}: {[short_name(x['zone']) for x in disks]}")

    disk_row = disks[0]
    zone = short_name(disk_row["zone"])
    users = disk_row.get("users") or []

    if not users:
        raise RuntimeError(f"Disk is not attached to any instance: {disk}")
    if len(users) > 1:
        raise RuntimeError(f"Disk is attached to multiple instances: {[short_name(x) for x in users]}")

    return disk, short_name(users[0]), zone


@measure_duration
def vm_detach_disk(
    disk: str = "test-model",
    service: str = "",
    on_output: Callable[[str], None] = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    emit_state(update_state, "running", "vm_detach_disk", job_id=job_id, service=service, disk=disk)

    try:
        disk, instance, zone = find_disk_attachment(disk, on_output)
        run(["gcloud", "compute", "instances", "detach-disk", instance, "--zone", zone, "--disk", disk], on_output)

        result = {"message": "Disk detached successfully", "disk": disk, "instance": instance, "zone": zone}
        emit_state(update_state, "finished", "vm_detach_disk.finished", job_id=job_id, service=service, result=result)
        return result

    except Exception as exc:
        emit_state(update_state, "error", "vm_detach_disk.error", job_id=job_id, service=service, error=str(exc), disk=disk)
        raise


def register() -> dict[str, Any]:
    return {"handlers": {"vm_detach_disk": vm_detach_disk}}


if __name__ == "__main__":
    vm_detach_disk()