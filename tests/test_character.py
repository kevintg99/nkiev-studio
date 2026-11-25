"""Fotos do personagem em cena: momentos (depois do avatar, momentos fortes), geração com OpenAI falsa, cache,
custo e o encaixe nos shots de b-roll."""

import base64
import json
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from broll_bot import usage
from broll_bot.character import CharacterJob, OpenAIImages, pick_moments, place, start_character, strong_score
from broll_bot.config import AppConfig, CharacterConfig
from broll_bot.models import Sentence, Shot, Transition


def _sentences(spec):
    return [Sentence(i, t, 0, a, b) for i, (t, a, b) in enumerate(spec)]


def _png(color=(200, 60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 42), color).save(buf, "PNG")
    return buf.getvalue()


class FakeImages:
    def __init__(self):
        self.calls = []

    def edit(self, image, prompt, **kw):
        self.calls.append({"prompt": prompt, **kw, "ref": image.read()})
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(_png()).decode(), url=None)])


class FakeClient:
    def __init__(self):
        self.images = FakeImages()


SCRIPT = _sentences([
    ("Seventy thousand miles.", 0.0, 2.0),
    ("That is roughly where the average failure happened on this engine.", 2.2, 7.0),
    ("And this is not some rare engine you have never heard of, believe me.", 7.2, 12.0),
    ("Body text about the design of the timing drive.", 12.2, 60.0),
    ("And an engine without oil pressure destroys its own bearings in a very short time.", 60.2, 66.0),
    ("How strong the evidence of failures is.", 66.2, 70.0),
    ("Soulagez l'accélérateur et passez au point mort pendant les longs arrêts.", 70.2, 76.0),
    ("More body text here about the list and other things.", 76.2, 110.0),
    ("Et le pire, c'est la chaîne : si elle saute, le moteur est mort.", 110.2, 116.0),
    ("Even more body text here about the list and other things.", 116.2, 156.0),
    ("If this is useful, subscribe now and it will save you an engine from death.", 156.2, 162.0),
    ("More body text.", 162.2, 230.0),
    ("I'm Graham Mercer. Look after your car.", 230.2, 236.0),
])
WINDOWS = [(0.0, 7.3, "intro"), (156.1, 162.3, "cta"), (230.0, 236.0, "outro")]


def test_moments_after_avatar_and_strong_ones():
    ms = pick_moments(SCRIPT, WINDOWS, CharacterConfig(min_gap=30))
    kinds = [(m.kind, round(m.at)) for m in ms]
    assert ("after_avatar", 7) in kinds and ("after_avatar", 162) in kinds  # depois da abertura e da inscrição
    assert not any(m.kind == "after_avatar" and m.at > 230 for m in ms)  # depois do final não tem
    strong = [m.text for m in ms if m.kind == "strong"]
    assert any("destroys its own bearings" in t for t in strong) and any("moteur est mort" in t for t in strong)
    assert not any("evidence of failures" in t or "point mort" in t for t in strong)  # fracos/neutro
    assert not any("subscribe" in t for t in strong)  # chamada para se inscrever não
    assert [m.kind for m in pick_moments(SCRIPT, WINDOWS, CharacterConfig(strong_max=0))] == ["after_avatar"] * 2
    # espaçamento: com 75 s, o momento forte colado na imagem de depois do avatar fica de fora
    assert all(m.kind == "after_avatar" for m in pick_moments(SCRIPT, WINDOWS, CharacterConfig(min_gap=200)))


def test_strong_score_weights():
    assert strong_score("the engine is destroyed") >= 3 and strong_score("How strong the evidence of failures is") < 3
    assert strong_score("passez au point mort") == 0


def test_job_generates_with_the_channel_character_and_caches(tmp_path):
    ref = tmp_path / "mecanico.jpg"
    Image.new("RGB", (100, 100), (10, 20, 30)).save(ref)
    ms = pick_moments(SCRIPT, WINDOWS, CharacterConfig())

    def run():
        usage.reset()
        client = FakeClient()
        job = CharacterJob(CharacterConfig(), ref, pick_moments(SCRIPT, WINDOWS, CharacterConfig()),
                           tmp_path / "cache", maker=OpenAIImages(client)).start()
        return job, job.result(), client, usage.summary()

    job, ready, client, u = run()
    assert len(ready) == len(ms) and all(m.status == "ok" for m in ready)
    call = client.images.calls[0]
    assert call["ref"] == ref.read_bytes()  # a imagem do personagem do canal é a referência
    assert call["model"] == "gpt-image-1" and call["size"] == "1536x1024" and call["input_fidelity"] == "high"
    assert "same face" in call["prompt"]
    assert u["steps"]["imagens do personagem"]["usd"] == pytest.approx(0.063 * len(ms), abs=1e-4)
    job, ready, client, u = run()  # de novo: cache, sem custo
    assert all(m.status == "cache" for m in ready) and not client.images.calls and u["services_usd"] == 0


