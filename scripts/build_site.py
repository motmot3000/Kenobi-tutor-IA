#!/usr/bin/env python3
"""Assemble the web version (static files only) into a folder, ready for any static hosting.

    python3 scripts/build_site.py              # → _site/
    python3 scripts/build_site.py --out DIR

Used by the GitHub Pages workflow. The site holds the interface, the in-browser backend (browser/), the assets, the
built-in course, and the two bridges that the page offers for download (terminal and local voice). Nothing else:
no Python server code, no tests, no data of the computer that builds it.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ("index.html", "config.js", "styles.css", "core.js", "editor.js", "terminal.js", "vad.js", "settings.js",
         "call.js", "vad-worklet.js", "tour.js", "app.js", "terminal_bridge.py", "voice_bridge.py", "LICENSE")
FOLDERS = ("assets", "browser")
DATA = ("data/default-state.json", "data/plan-formation-python.md")


def build(out):
    check = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_browser.py"), "--check"], cwd=ROOT)
    if check.returncode:
        raise SystemExit("browser/shared.js is out of date: run python3 scripts/build_browser.py")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(ROOT / name, out / name)
    for name in FOLDERS:
        shutil.copytree(ROOT / name, out / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    for name in DATA:
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, out / name)
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve every file as it is (GitHub Pages)
    config = (out / "config.js").read_text(encoding="utf-8")
    if 'window.KENOBI_BACKEND = "browser";' not in config:
        raise SystemExit("config.js must select the browser backend for a static site")
    count = sum(1 for path in out.rglob("*") if path.is_file())
    print(f"{out}: {count} files")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "_site"), help="output folder (default: _site)")
    build(Path(parser.parse_args().out).resolve())


if __name__ == "__main__":
    main()
