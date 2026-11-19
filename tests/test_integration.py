"""Chạy ffmpeg thật trên video thử 5 giây (bỏ qua nếu máy không có ffmpeg/ffprobe)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vncontent import pipeline as P
from vncontent import stages as S
from vncontent.runner import Runner

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="cần ffmpeg + ffprobe")


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    return {"w": j["streams"][0]["width"], "h": j["streams"][0]["height"], "dur": float(j["format"]["duration"])}


@pytest.fixture
def sample(tmp_path):
    video = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=5",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:v", "libx264", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", "-shortest", str(video)], check=True)
    srt = tmp_path / "in.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:03,000\nXin chào các bạn\n\n"
                   "2\n00:00:03,200 --> 00:00:04,800\nTạm biệt\n", encoding="utf-8")
    return video, srt


@pytest.mark.parametrize("mode", ["blur", "crop"])
def test_render_makes_vertical_clips_under_limit(store, cfg, sample, mode):
    video, srt = sample
    cfg.raw["render"].update(max_seconds=2, mode=mode)
    job = store.add(str(video), "file", data={"dub_video": str(video), "dub_srt": str(srt)})
    store.update(job.id, merge={"dub_video": str(video), "dub_srt": str(srt)})
    out = S.render(store.get(job.id), cfg, Runner(echo=lambda s: None))
    assert len(out["parts"]) in (2, 3)
    for p in out["parts"]:
        m = probe(Path(p))
        assert (m["w"], m["h"]) == (1080, 1920) and m["dur"] <= 2.6
    assert Path(out["thumb"]).is_file()
    assert (cfg.job_dir(job.id) / "render" / "subs.srt").is_file()     # phụ đề đã được cháy vào


def test_file_job_end_to_end_up_to_meta(store, cfg, sample, monkeypatch):
    """ingest(file) -> [dub giả: dùng luôn video gốc] -> render -> meta với ffmpeg thật."""
    video, srt = sample
    cfg.raw["render"]["max_seconds"] = 59

    def fake_dub(job, cfg, run, **kw):
        return {"dub_video": job.data["video"], "dub_srt": job.data["srt"], "dub_audio": ""}
    monkeypatch.setitem(P.STAGE_FUNCS, "dub", fake_dub)
    job = store.add(str(video), "file", data={"srt": str(srt), "lang": "vi", "title_override": "Bài thử"})
    job = P.run_job(store, cfg, job.id, echo=lambda s: None)
    assert job.stage == "meta" and len(job.data["parts"]) == 1
    meta = json.loads(Path(job.data["meta"]).read_text(encoding="utf-8"))
    assert meta[0]["title"] == "Bài thử" and meta[0]["file"] == job.data["parts"][0]
    assert meta[0]["thumbnail"] == job.data["thumb"] != ""
