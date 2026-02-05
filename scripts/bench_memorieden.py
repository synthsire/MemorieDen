#!/usr/bin/env python3
"""Lightweight benchmark runner for MemorieDen endpoints.

Measures add + search latency for v1/v2/v3 and writes JSONL results for graphing.

Outputs: /home/radxa/.openclaw/workspace/memorieden_bench/results.jsonl

Usage:
  python3 bench_memorieden.py --runs 50
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import time
import urllib.request

OUT_PATH = "/home/radxa/.openclaw/workspace/memorieden_bench/results.jsonl"

TARGETS = {
    "v1": "http://127.0.0.1:18001",
    "v2": "http://127.0.0.1:18002",
    "v3": "http://127.0.0.1:18003",
    "v4": "http://127.0.0.1:18004",
    "v5": "http://127.0.0.1:18005",
    "v6": "http://127.0.0.1:18006",
}


def post_json(url: str, payload: dict, timeout=20) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(url: str, timeout=20) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def rand_text(n=40):
    return "".join(random.choice(string.ascii_lowercase) for _ in range(n))


def now_ms():
    return int(time.time() * 1000)


def time_call(fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    dt_ms = (time.perf_counter() - t0) * 1000
    return dt_ms, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--user", default="coriana")
    ap.add_argument("--query", default="MemorieDen")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    # sanity health
    health = {}
    for name, base in TARGETS.items():
        try:
            ms, resp = time_call(get_json, base + "/health")
            health[name] = {"ms": ms, "resp": resp}
        except Exception as e:
            health[name] = {"error": str(e)}

    with open(OUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_ms": now_ms(), "event": "health", "health": health}) + "\n")

        for name, base in TARGETS.items():
            # add
            for i in range(args.runs):
                content = f"bench {name} {i} {rand_text()}"
                # Some DBs may have UNIQUE(source,title); ensure titles are unique.
                payload = {"content": content, "user_id": args.user, "source": "bench", "title": f"bench-{name}-{i}-{rand_text(8)}", "metadata": {"tags": ["bench"], "v": name}}
                try:
                    ms, _ = time_call(post_json, base + "/memories/add", payload)
                    f.write(json.dumps({"ts_ms": now_ms(), "event": "add", "target": name, "ms": ms}) + "\n")
                except Exception as e:
                    f.write(json.dumps({"ts_ms": now_ms(), "event": "add", "target": name, "error": str(e)}) + "\n")

            # search
            for i in range(args.runs):
                payload = {"query": args.query, "user_id": args.user, "limit": 10}
                try:
                    ms, _ = time_call(post_json, base + "/memories/search", payload)
                    f.write(json.dumps({"ts_ms": now_ms(), "event": "search", "target": name, "ms": ms}) + "\n")
                except Exception as e:
                    f.write(json.dumps({"ts_ms": now_ms(), "event": "search", "target": name, "error": str(e)}) + "\n")

    print(f"Wrote: {OUT_PATH}")


if __name__ == "__main__":
    main()
