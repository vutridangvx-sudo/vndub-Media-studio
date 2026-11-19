"""Chạy lệnh ngoài (yt-dlp, vndub, ffmpeg, sau) — một chỗ duy nhất để in lệnh, ghi log và báo lỗi."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Sequence


class StageError(RuntimeError):
    pass


class Runner:
    """``dry_run``: chỉ in lệnh, không chạy. ``log``: file ghi lại toàn bộ đầu ra của job."""

    def __init__(self, dry_run: bool = False, log: Path | None = None,
                 echo: Callable[[str], None] = print):
        self.dry_run = dry_run
        self.log = log
        self.echo = echo
        self.commands: list[list[str]] = []

    def __call__(self, cmd: Sequence[str | Path], cwd: Path | None = None, env: dict | None = None) -> str:
        argv = [str(c) for c in cmd]
        self.commands.append(argv)
        self.echo("  $ " + subprocess.list2cmdline(argv))
        if self.dry_run:
            return ""
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            raise StageError(f"Không tìm thấy chương trình: {argv[0]} ({e}). Chạy scripts/bootstrap.ps1 "
                             "hoặc chỉnh [tools] trong config.toml.") from e
        if self.log:
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(f"$ {subprocess.list2cmdline(argv)}\n{p.stdout}\n{p.stderr}\n[exit {p.returncode}]\n\n")
        if p.returncode != 0:
            tail = "\n".join((p.stderr or p.stdout).strip().splitlines()[-12:])
            raise StageError(f"{Path(argv[0]).name} thoát với mã {p.returncode}:\n{tail}")
        return p.stdout
