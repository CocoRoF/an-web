"""Deterministic replay from saved traces."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session


# ── Replay step ───────────────────────────────────────────────────────────────

@dataclass
class ReplayStep:
    """A single action in a replay trace."""
    step_id: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    expected_status: str | None = None   # "ok" | "failed" — if set, assert on replay
    expected_url: str | None = None
    wait_ms: float = 0.0                 # artificial delay before this step (ms)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "step_id": self.step_id,
            "action": self.action,
            "params": self.params,
        }
        if self.expected_status is not None:
            d["expected_status"] = self.expected_status
        if self.expected_url is not None:
            d["expected_url"] = self.expected_url
        if self.wait_ms:
            d["wait_ms"] = self.wait_ms
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReplayStep":
        # Accept two formats:
        #   serialised (to_dict): has a nested "params" key
        #   flat (raw tool call): params are top-level keys beside reserved ones
        _reserved = {"step_id", "action", "tool", "expected_status",
                     "expected_url", "wait_ms", "params"}
        if "params" in d:
            params = dict(d["params"])
        else:
            params = {k: v for k, v in d.items() if k not in _reserved}
        return cls(
            step_id=d.get("step_id", f"step-{uuid.uuid4().hex[:8]}"),
            action=d.get("action") or d.get("tool", ""),
            params=params,
            expected_status=d.get("expected_status"),
            expected_url=d.get("expected_url"),
            wait_ms=float(d.get("wait_ms", 0)),
        )


# ── Replay trace ──────────────────────────────────────────────────────────────

@dataclass
class ReplayTrace:
    """Ordered sequence of steps to replay."""
    trace_id: str
    session_id: str
    steps: list[ReplayStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    # ── builder ───────────────────────────────────────────────────────────────

    @classmethod
    def new(cls, session_id: str = "", **metadata: Any) -> "ReplayTrace":
        return cls(
            trace_id=f"trace-{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            metadata=dict(metadata),
        )

    def add_step(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        expected_status: str | None = None,
        expected_url: str | None = None,
        wait_ms: float = 0.0,
    ) -> ReplayStep:
        step = ReplayStep(
            step_id=f"step-{uuid.uuid4().hex[:8]}",
            action=action,
            params=params or {},
            expected_status=expected_status,
            expected_url=expected_url,
            wait_ms=wait_ms,
        )
        self.steps.append(step)
        return step

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "steps": [s.to_dict() for s in self.steps],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReplayTrace":
        return cls(
            trace_id=d.get("trace_id", f"trace-{uuid.uuid4().hex[:12]}"),
            session_id=d.get("session_id", ""),
            steps=[ReplayStep.from_dict(s) for s in d.get("steps", [])],
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", time.time()),
        )

    @classmethod
    def from_json(cls, s: str) -> "ReplayTrace":
        return cls.from_dict(json.loads(s))


# ── Replay result ─────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    step_id: str
    action: str
    status: str                     # "ok" | "failed" | "assertion_failed" | "error"
    result: dict[str, Any]
    assertion_error: str | None = None
    duration_ms: float = 0.0


@dataclass
class ReplayResult:
    trace_id: str
    session_id: str
    steps: list[StepResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def succeeded(self) -> bool:
        return all(s.status == "ok" for s in self.steps)

    @property
    def failed_steps(self) -> list[StepResult]:
        return [s for s in self.steps if s.status != "ok"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "succeeded": self.succeeded,
            "total_duration_ms": self.total_duration_ms,
            "steps": [
                {
                    "step_id": s.step_id,
                    "action": s.action,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "assertion_error": s.assertion_error,
                }
                for s in self.steps
            ],
        }


# ── Replay engine ─────────────────────────────────────────────────────────────

class ReplayEngine:
    """
    Replays a ReplayTrace (or a raw action_log list) deterministically.

    Assertions:
      - ``expected_status``: checks ``result["status"]``.
      - ``expected_url``: checks ``session.current_url`` after the step.

    On assertion failure the step is marked ``assertion_failed`` but
    execution continues so the caller sees all assertion results.
    """

    async def replay(
        self,
        session: "Session",
        action_log: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Backward-compatible: replay a raw list, return list of result dicts."""
        trace = ReplayTrace.new(session_id=getattr(session, "session_id", ""))
        for entry in action_log:
            trace.steps.append(ReplayStep.from_dict(entry))
        result = await self.replay_trace(session, trace)
        return [s.result for s in result.steps]

    async def replay_trace(
        self,
        session: "Session",
        trace: ReplayTrace,
    ) -> ReplayResult:
        """Full replay with assertions. Returns ReplayResult."""
        replay_result = ReplayResult(
            trace_id=trace.trace_id,
            session_id=trace.session_id,
        )
        total_start = time.perf_counter()

        for step in trace.steps:
            step_result = await self._execute_step(session, step)
            replay_result.steps.append(step_result)

        replay_result.total_duration_ms = (time.perf_counter() - total_start) * 1000
        return replay_result

    async def _execute_step(
        self,
        session: "Session",
        step: ReplayStep,
    ) -> StepResult:
        import asyncio

        if step.wait_ms > 0:
            await asyncio.sleep(step.wait_ms / 1000)

        t0 = time.perf_counter()
        raw: Any = None
        try:
            raw = await session.act({"tool": step.action, **step.params})
            result_dict = raw if isinstance(raw, dict) else {"result": str(raw)}
        except Exception as exc:
            duration = (time.perf_counter() - t0) * 1000
            return StepResult(
                step_id=step.step_id,
                action=step.action,
                status="error",
                result={"error": str(exc)},
                duration_ms=duration,
            )

        duration = (time.perf_counter() - t0) * 1000
        status = "ok"
        assertion_error: str | None = None

        # Assert expected_status
        if step.expected_status is not None:
            actual_status = result_dict.get("status", "")
            if actual_status != step.expected_status:
                status = "assertion_failed"
                assertion_error = (
                    f"expected status={step.expected_status!r}, "
                    f"got {actual_status!r}"
                )

        # Assert expected_url
        if step.expected_url is not None and status == "ok":
            actual_url = getattr(session, "current_url", None) or ""
            if actual_url != step.expected_url:
                status = "assertion_failed"
                assertion_error = (
                    f"expected url={step.expected_url!r}, got {actual_url!r}"
                )

        return StepResult(
            step_id=step.step_id,
            action=step.action,
            status=status,
            result=result_dict,
            assertion_error=assertion_error,
            duration_ms=duration,
        )
