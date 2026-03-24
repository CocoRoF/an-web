"""Artifact collection — structured evidence for AI debugging and replay."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ── Artifact kind ─────────────────────────────────────────────────────────────

class ArtifactKind(StrEnum):
    DOM_SNAPSHOT       = "dom_snapshot"
    SEMANTIC_SNAPSHOT  = "semantic_snapshot"
    NETWORK_TRACE      = "network_trace"
    JS_EXCEPTION       = "js_exception"
    ACTION_TRACE       = "action_trace"
    POLICY_VIOLATION   = "policy_violation"
    CUSTOM             = "custom"


# ── Artifact dataclass ─────────────────────────────────────────────────────────

@dataclass
class Artifact:
    artifact_id: str
    kind: str          # ArtifactKind value or custom string
    timestamp: float
    session_id: str
    data: dict[str, Any]
    action_id: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── hash ──────────────────────────────────────────────────────────────────

    @property
    def content_hash(self) -> str:
        payload = json.dumps(self.data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "data": self.data,
            "content_hash": self.content_hash,
        }
        if self.action_id is not None:
            d["action_id"] = self.action_id
        if self.url is not None:
            d["url"] = self.url
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Artifact:
        return cls(
            artifact_id=d["artifact_id"],
            kind=d["kind"],
            timestamp=d["timestamp"],
            session_id=d.get("session_id", ""),
            data=d.get("data", {}),
            action_id=d.get("action_id"),
            url=d.get("url"),
            metadata=d.get("metadata", {}),
        )


# ── Payload builders ──────────────────────────────────────────────────────────

def _dom_payload(html: str, url: str) -> dict[str, Any]:
    text = _extract_visible_text(html)
    return {
        "html": html,
        "url": url,
        "char_count": len(html),
        "visible_text_preview": text[:500] if text else "",
    }


def _semantic_payload(semantics: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_type": semantics.get("page_type", "unknown"),
        "title": semantics.get("title", ""),
        "url": semantics.get("url", ""),
        "primary_actions": semantics.get("primary_actions", []),
        "inputs": semantics.get("inputs", []),
        "blocking_elements": semantics.get("blocking_elements", []),
        "snapshot_id": semantics.get("snapshot_id"),
        "raw": semantics,
    }


def _network_request_payload(
    url: str,
    method: str,
    status: int | None,
    duration_ms: float | None,
    request_headers: dict[str, str] | None,
    response_headers: dict[str, str] | None,
    body_preview: str | None,
) -> dict[str, Any]:
    return {
        "url": url,
        "method": method.upper(),
        "status": status,
        "duration_ms": duration_ms,
        "request_headers": request_headers or {},
        "response_headers": response_headers or {},
        "body_preview": (body_preview or "")[:2048],
    }


def _js_exception_payload(
    message: str,
    stack: str | None,
    url: str | None,
    line: int | None,
    col: int | None,
    context_snippet: str | None,
) -> dict[str, Any]:
    return {
        "message": message,
        "stack": stack or "",
        "url": url or "",
        "line": line,
        "col": col,
        "context_snippet": context_snippet or "",
    }


def _action_trace_payload(
    action: str,
    status: str,
    target: str | None,
    error: str | None,
    duration_ms: float | None,
    effects: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "action": action,
        "status": status,
        "target": target,
        "error": error,
        "duration_ms": duration_ms,
        "effects": effects or [],
    }


def _policy_violation_payload(
    action: str,
    violation_type: str,
    reason: str,
    url: str | None,
    details: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "action": action,
        "violation_type": violation_type,
        "reason": reason,
        "url": url,
        "details": details or {},
    }


# ── Visible text helper ───────────────────────────────────────────────────────

def _extract_visible_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace to get visible text."""
    no_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    no_tags = re.sub(r"<[^>]+>", " ", no_script)
    return re.sub(r"\s+", " ", no_tags).strip()


# ── ArtifactCollector ─────────────────────────────────────────────────────────

