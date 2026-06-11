from __future__ import annotations

import click

from dax.cli import add_common_submit_options, resolve_stack_name, submit_capability, load_json_file, load_json_object

def register(run: click.Group) -> None:
    run.add_command(create_vm_inference)



def infer_image_name_from_repo(repo: str) -> str:
    raw = repo.strip().rstrip("/")

    if not raw:
        raise click.BadParameter("Repo cannot be empty.")

    # Handles:
    # google/gemma-4-E4B-it
    # https://huggingface.co/google/gemma-4-E4B-it
    # https://huggingface.co/google/gemma-4-E4B-it/tree/main
    # https://huggingface.co/datasets/google/gemma-4-E4B-it
    if "huggingface.co/" in raw:
        raw = raw.split("huggingface.co/", 1)[1]

    raw = raw.split("?", 1)[0].split("#", 1)[0]

    parts = [p for p in raw.split("/") if p]

    # Optional HF route prefix
    if parts and parts[0] in {"models", "datasets", "spaces"}:
        parts = parts[1:]

    if len(parts) < 2:
        raise click.BadParameter(f"Cannot infer image name from repo: {repo}")

    owner, name = parts[0], parts[1]

    return f"{owner}/{name}"


@click.command("create_vm_inference")
@click.option("--stack-name", default="", help="DAX stack name. Auto-generated if empty.")
@click.option("--model", default="", help="HF model URL or disk image name.")
@click.option("--taskfile", default="", help="Taskfile content/path/string.")
@click.option("--config-json", default=None, help="Inline JSON object for params.config.")
@click.option("--config-file", default=None, help="JSON file for params.config.")
@add_common_submit_options
@click.pass_context
def create_vm_inference(
    ctx: click.Context,
    stack_name: str,
    model: str,
    taskfile: str,
    config_json: str | None,
    config_file: str | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    """
    Run VLLM inference from cached disk HF model
    """
    stack_name = resolve_stack_name(stack_name, service="inference")

    if config_json and config_file:
        raise click.BadParameter("Use only one of --config-json or --config-file.")

    # rename model format
    if model:
        model = infer_image_name_from_repo(model)

    config = (
        load_json_object(config_json, option_name="--config-json")
        if config_json
        else load_json_file(config_file, option_name="--config-file")
    )


    submit_capability(
        client=ctx.obj["client"],
        handler="create_vm_inference",
        params={
            "model": model,
            "stack_name": stack_name,
            "taskfile": taskfile,
            "config": config,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )