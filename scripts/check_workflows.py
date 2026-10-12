#!/usr/bin/env python3
"""Validate exported n8n workflows before they are committed or deployed.

Checks every */workflow.json (and infra/*-workflow.json):
  - the file is valid JSON with the shape of an n8n workflow;
  - no pinned test data is left in the export;
  - no values that look like secrets or real Telegram chat IDs are hardcoded
    (these must be placeholders like __ALLOWED_CHAT_ID__, filled in by CI).

Exit code 1 if any problem is found.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = {
    "Telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "OpenAI/OpenRouter key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
# Keys whose values must be a __PLACEHOLDER__ (or an expression), never a literal
PLACEHOLDER_KEYS = re.compile(r"chat_?id", re.IGNORECASE)
PLACEHOLDER = re.compile(r"^__[A-Z0-9_]+__$")


def walk(node, path=""):
    if isinstance(node, dict):
        # Edit Fields (Set) node stores fields as {"name": "...", "value": "..."}
        name = node.get("name")
        if isinstance(name, str) and PLACEHOLDER_KEYS.search(name) and "value" in node:
            yield path + ".value", name, node["value"]
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}")
            if isinstance(v, (str, int)) and PLACEHOLDER_KEYS.search(k):
                yield path + "." + k, k, v
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def check(file: Path) -> list[str]:
    problems = []
    text = file.read_text(encoding="utf-8")
    try:
        wf = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]

    if not isinstance(wf, dict) or "nodes" not in wf or "connections" not in wf:
        problems.append("not an n8n workflow (missing 'nodes' or 'connections')")
    if not wf.get("id"):
        problems.append("missing workflow 'id' (needed so re-import updates instead of duplicating)")
    if wf.get("pinData"):
        problems.append("contains pinData (pinned test data) — remove before committing")

    for name, rx in PATTERNS.items():
        if rx.search(text):
            problems.append(f"looks like a hardcoded {name}")

    for path, key, value in walk(wf):
        s = str(value)
        if s.startswith("=") or PLACEHOLDER.match(s) or s == "":
            continue
        problems.append(f"{path}: '{key}' must be a placeholder like __ALLOWED_CHAT_ID__, got a literal")

    return problems


def main() -> int:
    files = sorted(ROOT.glob("*/workflow.json")) + sorted(ROOT.glob("infra/*-workflow.json"))
    if not files:
        print("No workflow files found yet — nothing to check.")
        return 0

    failed = False
    for f in files:
        problems = check(f)
        rel = f.relative_to(ROOT)
        if problems:
            failed = True
            print(f"✗ {rel}")
            for p in problems:
                print(f"    - {p}")
        else:
            print(f"✓ {rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
