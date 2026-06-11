from __future__ import annotations

import json
from typing import Any

import click

from dax.client import DaxClient
from dax.cli import add_common_submit_options, console, submit_capability


def register(run: click.Group) -> None:
    for cmd in (
        destroy,
        pulumi_cancel,
        vm_clone_boot_image,
        vm_copy_disk,
        vm_create_disk,
        vm_attach_disk,
        vm_copy_and_attach_disk,
        pulumi_ls,
    ):
        run.add_command(cmd)


@click.command("destroy")
@click.option("--stack-name", required=True, help="DAX stack name to destroy.")
@add_common_submit_options
@click.pass_context
def destroy(ctx: click.Context, stack_name: str, output_json: bool, use_httpx: bool) -> None:
    """
    Destroy selected stack and all child resources.
    """
    client: DaxClient = ctx.obj["client"]
    submit_capability(
        client=client,
        handler="pulumi_destroy",
        params={"stack_name": stack_name},
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command("cancel")
@click.option("--stack-name", required=True, help="DAX stack name to cancel.")
@click.option(
    "--no-refresh",
    is_flag=True,
    default=False,
    help="Skip pulumi refresh after cancellation.",
)
@click.option(
    "--destroy/--no-destroy",
    default=True,
    help="Destroy known resources after cancellation. Default: --destroy.",
)
@click.option(
    "--remove-stack",
    is_flag=True,
    default=False,
    help="Remove Pulumi stack after destroy succeeds.",
)
@add_common_submit_options
@click.pass_context
def pulumi_cancel(
    ctx: click.Context,
    stack_name: str,
    no_refresh: bool,
    destroy: bool,
    remove_stack: bool,
    output_json: bool,
    use_httpx: bool,
) -> None:
    """
    Emergency-cancel Pulumi provisioning.
    """
    client: DaxClient = ctx.obj["client"]

    submit_capability(
        client=client,
        handler="pulumi_cancel",
        params={
            "stack_name": stack_name,
            "refresh": not no_refresh,
            "destroy": destroy,
            "remove_stack": remove_stack,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command("vm_clone_boot_image")
@click.option("--source", required=True, help="Source boot image name to clone.")
@add_common_submit_options
@click.pass_context
def vm_clone_boot_image(ctx: click.Context, source: str, output_json: bool, use_httpx: bool) -> None:
    """
    Clone VM instance boot disk into an image.
    """
    client: DaxClient = ctx.obj["client"]

    submit_capability(
        client=client,
        handler="vm_clone_boot_image",
        params={"source": source},
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command("vm_copy_disk")
@click.option("--disk", required=True, help="Source disk name to convert into image.")
@click.option("--image", default=None, help="Name for the created disk image. Auto-generated if empty.")
@click.option("--storage-location", default="us", show_default=True, help="Image storage location.")
@add_common_submit_options
@click.pass_context
def vm_copy_disk(
    ctx: click.Context,
    disk: str,
    image: str | None,
    storage_location: str,
    output_json: bool,
    use_httpx: bool,
) -> None:
    """
    Create a reusable GCP disk image from an existing disk.

    Example:

        dax run vm_copy_disk --disk model-cache --image img-model-cache
    """
    client: DaxClient = ctx.obj["client"]

    params: dict[str, Any] = {
        "disk": disk,
        "storage_location": storage_location,
    }

    if image:
        params["image"] = image

    submit_capability(
        client=client,
        handler="vm_copy_disk",
        params=params,
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command("vm_create_disk")
@click.option("--image", required=True, help="Source disk image name.")
@click.option("--target-zone", required=True, help="Target zone where the disk will be created.")
@click.option("--new-disk", default=None, help="Name for the new disk. Auto-generated if empty.")
@click.option("--disk-type", default="pd-balanced", show_default=True, help="Disk type for the new disk.")
@click.option("--size-gb", type=int, default=None, help="Optional disk size in GB.")
@add_common_submit_options
@click.pass_context
def vm_create_disk(
    ctx: click.Context,
    image: str,
    target_zone: str,
    new_disk: str | None,
    disk_type: str,
    size_gb: int | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    """
    Create a zonal GCP disk from a reusable disk image.

    Example:

        dax run vm_create_disk --image img-model-cache --target-zone us-east5-a --new-disk model-cache-us-east5-a
    """
    client: DaxClient = ctx.obj["client"]

    params: dict[str, Any] = {
        "image": image,
        "target_zone": target_zone,
        "disk_type": disk_type,
    }

    if new_disk:
        params["new_disk"] = new_disk

    if size_gb:
        params["size_gb"] = size_gb

    submit_capability(
        client=client,
        handler="vm_create_disk",
        params=params,
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command("vm_attach_disk")
@click.option("--disk", required=True, help="Existing disk name to attach.")
@click.option("--instance", required=True, help="Target VM instance name.")
@click.option(
    "--mode",
    default="rw",
    show_default=True,
    type=click.Choice(["rw", "ro"]),
    help="Attach mode.",
)
@add_common_submit_options
@click.pass_context
def vm_attach_disk(
    ctx: click.Context,
    disk: str,
    instance: str,
    mode: str,
    output_json: bool,
    use_httpx: bool,
) -> None:
    """
    Attach an existing disk to an existing VM.

    Disk and VM must be in the same zone.

    Example:

        dax run vm_attach_disk --disk model-cache-copy --instance gpu-vm
    """
    client: DaxClient = ctx.obj["client"]

    submit_capability(
        client=client,
        handler="vm_attach_disk",
        params={
            "disk": disk,
            "instance": instance,
            "mode": mode,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command("vm_copy_and_attach_disk")
@click.option("--disk", required=True, help="Source disk name to copy.")
@click.option("--instance", required=True, help="Target VM instance name.")
@click.option(
    "--storage-location",
    default="us",
    show_default=True,
    help="Snapshot storage location.",
)
@click.option(
    "--mode",
    default="rw",
    show_default=True,
    type=click.Choice(["rw", "ro"]),
    help="Attach mode.",
)
@add_common_submit_options
@click.pass_context
def vm_copy_and_attach_disk(
    ctx: click.Context,
    disk: str,
    instance: str,
    storage_location: str,
    mode: str,
    output_json: bool,
    use_httpx: bool,
) -> None:
    """
    Copy a disk into the target VM zone, then attach it.

    This keeps the old behavior.

    Example:

        dax run vm_copy_and_attach_disk --disk model-cache --instance gpu-vm
    """
    client: DaxClient = ctx.obj["client"]

    submit_capability(
        client=client,
        handler="vm_copy_and_attach_disk",
        params={
            "disk": disk,
            "instance": instance,
            "storage_location": storage_location,
            "mode": mode,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )


def print_wait_result(raw: dict[str, Any], output_json: bool) -> int:
    if output_json:
        console.print_json(json.dumps(raw, default=str))
        return 0

    tool = raw.get("tool", raw)
    output = tool.get("output") or {}

    console.print(
        f"[bold green]{tool.get('status', 'UNKNOWN')}[/bold green] "
        f"{tool.get('handler', '')}"
    )

    if output.get("message"):
        console.print(output["message"])

    if output.get("output"):
        console.print(output["output"])

    return 0


@click.command("ls")
@click.option("--json", "output_json", is_flag=True, default=False)
@click.option("--use-httpx", is_flag=True, default=False)
@click.pass_context
def pulumi_ls(ctx: click.Context, output_json: bool, use_httpx: bool) -> None:
    """
    Show running stacks.
    """
    client: DaxClient = ctx.obj["client"]

    try:
        raw = (
            client.submit_wait(handler="pulumi_ls")
            if use_httpx
            else client.submit_wait_with_curl(handler="pulumi_ls")
        )
        raise SystemExit(print_wait_result(raw, output_json))
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise SystemExit(1) from exc