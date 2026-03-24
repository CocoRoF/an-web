"""AN-Web main engine entry point."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.policy.rules import PolicyRules


class ANWebEngine:
    """
    Top-level engine that manages session lifecycle.

    Usage::

        engine = ANWebEngine()
        session = await engine.create_session()
        await session.navigate("https://example.com")
        state = await session.snapshot()
    """

    def __init__(self) -> None:
        self._sessions: list[Session] = []

    async def create_session(
        self,
        policy: PolicyRules | None = None,
    ) -> Session:
        """Create a new isolated browser session."""
        from an_web.core.session import Session
        from an_web.policy.rules import PolicyRules as DefaultPolicy

        session = Session(
            engine=self,
            policy=policy or DefaultPolicy.default(),
        )
        await session._init()
        self._sessions.append(session)
        return session

    async def close(self) -> None:
        """Close all sessions and release resources."""
        for session in self._sessions:
            await session.close()
        self._sessions.clear()

    async def __aenter__(self) -> ANWebEngine:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
