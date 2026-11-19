import copy
import subprocess

import pytest

from vncontent.config import DEFAULTS, Config
from vncontent.store import Store


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("VNDUB_MEDIA", raising=False)
    raw = copy.deepcopy(DEFAULTS)
    raw["paths"]["media_root"] = str(tmp_path / "media")
    raw["tools"].update(vndub_python="python-vndub", sau="sau-fake")
    return Config(raw)


@pytest.fixture
def store(cfg):
    s = Store(cfg.db_path())
    yield s
    s.close()


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Thay subprocess.run trong runner: ghi lại lệnh, trả mã 0 (hoặc theo `fail_on`)."""
    calls: list[list[str]] = []
    state = {"fail_on": None, "hook": None}

    def fake_run(argv, **kw):
        calls.append(list(argv))
        if state["hook"]:
            state["hook"](list(argv))
        bad = state["fail_on"] and state["fail_on"] in " ".join(map(str, argv))
        return subprocess.CompletedProcess(argv, 1 if bad else 0, stdout="ok", stderr="boom" if bad else "")

    monkeypatch.setattr("vncontent.runner.subprocess.run", fake_run)
    fake_run.calls = calls
    fake_run.state = state
    return fake_run
