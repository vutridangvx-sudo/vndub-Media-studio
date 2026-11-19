"""Chế độ truyện audio: phần thuần (SRT, kế hoạch cảnh, preset) + các bước ingest/dub/meta với subprocess giả."""

import json
from pathlib import Path

import pytest

from vncontent import cli
from vncontent import srt as SRT
from vncontent import stages as S
from vncontent import story as T
from vncontent.runner import Runner, StageError


def quiet(_):
    pass


PRESET = """
id = "thu"
title = "Thể loại thử"
[voice]
style = "truyen-ma"
speed = 0.95
one_voice = true
[text]
lexicon = { "Tiêu Viêm" = "Tiêu Diêm", "Tiêu" = "Tiểu" }
[visual]
scene_seconds = 3
zoom_max = 1.1
[audio]
bgm_volume = 0.2
lufs = -16
[meta]
hashtags = ["#truyenaudio", "tienhiep"]
ai_note = "Giọng đọc AI."
desc_footer = "Đăng ký kênh nhé."
hook_prompt = "hãy viết hook"
[safety]
flag = ["chặt đứt đầu"]
"""


@pytest.fixture
def scfg(cfg, tmp_path):
    d = tmp_path / "presets"
    d.mkdir()
    (d / "thu.toml").write_text(PRESET, encoding="utf-8")
    cfg.raw["story"].update(presets_dir=str(d), preset="thu")
    return cfg


def story_job(store, scfg, tmp_path, **data):
    ch = []
    for i, txt in enumerate(["Chương một. Tiêu Viêm bước ra.", "Chương hai. Hắn chặt đứt đầu kẻ địch."], 1):
        p = tmp_path / f"chuong{i}.txt"
        p.write_text(txt, encoding="utf-8")
        ch.append(str(p))
    job = store.add(ch[0], "story", data={"chapters": ch, "preset": "thu", **data})
    return store.get(job.id)


# ---- SRT --------------------------------------------------------------------------------------

CUES = [SRT.Cue(0, 4, "Mở đầu."), SRT.Cue(4.5, 9, "Giữa câu,"), SRT.Cue(9, 14, "hết câu."),
        SRT.Cue(14, 20, "Câu tiếp theo!"), SRT.Cue(20, 26, "Chưa xong"), SRT.Cue(26, 30, "Kết thúc…")]


def test_srt_roundtrip_and_times():
    text = SRT.format_srt(CUES)
    back = SRT.parse_srt("\ufeff" + text.replace("\n", "\r\n"))
    assert back == CUES
    assert SRT.fmt_time(3725.5) == "01:02:05,500"
    assert SRT.hms(75) == "1:15" and SRT.hms(3725) == "1:02:05"
    assert SRT.parse_clock("12:30") == 750 and SRT.parse_clock("1:02:05") == 3725 and SRT.parse_clock("90") == 90
    with pytest.raises(ValueError):
        SRT.parse_clock("1:2:3:4")


def test_midroll_points_land_on_sentence_ends():
    cues = [SRT.Cue(i * 5, i * 5 + 5, "câu." if i % 4 == 3 else "giữa câu,") for i in range(60)]   # 300 s
    pts = SRT.midroll_points(cues, 300, every=100, tail=20)
    assert pts and all(any(abs(p - c.end) < 1e-6 and c.ends_sentence for c in cues) for p in pts)
    assert pts == sorted(pts) and all(p >= 100 for p in pts[:1])
    assert SRT.midroll_points(cues, 300, every=1000) == []          # ngắn hơn khoảng cách -> không có điểm
    assert SRT.midroll_points([], 300, every=100) == []


def test_pick_window_ends_on_sentence_within_limit():
    start, end = SRT.pick_window(CUES, target_end=27, max_seconds=15)
    assert end == 20 and start >= 5 and end - start <= 15           # câu 20-26 chưa kết câu -> lùi về 20
    # câu dài hơn giới hạn: lấy phần cuối
    s2, e2 = SRT.pick_window([SRT.Cue(0, 100, "rất dài.")], 100, 59)
    assert (s2, e2) == (41, 100)
    with pytest.raises(ValueError):
        SRT.pick_window([], 10, 5)


def test_shift_cues():
    out = SRT.shift_cues(CUES, 9, 20)
    assert [c.text for c in out] == ["hết câu.", "Câu tiếp theo!"] and out[0].start == 0 and out[-1].end == 11
    assert SRT.shift_cues(CUES, 3, 5)[0] == SRT.Cue(0, 1, "Mở đầu.")        # cắt giữa câu


