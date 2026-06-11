from __future__ import annotations

import inspect
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from hatchet_sdk import Context
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf

from dagploy_dax.manager.client import get_hatchet
from dagploy_dax.manager.contracts import RunCapabilityInput, RunCapabilityOutput
from dagploy_dax.manager.registry import load_registry
from dagploy_dax.manager.utils import (
    format_exception_without_wrapper,
    load_app_env,
    make_json_safe,
)


HANDLERS = load_registry()

dax_workflow = get_hatchet().workflow(
    name="dax",
    input_validator=RunCapabilityInput,
)


FRAMEWORK_ONLY_KEYS = {
    "handler",
    "request_id",
    "params",
}


def load_runtime_config() -> dict[str, Any]:
    env = load_app_env()
    project_path = env.get("PROJECT_PATH", "")

    if not project_path:
        raise ValueError("PROJECT_PATH is missing from .env")

    config_path = (Path(project_path) / "config").resolve()

    if not config_path.exists():
        raise FileNotFoundError(f"Config folder not found: {config_path}")

    with initialize_config_dir(config_dir=str(config_path), version_base=None):
        raw_cfg: DictConfig = compose(config_name="config")

    return {
        "cfg": OmegaConf.to_container(raw_cfg.env, resolve=True),
        "resources": OmegaConf.to_container(raw_cfg.resources, resolve=True),
    }


def get_context_run_id(ctx: Context) -> str | None:
    for attr in (
        "workflow_run_id",
        "run_id",
        "task_run_id",
        "external_id",
    ):
        value = getattr(ctx, attr, None)

        if value:
            return str(value)

    return None


def get_job_id(req: RunCapabilityInput, ctx: Context) -> str:
    return (
        req.job_id
        or req.request_id
        or get_context_run_id(ctx)
        or str(uuid.uuid4())
    )


def fallback_print(message: str) -> None:
    try:
        print(message, flush=True)
    except BrokenPipeError:
        pass
    except OSError:
        pass


def log_line(ctx: Context, message: Any) -> None:
    """
    Single logging path.

    Do not call both print() and ctx.log() for the same message.
    Hatchet already prints ctx.log() output.
    """
    if message is None:
        return

    text = str(message).rstrip("\r\n")

    if not text.strip():
        return

    try:
        ctx.log(text)
    except Exception:
        fallback_print(text)


def make_on_output(ctx: Context):
    def on_output(line: Any) -> None:
        log_line(ctx, line)

    return on_output


def input_to_dict(req: RunCapabilityInput) -> dict[str, Any]:
    """
    Supports Pydantic v1 and v2.
    """
    if hasattr(req, "model_dump"):
        return req.model_dump(exclude_none=True)

    if hasattr(req, "dict"):
        return req.dict(exclude_none=True)

    return {}


def get_handler_signature(handler: Any) -> inspect.Signature:
    return inspect.signature(handler)


def handler_accepts_kwargs(handler: Any) -> bool:
    signature = get_handler_signature(handler)

    return any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )


def handler_accepts_param(handler: Any, name: str) -> bool:
    if handler_accepts_kwargs(handler):
        return True

    signature = get_handler_signature(handler)

    return name in signature.parameters


def filter_handler_params(
    *,
    handler: Any,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Pass only arguments the handler can accept.

    Example:
      pulumi_ls(service, on_output, job_id)
      will not receive handler/cfg/resources.

    Example:
      create_vm_gpu(..., cfg, resources, **kwargs)
      will receive cfg/resources and other allowed runtime args.
    """
    clean_params = {
        key: value
        for key, value in params.items()
        if key not in FRAMEWORK_ONLY_KEYS
    }

    if handler_accepts_kwargs(handler):
        return clean_params

    signature = get_handler_signature(handler)
    accepted_keys = set(signature.parameters)

    return {
        key: value
        for key, value in clean_params.items()
        if key in accepted_keys
    }


def build_handler_params(
    *,
    req: RunCapabilityInput,
    handler: Any,
    job_id: str,
    ctx: Context,
) -> dict[str, Any]:
    req_dict = input_to_dict(req)

    params: dict[str, Any] = dict(req.params or {})

    for key, value in req_dict.items():
        if key in FRAMEWORK_ONLY_KEYS:
            continue

        if key in {"cfg", "resources"}:
            continue

        params.setdefault(key, value)

    params["job_id"] = job_id
    params["on_output"] = make_on_output(ctx)

    needs_cfg = handler_accepts_param(handler, "cfg")
    needs_resources = handler_accepts_param(handler, "resources")

    if needs_cfg or needs_resources:
        runtime_config = load_runtime_config()

        if needs_cfg:
            params["cfg"] = (
                req.cfg
                or params.get("cfg")
                or runtime_config["cfg"]
            )

        if needs_resources:
            params["resources"] = (
                req.resources
                or params.get("resources")
                or runtime_config["resources"]
            )

    return filter_handler_params(
        handler=handler,
        params=params,
    )


def normalize_output(output: Any, handler_name: str) -> dict[str, Any]:
    if output is None:
        return {
            "status": "success",
            "message": "Capability finished without returning output",
            "handler": handler_name,
        }

    if isinstance(output, dict):
        return output

    return {
        "status": "success",
        "handler": handler_name,
        "result": output,
    }


@dax_workflow.task(
    name="tool",
    execution_timeout=timedelta(hours=12),
    schedule_timeout=timedelta(minutes=30),
    retries=0,
)
def run_capability(
    input: RunCapabilityInput,
    ctx: Context,
) -> RunCapabilityOutput:
    """
    Keep this task synchronous.

    Most handlers in this system are blocking:
    - Pulumi automation
    - Docker commands
    - GCloud commands
    - subprocess calls
    - file/network I/O

    If this is async and calls blocking handlers directly, it can block
    Hatchet's worker event loop and cause:

      THE TIME TO START THE TASK RUN IS TOO LONG,
      THE EVENT LOOP MAY BE BLOCKED
    """
    req = input
    handler_name = req.handler
    handler = HANDLERS.get(handler_name)

    if handler is None:
        available = sorted(HANDLERS)
        raise ValueError(
            f"Unknown handler '{handler_name}'. Available handlers: {available}"
        )

    if inspect.iscoroutinefunction(handler):
        raise TypeError(
            f"Handler '{handler_name}' is async, but run_capability is sync. "
            "Use a synchronous handler for blocking infra jobs, or create a "
            "separate async Hatchet task that awaits it safely."
        )

    job_id = get_job_id(req, ctx)

    log_line(ctx, f"🟡 Starting handler={handler_name} job_id={job_id}")

    try:
        params = build_handler_params(
            req=req,
            handler=handler,
            job_id=job_id,
            ctx=ctx,
        )

        log_line(
            ctx,
            f"🟡 Calling handler={handler_name} with params={sorted(params)}",
        )

        output = handler(**params)
        output = normalize_output(output, handler_name)
        output = make_json_safe(output)

        log_line(ctx, f"✅ Finished handler={handler_name} job_id={job_id}")

        return RunCapabilityOutput(
            status="SUCCEEDED",
            handler=handler_name,
            job_id=job_id,
            output=output,
        )

    except TimeoutError:
        log_line(ctx, f"⏱️ Job {job_id} timed out.")
        raise

    except Exception as exc:
        formatted = format_exception_without_wrapper(exc)

        log_line(
            ctx,
            f"❌ Failed handler={handler_name} job_id={job_id}\n{formatted}",
        )

        raise RuntimeError(formatted) from exc