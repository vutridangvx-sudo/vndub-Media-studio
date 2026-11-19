"""Chế độ truyện chạy ffmpeg thật: ảnh -> video ngang có giọng + nhạc nền -> bản dọc cliffhanger (vndub giả: tạo sẵn voice.wav/.srt)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vncontent import pipeline as P
from vncontent import srt as SRT
from vncontent import story as T

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="cần ffmpeg + ffprobe")

PRESET = """
id = "thu"
[voice]
style = "truyen-ma"
[visual]
scene_seconds = 3
zoom_max = 1.1
[audio]
bgm_volume = 0.2
lufs = -16
[meta]
hashtags = ["#truyen"]
"""


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def probe(path: Path, entries: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.fixture
def scfg(cfg, tmp_path):
    d = tmp_path / "presets"
    d.mkdir()
    (d / "thu.toml").write_text(PRESET, encoding="utf-8")
    cfg.raw["story"].update(presets_dir=str(d), preset="thu", width=640, height=360, fps=10, short_seconds=5)
    cfg.raw["render"].update(width=270, height=480)
    return cfg


def fake_speak(job, cfg, run, **_):
    """Thay `vndub speak`: giọng 8 giây + phụ đề 4 câu."""
    out = cfg.job_dir(job.id) / "dub" / "voice.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", "sine=frequency=300:duration=8", "-ar", "48000", out)
    out.with_suffix(".srt").write_text(SRT.format_srt([
        SRT.Cue(0, 2, "Câu một."), SRT.Cue(2, 4, "Câu hai."), SRT.Cue(4, 6, "Câu ba."), SRT.Cue(6, 8, "Câu bốn.")]),
        encoding="utf-8")
    return {"dub_audio": str(out), "dub_srt": str(out.with_suffix(".srt"))}


def make_job(store, scfg, tmp_path, with_images=True, with_bgm=False, **data):
    ch = tmp_path / "c1.txt"
    ch.write_text("Nội dung chương.", encoding="utf-8")
    assets = scfg.assets_dir("thu")
    (assets / "images").mkdir(parents=True)
    (assets / "bgm").mkdir()
    if with_images:
        for i, color in enumerate(("red", "blue")):
            ff("-f", "lavfi", "-i", f"color=c={color}:s=800x600", "-frames:v", "1", assets / "images" / f"a{i}.png")
    if with_bgm:
        ff("-f", "lavfi", "-i", "sine=frequency=120:duration=3", "-ar", "48000", assets / "bgm" / "n.wav")
    job = store.add(str(ch), "story", data={"chapters": [str(ch)], "preset": "thu", "ep": 1, "series_title": "Thử",
                                            "hook": "Mở đầu gây sốc.", **data})
    return store.get(job.id)


@pytest.mark.parametrize("with_bgm", [False, True])
def test_story_job_end_to_end_up_to_meta(store, scfg, tmp_path, monkeypatch, with_bgm):
    monkeypatch.setattr(T, "dub", fake_speak)
    job = make_job(store, scfg, tmp_path, with_bgm=with_bgm)
    job = P.run_job(store, scfg, job.id, echo=lambda s: None)
    assert job.stage == "meta"
    long = probe(Path(job.data["long"]), "stream=codec_type,width,height:format=duration")
    kinds = {s["codec_type"] for s in long["streams"]}
    assert kinds == {"video", "audio"}
    v = next(s for s in long["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (640, 360) and abs(float(long["format"]["duration"]) - 8) < 0.3

    short = probe(Path(job.data["short"]), "stream=width,height:format=duration")
    assert (short["streams"][0]["width"], short["streams"][0]["height"]) == (270, 480)
    s0, s1 = job.data["short_window"]
    assert s1 - s0 <= 5 and s1 in (2, 4, 6, 8) and abs(float(short["format"]["duration"]) - (s1 - s0)) < 0.3
    assert (cfg_render := scfg.job_dir(job.id) / "render" / "short.srt").is_file() and cfg_render.read_text(encoding="utf-8")
    assert Path(job.data["thumb"]).is_file() and not (scfg.job_dir(job.id) / "render" / "scenes").exists()
    assert json.loads(Path(job.data["midrolls_file"]).read_text())["seconds"] == []        # 8 giây: chưa tới mốc 10 phút

    meta = json.loads(Path(job.data["meta"]).read_text(encoding="utf-8"))
    assert [e["kind"] for e in meta] == ["long", "short"] and meta[0]["title"] == "Thử - Tập 1"


def test_story_without_images_falls_back_to_plain_card(store, scfg, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "dub", fake_speak)
    job = make_job(store, scfg, tmp_path, with_images=False)
    lines: list[str] = []
    job = P.run_job(store, scfg, job.id, echo=lines.append)
    assert job.stage == "meta" and any("chưa có ảnh nền" in l for l in lines)


def test_story_short_end_override_and_custom_images(store, scfg, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "dub", fake_speak)
    mine = tmp_path / "anh-rieng"
    mine.mkdir()
    ff("-f", "lavfi", "-i", "color=c=green:s=640x480", "-frames:v", "1", mine / "x.png")
    job = make_job(store, scfg, tmp_path, with_images=False, images=str(mine), short_end=4)
    job = P.run_job(store, scfg, job.id, echo=lambda s: None)
    assert job.data["short_window"][1] == 4
