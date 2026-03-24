"""Session lifecycle management for AN-Web."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from an_web.core.engine import ANWebEngine
    from an_web.core.scheduler import EventLoopScheduler
    from an_web.core.snapshot import SnapshotManager
    from an_web.dom.nodes import Document
    from an_web.dom.semantics import PageSemantics
    from an_web.net.client import NetworkClient
    from an_web.net.cookies import CookieJar
    from an_web.policy.rules import PolicyRules


class Session:
    """
    An isolated browser execution world.

    Session holds:
    - Cookie jar, localStorage, sessionStorage
    - Navigation history
    - JS runtime instance
    - Event loop scheduler
    - Policy enforcement context

    Inspired by Lightpanda's Session.zig which manages:
    browser, history, navigation, storage_shed, cookie_jar per session.
    """

    def __init__(self, engine: ANWebEngine, policy: PolicyRules) -> None:
        self.engine = engine
        self.policy = policy
        self._closed = False

        # Subsystems — initialized in _init()
        self.scheduler: EventLoopScheduler | None = None
        self.network: NetworkClient | None = None
        self.cookies: CookieJar | None = None
        self.snapshots: SnapshotManager | None = None

        # Current page state
        self._current_url: str = "about:blank"
        self._current_document: Document | None = None

        # Navigation history
        self._history: list[str] = []

    async def _init(self) -> None:
        """Lazy initialization of all subsystems."""
        from an_web.core.scheduler import EventLoopScheduler
        from an_web.core.snapshot import SnapshotManager
        from an_web.net.client import NetworkClient
        from an_web.net.cookies import CookieJar

        self.cookies = CookieJar()
        self.network = NetworkClient(cookie_jar=self.cookies)
        self.scheduler = EventLoopScheduler()
        self.snapshots = SnapshotManager()

    async def navigate(self, url: str) -> dict[str, Any]:
        """
        Navigate to a URL, parse HTML, and settle the page.

        Returns ActionResult as a dict.
        """
        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session=self, url=url)
        if result.is_ok():
            self._history.append(url)
        return result.to_dict()

    async def snapshot(self) -> PageSemantics:
        """Return current page semantic state as a PageSemantics object."""
        from an_web.semantic.extractor import SemanticExtractor
        extractor = SemanticExtractor()
        return await extractor.extract(session=self)

    async def act(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """
        Execute an AI tool call and return a structured ActionResult dict.

        tool_call format::

            {"tool": "click", "target": "#submit-btn"}
            {"tool": "type", "target": "#email", "text": "user@example.com"}
            {"tool": "navigate", "url": "https://example.com"}
        """
        from an_web.api.rpc import dispatch_tool
        return await dispatch_tool(tool_call, session=self)

    async def close(self) -> None:
        """Release all session resources."""
        if self._closed:
            return
        if self.network:
            await self.network.close()
        self._closed = True

    @property
    def current_url(self) -> str:
        return self._current_url

    @property
    def history(self) -> list[str]:
        return list(self._history)

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
