#!/usr/bin/env python3
"""Prepare an exported n8n workflow for deploy to the target (prod) instance.

Usage: render_workflow.py <workflow.json> <output.json> [<target-credentials.json>]

1. Fills __PLACEHOLDERS__:
   - built-ins: __GIT_SHA__ (short commit SHA), __DEPLOYED_AT__ (UTC time)
   - GitHub secrets passed as JSON in SECRETS_JSON: __ALLOWED_CHAT_ID__ -> secret ALLOWED_CHAT_ID
2. Re-points credentials: the workflow is exported from dev, so its nodes reference
   dev credential IDs. Each reference is replaced with the ID of the target credential
   that has the same name and type. <target-credentials.json> is a list of
   {"id", "name", "type"} taken from the target instance.

Fails (exit 1) if a placeholder has no value or a credential is missing on the target,
so a half-configured workflow is never deployed. Prints the workflow ID to stdout.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

PLACEHOLDER = re.compile(r"__([A-Z][A-Z0-9_]*)__")


def fill_placeholders(text: str) -> str:
    values = json.loads(os.environ.get("SECRETS_JSON") or "{}")
    values["GIT_SHA"] = os.environ.get("GIT_SHA", "unknown")[:7]
    values["DEPLOYED_AT"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    missing = sorted({m for m in PLACEHOLDER.findall(text) if not values.get(m)})
    if missing:
        sys.exit("Missing values for placeholders (add them as GitHub secrets): " + ", ".join(missing))

    # json.dumps(...)[1:-1] escapes quotes/backslashes so the result stays valid JSON
    return PLACEHOLDER.sub(lambda m: json.dumps(str(values[m.group(1)]))[1:-1], text)


def remap_credentials(workflow: dict, target_creds: list) -> None:
    by_name_type = {(c["name"], c["type"]): c["id"] for c in target_creds}
    missing = set()
    for node in workflow.get("nodes", []):
        for cred_type, ref in (node.get("credentials") or {}).items():
            target_id = by_name_type.get((ref.get("name"), cred_type))
            if target_id:
                ref["id"] = target_id
            else:
                missing.add(f"'{ref.get('name')}' ({cred_type}) used by node '{node.get('name')}'")
    if missing:
        sys.exit("Credentials not found on the target n8n (create them there with the same name):\n  "
                 + "\n  ".join(sorted(missing)))


def main() -> int:
    # Windows consoles/pipes default to cp1252, which cannot print ✓/✗ or Cyrillic
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    src, dst = sys.argv[1], sys.argv[2]
    creds_file = sys.argv[3] if len(sys.argv) > 3 else None

    workflow = json.loads(fill_placeholders(open(src, encoding="utf-8").read()))
    if creds_file:
        remap_credentials(workflow, json.load(open(creds_file, encoding="utf-8")))

    with open(dst, "w", encoding="utf-8") as f:
        json.dump(workflow, f, ensure_ascii=False)
    print(workflow["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
