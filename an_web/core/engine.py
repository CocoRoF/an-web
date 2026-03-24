"""
AN-Web main engine entry point.

ANWebEngine is the top-level factory for browser sessions.  It owns the
session lifecycle, enforces the ``max_concurrent_sessions`` limit, and
provides a session registry for look-up by ID.

Usage::

    engine = ANWebEngine()
    session = await engine.create_session()
    await session.navigate("https://example.com")
    state = await session.snapshot()
    await session.close()
    await engine.close()

Context-manager form (preferred)::

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://example.com")
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.policy.rules import PolicyRules

log = logging.getLogger(__name__)

# Default upper bound on simultaneously open sessions.  Primarily guards
# against accidental leaks in long-running processes.
_DEFAULT_MAX_SESSIONS = 100


class ANWebEngine:
    """
    Top-level engine that manages Session lifecycle.

    Responsibilities:
    - Create and initialise Session instances.
    - Maintain a session registry keyed by session_id.
    - Enforce the ``max_concurrent_sessions`` cap.
    - Close all open sessions when the engine itself is closed.

    Thread-safety: NOT thread-safe.  Use one engine per asyncio task/coroutine.
    """

    def __init__(
        self,
        max_concurrent_sessions: int = _DEFAULT_MAX_SESSIONS,
    ) -> None:
        self._max_concurrent_sessions = max_concurrent_sessions
        # Ordered list keeps creation order for iteration.
        self._sessions: list["Session"] = []
        # Registry for O(1) look-up by session_id.
        self._session_map: dict[str, "Session"] = {}

    # ------------------------------------------------------------------
    # Session creation
    # ------------------------------------------------------------------

    async def create_session(
        self,
        policy: "PolicyRules | None" = None,
        session_id: str | None = None,
    ) -> "Session":
        """
        Create a new isolated browser session.

        Args:
            policy:     Policy rules for this session.  Defaults to an
                        unrestricted ``PolicyRules.default()`` instance.
            session_id: Explicit session ID.  Auto-generated (UUID4) if
                        omitted.

        Returns:
            A fully-initialised Session ready for use.

        Raises:
            RuntimeError: If the engine is already at ``max_concurrent_sessions``.
        """
        # Count only live (non-closed) sessions against the cap.
        live = self.active_sessions
        if len(live) >= self._max_concurrent_sessions:
            raise RuntimeError(
                f"ANWebEngine is at capacity "
                f"({self._max_concurrent_sessions} concurrent sessions). "
                f"Close existing sessions before creating new ones."
            )

        from an_web.core.session import Session
        from an_web.policy.rules import PolicyRules as DefaultPolicy

        session = Session(
            engine=self,
            policy=policy or DefaultPolicy.default(),
            session_id=session_id,
        )
        await session._init()

        self._sessions.append(session)
        self._session_map[session.session_id] = session

        log.debug("ANWebEngine: created session %s", session.session_id[:8])
        return session

    # ------------------------------------------------------------------
    # Session registry
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> "Session | None":
        """
        Look up a session by its UUID.

        Returns:
            The Session, or ``None`` if the ID is not found or the session
            has been closed and removed.
        """
        return self._session_map.get(session_id)

    def remove_session(self, session_id: str) -> None:
        """
        Remove a session from the engine registry (does NOT close it).

        Normally called automatically on engine.close(); can also be
        called manually after session.close() to release the registry entry.
        """
        self._session_map.pop(session_id, None)
        self._sessions = [
            s for s in self._sessions if s.session_id != session_id
        ]

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_sessions(self) -> list["Session"]:
        """Return all sessions that have not yet been closed."""
        return [s for s in self._sessions if not s._closed]

    @property
    def session_count(self) -> int:
        """Total number of sessions ever created (including closed ones)."""
        return len(self._sessions)

    @property
    def active_session_count(self) -> int:
        """Number of currently open (non-closed) sessions."""
        return len(self.active_sessions)

    @property
    def max_concurrent_sessions(self) -> int:
        """The maximum number of simultaneously open sessions."""
        return self._max_concurrent_sessions

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """
        Close all sessions and release resources.

        Idempotent — safe to call multiple times.
        """
        # Close all sessions (including already-closed ones which are no-ops)
        for session in list(self._sessions):
            await session.close()
        self._sessions.clear()
        self._session_map.clear()
        log.debug("ANWebEngine closed (%d sessions released)", self.session_count)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ANWebEngine":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def __repr__(self) -> str:
        return (
            f"ANWebEngine("
            f"active={self.active_session_count}, "
            f"total={self.session_count}, "
            f"max={self._max_concurrent_sessions}"
            f")"
        )
