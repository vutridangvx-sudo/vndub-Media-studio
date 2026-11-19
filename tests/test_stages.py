from datetime import datetime
from pathlib import Path

import pytest

from vncontent import stages as S
from vncontent.runner import StageError


def test_same_lang():
    assert S.same_lang("vi", "vi") and S.same_lang("vi-VN", "vi") and S.same_lang("VI", "vi-vn")
    assert not S.same_lang("en", "vi")
    assert not S.same_lang("", "vi")          # chưa biết ngôn ngữ -> coi như cần dịch


def test_pick_sub_follows_preference():
    subs = {"en": Path("a.en.srt"), "vi": Path("a.vi.srt")}
    assert S.pick_sub(subs, ["vi", "en"]) == ("vi", Path("a.vi.srt"))
    assert S.pick_sub({"en-US": Path("x.srt")}, ["vi", "en"]) == ("en-US", Path("x.srt"))
    assert S.pick_sub({"fr": Path("x.srt")}, ["vi", "en"]) is None
    assert S.pick_sub({}, ["vi"]) is None


def test_video_filter_modes():
    blur = S.video_filter(1080, 1920, "blur", "subs.srt")
    assert "boxblur" in blur and "overlay" in blur and "subtitles=subs.srt" in blur and blur.endswith("[v]")
    crop = S.video_filter(1080, 1920, "crop", None)
    assert "crop=1080:1920" in crop and "subtitles" not in crop and "overlay" not in crop


def test_subtitle_style_is_scaled_to_libass_288_grid():
    f = S.video_filter(1080, 1920, "blur", "subs.srt")
    assert "FontSize=9," in f and "MarginV=49" in f and "original_size" not in f   # 9×(1920/288) ≈ 60 px
    sq = S.video_filter(1080, 1080, "crop", "subs.srt")
    assert "FontSize=16," in sq and "MarginV=49" in sq


def _dub_job(store, **data):
    job = store.add("v.mp4", "file")
    return store.update(job.id, merge={"video": "v.mp4", "srt": "v.srt", "src_lang": "en", **data})


def test_dub_command_translates_foreign_subs(store, cfg, fake_subprocess):
    with pytest.raises(StageError, match="không thấy video"):    # subprocess giả không tạo file ra
        S.dub(_dub_job(store), cfg, S.Runner(echo=lambda s: None))
    cmd = fake_subprocess.calls[0]
    assert cmd[:4] == ["python-vndub", "-m", "vndub.cli", "dub"] and "--to" in cmd and "--cast" not in cmd


def test_dub_skips_translation_when_already_target_lang(store, cfg, fake_subprocess):
    with pytest.raises(StageError):
        S.dub(_dub_job(store, src_lang="vi"), cfg, S.Runner(echo=lambda s: None))
    assert "--to" not in fake_subprocess.calls[0]


def test_dub_without_subs_uses_dub_video_and_series_cast(store, cfg, fake_subprocess):
    with pytest.raises(StageError):
        S.dub(_dub_job(store, srt="", series="phim-a"), cfg, S.Runner(echo=lambda s: None))
    cmd = fake_subprocess.calls[0]
    assert cmd[3] == "dub-video" and cmd[cmd.index("--to") + 1] == "tiếng Việt"
    assert Path(cmd[cmd.index("--cast") + 1]) == cfg.content_dir() / "series" / "phim-a" / "cast.json"


def _fake_ytdlp(files: dict[str, str]):
    """Hook: giả lập yt-dlp ghi các file vào thư mục của mẫu -o."""
    def hook(argv):
        out = Path(argv[argv.index("-o") + 1]).parent
        for name, content in files.items():
            (out / name).write_text(content, encoding="utf-8")
    return hook


def test_ingest_url_requires_rights(store, cfg, fake_subprocess):
    job = store.add("https://example.com/v", "url")          # không ghi chú quyền sử dụng
    with pytest.raises(StageError, match="--rights"):
        S.ingest(job, cfg, S.Runner(echo=lambda s: None))
    assert fake_subprocess.calls == []


def test_ingest_url_picks_manual_vietnamese_subs(store, cfg, fake_subprocess):
    fake_subprocess.state["hook"] = _fake_ytdlp({
        "source.mp4": "v", "source.en.srt": "e", "source.vi.srt": "v",
        "source.info.json": '{"title": "Video hay", "uploader": "Kênh A", "webpage_url": "https://y/1", '
                            '"license": "Creative Commons Attribution license", "duration": 61}'})
    job = store.add("https://example.com/v", "url", "kênh của tôi")
    out = S.ingest(job, cfg, S.Runner(echo=lambda s: None))
    cmd = fake_subprocess.calls[0]
    assert cmd[1:3] == ["-m", "yt_dlp"] and cmd[-1] == "https://example.com/v"
    assert "--write-subs" in cmd and "--write-auto-subs" not in cmd and "--no-playlist" in cmd
    assert cmd[cmd.index("--sub-langs") + 1] == "vi,en"
    assert out["src_lang"] == "vi" and out["srt"].endswith("source.vi.srt") and out["video"].endswith("source.mp4")
    assert (out["title"], out["uploader"], out["duration"]) == ("Video hay", "Kênh A", 61)
    assert "Creative Commons" in out["license"]