# ---- phần thuần của story ---------------------------------------------------------------------

def test_expand_chapters_natural_order(tmp_path):
    for n in ("c10.txt", "c2.txt", "c1.md", "ghichu.pdf"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    got = [p.name for p in T.expand_chapters([tmp_path])]
    assert got == ["c1.md", "c2.txt", "c10.txt"]
    with pytest.raises(StageError, match="Không thấy"):
        T.expand_chapters([tmp_path / "khong-co.txt"])
    (tmp_path / "rong").mkdir()
    with pytest.raises(StageError, match="không có chương"):
        T.expand_chapters([tmp_path / "rong"])


def test_lexicon_prefers_longest_key_and_safety_is_case_insensitive():
    assert T.apply_lexicon("Tiêu Viêm và Tiêu Nại", {"Tiêu": "Tiểu", "Tiêu Viêm": "Tiêu Diêm"}) == "Tiêu Diêm và Tiểu Nại"
    assert T.safety_hits("Chặt đứt đầu, chặt đứt đầu", ["chặt đứt đầu", "máu"]) == {"chặt đứt đầu": 2}


def test_build_script_and_titles():
    s = T.build_script(["C1", "C2"], hook="Hook", recap="Tóm tắt", gap="[nghỉ 1s]")
    assert s == "Hook\n\n[nghỉ 1s]\n\nTóm tắt\n\n[nghỉ 1s]\n\nC1\n\n[nghỉ 1s]\n\nC2\n"
    assert T.build_script(["C1"], gap="") == "C1\n"
    assert T.episode_title({"title": "Riêng"}, []) == "Riêng"
    assert T.episode_title({"series_title": "Vạn Cổ", "ep": 3}, []) == "Vạn Cổ - Tập 3"
    assert T.episode_title({}, ["D:/x/chuong1.txt"]) == "chuong1"


def test_story_desc():
    d = T.story_desc(hook="Dòng\nmở  đầu", n_chapters=3, ep=2, footer="Đăng ký", ai_note="AI", hashtags=["#a", "b"])
    assert d.splitlines() == ["Dòng mở đầu", "Tập 2 · 3 chương", "Đăng ký", "AI", "#a #b"]
    assert T.story_desc(hook="", n_chapters=1, ep=None, footer="", ai_note="", hashtags=[]) == ""


def test_scene_plan_sums_to_duration():
    plan = T.scene_plan(62.0, 25, 25)
    assert plan == [625, 625, 300] and sum(plan) == 62 * 25
    assert T.scene_plan(10.0, 25, 25) == [250]
    assert T.scene_plan(50.0, 25, 25) == [625, 625]      # chia hết: không sinh cảnh rỗng


def test_scene_filter_alternates_zoom_direction():
    zin, zout = T.scene_filter(1920, 1080, 25, 250, 0, 1.12), T.scene_filter(1920, 1080, 25, 250, 1, 1.12)
    assert "zoompan=z='min(zoom+" in zin and "if(eq(on,0),1.12" in zout
    assert "s=1920x1080" in zin and "d=250" in zin and "scale=3840:2160" in zin


def test_audio_graph():
    assert T.audio_graph(False, 0.18, -16) is None
    g = T.audio_graph(True, 0.18, -16)
    assert "sidechaincompress" in g and "volume=0.18" in g and "loudnorm=I=-16" in g and g.endswith("[a]")


def test_real_presets_load_and_missing_preset_lists_available(cfg):
    for name in ("tien-hiep-hac-am", "linh-di-dan-gian", "tam-su-gia-dinh"):
        p = T.load_preset(cfg, name)
        assert p["id"] == name and p["voice"]["style"] and p["visual"]["scene_seconds"] > 0
    with pytest.raises(StageError, match="tien-hiep-hac-am"):
        T.load_preset(cfg, "khong-co")


# ---- các bước ---------------------------------------------------------------------------------

def test_ingest_builds_script_with_hook_lexicon_and_safety_report(store, scfg, tmp_path):
    job = story_job(store, scfg, tmp_path, hook="Tiêu Viêm sắp chết.", recap="Tập trước.")
    lines: list[str] = []
    out = T.ingest(job, scfg, Runner(echo=lines.append))
    script = Path(out["script"]).read_text(encoding="utf-8")
    assert script.startswith("Tiêu Diêm sắp chết.\n\n[nghỉ 1s]\n\nTập trước.")
    assert "Tiêu Diêm bước ra" in script and "Tiêu Viêm" not in script
    assert out["chapter_count"] == 2 and out["safety_hits"] == {"chặt đứt đầu": 1}
    assert (Path(out["script"]).parent / "safety.txt").is_file() and any("không hợp quảng cáo" in l for l in lines)
    assert (scfg.assets_dir("thu") / "images").is_dir() and (scfg.assets_dir("thu") / "bgm").is_dir()


def test_ingest_fails_clearly(store, scfg, tmp_path):
    job = store.add("x.txt", "story", data={"preset": "thu", "chapters": [str(tmp_path / "mat.txt")]})
    with pytest.raises(StageError, match="Không thấy file chương"):
        T.ingest(store.get(job.id), scfg, Runner(echo=quiet))
    job = store.add("x.txt", "story", data={"preset": "thu"})
    with pytest.raises(StageError, match="chưa có chương"):
        T.ingest(store.get(job.id), scfg, Runner(echo=quiet))


def test_dub_command_follows_preset_and_series_cast(store, scfg, tmp_path, fake_subprocess):
    job = story_job(store, scfg, tmp_path, script=str(tmp_path / "script.txt"), series="van-co")
    with pytest.raises(StageError, match="thiếu voice.wav"):          # subprocess giả không tạo file
        T.dub(job, scfg, Runner(echo=quiet))
    cmd = fake_subprocess.calls[0]
    assert cmd[:5] == ["python-vndub", "-m", "vndub.cli", "speak", "-f"]
    assert cmd[cmd.index("--style") + 1] == "truyen-ma" and cmd[cmd.index("--speed") + 1] == "0.95"
    assert "--one-voice" in cmd and cmd[cmd.index("--lufs") + 1] == "-16"
    assert Path(cmd[cmd.index("--cast") + 1]) == scfg.content_dir() / "series" / "van-co" / "cast.json"


def test_dub_without_series_leaves_cast_to_vndub(store, scfg, tmp_path, fake_subprocess):
    job = story_job(store, scfg, tmp_path, script=str(tmp_path / "script.txt"))
    scfg.raw["story"]["speak_args"] = ["--polish"]
    with pytest.raises(StageError):
        T.dub(job, scfg, Runner(echo=quiet))
    cmd = fake_subprocess.calls[0]
    assert "--cast" not in cmd and cmd[-1] == "--polish"


def test_dub_returns_audio_and_srt_when_vndub_wrote_them(store, scfg, tmp_path, fake_subprocess):
    def hook(argv):
        out = Path(argv[argv.index("-o") + 1])
        out.write_bytes(b"w"), out.with_suffix(".srt").write_text("1\n", encoding="utf-8")
    fake_subprocess.state["hook"] = hook
    job = story_job(store, scfg, tmp_path, script=str(tmp_path / "script.txt"))
    out = T.dub(job, scfg, Runner(echo=quiet))
    assert out["dub_audio"].endswith("voice.wav") and out["dub_srt"].endswith("voice.srt")


def test_meta_entries_long_youtube_only_and_short(store, scfg, tmp_path):
    job = story_job(store, scfg, tmp_path, ep=4, series_title="Vạn Cổ", hook="Dòng một.\nDòng hai.", chapter_count=2,
                    long="L.mp4", short="S.mp4", thumb="t.jpg")
    out = T.meta(job, scfg, Runner(echo=quiet))
    long, short = json.loads(Path(out["meta"]).read_text(encoding="utf-8"))
    assert long["file"] == "L.mp4" and long["title"] == "Vạn Cổ - Tập 4" and long["platforms"] == ["youtube"]
    assert long["thumbnail"] == "t.jpg" and long["tags"] == ["truyenaudio", "tienhiep"]
    assert long["desc"].splitlines() == ["Dòng một.", "Tập 4 · 2 chương", "Đăng ký kênh nhé.", "Giọng đọc AI.",
                                         "#truyenaudio #tienhiep"]
    assert short["file"] == "S.mp4" and short["title"] == "Dòng một. #shorts" and "platforms" not in short
    assert short["desc"].endswith("#truyenaudio #tienhiep #shorts") and short["thumbnail"] == ""


def test_publish_respects_entry_platforms(store, scfg, tmp_path, fake_subprocess):
    scfg.raw["publish"]["platforms"] = ["youtube", "facebook"]
    job = story_job(store, scfg, tmp_path, long="L.mp4", short="S.mp4")
    meta = scfg.job_dir(job.id) / "meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps([
        {"file": "L.mp4", "title": "L", "desc": "", "tags": [], "thumbnail": "", "platforms": ["youtube"]},
        {"file": "S.mp4", "title": "S", "desc": "", "tags": [], "thumbnail": ""}]), encoding="utf-8")
    store.update(job.id, merge={"meta": str(meta)})
    out = S.publish(store.get(job.id), scfg, Runner(echo=quiet))
    files = [(c[1], c[c.index("--file") + 1]) for c in fake_subprocess.calls]
    assert files == [("youtube", "L.mp4"), ("youtube", "S.mp4"), ("facebook", "S.mp4")]
    assert out["published"] == ["facebook:2", "youtube:1", "youtube:2"]


