"""Matéria de site citada na fala (vídeo de política do Gord Wallace: "Trump to swap some tariffs on Canadian goods",
com o trecho dito pela voz no marca-texto amarelo) e mais fotos para quem a fala cita a toda hora."""

import io
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image

import broll_bot.motion  # noqa: F401 - registra os templates
from broll_bot import director, named_images
from broll_bot.config import AppConfig, NamedImagesConfig
from broll_bot.director import Named
from broll_bot.models import SceneAnalysis
from broll_bot.motion.planner import MEDIA_FREE, TemplatePlanner
from broll_bot.motion.templates import TEMPLATES, TemplateSpec
from broll_bot.motion.templates_article import mark_span
from broll_bot.motion.templates_doc import template_cues
from broll_bot.named_images import fetch_named, wanted

CAPS = [(0.0, 2.0, "Then he said Canada would wait if it has to."),
        (2.0, 5.0, 'Months, or in his words, "another administration, we will do that."')]


def _spec(**kw):
    base = dict(kind="article", theme="navy", w=640, h=360, fps=10, frames=50, seed=1, lang="en",
                keyword="CBC News", subtitle="Oct 23", body="Trump to swap some tariffs on Canadian goods",
                labels=["Canada would wait if it has to"], captions=CAPS)
    base.update(kw)
    return TemplateSpec(**base)


def _yellow(img):
    a = np.asarray(img.convert("RGB")).astype(int)
    return int(((a[..., 0] > 230) & (a[..., 1] > 200) & (a[..., 2] < 110)).sum())


def test_quote_is_found_in_the_spoken_paragraph_ignoring_punctuation_and_case():
    words = "Then he said Canada would wait, if it has to.".split()
    assert mark_span(words, "canada would wait if it has to") == (3, 10)
    # a IA cortou/mudou uma ponta: fica o maior pedaço que casa
    assert mark_span(words, "Canada would wait if it must") == (3, 8)
    assert mark_span(words, "nothing like this") is None


def test_article_draws_page_and_the_highlight_comes_after_the_page():
    tpl = TEMPLATES["article"](_spec())
    first, late = tpl.frame(2), tpl.frame(45)
    assert first.size == (640, 360) and late.size == (640, 360)
    assert _yellow(first) == 0  # o marca-texto passa depois que a página entra
    assert _yellow(late) > 200
    # sem trecho citado: a página entra igual, sem amarelo
    plain = TEMPLATES["article"](_spec(labels=[]))
    assert _yellow(plain.frame(45)) == 0
    assert [c[1] for c in template_cues("article", _spec())][-1] == "swipe"


def _an(**kw):
    return SceneAnalysis(visual_summary="", queries=[], fallback_query="", **kw)


def test_article_needs_outlet_and_headline_or_quote_and_downloads_no_broll():
    assert "article" in MEDIA_FREE
    ok = _an(caption="CBC News", body="Trump to swap some tariffs")
    assert TemplatePlanner._data_ok("article", ok)
    assert TemplatePlanner._data_ok("article", _an(caption="CBC", highlights=["we will do that"]))
    assert not TemplatePlanner._data_ok("article", _an(body="sem veículo"))
    assert "article" in AppConfig().templates.styles


def _sentences(texts):
    return [SimpleNamespace(index=i, text=t, start=i * 12.0, end=i * 12.0 + 4) for i, t in enumerate(texts)]


def test_director_article_card_keeps_long_quote_and_drops_invented_headline():
    sents = _sentences(["Hello there, welcome back to the channel today.",
                        "CBC reported Friday that Trump said Canada would wait if it has to, for another administration.",
                        "Then Fox News said the deal was dead."])
    quote = "Canada would wait if it has to, for another administration"
    raw = {"cards": [
        {"first": 1, "last": 1, "kind": "article", "fields": {"caption": "CBC", "body": "Trump said Canada would wait",
                                                              "highlights": [quote]}},
        {"first": 2, "last": 2, "kind": "article", "fields": {"caption": "Fox News",
                                                              "body": "Senate leaders reject Ottawa ultimatum"}},
    ]}
    plan = director.validate(raw, sents, "en")
    assert [c.kind for c in plan.cards] == ["article"]
    assert plan.cards[0].fields["highlights"] == [quote]  # o trecho não é cortado nos 40 caracteres
    assert any("texto fora da fala" in d for d in plan.dropped)


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (900, 900), (90, 90, 90)).save(buf, "JPEG")
    return buf.getvalue()


def test_name_said_all_the_time_gets_more_photos_without_repeating_the_ones_it_has(tmp_path, monkeypatch):
    # o Trump citado 30 vezes num vídeo de 9 min: 2 fotos repetiam a mesma cara o vídeo inteiro
    sents = _sentences([f"Trump said it again, round {k}." for k in range(30)] + ["Carney stayed home."])
    plan = director.validate({"named": [{"name": "Donald Trump", "query": "Donald Trump portrait", "say": ["Trump"]},
                                        {"name": "Mark Carney", "query": "Mark Carney portrait", "say": ["Carney"]}]},
                             sents, "en")
    trump, carney = plan.named
    assert trump.mentions == 30 and carney.mentions == 1
    cfg = NamedImagesConfig()
    assert wanted(trump, cfg) == cfg.per_name_max and wanted(carney, cfg) == cfg.per_name

    lib = tmp_path / "mixkit"
    (lib / "_auto").mkdir(parents=True)
    Image.open(io.BytesIO(_jpeg())).save(lib / "_auto" / "donald-trump.jpg")
    (lib / "_auto" / "donald-trump.json").write_text(json.dumps({"say": ["Trump"], "page_url": "https://c/0"}),
                                                     encoding="utf-8")
    hits = [(f"https://c/{k}.jpg", f"https://c/{k}", "Wikimedia", f"Donald Trump official portrait {k}")
            for k in range(10)]
    monkeypatch.setattr(named_images, "_download", lambda url: _jpeg())
    report = fetch_named([trump], lib, cfg, key=None, fallback=lambda q: hits)
    got = report["downloaded"]["Donald Trump"]
    assert len(got) == cfg.per_name_max - 1  # já tinha 1
    assert "donald-trump.jpg" not in got  # não sobrescreve a que estava lá
    pages = [json.loads((lib / "_auto" / n).with_suffix(".json").read_text(encoding="utf-8"))["page_url"]
             for n in got]
    assert "https://c/0" not in pages
    # com as fotos completas, a próxima rodada não busca de novo
    again = fetch_named([trump], lib, cfg, key=None, fallback=lambda q: (_ for _ in ()).throw(AssertionError))
    assert again["already_in_library"] == 1