def test_place_skips_templates_and_avatar_shots():
    shots = [Shot(i, i, i * 5.0, i * 5.0 + 5, 5.0, Transition("cut", 0), start_frame=i * 150, end_frame=i * 150 + 150)
             for i in range(6)]
    shots[1].avatar = [{"path": "a.mp4"}]
    shots[2].template = "newsrank"
    from broll_bot.character import CharacterMoment

    ms = [CharacterMoment("after_avatar", 7.0, "x", path="/tmp/p1.png"),
          CharacterMoment("strong", 21.0, "y", path="/tmp/p2.png")]
    assert place(shots, ms, 30) == 2
    assert ms[0].shot == 3  # o 1 tem avatar, o 2 é template: entra no 3
    assert shots[3].candidate.provider == "character" and shots[3].layout["name"] == "kenburns"
    assert shots[4].media_path == "/tmp/p2.png"


def test_needs_channel_image_and_openai_key(tmp_path, monkeypatch):
    cfg = AppConfig()
    assert start_character(cfg, SCRIPT, WINDOWS, tmp_path, tmp_path) is None  # canal sem imagem
    (tmp_path / "p.jpg").write_bytes(_png())
    cfg.avatar.photo = "p.jpg"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert start_character(cfg, SCRIPT, WINDOWS, tmp_path, tmp_path) is None  # sem chave
    cfg.character.enabled = False
    assert start_character(cfg, SCRIPT, WINDOWS, tmp_path, tmp_path, maker=object()) is None  # desligado no vídeo


def test_video_switch_in_overrides(tmp_path):
    import yaml

    from broll_bot.channels import apply_overrides
    from broll_bot.looks import job_overrides

    cfg = AppConfig()
    over = tmp_path / "a.yaml"
    over.write_text(yaml.safe_dump(job_overrides("mix", character=False)), encoding="utf-8")
    apply_overrides(cfg, over)
    assert cfg.character.enabled is False
    assert "character" not in job_overrides("mix")
    assert Path  # noqa: B018


def test_channel_library_reuses_images_in_next_videos(tmp_path):
    """As imagens geradas ficam em canais/personagem/<canal>/ e voltam nos próximos vídeos do canal: momento
    parecido reaproveita (de graça); biblioteca cheia só reaproveita; outro personagem tem outra biblioteca."""
    from broll_bot.character import CharacterLibrary, CharacterMoment

    ref = tmp_path / "mecanico.jpg"
    Image.new("RGB", (100, 100), (10, 20, 30)).save(ref)
    lib_dir = tmp_path / "canais" / "personagem" / "graham"

    def run(texts, cfg=CharacterConfig()):
        usage.reset()
        client = FakeClient()
        ms = [CharacterMoment("strong", 30.0 * k, t) for k, t in enumerate(texts)]
        CharacterJob(cfg, ref, ms, tmp_path / "cache", maker=OpenAIImages(client), library_dir=lib_dir).start().result()
        return ms, client, usage.summary()

    ms, client, _ = run(["The injectors cost more than a holiday to replace.",
                         "The pump clicks off at one hundred and ten pounds."])
    assert [m.status for m in ms] == ["ok", "ok"] and len(client.images.calls) == 2
    saved = json.loads((lib_dir / "imagens.json").read_text(encoding="utf-8"))
    assert len(saved) == 2 and all((lib_dir / e["file"]).is_file() for e in saved)
    # vídeo seguinte: o momento dos injetores reaproveita; o do turbo (nada a ver) gera outra
    ms, client, u = run(["A set of injectors fitted at a garage costs a fortune to replace.",
                         "The turbo whistles and the boost drops on the motorway."])
    assert [m.status for m in ms] == ["reused", "ok"] and len(client.images.calls) == 1
    assert Path(ms[0].path).parent == lib_dir and u["steps"]["imagens do personagem"]["calls"] == 1
    # biblioteca cheia: só reaproveita (as menos usadas), nunca a mesma duas vezes no vídeo
    ms, client, u = run(["Completely unrelated sentence about weather patterns today.",
                         "Another unrelated line about holidays abroad this year."],
                        CharacterConfig(library_max=3))
    assert [m.status for m in ms] == ["reused", "reused"] and not client.images.calls and u["services_usd"] == 0
    assert ms[0].path != ms[1].path
    # imagem apagada na mão sai da lista; outro personagem (outra imagem) não usa a biblioteca deste
    lib = CharacterLibrary(lib_dir, "outro-personagem")
    assert lib.usable() == [] and lib.pick("injectors replace", set(), CharacterConfig(library_max=1)) is None
    ms, client, _ = run(["The injectors cost more than a holiday to replace."], CharacterConfig(reuse=False))
    assert ms[0].status == "cache" and not client.images.calls  # sem biblioteca: o cache do mesmo vídeo vale
