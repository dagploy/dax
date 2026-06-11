from __future__ import annotations

import json

import click

from dax.cli import add_common_submit_options, resolve_stack_name, submit_capability, load_json_file, load_json_object

def register(run: click.Group) -> None:
    run.add_command(download_hf)
    run.add_command(download_hf_multi)


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


@click.command(
    "download_hf",
    help="Cache a single HF model/dataset into disk image.",
    epilog="""\b
Eg: 
dax run download_hf meta-llama/Llama-3.1-8B-Instruct --image-size 80
dax run download_hf https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct --image-size 80
dax run download_hf HuggingFaceFW/fineweb --repo-type dataset --images fineweb --image-size 30
""",
)
@click.argument("repo", required=True)
@click.option("--stack-name", default="", help="DAX stack name. Auto-generated if empty.")
@click.option(
    "--repo-type",
    type=click.Choice(["model", "dataset"]),
    default="model",
    show_default=True,
    help="Hugging Face repo type.",
)
@click.option("--images", default="", help="Image names. Auto-generated from repo if empty.")
@click.option("--image-size", default="", help="Image disk size in GB, for example: 100.")
@click.option("--branch", default="", help="Hugging Face repo branch/revision.")
@click.option("--config-json", default=None, help="Inline JSON object for params.config.")
@click.option("--config-file", default=None, help="JSON file for params.config.")
@add_common_submit_options
@click.pass_context
def download_hf(
    ctx: click.Context,
    repo: str,
    stack_name: str,
    repo_type: str,
    images: str,
    image_size: str,
    branch: str,
    config_json: str | None,
    config_file: str | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    if config_json and config_file:
        raise click.BadParameter("Use only one of --config-json or --config-file.")

    config = (
        load_json_object(config_json, option_name="--config-json")
        if config_json
        else load_json_file(config_file, option_name="--config-file")
    )

    # Normalize repo format
    if repo:
        repo = infer_image_name_from_repo(repo)

    print(f"infer repo {repo} from input")

    stack_name = resolve_stack_name(stack_name, service="download-hf")

    submit_capability(
        client=ctx.obj["client"],
        handler="download_hf",
        params={
            "repo": repo,
            "repo_type": repo_type,
            "images": parse_list_or_string(images),
            "image_size": image_size or None,
            "branch": branch or None,
            "stack_name": stack_name,
            "config": config,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command(
    "download_hf_multi",
    help="Cache multiple HF model/dataset into disk image.",
    epilog="""\b
Eg: 
dax run download_hf_multi openai/gpt-oss-20b,Qwen/Qwen3-0.6B --images models-openai-qwen --image-size 100
dax run download_hf_multi --repo-urls openai/gpt-oss-20b,Qwen/Qwen3-0.6B --images models-openai-qwen --image-size 100
""",
)
@click.argument("repo_urls_arg", required=False)
@click.option("--stack-name", default="", help="DAX stack name. Auto-generated if empty.")
@click.option("--repo-urls", default="", help="HF repo URLs. String, comma-separated, or JSON list.")
@click.option("--images", required=True, help="Image names. String, comma-separated, or JSON list.")
@click.option("--image-size", default="", help="Image disk size in GB, for example: 100.")
@click.option("--config-json", default=None, help="Inline JSON object for params.config.")
@click.option("--config-file", default=None, help="JSON file for params.config.")
@add_common_submit_options
@click.pass_context
def download_hf_multi(
    ctx: click.Context,
    repo_urls_arg: str | None,
    stack_name: str,
    repo_urls: str,
    images: str,
    image_size: str,
    config_json: str | None,
    config_file: str | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    repo_urls_value = repo_urls or repo_urls_arg

    if not repo_urls_value:
        raise click.UsageError("Missing Hugging Face repo URLs")

    if config_json and config_file:
        raise click.BadParameter("Use only one of --config-json or --config-file.")

    config = (
        load_json_object(config_json, option_name="--config-json")
        if config_json
        else load_json_file(config_file, option_name="--config-file")
    )

    stack_name = resolve_stack_name(stack_name, service="download-hf-multi")

    submit_capability(
        client=ctx.obj["client"],
        handler="download_hf_multi",
        params={
            "repo_urls": parse_list_or_string(repo_urls_value),
            "images": parse_list_or_string(images),
            "image_size": image_size or None,
            "stack_name": stack_name,
            "config": config,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )


def parse_list_or_string(value: str) -> list[str] | str | None:
    if not value:
        return None

    value = value.strip()

    if value.startswith("["):
        parsed = json.loads(value)

        if not isinstance(parsed, list):
            raise click.BadParameter("JSON value must be a list.")

        return parsed

    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]

    return value