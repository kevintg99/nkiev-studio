"""Fotos dos nomes do roteiro: o diretor lista, o bot baixa para library/mixkit/_auto com o "say" pronto."""

import io
import json
from types import SimpleNamespace

from PIL import Image

from broll_bot import named_images
from broll_bot.config import NamedImagesConfig
from broll_bot.director import Named, validate
from broll_bot.models import MediaType
from broll_bot.named_images import fetch_named, fit_16x9, library_has
from broll_bot.providers.local_library import LocalLibraryProvider


def _sentences(texts):
    return [SimpleNamespace(index=i, text=t, start=i * 4.0, end=i * 4.0 + 4) for i, t in enumerate(texts)]


def _jpeg(w=800, h=800):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def test_director_keeps_only_names_the_narration_says():
    sents = _sentences(["Number nine, the Optima YellowTop.", "Costco sells the Kirkland Signature AGM.",
                        "The plain Kirkland is cheaper."])
    raw = {"named": [
        {"name": "Optima YellowTop", "query": "Optima YellowTop D35 car battery", "say": ["YellowTop", "Optima"]},
        {"name": "Kirkland Signature", "query": "Kirkland Signature car battery", "say": ["Kirkland"],
         "say_not": ["Kirkland Signature AGM", "DieHard"]},
        {"name": "DieHard Gold", "query": "DieHard Gold battery", "say": ["DieHard"]},  # o roteiro não cita
    ]}
    plan = validate(raw, sents)
    assert [n.name for n in plan.named] == ["Optima YellowTop", "Kirkland Signature"]
    assert plan.named[1].say_not == ["Kirkland Signature AGM"]
    assert any("DieHard" in d for d in plan.dropped)


def test_product_photo_becomes_16x9_with_the_whole_product_visible():
    img = fit_16x9(_jpeg(800, 800), min_side=400)
    assert img.size == (1920, 1080)
    r, g, b = img.getpixel((960, 540))
    assert r > 150 and g < 80  # o produto inteiro no meio
    assert fit_16x9(_jpeg(200, 200), min_side=400) is None  # miniatura pequena demais fica de fora
    assert fit_16x9(b"<html>bloqueado</html>", min_side=400) is None


def test_downloads_photo_with_say_rules_and_skips_names_already_in_library(tmp_path, monkeypatch):
    # bateria Optima: na v1 das 9 baterias a foto teve que ser baixada e etiquetada à mão
    lib = tmp_path / "mixkit"
    (lib / "refs").mkdir(parents=True)
    (lib / "refs" / "kirkland.json").write_text(json.dumps({"say": ["Kirkland"]}), encoding="utf-8")
    searched = []

    def fake_search(query, key, num=10):
        searched.append(query)
        return [("https://x/competitor.jpg", "https://x/p", "x.com (web)", "DieHard Platinum battery"),
                ("https://x/optima.jpg", "https://shop/optima", "shop.com (foto da web, licença não verificada)",
                 "OPTIMA YellowTop D35 Battery")]

    monkeypatch.setattr(named_images, "serper_search", fake_search)
    monkeypatch.setattr(named_images, "_download", lambda url: _jpeg())
    named = [Named("Optima YellowTop", "Optima YellowTop D35 car battery", ["YellowTop", "Optima"], ["Optima Red"]),
             Named("Kirkland Signature", "Kirkland Signature battery", ["Kirkland"])]
    cfg = NamedImagesConfig(per_name=2)
    report = fetch_named(named, lib, cfg, key="k")
    assert searched == ["Optima YellowTop D35 car battery"]  # a Kirkland já estava na biblioteca
    assert report["downloaded"] == {"Optima YellowTop": ["optima-yellowtop.jpg"]}  # a do concorrente não entra
    meta = json.loads((lib / "_auto" / "optima-yellowtop.json").read_text(encoding="utf-8"))
    assert meta["say"] == ["YellowTop", "Optima"] and meta["requires"] == ["YellowTop", "Optima"]
    assert meta["say_not"] == ["Optima Red"] and "licença não verificada" in meta["credit"]
    assert library_has(lib, Named("Optima", "", ["optima"]))


def test_library_photo_counts_for_longer_name_but_not_for_the_excluded_one(tmp_path):
    (tmp_path / "kirkland.json").write_text(json.dumps({"say": ["Kirkland"], "say_not": ["Kirkland Signature AGM"]}),
                                            encoding="utf-8")
    assert library_has(tmp_path, Named("Kirkland Signature", "", ["Kirkland Signature"]))
    assert not library_has(tmp_path, Named("Kirkland Signature AGM", "", ["Kirkland Signature AGM"]))


def test_wikimedia_result_must_show_what_the_thing_is(tmp_path, monkeypatch):
    # sem a chave do Google, a Wikimedia devolveu leite e garrafa d'água da Kirkland para a bateria Kirkland
    monkeypatch.setattr(named_images, "_download", lambda url: _jpeg())

    def wiki(query):
        return [("https://u/milk.jpg", "p", "c", "Kirkland Signature Fat Free Milk"),
                ("https://u/water.jpg", "p", "c", "Kirkland Signature water bottles"),
                ("https://u/logo.jpg", "p", "c", "Rocky Mountaineer New Logo Canada")]

    named = [Named("Kirkland Signature", "Kirkland Signature car battery", ["Kirkland Signature"]),
             Named("Transport Canada", "Transport Canada logo", ["Transport Canada"])]
    report = fetch_named(named, tmp_path, NamedImagesConfig(), key=None, fallback=wiki)
    assert report["downloaded"] == {} and report["missing"] == ["Kirkland Signature", "Transport Canada"]


