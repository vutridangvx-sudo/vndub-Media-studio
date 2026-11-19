"""Chế độ truyện audio (job ``kind = "story"``): chương văn bản -> giọng đọc -> video ngang có hình -> bản dọc cliffhanger.

Dùng chung hàng đợi, năm tên bước và bước ``publish`` với chế độ video; ``stages.py`` chuyển sang đây khi ``job.kind == "story"``:

ingest : gộp chương + hook + tóm tắt tập trước, áp từ điển phát âm của thể loại, báo cụm từ nhạy cảm -> source/script.txt
dub    : ``vndub speak`` đọc script (giọng/tốc độ theo preset, bảng phân vai chung cả bộ) -> voice.wav + voice.srt
render : ảnh theo cảnh (zoom chậm) + giọng + nhạc nền hạ khi có thoại -> video ngang; cắt bản dọc 9:16 kết ở cliffhanger;
         tính điểm đặt quảng cáo giữa video
meta   : tiêu đề/mô tả/hashtag cho video dài và bản dọc -> meta.json (sửa tay được trước khi đăng)
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tomllib
from pathlib import Path

from . import srt as SRT
from .config import Config, _merge
from .runner import Runner, StageError
from .store import Job

TEXT_EXT = (".txt", ".md")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".flac", ".ogg")

PRESET_DEFAULTS: dict = {
    "voice": {"style": "", "speed": 0, "one_voice": False},
    "text": {"lexicon": {}},
    "visual": {"scene_seconds": 25, "zoom_max": 1.12},
    "audio": {"bgm_volume": 0.18, "lufs": -16},
    "meta": {"hashtags": [], "ai_note": "", "desc_footer": "", "hook_prompt": ""},
    "safety": {"flag": []},
}


# --------------------------------------------------------------------------------------------
# helpers (thuần, có test)
# --------------------------------------------------------------------------------------------

def natural_key(p: Path | str) -> list:
    name = Path(p).name
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def expand_chapters(paths: list[str | Path]) -> list[Path]:
    """File chương hoặc thư mục (lấy mọi .txt/.md theo thứ tự số tự nhiên: c2 trước c10)."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found = sorted((f for f in p.iterdir() if f.suffix.lower() in TEXT_EXT), key=natural_key)
            if not found:
                raise StageError(f"Thư mục không có chương .txt/.md nào: {p}")
            out += found
        elif p.is_file():
            out.append(p)
        else:
            raise StageError(f"Không thấy chương/thư mục: {p}")
    return [p.resolve() for p in out]


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    """Thay một lượt (khoá dài thắng khoá ngắn, kết quả không bị thay lần nữa)."""
    keys = sorted((k for k in lexicon if k), key=len, reverse=True)
    if not keys:
        return text
    return re.sub("|".join(re.escape(k) for k in keys), lambda m: lexicon[m.group(0)], text)


def safety_hits(text: str, phrases: list[str]) -> dict[str, int]:
    low = text.lower()
    return {p: low.count(p.lower()) for p in phrases if p and p.lower() in low}


def build_script(chapters: list[str], *, hook: str = "", recap: str = "", gap: str = "[nghỉ 1s]") -> str:
    """Hook (5 giây đầu) -> tóm tắt tập trước -> các chương, ngăn bằng thẻ nghỉ của vndub."""
    parts = [p.strip() for p in (hook, recap, *chapters) if p and p.strip()]
    return (f"\n\n{gap}\n\n" if gap else "\n\n").join(parts) + "\n"


def episode_title(data: dict, chapters: list[str]) -> str:
    if data.get("title"):
        return data["title"]
    st, ep = data.get("series_title", ""), data.get("ep")
    if st and ep:
        return f"{st} - Tập {ep}"
    return st or (Path(chapters[0]).stem if chapters else "Tập mới")


def story_desc(*, hook: str, n_chapters: int, ep, footer: str, ai_note: str, hashtags: list[str]) -> str:
    lines = []
    if hook.strip():
        lines.append(" ".join(hook.split()))
    if ep or n_chapters > 1:
        lines.append(" · ".join(x for x in (f"Tập {ep}" if ep else "", f"{n_chapters} chương" if n_chapters > 1 else "") if x))
    for extra in (footer, ai_note):
        if extra.strip():
            lines.append(extra.strip())
    tags = " ".join("#" + t.lstrip("#") for t in hashtags if t.strip("#"))
    if tags:
        lines.append(tags)
    return "\n".join(lines)


