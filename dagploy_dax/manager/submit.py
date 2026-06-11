from __future__ import annotations

import json
from typing import Any

import typer

from dagploy_dax.manager.contracts import RunCapabilityInput
from dagploy_dax.manager.task import run_capability

app = typer.Typer(no_args_is_help=True)


@app.command("run")
def run_handler(
    handler: str = typer.Argument(..., help="Registered handler name, e.g. create_vm"),
    json_params: str = typer.Option("{}", "--json", help="JSON params passed to the handler"),
    wait: bool = typer.Option(False, "--wait", help="Wait for result"),
) -> None:
    params: dict[str, Any] = json.loads(json_params)

    payload = RunCapabilityInput(
        handler=handler,
        params=params,
    )

    if wait:
        result = run_capability.run(input=payload)
        typer.echo(result)
    else:
        ref = run_capability.run(input=payload, wait_for_result=False)
        typer.echo(f"submitted run_id={ref.run_id}")


if __name__ == "__main__":
    app()