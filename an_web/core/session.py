"""Session lifecycle management for AN-Web."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from an_web.core.engine import ANWebEngine
    from an_web.core.scheduler import EventLoopScheduler
    from an_web.core.snapshot import SnapshotManager
    from an_web.net.client import NetworkClient
    from an_web.net.cookies import CookieJar
    from an_web.policy.rules import PolicyRules
    from an_web.semantic.extractor import PageSemantics


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

        # These are initialized in _init()
        self.scheduler: EventLoopScheduler | None = None
        self.network: NetworkClient | None = None
        self.cookies: CookieJar | None = None
        self.snapshots: SnapshotManager | None = None
        self._current_url: str = "about:blank"

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
        """Navigate to a URL and return action result."""
        from an_web.actions.navigate import NavigateAction
        action = NavigateAction()
        return await action.execute(url=url, session=self)

    async def snapshot(self) -> PageSemantics:
        """Return current page semantic state."""
        from an_web.semantic.extractor import SemanticExtractor
        extractor = SemanticExtractor()
        return await extractor.extract(session=self)

    async def act(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Execute an AI tool call and return structured result."""
        from an_web.api.rpc import dispatch_tool
        return await dispatch_tool(tool_call, session=self)

    async def close(self) -> None:
        """Release all session resources."""
        if self._closed:
            return
        if self.network:
            await self.network.close()
        self._closed = True

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