def scene_plan(total: float, scene_seconds: float, fps: int) -> list[int]:
    """Số khung hình của từng cảnh; cảnh cuối lấy phần dư để tổng đúng bằng độ dài giọng đọc."""
    n = max(1, math.ceil(total / scene_seconds - 1e-9))
    each = max(1, round(scene_seconds * fps))
    last = max(1, round((total - (n - 1) * scene_seconds) * fps))
    return [each] * (n - 1) + [last]


def scene_filter(w: int, h: int, fps: int, frames: int, idx: int, zoom_max: float) -> str:
    """Ảnh -> khung WxH với zoom chậm; cảnh chẵn phóng vào, cảnh lẻ thu ra để đỡ đơn điệu. Phóng ảnh 2x trước để không giật."""
    dz = (zoom_max - 1.0) / max(frames, 1)
    z = (f"min(zoom+{dz:.6f},{zoom_max})" if idx % 2 == 0
         else f"if(eq(on,0),{zoom_max},max(zoom-{dz:.6f},1.0))")
    return (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},format=yuv420p")


def audio_graph(has_bgm: bool, bgm_volume: float, lufs: float) -> str | None:
    """Nhạc nền tự hạ khi có giọng (sidechain), trộn rồi chuẩn hoá loudness. Không có nhạc nền: giữ nguyên giọng."""
    if not has_bgm:
        return None
    return ("[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[vsc][vmix];"
            f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={bgm_volume}[bgm];"
            "[bgm][vsc]sidechaincompress=threshold=0.015:ratio=6:attack=40:release=650[duck];"
            "[vmix][duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
            f"loudnorm=I={lufs}:TP=-1.5:LRA=11[a]")


def list_assets(folder: Path, exts: tuple[str, ...]) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.suffix.lower() in exts), key=natural_key) if folder.is_dir() else []


def load_preset(cfg: Config, name: str) -> dict:
    d = cfg.presets_dir()
    path = d / f"{name}.toml"
    if not path.is_file():
        have = sorted(p.stem for p in d.glob("*.toml")) if d.is_dir() else []
        raise StageError(f"Không thấy preset thể loại '{name}' trong {d} (có: {', '.join(have) or 'chưa có'}).")
    with open(path, "rb") as f:
        p = _merge(PRESET_DEFAULTS, tomllib.load(f))
    p.setdefault("id", name)
    return p


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def _num(x, ndigits: int = 3) -> float:
    return round(float(x), ndigits)


# --------------------------------------------------------------------------------------------
# các bước
# --------------------------------------------------------------------------------------------

def ingest(job: Job, cfg: Config, run: Runner, **_) -> dict:
    d = job.data
    preset = load_preset(cfg, d.get("preset") or cfg["story"]["preset"])
    chapters = [Path(p) for p in d.get("chapters", [])]
    if not chapters:
        raise StageError("Job truyện chưa có chương nào (vnc story <chương...>).")
    for c in chapters:
        if not c.is_file():
            raise StageError(f"Không thấy file chương: {c}")
    lex = preset["text"]["lexicon"]
    texts = [apply_lexicon(read_text(c).strip(), lex) for c in chapters]
    hook, recap = apply_lexicon(d.get("hook", ""), lex), apply_lexicon(d.get("recap", ""), lex)
    script = build_script(texts, hook=hook, recap=recap, gap=cfg["story"]["chapter_gap"])

    src = cfg.job_dir(job.id) / "source"
    src.mkdir(parents=True, exist_ok=True)
    (src / "script.txt").write_text(script, encoding="utf-8")

    hits = safety_hits(script, preset["safety"]["flag"])
    report = src / "safety.txt"
    if hits:
        report.write_text("Cụm từ có nguy cơ bị gắn nhãn không phù hợp quảng cáo (chưa sửa, bạn duyệt):\n"
                          + "".join(f"  {n}× {p}\n" for p, n in hits.items()), encoding="utf-8")
        run.echo(f"  ⚠ {sum(hits.values())} chỗ có thể không hợp quảng cáo — xem {report}")
    elif report.exists():
        report.unlink()
    assets = cfg.assets_dir(preset["id"])
    for sub in ("images", "bgm"):
        (assets / sub).mkdir(parents=True, exist_ok=True)
    return {"script": str(src / "script.txt"), "preset": preset["id"], "chapter_count": len(chapters),
            "script_chars": len(script), "safety_hits": hits}


