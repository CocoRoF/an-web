"""Shared scenario definitions + helpers for the an-web vs playwright bench."""
import json
import os
import time

import psutil

SITES = {
    "static": "https://example.com",
    "wiki": "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "hn": "https://news.ycombinator.com",
    "spa": "https://hrletsgo.me",
    "form": "https://httpbin.org/forms/post",
    "api_base": "https://httpbin.org/html",
}

MICRO_ROUNDS = 20  # per-action latency micro-bench rounds


def tree_rss_mb() -> float:
    """RSS of this process + all children (chromium!), in MB."""
    p = psutil.Process(os.getpid())
    total = p.memory_info().rss
    for c in p.children(recursive=True):
        try:
            total += c.memory_info().rss
        except psutil.Error:
            pass
    return round(total / 1024 / 1024, 1)


class Recorder:
    def __init__(self, engine_name: str):
        self.engine = engine_name
        self.results = []
        self.peak_rss = 0.0

    def sample_mem(self):
        self.peak_rss = max(self.peak_rss, tree_rss_mb())

    def add(self, scenario: str, ok: bool, ms: float, **extra):
        self.sample_mem()
        row = {"scenario": scenario, "ok": bool(ok), "ms": round(ms, 1), **extra}
        self.results.append(row)
        print(f"[{self.engine}] {scenario}: ok={ok} {row['ms']}ms", flush=True)

    def finish(self, path: str, **meta):
        out = {"engine": self.engine, "peak_rss_mb": self.peak_rss, **meta,
               "results": self.results}
        with open(path, "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"[{self.engine}] wrote {path}", flush=True)


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t0) * 1000
