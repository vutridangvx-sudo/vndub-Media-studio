import os
import string
import sys
from pathlib import Path

import pytest

from vncontent import config as C
from vncontent.store import STAGES, Store


def test_store_roundtrip_and_merge(store):
    job = store.add("https://x/v", "url", "kênh tôi", {"lang": "en"})
    assert (job.id, job.status, job.stage, job.rights) == (1, "pending", "", "kênh tôi")
    assert job.next_stage() == "ingest"
    j = store.update(job.id, merge={"video": "a.mp4"}, stage="ingest")
    j = store.update(job.id, merge={"srt": "a.srt"})
    assert j.data == {"lang": "en", "video": "a.mp4", "srt": "a.srt"} and j.next_stage() == "dub"
    assert [x.id for x in store.all("pending")] == [1] and store.all("done") == []
    with pytest.raises(KeyError):
        store.get(99)


def test_next_stage_ends(store):
    job = store.add("f.mp4", "file")
    job = store.update(job.id, stage=STAGES[-1])
    assert job.next_stage() is None


def test_store_persists(tmp_path):
    p = tmp_path / "x" / "s.db"
    Store(p).add("f.mp4", "file")
    assert Store(p).get(1).source == "f.mp4"


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VNC_CONFIG", raising=False)
    cfg = C.Config()
    assert cfg["render"]["max_seconds"] == 59 and cfg["publish"]["visibility"] == "private"


def test_load_merges_over_defaults(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[render]\nmax_seconds = 30\n[publish.accounts]\nyoutube = "kenh2"\n', encoding="utf-8")
    cfg = C.load(f)
    assert cfg["render"]["max_seconds"] == 30 and cfg["render"]["mode"] == "blur"    # phần còn lại giữ mặc định
    assert cfg["publish"]["accounts"] == {"youtube": "kenh2", "facebook": "main", "instagram": "main"}
    with pytest.raises(C.ConfigError):
        C.load(tmp_path / "khong-co.toml")


def test_vndub_env_overlays_process_env(monkeypatch):
    monkeypatch.setenv("KEEP_ME", "1")
    cfg = C.Config()
    cfg.raw["tools"]["vndub_env"] = {"HF_HOME": "X:\\hf"}
    env = cfg.vndub_env()
    assert env["HF_HOME"] == "X:\\hf" and env["KEEP_ME"] == "1"


def test_media_root_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("VNDUB_MEDIA", str(tmp_path / "env"))
    cfg = C.Config()
    assert cfg.media_root() == tmp_path / "env"
    cfg.raw["paths"]["media_root"] = str(tmp_path / "cfg")
    assert cfg.media_root() == tmp_path / "cfg"
    assert cfg.job_dir(7) == tmp_path / "cfg" / "content" / "jobs" / "0007"
    monkeypatch.delenv("VNDUB_MEDIA")
    cfg.raw["paths"]["media_root"] = ""
    assert cfg.media_root() == C.REPO / "data"


@pytest.mark.skipif(sys.platform != "win32", reason="ký tự ổ chỉ có trên Windows")
def test_missing_drive_is_clear_error():
    free = next((c for c in reversed(string.ascii_uppercase) if not Path(f"{c}:\\").exists()), None)
    if free is None:
        pytest.skip("không còn ký tự ổ trống")
    cfg = C.Config()
    cfg.raw["paths"]["media_root"] = f"{free}:\\media"
    with pytest.raises(C.ConfigError, match="SSD"):
        cfg.media_root()
