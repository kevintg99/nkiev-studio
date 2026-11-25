"""Destaques: card de consumo, frase com marca-texto, galeria, mão desenhando e showcase lateral."""

import random
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw

from broll_bot.coherence import enforce_coherence
from broll_bot.config import AppConfig
from broll_bot.cues import set_language
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis, Shot, Transition, Word
from broll_bot.motion import TEMPLATES, gfx
from broll_bot.motion.overlays import OVERLAYS, OverlaySpec, overlay_cues
from broll_bot.motion.planner import plan_overlays
from broll_bot.motion.templates import MediaRef, TemplateSpec, render_template
from broll_bot.motion.templates_brand import Showcase
from broll_bot.rhythm import VISUALS, add_fact_overlays, add_rhythm, fuel_fact, pick_visual
from broll_bot.sfx import synth
from broll_bot.transcription import parse_script

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")


# --------------------------------------------------------------- consumo
@pytest.mark.parametrize("lang,text,title,unit,where", [
    ("fr", "Il tourne tranquillement, il consomme autour de 5 litres aux cent, et il se contente de vidanges.",
     "5", "L/100 km", ""),
    ("fr", "Le défaut, c'est la consommation : comptez 8 à 9 litres aux cent.", "8–9", "L/100 km", ""),
    ("fr", "En ville, elle consomme souvent autour de 4 litres aux cent.", "4", "L/100 km", "EN VILLE"),
    ("fr", "Elle se contente de 4,5 l/100 km sur autoroute.", "4,5", "L/100 km", "SUR ROUTE"),
    ("pt", "Na cidade ele faz 12 km/l, o que é ótimo.", "12", "km/l", "NA CIDADE"),
    ("pt", "Ele faz de 14 a 15 km por litro na estrada.", "14–15", "km/l", "NA ESTRADA"),
    ("pl", "W mieście pali około 6 litrów na sto.", "6", "l/100 km", "W MIEŚCIE"),
])
def test_fuel_is_detected_in_the_three_languages(lang, text, title, unit, where):
    set_language(lang)
    fact = fuel_fact(text)
    assert (fact["kind"], fact["title"], fact["detail"], fact["title_b"]) == ("fuel", title, unit, where)


def test_engine_size_and_oil_consumption_are_not_fuel():
    set_language("fr")
    assert not fuel_fact("Un vrai deux-litres atmosphérique, avec une chaîne de distribution.")
    assert not fuel_fact("Ce petit moteur turbo a connu des problèmes de consommation d'huile.")
    assert not fuel_fact("Le coffre fait 450 litres.")


def test_fuel_overlay_goes_on_the_scene_and_survives_coherence():
    set_language("fr")
    text = "Le petit bémol, c'est la consommation. Comptez 8 à 9 litres aux cent."
    scene = Scene(0, text, 0, 6, SceneAnalysis("car", ["car"], "car", overlay={"kind": "keyword", "title": "bémol"}))
    tpl = Scene(1, text, 6, 12, SceneAnalysis("car", ["car"], "car", style="specs"))
    notes = add_fact_overlays([scene, tpl])
    assert scene.analysis.overlay["kind"] == "fuel" and notes and not tpl.analysis.overlay
    enforce_coherence([scene])
    assert scene.analysis.overlay["title"] == "8–9"
    wrong = Scene(0, text, 0, 6, SceneAnalysis("car", ["car"], "car", overlay={
        "kind": "fuel", "title": "6", "detail": "L/100 km", "title_b": "", "detail_b": "", "lines": []}))
    enforce_coherence([wrong])
    assert not wrong.analysis.overlay  # número que ninguém falou


