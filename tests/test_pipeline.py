import json
from pathlib import Path

import pytest

from vncontent import pipeline as P
from vncontent.runner import StageError


@pytest.fixture
def fakes(monkeypatch, cfg):
    """Ba bước đầu + meta giả (không cần video thật); publish là bản thật chạy trên subprocess giả."""
    order: list[str] = []

    def fake(name, out):
        def fn(job, cfg, run, **kw):
            order.append(name)
            return out(job) if callable(out) else out
        return fn

    def meta_out(job):
        p = cfg.job_dir(job.id) / "meta.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([
            {"file": "p1.mp4", "title": "T1", "desc": "", "tags": ["a"], "thumbnail": ""},
            {"file": "p2.mp4", "title": "T2", "desc": "", "tags": ["a"], "thumbnail": ""}]), encoding="utf-8")
        return {"meta": str(p)}

    funcs = dict(P.STAGE_FUNCS)
    funcs.update(ingest=fake("ingest", {"video": "v.mp4"}), dub=fake("dub", {"dub_video": "d.mp4"}),
                 render=fake("render", {"parts": ["p1.mp4", "p2.mp4"]}), meta=fake("meta", meta_out))
    monkeypatch.setattr(P, "STAGE_FUNCS", funcs)
    return order


def quiet(_):
    pass


def test_default_stops_after_meta(store, cfg, fakes):
    job = store.add("v.mp4", "file")
    job = P.run_job(store, cfg, job.id, echo=quiet)
    assert fakes == ["ingest", "dub", "render", "meta"]
    assert job.stage == "meta" and job.status == "pending" and "meta" in job.data


def test_publish_without_yes_only_prints(store, cfg, fakes, fake_subprocess):
    job = store.add("v.mp4", "file")
    lines: list[str] = []
    job = P.run_job(store, cfg, job.id, to="publish", echo=lines.append)
    assert fake_subprocess.calls == []                      # không chạy sau `sau`
    assert job.stage == "meta" and not job.data.get("published")
    assert sum("sau-fake youtube upload-video" in l for l in lines) == 2    # in 2 lệnh (2 clip)


def test_publish_with_yes_records_and_is_idempotent(store, cfg, fakes, fake_subprocess):
    job = store.add("v.mp4", "file")
    job = P.run_job(store, cfg, job.id, to="publish", yes=True, echo=quiet)
    assert [c[0] for c in fake_subprocess.calls] == ["sau-fake", "sau-fake"]
    assert job.stage == "publish" and job.status == "done"
    assert job.data["published"] == ["youtube:1", "youtube:2"]
    # chạy lại từ publish: clip đã đăng thì không đăng lần nữa
    P.run_job(store, cfg, job.id, to="publish", start_from="publish", yes=True, echo=quiet)
    assert len(fake_subprocess.calls) == 2


def test_publish_failure_keeps_partial_progress(store, cfg, fakes, fake_subprocess):
    job = store.add("v.mp4", "file")
    fake_subprocess.state["fail_on"] = "p2.mp4"
    with pytest.raises(StageError):
        P.run_job(store, cfg, job.id, to="publish", yes=True, echo=quiet)
    j = store.get(job.id)
    assert j.status == "failed" and "publish" in j.error and j.data["published"] == ["youtube:1"]
    fake_subprocess.state["fail_on"] = None
    P.run_job(store, cfg, job.id, to="publish", start_from="publish", yes=True, echo=quiet)
    assert store.get(job.id).data["published"] == ["youtube:1", "youtube:2"]
    assert len(fake_subprocess.calls) == 3                   # clip 1 một lần, clip 2 hai lần


def test_failure_marks_job_and_rerun_resumes(store, cfg, fakes, monkeypatch):
    calls = {"dub": 0}

    def flaky(job, cfg, run, **kw):
        calls["dub"] += 1
        if calls["dub"] == 1:
            raise StageError("vndub chết")
        return {"dub_video": "d.mp4"}
    monkeypatch.setitem(P.STAGE_FUNCS, "dub", flaky)
    job = store.add("v.mp4", "file")
    with pytest.raises(StageError, match="dub"):
        P.run_job(store, cfg, job.id, echo=quiet)
    j = store.get(job.id)
    assert j.status == "failed" and j.stage == "ingest" and "vndub chết" in j.error
    fakes.clear()
    j = P.run_job(store, cfg, job.id, echo=quiet)
    assert fakes == ["render", "meta"] and j.stage == "meta"      # ingest không chạy lại


def test_start_from_rewinds(store, cfg, fakes):
    job = store.add("v.mp4", "file")
    P.run_job(store, cfg, job.id, echo=quiet)
    fakes.clear()
    P.run_job(store, cfg, job.id, start_from="render", to="render", echo=quiet)
    assert fakes == ["render"]


def test_bad_stage_name(store, cfg):
    job = store.add("v.mp4", "file")
    with pytest.raises(ValueError):
        P.run_job(store, cfg, job.id, to="nope")
