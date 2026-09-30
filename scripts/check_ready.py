#!/usr/bin/env python3
"""Small repository readiness check for local use and CI."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "artemis"
OWNER_TOKEN = "__" + "GITHUB_OWNER" + "__"
REPO_TOKEN = "__" + "GITHUB_REPO" + "__"

errors: list[str] = []

required = [
    ROOT / "README.md",
    ROOT / "hacs.json",
    INTEGRATION / "__init__.py",
    INTEGRATION / "manifest.json",
    INTEGRATION / "brand" / "icon.png",
]
for path in required:
    if not path.exists():
        errors.append(f"Missing required file: {path.relative_to(ROOT)}")

custom_dirs = [p for p in (ROOT / "custom_components").iterdir() if p.is_dir()]
if len(custom_dirs) != 1:
    errors.append("HACS repository must contain exactly one integration under custom_components/")

manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
for key in ("domain", "name", "version", "documentation", "issue_tracker", "codeowners"):
    if key not in manifest:
        errors.append(f"manifest.json missing key: {key}")

if manifest.get("domain") != "artemis":
    errors.append("manifest domain must be 'artemis'")

hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
if not hacs.get("name"):
    errors.append("hacs.json missing name")

placeholder_files: list[str] = []
for path in (
    ROOT / "custom_components" / "artemis" / "manifest.json",
    ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
):
    text = path.read_text(encoding="utf-8")
    if OWNER_TOKEN in text or REPO_TOKEN in text:
        placeholder_files.append(str(path.relative_to(ROOT)))

if placeholder_files:
    errors.append(
        "GitHub placeholders remain in repository metadata.\n"
        + "  " + "\n  ".join(sorted(placeholder_files))
    )

if errors:
    print("Repository is not ready:\n- " + "\n- ".join(errors), file=sys.stderr)
    raise SystemExit(1)

print("Repository metadata looks ready for HACS validation.")