def test_fuel_card_counts_up_to_the_spoken_number():
    gfx.set_brand(True)
    try:
        spec = OverlaySpec.from_dict({"kind": "fuel", "title": "8–9", "detail": "L/100 km", "title_b": "EN VILLE",
                                      "w": 960, "h": 540, "fps": 30, "frames": 90, "start": 0.2, "lang": "fr"})
        ov = OVERLAYS["fuel"](spec)
        assert ov._num_text(1.0) == "8–9" and ov._num_text(0.5) == "4–4"
        early, late = ov.frame(3), ov.frame(60)
        assert late.getbbox() is not None
        box = late.getbbox()
        assert box[0] < 960 * 0.2 and box[3] > 540 * 0.6  # canto de baixo, à esquerda
        arr = np.asarray(late)
        yellow = (abs(arr[..., 0].astype(int) - gfx.BRAND["text"][0]) < 8) & (arr[..., 2] < 60) & (arr[..., 3] > 200)
        assert yellow.sum() > 200  # número amarelo
        assert (early.getbbox() or (0, 0, 0, 0)) != box
        names = [c[1] for c in overlay_cues(spec.__dict__, 90, 30)]
        assert "tick" in names and "impact_soft" in names
    finally:
        gfx.set_brand(False)


def _shots(bounds):
    shots = []
    for i, (a, b) in enumerate(bounds):
        s = Shot(i, 0, a, b, b - a, Transition("cut", 0), round(a * 30), round(b * 30))
        s.candidate = Candidate("x", str(i), MediaType.VIDEO, 1920, 1080, 10.0, None, None)
        shots.append(s)
    return shots


def test_fuel_and_phrase_land_on_the_shot_where_they_are_spoken():
    cfg = AppConfig()
    scene = Scene(0, "t", 0, 12, SceneAnalysis("x", ["x"], "x"))
    scene.analysis.overlay = {"kind": "fuel", "title": "8–9", "detail": "L/100 km"}
    words = [Word("Comptez", 7.0, 7.4), Word("8", 7.5, 7.7), Word("à", 7.7, 7.8), Word("9", 7.8, 8.0)]
    shots = _shots([(0, 4.0), (4.0, 6.5), (6.5, 12.0)])
    assert plan_overlays(shots, {0: scene}, cfg, words) == 1
    assert not shots[0].overlay and not shots[1].overlay
    assert shots[2].overlay["kind"] == "fuel" and shots[2].overlay["start"] == pytest.approx(1.0, abs=0.01)

    scene.analysis.overlay = {"kind": "phrase", "title": "Et c'est une erreur."}
    words = [Word("Et", 4.5, 4.6), Word("c'est", 4.6, 4.8), Word("une", 4.8, 4.9), Word("erreur", 4.9, 5.3)]
    shots = _shots([(0, 4.0), (4.0, 7.0), (7.0, 12.0)])
    assert plan_overlays(shots, {0: scene}, cfg, words) == 1
    assert shots[1].overlay["kind"] == "phrase"


# ---------------------------------------------------------------- frase
def test_phrase_overlay_highlights_the_key_word_in_yellow():
    gfx.set_brand(True)
    try:
        spec = OverlaySpec.from_dict({"kind": "phrase", "title": "Et c'est là que tout se joue, croyez-moi.",
                                      "lines": ["joue"], "w": 960, "h": 540, "fps": 30, "frames": 120, "start": 0.2})
        ov = OVERLAYS["phrase"](spec)
        assert [w[0] for w in ov.words if w[4]] == ["joue,"]
        late = np.asarray(ov.frame(80))
        box = Image.fromarray(late).getbbox()
        assert box is not None and box[1] > 540 * 0.5  # terço de baixo
        yellow = (late[..., 0] > 230) & (late[..., 1] > 180) & (late[..., 2] < 80) & (late[..., 3] > 200)
        assert yellow.sum() > 300  # o marca-texto
        white = (late[..., 0] > 245) & (late[..., 1] > 245) & (late[..., 2] > 245) & (late[..., 3] > 200)
        assert white.sum() > 300  # o resto da frase em branco com contorno
        early = np.asarray(ov.frame(8))
        assert not ((early[..., 0] > 230) & (early[..., 2] < 80) & (early[..., 3] > 200)).sum()  # marca só depois
    finally:
        gfx.set_brand(False)


