"""
QuickJS runtime bridge for AN-Web.

Uses quickjs-py (or compatible binding) to embed QuickJS.
Falls back to a no-op stub if quickjs is not installed,
allowing core DOM/semantic functionality to work without JS.

Key insight from Lightpanda: the JS *engine* matters less than
the *host Web API* implementation. QuickJS with a good host
beats V8 with a poor host for AI automation tasks.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session


class JSRuntime:
    """
    Wraps a QuickJS context with AN-Web host Web API bindings.

    Lifecycle:
        runtime = JSRuntime(session)
        runtime.eval("document.title")
        await runtime.drain_microtasks()
        runtime.close()
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._ctx: Any = None
        self._available = False
        self._pending_jobs: list[Any] = []
        self._try_init()

    def _try_init(self) -> None:
        try:
            import quickjs  # type: ignore[import]
            self._ctx = quickjs.Context()
            self._available = True
            self._setup_host_api()
        except ImportError:
            # QuickJS not installed — use stub mode
            self._available = False

    def _setup_host_api(self) -> None:
        """Inject host Web APIs into JS context."""
        if not self._ctx:
            return
        from an_web.js.host_api import build_host_globals
        globals_dict = build_host_globals(self.session)
        for name, value in globals_dict.items():
            try:
                self._ctx.set_global(name, value)
            except Exception:
                pass

    def eval(self, script: str) -> Any:
        """Evaluate a JS expression/statement and return result."""
        if not self._available or not self._ctx:
            return None
        try:
            return self._ctx.eval(script)
        except Exception as e:
            raise RuntimeError(f"JS eval error: {e}") from e

    async def drain_microtasks(self) -> None:
        """
        Process pending JS microtasks (Promise jobs).
        Corresponds to Lightpanda's runMicrotasks() / env.runMicrotasks().
        """
        if not self._available or not self._ctx:
            return
        try:
            # quickjs-py: execute_pending_jobs() processes Promise callbacks
            if hasattr(self._ctx, "execute_pending_jobs"):
                self._ctx.execute_pending_jobs()
        except Exception:
            pass

    def is_available(self) -> bool:
        return self._available

    def close(self) -> None:
        self._ctx = None
        self._available = False
