#!/usr/bin/env python3
"""Minimal helper CLI for MemorieDen.

Examples:
  python3 scripts/memorieden_cli.py health
  python3 scripts/memorieden_cli.py add --title "note" --content "hello" --user zex --source manual
  python3 scripts/memorieden_cli.py search --query "moltbook" --user zex
"""

import argparse
import json
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:18001"


def req(method: str, url: str, payload=None):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")

    ap_add = sub.add_parser("add")
    ap_add.add_argument("--title")
    ap_add.add_argument("--content", required=True)
    ap_add.add_argument("--user")
    ap_add.add_argument("--source")

    ap_search = sub.add_parser("search")
    ap_search.add_argument("--query", required=True)
    ap_search.add_argument("--user")
    ap_search.add_argument("--limit", type=int, default=10)

    args = ap.parse_args()

    if args.cmd == "health":
        out = req("GET", args.base + "/health")
    elif args.cmd == "add":
        out = req(
            "POST",
            args.base + "/memories/add",
            {
                "title": args.title,
                "content": args.content,
                "user_id": args.user,
                "source": args.source,
            },
        )
    elif args.cmd == "search":
        out = req(
            "POST",
            args.base + "/memories/search",
            {"query": args.query, "user_id": args.user, "limit": args.limit},
        )
    else:
        raise SystemExit("unknown cmd")

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
