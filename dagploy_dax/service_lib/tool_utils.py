from string import Template
from typing import Any, Callable

from pathlib import Path
from hydra import compose
from dagploy_dax.service_lib.utils import load_app_env, load_pulumi_path

PULUMI_YAML_DIR = str(load_pulumi_path())


SlotValue = str | Path | Callable[[dict[str, Any]], str]


def safe_values(values: dict[str, Any]) -> dict[str, str]:
    return {k: "" if v is None else str(v) for k, v in values.items()}


def render_template(content: str, values: dict[str, Any]) -> str:
    return Template(content).safe_substitute(safe_values(values))


def read_file(path: str | Path, *, required: bool = True) -> str:
    path = Path(path)

    if not path.exists():
        if required:
            raise FileNotFoundError(f"File not found: {path}")
        return ""

    return path.read_text()


def resolve_slot(value: SlotValue, merged: dict[str, Any]) -> str:
    """
    Slot value supports:
    - Path: read file content
    - callable: generate dynamic content
    - str: raw text
    """
    if isinstance(value, Path):
        return read_file(value, required=False)

    if callable(value):
        return value(merged)

    return value


def render_script(
    *,
    merged: dict[str, Any],
    template_path: str | Path,
    slots: dict[str, SlotValue] | None = None,
) -> str:
    template = read_file(template_path, required=True)

    for placeholder, value in (slots or {}).items():
        template = template.replace(
            placeholder,
            resolve_slot(value, merged),
        )

    return render_template(template, merged)


def join_scripts(*paths: str | Path) -> str:
    parts: list[str] = []

    for path in paths:
        path = Path(path)
        content = read_file(path, required=True).rstrip()

        parts.append(
            f"""
# === BEGIN SCRIPT: {path.name} ===
{content}
# === END SCRIPT: {path.name} ===
""".strip()
        )

    return "\n\n".join(parts)



def read_required_file(path: Path) -> str:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    return path.read_text()
