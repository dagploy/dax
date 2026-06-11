from __future__ import annotations

import click

from dax.cli import add_common_submit_options, resolve_stack_name, submit_capability, load_json_object, load_json_file

def register(run: click.Group) -> None:
    run.add_command(launch_vm_gpu)


@click.command("launch_vm_gpu")
@click.option("--stack-name", default="", help="DAX stack name. Auto-generated if empty.")
@click.option("--config-json", default=None, help="Inline JSON object for params.config.")
@click.option("--config-file", default=None, help="JSON file for params.config.")
@add_common_submit_options
@click.pass_context
def launch_vm_gpu(
    ctx: click.Context,
    stack_name: str,
    config_json: str | None,
    config_file: str | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    """
    Example usage:
        dax run launch_vm_gpu --stack-name gpu
    """
    stack_name = resolve_stack_name(stack_name, service="vm_gpu")

    if config_json and config_file:
        raise click.BadParameter("Use only one of --config-json or --config-file.")

    config = (
        load_json_object(config_json, option_name="--config-json")
        if config_json
        else load_json_file(config_file, option_name="--config-file")
    )

    submit_capability(
        client=ctx.obj["client"],
        handler="launch_vm_gpu",
        params={"stack_name": stack_name, "config": config},
        output_json=output_json,
        use_httpx=use_httpx,
    )