def dub(job: Job, cfg: Config, run: Runner, **_) -> dict:
    d = job.data
    preset = load_preset(cfg, d["preset"])
    out = cfg.job_dir(job.id) / "dub" / "voice.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    v = preset["voice"]
    cmd = [cfg.vndub_python(), "-m", "vndub.cli", "speak", "-f", d["script"], "-o", str(out)]
    if v["style"]:
        cmd += ["--style", v["style"]]
    if v["speed"]:
        cmd += ["--speed", str(v["speed"])]
    if v["one_voice"]:
        cmd += ["--one-voice"]
    cmd += ["--lufs", str(preset["audio"]["lufs"])]
    if d.get("series"):   # cùng series = cùng bảng phân vai: người kể/nhân vật giữ một giọng qua mọi tập
        cast = cfg.content_dir() / "series" / d["series"] / "cast.json"
        cast.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--cast", str(cast)]
    run(cmd + list(cfg["story"]["speak_args"]), cwd=cfg.vndub_cwd(), env=cfg.vndub_env())
    srt = out.with_suffix(".srt")
    if not out.is_file() or not srt.is_file():
        raise StageError(f"vndub chạy xong nhưng thiếu {out.name} hoặc {srt.name} trong {out.parent}")
    return {"dub_audio": str(out), "dub_srt": str(srt)}


