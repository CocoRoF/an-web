"""Structured logging for AN-Web."""
from __future__ import annotations

import logging
import time
from typing import Any


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"an_web.{name}")


class ActionLogger:
    """Records structured action events for debugging and replay."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def log(self, event_type: str, data: dict[str, Any]) -> None:
        self._events.append({
            "timestamp": time.time(),
            "type": event_type,
            **data,
        })

    def get_events(self) -> list[dict[str, Any]]:
        return list(self._events)
