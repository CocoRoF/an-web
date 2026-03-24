"""Engine global state management."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class EngineStatus(Enum):
    IDLE = auto()
    LOADING = auto()
    EXECUTING_JS = auto()
    WAITING_NETWORK = auto()
    EXTRACTING_SEMANTICS = auto()
    ERROR = auto()


@dataclass
class PageState:
    """Current page execution state."""
    url: str = "about:blank"
    status: EngineStatus = EngineStatus.IDLE
    dom_ready: bool = False
    js_loaded: bool = False
    network_idle: bool = True
    error: str | None = None
    pending_requests: int = 0
    mutation_count: int = 0
    navigation_count: int = 0
    extra: dict = field(default_factory=dict)

    def reset(self) -> None:
        self.url = "about:blank"
        self.status = EngineStatus.IDLE
        self.dom_ready = False
        self.js_loaded = False
        self.network_idle = True
        self.error = None
        self.pending_requests = 0
        self.mutation_count = 0
        self.navigation_count = 0
        self.extra = {}
