from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import tools

Handler = Callable[..., Any]
Registry = dict[str, Handler]


SKIP_MODULE_SUFFIXES = {
    ".common",
    ".loader",
    ".__main__",
}


def should_skip_module(module_name: str) -> bool:
    return any(module_name.endswith(suffix) for suffix in SKIP_MODULE_SUFFIXES)


def iter_tool_module_names() -> list[str]:
    module_names: list[str] = []

    for module_info in pkgutil.walk_packages(
        tools.__path__,
        prefix="tools.",
    ):
        if module_info.ispkg:
            continue

        module_name = module_info.name

        if should_skip_module(module_name):
            continue

        module_names.append(module_name)

    return sorted(module_names)


def load_module_handlers(module_name: str) -> Registry:
    module = importlib.import_module(module_name)

    register = getattr(module, "register", None)

    if register is None:
        return {}

    if not callable(register):
        raise TypeError(f"{module_name}.register is not callable")

    data = register()

    if not isinstance(data, dict):
        raise TypeError(f"{module_name}.register() must return dict")

    handlers = data.get("handlers")

    if handlers is None:
        return {}

    if not isinstance(handlers, dict):
        raise TypeError(f"{module_name}.register()['handlers'] must be dict")

    result: Registry = {}

    for handler_name, handler_fn in handlers.items():
        if not isinstance(handler_name, str):
            raise TypeError(
                f"{module_name}.register()['handlers'] contains non-string key"
            )

        if not callable(handler_fn):
            raise TypeError(
                f"{module_name}.register()['handlers'][{handler_name!r}] "
                "is not callable"
            )

        result[handler_name] = handler_fn

    return result


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    registry: Registry = {}

    for module_name in iter_tool_module_names():
        handlers = load_module_handlers(module_name)

        for handler_name, handler_fn in handlers.items():
            if handler_name in registry:
                old_fn = registry[handler_name]

                raise RuntimeError(
                    f"Duplicate handler registered: {handler_name}\n"
                    f"  existing: {old_fn.__module__}.{old_fn.__name__}\n"
                    f"  new:      {handler_fn.__module__}.{handler_fn.__name__}"
                )

            registry[handler_name] = handler_fn

    return registry


def reload_registry() -> Registry:
    load_registry.cache_clear()
    return load_registry()


def get_handler(name: str) -> Handler | None:
    return load_registry().get(name)


def require_handler(name: str) -> Handler:
    handler = get_handler(name)

    if handler is None:
        available = ", ".join(list_handlers())

        raise RuntimeError(
            f"Handler not found: {name}. Available handlers: {available}"
        )

    return handler


def list_handlers() -> list[str]:
    return sorted(load_registry())