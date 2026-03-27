"""
JavaScript runtime bridge for AN-Web.

Modules:
    runtime   - V8 runtime wrapper via PyMiniRacer (JSRuntime)
    host_api  - document/window/fetch/timer host environment
    bridge    - JS <-> Python object marshalling
    timers    - setTimeout/queueMicrotask/Promise drain
"""
