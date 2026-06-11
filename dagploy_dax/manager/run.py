from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from dagploy_dax.manager.contracts import RunCapabilityInput
from dagploy_dax.manager.task import dax_workflow


async def main() -> None:
    """
    Simple CLI to run a capability handler with JSON params. 
    
    Example usage:
        python -m manager.run training_axolotl --json '{"profile": "gcp-gpu-dev", "stack_name": "demo-training", "mode": "train"}' --job-id my-job-123
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("handler", help="Handler name, e.g. debug_echo")
    parser.add_argument("--json", default="{}", help="Handler params as JSON")
    parser.add_argument("--job-id", default=None)

    args = parser.parse_args()

    params: dict[str, Any] = json.loads(args.json)

    payload = RunCapabilityInput(
        handler=args.handler,
        params=params,
        job_id=args.job_id,
    )

    print("Submitting dax-workflow with payload:")
    print(payload.model_dump())

    result = await dax_workflow.aio_run(payload)

    print("Finished running capability:")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())