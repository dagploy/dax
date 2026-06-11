from __future__ import annotations

import importlib
import json
import pkgutil
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

import click
import petname
from rich.console import Console
from rich.panel import Panel

from dax.client import DaxClient, DaxSubmitResult

console = Console()
BAD_WORDS = {"pig", "maggot"}
CTX = {"help_option_names": ["-h", "--help"]}


class NaturalOrderGroup(click.Group):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.commands = OrderedDict()

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(self.commands)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows: list[tuple[str, str]] = []

        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)

            if cmd is None or cmd.hidden:
                continue

            rows.append((name, cmd.get_short_help_str()))

            if isinstance(cmd, click.Group):
                for sub_name in sorted(cmd.list_commands(ctx)):
                    sub_cmd = cmd.get_command(ctx, sub_name)

                    if sub_cmd is None or sub_cmd.hidden:
                        continue

                    rows.append((f"{name} {sub_name}", sub_cmd.get_short_help_str()))

        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


def generate_safe_petname() -> str:
    while True:
        name = petname.Generate(1).lower()
        if not any(w in name for w in BAD_WORDS):
            return name


def generate_stack_name(service: str, limit: int = 8) -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "", service.lower().replace("_", "-")).strip("-") or "dax"
    return f"{'-'.join(cleaned.split('-')[:limit])}-{generate_safe_petname()}"


def resolve_stack_name(stack_name: str | None, *, service: str) -> str:
    if stack_name and stack_name.strip():
        return stack_name.strip()

    name = generate_stack_name(service)
    console.print(f"[yellow]No --stack-name provided.[/yellow] Generated: [bold]{name}[/bold]")
    return name


def load_json_object(value: str | None, *, option_name: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"Invalid JSON for {option_name}: {exc}") from exc
    if not isinstance(data, dict):
        raise click.BadParameter(f"{option_name} must be a JSON object.")
    return data


def load_json_file(path: str | None, *, option_name: str) -> dict[str, Any]:
    if not path:
        return {}

    file = Path(path).expanduser().resolve()
    if not file.exists():
        raise click.BadParameter(f"File not found for {option_name}: {file}")

    return load_json_object(file.read_text(), option_name=option_name)


def print_submit_result(result: DaxSubmitResult, output_json: bool) -> int:
    if output_json:
        console.print_json(json.dumps(result.raw, default=str))
        return 0

    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"[bold green]{result.status}[/bold green]",
                    f"workflow: [bold]{result.workflow}[/bold]",
                    f"handler: [bold]{result.handler}[/bold]",
                    f"job_id: [bold]{result.job_id}[/bold]",
                    f"workflow_run_id: [bold]{result.workflow_run_id}[/bold]",
                ]
            ),
            title="DAX Submitted",
            border_style="green",
        )
    )
    return 0


def submit_capability(
    *,
    client: DaxClient,
    handler: str,
    params: dict[str, Any],
    output_json: bool,
    use_httpx: bool,
) -> None:
    try:
        result = (
            client.submit(handler=handler, params=params)
            if use_httpx
            else client.submit_with_curl(handler=handler, params=params)
        )
        raise SystemExit(print_submit_result(result, output_json))
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise SystemExit(1) from exc


def add_common_submit_options(fn: Any) -> Any:
    for option in (
        click.option("--use-httpx", is_flag=True, default=False, help="Use Python HTTP client."),
        click.option("--json", "output_json", is_flag=True, default=False, help="Print JSON."),
    ):
        fn = option(fn)
    return fn


@click.group(
    cls=NaturalOrderGroup,
    context_settings=CTX,
    no_args_is_help=True,
)
@click.option(
    "--server",
    default="http://localhost:8001",
    show_default=True,
    envvar="DAX_SERVER_URL",
    help="DAX API server URL.",
)
@click.pass_context
def cli(ctx: click.Context, server: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["client"] = DaxClient(server_url=server)


@cli.group(cls=NaturalOrderGroup)
def run() -> None:
    """Run DAX infrastructure capabilities."""


def load_command_modules() -> None:
    pkg = importlib.import_module("dax.commands")

    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue

        module = importlib.import_module(f"dax.commands.{info.name}")
        register = getattr(module, "register", None)

        if callable(register):
            register(run)


load_command_modules()