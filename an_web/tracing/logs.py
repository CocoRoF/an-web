"""Structured logging for AN-Web — ring buffer + standard Python logging."""
from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ── Log level ─────────────────────────────────────────────────────────────────

class LogLevel(StrEnum):
    DEBUG   = "debug"
    INFO    = "info"
    WARNING = "warning"
    ERROR   = "error"
    CRITICAL = "critical"


# ── Log record ────────────────────────────────────────────────────────────────

@dataclass
class LogRecord:
    record_id: str
    level: str
    message: str
    timestamp: float
    session_id: str
    action_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "record_id": self.record_id,
            "level": self.level,
            "message": self.message,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
        }
        if self.action_id is not None:
            d["action_id"] = self.action_id
        if self.data:
            d["data"] = self.data
        return d


# ── Structured logger ─────────────────────────────────────────────────────────

class StructuredLogger:
    """
    Ring buffer structured logger.

    Logs are stored in memory (up to max_size entries) AND forwarded to
    the standard Python ``logging`` system so they appear in test output
    and log files.

    Args:
        name:       Logger name (forwarded to ``logging.getLogger``).
        session_id: Session this logger is bound to.
        max_size:   Ring buffer capacity. 0 = unlimited.
    """

    def __init__(
        self,
        name: str = "an_web",
        session_id: str = "",
        max_size: int = 1000,
    ) -> None:
        self._name = name
        self._session_id = session_id
        self._max_size = max_size
        self._records: deque[LogRecord] = deque()
        self._py_logger = logging.getLogger(f"an_web.{name}")
        self._current_action_id: str | None = None

    # ── core log method ───────────────────────────────────────────────────────

    def log(
        self,
        level: str | LogLevel,
        message: str,
        *,
        action_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> LogRecord:
        lvl = str(level)
        rec = LogRecord(
            record_id=f"log-{uuid.uuid4().hex[:8]}",
            level=lvl,
            message=message,
            timestamp=time.time(),
            session_id=self._session_id,
            action_id=action_id or self._current_action_id,
            data=data or {},
        )
        self._records.append(rec)
        if self._max_size > 0:
            while len(self._records) > self._max_size:
                self._records.popleft()

        # Forward to standard logging
        py_level = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(lvl, logging.DEBUG)
        self._py_logger.log(py_level, message, extra={"an_web_data": rec.data})

        return rec

    # ── convenience wrappers ──────────────────────────────────────────────────

    def debug(self, message: str, **kwargs: Any) -> LogRecord:
        return self.log(LogLevel.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> LogRecord:
        return self.log(LogLevel.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> LogRecord:
        return self.log(LogLevel.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> LogRecord:
        return self.log(LogLevel.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> LogRecord:
        return self.log(LogLevel.CRITICAL, message, **kwargs)

    # ── action context manager ────────────────────────────────────────────────

    def action_context(self, action_id: str) -> _ActionContext:
        return _ActionContext(self, action_id)

    def _set_action_id(self, action_id: str | None) -> None:
        self._current_action_id = action_id

    # ── queries ───────────────────────────────────────────────────────────────

    def get_all(self) -> list[LogRecord]:
        return list(self._records)

    def get_by_level(self, level: str | LogLevel) -> list[LogRecord]:
        lvl = str(level)
        return [r for r in self._records if r.level == lvl]

    def get_by_action(self, action_id: str) -> list[LogRecord]:
        return [r for r in self._records if r.action_id == action_id]

    def get_errors(self) -> list[LogRecord]:
        return [r for r in self._records if r.level in ("error", "critical")]

    def clear(self) -> None:
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"StructuredLogger(name={self._name!r}, records={len(self._records)})"


# ── Action context helper ─────────────────────────────────────────────────────

class _ActionContext:
    """Context manager that binds all log calls to a specific action_id."""

    def __init__(self, logger: StructuredLogger, action_id: str) -> None:
        self._logger = logger
        self._action_id = action_id
        self._previous: str | None = None

    def __enter__(self) -> StructuredLogger:
        self._previous = self._logger._current_action_id
        self._logger._set_action_id(self._action_id)
        return self._logger

    def __exit__(self, *_: Any) -> None:
        self._logger._set_action_id(self._previous)


# ── ActionLogger (backward-compatible thin wrapper) ───────────────────────────

class ActionLogger:
    """
    Records structured action events for debugging and replay.

    Thin wrapper over StructuredLogger that preserves the original dict-list API.
    """

    def __init__(self, session_id: str = "", max_size: int = 1000) -> None:
        self._structured = StructuredLogger(
            name="action_logger", session_id=session_id, max_size=max_size
        )

    def log(self, event_type: str, data: dict[str, Any]) -> None:
        self._structured.info(event_type, data=data)

    def get_events(self) -> list[dict[str, Any]]:
        return [
            {"timestamp": r.timestamp, "type": r.message, **r.data}
            for r in self._structured.get_all()
        ]

    @property
    def structured(self) -> StructuredLogger:
        return self._structured


# ── Module-level convenience ──────────────────────────────────────────────────

def get_logger(name: str, session_id: str = "", max_size: int = 1000) -> StructuredLogger:
    """Return a StructuredLogger bound to *name* and optionally *session_id*."""
    return StructuredLogger(name=name, session_id=session_id, max_size=max_size)
