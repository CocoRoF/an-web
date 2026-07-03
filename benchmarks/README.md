# Benchmarks

Reproducible harness behind the numbers in the main README ("Benchmarks vs
Playwright"). Both engines run the same sites with the same success criteria;
failures are recorded as failures.

## Setup

```bash
pip install an-web playwright psutil
playwright install chromium
```

## Run

```bash
# 10 famous sites, one engine at a time (sequential = fair network conditions)
python bench_famous.py anweb   # writes famous_anweb.json
python bench_famous.py pw      # writes famous_pw.json

# 7-scenario functional bench (static/wiki/HN/SPA/form/api/micro-latency)
python bench_anweb.py          # writes results_anweb.json
python bench_pw.py             # writes results_pw.json
```

Metrics collected: wall time per site, cold start, warm per-action latency,
peak process-tree RSS (Chromium child processes included via psutil), extracted
title/text/link counts, and an agent-facing snapshot size comparison
(semantic tree vs aria snapshot).

Published results: 2026-07-03, Ubuntu 24.04, Python 3.12.3,
an-web 0.8.0 vs playwright 1.61.0 (Chromium 1228 headless shell).