def test_wikimedia_file_name_decides_not_the_description(tmp_path, monkeypatch):
    # vídeo da garantia Toyota: "2021 Toyota Highlander SUV" recusava todos os Highlander (o título não diz "SUV")
    # e a descrição do logo da Sky Tamil News ("global Tamil media") passava por logo da Global News
    from broll_bot.named_images import commons_title

    monkeypatch.setattr(named_images, "_download", lambda url: _jpeg())
    pages = {"https://commons.wikimedia.org/wiki/File:Sky_Tamil_News_Logo.png":
             "Sky Tamil News Logo The Sky Tamil News logo reflects a modern and global Tamil media identity",
             "https://commons.wikimedia.org/wiki/File:2021_Toyota_Highlander_2.5_Hybrid.jpg":
             "2021 Toyota Highlander 2.5 Hybrid Toyota Highlander"}

    def wiki(query):
        return [(f"https://u/{i}.jpg", page, "c", commons_title(SimpleNamespace(page_url=page, description=desc)))
                for i, (page, desc) in enumerate(pages.items())]

    named = [Named("Global News", "Global News logo", ["Global News"]),
             Named("2021 Toyota Highlander", "2021 Toyota Highlander SUV", ["Highlander"])]
    report = fetch_named(named, tmp_path, NamedImagesConfig(per_name=1), key=None, fallback=wiki)
    assert report["downloaded"] == {"2021 Toyota Highlander": ["2021-toyota-highlander.jpg"]}
    assert report["missing"] == ["Global News"]


def test_name_in_parentheses_or_acronym_of_the_query_is_enough():
    from broll_bot.named_images import _mentions

    assert _mentions("OMVIC logo", "OMVIC (Ontario Motor Vehicle Industry Council)", "OMVIC logo", strict=True)
    assert _mentions("NHTSA logo", "National Highway Traffic Safety Administration", "NHTSA logo", strict=True)
    assert not _mentions("2015 Toyota Camry LE", "Toyota Camry (2021)", "2021 Toyota Camry", strict=True)
    assert not _mentions("Quebec City skyline", "Office de la protection du consommateur (Quebec)",
                         "Office de la protection du consommateur Quebec logo", strict=True)


def test_without_web_key_uses_wikimedia(tmp_path, monkeypatch):
    monkeypatch.setattr(named_images, "_download", lambda url: _jpeg())
    calls = []

    def wiki(query):
        calls.append(query)
        return [("https://upload/ct.jpg", "https://commons/ct", "Foo (CC BY 4.0)", "Canadian Tire store Toronto")]

    report = fetch_named([Named("Canadian Tire", "Canadian Tire store exterior", ["Canadian Tire"])],
                         tmp_path, NamedImagesConfig(), key=None, fallback=wiki)
    assert calls == ["Canadian Tire store exterior"]
    assert report["downloaded"] == {"Canadian Tire": ["canadian-tire.jpg"]}


def test_wikimedia_limit_does_not_crash_the_video_and_calls_go_one_at_a_time(tmp_path, monkeypatch):
    # vídeo da garantia Toyota: 6 buscas juntas na Wikimedia estouraram o limite, e o aviso do erro (com "name" no
    # extra do log) derrubou o vídeo inteiro
    import threading
    import time

    from broll_bot.errors import RateLimitedError

    monkeypatch.setattr(named_images, "_download", lambda url: _jpeg())
    lock, busy, overlap = threading.Lock(), [0], []

    def wiki(query):
        with lock:
            busy[0] += 1
            overlap.append(busy[0])
        time.sleep(0.02)
        with lock:
            busy[0] -= 1
        if "Global News" in query:
            raise RateLimitedError("wikimedia: limite local atingido (espera > 20s)")
        return [("https://u/h.jpg", "p", "c", "2021 Toyota Highlander SUV")]

    named = [Named("Global News", "Global News logo", ["Global News"]),
             Named("Toyota Highlander", "Toyota Highlander SUV", ["Highlander"]),
             Named("CAMVAP", "CAMVAP arbitration", ["CAMVAP"])]
    report = fetch_named(named, tmp_path, NamedImagesConfig(), key=None, fallback=wiki)
    assert max(overlap) == 1
    assert report["downloaded"] == {"Toyota Highlander": ["toyota-highlander.jpg"]}
    assert report["missing"] == ["Global News", "CAMVAP"]


def test_auto_folder_name_is_not_a_tag(tmp_path):
    # pasta genérica vira etiqueta de todos os arquivos ("car-battery-canada"): a _auto não pode
    lib = tmp_path / "mixkit"
    (lib / "_auto").mkdir(parents=True)
    Image.new("RGB", (640, 360)).save(lib / "_auto" / "optima-yellowtop.jpg")
    prov = LocalLibraryProvider(lib, tmp_path / "idx.json")
    (cand,) = [c for c in prov.entries if c.media_type is MediaType.IMAGE]
    assert "auto" not in cand.tags and "optima" in cand.tags
