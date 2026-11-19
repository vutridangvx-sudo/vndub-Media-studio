"""Chạy một job qua các bước còn lại, có thể dừng/tiếp tục, ghi trạng thái vào SQLite."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .config import Config, ConfigError
from .runner import Runner, StageError
from .stages import STAGE_FUNCS
from .store import STAGES, Job, Store


def run_job(store: Store, cfg: Config, job_id: int, *, to: str = "meta", start_from: str | None = None,
            yes: bool = False, platforms: list[str] | None = None, start: datetime | None = None,
            echo: Callable[[str], None] = print) -> Job:
    """Mặc định dừng sau ``meta`` (để bạn xem/sửa meta.json). Đăng thật chỉ khi ``to='publish'`` VÀ ``yes``;
    thiếu ``yes`` thì ``publish`` chỉ in lệnh (job không bị đánh dấu đã đăng)."""
    if to not in STAGES or (start_from and start_from not in STAGES):
        raise ValueError(f"Bước hợp lệ: {', '.join(STAGES)}")
    job = store.get(job_id)
    if start_from:
        i = STAGES.index(start_from)
        job = store.update(job_id, stage=STAGES[i - 1] if i else "", status="pending", error="")
    while (st := job.next_stage()) and STAGES.index(st) <= STAGES.index(to):
        run = Runner(dry_run=(st == "publish" and not yes),
                     log=cfg.job_dir(job.id) / "run.log", echo=echo)
        echo(f"[#{job.id}] {st}" + ("  (chỉ in lệnh — thêm --yes để đăng thật)" if run.dry_run else ""))
        store.update(job.id, status="running", error="")
        opts = {"platforms": platforms, "start": start,
                "on_done": lambda key, j=job.id: store.update(
                    j, merge={"published": sorted({*store.get(j).data.get("published", []), key})})}
        try:
            out = STAGE_FUNCS[st](store.get(job.id), cfg, run, **opts)
        except (StageError, ConfigError, KeyError, OSError) as e:
            store.update(job.id, status="failed", error=f"{st}: {e}")
            raise StageError(f"[#{job.id}] bước {st} lỗi: {e}") from e
        if run.dry_run and run.commands:   # mới chỉ in kế hoạch đăng — chưa đăng gì, chưa đánh dấu xong
            store.update(job.id, status="pending")
            break
        job = store.update(job.id, merge=out, stage=st, status="done" if st == STAGES[-1] else "pending")
    return store.get(job.id)
