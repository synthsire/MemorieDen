#!/usr/bin/env python3
"""Multi-MemorieDen helper CLI.

Supports:
- v1 (OpenClaw local): http://127.0.0.1:18001
  - GET  /health
  - POST /memories/add    {title, content, user_id, source}
  - POST /memories/search {query, user_id, limit}

- v2 (Coriana dockerized): http://127.0.0.1:18002
  - GET  /users/all (used as health)
  - POST /memories/add    {content, user_id, metadata}
  - GET  /memories/search?query=...&user_id=...

Examples:
  python3 memorieden_multi_cli.py health --which v1
  python3 memorieden_multi_cli.py health --which v2
  python3 memorieden_multi_cli.py add --which both --user coriana --content "hello" --title "optional" --source zex
  python3 memorieden_multi_cli.py search --which both --user coriana --query "moltbook" --limit 5
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

V1 = "http://127.0.0.1:18001"
V2 = "http://127.0.0.1:18002"


def req_json(method: str, url: str, payload=None, timeout=20):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def health_v1():
    return req_json("GET", V1 + "/health")


def health_v2():
    # Prefer dedicated health endpoint when present; fall back to /users/all.
    try:
        return req_json("GET", V2 + "/health")
    except Exception:
        return req_json("GET", V2 + "/users/all")


def add_v1(title, content, user, source):
    return req_json(
        "POST",
        V1 + "/memories/add",
        {"title": title, "content": content, "user_id": user, "source": source},
    )


def add_v2(content, user, metadata):
    return req_json(
        "POST",
        V2 + "/memories/add",
        {"content": content, "user_id": user, "metadata": metadata},
    )


def search_v1(query, user, limit):
    return req_json(
        "POST",
        V1 + "/memories/search",
        {"query": query, "user_id": user, "limit": limit},
    )


def search_v2(query, user, limit=None):
    # Prefer v1-compatible POST endpoint if available; fall back to legacy GET.
    payload = {"query": query, "user_id": user}
    if limit is not None:
        payload["limit"] = limit
    try:
        return req_json("POST", V2 + "/memories/search", payload)
    except Exception:
        qs = urllib.parse.urlencode({"query": query, "user_id": user})
        return req_json("GET", V2 + "/memories/search?" + qs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=["v1", "v2", "both"], default="both")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")

    ap_add = sub.add_parser("add")
    ap_add.add_argument("--title", default=None)
    ap_add.add_argument("--content", required=True)
    ap_add.add_argument("--user", default="coriana")
    ap_add.add_argument("--source", default="zex")
    ap_add.add_argument("--metadata", default=None, help="JSON string for v2 metadata")

    ap_search = sub.add_parser("search")
    ap_search.add_argument("--query", required=True)
    ap_search.add_argument("--user", default="coriana")
    ap_search.add_argument("--limit", type=int, default=10)

    args = ap.parse_args()

    out = {"which": args.which, "cmd": args.cmd}

    if args.cmd == "health":
        if args.which in ("v1", "both"):
            t0 = time.time()
            out["v1"] = {"ms": int((time.time() - t0) * 1000), "resp": health_v1()}
        if args.which in ("v2", "both"):
            t0 = time.time()
            out["v2"] = {"ms": int((time.time() - t0) * 1000), "resp": health_v2()}

    elif args.cmd == "add":
        metadata = None
        if args.metadata:
            metadata = json.loads(args.metadata)
        else:
            metadata = {"source": args.source}

        if args.which in ("v1", "both"):
            t0 = time.time()
            out["v1"] = {"ms": int((time.time() - t0) * 1000), "resp": add_v1(args.title, args.content, args.user, args.source)}
        if args.which in ("v2", "both"):
            t0 = time.time()
            out["v2"] = {"ms": int((time.time() - t0) * 1000), "resp": add_v2(args.content, args.user, metadata)}

    elif args.cmd == "search":
        if args.which in ("v1", "both"):
            t0 = time.time()
            out["v1"] = {"ms": int((time.time() - t0) * 1000), "resp": search_v1(args.query, args.user, args.limit)}
        if args.which in ("v2", "both"):
            t0 = time.time()
            out["v2"] = {"ms": int((time.time() - t0) * 1000), "resp": search_v2(args.query, args.user, args.limit)}

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