def test_phrase_overlay_must_be_the_spoken_sentence():
    text = "Et c'est là que tout se joue. Le moteur est un vieux bloc Renault."
    ok = Scene(0, text, 0, 6, SceneAnalysis("car", ["car"], "car", overlay={"kind": "phrase",
                                                                            "title": "Et c'est là que tout se joue."}))
    bad = Scene(1, text, 6, 12, SceneAnalysis("car", ["car"], "car", overlay={
        "kind": "phrase", "title": "La fiabilité japonaise est imbattable depuis toujours."}))
    enforce_coherence([ok, bad])
    assert ok.analysis.overlay["kind"] == "phrase" and not bad.analysis.overlay


# ---------------------------------------------------------------- ritmo
SCRIPT = """Le design de la Yaris a été dessiné en Europe, avec une silhouette compacte.
Mais soulevez le capot et regardez le moteur, un bloc hybride que tout le monde connaît bien.
Et c'est là que tout se joue pour la fiabilité sur le long terme.
Le coffre est correct et la banquette arrière se replie facilement pour la famille entière.
Les pièces sont partout et elles coûtent trois fois rien chez votre garagiste du coin.
Beaucoup de gens la regardent de haut sur le marché de l'occasion, et ils ont tort.
Elle démarre tous les matins, hiver comme été, sans jamais faire de caprice.
Et quand on la revend, elle part en quelques jours seulement sur les annonces."""


def _scenes(subject):
    set_language("fr")
    sents = parse_script(" ".join([SCRIPT] * 3))
    t = 0.0
    for s in sents:
        d = len(s.text.split()) * 0.45
        s.start, s.end = t, t + d
        t += d
    return [Scene(k, s.text, s.start, s.end, SceneAnalysis("car", ["car"], "car", subject=subject))
            for k, s in enumerate(sents)], sents


def test_rhythm_alternates_photo_templates_and_adds_highlight_phrases():
    scenes, sents = _scenes("Toyota Yaris Hybrid")
    out, notes = add_rhythm(scenes, sents, gap=2.0, statement_every=1e9, lead_in=0.0, seed=4, phrase_chance=0.5)
    visuals = [s.analysis.style for s in out if s.analysis.style in VISUALS]
    phrases = [s.analysis.overlay for s in out if s.analysis.overlay.get("kind") in ("phrase", "impact")]
    assert len(set(visuals)) >= 2 and phrases
    assert all(a != b for a, b in zip(visuals, visuals[1:]))  # nunca o mesmo duas vezes seguidas
    assert all(s.analysis.caption == "Toyota Yaris Hybrid" for s in out
               if s.analysis.style in VISUALS and s.analysis.style not in ("annotate", "textmask", "wordpop"))
    assert not [n for n in enforce_coherence(out) if "removido" in n or "virou" in n]
    # o intervalo padrão ficou mais curto: mais recursos no mesmo trecho
    few, _ = add_rhythm(*_scenes("Toyota Yaris Hybrid"), gap=16.0, statement_every=1e9, seed=4)
    many, _ = add_rhythm(*_scenes("Toyota Yaris Hybrid"), statement_every=1e9, seed=4)
    count = lambda xs: sum(s.analysis.style != "normal" or bool(s.analysis.overlay) for s in xs)  # noqa: E731
    assert count(many) > count(few)


def test_design_talk_calls_the_drawing_hand():
    rng = random.Random(0)
    assert pick_visual(rng, "", 5.0, "Le design a été dessiné en Italie.") == "sketch"
    assert pick_visual(rng, "sketch", 5.0, "Le design a été dessiné en Italie.") != "sketch"
    assert pick_visual(rng, "", 3.6, "Le design.") != "sketch"  # desenho precisa de 4 s
    seen = {pick_visual(random.Random(k), "", 5.0) for k in range(40)}
    assert seen == set(VISUALS)


# ------------------------------------------------------------- templates
def _car(path):
    img = Image.new("RGB", (800, 500), (120, 150, 190))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 330, 800, 500], fill=(70, 72, 78))
    d.rounded_rectangle([120, 230, 680, 350], 40, fill=(170, 20, 30))
    d.polygon([(240, 235), (320, 150), (520, 150), (600, 235)], fill=(150, 15, 25))
    for cx in (230, 570):
        d.ellipse([cx - 60, 300, cx + 60, 420], fill=(20, 20, 22))
    img.save(path)
    return path


