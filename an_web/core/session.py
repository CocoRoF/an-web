"""
Session lifecycle management for AN-Web.

A Session is the top-level execution context for a single browser "tab":
it owns the cookie jar, per-origin localStorage, per-page sessionStorage,
navigation history, JS runtime, and event-loop scheduler.

Lifecycle::

    engine = ANWebEngine()
    session = await engine.create_session()
    await session.navigate("https://example.com")

    # Read semantic state
    page = await session.snapshot()

    # Execute tool calls
    result = await session.act({"tool": "click", "target": "#submit"})

    # Direct JS evaluation (after navigate)
    title = await session.execute_script("document.title")

    await session.close()

Design notes:
- JS runtime (QuickJS) is reset on every navigate() so page-global state
  doesn't bleed between pages.
- localStorage is keyed by origin (netloc) and survives navigations.
- sessionStorage is per-page and cleared on every navigate().
- The scheduler's network-settle phase yields once to allow asyncio tasks
  to flush; heavier settle logic (e.g. waiting on XHR) is done in the
  network layer itself.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from an_web.core.state import EngineStatus, PageState

if TYPE_CHECKING:
    from an_web.core.engine import ANWebEngine
    from an_web.core.scheduler import EventLoopScheduler
    from an_web.core.snapshot import SnapshotManager
    from an_web.dom.nodes import Document
    from an_web.dom.semantics import ActionResult, PageSemantics
    from an_web.js.runtime import JSRuntime
    from an_web.net.client import NetworkClient
    from an_web.net.cookies import CookieJar
    from an_web.policy.rules import PolicyRules

log = logging.getLogger(__name__)


class Session:
    """
    An isolated browser execution world.

    Each Session holds its own:
    - Cookie jar
    - localStorage (per-origin, persists across navigations)
    - sessionStorage (per-page, cleared on navigate)
    - Navigation history with back() support
    - JS runtime (QuickJS) instance — reset on every navigate
    - Event-loop scheduler (microtasks, timers, network settle)
    - PageState tracking
    - SnapshotManager for deterministic replay

    Inspired by Lightpanda's Session.zig which manages:
    browser, history, navigation, storage_shed, cookie_jar per session.
    """

    def __init__(
        self,
        engine: "ANWebEngine",
        policy: "PolicyRules",
        session_id: str | None = None,
    ) -> None:
        self.session_id: str = session_id or str(uuid.uuid4())
        self.engine = engine
        self.policy = policy
        self._closed: bool = False

        # Subsystems — populated by _init()
        self.scheduler: "EventLoopScheduler | None" = None
        self.network: "NetworkClient | None" = None
        self.cookies: "CookieJar | None" = None
        self.snapshots: "SnapshotManager | None" = None
        self.js_runtime: "JSRuntime | None" = None

        # Storage
        # localStorage: keyed by origin (netloc), persists across navigations
        self._local_storage: dict[str, dict[str, str]] = {}
        # sessionStorage: cleared on every navigate()
        self._session_storage: dict[str, str] = {}

        # Current page state
        self._current_url: str = "about:blank"
        self._current_document: "Document | None" = None
        self._page_state: PageState = PageState()

        # Navigation history (list of successfully visited URLs)
        self._history: list[str] = []

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def _init(self) -> None:
        """
        Lazy initialisation of all subsystems.

        Called by ANWebEngine.create_session() immediately after construction.
        """
        from an_web.core.scheduler import EventLoopScheduler
        from an_web.core.snapshot import SnapshotManager
        from an_web.js.runtime import JSRuntime
        from an_web.net.client import NetworkClient
        from an_web.net.cookies import CookieJar

        self.cookies = CookieJar()
        self.network = NetworkClient(cookie_jar=self.cookies)
        self.scheduler = EventLoopScheduler()
        self.snapshots = SnapshotManager()
        self.js_runtime = JSRuntime(session=self)

        # Register a lightweight network-settle hook: simply yield so
        # any asyncio tasks spawned during the action can flush.
        async def _network_settle() -> None:
            await asyncio.sleep(0)

        self.scheduler.register_network_settle(_network_settle)

        log.debug("Session %s initialised", self.session_id[:8])

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> dict[str, Any]:
        """
        Load a URL, build the DOM, and settle the page.

        Steps:
        1. Policy check (domain allow/deny list)
        2. HTTP fetch via NetworkClient
        3. HTML parse -> Document
        4. Create snapshot
        5. Reset JS runtime for new page (discards old scripts)
        6. Clear sessionStorage
        7. Dispatch DOMContentLoaded + load events
        8. Return ActionResult dict

        Returns:
            ActionResult as a dict with ``status`` (``"ok"`` | ``"failed"``).
        """
        from an_web.actions.navigate import NavigateAction

        self._page_state.status = EngineStatus.LOADING
        result = await NavigateAction().execute(session=self, url=url)

        if result.is_ok():
            self._history.append(self._current_url)
            # sessionStorage is scoped to the page — clear on navigation
            self._session_storage.clear()

            # Reset JS runtime for the new document
            if self.js_runtime is not None:
                self.js_runtime.on_page_load()
                # Fire page lifecycle events into the freshly-reset context
                self.js_runtime.dispatch_dom_content_loaded()
                self.js_runtime.dispatch_load()

            self._page_state.status = EngineStatus.IDLE
            self._page_state.dom_ready = True
            self._page_state.js_loaded = self.js_runtime is not None and self.js_runtime.is_available()
            self._page_state.url = self._current_url
            self._page_state.navigation_count += 1

            log.debug(
                "Session %s navigated to %s",
                self.session_id[:8],
                self._current_url,
            )
        else:
            self._page_state.status = EngineStatus.ERROR
            self._page_state.error = result.error

        return result.to_dict()

    async def back(self) -> dict[str, Any]:
        """
        Navigate to the previous URL in the history stack.

        Removes the current URL from history and re-navigates to the
        previous entry.  Returns a failure ActionResult if there is no
        previous page.
        """
        from an_web.dom.semantics import ActionResult

        # Need at least two entries: previous + current
        if len(self._history) < 1:
            return ActionResult(
                status="failed",
                action="back",
                error="no_history",
                recommended_next_actions=[
                    {"note": "No previous page to go back to"}
                ],
            ).to_dict()

        prev_url = self._history.pop()
        return await self.navigate(prev_url)

    # ------------------------------------------------------------------
    # Semantic snapshot
    # ------------------------------------------------------------------

    async def snapshot(self) -> "PageSemantics":
        """Return the current page's semantic state as a PageSemantics object."""
        from an_web.semantic.extractor import SemanticExtractor

        self._page_state.status = EngineStatus.EXTRACTING_SEMANTICS
        try:
            result = await SemanticExtractor().extract(session=self)
        finally:
            self._page_state.status = EngineStatus.IDLE
        return result

    # ------------------------------------------------------------------
    # Tool dispatch (AI action interface)
    # ------------------------------------------------------------------

    async def act(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """
        Execute an AI tool call and return a structured ActionResult dict.

        Accepted formats::

            {"tool": "click", "target": "#submit-btn"}
            {"tool": "type", "target": "#email", "text": "user@example.com"}
            {"tool": "navigate", "url": "https://example.com"}
            {"name": "click", "input": {"target": "#btn"}}   # input_schema fmt

        Returns:
            ActionResult dict with at minimum ``"status"`` and ``"action"`` keys.
        """
        from an_web.api.rpc import dispatch_tool

        return await dispatch_tool(tool_call, session=self)

    # ------------------------------------------------------------------
    # JavaScript execution
    # ------------------------------------------------------------------

    async def execute_script(self, script: str) -> Any:
        """
        Evaluate JavaScript in the current page context.

        Drains microtasks (Promise chains) after evaluation so that
        async JS operations started by the script are settled.

        Args:
            script: JavaScript source string.

        Returns:
            The Python-converted return value, or ``None`` if the runtime
            is unavailable or the script raises.
        """
        if self.js_runtime is None or not self.js_runtime.is_available():
            return None

        result = self.js_runtime.eval_safe(script)
        # Drain any microtasks queued by the script (Promise continuations)
        await self.js_runtime.drain_microtasks()
        return result.value if result.ok else None

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    @property
    def local_storage(self) -> dict[str, str]:
        """
        localStorage for the current page's origin.

        Persists across navigations within this session.
        Returns a mutable dict — writes are reflected immediately.
        """
        origin = self._origin()
        if origin not in self._local_storage:
            self._local_storage[origin] = {}
        return self._local_storage[origin]

    def get_local_storage(self, origin: str) -> dict[str, str]:
        """
        Return localStorage for an explicit origin (e.g. ``"example.com"``).

        Use when you need storage for an origin other than the current page.
        """
        if origin not in self._local_storage:
            self._local_storage[origin] = {}
        return self._local_storage[origin]

    @property
    def session_storage(self) -> dict[str, str]:
        """
        sessionStorage for the current page.

        Cleared on every navigate().
        Returns a mutable dict — writes are reflected immediately.
        """
        return self._session_storage

    def storage_state(self) -> dict[str, Any]:
        """
        Dump all storage state as a JSON-serialisable dict.

        Suitable for inclusion in a Snapshot's ``storage_state`` field.
        """
        return {
            "local_storage": {
                origin: dict(data)
                for origin, data in self._local_storage.items()
            },
            "session_storage": dict(self._session_storage),
            "cookies": self.cookies.to_dict() if self.cookies else {},
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_url(self) -> str:
        """The URL of the currently loaded page."""
        return self._current_url

    @property
    def history(self) -> list[str]:
        """Snapshot of the navigation history (read-only copy)."""
        return list(self._history)

    @property
    def page_state(self) -> PageState:
        """Current page execution state (mutable, for internal use)."""
        return self._page_state

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """
        Release all session resources.

        Idempotent — safe to call multiple times.
        """
        if self._closed:
            return
        # Shut down JS runtime first (releases QuickJS heap)
        if self.js_runtime is not None:
            self.js_runtime.close()
        # Close the HTTP client (flushes keep-alive pool)
        if self.network is not None:
            await self.network.close()
        # Discard all pending timers and queued callbacks
        if self.scheduler is not None:
            self.scheduler.reset()
        self._closed = True
        log.debug("Session %s closed", self.session_id[:8])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _origin(self) -> str:
        """Return the netloc (``host:port``) of the current URL."""
        parsed = urlparse(self._current_url)
        return parsed.netloc or "about:blank"

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def __repr__(self) -> str:
        return (
            f"Session("
            f"id={self.session_id[:8]}..., "
            f"url={self._current_url!r}, "
            f"closed={self._closed}"
            f")"
        )