# ---- CLI --------------------------------------------------------------------------------------

@pytest.fixture
def cli_cfg(scfg, tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        f"[paths]\nmedia_root = '{scfg.raw['paths']['media_root']}'\n"
        f"[story]\npresets_dir = '{scfg.raw['story']['presets_dir']}'\npreset = 'thu'\n"
        "[tools]\nvndub_python = 'python-vndub'\nsau = 'sau-fake'\n", encoding="utf-8")
    monkeypatch.delenv("VNC_CONFIG", raising=False)
    return str(path)


def run_cli(cli_cfg, *argv):
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", cli_cfg, *argv])
    return e.value.code


def test_cli_story_adds_job(cli_cfg, scfg, tmp_path, capsys):
    (tmp_path / "hook.txt").write_text("Hook từ file.\n", encoding="utf-8")
    chdir = tmp_path / "chuong"
    chdir.mkdir()
    for n in ("c2.txt", "c10.txt", "c1.txt"):
        (chdir / n).write_text("nội dung", encoding="utf-8")
    code = run_cli(cli_cfg, "story", str(chdir), "--series", "van-co", "--ep", "3", "--hook", f"@{tmp_path / 'hook.txt'}",
                   "--short-end", "12:30", "--series-title", "Vạn Cổ")
    assert code == 0 and "Đã thêm job truyện #1: 3 chương" in capsys.readouterr().out
    from vncontent.store import Store
    s = Store(scfg.db_path())
    j = s.get(1)
    s.close()
    assert j.kind == "story" and [Path(c).name for c in j.data["chapters"]] == ["c1.txt", "c2.txt", "c10.txt"]
    assert (j.data["series"], j.data["ep"], j.data["hook"], j.data["short_end"], j.data["preset"]) == \
           ("van-co", 3, "Hook từ file.", 750, "thu")


