"""Divisão da narração em cenas semânticas + análise visual de cada cena.

Dois analisadores com a mesma interface:

* ``ClaudeSceneAnalyzer`` (recomendado): uma única chamada à API do Claude
  agrupa as frases por ideia e devolve, para cada cena, o resumo visual,
  consultas curtas em inglês, termos a evitar e o tom. Resultado em cache.
* ``HeuristicSceneAnalyzer``: plano B offline (sem LLM), agrupa por duração e
  parágrafo e extrai palavras-chave no idioma do roteiro.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError, model_validator

from .cache import SQLiteCache, make_key
from .config import AnalysisConfig
from .log import get_logger
from .models import Scene, SceneAnalysis, Sentence
from .text import STOPWORDS, keywords, tokens

log = get_logger("scenes")


class SceneAnalyzer(Protocol):
    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]: ...


# ---------------------------------------------------------------- Claude
# ------------------------------------------------ tolerância a respostas "tortas"
# Outros modelos (e o próprio chat) às vezes mandam "Consumo baixo" no lugar de
# {"text": "Consumo baixo", "positive": true}, um número solto no lugar de {"value": ...},
# null no lugar de "" etc. Em vez de recusar o plano inteiro, o bot corrige esses formatos.
_CON_WORDS = ("caro", "cara", "menor", "pior", "ruim", "fraco", "fraca", "problema", "defeito", "falta",
              "sem ", "baixa revenda", "alto consumo", "barulh", "desconfort", "lento", "lenta", "limitad")


def _as_list(value: object) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _num(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    from .motion.gfx import parse_number

    parsed = parse_number(str(value or ""))
    return parsed[0] if parsed else 0.0


def _normalize_scene(d: dict) -> dict:
    d = dict(d)
    for key in ("visual_summary", "fallback_query", "tone", "caption", "subtitle", "body", "shows"):
        if d.get(key) is None:
            d[key] = ""
    d["style"] = d.get("style") or "normal"
    for key in ("queries", "negative_terms", "highlights"):
        if key in d:
            d[key] = [str(x) for x in _as_list(d[key])]
    d["items"] = [{"label": str(it), "query": str(it)} if not isinstance(it, dict)
                  else {**it, "note": str(it.get("note") or "")} for it in _as_list(d.get("items"))]
    d["stats"] = [it if isinstance(it, dict) else {"label": str(it), "value": ""} for it in _as_list(d.get("stats"))]
    points = []
    for it in _as_list(d.get("points")):
        if isinstance(it, dict):
            text = str(it.get("text") or it.get("point") or it.get("label") or "")
            positive = it.get("positive", it.get("pro", True))
            points.append({"text": text, "positive": bool(positive) if not isinstance(positive, str)
                           else positive.strip().lower() not in {"false", "0", "no", "não", "nao"}})
        else:
            text = str(it)
            points.append({"text": text, "positive": not any(w in text.lower() for w in _CON_WORDS)})
    d["points"] = points
    number = d.get("number")
    if number is None or number == "":
        d["number"] = {}
    elif not isinstance(number, dict):
        d["number"] = {"value": _num(number)}
    else:
        number = {k: v for k, v in number.items() if v is not None}
        for key in ("value", "max", "level"):
            if key in number and not isinstance(number[key], (int, float)):
                number[key] = _num(number[key]) if key != "level" or str(number[key]).strip() else -1.0
        d["number"] = number
    overlay = d.get("overlay")
    if not isinstance(overlay, dict):
        d["overlay"] = {}
    else:
        d["overlay"] = {**{k: ("" if v is None else v) for k, v in overlay.items()},
                        "lines": [str(x) for x in _as_list(overlay.get("lines"))]}
    for key, cast in (("template_seconds", _num), ("rank", lambda v: int(_num(v)))):
        if key in d and not isinstance(d[key], (int, float)):
            d[key] = cast(d[key])
    return d


class _ItemOut(BaseModel):
    label: str = Field(description="Short label in the SCRIPT language, e.g. 'Corolla' or '2019'")
    query: str = Field(description="Short English stock search query for this item, e.g. 'toyota corolla sedan'")
    note: str = Field(default="", description="timeline only: short detail under the label, e.g. 'E10' or '1ª geração'")


class _StatOut(BaseModel):
    label: str = Field(description="Spec name in the SCRIPT language, e.g. 'Potência'")
    value: str = Field(description="Value as spoken/written, e.g. '177' or '7,2'")
    unit: str = Field(default="", description="Unit, e.g. 'cv', 'km/l', 's'")
    bar: float = Field(default=0.0, description="0-1 relative strength for the animated bar (0 = no bar)")
    value_b: str = Field(default="", description="Only for versus: the second item's value")


class _NumberOut(BaseModel):
    value: float = Field(default=0.0, description="Number to animate (e.g. 89900, 7.2, 210)")
    prefix: str = Field(default="", description="e.g. 'R$ ' (only when the narration says reais)")
    suffix: str = Field(default="", description="e.g. ' km/h', ' cv', ' s', ' €', ' zł'")
    decimals: int = Field(default=0, description="Decimal places to show")
    max: float = Field(default=0.0, description="gauge/speedometer: full-scale value (e.g. 260 for km/h); score: best grade (10)")
    value_to: float = Field(default=0.0, description="price only: end of a spoken price range ('entre 3 000 et 6 000 euros' -> value 3000, value_to 6000)")
    level: float = Field(default=-1.0, description="price only: 0 = cheap, 0.5 = fair, 1 = expensive, as the narration judges it (-1 = not said)")
    label: str = Field(default="", description="Short label in the SCRIPT language, e.g. 'Velocidade máxima'")


class _PointOut(BaseModel):
    text: str = Field(description="Short point in the SCRIPT language (max ~6 words)")
    positive: bool = Field(default=True, description="true = pro (✓), false = con (✗)")


class _OverlayOut(BaseModel):
    kind: str = Field(default="", description="label | pair | callout | tag | badge | keyword | bullets | panel | bignum | subscribe ('' = none)")
    title: str = Field(default="", description="Main text in the SCRIPT language (short), e.g. '51 PSI' or '90%'")
    detail: str = Field(default="", description="Second line, e.g. 'máximo' or 'dos motoristas erram'")
    title_b: str = Field(default="", description="pair only: right box main text")
    detail_b: str = Field(default="", description="pair only: right box second line")
    lines: list[str] = Field(default_factory=list, description="bullets only: 2-4 short lines")


class _SceneOut(BaseModel):
    first_sentence: int = Field(description="Índice da primeira frase da cena")
    last_sentence: int = Field(description="Índice da última frase da cena (inclusive)")
    visual_summary: str = Field(description="What the viewer should SEE, concrete and filmable, in English")
    shows: str = Field(default="", description="'car' or 'other': what the images of this scene must show")
    queries: list[str] = Field(description="2 to 4 short stock-footage search queries in English (2-4 words each)")
    fallback_query: str = Field(description="One broader, generic query in English used if the others fail")
    negative_terms: list[str] = Field(description="Visual things to avoid for this scene, in English")
    tone: str = Field(description="Mood of this part (also picks the background music): energetic, epic, tense, calm, technical, emotional or fun")
    style: str = Field(default="normal", description="Presentation style (see instructions)")
    template_seconds: float = Field(default=0.0, description="How long the motion graphic stays on screen (0 = auto)")
    caption: str = Field(default="", description="1-4 word title/keyword in the SCRIPT language shown on screen")
    subtitle: str = Field(default="", description="Optional short second line in the SCRIPT language")
    items: list[_ItemOut] = Field(default_factory=list, description="comparison/versus/timeline/beforeafter/poll items")
    stats: list[_StatOut] = Field(default_factory=list, description="specs/versus: 2-5 spec rows")
    number: _NumberOut = Field(default_factory=_NumberOut, description="stat/gauge: the number to animate")
    points: list[_PointOut] = Field(default_factory=list, description="checklist: 2-5 pros/cons")
    rank: int = Field(default=0, description="ranking: position number (1 = first)")
    highlights: list[str] = Field(default_factory=list, description="quote/news: 1-3 short spoken chunks to highlight")
    body: str = Field(default="", description="news/comment: the headline or comment text as spoken (max ~120 chars)")
    overlay: _OverlayOut = Field(default_factory=_OverlayOut, description="Optional overlay drawn over the b-roll")

    @model_validator(mode="before")
    @classmethod
    def _tolerant(cls, data: object) -> object:
        return _normalize_scene(data) if isinstance(data, dict) else data


class _ScenePlan(BaseModel):
    scenes: list[_SceneOut]

    @model_validator(mode="before")
    @classmethod
    def _tolerant(cls, data: object) -> object:
        return {"scenes": data} if isinstance(data, list) else data


SYSTEM_PROMPT = """You are a senior video editor who picks stock b-roll for narrated videos.
You receive a narration script split into numbered sentences with their timing.
Group consecutive sentences into scenes by IDEA (a change of subject, example or argument starts a new scene), not by sentence count.

