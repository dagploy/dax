from __future__ import annotations
import uuid

from typing import Any

import uvicorn
from fastapi import FastAPI

from dagploy_dax.manager.contracts import RunCapabilityInput
from dagploy_dax.manager.task import dax_workflow
from dagploy_dax.manager.utils import get_api_host, get_api_port
from fastapi.responses import PlainTextResponse

app = FastAPI(title="DAX API")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/submit")
async def submit(req: RunCapabilityInput):
    if not req.job_id:
        req.job_id = str(uuid.uuid4())

    ref = await dax_workflow.aio_run_no_wait(req)

    return {
        "status": "SUBMITTED",
        "workflow": "dax-workflow",
        "handler": req.handler,
        "job_id": req.job_id,
        "ref": repr(ref),
        "workflow_run_id": ref.workflow_run_id,
    }

@app.post("/submit-wait")
async def submit_and_wait(req: RunCapabilityInput):
    if not req.job_id:
        req.job_id = str(uuid.uuid4())

    result = await dax_workflow.aio_run(req)

    if isinstance(result, dict) and result.get("output"):
        return PlainTextResponse(result["output"])

    return result


def main() -> None:
    uvicorn.run(
        "dagploy_dax.manager.api_server:app",
        host=get_api_host(),
        port=get_api_port(),
        reload=False,
    )


if __name__ == "__main__":
    main()