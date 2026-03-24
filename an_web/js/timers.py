"""Timer management — setTimeout/clearTimeout/queueMicrotask."""
from __future__ import annotations
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.scheduler import EventLoopScheduler


class TimerManager:
    """Bridges JS timer calls to Python asyncio scheduler."""

    def __init__(self, scheduler: EventLoopScheduler) -> None:
        self._scheduler = scheduler

    def set_timeout(self, callback: Callable, delay_ms: int = 0) -> int:
        async def _wrap() -> None:
            callback()
        return self._scheduler.set_timeout(_wrap, delay_ms)

    def clear_timeout(self, timer_id: int) -> None:
        self._scheduler.clear_timeout(timer_id)

    def queue_microtask(self, callback: Callable) -> None:
        async def _wrap() -> None:
            callback()
        self._scheduler.queue_microtask(_wrap)