For every scene:
- visual_summary: describe the concrete shot a viewer should see while hearing this part. Prefer literal, filmable imagery that stock libraries actually have (people, places, objects, actions). For abstract ideas, choose a clear visual metaphor that still makes sense with the narration.
- shows: "car" when this part of the narration is about a car (a model, driving, a car part or engine, a repair on a car, a dealership, cars on the road, a car show or exhibition with cars, and also generic narration with no concrete subject, like an intro or a transition); "other" when it talks about something else (a person, a company, a bank, an office, insurers, money, a document or report, a battery factory, a country, a phone) — then visual_summary and queries must show THAT thing, not a car. Decide scene by scene: the same video can switch between both.
- queries: 2-4 short ENGLISH search queries (2-4 words each), ordered from most specific to more general. ALWAYS English, even when the script is French, Polish or Portuguese: the stock sites only understand English ("used car dealership", never "voiture occasion concession"). visual_summary, fallback_query and negative_terms are English too. Name subjects and actions, not concepts. No quotes, no hashtags. When the narration describes something unique that stock sites will not have (one company's prototype machine, a specific test site, a named person), the FIRST query names it (it may exist on Wikimedia/YouTube) and the others show the closest GENERIC footage that conveys the same idea to a viewer ("rocket launch", "spinning centrifuge", "desert test site"): never a word-by-word description of the unique thing ("projectile up tower" brings power-line towers, "large metal disk structure" brings random metal).
- fallback_query: one broader English query that still fits the scene.
- negative_terms: 3-8 short English terms for things that would look wrong or off-topic for this scene (e.g. wrong setting, text overlays, logos, cartoon). When the video is about a specific product/car/model, list FIRST the look-alikes that would contradict the narration: other brands and other categories (e.g. narration about a Toyota Corolla sedan → "sports car", "mercedes", "bmw", "suv", "pickup truck"). Stock libraries are full of luxury and sports cars, so this matters.
- tone: the mood of this part of the narration, one of: energetic, epic, tense, calm, technical, emotional, fun. It also chooses the background music, which only changes when the mood really changes, so keep neighbouring scenes of the same topic on the same mood (e.g. a warning/problem → tense, specs and numbers → technical, performance or a win → energetic, history/nostalgia → emotional, a big reveal → epic, a joke → fun, plain explanation → calm).

- style: how the scene is presented. Most scenes are "normal" (plain full-screen b-roll). Motion graphics raise viewer retention, so use one whenever the narration clearly fits it, about 1 in 3 scenes, never the same style twice in a row:
  - "comparison": compares/lists 2-3 concrete things or generations → "items" (2-3 {label, query}).
  - "versus": head-to-head of exactly 2 things (car A vs car B) → "items" (2) and optionally "stats" with value (A) and value_b (B).
  - "specs": technical sheet (power, torque, 0-100, consumption, price...) → "stats" (2-5 rows; bar = relative 0-1).
  - "stat": ONE impressive number (price, FIPE value, sales, 0-100 time, years) → "number".
  - "gauge": a speed/rpm/power figure shown on a speedometer → "number" with "max" (full scale).
  - "blueprint": technical drawing moment: design, dimensions, engineering, "how it was built" → "caption" (and optionally stats[0] as a dimension like "4.630 mm").
  - "checklist": pros and cons, what is good/bad, what to check before buying → "points" (2-5).
  - "ranking": a position in a top list ("em terceiro lugar...", "Numéro 8 : la Dacia Logan MCV") → "rank" and "caption" (the item name, e.g. "Dacia Logan MCV"); the header sentence alone is the scene.
  - "chapter": the video moves to a new section/topic ("Agora, o motor") → "caption" (1-2 words) and optional "subtitle". Short (2.5-4 s).
  - "mosaic": many examples, variety, "thousands of...", a whole lineup.
  - "hud": zooming into one specific detail (headlight, wheel, dashboard feature) → "caption".
  - "card": introducing one product/car/object for the first time → "caption".
  - "vhs": memory, the past, old generations, a personal/first-person experience.
  - "statement": a short punchy sentence (3-9 words, "Et c'est une erreur.", "Ce qui n'est pas là ne peut pas casser.") shown word by word on a black screen → "body" = the sentence copied exactly, optional "highlights" = the key word. Its own short scene, 1.5-4 s. Use it for turns and punchlines, about once a minute.
  - "showcase": a photo of the car the scene talks about floating on the channel's blueprint background, with its name → "caption" = the car name. 3-5 s. Good right after a section header or when the narration describes the car.
  - "gallery": three photos of the car floating in layers on the blueprint, with its name → "caption" = the car name. 4-6 s. When the narration talks about the car's look, versions or the car in general.
  - "dossier": a paper dossier — the photo with a dashed selection frame on beige grid paper, a typewritten title on top and a paper strip below → "caption" = title (the car or the topic, copied from the narration), "subtitle" = a short phrase of the narration for the strip (2-6 words, copied). 4-6 s.
  - "sketch": a hand with a pencil draws the car on the blueprint, then the photo appears over the drawing → "caption" = the car name. 4-6 s. When the narration talks about design, conception, engineering or how the car was made.
  - "headline": a strong highlight with NO image: a name, a model, a striking fact or a turn in the story ("O motor que mudou tudo") → "caption" (the title, 1-5 words) and optional "subtitle". Very short: 1.5-3 s. Works best as its own short scene (one short sentence).
  - "triptych": 3 (or 2) images side by side, each with a big value ("47 PSI", "42 PSI", "37 PSI" / "2014", "2018", "2024") → "items" (label = the value shown, query = what to show).
  - "counter": a percentage or proportion ("4 em cada 5", "80%") → "number" (value, suffix "%") and "caption" (the phrase on top).
  - "chart": 2-5 numbers compared in a bar chart (braking distance, prices by year, consumption) → "stats" (label, value, unit) and "caption" (chart title).
  - "quote": a punchy statement shown as text over the darkened image, with the strong words in yellow highlighter ("Nenhum carro chega aos" + "500 mil km" + "sem reparos" + "Nenhum.") → "caption" (the plain lead-in, 2-6 words), "highlights" (1-3 short chunks, 1-3 words each, copied from the narration) and optional "subtitle" (a 1-2 word kicker shown last). 3-6 s.
  - "scrapbook": 1-3 photos of the subject appearing one by one on grid paper, like an album (showing examples, a car in real life, "a friend's car") → optional "items" (what each photo shows). 4-8 s.
  - "timeline": how a model evolved through its generations/years ("o Corolla evoluiu...", "a primeira geração de 1966... em 2002...") → "items" (2-6, in chronological order: label = the year or generation name as spoken, query = that generation, e.g. "toyota corolla 1966", note = optional short detail like "E10") and "caption" (the title). 7-14 s.
  - "speedometer": acceleration or top speed on a big speedometer. 0-100 km/h time → "number" {value: 9.2, decimals: 1, suffix: "s", label: "0 a 100 km/h"}; top speed → "number" {value: 210, suffix: "km/h", label}. 4-6 s.
  - "datasheet": the car's technical sheet read aloud, each line entering when it is spoken → "stats" (2-6 rows: label, value, unit) + "caption" (title) + optional "subtitle" (version). 7-12 s.
  - "price": a price or FIPE value → "number" {value, prefix/suffix in the currency the narration SAYS ("R$ " prefix for reais, " €" suffix for euros, " zł" suffix for złoty, "C$" prefix for Canadian dollars), label} (a spoken range "entre 3 000 et 6 000 euros" → value 3000, value_to 6000) and "level" 0-1 only when the narration says whether it is cheap (0), fair (0.5) or expensive (1). 4-6 s.
  - "proscons": pros AND cons of the car side by side (green/red split screen) → "points" with at least one positive and one negative. 6-10 s. (Use "checklist" when there are only pros or only cons.)
  - "score": the final verdict with grades per criterion ("conforto nota 9, consumo 8...") → "stats" (label = criterion, value = grade) and "number" (the final grade, if spoken). 6-10 s.
  - "magnifier": zooming into ONE detail of the car with a magnifying lens (headlight, wheel, a badge, a panel) → "caption" (the detail's name) and optional "subtitle". 3-5 s.
  - "beforeafter": before vs after of the same car (facelift, old vs new generation, dirty vs clean) → "items" (2: label = "2019"/"2023" or what each shows, query = what to show). 4-7 s.
  - "news": a news story, recall or announcement → "body" (the headline, copied from the narration), "subtitle" (source/date if spoken), "highlights" (1-3 chunks of the headline to mark). 4-7 s.
  - "comment": the narrator reads a viewer's comment → "body" (the comment text as read), "subtitle" (the @username if said). 4-8 s.
  - "poll": asking the audience to choose ("e você, qual escolheria?") → "caption" (the question) and "items" (2-4 options, label + query). 4-7 s.
- template_seconds: how long the motion graphic stays on screen, based on how much there is to read and on the narration: comparison/versus with 2+ items at least 10; specs/checklist 8-12; blueprint 7-10; stat/gauge/ranking/counter 4-7; triptych 5-8; chart 6-9; quote 3-6; scrapbook 4-8; chapter 2.5-4; headline 1.5-3; mosaic/hud/card/vhs 5-8; timeline 7-14; datasheet/score/proscons 6-12; speedometer/price 4-6; magnifier 3-5; beforeafter/news/poll 4-7; comment 4-8. Never longer than the scene. Use 0 for "normal".
- overlay: for about half of the "normal" scenes, add ONE small graphic drawn over the b-roll (the image stays visible), in the SCRIPT language, only with facts from the narration:
  - "label": a value or term in a box → title "51 PSI", detail "máximo".
  - "pair": two values being contrasted → title/detail (left) and title_b/detail_b (right), e.g. "51 PSI"/"máximo" vs "40 PSI"/"frio".
  - "callout": points at a detail of the image (a part of the car, a label on the tire) → title "banda de rodagem".
  - "tag": names a part with a short fact, with a guide line to a yellow marker → title "correia dentada", detail "troca a cada 60 mil km".
  - "badge": a live reading (speed, rpm) in the corner → title "180", detail "km/h".
  - "fuel": fuel consumption the narration gives ("5 litres aux cent", "12 km/l", "8 à 9 l/100 km") → a card with a fuel pump and the number counting up; title = the number or range as spoken ("5", "8–9", "4,5"), detail = the unit ("L/100 km" or "km/l"), title_b = "EN VILLE"/"NA CIDADE" if the narration says where (else "").
  - "keyword": a minimal caption at the bottom with the part or key phrase the narration says (1-4 words) → title "turbo", "pneu murcho gasta mais".
  - "bullets": 2-4 quick tips → title + lines.
  - "panel": a dark grid panel on the left with a title and 2-4 items (the big failures, what to check) → title + lines.
  - "bignum": one striking number over the image → title "90%", detail "dos motoristas erram".
  - "file": a paper file card with the car name — added automatically once at the start of each car section; do not suggest it.
  - "impact": one strong word of a punchy sentence written huge over a translucent dark-blue veil, the sentence typed below → title = that word (1-2 words, copied), detail = the sentence copied EXACTLY (4-13 words).
  - "phrase": a punchy, quotable sentence of the narration ("Et c'est là que tout se joue.") written at the bottom with a yellow highlighter on the key word → title = the sentence copied EXACTLY (4-13 words), lines = [the key word].
  - "subscribe": a call to action ("se inscreva", "deixa o like", "ativa o sininho") → the like/subscribe/bell buttons are clicked on screen; title "" is fine.
  Keep texts very short. Never two overlays in a row on consecutive scenes of the same kind. Leave overlay kind "" when nothing fits.
- caption: 1-4 words in the SCRIPT language (title/keyword shown on screen). subtitle: optional short second line.
- Numbers, prices and units shown on screen follow the SCRIPT language conventions: Portuguese "R$ 89.900", "7,2 s", "177 cv", "12 km/l"; French "32 900 €", "7,2 s", "140 ch", "4,5 l/100 km"; Polish "119 900 zł", "7,2 s", "140 KM", "4,5 l/100 km" (use prefix/suffix for the currency). All on-screen texts (caption, subtitle, labels, points, body) are in the SCRIPT language.
- Numbers and specs must come from the narration; never invent data. If the narration has no numbers, do not use specs/stat/gauge/speedometer/price/datasheet/score.
- KEEP TOGETHER: a sequence of years/generations, a list of specs, pros AND cons, a price and its verdict ("é um preço justo"), a list of grades — each is ONE scene with ONE template, never one scene per sentence.
- SCENE LENGTH: keep scenes between 4 and 15 seconds. A long explanation becomes several scenes, each with queries for what THAT part says (e.g. pressing the button → window going down → hot day, ventilating the car).
- visual_summary and queries always describe REAL filmable footage (what the b-roll shows), even in scenes with a motion graphic. Never "title card", "icon", "text", "number two", "graphic". Stock sites have no charts, graphs, statistics, percentages, survey forms, rankings or numbers on paper: when the narration gives a figure ("39 per cent of owners reported a fault"), show what it is about (the car, the broken part, a mechanic working on it, an owner waiting at the dealership), never the number itself.
- For scenes over 8 s give 4 queries that show DIFFERENT shots (close-up, wide, action, context), not 4 wordings of the same shot.
- Numbered lists ("Número 1", "Dica 2", "Em terceiro lugar"): use "chapter" (caption = the item's title in 1-4 words, subtitle = short description), or "headline" for a strong statement. Aim for a motion graphic or an overlay about every 20-25 s of video.
- COHERENCE (most important): a template or overlay must illustrate exactly what is SPOKEN in that same scene, never something from another scene or general knowledge. Every number, name, item, point, title and highlighted phrase shown on screen must be said in that scene's sentences (copy the words). When in doubt, use "normal" with no overlay — a plain relevant b-roll is always better than an unrelated graphic. Anything that does not match the narration is discarded automatically.
  Good: "O Corolla entrega 177 cavalos, enquanto o Civic fica com 155" → versus Corolla x Civic with 177/155. Bad: a chart with a price that nobody says; a versus against a car that is not mentioned; a VHS scene about a brand-new car; a headline with a phrase that is not in the script.
- Leave unused fields empty ([], "", 0).

Scenes must be contiguous, cover every sentence exactly once and keep the original order."""


def user_prompt(sentences: list[Sentence], cfg: AnalysisConfig) -> str:
    from .subject import GLOSSARY_PROMPT, is_automotive

    lines = [f"[{s.index}] ({s.start:.1f}s-{s.end:.1f}s, {s.end - s.start:.1f}s) {s.text}" for s in sentences]
    context = cfg.sections_hint
    if cfg.subject and not cfg.sections_hint:
        context += (f"Main subject of the video: {cfg.subject}. When a scene talks about it (even with 'ele', "
                    f"'o carro', 'o sedã'), its queries must name it (e.g. '{cfg.subject} front', "
                    f"'{cfg.subject} interior') and its negative_terms must list other brands and car categories.\n")
    if is_automotive(cfg.niche):
        context += ("Scenes with shows=\"car\": every query must show a vehicle or a part of one (car, engine, wheel, "
                    "dashboard, dealership...). Scenes with shows=\"other\": every query must show the concrete thing "
                    "the narration talks about (the bank, the insurer's office, the battery factory, the exhibition "
                    "hall, the document), with no car forced in. Never abstract queries, and never a presenter, host, "
                    "narrator or talking head (the video has no presenter on screen).\n" + GLOSSARY_PROMPT + "\n")
    return (
        f"Channel niche: {cfg.niche}. Script language: {cfg.script_language} "
        f"(queries, fallback_query, visual_summary and negative_terms in ENGLISH; only on-screen texts in "
        f"{cfg.script_language}). "
        f"Target scene length: about {cfg.target_scene_seconds:.0f}s "
        f"(between {cfg.min_scene_seconds:.0f}s and {cfg.max_scene_seconds:.0f}s when the ideas allow).\n"
        + context + "\nSentences:\n" + "\n".join(lines)
    )


MANUAL_FORMAT = """Answer with ONLY a JSON object (no explanations, no markdown) in exactly this format:
{
  "scenes": [
    {
      "first_sentence": 0, "last_sentence": 1,
      "visual_summary": "silver sedan driving on a highway at sunset, tracking shot",
      "shows": "car",
      "queries": ["sedan driving highway", "car driving sunset", "car on road"],
      "fallback_query": "car driving",
      "negative_terms": ["cartoon", "text overlay", "logo"],
      "tone": "confident",
      "style": "normal", "template_seconds": 0, "caption": "", "subtitle": "",
      "items": [], "stats": [], "number": {}, "points": [], "rank": 0, "highlights": [], "body": "",
      "overlay": {"kind": "badge", "title": "200", "detail": "km/h", "title_b": "", "detail_b": "", "lines": []}
    },
    {
      "first_sentence": 2, "last_sentence": 2,
      "visual_summary": "dark navy title card",
      "queries": ["toyota corolla sedan"], "fallback_query": "sedan car",
      "negative_terms": ["sports car", "mercedes", "bmw", "cartoon"],
      "tone": "dramatic",
      "style": "headline", "template_seconds": 2.2, "caption": "Corolla XEi", "subtitle": "o sedã mais vendido",
      "items": [], "stats": [], "number": {}, "points": [], "rank": 0,
      "overlay": {"kind": "", "title": "", "detail": "", "title_b": "", "detail_b": "", "lines": []}
    },
    {
      "first_sentence": 3, "last_sentence": 4,
      "visual_summary": "toyota corolla and honda civic side by side",
      "queries": ["toyota corolla sedan", "honda civic sedan"],
      "fallback_query": "sedan cars",
      "negative_terms": ["cartoon", "logo"],
      "tone": "competitive",
      "style": "versus", "template_seconds": 11, "caption": "Corolla x Civic", "subtitle": "",
      "items": [{"label": "Corolla", "query": "toyota corolla sedan"}, {"label": "Civic", "query": "honda civic sedan"}],
      "stats": [{"label": "Potência", "value": "177", "unit": "cv", "bar": 0, "value_b": "155"}],
      "number": {}, "points": [], "rank": 0,
      "overlay": {"kind": "pair", "title": "177 cv", "detail": "Corolla", "title_b": "155 cv", "detail_b": "Civic", "lines": []}
    },
    {
      "first_sentence": 5, "last_sentence": 6,
      "visual_summary": "engine bay of a modern sedan, close-up",
      "queries": ["car engine bay", "car engine close up"],
      "fallback_query": "car engine",
      "negative_terms": ["cartoon", "text overlay"],
      "tone": "technical",
      "style": "specs", "template_seconds": 9, "caption": "Ficha técnica", "subtitle": "Corolla 2.0",
      "items": [],
      "stats": [{"label": "Potência", "value": "177", "unit": "cv", "bar": 0.8, "value_b": ""},
                {"label": "Torque", "value": "21,4", "unit": "kgfm", "bar": 0.7, "value_b": ""}],
      "number": {}, "points": [], "rank": 0,
      "overlay": {"kind": "", "title": "", "detail": "", "title_b": "", "detail_b": "", "lines": []}
    }
  ]
}
"first_sentence"/"last_sentence" are the numbers in [brackets]. Cover every sentence from [0] to [LAST] exactly once, in order."""


def manual_prompt(sentences: list[Sentence], cfg: AnalysisConfig) -> str:
    """Prompt completo para colar no chat do Claude (claude.ai), sem usar a API."""
    fmt = MANUAL_FORMAT.replace("[LAST]", f"[{len(sentences) - 1}]")
    return f"{SYSTEM_PROMPT}\n\n{fmt}\n\n{user_prompt(sentences, cfg)}\n"


class ManualSceneAnalyzer:
    """Lê o plano de cenas (cenas.json) gerado colando o prompt no chat do Claude."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
        text = self.path.read_text(encoding="utf-8-sig")
        try:
            plan = _ScenePlan.model_validate(_extract_json(text))
        except (ValueError, ValidationError) as exc:
            raise AnalysisError(
                f"{self.path.name} não é um plano de cenas válido ({exc.__class__.__name__}). "
                "Cole a resposta do Claude inteira, só o JSON, e salve de novo."
            ) from exc
        if not plan.scenes:
            raise AnalysisError(f"{self.path.name} não tem nenhuma cena")
        highest = max(s.last_sentence for s in plan.scenes)
        if highest != len(sentences) - 1:
            log.warning(
                "o plano de cenas não bate com o roteiro atual (roteiro mudou depois de gerar o prompt?)",
                extra={"stage": "scenes", "last_sentence_in_plan": highest, "sentences": len(sentences)},
            )
        log.info("plano de cenas lido do arquivo", extra={"stage": "scenes", "file": self.path.name,
                                                          "scenes": len(plan.scenes)})
        return _plan_to_scenes(plan, sentences, audio_duration)


def _extract_json(text: str) -> object:
    """Aceita o JSON puro ou dentro de ```json ... ``` (como o chat costuma responder)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("nenhum objeto JSON encontrado")
    return json.loads(text[start:end + 1])


class ClaudeSceneAnalyzer:
    def __init__(self, cfg: AnalysisConfig, cache: SQLiteCache | None = None) -> None:
        import anthropic  # import tardio: só é necessário neste modo

        self.cfg = cfg
        self.cache = cache
        self.client = anthropic.Anthropic()

    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
        import anthropic

        prompt = user_prompt(sentences, self.cfg)
        key = make_key("scene-plan", self.cfg.model, SYSTEM_PROMPT, prompt)
        plan_data = self.cache.get("scene_plan", key) if self.cache else None
        if plan_data is None:
            try:
                response = self.client.beta.messages.parse(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_format=_ScenePlan,
                    output_config={"effort": self.cfg.effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.APIError as exc:
                raise AnalysisError(f"falha na API do Claude: {exc}") from exc
            from . import usage

            usage.from_anthropic(response, getattr(response, "model", None) or self.cfg.model, "plano de cenas")
            if response.stop_reason == "refusal":
                raise AnalysisError("o modelo recusou a análise do roteiro")
            if response.stop_reason == "max_tokens" or response.parsed_output is None:
                raise AnalysisError("resposta incompleta do modelo (aumente analysis.max_tokens)")
            plan_data = response.parsed_output.model_dump()
            if self.cache:
                self.cache.set("scene_plan", key, plan_data, ttl_seconds=30 * 86400)
            log.info("plano de cenas gerado pelo Claude", extra={"stage": "scenes", "scenes": len(plan_data["scenes"])})
        else:
            log.info("plano de cenas reaproveitado do cache", extra={"stage": "scenes"})
        return _plan_to_scenes(_ScenePlan.model_validate(plan_data), sentences, audio_duration)


class AnalysisError(RuntimeError):
    pass


class OpenAISceneAnalyzer:
    """Plano de cenas pela API da OpenAI (ChatGPT): mesmo prompt do modo chat, resposta em JSON."""

    def __init__(self, cfg: AnalysisConfig, cache: SQLiteCache | None = None, client: object = None) -> None:
        from .llm_openai import OpenAIJSON

        self.cfg = cfg
        self.cache = cache
        self.llm = OpenAIJSON(cfg.openai_model, cfg.openai_effort, cfg.openai_max_tokens, client=client,
                              step="plano de cenas")

    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
        from .llm_openai import OpenAIError

        prompt = manual_prompt(sentences, self.cfg)
        # outro nível de raciocínio ou regras novas no prompt do sistema = plano novo (senão o cache devolvia as
        # buscas antigas no mesmo roteiro)
        key = make_key("scene-plan-openai", self.cfg.openai_model, self.cfg.openai_effort, SYSTEM_PROMPT, prompt)
        plan_data = self.cache.get("scene_plan", key) if self.cache else None
        if plan_data is None:
            system = "You are a senior video editor. Answer only with the JSON object requested."
            try:
                raw = self.llm.complete(system, prompt)
                try:
                    plan = _ScenePlan.model_validate(raw)
                except ValidationError as first:
                    # uma segunda chance: devolve o erro e pede o JSON corrigido
                    log.warning("plano da OpenAI fora do formato; pedindo correção",
                                extra={"stage": "scenes", "errors": first.error_count()})
                    fix = (f"{prompt}\n\nYour previous answer did not match the format. Errors:\n"
                           f"{str(first)[:1500]}\n\nPrevious answer:\n{json.dumps(raw, ensure_ascii=False)[:12000]}\n\n"
                           "Answer again with the corrected JSON object only.")
                    plan = _ScenePlan.model_validate(self.llm.complete(system, fix))
            except (OpenAIError, ValidationError) as exc:
                raise AnalysisError(f"plano de cenas pela OpenAI falhou: {exc}") from exc
            if not plan.scenes:
                raise AnalysisError("a OpenAI devolveu um plano sem cenas")
            plan_data = plan.model_dump()
            if self.cache:
                self.cache.set("scene_plan", key, plan_data, ttl_seconds=30 * 86400)
            log.info("plano de cenas gerado pela OpenAI", extra={"stage": "scenes", "model": self.cfg.openai_model,
                                                                  "scenes": len(plan_data["scenes"])})
        else:
            log.info("plano de cenas reaproveitado do cache", extra={"stage": "scenes"})
        return _plan_to_scenes(_ScenePlan.model_validate(plan_data), sentences, audio_duration)


# valor em busca ("dealership 12000 off banner", "nissan ariya discount 6000", "37%"): os bancos não marcam
# fotos com preço; ano (1950-2039) e número de modelo (500, 208, 350) ficam
_QUERY_MONEY = re.compile(r"^(?:[$£€]|c\$|r\$|us\$)?\d[\d,.]*(?:%|k|€|\$|£)?$", re.I)


def _query_word_ok(w: str) -> bool:
    if not _QUERY_MONEY.match(w):
        return True
    if not re.search(r"\d", w) or re.search(r"[$£€%k]", w, re.I):
        return not re.search(r"\d", w)
    if "," in w:
        return False  # "12,000"
    n = float(w) if re.fullmatch(r"\d+", w) else 0.0
    # "12000"/"6000" (valor redondo) sai; "3008", "5008" (modelo) e anos ficam
    return n < 1000 or 1950 <= n <= 2039 or n % 100 != 0


def _clean_queries(queries: list[str], limit: int = 4) -> list[str]:
    out: list[str] = []
    for q in queries:
        q = " ".join(w for w in q.replace('"', " ").replace("#", " ").split() if _query_word_ok(w))
        q = " ".join(q.split()[:5]).strip()
        if q and q.lower() not in {o.lower() for o in out}:
            out.append(q)
    return out[:limit]


STYLES = {"normal", "comparison", "mosaic", "hud", "card", "vhs", "versus", "specs", "stat", "gauge",
          "blueprint", "checklist", "ranking", "chapter", "headline", "triptych", "counter", "chart",
          "quote", "scrapbook", "timeline", "speedometer", "datasheet", "price", "proscons", "score",
          "magnifier", "beforeafter", "news", "comment", "poll", "showcase", "gallery", "sketch",
          "dossier", "statement", "bulletin", "report", "videowall", "paperfloat", "paperpair",
          "papertimeline", "papercompare", "paperstatement", "paperrank", "newsrank", "newsfloat",
          "newstimeline", "newscompare",  # famílias papel/TV: entram pela troca do estilo
          "percentbar", "peoplegrid", "receipt", "downtime", "warning", "textmask", "photostack", "splitpanels",
          "annotate", "wordpop",  # edição magnética: o bot põe sozinho (fala e ritmo)
          "source", "fromto", "coverage", "steps", "unverified", "exploded",  # reportagem: o bot põe sozinho
          "filmstrip", "opener", "profile", "yearflash", "flow", "mythfact", "definition", "agenda",  # qualquer nicho
          "article",  # matéria de site citada
          "docphrase", "docbars", "diagram",  # estilo Documentário (templates_docu.py)
          "map", "flagversus"}  # mapa e país x país (templates_map.py)


OVERLAY_KINDS = {"label", "pair", "callout", "tag", "badge", "keyword", "bullets", "panel", "bignum", "subscribe",
                 "arrow", "fuel", "phrase", "file", "impact", "lowerthird", "ticker", "crawl", "years",
                 "paperyears", "papertag"}


def _clean_overlay(ov: "_OverlayOut") -> dict:
    kind = (ov.kind or "").strip().lower()
    if kind not in OVERLAY_KINDS or (not ov.title.strip() and kind != "subscribe"):
        return {}
    if kind == "pair" and not ov.title_b.strip():
        kind = "label"
    title_max = 110 if kind == "phrase" else 40  # a frase de destaque é uma frase inteira da fala
    detail_max = 110 if kind == "impact" else 48  # no "impact", o detalhe é a frase inteira
    return {"kind": kind, "title": ov.title.strip()[:title_max], "detail": ov.detail.strip()[:detail_max],
            "title_b": ov.title_b.strip()[:40], "detail_b": ov.detail_b.strip()[:48],
            "lines": [ln.strip()[:60] for ln in ov.lines if ln.strip()][:4]}


def _clean_style(style: str) -> str:
    style = (style or "normal").strip().lower()
    return style if style in STYLES else "normal"


def _plan_to_scenes(plan: _ScenePlan, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
    """Converte o plano em cenas, reparando lacunas/sobreposições se houver."""
    n = len(sentences)
    by_start: dict[int, _SceneOut] = {}
    for item in sorted(plan.scenes, key=lambda s: s.first_sentence):
        start = min(max(item.first_sentence, 0), n - 1)
        by_start.setdefault(start, item)
    starts = sorted(by_start)
    if not starts or starts[0] != 0:
        first = by_start.get(starts[0]) if starts else None
        starts = [0, *[s for s in starts if s != 0]]
        if first is not None:
            by_start[0] = first
    scenes: list[Scene] = []
    for i, start in enumerate(starts):
        stop = starts[i + 1] if i + 1 < len(starts) else n
        chunk = sentences[start:stop]
        item = by_start[start]
        fallback = _clean_queries([item.fallback_query])
        queries = _clean_queries(item.queries) or fallback or _clean_queries([item.visual_summary])
        analysis = SceneAnalysis(
            visual_summary=item.visual_summary.strip(),
            queries=queries,
            fallback_query=fallback[0] if fallback else queries[-1],
            negative_terms=[t.strip() for t in item.negative_terms if t.strip()][:8],
            tone=item.tone.strip() or "neutral",
            query_language="en",
            style=_clean_style(item.style),
            caption=item.caption.strip()[:48],
            subtitle=item.subtitle.strip()[:60],
            template_seconds=max(0.0, float(item.template_seconds or 0)),
            items=[{"label": i.label.strip(), "query": i.query.strip(), "note": i.note.strip()[:24]} for i in item.items
                   if i.label.strip() and i.query.strip()][:6],
            stats=[st.model_dump() for st in item.stats if st.label.strip() and st.value.strip()][:6],
            number=item.number.model_dump(),
            points=[p.model_dump() for p in item.points if p.text.strip()][:6],
            rank=max(0, int(item.rank or 0)),
            overlay=_clean_overlay(item.overlay),
            highlights=[x.strip()[:32] for x in item.highlights if x.strip()][:3],
            body=item.body.strip()[:140],
            shows=item.shows.strip().lower() if item.shows.strip().lower() in ("car", "other") else "",
        )
        scenes.append(Scene(len(scenes), " ".join(s.text for s in chunk), chunk[0].start,
                            chunk[-1].end if stop < n else audio_duration, analysis))
    return scenes


# -------------------------------------------------------------- heurística
class HeuristicSceneAnalyzer:
    def __init__(self, cfg: AnalysisConfig) -> None:
        self.cfg = cfg

    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
        groups: list[list[Sentence]] = []
        current: list[Sentence] = []
        for sentence in sentences:
            if current:
                dur = current[-1].end - current[0].start
                new_paragraph = sentence.paragraph != current[-1].paragraph
                if dur >= self.cfg.target_scene_seconds or (new_paragraph and dur >= self.cfg.min_scene_seconds) \
                        or dur + (sentence.end - sentence.start) > self.cfg.max_scene_seconds:
                    groups.append(current)
                    current = []
            current.append(sentence)
        if current:
            if groups and current[-1].end - current[0].start < self.cfg.min_scene_seconds:
                groups[-1].extend(current)
            else:
                groups.append(current)

        from .lang import resolve

        self.lang = resolve(self.cfg.script_language, " ".join(s.text for s in sentences))
        doc_freq = Counter(k for s in sentences for k in set(keywords(s.text)))
        scenes = []
        for group in groups:
            text = " ".join(s.text for s in group)
            analysis = self._analysis(text, doc_freq, len(groups))
            scenes.append(Scene(len(scenes), text, group[0].start, group[-1].end, analysis))
        if scenes:
            scenes[0].start = 0.0
            scenes[-1].end = audio_duration
        log.info("cenas definidas pela heurística", extra={"stage": "scenes", "scenes": len(scenes)})
        return scenes

    def _analysis(self, text: str, doc_freq: Counter, n_docs: int) -> SceneAnalysis:
        counts = Counter(keywords(text, min_len=4))
        # TF-IDF simples: prioriza palavras marcantes da cena
        ranked = sorted(counts, key=lambda k: counts[k] * (1 + n_docs / (1 + doc_freq.get(k, 0))), reverse=True)
        top = ranked[:6] or [t for t in tokens(text) if t not in STOPWORDS][:3] or ["abstract"]
        queries = _clean_queries([" ".join(top[:2]), " ".join(top[1:3]), top[0], " ".join(top[2:4])])
        return SceneAnalysis(
            visual_summary=text[:300],
            queries=queries,
            fallback_query=top[0],
            negative_terms=["text", "logo", "watermark"],
            tone="neutral",
            query_language=getattr(self, "lang", self.cfg.script_language),
        )


def merge_short_scenes(scenes: list[Scene], min_seconds: float) -> list[Scene]:
    """Cenas curtíssimas viram parte da anterior (evita cortes de 0,5s)."""
    merged: list[Scene] = []
    for scene in scenes:
        if merged and scene.duration < min_seconds:
            prev = merged[-1]
            prev.end = scene.end
            prev.text = f"{prev.text} {scene.text}"
            continue
        merged.append(scene)
    if len(merged) > 1 and merged[0].duration < min_seconds:
        first = merged.pop(0)
        merged[0].start = first.start
        merged[0].text = f"{first.text} {merged[0].text}"
    for i, scene in enumerate(merged):
        scene.index = i
    return merged


def build_analyzer(cfg: AnalysisConfig, cache: SQLiteCache | None) -> SceneAnalyzer:
    """IA escolhida em ``analysis.provider``. Se ela não puder ser usada (sem SDK, sem
    chave), para com um erro claro — a heurística só entra se ``fallback_to_heuristic``."""
    if cfg.provider in ("openai", "anthropic"):
        name = "OpenAI (ChatGPT)" if cfg.provider == "openai" else "Claude (Anthropic)"
        try:
            return OpenAISceneAnalyzer(cfg, cache) if cfg.provider == "openai" else ClaudeSceneAnalyzer(cfg, cache)
        except Exception as exc:  # noqa: BLE001 - sem SDK/credencial
            if not cfg.fallback_to_heuristic:
                raise AnalysisError(f"{name} indisponível: {exc}") from exc
            log.warning(f"{name} indisponível; usando heurística", extra={"stage": "scenes", "error": str(exc)})
    return HeuristicSceneAnalyzer(cfg)
