"""
V8 runtime bridge for AN-Web.

Uses PyMiniRacer (pip install py_mini_racer) to embed Google's V8 engine.
Falls back to a no-op stub if the package is absent, allowing all
DOM/semantic/network functionality to work without JS support.

V8 advantages:
- Full ES2024+ (async/await, modules, WeakRef, BigInt, etc.)
- Automatic microtask flushing after each eval()
- Higher performance on large webpack bundles
- Same engine as Chrome, ensuring real-world compatibility

Architecture:
    PyMiniRacer does not support add_callable() (registering Python
    functions into JS). Instead, all _py_* host functions are implemented
    as pure JS functions inside the bootstrap shim, backed by a
    synchronous command bridge:

    1. The bootstrap creates _py_* functions that call into a Python-side
       dispatcher via a special ``_callPyBridge(name, argsJson)`` pattern.
    2. ``_callPyBridge`` is injected via eval before scripts run.
    3. V8 auto-flushes microtasks after each eval(), so Promises settle
       automatically without manual draining.

Lifecycle::

    runtime = JSRuntime(session)
    result  = runtime.eval("document.title")
    result  = runtime.eval_safe("1+1")
    runtime.call("initApp", arg1, arg2)
    await runtime.drain_microtasks()
    runtime.close()
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from an_web.js.bridge import EvalResult, JSError, py_to_js

if TYPE_CHECKING:
    from an_web.core.session import Session

log = logging.getLogger(__name__)

# Max microtask drain iterations (safety cap)
_MAX_MICROTASK_JOBS = 1000
# V8 soft memory limit
_V8_MEMORY_LIMIT = 256 * 1024 * 1024  # 256 MiB

# Per-eval V8 CPU budgets (ms).  Without these, a single pathological
# script (busy-wait, unbounded timer cascade) can block the engine for
# minutes — V8 evals are synchronous and cannot be cancelled from asyncio.
_DEFAULT_EVAL_TIMEOUT_MS = 5_000
# Housekeeping evals (timer firing, bridge drains) get a tighter budget.
_HOUSEKEEPING_EVAL_TIMEOUT_MS = 2_000
# After a drain eval is killed with zero timers fired, later drains on the
# same page get this reduced budget (the pump is almost certainly spinning).
_HOUSEKEEPING_REDUCED_TIMEOUT_MS = 400
# Consecutive fruitless kills before we stop draining for this page.
_DRAIN_KILL_GIVE_UP = 3

# Minimum script size (bytes) to consider for polyfill detection.
_POLYFILL_MIN_SIZE = 50_000


def _is_corejs_polyfill(source: str) -> bool:
    """Detect core-js / polyfill.io bundles.

    V8 handles modern JS natively, but these bundles often contain
    aggressive patching that conflicts with our host API shim.
    """
    if len(source) < _POLYFILL_MIN_SIZE:
        return False
    head = source[:5000]
    if "polyfill" in head.lower():
        return True
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

    Returns ``None`` if no webpack runtime is found.
    """
    import re

    if 'self.webpackChunkpc' not in source[-3000:]:
        return None

    push_match = re.search(
        r'self\.webpackChunkpc\s*=\s*self\.webpackChunkpc\s*\|\|\s*\[\]',
        source[-3000:],
    )
    if not push_match:
        return None

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

    cleaned = cleaned.replace(
        'if(a)var f=a(n)',
        'if(a)try{var f=a(n)}catch(_e){}'
    )

    return cleaned


# All MiniRacer instances must be constructed AND closed on ONE dedicated
# thread: the native layer segfaults when a context is disposed from a
# different thread than the one that created it (V8 thread affinity), and
# constructing on an asyncio-loop thread binds the context to that loop,
# which then forbids per-eval timeouts.
_creator_pool: Any = None


def _create_mini_racer() -> Any:
    """Create a MiniRacer whose context is NOT bound to our asyncio loop.

    mini-racer >= 0.12 binds a new context to the currently-running
    asyncio loop, and then refuses per-eval timeouts from that loop
    ("use eval_cancelable").  Constructing the instance on a dedicated
    creator thread (where no loop runs) makes mini-racer spawn its own
    internal event loop, so ``eval(code, timeout=...)`` works from any
    thread — including ours — via its thread-safe dispatch path.
    """
    from py_mini_racer import MiniRacer  # type: ignore[import]

    global _creator_pool
    if _creator_pool is None:
        from concurrent.futures import ThreadPoolExecutor
        _creator_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="anweb-v8-init"
        )
    return _creator_pool.submit(MiniRacer).result()