def test_cli_story_rejects_bad_input(cli_cfg, tmp_path):
    assert "Không thấy" in str(run_cli(cli_cfg, "story", str(tmp_path / "khong-co.txt")))
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    assert "preset" in str(run_cli(cli_cfg, "story", str(tmp_path / "c.txt"), "--preset", "sai"))
    assert "--series" in str(run_cli(cli_cfg, "story", str(tmp_path / "c.txt"), "--series", "a b"))
    assert "--short-end" in str(run_cli(cli_cfg, "story", str(tmp_path / "c.txt"), "--short-end", "a:b"))


def test_cli_clean_requires_done_and_removes_heavy_files(cli_cfg, scfg, store, tmp_path, capsys):
    job = store.add("c.txt", "story", data={"preset": "thu"})
    jd = scfg.job_dir(job.id)
    (jd / "dub").mkdir(parents=True)
    (jd / "dub" / "voice.wav").write_bytes(b"0" * 2048)
    (jd / "render" / "scenes").mkdir(parents=True)
    (jd / "render" / "scenes" / "s0.mp4").write_bytes(b"0" * 1024)
    (jd / "render" / "long.mp4").write_bytes(b"keep")
    store.close()
    assert "chưa đăng xong" in str(run_cli(cli_cfg, "clean", "1"))
    assert (jd / "dub" / "voice.wav").exists()
    assert run_cli(cli_cfg, "clean", "1", "--force") == 0
    assert not (jd / "dub" / "voice.wav").exists() and not (jd / "render" / "scenes").exists()
    assert (jd / "render" / "long.mp4").read_bytes() == b"keep"
    assert "giải phóng" in capsys.readouterr().out
