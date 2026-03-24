"""
QuickJS runtime bridge for AN-Web.

Uses quickjs-py (pip install quickjs) to embed QuickJS engine.
Falls back to a no-op stub if the package is absent, allowing all
DOM/semantic/network functionality to work without JS support.

Key insight from Lightpanda: the JS *engine* matters less than
the *host Web API* implementation. QuickJS with a good host layer
beats V8 with a poor host for AI automation tasks.

Lifecycle::

    runtime = JSRuntime(session)          # initialises QuickJS + host API
    result  = runtime.eval("document.title")
    result  = runtime.eval_safe("1+1")    # never throws; returns EvalResult
    runtime.call("initApp", arg1, arg2)   # call a named JS function
    await runtime.drain_microtasks()      # process Promise chains
    runtime.close()                       # release QuickJS context
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from an_web.js.bridge import EvalResult, JSError, js_to_py, py_to_js

if TYPE_CHECKING:
    from an_web.core.session import Session

log = logging.getLogger(__name__)

# Max microtask drain iterations (safety cap)
_MAX_MICROTASK_JOBS = 1000
# Memory / stack limits for the QuickJS sandbox
_MEMORY_LIMIT_BYTES = 64 * 1024 * 1024   # 64 MiB
_STACK_SIZE_BYTES   = 512 * 1024          # 512 KiB


class JSRuntime:
    """
    Wraps a QuickJS context with AN-Web host Web API bindings.

    Thread-safety: NOT thread-safe. Create one JSRuntime per Session.
    The runtime is synchronous internally; async drain helpers exist
    to cooperate with the asyncio event loop without blocking it.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._ctx: Any = None
        self._available: bool = False
        self._scripts_loaded: list[str] = []   # record of eval'd script tags
        self._try_init()

    # ─────────────────────────────────────────────────────────────────────────
    # Initialisation
    # ─────────────────────────────────────────────────────────────────────────

    def _try_init(self) -> None:
        """Attempt to create a QuickJS context. Falls back to stub on failure."""
        try:
            import quickjs  # type: ignore[import]
            ctx = quickjs.Context()
            ctx.set_memory_limit(_MEMORY_LIMIT_BYTES)
            ctx.set_max_stack_size(_STACK_SIZE_BYTES)
            self._ctx = ctx
            self._available = True
            self._setup_host_api()
        except ImportError:
            log.debug("quickjs package not installed — JS runtime disabled")
            self._available = False
        except Exception as exc:
            log.warning("JSRuntime init failed: %s", exc)
            self._available = False

    def _setup_host_api(self) -> None:
        """Install the full host Web API (document, window, fetch, …)."""
        if not self._ctx:
            return
        try:
            from an_web.js.host_api import install_host_api
            install_host_api(self._ctx, self.session)
        except Exception as exc:
            log.warning("host API install failed: %s", exc)

    def _reset_context(self) -> None:
        """Re-create the QuickJS context (e.g. after navigation)."""
        self.close()
        self._scripts_loaded.clear()
        self._try_init()

    # ─────────────────────────────────────────────────────────────────────────
    # Eval / call
    # ─────────────────────────────────────────────────────────────────────────

    def eval(self, script: str) -> Any:
        """
        Evaluate a JS script/expression and return the raw quickjs result.

        For objects/arrays, the returned value is a ``_quickjs.Object``
        with a ``.json()`` method. Use :func:`js_to_py` to convert.

        Raises:
            JSError: if the script throws a JS exception.
            RuntimeError: if QuickJS is not available.
        """
        if not self._available or not self._ctx:
            raise RuntimeError("JS runtime not available")
        try:
            return self._ctx.eval(script)
        except Exception as exc:
            # quickjs raises quickjs.JSException for runtime errors
            raise JSError.from_quickjs_exception(exc) from exc

    def eval_safe(self, script: str, default: Any = None) -> EvalResult:
        """
        Like eval() but never raises — wraps result in EvalResult.

        Args:
            script:  JavaScript source to evaluate.
            default: Value to use as EvalResult.value on error.

        Returns:
            EvalResult with .ok / .value / .error fields.
        """
        if not self._available or not self._ctx:
            return EvalResult.success(default)
        try:
            raw = self._ctx.eval(script)
            return EvalResult.success(js_to_py(raw))
        except Exception as exc:
            err = JSError.from_quickjs_exception(exc)
            log.debug("eval_safe error: %s", err)
            return EvalResult.failure(err)

    def get_global(self, name: str, default: Any = None) -> Any:
        """
        Retrieve a named global from the JS context.

        Returns *default* if the name is undefined or runtime unavailable.
        """
        result = self.eval_safe(f"(typeof {name} !== 'undefined') ? JSON.stringify({name}) : null")
        if not result.ok or result.value is None:
            return default
        if isinstance(result.value, str):
            try:
                return json.loads(result.value)
            except json.JSONDecodeError:
                return result.value
        return result.value

    def set_global(self, name: str, value: Any) -> None:
        """
        Set a named global in the JS context.

        For callables, use the underlying ctx.add_callable() directly.
        For everything else, the value is JSON-serialised and injected.
        """
        if not self._available or not self._ctx:
            return
        try:
            if callable(value):
                self._ctx.add_callable(name, value)
            else:
                js_val = py_to_js(value)
                if isinstance(js_val, (bool, int, float, str)) or js_val is None:
                    # Can set directly
                    self._ctx.set(name, js_val)
                else:
                    # Inject via JSON
                    serialised = json.dumps(js_val, default=str)
                    self._ctx.eval(f"var {name} = {serialised};")
        except Exception as exc:
            log.debug("set_global '%s' failed: %s", name, exc)

    def call(self, fn_name: str, *args: Any) -> Any:
        """
        Call a named JS function with Python arguments.

        Arguments are converted via py_to_js(). The return value is
        converted via js_to_py().

        Raises:
            JSError: if the JS function throws.
        """
        if not self._available or not self._ctx:
            return None
        # Build arg list as JSON-safe literals
        js_args: list[str] = []
        for a in args:
            converted = py_to_js(a)
            try:
                js_args.append(json.dumps(converted, default=str))
            except Exception:
                js_args.append("undefined")

        script = f"{fn_name}({', '.join(js_args)})"
        raw = self.eval(script)
        return js_to_py(raw)

    def call_safe(self, fn_name: str, *args: Any) -> EvalResult:
        """Non-throwing variant of call()."""
        try:
            value = self.call(fn_name, *args)
            return EvalResult.success(value)
        except JSError as exc:
            return EvalResult.failure(exc)
        except Exception as exc:
            err = JSError(message=str(exc))
            return EvalResult.failure(err)

    # ─────────────────────────────────────────────────────────────────────────
    # Script tag loading
    # ─────────────────────────────────────────────────────────────────────────

    def load_script(self, source: str, src_hint: str = "<script>") -> EvalResult:
        """
        Execute an inline script tag source.

        Records the script in _scripts_loaded for debugging/replaying.
        Errors are logged but not raised (mirrors browser behaviour where
        a broken third-party script shouldn't crash the page).
        """
        result = self.eval_safe(source)
        self._scripts_loaded.append(src_hint)
        if not result.ok:
            log.debug("Script '%s' threw: %s", src_hint[:60], result.error)
        return result

    async def load_script_async(self, source: str, src_hint: str = "<script>") -> EvalResult:
        """
        Async variant of load_script() — yields to the event loop first
        so network-triggered re-renders can interleave properly.
        """
        await asyncio.sleep(0)
        return self.load_script(source, src_hint)

    # ─────────────────────────────────────────────────────────────────────────
    # Microtask / timer drain
    # ─────────────────────────────────────────────────────────────────────────

    async def drain_microtasks(self, max_jobs: int = _MAX_MICROTASK_JOBS) -> int:
        """
        Drain all pending JS microtasks (Promise callbacks, queueMicrotask).

        Mirrors Lightpanda's ``env.runMicrotasks()``. Yields to asyncio
        between batches so network I/O can proceed.

        Returns:
            Number of microtask jobs executed.
        """
        if not self._available or not self._ctx:
            return 0

        total = 0
        try:
            while total < max_jobs:
                ran = self._ctx.execute_pending_job()
                if not ran:
                    break
                total += 1
                # Yield to event loop every 16 jobs
                if total % 16 == 0:
                    await asyncio.sleep(0)
        except Exception as exc:
            log.debug("drain_microtasks error after %d jobs: %s", total, exc)

        return total

    async def settle(
        self,
        microtask_rounds: int = 3,
        yield_between: float = 0.0,
    ) -> None:
        """
        Full event-loop settle: drain microtasks across multiple rounds.

        Runs *microtask_rounds* drain passes with optional asyncio yields
        between them. This mirrors the Lightpanda pattern of:
            runMicrotasks() → settle_network() → flush_dom_mutations()
        """
        for _ in range(microtask_rounds):
            drained = await self.drain_microtasks()
            if yield_between > 0:
                await asyncio.sleep(yield_between)
            if drained == 0:
                break

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation support
    # ─────────────────────────────────────────────────────────────────────────

    def on_page_load(self) -> None:
        """
        Called by Session.navigate() after a new document is parsed.

        Resets the JS context for the new page so scripts don't bleed
        across navigations. The host API is re-installed so document/
        window proxy callbacks reflect the new document.
        """
        self._reset_context()

    def dispatch_dom_content_loaded(self) -> None:
        """Fire DOMContentLoaded on both document and window."""
        self.eval_safe(
            "var _dce = new Event('DOMContentLoaded');"
            "if (document && document.dispatchEvent) document.dispatchEvent(_dce);"
            "if (window && window.dispatchEvent) window.dispatchEvent(_dce);"
        )

    def dispatch_load(self) -> None:
        """Fire window load event."""
        self.eval_safe(
            "if (window && window.dispatchEvent) "
            "window.dispatchEvent(new Event('load'));"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Introspection
    # ─────────────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if QuickJS is usable."""
        return self._available

    def memory_usage(self) -> dict[str, int]:
        """Return QuickJS memory statistics (if available)."""
        if not self._available or not self._ctx:
            return {}
        try:
            mem = self._ctx.memory()
            if hasattr(mem, "__dict__"):
                return {k: v for k, v in mem.__dict__.items() if isinstance(v, int)}
            return {}
        except Exception:
            return {}

    @property
    def ctx(self) -> Any:
        """
        Direct access to the underlying quickjs.Context.
        Use with care — bypasses error handling.
        """
        return self._ctx

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Release the QuickJS context."""
        self._ctx = None
        self._available = False

    def __enter__(self) -> JSRuntime:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "available" if self._available else "unavailable"
        return f"JSRuntime({status}, scripts={len(self._scripts_loaded)})"
