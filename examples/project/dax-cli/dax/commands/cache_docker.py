from __future__ import annotations

import json

import click

from dax.cli import add_common_submit_options, resolve_stack_name, submit_capability, load_json_object, load_json_file


def register(run: click.Group) -> None:
    run.add_command(download_docker)
    run.add_command(download_tar_docker)


@click.command(
    "download_docker",
    help="Create docker cache for /var/lib/docker",
    epilog="""\b 
eg: dax run download_docker vllm/vllm-openai:nightly --images vllm-docker-lib --image-size 70""")
@click.argument("docker_images_arg", required=False)
@click.option("--stack-name", default="", help="DAX stack name. Auto-generated if empty.")
@click.option("--docker-images", default="", help="Docker images. String, comma-separated, or JSON list.")
@click.option("--images", default="", help="Output disk image names. String, comma-separated, or JSON list.")
@click.option("--image-size", default="", help="Image disk size in GB, for example: 100.")
@click.option("--zone", default="", help="GCP zone, for example: us-central1-a.")
@click.option("--config-json", default=None, help="Inline JSON object for params.config.")
@click.option("--config-file", default=None, help="JSON file for params.config.")
@add_common_submit_options
@click.pass_context
def download_docker(
    ctx: click.Context,
    docker_images_arg: str | None,
    stack_name: str,
    docker_images: str,
    images: str,
    image_size: str,
    zone: str,
    config_json: str | None,
    config_file: str | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    docker_images_value = docker_images or docker_images_arg

    if not docker_images_value:
        raise click.UsageError("Missing Docker images")

    if config_json and config_file:
        raise click.BadParameter("Use only one of --config-json or --config-file.")
    
    config = (
        load_json_object(config_json, option_name="--config-json")
        if config_json
        else load_json_file(config_file, option_name="--config-file")
    )

    stack_name = resolve_stack_name(stack_name, service="download-docker-image")

    submit_capability(
        client=ctx.obj["client"],
        handler="download_image_docker",
        params={
            "docker_images": parse_list_or_string(docker_images_value),
            "images": parse_list_or_string(images),
            "image_size": image_size or None,
            "stack_name": stack_name,
            "zone": zone or None,
            "config": config,
        },
        output_json=output_json,
        use_httpx=use_httpx,
    )


@click.command(
    "download_tar_docker",
    help="Create docker cache as tar file",
    epilog="""\b 
eg: dax run download_tar_docker vllm/vllm-openai:nightly --images vllm-tar --image-size 70""",
)
@click.argument("docker_images_arg", required=False)
@click.option("--stack-name", default="", help="DAX stack name. Auto-generated if empty.")
@click.option("--docker-images", default="", help="Docker images. String, comma-separated, or JSON list.")
@click.option("--images", default="", help="Output tar/image names. String, comma-separated, or JSON list.")
@click.option("--image-size", default="", help="Image disk size in GB, for example: 100.")
@click.option("--zone", default="", help="GCP zone, for example: us-central1-a.")
@click.option("--config-json", default=None, help="Inline JSON object for params.config.")
@click.option("--config-file", default=None, help="JSON file for params.config.")
@add_common_submit_options
@click.pass_context
def download_tar_docker(
    ctx: click.Context,
    docker_images_arg: str | None,
    stack_name: str,
    docker_images: str,
    images: str,
    image_size: str,
    zone: str,
    config_json: str | None,
    config_file: str | None,
    output_json: bool,
    use_httpx: bool,
) -> None:
    docker_images_value = docker_images or docker_images_arg

    if not docker_images_value:
        raise click.UsageError("Missing Docker images")

    stack_name = resolve_stack_name(stack_name, service="download-docker")

    if config_json and config_file:
        raise click.BadParameter("Use only one of --config-json or --config-file.")
    
    config = (
        load_json_object(config_json, option_name="--config-json")
        if config_json
        else load_json_file(config_file, option_name="--config-file")
    )

    submit_capability(
        client=ctx.obj["client"],
        handler="download_tar_docker",
        params={
            "docker_images": parse_list_or_string(docker_images_value),
            "images": parse_list_or_string(images),
            "image_size": image_size or None,
            "stack_name": stack_name,
            "zone": zone or None,
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