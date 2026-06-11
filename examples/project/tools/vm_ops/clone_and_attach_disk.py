from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from typing import Any, Callable

from dagploy_dax.service_lib.utils import emit_state, measure_duration


OutputFn = Callable[[str], None]


def run(cmd: list[str], on_output: OutputFn = print) -> str:
    on_output(f"⩥ {' '.join(cmd)}")
    p = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if p.stdout.strip(): on_output(p.stdout.strip())
    if p.stderr.strip(): on_output(p.stderr.strip())
    return p.stdout


def short_name(value: str) -> str:
    return value.rstrip("/").split("/")[-1]


def make_gcp_name(value: str, max_len: int = 63) -> str:
    name = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", value.lower())).strip("-") or "disk-copy"
    if not name[0].isalpha(): name = f"d-{name}"
    return name[:max_len].rstrip("-")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def find_resource_zone(kind: str, name: str, on_output: OutputFn = print) -> str:
    out = run(["gcloud", "compute", f"{kind}s", "list", "--filter", f"name={name}", "--format=json"], on_output)
    rows = [x for x in json.loads(out or "[]") if x.get("name") == name]

    if not rows:
        raise RuntimeError(f"{kind.capitalize()} not found: {name}")
    if len(rows) > 1:
        raise RuntimeError(f"Multiple {kind}s named {name}: {[short_name(x['zone']) for x in rows]}")

    return short_name(rows[0]["zone"])


def disk_exists_in_zone(disk: str, zone: str, on_output: OutputFn = print) -> bool:
    out = run(["gcloud", "compute", "disks", "list", "--zones", zone, "--filter", f"name={disk}", "--format=json"], on_output)
    return any(x.get("name") == disk for x in json.loads(out or "[]"))


@measure_duration
def vm_copy_disk(
    disk: str = "test-model",
    image: str | None = None,
    storage_location: str = "us",
    service: str = "",
    on_output: OutputFn = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    emit_state(update_state, "running", "vm_copy_disk", job_id=job_id, service=service, disk=disk)

    ts = timestamp()
    source_zone = find_resource_zone("disk", disk, on_output)
    image = make_gcp_name(image or f"img-{disk}-{ts}")

    try:
        run([
            "gcloud", "compute", "images", "create", image,
            "--source-disk", disk,
            "--source-disk-zone", source_zone,
            "--storage-location", storage_location,
        ], on_output)

        result = {
            "message": "Disk image created successfully",
            "source_disk": disk,
            "source_zone": source_zone,
            "image": image,
            "storage_location": storage_location,
        }

        emit_state(update_state, "finished", "vm_copy_disk.finished", job_id=job_id, service=service, result=result)
        return result

    except Exception as exc:
        emit_state(update_state, "error", "vm_copy_disk.error", job_id=job_id, service=service, error=str(exc), disk=disk)
        raise


@measure_duration
def vm_create_disk(
    image: str,
    target_zone: str,
    new_disk: str | None = None,
    disk_type: str = "pd-balanced",
    size_gb: int | None = None,
    service: str = "",
    on_output: OutputFn = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    emit_state(update_state, "running", "vm_create_disk", job_id=job_id, service=service, image=image, target_zone=target_zone)

    ts = timestamp()
    new_disk = make_gcp_name(new_disk or f"disk-{image}-{target_zone}-{ts}")

    try:
        if disk_exists_in_zone(new_disk, target_zone, on_output):
            raise RuntimeError(f"Disk already exists: {new_disk}")

        cmd = [
            "gcloud", "compute", "disks", "create", new_disk,
            "--zone", target_zone,
            "--image", image,
            "--type", disk_type,
        ]

        if size_gb:
            cmd += ["--size", f"{size_gb}GB"]

        run(cmd, on_output)

        result = {
            "message": "Disk created from image successfully",
            "image": image,
            "new_disk": new_disk,
            "zone": target_zone,
            "disk_type": disk_type,
            "size_gb": size_gb,
        }

        emit_state(update_state, "finished", "vm_create_disk.finished", job_id=job_id, service=service, result=result)
        return result

    except Exception as exc:
        emit_state(
            update_state,
            "error",
            "vm_create_disk.error",
            job_id=job_id,
            service=service,
            error=str(exc),
            image=image,
            target_zone=target_zone,
        )
        raise


@measure_duration
def vm_attach_disk(
    disk: str = "test-model",
    instance: str = "test",
    mode: str = "rw",
    service: str = "",
    on_output: OutputFn = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    emit_state(update_state, "running", "vm_attach_disk", job_id=job_id, service=service, disk=disk, instance=instance, mode=mode)

    try:
        disk_zone = find_resource_zone("disk", disk, on_output)
        instance_zone = find_resource_zone("instance", instance, on_output)

        if disk_zone != instance_zone:
            raise RuntimeError(f"Disk zone '{disk_zone}' must match instance zone '{instance_zone}'")

        run([
            "gcloud", "compute", "instances", "attach-disk",
            instance, "--zone", instance_zone, "--disk", disk, "--mode", mode,
        ], on_output)

        result = {"message": "Disk attached successfully", "disk": disk, "instance": instance, "zone": instance_zone, "mode": mode}
        emit_state(update_state, "finished", "vm_attach_disk.finished", job_id=job_id, service=service, result=result)
        return result

    except Exception as exc:
        emit_state(update_state, "error", "vm_attach_disk.error", job_id=job_id, service=service, error=str(exc), disk=disk, instance=instance)
        raise


@measure_duration
def vm_copy_and_attach_disk(
    disk: str = "test-model",
    instance: str = "test",
    storage_location: str = "us",
    mode: str = "rw",
    service: str = "",
    on_output: OutputFn = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    emit_state(update_state, "running", "vm_copy_and_attach_disk", job_id=job_id, service=service, disk=disk, instance=instance)

    try:
        copied = vm_copy_disk(
            disk=disk,
            target_zone=find_resource_zone("instance", instance, on_output),
            new_disk=make_gcp_name(f"{instance}-{timestamp()}"),
            storage_location=storage_location,
            service=service,
            on_output=on_output,
            job_id=job_id,
            update_state=update_state,
        )
        attached = vm_attach_disk(
            disk=copied["new_disk"],
            instance=instance,
            mode=mode,
            service=service,
            on_output=on_output,
            job_id=job_id,
            update_state=update_state,
        )

        result = {
            "message": "Disk copied and attached successfully",
            "source_disk": disk,
            "source_zone": copied["source_zone"],
            "new_disk": copied["new_disk"],
            "instance": instance,
            "zone": attached["zone"],
            "mode": mode,
        }

        emit_state(update_state, "finished", "vm_copy_and_attach_disk.finished", job_id=job_id, service=service, result=result)
        return result

    except Exception as exc:
        emit_state(update_state, "error", "vm_copy_and_attach_disk.error", job_id=job_id, service=service, error=str(exc), disk=disk, instance=instance)
        raise


def register() -> dict[str, Any]:
    return {"handlers": {
        "vm_copy_disk": vm_copy_disk, 
        "vm_attach_disk": vm_attach_disk, 
        "vm_create_disk": vm_create_disk,
        "vm_copy_and_attach_disk": vm_copy_and_attach_disk,
    }}
