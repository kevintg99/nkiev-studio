"""Perfis de canal: cada canal com idioma, moeda, paleta e o que não usa, sem um atrapalhar o outro."""

from pathlib import Path

from broll_bot.channels import apply_channel, channel_for_language, find_channel, list_channels
from broll_bot.cli import _default_output, _pick_channel, parse_args
from broll_bot.config import AppConfig
from broll_bot.cues import C, set_currency, set_language
from broll_bot.motion import gfx
from broll_bot.motion.overlays import OVERLAYS, OverlaySpec
from broll_bot.rhythm import pick_visual

ROOT = Path(__file__).resolve().parents[1]
FIXT = Path(__file__).resolve().parent / "fixtures"  # canais de exemplo fixos (os de canais/ mudam pelo app)


def test_the_two_channels_exist_and_are_found_by_name_or_language():
    names = {name: lang for _, name, lang in list_channels(FIXT)}
    assert names == {"Bernard Chevalier": "fr", "Jhonny Walt": "en"}
    assert find_channel(FIXT, "jhonny walt").stem == "jhonny_walt"
    assert find_channel(FIXT, "Bernard Chevalier").stem == "bernard_chevalier"
    assert channel_for_language(FIXT, "en").stem == "jhonny_walt"
    assert channel_for_language(FIXT, "fr").stem == "bernard_chevalier"
    assert channel_for_language(FIXT, "pl") is None


def test_uk_channel_profile_changes_only_what_it_defines():
    cfg = apply_channel(AppConfig(), FIXT / "canais" / "jhonny_walt.yaml")
    ch = cfg.channel
    assert (ch.name, ch.language, ch.currency) == ("Jhonny Walt", "en", "£")
    assert cfg.analysis.script_language == "en" and cfg.transcription.language == "en"
    assert "dossier" not in cfg.templates.styles and "file" not in cfg.overlays.kinds
    assert "showcase" in cfg.templates.styles and "impact" in cfg.overlays.kinds
    fr = apply_channel(AppConfig(), FIXT / "canais" / "bernard_chevalier.yaml")
    assert "dossier" in fr.templates.styles and "file" in fr.overlays.kinds and fr.channel.palette == {}


def test_uk_palette_is_dark_grey_with_red_and_french_stays_the_same():
    before = {k: dict(v) for k, v in gfx.THEMES.items()}
    cfg = apply_channel(AppConfig(), FIXT / "canais" / "jhonny_walt.yaml")
    gfx.set_brand(True)
    try:
        gfx.apply_palette(cfg.channel.palette)
        assert gfx.THEMES["channel"]["bg"] == (24, 25, 28) and gfx.accent() == (214, 36, 36)
        assert gfx.THEMES["channel_blueprint"]["bg"] == (8, 20, 52)  # a prancheta continua azul-escura
        spec = OverlaySpec.from_dict({"kind": "keyword", "title": "turbo", "w": 480, "h": 270, "fps": 30,
                                      "frames": 60, "start": 0.0})
        ov = OVERLAYS["keyword"](spec)
        assert ov.yellow == (214, 36, 36)  # filete vermelho
        gfx.apply_palette({})  # o francês (sem paleta) volta exatamente ao visual de antes
        assert all(gfx.THEMES[k] == before[k] for k in before) and gfx.accent() == (255, 204, 0)
    finally:
        gfx.set_brand(False)
        gfx.apply_palette(None)


def test_channel_currency_is_the_default_but_the_spoken_one_wins():
    set_language("en")
    try:
        set_currency("£")
        assert C("currency") == ("£", "")
        set_currency("")
        assert C("currency") == ("$", "")
    finally:
        set_currency(None)
        set_language("pt")


def test_channel_without_a_template_never_gets_it_from_the_rhythm():
    import random

    allowed = {"showcase", "gallery", "sketch"}  # sem dossiê
    seen = {pick_visual(random.Random(k), "", 5.0, "", allowed) for k in range(60)}
    assert "dossier" not in seen and seen <= allowed
    assert pick_visual(random.Random(1), "", 5.0, "", set()) == ""


def test_cli_picks_the_channel_by_the_script_language_and_saves_in_its_folder(tmp_path, monkeypatch):
    script = tmp_path / "my script.txt"
    script.write_text("Number 1: the Honda Fit. It is cheap to run and the engine is bulletproof. "
                      "You will find it everywhere and the parts are cheap.", encoding="utf-8")
    args = parse_args(["--audio", "a.mp3", "--script", str(script)])
    assert _pick_channel(args, FIXT).stem == "jhonny_walt"
    args = parse_args(["--audio", "a.mp3", "--script", str(script), "--canal", "Bernard Chevalier"])
    assert _pick_channel(args, FIXT).stem == "bernard_chevalier"
    assert _pick_channel(parse_args(["--audio", "a", "--script", str(script), "--canal", "nope"]), FIXT) is False
    cfg = apply_channel(AppConfig(), FIXT / "canais" / "jhonny_walt.yaml")
    out = _default_output(cfg, script, tmp_path)
    assert out.parent == tmp_path / "videos" / "Jhonny Walt" and out.name.endswith("_my script.mp4")
    assert _default_output(AppConfig(), script, tmp_path) == Path("video_final.mp4")