@ffmpeg
@pytest.mark.parametrize("kind,n", [("gallery", 3), ("gallery", 1), ("sketch", 1), ("showcase", 1), ("dossier", 1)])
def test_photo_templates_render(tmp_path, kind, n):
    p = _car(tmp_path / "car.jpg")
    gfx.set_brand(True)
    try:
        spec = TemplateSpec(kind, "channel", 320, 180, 30, 30, seed=2, media=[MediaRef(str(p))] * n,
                            keyword="Toyota Yaris Hybrid")
        dest = render_template(spec, tmp_path / f"{kind}.mp4", ["-c:v", "libx264", "-preset", "ultrafast",
                                                                 "-pix_fmt", "yuv420p"])
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                              "stream=nb_read_frames", "-of", "csv=p=0", str(dest)], capture_output=True, text=True)
        assert out.stdout.strip() == "30"
    finally:
        gfx.set_brand(False)


def test_sketch_draws_lines_with_a_hand_then_shows_the_photo(tmp_path):
    p = _car(tmp_path / "car.jpg")
    spec = TemplateSpec("sketch", "channel", 640, 360, 30, 150, seed=1, media=[MediaRef(str(p))], keyword="Yaris")
    tpl = TEMPLATES["sketch"](spec)
    x0, y0 = round(tpl.cx - tpl.pw / 2), round(tpl.cy - tpl.ph / 2)
    region = (x0 + 4, y0 + 4, x0 + tpl.pw - 4, y0 + tpl.ph - 4)

    def reds(frame):
        a = np.asarray(frame.convert("RGB").crop(region)).astype(int)
        return int(((a[..., 0] > 140) & (a[..., 1] < 60) & (a[..., 2] < 70)).sum())

    def light(frame, left):
        a = np.asarray(frame.convert("RGB").crop(region)).astype(int)
        half = a[:, : a.shape[1] // 2] if left else a[:, a.shape[1] // 2:]
        return int(((half[..., 0] > 190) & (half[..., 2] > 220)).sum())

    mid = tpl.frame(round((tpl.t_draw0 + tpl.draw_len * 0.4) * 30))
    assert reds(mid) == 0  # ainda é desenho, sem a foto
    assert light(mid, True) > light(tpl.frame(1), True)  # traços já apareceram à esquerda
    end = tpl.frame(149)
    assert reds(end) > 500  # a foto (carro vermelho) apareceu
    tpl.close()


def test_showcase_has_two_layouts():
    looks = {Showcase.variant(TemplateSpec("showcase", "channel", 320, 180, 30, 30, seed=s)) for s in range(20)}
    assert looks == {"center", "side"}


def test_pencil_sound_has_the_requested_length():
    audio = synth("pencil", np.random.default_rng(0), 2.0)
    assert abs(len(audio) / 48000 - 2.0) < 0.01 and np.isfinite(audio).all()


def test_caption_skips_a_borrowed_shot():
    """"TURBO" em cima de uma cidade: a legenda só entra sobre a imagem escolhida para a cena."""
    cfg = AppConfig()
    scene = Scene(0, "t", 0, 12, SceneAnalysis("x", ["x"], "x"))
    scene.analysis.overlay = {"kind": "keyword", "title": "turbo"}
    shots = _shots([(0, 4.0), (4.0, 8.0), (8.0, 12.0)])
    shots[0].fallback_reason = "neighbor_scene_surplus"  # a cidade emprestada de outra cena
    assert plan_overlays(shots, {0: scene}, cfg, []) == 1
    assert not shots[0].overlay and shots[1].overlay["kind"] == "keyword"


def test_real_hand_photo_finds_the_pencil_tip(tmp_path):
    from broll_bot.motion.templates_brand import HAND_FILE, load_hand

    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).polygon([(10, 70), (120, 20), (190, 90), (60, 95)], fill=(220, 180, 150, 255))
    p = tmp_path / "hand.png"
    img.save(p)
    hand, tip = load_hand(p, 50)
    assert hand.height == 50 and abs(tip[0] - 5) <= 2 and abs(tip[1] - 35) <= 3  # o ponto mais à esquerda
    assert HAND_FILE.exists()  # a foto da mão vem com o projeto
    real, real_tip = load_hand(HAND_FILE, 540)
    assert real_tip[0] < real.width * 0.05 and real_tip[1] > real.height * 0.6  # lápis embaixo, à esquerda


