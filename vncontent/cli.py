"""Dòng lệnh ``vnc``.

  vnc add <url|file> --rights "kênh của tôi"   thêm job (URL bắt buộc ghi chú quyền sử dụng)
  vnc run <id|--all> [--to publish --yes]      chạy các bước còn lại (mặc định dừng sau `meta`)
  vnc publish <id> [--yes] [--start "2026-12-01 18:00"]
  vnc status [id] · vnc login youtube main · vnc doctor
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config as C
from .pipeline import run_job
from .runner import StageError
from .stages import PLATFORMS, VIDEO_EXT
from .store import STAGES, Store


def _parse_start(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        sys.exit('--start phải có dạng "YYYY-MM-DD HH:MM" (giờ máy), ví dụ "2026-12-01 18:00".')


def _csv(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def cmd_add(args, cfg: C.Config, store: Store) -> int:
    is_url = args.source.lower().startswith(("http://", "https://"))
    if is_url and not args.rights:
        sys.exit("Tải từ URL cần --rights \"...\" (ví dụ: \"video của kênh tôi\", \"CC-BY, đã xin phép\"). "
                 "Chỉ lấy nội dung bạn có quyền dùng lại; nền tảng có thể gỡ hoặc tắt kiếm tiền nội dung reup.")
    if not is_url and not Path(args.source).is_file():
        sys.exit(f"Không thấy file: {args.source}")
    for p in _csv(args.platforms) or []:
        if p not in PLATFORMS:
            sys.exit(f"Nền tảng không hỗ trợ: {p} (có: {', '.join(PLATFORMS)})")
    if args.series and not re.fullmatch(r"[\w\-]+", args.series):
        sys.exit("--series chỉ gồm chữ, số, gạch ngang/gạch dưới (dùng làm tên thư mục).")
    data = {k: v for k, v in {"srt": str(Path(args.srt).resolve()) if args.srt else "", "lang": args.lang,
                              "title_override": args.title, "platforms": _csv(args.platforms),
                              "series": args.series}.items() if v}
    job = store.add(args.source if is_url else str(Path(args.source).resolve()),
                    "url" if is_url else "file", args.rights or "", data)
    print(f"Đã thêm job #{job.id}. Chạy: vnc run {job.id}")
    return 0


def _at(value: str) -> str:
    """``@file.txt`` đọc nội dung file (hook/tóm tắt dài); còn lại giữ nguyên."""
    if value.startswith("@"):
        p = Path(value[1:])
        if not p.is_file():
            sys.exit(f"Không thấy file: {p}")
        return p.read_text(encoding="utf-8-sig").strip()
    return value


def cmd_story(args, cfg: C.Config, store: Store) -> int:
    from . import story
    from .srt import parse_clock
    try:
        chapters = story.expand_chapters(args.chapters)
        preset = story.load_preset(cfg, args.preset or cfg["story"]["preset"])
    except StageError as e:
        sys.exit(str(e))
    for p in _csv(args.platforms) or []:
        if p not in PLATFORMS:
            sys.exit(f"Nền tảng không hỗ trợ: {p} (có: {', '.join(PLATFORMS)})")
    if args.series and not re.fullmatch(r"[\w\-]+", args.series):
        sys.exit("--series chỉ gồm chữ, số, gạch ngang/gạch dưới (dùng làm tên thư mục).")
    if args.images and not Path(args.images).is_dir():
        sys.exit(f"Không thấy thư mục ảnh: {args.images}")
    try:
        short_end = parse_clock(args.short_end) if args.short_end else 0
    except ValueError as e:
        sys.exit(f"--short-end: {e}")
    data = {k: v for k, v in {
        "chapters": [str(c) for c in chapters], "preset": preset["id"], "series": args.series, "ep": args.ep,
        "title": args.title, "series_title": args.series_title, "hook": _at(args.hook), "recap": _at(args.recap),
        "images": str(Path(args.images).resolve()) if args.images else "", "short_end": short_end,
        "platforms": _csv(args.platforms)}.items() if v not in ("", None, 0, [])}
    job = store.add(str(chapters[0]), "story", args.rights or "", data)
    assets = cfg.assets_dir(preset["id"])
    print(f"Đã thêm job truyện #{job.id}: {len(chapters)} chương, thể loại {preset['title'] if 'title' in preset else preset['id']}.")
    print(f"  Ảnh nền: {args.images or assets / 'images'}   Nhạc nền: {assets / 'bgm'}")
    if not args.hook:
        print("  Chưa có --hook: tập sẽ vào thẳng chương 1. Gợi ý viết hook:", preset["meta"]["hook_prompt"] or "(không có)")
    print(f"Chạy: vnc run {job.id}")
    return 0


def cmd_clean(args, cfg: C.Config, store: Store) -> int:
    """Xoá file trung gian nặng (giọng WAV, cảnh dựng, video tải về) của job đã đăng xong — giữ clip và meta.json."""
    job = store.get(args.id)
    if job.status != "done" and not args.force:
        sys.exit(f"Job #{job.id} chưa đăng xong (trạng thái {job.status}); thêm --force nếu vẫn muốn dọn.")
    jd = cfg.job_dir(job.id)
    victims: list[Path] = [*jd.glob("dub/*.wav"), *jd.glob("dub/*_dub.*"), jd / "render" / "scenes"]
    if job.kind == "url":
        victims += [p for p in (jd / "source").glob("source.*") if p.suffix.lower() in VIDEO_EXT]
    freed = 0
    for v in victims:
        if v.is_dir():
            freed += sum(f.stat().st_size for f in v.rglob("*") if f.is_file())
            shutil.rmtree(v, ignore_errors=True)
        elif v.is_file():
            freed += v.stat().st_size
            v.unlink()
    print(f"Đã dọn #{job.id}: giải phóng {freed / 1_048_576:.0f} MB.")
    return 0


def cmd_run(args, cfg: C.Config, store: Store) -> int:
    ids = [j.id for j in store.all() if j.status != "done"] if args.all else [args.id]
    if not ids or ids == [None]:
        sys.exit("Cho id job hoặc --all.")
    rc = 0
    for i in ids:
        try:
            job = run_job(store, cfg, i, to=args.to, start_from=args.start_from, yes=args.yes,
                          platforms=_csv(args.platforms), start=_parse_start(args.start))
            print(f"[#{i}] xong tới: {job.stage or '-'} ({job.status})")
        except (StageError, KeyError, ValueError) as e:
            print(f"✗ {e}", file=sys.stderr)
            rc = 1
            if not args.all:
                break
    return rc


def cmd_publish(args, cfg: C.Config, store: Store) -> int:
    args.to, args.start_from, args.all = "publish", "publish", False
    return cmd_run(args, cfg, store)


def cmd_status(args, cfg: C.Config, store: Store) -> int:
    jobs = [store.get(args.id)] if args.id else store.all()
    if not jobs:
        print("Chưa có job. Thêm bằng: vnc add <url|file> --rights \"...\"")
    for j in jobs:
        title = j.data.get("title_override") or j.data.get("title") or Path(j.source).name
        print(f"#{j.id:<3} {j.status:<8} bước xong: {j.stage or '-':<8} {title[:50]}"
              + (f"   LỖI: {j.error}" if j.error else ""))
        if args.id:
            for k in ("video", "script", "dub_video", "dub_audio", "long", "short", "short_window", "midrolls_file",
                      "safety_hits", "parts", "meta", "published"):
                if j.data.get(k):
                    print(f"      {k}: {j.data[k]}")
    return 0


def cmd_login(args, cfg: C.Config, store: Store) -> int:
    """Đăng nhập tương tác (mở trình duyệt để bạn tự đăng nhập — không nhập mật khẩu qua pipeline)."""
    account = args.account or cfg["publish"]["accounts"].get(args.platform, "main")
    sub = "check" if args.check else "login"
    return subprocess.call([cfg.sau(), args.platform, sub, "--account", account], cwd=cfg.sau_cwd())


def cmd_doctor(args, cfg: C.Config, store: Store | None) -> int:
    import importlib.util
    import shutil
    ok = True

    def line(name: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok &= good
        print(f"{'✓' if good else '✗'} {name:<14} {detail}")

    line("config", True, str(cfg.source or "(mặc định — chưa có config.toml)"))
    try:
        root = cfg.media_root()
        line("dữ liệu", True, str(root))
    except C.ConfigError as e:
        line("dữ liệu", False, str(e))
    line("yt-dlp", importlib.util.find_spec("yt_dlp") is not None, "thư viện Python trong venv này")
    ff = shutil.which(cfg["tools"]["ffmpeg"])
    line("ffmpeg", bool(ff), ff or "không thấy trong PATH")
    try:
        py = cfg.vndub_python()
        r = subprocess.run([py, "-c", "import vndub"], capture_output=True, text=True)
        line("vndub", r.returncode == 0, py if r.returncode == 0 else f"{py}: không import được vndub")
    except (C.ConfigError, OSError) as e:
        line("vndub", False, str(e))
    fp = shutil.which(cfg["tools"]["ffprobe"])
    line("ffprobe", bool(fp), fp or "không thấy trong PATH (chế độ truyện cần để đo độ dài giọng đọc)")
    presets = sorted(p.stem for p in cfg.presets_dir().glob("*.toml")) if cfg.presets_dir().is_dir() else []
    line("preset", bool(presets), f"{len(presets)} thể loại trong {cfg.presets_dir()}: {', '.join(presets)}")
    sau = Path(cfg.sau())
    line("sau", sau.is_file(), str(sau) if sau.is_file() else f"{sau} — chạy scripts\\bootstrap.ps1")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="vnc", description="Pipeline nội dung video: tải -> lồng tiếng -> dựng dọc -> đăng")
    ap.add_argument("--config", help="đường dẫn config.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="thêm video vào hàng đợi")
    p.add_argument("source", help="URL (yt-dlp hỗ trợ) hoặc file video")
    p.add_argument("--rights", default="", help="ghi chú quyền sử dụng (bắt buộc với URL)")
    p.add_argument("--srt", help="phụ đề có sẵn (bỏ qua phụ đề tải về)")
    p.add_argument("--lang", default="", help="ngôn ngữ phụ đề/lời gốc, ví dụ en · vi (vi thì không dịch)")
    p.add_argument("--title", default="", help="tiêu đề đăng, mặc định lấy của video gốc")
    p.add_argument("--platforms", help="youtube,facebook,instagram (mặc định theo config)")
    p.add_argument("--series", default="", help="tên series: các video cùng series dùng chung bảng phân vai (giọng đồng bộ)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("story", help="thêm một tập truyện audio (chương .txt/.md -> giọng đọc -> video có hình)")
    p.add_argument("chapters", nargs="+", help="file chương hoặc thư mục chứa chương (thứ tự số tự nhiên)")
    p.add_argument("--preset", help="thể loại trong presets/genres (mặc định [story].preset)")
    p.add_argument("--series", default="", help="tên series (thư mục): cùng series dùng chung bảng phân vai -> giữ giọng")
    p.add_argument("--series-title", default="", help="tên bộ truyện hiển thị, dùng đặt tiêu đề")
    p.add_argument("--ep", type=int, help="số tập")
    p.add_argument("--title", default="", help="tiêu đề đăng (mặc định: <tên bộ> - Tập <số>)")
    p.add_argument("--hook", default="", help="2 câu mở đầu 5 giây (hoặc @file.txt)")
    p.add_argument("--recap", default="", help="tóm tắt tập trước ~20 giây (hoặc @file.txt)")
    p.add_argument("--images", help="thư mục ảnh nền riêng cho tập/bộ này (mặc định kho ảnh của thể loại)")
    p.add_argument("--short-end", help='kết bản dọc ở mốc này, ví dụ "12:30" (mặc định ~90%% độ dài, tại cuối một câu)')
    p.add_argument("--platforms", help="nền tảng cho bản dọc (video dài chỉ lên YouTube)")
    p.add_argument("--rights", default="", help="ghi chú quyền (truyện của bạn/đã xin phép) — lưu cùng job")
    p.set_defaults(fn=cmd_story)

    p = sub.add_parser("clean", help="dọn file trung gian nặng của job đã đăng xong")
    p.add_argument("id", type=int)
    p.add_argument("--force", action="store_true", help="dọn cả khi chưa đăng xong")
    p.set_defaults(fn=cmd_clean)

    p = sub.add_parser("run", help="chạy các bước còn lại của job")
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--all", action="store_true", help="mọi job chưa xong")
    p.add_argument("--to", default="meta", choices=STAGES, help="dừng sau bước này (mặc định meta)")
    p.add_argument("--from", dest="start_from", choices=STAGES, help="chạy lại từ bước này")
    p.add_argument("--yes", action="store_true", help="đăng THẬT khi tới bước publish (không có thì chỉ in lệnh)")
    p.add_argument("--platforms")
    p.add_argument("--start", help='hẹn giờ đăng đầu tiên "YYYY-MM-DD HH:MM" (chỉ YouTube)')
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("publish", help="đăng các clip của job (không --yes: chỉ in lệnh)")
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--platforms")
    p.add_argument("--start")
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("status", help="xem hàng đợi")
    p.add_argument("id", nargs="?", type=int)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("login", help="đăng nhập tài khoản mạng xã hội (mở trình duyệt, bạn tự đăng nhập)")
    p.add_argument("platform", choices=list(PLATFORMS))
    p.add_argument("account", nargs="?")
    p.add_argument("--check", action="store_true", help="chỉ kiểm tra phiên đăng nhập còn hiệu lực")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("doctor", help="kiểm tra công cụ và thư mục dữ liệu")
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    try:
        cfg = C.load(args.config)
        store = None if args.cmd == "doctor" else Store(cfg.db_path())
    except C.ConfigError as e:
        sys.exit(str(e))
    try:
        sys.exit(args.fn(args, cfg, store))
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    main()
