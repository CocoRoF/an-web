"""
Host Web API implementations injected into QuickJS context.

This is the most critical module — browser compatibility
depends on how well these APIs are implemented, not the JS engine.

Priority order (from implementation_plan.md):
  1. document / window basics
  2. EventTarget / addEventListener
  3. setTimeout / queueMicrotask / Promise drain
  4. fetch / XHR
  5. localStorage / sessionStorage / cookies
  6. location / history
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session


def build_host_globals(session: Session) -> dict[str, Any]:
    """Build the global host object map for QuickJS injection."""
    return {
        "console": _make_console(),
        # Full implementations in subsequent phases
    }


def _make_console() -> Any:
    """Minimal console object for JS debugging."""
    class Console:
        def log(self, *args: Any) -> None:
            import logging
            logging.getLogger("an_web.js.console").debug(" ".join(str(a) for a in args))

        def warn(self, *args: Any) -> None:
            import logging
            logging.getLogger("an_web.js.console").warning(" ".join(str(a) for a in args))

        def error(self, *args: Any) -> None:
            import logging
            logging.getLogger("an_web.js.console").error(" ".join(str(a) for a in args))

    return Console()
