#!/usr/bin/env python3
"""Small repository readiness check for local use and CI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "artemis"
OWNER_TOKEN = "__" + "GITHUB_OWNER" + "__"
REPO_TOKEN = "__" + "GITHUB_REPO" + "__"

errors: list[str] = []

required = [
    ROOT / "README.md",
    ROOT / "hacs.json",
    ROOT / "automation_status_notification.example.yaml",
    ROOT / "automation_interventions.example.yaml",
    ROOT / "dashboard_cycle_button.example.yaml",
    INTEGRATION / "__init__.py",
    INTEGRATION / "manifest.json",
    INTEGRATION / "brand" / "icon.png",
]
for path in required:
    if not path.exists():
        errors.append(f"Missing required file: {path.relative_to(ROOT)}")


obsolete = [
    INTEGRATION / "binary_sensor.py",
    ROOT / "automation_notification.example.yaml",
    ROOT / "automation_persistent_status.example.yaml",
    ROOT / "automation_intervention_status.example.yaml",
]
for path in obsolete:
    if path.exists():
        errors.append(f"Obsolete 0.5.0 file still present: {path.relative_to(ROOT)}")

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
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    if OWNER_TOKEN in text or REPO_TOKEN in text:
        placeholder_files.append(str(path.relative_to(ROOT)))

if placeholder_files:
    errors.append(
        "GitHub placeholders remain in repository metadata.\n"
        + "  " + "\n  ".join(sorted(placeholder_files))
    )

tracked = subprocess.run(
    ["git", "ls-files"],
    cwd=ROOT,
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
junk = [
    path
    for path in tracked
    if "__pycache__/" in path
    or path.endswith((".pyc", ".pyo"))
    or path
    in {
        "custom_components/CHANGELOG.md",
        "custom_components/CONTRIBUTING.md",
        "custom_components/automation_interventions.example.yaml",
        "custom_components/automation_status_notification.example.yaml",
    }
]
if junk:
    errors.append("Generated/duplicate files are tracked:\n  " + "\n  ".join(junk))

if errors:
    print("Repository is not ready:\n- " + "\n- ".join(errors), file=sys.stderr)
    raise SystemExit(1)

print("Repository metadata looks ready for HACS validation.")
