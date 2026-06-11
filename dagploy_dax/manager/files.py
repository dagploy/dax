# manager/files.py

from __future__ import annotations

import base64
from pathlib import Path
from string import Template
from typing import Any


def render_text(text: str, values: dict[str, Any]) -> str:
    """
    Replace ${variable} in startup.sh/taskfile.sh/files.

    Example:
      ${docker_image}
      ${command}
      ${config_text}
    """
    safe_values = {
        key: "" if value is None else str(value)
        for key, value in values.items()
    }
    return Template(text).safe_substitute(**safe_values)


def read_rendered_file(path: Path, values: dict[str, Any]) -> str:
    return render_text(path.read_text(), values)


def write_file_block(path: str, content: str, mode: str = "0644") -> str:
    """
    Generate shell that writes a file on the VM.

    Use base64 so user scripts can safely contain quotes, YAML, JSON,
    $VARIABLE, and heredocs.
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    return f"""
mkdir -p "$(dirname "{path}")"
base64 -d > "{path}" <<'DAX_FILE_EOF'
{encoded}
DAX_FILE_EOF
chmod {mode} "{path}"
""".strip()

