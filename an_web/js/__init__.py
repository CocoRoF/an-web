"""
JavaScript runtime bridge for AN-Web.

Uses QuickJS as the embedded JS engine (vs V8 in Lightpanda).
The critical insight: the JS engine choice matters less than the
host Web API implementation quality.

Modules:
    runtime   - QuickJS runtime wrapper (JSRuntime)
    host_api  - document/window/fetch/timer host environment
    bridge    - JS <-> Python object marshalling
    timers    - setTimeout/queueMicrotask/Promise drain
"""
