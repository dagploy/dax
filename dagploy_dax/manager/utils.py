from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import traceback

import os
from pathlib import Path

from dotenv import load_dotenv


def make_json_safe(value: Any) -> Any:
    
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Mapping):
        return {str(k): make_json_safe(v) for k, v in value.items()}

    if isinstance(value, tuple):
        return [make_json_safe(v) for v in value]

    if isinstance(value, list):
        return [make_json_safe(v) for v in value]

    if isinstance(value, set):
        return [make_json_safe(v) for v in value]

    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def emit(ctx, message) -> None:
    if message is None:
        return

    text = str(message).rstrip("\r\n")

    if not text.strip():
        return

    ctx.log(text)


def format_exception_without_wrapper(exc: BaseException) -> str:
    tb_exc = traceback.TracebackException.from_exception(exc)

    frames = [frame for frame in tb_exc.stack if frame.name != "wrapper"]

    if not frames:
        frames = list(tb_exc.stack)

    formatted_stack = "".join(traceback.StackSummary.from_list(frames).format())
    formatted_exception = "".join(tb_exc.format_exception_only())

    return f"{formatted_stack}{formatted_exception}"


def find_env_file(required: bool = False) -> Path | None:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
        Path.home() / ".dagploy" / ".env",
    ]

    for path in candidates:
        if path.exists():
            return path

    if required:
        checked = "\n".join(str(path) for path in candidates)
        raise FileNotFoundError(f".env file not found. Checked:\n{checked}")

    return None


def load_app_env(required: bool = False) -> dict[str, str]:
    env_path = find_env_file(required=required)

    if env_path is not None:
        load_dotenv(env_path)

    return dict(os.environ)

def get_worker_name() -> str:
    env = load_app_env()
    return env.get("DAX_HATCHET_WORKER_NAME", "dax-worker")


def get_worker_slots() -> int:
    env = load_app_env()
    return int(env.get("DAX_HATCHET_WORKER_SLOTS", "5"))


def get_api_host() -> str:
    env = load_app_env()
    return env.get("SERVER_HOST", "0.0.0.0")


def get_api_port() -> int:
    env = load_app_env()
    return int(env.get("SERVER_PORT", "8001"))