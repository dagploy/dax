from __future__ import annotations

import json
import subprocess
from datetime import datetime
from typing import Any, Callable

from dagploy_dax.service_lib.utils import emit_state, measure_duration


def run_cmd(
    cmd: list[str],
    *,
    on_output: Callable[[str], None] = print,
) -> subprocess.CompletedProcess[str]:
    on_output(f"⩥ Running: {' '.join(cmd)}")

    try:
        completed = subprocess.run(
            cmd,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            on_output(exc.stdout.strip())
        if exc.stderr:
            on_output(exc.stderr.strip())
        raise

    if completed.stdout:
        on_output(completed.stdout.strip())

    if completed.stderr:
        on_output(completed.stderr.strip())

    return completed


def run_gcloud_json(
    cmd: list[str],
    *,
    on_output: Callable[[str], None] = print,
) -> Any:
    completed = run_cmd(
        cmd + ["--format=json"],
        on_output=on_output,
    )

    output = completed.stdout.strip()
    if not output:
        return None

    return json.loads(output)


def find_instance_zone(
    *,
    instance_name: str,
    on_output: Callable[[str], None] = print,
) -> str:
    data = run_gcloud_json(
        [
            "gcloud",
            "compute",
            "instances",
            "list",
            "--filter",
            f"name={instance_name}",
        ],
        on_output=on_output,
    )

    if not data:
        raise RuntimeError(f"Instance not found: {instance_name}")

    if len(data) > 1:
        zones = [item["zone"].split("/")[-1] for item in data]
        raise RuntimeError(
            f"Multiple instances named {instance_name} found in zones: {zones}. "
            "Use a unique instance name."
        )

    zone_url = data[0]["zone"]
    return zone_url.rstrip("/").split("/")[-1]


def describe_instance(
    *,
    instance_name: str,
    zone: str,
    on_output: Callable[[str], None] = print,
) -> dict[str, Any]:
    return run_gcloud_json(
        [
            "gcloud",
            "compute",
            "instances",
            "describe",
            instance_name,
            "--zone",
            zone,
        ],
        on_output=on_output,
    )

def describe_disk(
    *,
    disk_name: str,
    zone: str,
    on_output: Callable[[str], None] = print,
) -> dict[str, Any]:
    return run_gcloud_json(
        [
            "gcloud",
            "compute",
            "disks",
            "describe",
            disk_name,
            "--zone",
            zone,
        ],
        on_output=on_output,
    )


def extract_source_spec(
    instance: dict[str, Any],
    *,
    zone: str,
    on_output: Callable[[str], None] = print,
) -> dict[str, Any]:
    machine_type = instance["machineType"].rstrip("/").split("/")[-1]

    disks = instance.get("disks", [])
    boot_disk = next((disk for disk in disks if disk.get("boot")), None)

    if not boot_disk:
        raise RuntimeError("Boot disk not found")

    boot_disk_name = boot_disk["source"].rstrip("/").split("/")[-1]
    boot_disk_size_gb = boot_disk.get("diskSizeGb")

    disk_info = describe_disk(
        disk_name=boot_disk_name,
        zone=zone,
        on_output=on_output,
    )

    boot_disk_type = disk_info["type"].rstrip("/").split("/")[-1]

    network_interface = instance.get("networkInterfaces", [{}])[0]

    network = network_interface.get("network", "").rstrip("/").split("/")[-1]
    subnet = network_interface.get("subnetwork", "").rstrip("/").split("/")[-1]

    tags = instance.get("tags", {}).get("items", [])

    service_accounts = instance.get("serviceAccounts", [])
    service_account = None
    scopes: list[str] = []

    if service_accounts:
        service_account = service_accounts[0].get("email")
        scopes = service_accounts[0].get("scopes", [])

    return {
        "machine_type": machine_type,
        "boot_disk_name": boot_disk_name,
        "boot_disk_size_gb": boot_disk_size_gb,
        "boot_disk_type": boot_disk_type,
        "network": network,
        "subnet": subnet,
        "tags": tags,
        "service_account": service_account,
        "scopes": scopes,
    }


def image_exists(
    *,
    image_name: str,
    on_output: Callable[[str], None] = print,
) -> bool:
    data = run_gcloud_json(
        [
            "gcloud",
            "compute",
            "images",
            "list",
            "--filter",
            f"name={image_name}",
        ],
        on_output=on_output,
    )

    return bool(data)


@measure_duration
def vm_clone_boot_image(
    source: str = "test",
    target: str = "test-clone",
    service: str = "",
    on_output: Callable[[str], None] = print,
    job_id: str = "",
    update_state=None,
) -> dict[str, Any]:
    """
    Clone a GCP VM by creating a boot image from its boot disk.

    If the image already exists, it will be deleted first.
    """

    emit_state(
        update_state,
        "running",
        "vm_clone_boot_image_instance",
        job_id=job_id,
        service=service,
        source=source,
        target=target,
    )

    source_instance_name = source

    try:
        on_output(f"===> Finding zone for instance: {source_instance_name}")

        zone = find_instance_zone(
            instance_name=source_instance_name,
            on_output=on_output,
        )

        on_output(f"===> Source zone found: {zone}")
        on_output(f"===> Describing source instance: {source_instance_name}")

        source_instance = describe_instance(
            instance_name=source_instance_name,
            zone=zone,
            on_output=on_output,
        )

        spec = extract_source_spec(
            source_instance,
            zone=zone,
            on_output=on_output,
        )

        boot_disk_name = spec["boot_disk_name"]

        # Deterministic image name so we can safely replace it.
        image_name = f"boot-{boot_disk_name}"

        on_output(f"===> Boot disk: {boot_disk_name}")
        on_output(f"===> Image name: {image_name}")
        on_output(f"===> Machine type: {spec['machine_type']}")
        on_output(f"===> Subnet: {spec['subnet']}")

        if image_exists(image_name=image_name, on_output=on_output):
            on_output(f"===> Existing image found: {image_name}")
            on_output(f"===> Deleting existing image: {image_name}")

            run_cmd(
                [
                    "gcloud",
                    "compute",
                    "images",
                    "delete",
                    image_name,
                    "--quiet",
                ],
                on_output=on_output,
            )
        else:
            on_output(f"===> No existing image found: {image_name}")

        on_output(f"===> Creating image from boot disk: {boot_disk_name}")

        run_cmd(
            [
                "gcloud",
                "compute",
                "images",
                "create",
                image_name,
                "--source-disk",
                boot_disk_name,
                "--source-disk-zone",
                zone,
                "--storage-location",
                "us",
            ],
            on_output=on_output,
        )

    except Exception as exc:
        emit_state(
            update_state,
            "error",
            "vm_clone_boot_image_instance.error",
            job_id=job_id,
            service=service,
            error=str(exc),
        )

        on_output(f"❌ vm_clone_boot_image_instance failed: {exc}")
        raise

    result: dict[str, Any] = {
        "message": "Boot image created successfully",
        "source": source_instance_name,
        "target": target,
        "zone": zone,
        "boot_disk": boot_disk_name,
        "image": image_name,
    }

    if job_id:
        result["job_id"] = job_id

    emit_state(
        update_state,
        "finished",
        "vm_clone_boot_image_instance.finished",
        job_id=job_id,
        service=service,
        result=result,
    )

    return result


def rich_output(message: str) -> None:
    from rich.console import Console

    console = Console()
    console.print(message)


def main() -> None:
    vm_clone_boot_image(on_output=rich_output)


def register() -> dict[str, Any]:
    return {
        "handlers": {
            "vm_clone_boot_image": vm_clone_boot_image,
        }
    }


if __name__ == "__main__":
    main()