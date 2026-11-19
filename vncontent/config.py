"""Đọc ``config.toml`` (trộn lên mặc định) và quyết định thư mục dữ liệu."""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "paths": {"media_root": ""},
    "tools": {"vndub_python": "", "vndub_dir": "", "vndub_env": {}, "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
              "sau": ""},
    "ingest": {"format": "bv*[height<=1080]+ba/b[height<=1080]", "sub_langs": ["vi", "en"],
               "use_auto_subs": False},
    "dub": {"target": "tiếng Việt", "target_lang": "vi", "extra_args": []},
    "render": {"width": 1080, "height": 1920, "mode": "blur", "max_seconds": 59, "burn_subs": True,
               "encoder": "libx264", "crf": 20},
    # Chế độ truyện audio (job kind = story): chương .txt -> giọng đọc -> video ngang có hình -> bản dọc cliffhanger
    "story": {"preset": "tien-hiep-hac-am", "presets_dir": "", "width": 1920, "height": 1080, "fps": 25,
              "chapter_gap": "[nghỉ 1s]", "speak_args": [],
              "midroll_minutes": 10, "short_seconds": 59, "short_end_fraction": 0.9,
              "encoder": "libx264", "crf": 21, "cleanup_scenes": True},
    "meta": {"hashtags": ["#shorts"], "credit": True},
    "publish": {"platforms": ["youtube"], "visibility": "private", "interval_hours": 6, "headless": True,
                "accounts": {"youtube": "main", "facebook": "main", "instagram": "main"}},
}


class ConfigError(RuntimeError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@dataclass
class Config:
    raw: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    source: Path | None = None

    def __getitem__(self, section: str) -> dict:
        return self.raw[section]

    # ---- thư mục dữ liệu --------------------------------------------------------------------
    def media_root(self) -> Path:
        """``paths.media_root`` > ``VNDUB_MEDIA`` > ``<repo>/data``. Ổ không có (SSD ngoài chưa cắm) -> lỗi rõ ràng."""
        raw = (self.raw["paths"]["media_root"] or os.environ.get("VNDUB_MEDIA", "")).strip().strip('"')
        root = Path(raw).expanduser() if raw else REPO / "data"
        if root.anchor and not Path(root.anchor).exists():
            raise ConfigError(f"Thư mục dữ liệu {root} nằm trên ổ {root.anchor} không có — cắm SSD ngoài "
                              "hoặc đổi [paths].media_root / biến VNDUB_MEDIA.")
        return root

    def content_dir(self) -> Path:
        return self.media_root() / "content"

    def job_dir(self, job_id: int) -> Path:
        return self.content_dir() / "jobs" / f"{job_id:04d}"

    def presets_dir(self) -> Path:
        d = self.raw["story"]["presets_dir"]
        return Path(d) if d else REPO / "presets" / "genres"

    def assets_dir(self, preset_id: str) -> Path:
        """Kho ảnh/nhạc nền theo thể loại, nằm trên ổ dữ liệu (không nằm trong repo)."""
        return self.content_dir() / "assets" / preset_id

    def db_path(self) -> Path:
        return self.content_dir() / "state.db"

    # ---- công cụ ngoài ---------------------------------------------------------------------
    def vndub_python(self) -> str:
        p = os.environ.get("VNC_VNDUB_PYTHON") or self.raw["tools"]["vndub_python"]
        if not p:
            raise ConfigError("Chưa đặt [tools].vndub_python (Python có cài vndub) trong config.toml.")
        return p

    def vndub_cwd(self) -> Path | None:
        d = self.raw["tools"]["vndub_dir"]
        return Path(d) if d else None

    def vndub_env(self) -> dict[str, str]:
        """Môi trường cho tiến trình vndub: môi trường hiện tại + ``[tools].vndub_env`` (ví dụ HF_HOME)."""
        return {**os.environ, **{k: str(v) for k, v in self.raw["tools"]["vndub_env"].items()}}

    def sau(self) -> str:
        p = self.raw["tools"]["sau"]
        if p:
            return p
        exe = REPO / "vendor" / "social-auto-upload" / ".venv" / ("Scripts/sau.exe" if os.name == "nt" else "bin/sau")
        return str(exe)

    def sau_cwd(self) -> Path:
        return REPO / "vendor" / "social-auto-upload"


def load(path: str | Path | None = None) -> Config:
    candidates = [path, os.environ.get("VNC_CONFIG"), REPO / "config.toml", Path("config.toml")]
    for c in candidates:
        if c and Path(c).is_file():
            with open(c, "rb") as f:
                return Config(_merge(DEFAULTS, tomllib.load(f)), Path(c))
        if c and c is path:
            raise ConfigError(f"Không thấy file cấu hình: {c}")
    return Config()