def probe_duration(cfg: Config, run: Runner, path: Path) -> float:
    out = run([cfg["tools"]["ffprobe"], "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(out.strip())
    except ValueError as e:
        raise StageError(f"ffprobe không đọc được độ dài của {path}: {out!r}") from e


def render(job: Job, cfg: Config, run: Runner, **_) -> dict:
    from .stages import encoder_args, video_filter     # tránh import vòng (stages gọi story trong hàm)
    d, s, r = job.data, cfg["story"], cfg["render"]
    preset = load_preset(cfg, d["preset"])
    ff = cfg["tools"]["ffmpeg"]
    rd = cfg.job_dir(job.id) / "render"
    rd.mkdir(parents=True, exist_ok=True)
    for old in (*rd.glob("long*.mp4"), *rd.glob("short*.mp4")):
        old.unlink()
    scenes = rd / "scenes"
    shutil.rmtree(scenes, ignore_errors=True)
    scenes.mkdir()

    voice = Path(d["dub_audio"])
    total = probe_duration(cfg, run, voice)
    srt_path = Path(d.get("dub_srt", ""))
    cues = SRT.parse_srt(read_text(srt_path)) if srt_path.is_file() else []
    w, h, fps = int(s["width"]), int(s["height"]), int(s["fps"])

    assets = cfg.assets_dir(preset["id"])
    images = list_assets(Path(d["images"]) if d.get("images") else assets / "images", IMAGE_EXT)
    if not images:
        card = rd / "card.png"
        run.echo(f"  ⚠ chưa có ảnh nền (bỏ ảnh vào {assets / 'images'} hoặc dùng --images) — dùng nền trơn; "
                 "video ảnh tĩnh đơn điệu dễ bị YouTube hạ tiếp cận")
        run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x14141c:s={w}x{h}:d=1",
             "-frames:v", "1", str(card)])
        images = [card]

    zmax = float(preset["visual"]["zoom_max"])
    lines = []
    for i, frames in enumerate(scene_plan(total, float(preset["visual"]["scene_seconds"]), fps)):
        clip = scenes / f"s{i:04d}.mp4"
        run([ff, "-y", "-loglevel", "error", "-i", str(images[i % len(images)]),
             "-vf", scene_filter(w, h, fps, frames, i, zmax), "-frames:v", str(frames), "-an",
             *encoder_args(s), str(clip)])
        lines.append(f"file '{clip.resolve().as_posix()}'")
    (scenes / "list.txt").write_text("\n".join(lines), encoding="utf-8")

    long_mp4 = rd / "long.mp4"
    bgm_files = list_assets(assets / "bgm", AUDIO_EXT)
    bgm = bgm_files[job.id % len(bgm_files)] if bgm_files else None
    cmd = [ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(scenes / "list.txt"), "-i", str(voice)]
    graph = audio_graph(bool(bgm), float(preset["audio"]["bgm_volume"]), float(preset["audio"]["lufs"]))
    if bgm and graph:
        cmd += ["-stream_loop", "-1", "-i", str(bgm), "-filter_complex", graph, "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    run(cmd + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart",
               str(long_mp4)])
    if not long_mp4.is_file():
        raise StageError("ffmpeg không tạo ra video dài.")

    thumb = rd / "thumb.jpg"
    run([ff, "-y", "-loglevel", "error", "-ss", "0.2", "-i", str(scenes / "s0000.mp4"), "-frames:v", "1",
         "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "3", str(thumb)])

    # bản dọc kết ở cliffhanger: cắt từ video dài, cháy phụ đề của đoạn đó
    target = float(d["short_end"]) if d.get("short_end") else total * float(s["short_end_fraction"])
    smax = float(s["short_seconds"])
    if cues:
        start, end = SRT.pick_window(cues, min(target, total), smax)
    else:
        end = min(total, target)
        start = max(0.0, end - smax)
    sub_name = None
    short_cues = SRT.shift_cues(cues, start, end)
    if short_cues and r["burn_subs"]:
        (rd / "short.srt").write_text(SRT.format_srt(short_cues), encoding="utf-8")
        sub_name = "short.srt"
    short_mp4 = rd / "short.mp4"
    run([ff, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(long_mp4), "-t", f"{end - start:.3f}",
         "-filter_complex", video_filter(int(r["width"]), int(r["height"]), r["mode"], sub_name),
         "-map", "[v]", "-map", "0:a:0", "-pix_fmt", "yuv420p", *encoder_args(r), "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(short_mp4)], cwd=rd)
    if not short_mp4.is_file():
        raise StageError("ffmpeg không tạo ra bản dọc.")

    points = SRT.midroll_points(cues, total, float(s["midroll_minutes"]) * 60)
    mid = rd / "midrolls.json"
    mid.write_text(json.dumps({"seconds": points, "youtube": [SRT.hms(p) for p in points]}, indent=2), encoding="utf-8")
    if s["cleanup_scenes"]:
        shutil.rmtree(scenes, ignore_errors=True)
    return {"long": str(long_mp4), "short": str(short_mp4), "parts": [str(long_mp4), str(short_mp4)],
            "thumb": str(thumb) if thumb.is_file() else "", "duration": _num(total), "midrolls": points,
            "midrolls_file": str(mid), "short_window": [_num(start), _num(end)]}


def meta(job: Job, cfg: Config, run: Runner, **_) -> dict:
    from .stages import clip_text
    d = job.data
    m = load_preset(cfg, d["preset"])["meta"]
    title = clip_text(episode_title(d, d.get("chapters", [])), 100)
    first_hook = next((l for l in d.get("hook", "").splitlines() if l.strip()), "")
    tags = [t.lstrip("#") for t in m["hashtags"] if t.strip("#")]
    long_desc = story_desc(hook=clip_text(first_hook, 220), n_chapters=int(d.get("chapter_count", 1)), ep=d.get("ep"),
                           footer=m["desc_footer"], ai_note=m["ai_note"], hashtags=tags)
    short_desc = "\n".join(x for x in ("Nghe trọn tập trên kênh.", m["ai_note"].strip(),
                                       " ".join("#" + t for t in [*tags, "shorts"])) if x)
    entries = [
        {"file": d["long"], "kind": "long", "title": title, "desc": long_desc, "tags": tags,
         "thumbnail": d.get("thumb", ""), "platforms": ["youtube"]},
        {"file": d["short"], "kind": "short", "title": clip_text(first_hook or title, 90) + " #shorts",
         "desc": short_desc, "tags": [*tags, "shorts"], "thumbnail": ""},
    ]
    path = cfg.job_dir(job.id) / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"meta": str(path)}