# ------------------------------------------------------- dossiê e ficha
def test_dossier_is_paper_with_dashed_frame_and_typed_title(tmp_path):
    p = _car(tmp_path / "car.jpg")
    gfx.set_brand(True)  # visual próprio: papel bege mesmo com o padrão do canal ligado
    try:
        spec = TemplateSpec("dossier", "dossier", 640, 360, 30, 90, seed=2, media=[MediaRef(str(p))],
                            keyword="Dacia Duster", subtitle="Première génération")
        tpl = TEMPLATES["dossier"](spec)
        early, late = tpl.frame(8).convert("RGB"), tpl.frame(85).convert("RGB")
        r, g, b = late.getpixel((5, 5))
        assert r > 200 and g > 190 and b > 170  # papel bege
        x, y, bw, bh = tpl.box
        top = np.asarray(late.crop((x, y - 30, x + bw, y + 3))).astype(int)
        assert ((top[..., 0] > 150) & (top[..., 1] < 90) & (top[..., 2] < 90)).sum() > 20  # tracejado vermelho
        title = (x, y - 35, x + bw // 2, y - 5)
        ink = lambda im: int((np.asarray(im.crop(title)).mean(axis=2) < 110).sum())  # noqa: E731
        assert ink(late) > ink(early)  # título datilografado aos poucos
        tpl.close()
    finally:
        gfx.set_brand(False)


def test_dossier_keeps_its_own_look_under_the_channel_theme():
    from broll_bot.motion.planner import OWN_LOOK

    assert OWN_LOOK["dossier"] == "dossier"


def test_file_card_types_the_car_name_on_paper():
    spec = OverlaySpec.from_dict({"kind": "file", "title": "Dacia Duster", "w": 960, "h": 540, "fps": 30,
                                  "frames": 120, "start": 0.2, "lang": "fr"})
    ov = OVERLAYS["file"](spec)
    assert ov.tag == "DOSSIER" and ov.name == "DACIA DUSTER"
    late = np.asarray(ov.frame(90))
    box = Image.fromarray(late).getbbox()
    assert box is not None and box[0] < 960 * 0.15 and box[2] < 960 * 0.75  # cartão à esquerda
    paper = (late[..., 0] > 220) & (late[..., 1] > 210) & (late[..., 2] > 190) & (late[..., 3] > 200)
    assert paper.sum() > 5000
    assert not np.asarray(ov.frame(3))[..., 3].any() or Image.fromarray(np.asarray(ov.frame(3))).getbbox() != box


def test_file_card_opens_each_car_section_and_dossier_strip_is_spoken():
    from broll_bot.rhythm import add_file_cards, short_clause

    set_language("fr")
    mk = lambda i, text, style="normal", subject="", **kw: Scene(  # noqa: E731
        i, text, i * 4.0, i * 4.0 + 4, SceneAnalysis("car", ["car"], "car", style=style, subject=subject, **kw))
    scenes = [mk(0, "Numéro 5 : le Dacia Duster.", "ranking", "Dacia Duster"),
              mk(1, "Il est simple et solide, avec un moteur connu.", subject="Dacia Duster",
                 overlay={"kind": "keyword", "title": "moteur"}),
              mk(2, "Encore une phrase sur lui.", subject="Dacia Duster"),
              mk(3, "Numéro 6 : le Citroën Berlingo.", "ranking", "Citroën Berlingo"),
              mk(4, "Le Berlingo transporte tout.", subject="Citroën Berlingo")]
    notes = add_file_cards(scenes)
    assert [s.analysis.overlay.get("kind") for s in scenes] == [None, "file", None, None, "file"]
    assert scenes[1].analysis.overlay["title"] == "Dacia Duster" and len(notes) == 2
    enforce_coherence(scenes)
    assert scenes[1].analysis.overlay["kind"] == "file"
    assert short_clause("Elle démarre tous les matins, hiver comme été, sans jamais faire de caprice.") \
        in ("Elle démarre tous les matins", "sans jamais faire de caprice")
    assert short_clause("Numéro 3 : la Mazda 3.") == ""
    # dossiê: a tira só fica se a frase for falada; o título vira o carro da seção
    d = mk(9, "Elle démarre tous les matins.", "dossier", "Toyota Aygo", caption="Peugeot 208",
           subtitle="la meilleure voiture du monde")
    enforce_coherence([d])
    assert d.analysis.caption == "Toyota Aygo" and d.analysis.subtitle == ""


def test_impact_word_over_a_translucent_blue_veil():
    gfx.set_brand(True)
    try:
        spec = OverlaySpec.from_dict({"kind": "impact", "title": "erreur", "detail": "Et c'est une erreur.",
                                      "w": 960, "h": 540, "fps": 30, "frames": 120, "start": 0.2})
        ov = OVERLAYS["impact"](spec)
        late = np.asarray(ov.frame(70)).astype(int)
        corner = late[5, 5]
        assert corner[3] < 255 and corner[3] > 100 and corner[2] > corner[0]  # véu azul, translúcido
        cream = (abs(late[..., 0] - 234) < 12) & (abs(late[..., 1] - 226) < 12) & (late[..., 3] > 200)
        assert cream.sum() > 2000  # a palavra grande em creme
        assert abs(int(np.argmax(cream.sum(axis=0))) - 480) < 300  # no meio da tela
    finally:
        gfx.set_brand(False)
    text = "Et c'est une erreur. Beaucoup la regardent de haut."
    good = Scene(0, text, 0, 6, SceneAnalysis("car", ["car"], "car", overlay={
        "kind": "impact", "title": "erreur", "detail": "Et c'est une erreur."}))
    bad = Scene(1, text, 6, 12, SceneAnalysis("car", ["car"], "car", overlay={
        "kind": "impact", "title": "fiabilité", "detail": "Et c'est une erreur."}))
    enforce_coherence([good, bad])
    assert good.analysis.overlay["kind"] == "impact" and not bad.analysis.overlay


def test_panel_is_a_minimal_numbered_list_over_a_blue_veil():
    spec = OverlaySpec.from_dict({"kind": "panel", "title": "À vérifier", "lines": ["historique entretien",
                                  "batterie hybride"], "w": 960, "h": 540, "fps": 30, "frames": 120, "start": 0.2})
    ov = OVERLAYS["panel"](spec)
    late = np.asarray(ov.frame(80)).astype(int)
    left, right = late[270, 20], late[270, 940]
    assert left[3] > 150 and left[2] > left[0] and right[3] < 20  # véu azul à esquerda, nada à direita
    assert ov.title == ["À VÉRIFIER"] and len(ov.rows) == 2
    right_list = np.asarray(OVERLAYS["bullets"](spec).frame(80)).astype(int)
    assert right_list[270, 940][3] > 150 and right_list[270, 20][3] < 20  # a lista "bullets" é do lado direito


def test_only_one_file_card_per_car():
    """Duas fichas seguidas do Aygo (a do bot + a que a IA sugeriu): fica só a primeira."""
    from broll_bot.rhythm import add_file_cards

    mk = lambda i, ov=None: Scene(i, "Le texte.", i * 4.0, i * 4.0 + 4,  # noqa: E731
                                  SceneAnalysis("car", ["car"], "car", subject="Toyota Aygo", overlay=ov or {}))
    ai = {"kind": "file", "title": "Toyota Aygo", "detail": "", "title_b": "", "detail_b": "", "lines": []}
    scenes = [mk(0), mk(1, dict(ai)), mk(2, dict(ai))]
    add_file_cards(scenes)
    kinds = [s.analysis.overlay.get("kind") for s in scenes]
    assert kinds.count("file") == 1
