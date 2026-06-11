from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class DaxSubmitResult:
    status: str
    workflow: str
    handler: str
    job_id: str
    workflow_run_id: str
    raw: dict[str, Any]


class DaxClient:
    def __init__(self, server_url: str) -> None:
        self.server_url = server_url.rstrip("/")

    def _payload(self, handler: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"handler": handler, **({"params": params} if params else {})}

    def _post(self, path: str, payload: dict[str, Any], timeout: float | None) -> dict[str, Any]:
        res = httpx.post(f"{self.server_url}{path}", json=payload, timeout=timeout)
        res.raise_for_status()
        return res.json()

    def _curl(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        out = subprocess.run(
            [
                "curl", "-sS", "-X", "POST", f"{self.server_url}{path}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps(payload),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(out.stdout)

    def _submit_result(self, raw: dict[str, Any], handler: str) -> DaxSubmitResult:
        return DaxSubmitResult(
            status=raw.get("status", ""),
            workflow=raw.get("workflow", ""),
            handler=raw.get("handler", handler),
            job_id=raw.get("job_id", ""),
            workflow_run_id=raw.get("workflow_run_id", ""),
            raw=raw,
        )

    def submit(self, *, handler: str, params: dict[str, Any]) -> DaxSubmitResult:
        raw = self._post("/submit", self._payload(handler, params), timeout=30)
        return self._submit_result(raw, handler)

    def submit_with_curl(self, *, handler: str, params: dict[str, Any]) -> DaxSubmitResult:
        raw = self._curl("/submit", self._payload(handler, params))
        return self._submit_result(raw, handler)

    def submit_wait(
        self,
        *,
        handler: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._post("/submit-wait", self._payload(handler, params), timeout=None)

    def submit_wait_with_curl(
        self,
        *,
        handler: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._curl("/submit-wait", self._payload(handler, params))