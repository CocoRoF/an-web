"""
Artifact and tracing layer for AN-Web.

Structured evidence is more important than screenshots for AI tooling.
Every action produces a deterministic, replayable trace.

Modules:
    artifacts  - DOM/semantic snapshots, network traces, JS exception logs
    logs       - Structured logging with action context
    replay     - Deterministic replay from saved traces
"""
