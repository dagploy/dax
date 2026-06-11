from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunCapabilityInput(BaseModel):
    handler: str
    params: dict[str, Any] = Field(default_factory=dict)

    job_id: str | None = None
    request_id: str | None = None
    user_id: str | None = None

    cfg: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)


class RunCapabilityOutput(BaseModel):
    status: str
    handler: str
    job_id: str | None = None
    output: Any = None