def _v8_to_py(value: Any, ctx: Any = None) -> Any:
    """Convert a PyMiniRacer return value to a Python native type.

    PyMiniRacer returns JSObject for complex types.  When *ctx* is
    provided, we use ``JSON.stringify`` inside V8 to serialise it,
    which is more reliable than ``str()``.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    type_name = type(value).__name__
    # mini-racer >= 0.12 returns a JSUndefined sentinel for undefined
    if type_name == "JSUndefinedType":
        return None

    # JS object handle (JSObject in py-mini-racer 0.6, JSMappedObjectImpl /
    # JSArrayImpl etc. in mini-racer >= 0.12) — use V8 JSON.stringify.
    if type_name.startswith("JS") and ctx is not None:
        try:
            # Store in a temp var, stringify, then clean up
            ctx.eval("var __tmp_conv = null;")
            # Re-evaluate the expression won't work — use the object id
            json_str = ctx.eval(
                "JSON.stringify(__tmp_conv)"
            )
            if json_str and isinstance(json_str, str):
                return json.loads(json_str)
        except Exception:
            pass

    # Fallback: str() → JSON parse
    try:
        s = str(value)
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return s
    except Exception:
        pass
    return value


class JSRuntime:
    """
    Wraps a V8 context (via PyMiniRacer) with AN-Web host Web API bindings.

    Thread-safety: NOT thread-safe. Create one JSRuntime per Session.
    V8 automatically flushes microtasks after each eval() call, so
    Promise chains settle without manual intervention.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._ctx: Any = None
        self._available: bool = False
        self._scripts_loaded: list[str] = []
        # Runaway-pump backoff: consecutive drain evals killed by the V8
        # CPU timeout without firing a single timer. See drain_microtasks.
        self._drain_kills: int = 0
        self._try_init()

    # ─────────────────────────────────────────────────────────────────────────
    # Initialisation
    # ─────────────────────────────────────────────────────────────────────────

    def _try_init(self) -> None:
        """Attempt to create a V8 context via PyMiniRacer."""
        try:
            ctx = _create_mini_racer()
            ctx.set_soft_memory_limit(_V8_MEMORY_LIMIT)
            self._ctx = ctx
            self._available = True
            self._setup_host_api()
        except ImportError:
            log.debug("py_mini_racer not installed — JS runtime disabled")
            self._available = False
        except Exception as exc:
            log.warning("JSRuntime V8 init failed: %s", exc)
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
        """Re-create the V8 context (e.g. after navigation)."""
        self.close()
        self._scripts_loaded.clear()
        self._drain_kills = 0  # fresh page → fresh chance
        self._try_init()

    # ─────────────────────────────────────────────────────────────────────────
    # Eval / call
    # ─────────────────────────────────────────────────────────────────────────

    def eval(self, script: str, timeout_ms: int | None = None) -> Any:
        """
        Evaluate a JS script/expression and return the result.

        V8 automatically flushes microtasks after eval(), so Promise
        continuations (.then) are already settled when this returns.

        Args:
            script:     JavaScript source to evaluate.
            timeout_ms: V8 CPU budget for this eval (defaults to
                        ``_DEFAULT_EVAL_TIMEOUT_MS``).

        Raises:
            JSError: if the script throws a JS exception or times out.
            RuntimeError: if V8 is not available.
        """
        if not self._available or not self._ctx:
            raise RuntimeError("JS runtime not available")
        try:
            result = self._ctx.eval(
                script, timeout=timeout_ms or _DEFAULT_EVAL_TIMEOUT_MS
            )
            # Process any pending bridge commands after eval
            self._process_bridge_commands()
            return self._convert_result(result, script)
        except Exception as exc:
            raise JSError.from_v8_exception(exc) from exc

    def eval_safe(
        self,
        script: str,
        default: Any = None,
        timeout_ms: int | None = None,
    ) -> EvalResult:
        """
        Like eval() but never raises — wraps result in EvalResult.

        Args:
            script:     JavaScript source to evaluate.
            default:    Value to use as EvalResult.value on error.
            timeout_ms: V8 CPU budget for this eval (defaults to
                        ``_DEFAULT_EVAL_TIMEOUT_MS``).

        Returns:
            EvalResult with .ok / .value / .error fields.
        """
        if not self._available or not self._ctx:
            return EvalResult.success(default)
        try:
            raw = self._ctx.eval(
                script, timeout=timeout_ms or _DEFAULT_EVAL_TIMEOUT_MS
            )
            self._process_bridge_commands()
            return EvalResult.success(self._convert_result(raw, script))
        except Exception as exc:
            err = JSError.from_v8_exception(exc)
            log.debug("eval_safe error: %s", err)
            return EvalResult.failure(err)

    def _convert_result(self, value: Any, script: str = "") -> Any:
        """Convert a V8 result to a Python native type.

        PyMiniRacer returns ``JSObject`` for complex JS objects.  When
        detected, we re-eval with ``JSON.stringify`` to produce a dict.
        """
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if type(value).__name__ == "JSUndefinedType":
            return None
        if type(value).__name__.startswith("JS") and self._ctx and script:
            try:
                json_str = self._ctx.eval(
                    f"JSON.stringify({script})",
                    timeout=_HOUSEKEEPING_EVAL_TIMEOUT_MS,
                )
                if isinstance(json_str, str):
                    return json.loads(json_str)
            except Exception:
                pass
        return _v8_to_py(value)

    def get_global(self, name: str, default: Any = None) -> Any:
        """Retrieve a named global from the JS context."""
        result = self.eval_safe(
            f"(typeof {name} !== 'undefined') ? JSON.stringify({name}) : null"
        )
        if not result.ok or result.value is None:
            return default
        if isinstance(result.value, str):
            try:
                return json.loads(result.value)
            except json.JSONDecodeError:
                return result.value
        return result.value

    def set_global(self, name: str, value: Any) -> None:
        """Set a named global in the JS context via JSON serialisation."""
        if not self._available or not self._ctx:
            return
        try:
            js_val = py_to_js(value)
            serialised = json.dumps(js_val, default=str)
            self._ctx.eval(f"var {name} = {serialised};")
        except Exception as exc:
            log.debug("set_global '%s' failed: %s", name, exc)

    def call(self, fn_name: str, *args: Any) -> Any:
        """Call a named JS function with Python arguments."""
        if not self._available or not self._ctx:
            return None
        js_args: list[str] = []
        for a in args:
            converted = py_to_js(a)
            try:
                js_args.append(json.dumps(converted, default=str))
            except Exception:
                js_args.append("undefined")

        script = f"{fn_name}({', '.join(js_args)})"
        raw = self.eval(script)
        return _v8_to_py(raw)

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
    # Bridge command processing
    # ─────────────────────────────────────────────────────────────────────────

    def _process_bridge_commands(self) -> None:
        """
        Process pending bridge commands and sync DOM mutations from JS.

        After each eval(), JS may have queued async commands and logged
        DOM mutations.  Process both so the Python DOM stays in sync.
        """
        if not self._ctx:
            return
        # 1. Drain bridge commands (dynamic scripts, navigations)
        try:
            raw = self._ctx.eval(
                "typeof _drainBridgeCommands === 'function'"
                " ? _drainBridgeCommands() : '[]'",
                timeout=_HOUSEKEEPING_EVAL_TIMEOUT_MS,
            )
            if raw and raw != '[]':
                commands = json.loads(raw) if isinstance(raw, str) else []
                for cmd in commands:
                    self._handle_bridge_command(cmd)
        except Exception:
            pass

        # 2. Sync DOM mutations back to Python
        try:
            from an_web.js.host_api import sync_dom_mutations
            sync_dom_mutations(self._ctx, self.session)
        except Exception:
            pass

    def _handle_bridge_command(self, cmd: dict) -> None:
        """Handle a single bridge command from JS."""
        cmd_type = cmd.get("type", "")
        if cmd_type == "dynamic_script":
            src = cmd.get("src", "")
            if src:
                pending = getattr(self.session, "_pending_dynamic_scripts", None)
                if pending is None:
                    self.session._pending_dynamic_scripts = []  # type: ignore[attr-defined]
                    pending = self.session._pending_dynamic_scripts  # type: ignore[attr-defined]
                pending.append({"src": src})
        elif cmd_type == "navigate":
            url = cmd.get("url", "")
            if url:
                self.session._pending_js_navigation = url  # type: ignore[attr-defined]
        elif cmd_type in ("fetch", "fetch_async"):
            pending_fetches = getattr(self.session, "_pending_fetches", None)
            if pending_fetches is None:
                self.session._pending_fetches = {}  # type: ignore[attr-defined]
                pending_fetches = self.session._pending_fetches  # type: ignore[attr-defined]
            rid = str(cmd.get("id") or f"auto{len(pending_fetches) + 1}")
            pending_fetches[rid] = {
                "url": cmd.get("url", ""),
                "method": cmd.get("method", "GET"),
                "body": cmd.get("body"),
                "headers_json": cmd.get("headersJson", "null"),
                "kind": cmd.get("kind", "fetch"),
                "resolved": False,
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Script tag loading
    # ─────────────────────────────────────────────────────────────────────────

    def load_script(self, source: str, src_hint: str = "<script>") -> EvalResult:
        """
        Execute a script tag source.

        Records the script in _scripts_loaded for debugging/replaying.
        Errors are logged but not raised (mirrors browser behaviour).

        Core-js polyfill bundles are handled specially — skip polyfill
        modules but keep webpack runtime.
        """
        source = source.replace("\x00", "")

        if _is_corejs_polyfill(source):
            runtime = _extract_webpack_runtime(source)
            if runtime:
                log.info(
                    "Polyfill '%s': skipping modules, injecting webpack runtime",
                    src_hint[:60],
                )
                result = self.eval_safe(runtime)
                self._scripts_loaded.append(src_hint)
                if not result.ok:
                    log.debug(
                        "Webpack runtime from '%s' threw: %s",
                        src_hint[:60], result.error,
                    )
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
        """Async variant — yields to the event loop first."""
        await asyncio.sleep(0)
        return self.load_script(source, src_hint)

    # ─────────────────────────────────────────────────────────────────────────
    # Microtask / timer drain
    # ─────────────────────────────────────────────────────────────────────────

    async def drain_microtasks(self, max_jobs: int = _MAX_MICROTASK_JOBS) -> int:
        """
        Drain pending JS microtasks and fire ready timers.

        V8 automatically flushes microtasks after each eval(), so this
        primarily handles timer callbacks. After firing timers (via eval),
        V8 auto-flushes the resulting microtasks.

        Returns:
            Approximate number of tasks processed.
        """
        if not self._available or not self._ctx:
            return 0

        # Runaway-pump backoff: a page whose timer callback spins until the
        # V8 CPU kill (pathological setTimeout pumps) would otherwise burn
        # the full 2s budget on EVERY drain. After the first fruitless kill
        # the budget drops sharply; after several, we stop trying entirely
        # for this page (fresh page resets the counter).
        if self._drain_kills >= _DRAIN_KILL_GIVE_UP:
            return 0
        timeout_ms = (
            _HOUSEKEEPING_EVAL_TIMEOUT_MS
            if self._drain_kills == 0
            else _HOUSEKEEPING_REDUCED_TIMEOUT_MS
        )

        total = 0
        try:
            # Fire any ready timers (implemented in JS).  The JS side also
            # enforces a per-call budget; this timeout is the hard backstop
            # against a single long-running timer callback.
            raw = self._ctx.eval(
                "typeof _fireReadyTimers === 'function'"
                " ? _fireReadyTimers() : 0",
                timeout=timeout_ms,
            )
            timers_fired = int(raw) if raw else 0
            total += timers_fired
            if timers_fired > 0:
                self._drain_kills = 0  # real progress → restore full budget

            # V8 auto-flushes microtasks after the eval above,
            # so .then() chains from timer callbacks are settled.

            # Process bridge commands from timer callbacks
            self._process_bridge_commands()

            # Yield to asyncio event loop
            if total > 0:
                await asyncio.sleep(0)

        except Exception as exc:
            msg = str(exc).lower()
            if "terminated" in msg or "timeout" in msg or "timed out" in msg:
                self._drain_kills += 1
                log.debug(
                    "drain_microtasks: V8 kill #%d (budget %dms)",
                    self._drain_kills, timeout_ms,
                )
            else:
                log.debug("drain_microtasks error: %s", exc)

        return total

    async def settle(
        self,
        microtask_rounds: int = 3,
        yield_between: float = 0.0,
    ) -> None:
        """Full event-loop settle: drain microtasks across multiple rounds."""
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
        """Reset the V8 context for a new page."""
        self._reset_context()

    def dispatch_dom_content_loaded(self) -> None:
        """Fire DOMContentLoaded on both document and window."""
        self.eval_safe(
            "if (document) document.readyState = 'interactive';"
            "var _dce = new Event('DOMContentLoaded');"
            "if (document && document.dispatchEvent) document.dispatchEvent(_dce);"
            "if (window && window.dispatchEvent) window.dispatchEvent(_dce);"
        )

    def dispatch_load(self) -> None:
        """Fire window load event."""
        self.eval_safe(
            "if (document) document.readyState = 'complete';"
            "if (window && window.dispatchEvent) "
            "window.dispatchEvent(new Event('load'));"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Introspection
    # ─────────────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if V8 is usable."""
        return self._available

    def memory_usage(self) -> dict[str, int]:
        """Return V8 heap statistics."""
        if not self._available or not self._ctx:
            return {}
        try:
            stats = self._ctx.heap_stats()
            return {k: v for k, v in stats.items() if isinstance(v, int)}
        except Exception:
            return {}

    @property
    def ctx(self) -> Any:
        """Direct access to the underlying MiniRacer context."""
        return self._ctx

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Release the V8 context (and its internal event-loop thread).

        Disposal is routed through the same dedicated thread that created
        the context — closing from another thread segfaults in V8.
        """
        ctx = self._ctx
        self._ctx = None
        self._available = False
        if ctx is not None:
            try:
                if _creator_pool is not None:
                    _creator_pool.submit(ctx.close).result(timeout=10)
                else:
                    ctx.close()
            except Exception:
                pass

    def __enter__(self) -> JSRuntime:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "available" if self._available else "unavailable"
        return f"JSRuntime(V8, {status}, scripts={len(self._scripts_loaded)})"
