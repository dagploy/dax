# manager/tool_script.py

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any


def _safe_values(values: dict[str, Any]) -> dict[str, str]:
    return {k: "" if v is None else str(v) for k, v in values.items()}


def _render(content: str, values: dict[str, Any]) -> str:
    return Template(content).safe_substitute(_safe_values(values))


def build_tool_script(
    tools_dir: str | Path,
    values: dict[str, Any],
) -> str:
    """
    Build startup block from:
      - startup.sh
      - taskfile.sh

    taskfile.sh is rendered first, then exposed to startup.sh as:

      ${taskfile_content}
    """
    base = Path(tools_dir).resolve()

    startup_path = base / "startup.sh"
    taskfile_path = base / "taskfile.sh"

    if not startup_path.exists():
        raise FileNotFoundError(f"Missing capability startup.sh: {startup_path}")

    taskfile_content = ""
    if taskfile_path.exists():
        taskfile_content = _render(
            taskfile_path.read_text(encoding="utf-8"),
            values,
        )

    startup_values = {
        **values,
        "taskfile_content": taskfile_content,
    }

    return _render(
        startup_path.read_text(encoding="utf-8"),
        startup_values,
    ).strip()