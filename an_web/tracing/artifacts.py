"""Artifact collection — structured evidence for AI debugging and replay."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Artifact:
    artifact_id: str
    kind: str  # "dom_snapshot" | "semantic_snapshot" | "network_trace" | "action_log"
    timestamp: float
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifactId": self.artifact_id,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class ArtifactCollector:
    """
    Collects and stores structured artifacts for each action.

    Philosophy: structured evidence > screenshots for AI tooling.
    Every action leaves a trail that enables:
    - Failure diagnosis
    - Deterministic replay
    - Agent learning
    - Evaluation
    """

    def __init__(self) -> None:
        self._artifacts: list[Artifact] = []

    def _new_id(self) -> str:
        import uuid
        return f"art-{uuid.uuid4().hex[:8]}"

    def record_dom(self, html: str, url: str) -> Artifact:
        artifact = Artifact(
            artifact_id=self._new_id(),
            kind="dom_snapshot",
            timestamp=time.time(),
            data={"html": html, "url": url},
        )
        self._artifacts.append(artifact)
        return artifact

    def record_semantic(self, semantic_data: dict[str, Any]) -> Artifact:
        artifact = Artifact(
            artifact_id=self._new_id(),
            kind="semantic_snapshot",
            timestamp=time.time(),
            data=semantic_data,
        )
        self._artifacts.append(artifact)
        return artifact

    def record_action(self, action_result: dict[str, Any]) -> Artifact:
        artifact = Artifact(
            artifact_id=self._new_id(),
            kind="action_log",
            timestamp=time.time(),
            data=action_result,
        )
        self._artifacts.append(artifact)
        return artifact

    def record_network(self, requests: list[dict[str, Any]]) -> Artifact:
        artifact = Artifact(
            artifact_id=self._new_id(),
            kind="network_trace",
            timestamp=time.time(),
            data={"requests": requests},
        )
        self._artifacts.append(artifact)
        return artifact

    def get_all(self) -> list[Artifact]:
        return list(self._artifacts)

    def get_by_kind(self, kind: str) -> list[Artifact]:
        return [a for a in self._artifacts if a.kind == kind]

    def export(self) -> dict[str, Any]:
        return {
            "count": len(self._artifacts),
            "artifacts": [a.to_dict() for a in self._artifacts],
        }
