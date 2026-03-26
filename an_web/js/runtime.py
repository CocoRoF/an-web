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

# Minimum script size (bytes) to consider for polyfill detection.
# Small scripts are never polyfills and checking them would be wasteful.
_POLYFILL_MIN_SIZE = 50_000


def _is_corejs_polyfill(source: str) -> bool:
    """Detect core-js / polyfill.io bundles that cause infinite loops in QuickJS.

    QuickJS already supports ES2020+ natively, so these bundles are
    unnecessary and their aggressive patching of RegExp, Array, Symbol etc.
    can create infinite loops when interacting with Python-backed DOM objects.

    Heuristic: large script with many core-js module IDs and prototype patches.
    """
    if len(source) < _POLYFILL_MIN_SIZE:
        return False
    # Quick check: core-js webpack modules have numbered IDs and `.prototype`
    # A 200KB+ bundle with 100+ `.prototype` and webpack-style `function(t,r,e)`
    # is almost certainly a core-js polyfill.
    head = source[:5000]
    if "polyfill" in head.lower():
        return True
    # Count core-js signatures in first 1000 chars
    sample = source[:2000]
    if (
        sample.count(".prototype") > 3
        and "function(t,r,e)" in sample
        and len(source) > 100_000
    ):
        return True
    return False


def _extract_webpack_runtime(source: str) -> str | None:
    """Extract the webpack 5 runtime from a polyfill bundle.

    Core-js polyfill bundles on sites like naver.com often embed the
    webpack runtime (``__webpack_require__``, push interceptor, chunk
    loading) alongside the polyfill modules.  Skipping the entire bundle
    kills webpack's module system.

    Strategy: keep the entire IIFE (modules + runtime) but:
    1. Remove entry module calls that trigger polyfill patching
    2. Make chunk callback errors non-fatal so entry modules that
       depend on not-yet-available data don't crash chunk registration

    Returns ``None`` if no webpack runtime is found.
    """
    import re

    # Verify the bundle has a webpack runtime (push interceptor)
    if 'self.webpackChunkpc' not in source[-3000:]:
        return None

    push_match = re.search(
        r'self\.webpackChunkpc\s*=\s*self\.webpackChunkpc\s*\|\|\s*\[\]',
        source[-3000:],
    )
    if not push_match:
        return None

    # 1) Strip polyfill entry module calls at the tail.
    #    Pattern: }(),n(XXXXX);var X=n(XXXXX);X=n.O(X)}();
    cleaned = re.sub(
        r'\}\s*\(\s*\)\s*,\s*\w\(\d+\)[^}]*\}\s*\(\s*\)\s*;?\s*$',
        '}()}();',
        source,
    )

    if cleaned == source:
        cleaned = re.sub(
            r',\s*\w\(\d+\)\s*;\s*var\s+\w\s*=\s*\w\(\d+\)\s*;\s*\w\s*=\s*\w\.O\(\w\)\s*\}\s*\(\s*\)\s*;?\s*$',
            '}();',
            source,
        )

    if cleaned == source:
        return None

    # 2) Make the chunk callback error-tolerant.
    #    The interceptor has: if(a)var f=a(n)
    #    Wrap in try/catch: if(a)try{var f=a(n)}catch(_e){}
    #    This ensures chunks are still marked as installed even when
    #    entry modules throw (e.g. accessing data not yet available).
    cleaned = cleaned.replace(
        'if(a)var f=a(n)',
        'if(a)try{var f=a(n)}catch(_e){}'
    )

    return cleaned


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
                if isinstance(js_val, bool | int | float | str) or js_val is None:
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

        Core-js style polyfill bundles are auto-skipped because they can
        cause infinite loops in QuickJS (which already supports ES2020+).
        """
        # QuickJS rejects embedded null bytes — strip them.
        source = source.replace("\x00", "")

        if _is_corejs_polyfill(source):
            # The polyfill modules cause infinite loops, but the bundle may
            # also contain the webpack 5 runtime (module system, push
            # interceptor). Extract and run only the runtime portion.
            runtime = _extract_webpack_runtime(source)
            if runtime:
                log.info(
                    "Polyfill '%s': skipping modules, injecting webpack runtime",
                    src_hint[:60],
                )
                result = self.eval_safe(runtime)
                self._scripts_loaded.append(src_hint)
                if not result.ok:
                    log.debug("Webpack runtime from '%s' threw: %s", src_hint[:60], result.error)
                return result
            else:
                log.info(
                    "Skipping core-js polyfill '%s' (no webpack runtime found)",
                    src_hint[:60],
                )
                self._scripts_loaded.append(src_hint)
                return EvalResult.success(None)

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
        Drain all pending JS microtasks (Promise callbacks, queueMicrotask)
        and fire ready timers.

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

        # Fire any ready timers and drain resulting microtasks
        try:
            timers_fired = self.eval_safe("typeof _fireReadyTimers === 'function' ? _fireReadyTimers() : 0")
            if timers_fired.ok and timers_fired.value and int(timers_fired.value) > 0:
                # Drain microtasks queued by timer callbacks
                extra = 0
                while extra < max_jobs:
                    ran = self._ctx.execute_pending_job()
                    if not ran:
                        break
                    extra += 1
                    total += 1
        except Exception as exc:
            log.debug("timer fire error: %s", exc)

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
