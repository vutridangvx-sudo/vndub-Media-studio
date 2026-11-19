"""Đọc/ghi SRT và các phép tính trên mốc thời gian (điểm đặt quảng cáo giữa video, đoạn cắt cliffhanger). Thuần, có test."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_END_PUNCT = (".", "!", "?", "…", "”", '"', "。")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str

    @property
    def ends_sentence(self) -> bool:
        return self.text.rstrip().endswith(_END_PUNCT)


def _secs(s: str) -> float:
    m = _TIME.fullmatch(s.strip())
    if not m:
        raise ValueError(f"mốc thời gian SRT không hợp lệ: {s!r}")
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")) / 1000


def fmt_time(t: float) -> str:
    ms = round(max(0.0, t) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").lstrip("﻿").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if "-->" in line:
                a, b = line.split("-->", 1)
                try:
                    cues.append(Cue(_secs(a), _secs(b.split()[0]), " ".join(x.strip() for x in lines[i + 1:])))
                except ValueError:
                    pass
                break
    return cues


def format_srt(cues: list[Cue]) -> str:
    return "".join(f"{i}\n{fmt_time(c.start)} --> {fmt_time(c.end)}\n{c.text}\n\n" for i, c in enumerate(cues, 1))


def hms(t: float) -> str:
    """``75`` -> ``1:15``; ``3725`` -> ``1:02:05`` (dạng YouTube Studio hiểu)."""
    t = int(round(t))
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_clock(s: str) -> float:
    """``"12:30"`` / ``"1:02:05"`` / ``"90"`` -> giây."""
    parts = [float(p) for p in s.strip().split(":")]
    if not parts or len(parts) > 3:
        raise ValueError(f"thời điểm không hợp lệ: {s!r}")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def midroll_points(cues: list[Cue], total: float, every: float, *, tail: float = 90.0) -> list[float]:
    """Điểm đặt quảng cáo giữa video: mỗi ``every`` giây, dời tới chỗ hết một câu (không cắt ngang lời).

    Không đặt trong ``tail`` giây cuối. Video ngắn hơn ``every`` -> không có điểm nào.
    """
    points: list[float] = []
    if every <= 0 or not cues:
        return points
    target = every
    while target < total - tail:
        after = [c for c in cues if c.end >= target]
        pick = next((c for c in after if c.ends_sentence), after[0] if after else None)
        if pick is None or pick.end >= total - tail:
            break
        if not points or pick.end > points[-1]:
            points.append(round(pick.end, 2))
        target = max(target, pick.end) + every
    return points


def pick_window(cues: list[Cue], target_end: float, max_seconds: float) -> tuple[float, float]:
    """Đoạn ``<= max_seconds`` kết thúc ở cuối một câu gần ``target_end`` nhất (không vượt), bắt đầu ở đầu một câu.

    Dùng để cắt bản dọc kết ở chỗ chưa giải quyết (cliffhanger). Trả (bắt đầu, kết thúc) tính bằng giây.
    """
    if not cues:
        raise ValueError("không có phụ đề để chọn đoạn cắt")
    cand = [i for i, c in enumerate(cues) if c.end <= target_end] or [0]
    sentence = [i for i in cand if cues[i].ends_sentence]
    last = (sentence or cand)[-1]
    end = cues[last].end
    first = last
    while first > 0 and end - cues[first - 1].start <= max_seconds:
        first -= 1
    start = cues[first].start
    if end - start > max_seconds:          # một câu dài hơn giới hạn: lấy phần cuối
        start = end - max_seconds
    return round(start, 3), round(end, 3)


def shift_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
    """Các câu nằm trong [start, end], dời về mốc 0 (để cháy vào đoạn cắt)."""
    out = []
    for c in cues:
        if c.end <= start or c.start >= end:
            continue
        out.append(Cue(max(c.start, start) - start, min(c.end, end) - start, c.text))
    return out
