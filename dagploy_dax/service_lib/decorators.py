from functools import wraps
from typing import Any, Callable
from dagploy_dax.service_lib.utils import check_config_file


def auto_execute_pipeline(
    *,
    execute_fn: Callable,
    deploy_fn: Any,
    provisioning_function_dir: str,
    launch_config: Any,
):
    """
    Decorator that runs the execution pipeline after user customization.

    The decorated function must return a tuple:
      (merged_config, cfg, job_id, update_state)
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)

            if not isinstance(result, tuple) or len(result) != 4:
                raise ValueError(
                    f"Function '{func.__name__}' must return "
                    f"(merged, cfg, job_id, update_state)"
                )

            merged, cfg, job_id, update_state = result
            on_output = kwargs.get("on_output", print)

            # --- 1️⃣ Validate configuration file ---
            check_config_file(merged)

            # --- 2️⃣ Prepare working directory ---
            stack_name = merged.get("stackName", "default")

            # --- 4️⃣ Execute pipeline with full argument set ---
            on_output(f"🚀 Starting execution for stack: {stack_name}")

            return execute_fn(
                cfg=cfg,
                merged=merged,
                deploy_fn=deploy_fn,
                provisioning_function_dir=provisioning_function_dir,
                launch_config=launch_config,
                on_output=on_output,
                job_id=job_id,
                update_state=update_state,
            )

        return wrapper

    return decorator