class ArtifactCollector:
    """
    Collects and stores structured artifacts for each action.

    Philosophy: structured evidence > screenshots for AI tooling.
    Every action leaves a trail that enables:
    - Failure diagnosis
    - Deterministic replay
    - Agent learning
    - Evaluation

    Args:
        session_id: Owning session.
        max_size:   Ring buffer limit. 0 = unlimited.
    """

    def __init__(self, session_id: str = "", max_size: int = 0) -> None:
        self._session_id = session_id
        self._max_size = max_size
        self._artifacts: deque[Artifact] = deque()

    # ── internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _new_id() -> str:
        return f"art-{uuid.uuid4().hex[:12]}"

    def _append(self, artifact: Artifact) -> Artifact:
        self._artifacts.append(artifact)
        if self._max_size > 0:
            while len(self._artifacts) > self._max_size:
                self._artifacts.popleft()
        return artifact

    def record(
        self,
        kind: str | ArtifactKind,
        data: dict[str, Any],
        *,
        action_id: str | None = None,
        url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        artifact = Artifact(
            artifact_id=self._new_id(),
            kind=str(kind),
            timestamp=time.time(),
            session_id=self._session_id,
            data=data,
            action_id=action_id,
            url=url,
            metadata=metadata or {},
        )
        return self._append(artifact)

    # ── typed record helpers ──────────────────────────────────────────────────

    def record_dom(
        self,
        html: str,
        url: str = "",
        *,
        action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        return self.record(
            ArtifactKind.DOM_SNAPSHOT,
            _dom_payload(html, url),
            action_id=action_id,
            url=url,
            metadata=metadata,
        )

    def record_semantic(
        self,
        semantics: dict[str, Any],
        *,
        action_id: str | None = None,
        url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        return self.record(
            ArtifactKind.SEMANTIC_SNAPSHOT,
            _semantic_payload(semantics),
            action_id=action_id,
            url=url or semantics.get("url"),
            metadata=metadata,
        )

    def record_network(
        self,
        url: str,
        method: str = "GET",
        *,
        status: int | None = None,
        duration_ms: float | None = None,
        request_headers: dict[str, str] | None = None,
        response_headers: dict[str, str] | None = None,
        body_preview: str | None = None,
        action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        return self.record(
            ArtifactKind.NETWORK_TRACE,
            _network_request_payload(
                url, method, status, duration_ms,
                request_headers, response_headers, body_preview,
            ),
            action_id=action_id,
            url=url,
            metadata=metadata,
        )

    def record_js_exception(
        self,
        message: str,
        *,
        stack: str | None = None,
        url: str | None = None,
        line: int | None = None,
        col: int | None = None,
        context_snippet: str | None = None,
        action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        return self.record(
            ArtifactKind.JS_EXCEPTION,
            _js_exception_payload(message, stack, url, line, col, context_snippet),
            action_id=action_id,
            url=url,
            metadata=metadata,
        )

    def record_action_trace(
        self,
        action: str,
        status: str,
        *,
        target: str | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
        effects: list[dict[str, Any]] | None = None,
        action_id: str | None = None,
        url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        return self.record(
            ArtifactKind.ACTION_TRACE,
            _action_trace_payload(action, status, target, error, duration_ms, effects),
            action_id=action_id,
            url=url,
            metadata=metadata,
        )

    def record_policy_violation(
        self,
        action: str,
        violation_type: str,
        reason: str,
        *,
        url: str | None = None,
        details: dict[str, Any] | None = None,
        action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        return self.record(
            ArtifactKind.POLICY_VIOLATION,
            _policy_violation_payload(action, violation_type, reason, url, details),
            action_id=action_id,
            url=url,
            metadata=metadata,
        )

    # ── queries ───────────────────────────────────────────────────────────────

    def get_all(self) -> list[Artifact]:
        return list(self._artifacts)

    def get_by_kind(self, kind: str | ArtifactKind) -> list[Artifact]:
        k = str(kind)
        return [a for a in self._artifacts if a.kind == k]

    def get_by_action(self, action_id: str) -> list[Artifact]:
        return [a for a in self._artifacts if a.action_id == action_id]

    def get_failures(self) -> list[Artifact]:
        """Return ACTION_TRACE artifacts where status == 'failed'."""
        return [
            a for a in self._artifacts
            if a.kind == ArtifactKind.ACTION_TRACE
            and a.data.get("status") == "failed"
        ]

    def __len__(self) -> int:
        return len(self._artifacts)

    def __bool__(self) -> bool:
        return True  # always truthy even when empty

    # ── export / import ───────────────────────────────────────────────────────

    def export(self) -> dict[str, Any]:
        return {
            "session_id": self._session_id,
            "count": len(self._artifacts),
            "artifacts": [a.to_dict() for a in self._artifacts],
        }

    def export_json(self) -> str:
        return json.dumps(self.export(), ensure_ascii=False, indent=2, default=str)

    @classmethod
    def from_export(cls, data: dict[str, Any], max_size: int = 0) -> ArtifactCollector:
        session_id = data.get("session_id", "")
        collector = cls(session_id=session_id, max_size=max_size)
        for d in data.get("artifacts", []):
            collector._artifacts.append(Artifact.from_dict(d))
        return collector

    # ── summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        failures = 0
        for a in self._artifacts:
            counts[a.kind] = counts.get(a.kind, 0) + 1
            if a.kind == ArtifactKind.ACTION_TRACE and a.data.get("status") == "failed":
                failures += 1
        return {
            "session_id": self._session_id,
            "total": len(self._artifacts),
            "by_kind": counts,
            "action_failures": failures,
        }

    def __repr__(self) -> str:
        return f"ArtifactCollector(session={self._session_id!r}, count={len(self._artifacts)})"
