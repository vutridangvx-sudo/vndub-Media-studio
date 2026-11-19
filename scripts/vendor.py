"""Clone/cập nhật các repo trong vendor.toml vào vendor/<tên>, đúng commit đã ghim.

    python scripts/vendor.py            # chỉ repo enabled = true
    python scripts/vendor.py --all      # kèm repo tuỳ chọn
    python scripts/vendor.py postiz-app # đích danh một repo
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str, cwd: Path | None = None) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        sys.exit(f"git {' '.join(args)} lỗi:\n{p.stderr.strip()}")
    return p.stdout.strip()


def sync(name: str, spec: dict) -> None:
    dest = ROOT / "vendor" / name
    ref = spec.get("ref", "main")
    if not (dest / ".git").is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=dest)
        git("remote", "add", "origin", spec["url"], cwd=dest)
    git("fetch", "--depth", "1", "origin", ref, cwd=dest)
    git("checkout", "-q", "--force", "FETCH_HEAD", cwd=dest)
    print(f"✓ {name:<20} {git('rev-parse', '--short', 'HEAD', cwd=dest)}  ({spec.get('role', '')})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--all", action="store_true", help="cả repo enabled = false")
    args = ap.parse_args()
    specs = tomllib.loads((ROOT / "vendor.toml").read_text(encoding="utf-8"))
    chosen = {n: s for n, s in specs.items()
              if (n in args.names if args.names else (args.all or s.get("enabled", False)))}
    missing = set(args.names) - set(specs)
    if missing:
        sys.exit(f"Không có trong vendor.toml: {', '.join(sorted(missing))}")
    for name, spec in chosen.items():
        sync(name, spec)


if __name__ == "__main__":
    main()