def test_ingest_url_without_subs_leaves_srt_empty(store, cfg, fake_subprocess):
    cfg.raw["ingest"]["use_auto_subs"] = True
    fake_subprocess.state["hook"] = _fake_ytdlp({"source.mp4": "v"})
    out = S.ingest(store.add("https://example.com/v", "url", "ok"), cfg, S.Runner(echo=lambda s: None))
    assert "--write-auto-subs" in fake_subprocess.calls[0]
    assert out["srt"] == "" and out["src_lang"] == "" and out["title"] == ""


def test_ingest_url_fails_clearly_when_nothing_downloaded(store, cfg, fake_subprocess):
    with pytest.raises(StageError, match="không tạo ra file video"):
        S.ingest(store.add("https://example.com/v", "url", "ok"), cfg, S.Runner(echo=lambda s: None))


def test_ingest_file_copies_srt_into_job_dir(store, cfg, tmp_path):
    video, srt = tmp_path / "a.mp4", tmp_path / "a.srt"
    video.write_text("v"), srt.write_text("s")
    job = store.add(str(video), "file", data={"srt": str(srt), "lang": "vi"})
    out = S.ingest(job, cfg, S.Runner(echo=lambda s: None))
    assert Path(out["srt"]).parent == cfg.job_dir(job.id) / "source" and Path(out["srt"]).read_text() == "s"
    assert out["src_lang"] == "vi" and out["title"] == "a"


def test_encoder_args():
    assert S.encoder_args({"encoder": "libx264", "crf": 20})[:2] == ["-c:v", "libx264"]
    assert "-cq" in S.encoder_args({"encoder": "h264_nvenc", "crf": 20})


def test_build_meta_parts_and_credit():
    m = S.build_meta("Tiêu đề", 2, hashtags=["#shorts", "vn"], credit=True, uploader="Kênh A",
                     url="https://y/x", rights="CC-BY", thumb="t.jpg", parts=["p1.mp4", "p2.mp4"])
    assert [e["title"] for e in m] == ["Tiêu đề (Phần 1/2)", "Tiêu đề (Phần 2/2)"]
    assert m[0]["thumbnail"] == "t.jpg" and m[1]["thumbnail"] == ""
    assert "Nguồn: Kênh A — https://y/x" in m[0]["desc"] and "Quyền sử dụng: CC-BY" in m[0]["desc"]
    assert m[0]["tags"] == ["shorts", "vn"] and "#shorts #vn" in m[0]["desc"]


def test_build_meta_single_and_no_credit():
    m = S.build_meta("x" * 200, 1, hashtags=[], credit=False, url="https://y", rights="r", parts=["p.mp4"])
    assert len(m[0]["title"]) == 100 and m[0]["title"].endswith("…")
    assert m[0]["desc"] == ""


def _entry():
    return {"file": "p1.mp4", "title": "T", "desc": "D", "tags": ["a", "b"], "thumbnail": "t.jpg"}


def test_sau_command_youtube(cfg):
    cmd = S.sau_command(cfg, "youtube", _entry(), index=0, start=None)
    assert cmd[:5] == ["sau-fake", "youtube", "upload-video", "--account", "main"]
    assert cmd[cmd.index("--visibility") + 1] == "private"      # mặc định riêng tư
    assert cmd[cmd.index("--thumbnail") + 1] == "t.jpg" and "--headless" in cmd
    assert "--schedule" not in cmd


def test_sau_command_schedule_spaced_by_interval(cfg):
    start = datetime(2026, 12, 1, 18, 0)
    c0 = S.sau_command(cfg, "youtube", _entry(), index=0, start=start)
    c2 = S.sau_command(cfg, "youtube", _entry(), index=2, start=start)
    assert c0[c0.index("--schedule") + 1] == "2026-12-01 18:00"
    assert c2[c2.index("--schedule") + 1] == "2026-12-02 06:00"   # 2 × 6 giờ


def test_sau_command_platform_capabilities(cfg):
    fb = S.sau_command(cfg, "facebook", _entry(), index=0, start=None)
    assert "--visibility" not in fb and "--thumbnail" not in fb
    with pytest.raises(StageError, match="hẹn giờ"):
        S.sau_command(cfg, "facebook", _entry(), index=0, start=datetime(2026, 12, 1, 18, 0))
    cfg.raw["publish"]["accounts"].pop("youtube")
    with pytest.raises(StageError, match="tài khoản"):
        S.sau_command(cfg, "youtube", _entry(), index=0, start=None)
