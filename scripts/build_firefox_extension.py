#!/usr/bin/env python3
"""Build a Firefox-loadable copy of the WebExtension source."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "extension"
DEFAULT_OUTPUT = ROOT / "build" / "firefox"
DEFAULT_BACKEND = "http://127.0.0.1:8000"


def backend_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Backend URL must be an absolute http:// or https:// URL.")
    return f"{parsed.scheme}://{parsed.netloc}/*"


def build(output: Path, backend_url: str) -> None:
    output = output.resolve()
    if output == SOURCE or SOURCE in output.parents:
        raise ValueError("Output directory must be outside extension/.")

    shutil.rmtree(output, ignore_errors=True)
    shutil.copytree(SOURCE, output)

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("minimum_chrome_version", None)
    manifest["permissions"] = [
        permission for permission in manifest.get("permissions", [])
        if permission != "sidePanel"
    ]
    manifest["background"] = {"scripts": ["background.js"]}
    manifest.pop("side_panel", None)
    manifest["sidebar_action"] = {
        "default_panel": "panel.html",
        "default_title": "TAYO",
        "open_at_install": True,
    }

    origin = backend_origin(backend_url)
    host_permissions = set(manifest.get("host_permissions", []))
    host_permissions.add(origin)
    manifest["host_permissions"] = sorted(host_permissions)
    manifest["browser_specific_settings"] = {
        "gecko": {
            "id": "tayo-firefox@local",
            "data_collection_permissions": {"required": ["websiteContent"]},
        }
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    panel_path = output / "panel.js"
    panel_source = panel_path.read_text(encoding="utf-8")
    panel_source, replacements = re.subn(
        r'^const DEFAULT_BACKEND = ".*?";$',
        f'const DEFAULT_BACKEND = "{backend_url.rstrip("/")}";',
        panel_source,
        count=1,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        raise ValueError("Could not set the Firefox build's default backend URL.")
    panel_path.write_text(panel_source, encoding="utf-8")
    print(f"Firefox extension built at {output}")
    print(f"Backend URL: {backend_url.rstrip('/')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND)
    args = parser.parse_args()
    build(args.output, args.backend_url)


if __name__ == "__main__":
    main()
