"""Năm bước của pipeline. Mỗi bước: ``fn(job, cfg, run, **opts) -> dict`` (đường dẫn kết quả, được ghi vào job.data).

(Job kind=story: truyện audio — xem story.py; mỗi bước dưới đây chuyển sang bản của nó.)

ingest  : yt-dlp tải video + phụ đề + info.json (hoặc dùng file có sẵn)
dub     : vndub lồng tiếng (dub nếu có phụ đề, dub-video nếu không) -> video đã lồng tiếng
render  : ffmpeg dựng dọc 9:16, cháy phụ đề, cắt clip <= max_seconds, lấy thumbnail
meta    : tiêu đề / mô tả / hashtag từng clip -> meta.json (sửa tay được trước khi đăng)
publish : social-auto-upload (`sau`) đăng từng clip; không có --yes thì chỉ in lệnh
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import Config
from .runner import Runner, StageError
from .store import Job

VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".avi")

# Nền tảng mà `sau` có CLI upload-video. Khoá: có hỗ trợ tuỳ chọn riêng hay không.
PLATFORMS: dict[str, dict[str, bool]] = {
    "youtube":   {"visibility": True,  "thumbnail": True,  "schedule": True},
    "facebook":  {"visibility": False, "thumbnail": False, "schedule": False},
    "instagram": {"visibility": False, "thumbnail": True,  "schedule": False},
}


# --------------------------------------------------------------------------------------------
# helpers (thuần, có test)
# --------------------------------------------------------------------------------------------

def same_lang(a: str, b: str) -> bool:
    """``vi`` == ``vi-VN``; ngôn ngữ chưa biết (rỗng) -> False (coi như cần dịch)."""
    return bool(a) and a.lower().split("-")[0] == b.lower().split("-")[0]


def pick_sub(subs: dict[str, Path], prefer: list[str]) -> tuple[str, Path] | None:
    """Chọn phụ đề theo thứ tự ưu tiên ngôn ngữ (``en`` khớp cả ``en-US``)."""
    for want in prefer:
        for lang, path in sorted(subs.items()):
            if lang == want or lang.startswith(want + "-") or lang.startswith(want + "."):
                return lang, path
    return None


def video_filter(w: int, h: int, mode: str, subs: str | None) -> str:
    """Đồ thị lọc ffmpeg dựng khung dọc. ``subs`` là TÊN file .srt nằm trong thư mục chạy (tránh lỗi escape ổ đĩa)."""
    if mode == "crop":
        g = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        g = (f"[0:v]split=2[a][b];"
             f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:5[bg];"
             f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
             f"[bg][fg]overlay=(W-w)/2:(H-h)/2")
    if subs:
        # SRT -> ASS của ffmpeg dùng hệ toạ độ 288 px cao rồi libass phóng lên theo chiều cao video, nên đổi
        # từ pixel thật (chữ ~5,4% bề rộng, cách đáy ~17% chiều cao) sang hệ 288. (original_size KHÔNG sửa được việc này.)
        k = 288 / h
        fs, mv = max(1, round(w * 0.054 * k)), round(h * 0.17 * k)
        g += (f",subtitles={subs}:force_style="
              f"'FontName=Arial,FontSize={fs},Outline=1,Shadow=0,Alignment=2,MarginV={mv}'")
    return g + "[v]"


def encoder_args(r: dict) -> list[str]:
    if r["encoder"] == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", str(int(r["crf"]) + 3)]
    return ["-c:v", r["encoder"], "-preset", "medium", "-crf", str(r["crf"])]


def clip_text(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_meta(base_title: str, n_parts: int, *, hashtags: list[str], credit: bool, uploader: str = "",
               url: str = "", rights: str = "", thumb: str = "", parts: list[str]) -> list[dict]:
    tags = [t.lstrip("#") for t in hashtags if t.strip("#")]
    lines: list[str] = []
    if credit and url:
        lines.append(f"Nguồn: {uploader + ' — ' if uploader else ''}{url}")
    if credit and rights:
        lines.append(f"Quyền sử dụng: {rights}")
    if tags:
        lines.append(" ".join("#" + t for t in tags))
    out = []
    for i, p in enumerate(parts, 1):
        title = base_title if n_parts == 1 else f"{base_title} (Phần {i}/{n_parts})"
        out.append({"file": p, "title": clip_text(title, 100), "desc": "\n".join(lines), "tags": tags,
                    "thumbnail": thumb if i == 1 else ""})
    return out


def sau_command(cfg: Config, platform: str, entry: dict, *, index: int, start: datetime | None) -> list[str]:
    pub = cfg["publish"]
    caps = PLATFORMS[platform]
    account = pub["accounts"].get(platform)
    if not account:
        raise StageError(f"Chưa có tài khoản cho {platform} trong [publish.accounts] (config.toml).")
    cmd = [cfg.sau(), platform, "upload-video", "--account", account, "--file", entry["file"],
           "--title", entry["title"], "--desc", entry["desc"], "--tags", ",".join(entry["tags"])]
    if caps["visibility"]:
        cmd += ["--visibility", pub["visibility"]]
    if caps["thumbnail"] and entry.get("thumbnail"):
        cmd += ["--thumbnail", entry["thumbnail"]]
    if start is not None:
        if not caps["schedule"]:
            raise StageError(f"{platform}: sau chưa hỗ trợ hẹn giờ qua CLI — bỏ --start hoặc đăng ngay.")
        when = start + timedelta(hours=float(pub["interval_hours"]) * index)
        cmd += ["--schedule", when.strftime("%Y-%m-%d %H:%M")]
    if pub["headless"]:
        cmd += ["--headless"]
    return cmd


# --------------------------------------------------------------------------------------------
# các bước
# --------------------------------------------------------------------------------------------

def _story(name: str, job: Job, cfg: Config, run: Runner) -> dict:
    """Job truyện audio (kind = story) đi qua cùng năm bước nhưng chạy bản của story.py."""
    from . import story
    return getattr(story, name)(job, cfg, run)


def ingest(job: Job, cfg: Config, run: Runner, **_) -> dict:
    if job.kind == "story":
        return _story("ingest", job, cfg, run)
    if job.kind == "file":
        video = Path(job.source)
        if not video.is_file():
            raise StageError(f"Không thấy file video: {video}")
        srt = job.data.get("srt", "")
        if srt:
            if not Path(srt).is_file():
                raise StageError(f"Không thấy file phụ đề: {srt}")
            # Chép vào thư mục job: vndub ghi cast.json/.aligned.srt cạnh phụ đề, đừng để chúng rơi vào thư mục của bạn.
            local = cfg.job_dir(job.id) / "source" / Path(srt).name
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(srt, local)
            srt = str(local)
        return {"video": str(video.resolve()), "srt": srt, "src_lang": job.data.get("lang", ""),
                "title": video.stem}

    if not job.rights.strip():
        raise StageError("Job tải từ URL cần ghi chú quyền sử dụng (--rights) khi `vnc add`.")
    c = cfg["ingest"]
    out = cfg.job_dir(job.id) / "source"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "yt_dlp", "-f", c["format"], "--merge-output-format", "mp4", "--no-playlist",
           "--write-info-json", "--write-subs", "--sub-langs", ",".join(c["sub_langs"]),
           "--convert-subs", "srt", "-o", str(out / "source.%(ext)s")]
    if c["use_auto_subs"]:
        cmd.append("--write-auto-subs")
    run(cmd + [job.source])

    videos = sorted(p for p in out.glob("source.*") if p.suffix.lower() in VIDEO_EXT)
    if not videos:
        raise StageError("yt-dlp không tạo ra file video nào.")
    subs = {p.suffixes[-2].lstrip("."): p for p in out.glob("source.*.srt") if len(p.suffixes) >= 2}
    chosen = pick_sub(subs, list(c["sub_langs"]))
    info_file = out / "source.info.json"
    info = json.loads(info_file.read_text(encoding="utf-8")) if info_file.is_file() else {}
    return {"video": str(videos[0]), "srt": str(chosen[1]) if chosen else "", "src_lang": chosen[0] if chosen else "",
            "title": info.get("title", ""), "uploader": info.get("uploader", ""),
            "webpage_url": info.get("webpage_url", job.source), "license": info.get("license") or "",
            "duration": info.get("duration", 0)}


def dub(job: Job, cfg: Config, run: Runner, **_) -> dict:
    if job.kind == "story":
        return _story("dub", job, cfg, run)
    d = job.data
    out = cfg.job_dir(job.id) / "dub" / "voice.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    c = cfg["dub"]
    base = [cfg.vndub_python(), "-m", "vndub.cli"]
    srt = d.get("srt", "")
    if srt:
        cmd = base + ["dub", srt, "--video", d["video"], "-o", str(out)]
        if not same_lang(d.get("src_lang", ""), c["target_lang"]):
            cmd += ["--to", c["target"]]
    else:   # không có phụ đề: Gemini chép lời + dịch (cần GEMINI_API_KEY trong .env của vndub)
        cmd = base + ["dub-video", d["video"], "-o", str(out), "--to", c["target"]]
    if d.get("series"):   # cùng series = cùng bảng phân vai: mỗi nhân vật giữ một giọng qua mọi tập
        cast = cfg.content_dir() / "series" / d["series"] / "cast.json"
        cast.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--cast", str(cast)]
    run(cmd + list(c["extra_args"]), cwd=cfg.vndub_cwd(), env=cfg.vndub_env())
    video = out.with_name("voice_dub" + Path(d["video"]).suffix)
    if not video.is_file():
        raise StageError(f"vndub chạy xong nhưng không thấy video lồng tiếng: {video}")
    return {"dub_audio": str(out), "dub_srt": str(out.with_suffix(".srt")), "dub_video": str(video)}


def render(job: Job, cfg: Config, run: Runner, **_) -> dict:
    if job.kind == "story":
        return _story("render", job, cfg, run)
    d, r = job.data, cfg["render"]
    rd = cfg.job_dir(job.id) / "render"
    rd.mkdir(parents=True, exist_ok=True)
    for old in rd.glob("part*.mp4"):
        old.unlink()
    subs = None
    if r["burn_subs"] and d.get("dub_srt") and Path(d["dub_srt"]).is_file():
        shutil.copyfile(d["dub_srt"], rd / "subs.srt")
        subs = "subs.srt"
    ff, n = cfg["tools"]["ffmpeg"], int(r["max_seconds"])
    cmd = [ff, "-y", "-loglevel", "error", "-i", d["dub_video"],
           "-filter_complex", video_filter(int(r["width"]), int(r["height"]), r["mode"], subs),
           "-map", "[v]", "-map", "0:a:0", "-pix_fmt", "yuv420p", *encoder_args(r),
           "-c:a", "aac", "-b:a", "192k",
           "-force_key_frames", f"expr:gte(t,n_forced*{n})",
           "-f", "segment", "-segment_time", str(n), "-reset_timestamps", "1", "-segment_format", "mp4",
           "-segment_format_options", "movflags=+faststart", "part%02d.mp4"]
    run(cmd, cwd=rd)
    parts = sorted(rd.glob("part*.mp4"))
    if not parts:
        raise StageError("ffmpeg không tạo ra clip nào.")
    thumb = rd / "thumb.jpg"
    run([ff, "-y", "-loglevel", "error", "-ss", "1", "-i", str(parts[0]), "-frames:v", "1", "-q:v", "2", str(thumb)])
    return {"parts": [str(p) for p in parts], "thumb": str(thumb) if thumb.is_file() else ""}


def meta(job: Job, cfg: Config, run: Runner, **_) -> dict:
    if job.kind == "story":
        return _story("meta", job, cfg, run)
    d, m = job.data, cfg["meta"]
    base = d.get("title_override") or d.get("title") or Path(job.source).stem
    entries = build_meta(base, len(d["parts"]), hashtags=m["hashtags"], credit=m["credit"],
                         uploader=d.get("uploader", ""), url=d.get("webpage_url", "") if job.kind == "url" else "",
                         rights=job.rights, thumb=d.get("thumb", ""), parts=d["parts"])
    path = cfg.job_dir(job.id) / "meta.json"
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"meta": str(path)}


def publish(job: Job, cfg: Config, run: Runner, *, platforms: list[str] | None = None,
            start: datetime | None = None, on_done: Callable[[str], None] | None = None, **_) -> dict:
    d = job.data
    entries = json.loads(Path(d["meta"]).read_text(encoding="utf-8"))
    done = set(d.get("published", []))
    plan: list[tuple[str, list[str]]] = []
    for plat in platforms or d.get("platforms") or cfg["publish"]["platforms"]:
        if plat not in PLATFORMS:
            raise StageError(f"{plat}: social-auto-upload chưa có CLI đăng video cho nền tảng này "
                             f"(có: {', '.join(PLATFORMS)}).")
        for i, e in enumerate(entries):
            key = f"{plat}:{i + 1}"
            if e.get("platforms") and plat not in e["platforms"]:   # ví dụ video dài truyện chỉ lên YouTube
                continue
            if key not in done:
                plan.append((key, sau_command(cfg, plat, e, index=i, start=start)))
    for key, cmd in plan:
        run(cmd, cwd=cfg.sau_cwd())
        if not run.dry_run:
            done.add(key)
            if on_done:
                on_done(key)
    return {"published": sorted(done)}


STAGE_FUNCS = {"ingest": ingest, "dub": dub, "render": render, "meta": meta, "publish": publish}
