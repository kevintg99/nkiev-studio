"""Motion de retenção do vídeo curto: gancho, ênfase, desenhos, interrupções, contador e final em loop."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from broll_bot.models import Word
from broll_bot.shortsfx import plan_fx, render_fx
from broll_bot.shortsfx.plan import Segment, hook_text, max_gap, read_number, stamp_text

TEXT = ("This car was banned in three countries. Nobody expected the price: $45,000 for a used one. "
        "The real problem? Winter. It loses 40% of its range in the cold, and the battery is the secret. "
        "But the fastest version still hits 250 km/h. Insurance costs doubled in two years. "
        "Dealers never tell you this.")


def _words(text: str = TEXT, start: float = 0.3) -> list[Word]:
    out, t = [], start
    for raw in text.split():
        d = max(0.2, min(0.55, 0.06 * len(raw) + 0.12))
        out.append(Word(raw, round(t, 3), round(t + d, 3)))
        t += d + (0.28 if raw.endswith((".", "?", ":")) else 0.03)
    return out


def _segments(total: float, durs=(2.6, 3.2, 2.4, 3.6, 2.8, 3.1, 2.9, 3.3), **flags) -> list[Segment]:
    segs, a = [], 0.0
    for d in durs:
        segs.append(Segment(a, min(total, a + d), **flags))
        a += d
    if a < total:
        segs.append(Segment(a, total))
    return segs


def _plan(words=None, segs=None, **kw):
    words = words or _words()
    total = round(words[-1].end + 0.4, 2)
    return plan_fx(words, segs or _segments(total), total, "en", **{"seed": 3, **kw}), total


def test_numbers_said_become_counters_with_their_unit():
    w = _words("It costs $45,000 today, loses 40% and hits 250 km/h since 2019 with 3 seats.")
    got = {w[i].text: read_number(w, i, "en") for i in range(len(w))}
    assert (got["$45,000"].value, got["$45,000"].prefix, got["$45,000"].kind) == (45000, "$", "money")
    assert (got["40%"].value, got["40%"].suffix) == (40, "%")
    assert (got["250"].value, got["250"].suffix, got["250"].kind) == (250, " km/h", "speed")
    assert got["2019"] is None and got["3"] is None  # ano e número pequeno: sem contador
    pt = _words("Custa 5 mil reais.")
    assert read_number(pt, 1, "pt") is None  # "5" sozinho é pequeno
    assert read_number(_words("O preço: 89.900 reais."), 2, "pt").kind == "money"


def test_hook_uses_a_whole_short_phrase_never_a_cut_one():
    assert hook_text("", "", _words("This car was banned in three countries. Then...")) == \
        "This car was banned in three countries"
    long = _words("There are three cars on British roads right now that just about never break down.")
    assert hook_text("", "", long) == "There are three cars on British roads right now"
    assert hook_text("Banned in 3 countries", "", long) == "Banned in 3 countries"  # o da IA vence
    assert stamp_text("", _words(TEXT), "en") == "$45,000"
    assert stamp_text("proibido", [], "pt") == "PROIBIDO"


def test_plan_puts_each_effect_on_the_word_it_shows():
    plan, total = _plan(stamp="BANNED", teaser="list")
    kinds = {e.kind for e in plan.events}
    assert {"hook", "teaser", "punch", "freeze", "count", "loop"} <= kinds
    hook = plan.of("hook")[0]
    assert hook.t0 == 0 and hook.data["text"].startswith("This car was banned") and hook.data["stamp"] == "BANNED"
    words = _words()
    freeze = plan.of("freeze")[0]
    assert abs(freeze.t0 - (next(w for w in words if w.text == "problem?").end - 0.05)) < 1e-6
    count = plan.of("count")[0]
    assert count.data["value"] == 250 and count.t0 == next(w for w in words if w.text == "250").start
    assert plan.of("loop")[0].t1 == total
    # a legenda destaca as mesmas palavras
    assert next(w for w in words if w.text == "250").start in plan.emphasis
    # nunca dois desenhos grandes ao mesmo tempo
    big = [e for e in plan.events if e.kind in ("circle", "magnify", "freeze", "count", "sticker", "stack")]
    for a, b in zip(big, big[1:]):
        assert b.t0 >= a.t1 - 0.05 or {a.kind, b.kind} <= {"count", "sticker"}, (a, b)


def test_plan_is_deterministic_and_respects_density():
    a, _ = _plan()
    b, _ = _plan()
    assert [e.to_dict() for e in a.events] == [e.to_dict() for e in b.events]
    few, _ = _plan(density=0.4)
    assert len(few.events) < len(a.events)


def test_no_three_seconds_without_something_new():
    words = _words()
    total = round(words[-1].end + 0.4, 2)
    segs = _segments(total, durs=(6.0, 7.0, 8.0))  # planos longos: o bot preenche
    plan = plan_fx(words, segs, total, "en", seed=1)
    assert max_gap(plan, segs) <= 3.05


def test_templates_only_get_the_caption_emphasis():
    words = _words()
    total = round(words[-1].end + 0.4, 2)
    segs = [Segment(0, 4.0), Segment(4.0, total, template=True)]
    plan = plan_fx(words, segs, total, "en", seed=2)
    visual = [e for e in plan.events if e.kind not in ("loop",) and e.t0 >= 4.0]
    assert not visual
    assert any(t >= 4.0 for t in plan.emphasis)


def test_hook_text_is_skipped_when_a_template_opens_the_video():
    words = _words()
    total = round(words[-1].end + 0.4, 2)
    plan = plan_fx(words, [Segment(0, 3.5, template=True), Segment(3.5, total)], total, "en", seed=2)
    assert plan.of("hook")[0].data["text"] == ""


def test_our_punch_never_doubles_a_cut_that_already_punches():
    words = _words()
    total = round(words[-1].end + 0.4, 2)
    plain = plan_fx(words, _segments(total), total, "en", seed=3)
    punch = next(e for e in plain.of("punch") if not e.data.get("intro"))
    segs = [Segment(0, punch.t0 - 0.05), Segment(punch.t0 - 0.05, total, effect="punch")]
    plan = plan_fx(words, segs, total, "en", seed=3)
    assert not any(abs(e.t0 - punch.t0) < 0.01 for e in plan.of("punch"))


def test_cues_have_the_new_sounds():
    from broll_bot.sfx import NAMES, SfxBank

    plan, _ = _plan(stamp="BANNED")
    names = {c[1].split(":")[0] for c in plan.cues()}
    assert {"bass", "stamp", "snap"} <= names <= set(NAMES)
    bank = SfxBank(None, 1)
    for n in ("bass", "stamp", "scratch"):
        assert len(bank.get(n)) > 1000


def test_caption_emphasis_colors_and_grows_the_word():
    from broll_bot.captions import build_ass

    words = _words("It costs a fortune never seen before.")
    ass = build_ass(words, 1080, 1920, "palavra", (255, 0, 0), emphasis=[words[4].start])
    never = next(x for x in ass.splitlines() if x.endswith("NEVER"))
    assert "fscx138" in never and "&H000000FF" in never
    other = next(x for x in ass.splitlines() if x.endswith("COSTS"))
    assert "fscx138" not in other
    chunk = build_ass(words, 1080, 1920, "frase", (255, 0, 0), emphasis=[words[4].start])
    assert "fscx124" in chunk


def test_director_hook_and_stamp_must_come_from_the_first_sentences():
    from broll_bot import director
    from broll_bot.models import Sentence

    ss = [Sentence(0, "This car was banned in three countries.", 0, 0, 3),
          Sentence(1, "It costs forty-five thousand dollars.", 0, 3, 6)]
    plan = director.validate({"hook": "Banned in three countries", "stamp": "$45,000"}, ss, "en")
    assert plan.hook == "Banned in three countries" and plan.stamp == "$45,000"
    bad = director.validate({"hook": "The best SUV ever made", "stamp": "$99,000"}, ss, "en")
    assert bad.hook == "" and bad.stamp == ""
    assert any("gancho" in d for d in bad.dropped) and any("carimbo" in d for d in bad.dropped)


def test_vertical_profile_turns_the_effects_on():
    from broll_bot.config import AppConfig

    v = AppConfig().vertical
    assert v.fx and v.fx_hook and v.ending == "loop" and v.fx_density == 1.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sem ffmpeg")
def test_render_applies_the_effects_and_keeps_audio(tmp_path: Path):
    w, h, fps = 216, 384, 30
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={fps}:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    words = _words("This car was banned. Is it worth $45,000? Never.", start=0.1)
    plan = plan_fx(words, [Segment(0, 1.5), Segment(1.5, 3.0)], 3.0, "en", seed=1, stamp="BANNED")
    dest = tmp_path / "fx.mp4"
    info = render_fx(src, plan, dest, w, h, fps, ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
                     grade=True)
    assert info["frames"] == 90 and info["touched"] > 10
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(dest)], capture_output=True, text=True, check=True).stdout.split()
    assert streams.count("video") == 1 and streams.count("audio") == 1

    def frame(path: Path, t: float) -> np.ndarray:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f",
                              "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)

    # o gancho escreve o título por cima no começo
    assert np.abs(frame(dest, 0.6) - frame(src, 0.6)).mean() > 8


def test_stickers_draw_every_icon():
    from broll_bot.motion.stickers import KINDS, sticker

    for k in KINDS:
        img = sticker(k, 120)
        assert isinstance(img, Image.Image) and img.mode == "RGBA"
        alpha = np.asarray(img.getchannel("A"))
        assert alpha.max() == 255 and alpha[0, 0] == 0  # borda transparente, ícone opaco
