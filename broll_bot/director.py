"""Diretor de IA: lê o roteiro inteiro ANTES de tudo e decide o que vai na tela.

Uma chamada a um modelo forte (``director.openai_model`` / ``director.model``) recebe o roteiro numerado
e o catálogo do que o bot sabe desenhar, e devolve um plano de edição:

* **nicho** do vídeo (carros, finanças, culinária...), assunto principal e moeda;
* **seções** de ranking (o cabeçalho "Number ten.", o nome limpo "Chevrolet Blazer", até onde vai);
* **listas** numeradas ("One. The sales numbers." / "Round three. Money and time.");
* **cards** de dado (barra de %, nota da oficina, fonte, preço, linha do tempo...) com os campos prontos,
  já escritos como vão na tela ("C$4,000", "2026s", "IONIQ 5");
* (fase 2) o **vocabulário visual** do nicho: o que conta como imagem do assunto e o que é fora dele.

Nada disso é aceito de olhos fechados: ``validate`` confere cada item contra o roteiro. Número que não foi
dito, nome que não aparece, frase fora do roteiro, card em cima de cabeçalho: sai, e o motivo vai para o
report. Se a chamada falhar (sem chave, API fora, JSON quebrado), o vídeo segue com as regras de sempre.

Com um plano válido instalado (``install``), quem decide ranking, listas e cards é ele: ``rank_header``,
``step_of`` e ``auto_templates.detect`` consultam o plano em vez das regras.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .log import get_logger
from .text import normalize

log = get_logger("director")

PROMPT_VERSION = 3

# --------------------------------------------------------------------------------------------- catálogo
# o que o diretor pode pôr na tela: quando usar e o formato dos campos (o mesmo que os detectores do bot
# produzem, então o render já sabe desenhar)
CATALOG: dict[str, tuple[str, str]] = {
    "percentbar": ("a share of a whole ('82% of thefts happen at home', '39% of owners reported a fault'). "
                   "Not for changes ('sales down 37%' is a change: use stat)",
                   '{"number": {"value": 82, "suffix": "%", "decimals": 0}, "caption": "of thefts happen at home"}'),
    "peoplegrid": ("a proportion of people/things said as 'N in M' ('one in five drivers')",
                   '{"number": {"value": 1, "total": 5}, "caption": "drivers never check it"}'),
    "stat": ("ONE striking number or change: sales, units, a price drop, a count ('sold three units', "
             "'down 61%'). For a change use prefix '-' or '+'",
             '{"number": {"value": 61, "prefix": "-", "suffix": "%", "decimals": 0, "label": "sales in 2026"}, '
             '"caption": "Nissan Ariya"}'),
    "fromto": ("a value that went from A to B ('from 471 to three', 'from C$5,000 to C$4,000')",
               '{"number": {"from": 471, "value": 3, "prefix": ""}, "caption": "units sold", "subtitle": "Q3"}'),
    "receipt": ("a repair bill, a fuel fill-up or a service cost (a workshop invoice)",
                '{"number": {"value": 1800, "prefix": "£", "low": 800}, "caption": "Timing belt", '
                '"points": [{"text": "timing belt"}, {"text": "water pump"}]}'),
    "price": ("the price of the product itself, or a discount/incentive amount ('C$12,000 off the Prologue')",
              '{"number": {"value": 12000, "prefix": "C$", "suffix": "", "decimals": 0, "label": "incentive", '
              '"level": -1}, "caption": "Honda Prologue", "subtitle": "after taxes"}'),
    "downtime": ("days a car/thing was off the road or out of use",
                 '{"number": {"value": 14}, "caption": "cars off the road waiting for parts"}'),
    "warning": ("the main problem/defect of the subject named by the narration",
                '{"caption": "Infotainment", "subtitle": "biggest complaint"}'),
    "source": ("the narration cites who said/published something (a magazine, an institute, an authority). "
               "Once per source, at its first important mention",
               '{"caption": "Le Guide de l\'Auto", "subtitle": "first half of 2026", '
               '"body": "the sentence that cites it, as written on screen"}'),
    "unverified": ("the narration says something could not be confirmed, or there is no data yet",
                   '{"caption": "how reliable they will be after ten years", "subtitle": "no_data"}'),
    "timeline": ("3-6 years or generations in chronological order",
                 '{"caption": "Toyota Corolla", "items": [{"label": "1966", "query": "toyota corolla 1966", '
                 '"note": ""}]}'),
    "beforeafter": ("before vs after of the same thing (old vs new generation, facelift)",
                    '{"items": [{"label": "2019", "query": "..."}, {"label": "2024", "query": "..."}]}'),
    "speedometer": ("0-100 km/h (0-60 mph) time or top speed",
                    '{"number": {"value": 9.2, "decimals": 1, "suffix": "s", "label": "0-100 km/h"}}'),
    "datasheet": ("a technical sheet read aloud (power, torque, consumption, price...), 2-6 rows",
                  '{"caption": "Specs", "subtitle": "1.5 TSI", "stats": [{"label": "Power", "value": "150", '
                  '"unit": "hp"}]}'),
    "chart": ("2-5 numbers of the same kind compared (braking distances, prices by year). Values are positive; "
              "for drops say it in the caption ('Sales drop')",
              '{"caption": "Braking on ice", "stats": [{"label": "Winter", "value": "12", "unit": "m"}, '
              '{"label": "All-weather", "value": "16", "unit": "m"}]}'),
    "proscons": ("pros AND cons of the subject (at least one of each)",
                 '{"points": [{"text": "Cheap to run", "positive": true}, {"text": "Weak resale", "positive": false}]}'),
    "checklist": ("2-5 points of the same kind (what to check, the signs, the reasons)",
                  '{"caption": "Before you sign", "points": [{"text": "Check the build date", "positive": true}]}'),
    "score": ("grades per criterion and a final grade",
              '{"caption": "Verdict", "stats": [{"label": "Comfort", "value": "9"}], "number": {"value": 8.5, '
              '"decimals": 1}}'),
    "news": ("a news story, recall or announcement told as news",
             '{"body": "the headline as written on screen", "highlights": ["two words"]}'),
    "comment": ("the narrator reads a viewer comment", '{"body": "the comment as read"}'),
    "article": ("the narration cites a SPECIFIC report, article, press release, official statement or social-media "
                "post and says or quotes what it said ('CBC reported Friday that...', 'In a Truth Social post, the "
                "President wrote...'). caption = the outlet/platform as said; subtitle = its date only if said; body "
                "= a short headline of that story using the narration's own words; highlights = [the exact quoted or "
                "key words as spoken, 3-12 words]. Prefer it over source and news when something is quoted",
                '{"caption": "CBC News", "subtitle": "Oct 23", "body": "Trump to swap some tariffs on Canadian '
                'goods", "highlights": ["Canada would wait if it has to"]}'),
    "poll": ("the narrator asks the audience to choose between 2-4 options",
             '{"caption": "Winter tires or all-weathers?", "items": [{"label": "Winter", "query": "winter tires"}, '
             '{"label": "All-weather", "query": "all weather tires"}]}'),
    "chapter": ("the video moves to a new big part ('Now, the biggest discounts in Canada.')",
                '{"caption": "Biggest discounts", "subtitle": ""}'),
    "profile": ("a real person the narration introduces (a scientist, an inventor, a founder): name, who they are and "
                "one fact said about them; items[0].query = the person's name + 'portrait' for the photo",
                '{"caption": "Fritz Haber", "subtitle": "German chemist", "body": "Nobel Prize in Chemistry", '
                '"items": [{"label": "Fritz Haber", "query": "Fritz Haber portrait"}]}'),
    "yearflash": ("a year that opens a moment of the story ('In 1909, this German chemist...'), shown huge",
                  '{"number": {"value": 1909}, "subtitle": "Haber pulls nitrogen from the air"}'),
    "flow": ("a process in 3-5 steps or a chain of cause and effect told in order (ingredients -> fermentation -> "
             "product)", '{"caption": "How it is made", "points": [{"text": "Manure, milk and molasses"}, '
                         '{"text": "Fermentation"}, {"text": "Strain and dilute"}]}'),
    "mythfact": ("the narration corrects a common belief (a myth, then the truth): points = the myth (positive: false) "
                 "and the fact (positive: true)",
                 '{"points": [{"text": "It replaces all fertilizer", "positive": false}, '
                 '{"text": "Today it cuts part of it", "positive": true}]}'),
    "definition": ("the narration explains what a term means ('Researchers call this rock dusting.')",
                   '{"caption": "Rock dusting", "body": "Ground rock used as a slow source of minerals for the soil"}'),
    "agenda": ("near the start, the narration promises what is coming later in the video (2-4 teasers): an open loop",
               '{"points": [{"text": "The chemist who fed half of humanity"}, {"text": "The war that scared farmers"}]}'),
    "quote": ("a punchy statement worth showing as text, with 1-3 key chunks highlighted",
              '{"caption": "A recall you don\'t know about", "highlights": ["can really hurt you"]}'),
}
# cards que só entram no pedido quando o estilo do vídeo os liga (``templates.styles``): os outros canais
# continuam com o mesmo pedido (e o mesmo cache)
EXTRA_CATALOG: dict[str, tuple[str, str]] = {
    "diagram": ("the narration explains HOW something physical works or happens (how a mine jumps and explodes, how "
                "soldiers probed for mines, how a dam holds water, how a ship was sunk): a simple hand-drawn side-view "
                "explainer sketch. At most 3 per video, only for mechanisms/actions a drawing makes clearer. "
                "caption = 2-5 word title from the narration; subtitle = one short line; elements = 4-12 drawing pieces "
                "on a canvas 100 wide x 56 high (x right, y DOWN, ground usually y=40), drawn in order: "
                "ground {y}; line {pts:[[x,y],...], w:1-4}; box {x,y,w,h, fill:grey|ink|none} (x,y = top-left); "
                "circle {x,y,r, fill}; person {x,y=feet,h:10-20, pose:stand|walk|crawl|point|fall, face:left|right}; "
                "arrow {from:[x,y], to:[x,y]}; burst {x,y,r} (explosion rays); measure {x,y1,y2,label} (height "
                "marker); text {x,y,text,size:s|m|l, font:hand|type}; vehicle {x (middle), y (ground; for a plane its "
                "height), kind:tank|jeep|truck|car|plane|ship|cannon, w: length (tank 24, jeep 15, truck 22, plane 28), "
                "face:left|right, fill} - ALWAYS use vehicle for machines, never boxes. Any piece may have color:red|ink "
                "and say: one "
                "narration word that makes it appear. Every text/label uses the narration's own words (numbers as "
                "spoken). Keep pieces apart, texts short (1-5 words), nothing off the canvas",
                '{"caption": "The S-mine", "subtitle": "German jumping mine", "elements": [{"t": "ground", "y": 40}, '
                '{"t": "box", "x": 46, "y": 40, "w": 6, "h": 9, "fill": "grey"}, {"t": "arrow", "from": [49, 40], '
                '"to": [49, 28], "say": "throws"}, {"t": "burst", "x": 49, "y": 24, "r": 12, "say": "steel"}, '
                '{"t": "text", "x": 12, "y": 18, "text": "hundreds of steel balls", "size": "l", "color": "red"}, '
                '{"t": "person", "x": 78, "y": 40, "h": 16, "pose": "walk", "face": "left"}, {"t": "measure", '
                '"x": 66, "y1": 40, "y2": 26, "label": "waist height"}]}'),
}
EXTRA_CATALOG["map"] = (
    "the narration says WHERE something happens: a place, a route, an attack, a crossing, a border, countries next to "
    "each other ('the raid on Dieppe', 'from Portsmouth across the Channel', 'Germany invaded Poland'). At most one "
    "map every 60 seconds. caption = a short line in the narration's words for the label box (a question or a fact, "
    "3-9 words); elements = 1-8 pieces: {t:'country', name (English), say} countries to highlight; {t:'place', name "
    "(English), country, lat, lon, say} cities, ports, beaches, battles (lat/lon in decimal degrees, REQUIRED, as "
    "precise as you know); {t:'route', from: place name, to: place name, say} arrows for movement/attack/trip "
    "(at most 3). say = the word the narration uses for it (its own spelling and language)",
    '{"caption": "A raid: could a port be taken from the sea?", "elements": [{"t": "place", "name": "Portsmouth", '
    '"country": "United Kingdom", "lat": 50.8, "lon": -1.09, "say": "Portsmouth"}, {"t": "place", "name": "Dieppe", '
    '"country": "France", "lat": 49.92, "lon": 1.08, "say": "Dieppe"}, {"t": "route", "from": "Portsmouth", '
    '"to": "Dieppe", "say": "raid"}]}')
EXTRA_CATALOG["flagversus"] = (
    "the narration puts two countries face to face: a war, a rivalry, a comparison of armies, sizes or economies "
    "('Poland against Germany', 'France had more tanks than Germany'). items = exactly 2 countries: label = the name "
    "as the narration says it, query = the English country name; caption = what is compared, in the narration's words; "
    "stats = optional 1-4 rows {label, value (1st country), value_b (2nd)} only with numbers the narration says",
    '{"caption": "Armies in 1939", "items": [{"label": "Poland", "query": "Poland"}, {"label": "Germany", '
    '"query": "Germany"}], "stats": [{"label": "Tanks", "value": "880", "value_b": "2,700"}]}')
# do diretor só saem estes; os decorativos (headline, mosaic, showcase...) continuam com a IA das cenas e o ritmo
STRUCTURE_KINDS = ("ranking", "steps")
DIRECTOR_KINDS = set(CATALOG) | set(EXTRA_CATALOG) | set(STRUCTURE_KINDS)
DATA_GAP = 8.0  # dois cards de dado nunca a menos disso (o vídeo vira só gráfico)

SYSTEM = """You are the editor-in-chief of a faceless YouTube channel. Before the video is assembled you read the
WHOLE narration script and decide, sentence by sentence, what goes on screen. A program then draws exactly what
you decide, so be precise. Answer with ONE JSON object only.

The script is given as numbered sentences "[i] text" (one sentence takes about 4 seconds of narration). Always
refer to sentences by their number i.

Return:
{
  "niche": short English niche of THIS video ("cars", "personal finance", "cooking", "true crime"...),
  "automotive": true when the video is about cars/vehicles/parts/driving,
  "subject": the single product/thing the WHOLE video is about ("Toyota Corolla"), or "" (lists, rankings, topics),
  "title": the video's title for the opening title card, in the SCRIPT language, 3-8 words, from what the hook
           promises ("A invenção que pode aposentar o adubo"),
  "kicker": 1-3 words in the SCRIPT language shown small above the title (the theme: "Agro brasileiro", "Recalls 2026"),
  "hook": for short vertical videos: the 3-7 word punchline of the FIRST sentences, in the SCRIPT language, words taken
          from the narration, written to stop the scroll ("This car was banned in 3 countries"),
  "stamp": 1-2 words or a number from the first sentences for a rubber stamp over the hook ("BANNED", "$45,000",
           "PROIBIDO") or "",
  "currency": the currency symbol the money in the script uses ("C$", "£", "€", "$", "R$", "zł") or "",
  "visual_niche": 6-10 short CLIP-style phrases describing what on-topic images of this video look like
                  ("a photo of a car dealership lot", "a close-up of winter tire tread", "a kitchen counter with
                  fresh vegetables"),
  "visual_off": 3-6 short phrases of images that would be OFF-topic for this video ("a beach landscape",
                "a portrait of a person", "a city skyline"),
  "sections": [ranked items, see RANKINGS],
  "lists": [numbered lists, see LISTS],
  "cards": [data cards, see CARDS],
  "named": [specific real things the viewer should SEE a photo of, see NAMED]
}

RANKINGS (top-N lists, countdowns, "avoid/buy" lists): one entry per ranked item:
  {"header": i, "last": j, "rank": 8, "name": "Chevrolet Blazer", "verdict": "", "show": true}
  - header: the sentence that announces the position ("Number eight.", "Numéro 4 : la Mazda 6", "Avoid number two.").
    Teasers are NOT headers ("And number one? It sold three in a quarter.", "And number one, by the way, is new.").
  - last: the last sentence about that item. The section ENDS before recaps ("So that's five."), like/subscribe
    requests, transitions to another part ("Now, the biggest discounts...") and before the next header.
  - name: the item exactly as it should be WRITTEN on screen: brand + model, digits for numbers, official spelling
    ("Hyundai IONIQ 5", "Ford F-150", "Toyota RAV4", "Lexus RX 350", "Audi Q4 e-tron"). No articles, no
    "gas"/"electric"/"new" in front. When the item is a reason/criterion, the short title ("Salt", "Cold starts").
  - verdict: "Avoid" or "Buy" (in the script language) when the header says it, else "".
  - show: true when images should show that named thing (a car, a product, a place); false for abstract reasons.

LISTS (numbered tips, steps, rounds, layers, signs: "One. The sales numbers." "Round three. Money and time."):
  {"word": "ROUND", "items": [{"header": i, "n": 1, "title": "Money and time"}, ...]}
  - header: the sentence that says the number ("Two." or "Round two. Tread life." or "Two: the invoices.").
  - n: 1, 2, 3... in order; a list has at least 2 items. Do not make a list out of ranking headers.
  - title: 1-6 words, the tip's title copied from the narration (the words right after the number).
  - word: "ROUND", "LAYER", "RULE", "TIP", "STEP"... as said ("" when the script only says "One.", "Two.").

CARDS: data graphics. Kinds and their fields:
{catalog}
  Card format: {"first": i, "last": j, "kind": "...", "fields": {...}} (first..last = the sentences it illustrates,
  usually one; at most 6).
  - Use a card ONLY where the narration says it (a number, a person, a year, a term, a process, a myth, a promise):
    aim for one card about every 20-30 seconds of narration across the whole video (story videos too, not only
    data), never two cards less than 10 seconds apart, never on a ranking header or list header sentence, never on
    the intro hook of the first 10 seconds, never on like/subscribe requests, never on the legal disclaimer.
  - Prefer the richest fitting kind (a repair cost is "receipt", not "price"; a share of a whole is "percentbar";
    a change like "down 37%" is "stat" with prefix "-").

NAMED: every specific, real thing with a proper name that the narration talks about and a photo can show: a product
  or model ("Optima YellowTop D35"), a store or brand ("Canadian Tire"), a person, a place or landmark, a building, a
  vehicle, a ship, an event. Not generic things ("a battery", "a store", "winter"). The program downloads a photo of
  each one and shows it where the narration names it. Most important first, at most 15:
  {"name": "Optima YellowTop", "query": "Optima YellowTop D35 car battery", "say": ["YellowTop", "Optima"],
   "say_not": []}
  - query: an English web image search that returns a photo of exactly that thing (brand + model + what it is:
    "Canadian Tire store exterior", "Kirkland Signature car battery group 35", "Elon Musk portrait").
  - say: 1-3 words or short phrases spelled exactly as the narration says them, that mark a sentence as talking
    about this thing.
  - say_not: names of SIMILAR things in this script that contain a say word, where this photo must NOT appear
    (for "Kirkland Signature" when the script also has "Kirkland Signature AGM": ["Kirkland Signature AGM"]).

ON-SCREEN TEXT (names, captions, labels, titles, body, points):
  - in the SCRIPT language, short, copied from the narration (you may shorten, never add facts);
  - numbers as digits with the currency symbol and thousands separator of the script language
    ("C$12,000", "£1,800", "32 900 €", "R$ 89.900"), years as digits ("2026", "the 2027s"), percentages "37%";
  - product names in their written form ("IONIQ 5", "bZ", "Fiat 500e", "EX-L", "CR-V", "F-150").
  - EVERY number shown must be said in that card's sentences. Never invent data, never use general knowledge.
Leave lists empty when there is nothing of that kind ([] / "")."""


def system_prompt(extra: tuple[str, ...] = ()) -> str:
    """``extra``: cards do ``EXTRA_CATALOG`` que o estilo do vídeo liga (o esquema desenhado do Documentário)."""
    items = [*CATALOG.items(), *((k, EXTRA_CATALOG[k]) for k in extra if k in EXTRA_CATALOG)]
    lines = [f'  - "{kind}": {when}. Fields: {example}' for kind, (when, example) in items]
    return SYSTEM.replace("{catalog}", "\n".join(lines))


def card_goal(n_sentences: int, extra: tuple[str, ...] = ()) -> str:
    """Quantos cards pedir, com número: no roteiro longo o gpt-5 economizava ("um a cada 20–30 s" virou 8 cards em
    19 min de Operação Greif, com um mapa só e sem seta, num roteiro cheio de lugares)."""
    minutes = n_sentences * 4 / 60
    lo, hi = max(3, round(minutes * 60 / 30)), max(4, round(minutes * 60 / 20))
    goal = (f"This narration has {n_sentences} sentences (about {minutes:.0f} minutes): return {lo}-{hi} cards, spread "
            "over the WHOLE video, not only the beginning.")
    if "map" in extra:
        goal += (" Every time the story moves to a new place or describes a movement, attack, march, trip or border, "
                 "use a map (with route arrows when someone goes from A to B): a history or war video usually "
                 "needs 6-12 maps.")
    if "flagversus" in extra:
        goal += " Use flagversus when two countries face each other or are compared."
    return goal


def user_prompt(sentences: list, channel_niche: str = "", language: str = "", extra: tuple[str, ...] = ()) -> str:
    head = []
    if extra:  # estilo Documentário: o número de cards e de mapas vem escrito (os outros canais ficam como eram)
        head.append(card_goal(len(sentences), extra))
    if channel_niche:
        head.append(f"Channel niche (a hint; the script decides): {channel_niche}.")
    if language:
        head.append(f"Script language: {language}.")
    lines = [f"[{s.index}] {s.text}" for s in sentences]  # sem tempo: o diretor roda junto com a transcrição
    return "\n".join(head + ["", "Script:"] + lines)


# --------------------------------------------------------------------------------------------- plano
@dataclass
class Section:
    header: int
    last: int
    rank: int
    name: str
    verdict: str = ""
    show: bool = True


@dataclass
class StepItem:
    header: int
    n: int
    total: int
    title: str
    word: str = ""


@dataclass
class Card:
    first: int
    last: int
    kind: str
    fields: dict[str, Any]


@dataclass
class Named:
    """Coisa com nome próprio que precisa de foto (a bateria, a loja, a pessoa): ``broll_bot/named_images.py``."""
    name: str
    query: str
    say: list[str]
    say_not: list[str] = field(default_factory=list)
    mentions: int = 0  # quantas vezes a fala cita o nome (quem aparece a toda hora ganha mais fotos)


@dataclass
class DirectorPlan:
    niche: str = ""
    automotive: bool = True
    subject: str = ""
    title: str = ""  # abertura do vídeo (template "opener")
    kicker: str = ""
    hook: str = ""  # gancho do vídeo curto (broll_bot/shortsfx)
    stamp: str = ""
    currency: str = ""
    visual_niche: list[str] = field(default_factory=list)
    visual_off: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    steps: list[StepItem] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    named: list[Named] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)  # as frases do roteiro (para achar o índice pela frase)
    dropped: list[str] = field(default_factory=list)  # o que a validação tirou, e por quê
    model: str = ""

    def per_sentence(self) -> list[str]:
        """Assunto de cada frase (o nome da seção do ranking, se ela mostra o item)."""
        out = [""] * len(self.texts)
        for sec in self.sections:
            if not sec.show:
                continue
            for i in range(sec.header, min(sec.last, len(out) - 1) + 1):
                out[i] = sec.name
        return out

    def summary(self) -> dict[str, Any]:
        return {"model": self.model, "niche": self.niche, "automotive": self.automotive, "subject": self.subject,
                "title": self.title, "hook": self.hook, "stamp": self.stamp,
                "currency": self.currency, "sections": [f"#{s.rank} {s.name}" for s in self.sections],
                "steps": [f"{s.word or 'STEP'} {s.n}/{s.total}: {s.title}" for s in self.steps],
                "cards": [f"[{c.first}] {c.kind}" for c in self.cards], "named": [n.name for n in self.named],
                "dropped": self.dropped}


_PLAN: DirectorPlan | None = None


def install(plan: DirectorPlan | None) -> None:
    global _PLAN
    _PLAN = plan


def reset() -> None:
    install(None)


def active() -> bool:
    return _PLAN is not None


def current() -> DirectorPlan | None:
    return _PLAN


# --------------------------------------------------------------------------------------------- consultas
def _indices(text: str) -> list[int]:
    t = (text or "").strip()
    return [i for i, x in enumerate(_PLAN.texts) if x.strip() == t] if _PLAN else []


def header(text: str) -> tuple[int, str, str] | None:
    """(posição, nome, veredito) quando ``text`` é o cabeçalho de uma seção do ranking."""
    if _PLAN is None:
        return None
    idx = set(_indices(text))
    for sec in _PLAN.sections:
        if sec.header in idx:
            return sec.rank, sec.name, sec.verdict
    return None


def fill_steps(pairs: dict, single: dict) -> None:
    """Preenche as tabelas de ``story_detect`` (frase + seguinte -> (n, total)) com as listas do plano."""
    if _PLAN is None:
        return
    seen: dict[str, int] = {}
    for t in _PLAN.texts:
        seen[t.strip()] = seen.get(t.strip(), 0) + 1
    for st in _PLAN.steps:
        text = _PLAN.texts[st.header].strip()
        nxt = _PLAN.texts[st.header + 1].strip() if st.header + 1 < len(_PLAN.texts) else ""
        pairs[(text, nxt)] = (st.n, st.total)
        if seen[text] == 1:
            single[text] = (st.n, st.total)


def _locate(texts: list[str]) -> int | None:
    """Índice no roteiro da 1ª frase de ``texts`` (as frases de uma cena, em ordem)."""
    if _PLAN is None or not texts:
        return None
    n = len(_PLAN.texts)
    for i in _indices(texts[0]):
        if all(i + k < n and _PLAN.texts[i + k].strip() == t.strip() for k, t in enumerate(texts)):
            return i
    return None


def detections(texts: list[str]) -> list:
    """Os cards e passos do plano que caem nestas frases, como ``Detection`` (índices relativos a ``texts``)."""
    from .auto_templates import Detection

    base = _locate(texts)
    if base is None or _PLAN is None:
        return []
    end = base + len(texts) - 1
    out = []
    for st in _PLAN.steps:
        if base <= st.header <= end:
            last = st.header + 1 if len(_PLAN.texts[st.header].split()) <= 3 and st.header < end else st.header
            # "ROUND 1 OF 8" no lugar de "STEP 1 OF 8"; "step"/"étape"/"passo" é o rótulo padrão do quadro
            word = "" if st.word.lower() in ("step", "steps", "étape", "passo", "krok") else st.word.upper()
            fields = {"number": {"value": st.n, "total": st.total}, "caption": st.title, "subtitle": word}
            out.append(Detection("steps", st.header - base, last - base, 9, fields))
    for c in _PLAN.cards:
        if base <= c.first <= end:
            out.append(Detection(c.kind, c.first - base, min(c.last, end) - base, 7, json.loads(json.dumps(c.fields))))
    return sorted(out, key=lambda d: d.first)


def cross_detection(texts: list[str], subject: str = ""):
    """Para ``merge_across_scenes``: o primeiro card do plano que ocupa mais de uma frase destas."""
    for d in detections(texts):
        if d.last > d.first:
            return d
    return None


# --------------------------------------------------------------------------------------------- validação
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "your", "their", "its", "are", "was", "were", "has",
         "have", "had", "but", "not", "you", "les", "des", "une", "pour", "avec", "dans", "sur", "que", "qui",
         "por", "para", "com", "uma", "dos", "das", "nos", "nas", "mais", "como", "est"}


def _numbers(text: str) -> set[float]:
    """Os números de um texto já escrito como na tela ("C$12,000" -> 12000; "2.5" -> 2.5; "1 500" -> 1500)."""
    out: set[float] = set()
    t = re.sub(r"(?<=\d)[\s  ](?=\d{3}\b)", "", text)  # "32 900" (fr/pl)
    for m in _NUM.finditer(t):
        tok = m.group(0)
        cands = {tok.replace(",", ""), tok.replace(".", "").replace(",", "."), tok.replace(",", ".")}
        for c in cands:
            try:
                out.add(round(float(c), 4))
            except ValueError:
                pass
    return out


def _spoken_numbers(text: str, lang: str) -> set[float]:
    from .spoken import screen_text, word_int

    out = _numbers(text) | _numbers(screen_text(text, lang))
    from .coherence import SPELLED

    for w in re.findall(r"[A-Za-zÀ-ÿ]+(?:-[A-Za-zÀ-ÿ]+)?", text):
        n = word_int(w.lower())
        if n is None:
            n = SPELLED.get(normalize(w))  # "cem quilos", "duzentos", "mille", "sto" (pt/fr/pl)
        if n is not None:
            out.add(float(n))
    from .spelled import spelled_values

    out |= spelled_values(text, lang)  # "vinte e oito horas", "setecentos milhões", "dois mil e quinze"
    for w in re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower()):  # "January first", "the thirtieth"
        if w in _ORDINALS:
            out.add(float(_ORDINALS[w]))
    low = text.lower()
    for word, days in (("week", 7), ("fortnight", 14), ("month", 30), ("semaine", 7), ("semana", 7), ("mois", 30),
                       ("mês", 30), ("tydzie", 7)):
        if word in low:
            out.add(float(days))
    out |= {round(v * k, 4) for v in list(out) for k in (1000.0, 1e6, 1e9)}
    return out


_ORDINALS = {w: k for k, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth "
    "fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split(), 1)}
_ORDINALS.update({"thirtieth": 30, "twenty-first": 21, "twenty-second": 22, "twenty-third": 23, "thirty-first": 31})


def _field_numbers(kind: str, fields: dict[str, Any]) -> list[float]:
    """Os valores do card (o dado em si: tem que ter sido dito no trecho)."""
    vals: list[float] = []
    num = fields.get("number") or {}
    for key in ("value", "value_to", "low", "from"):
        v = num.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            vals.append(float(v))
    if kind == "peoplegrid" and isinstance(num.get("total"), (int, float)):
        vals.append(float(num["total"]))
    for st in fields.get("stats") or []:
        for key in ("value", "value_b"):
            vals += sorted(_numbers(str(st.get(key, ""))))
    return vals


def _label_numbers(fields: dict[str, Any]) -> list[float]:
    """Números dentro dos textos (legenda, tópicos, rótulos): nomes como "Q4 e-tron" e datas entram aqui."""
    vals: list[float] = []
    for key in ("caption", "subtitle", "body"):
        vals += sorted(_numbers(str(fields.get(key, ""))))
    for p in fields.get("points") or []:
        vals += sorted(_numbers(str(p.get("text", "") if isinstance(p, dict) else p)))
    for it in fields.get("items") or []:
        vals += sorted(_numbers(str(it.get("label", "") if isinstance(it, dict) else it)))
    vals += sorted(_numbers(str((fields.get("number") or {}).get("label", ""))))
    return vals


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", normalize(text)) if len(w) >= 3 and w not in _STOP]


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _text_ok(value: str, span_words: set[str], strict: bool = False) -> bool:
    """O texto da tela vem da fala: a maioria das palavras dele foi dita (rótulo curto passa)."""
    words = _words(value)
    if not words:
        return True
    singular = {_singular(s) for s in span_words}
    # "fines"/"fine", "tire"/"tires": plural curto não casava (o prefixo de 5 letras só vale para palavra longa)
    hit = sum(1 for w in words if w in span_words or _singular(w) in singular
              or any(w[:5] == s[:5] for s in span_words if len(w) >= 5))
    if strict:
        return hit == len(words)
    return len(words) <= 2 or hit / len(words) >= 0.5


def _span(texts: list[str], first: int, last: int, lang: str) -> tuple[str, set[str]]:
    from .spoken import screen_text

    raw = " ".join(texts[first:last + 1])
    shown = screen_text(raw, lang)
    # números ditos por extenso também valem como palavra: "A tragédia de mais de 150 mortos" numa fala com
    # "mais de cento e cinquenta pessoas" (o "150" da tela foi dito)
    from .spelled import spelled_values

    digits = {f"{v:g}" for v in spelled_values(raw, lang) if v >= 10 and v == int(v)}
    return raw, set(_words(raw)) | set(_words(shown)) | digits


def validate(raw: dict[str, Any], sentences: list, lang: str = "en") -> DirectorPlan:
    """Plano da IA -> ``DirectorPlan`` só com o que bate com o roteiro (o resto vai para ``dropped``)."""
    texts = [s.text for s in sentences]
    starts = [s.start for s in sentences]
    n = len(texts)
    plan = DirectorPlan(texts=texts)
    plan.niche = str(raw.get("niche") or "").strip()[:60]
    plan.automotive = bool(raw.get("automotive", True))
    plan.currency = str(raw.get("currency") or "").strip()[:3]
    subject = str(raw.get("subject") or "").strip()
    if subject:
        if _text_ok(subject, set(_words(" ".join(texts))) | set(_words(_screen_all(texts, lang))), strict=True):
            plan.subject = subject
        else:
            plan.dropped.append(f"assunto '{subject}': não aparece no roteiro")
    title = _cap(_screen(str(raw.get("title") or "").strip().rstrip("."), lang))
    if title and len(title.split()) <= 12:
        all_words = set(_words(" ".join(texts))) | set(_words(_screen_all(texts, lang)))
        if _text_ok(title, all_words):  # o título resume o gancho: a maioria das palavras está no roteiro
            plan.title = title[:80]
            plan.kicker = _screen(str(raw.get("kicker") or "").strip(), lang)[:30]
        else:
            plan.dropped.append(f"título '{title[:40]}': fora do roteiro")
    head = texts[:3]
    head_words = set(_words(" ".join(head))) | set(_words(_screen_all(head, lang)))
    hook = _screen(" ".join(str(raw.get("hook") or "").split()).rstrip("."), lang)
    if hook and len(hook.split()) <= 9:
        if _text_ok(hook, head_words):
            plan.hook = hook[:70]
        else:
            plan.dropped.append(f"gancho '{hook[:40]}': fora das primeiras frases")
    stamp = _screen(" ".join(str(raw.get("stamp") or "").split()).strip(" .!"), lang)
    if stamp and len(stamp.split()) <= 3 and len(stamp) <= 18:
        nums = _numbers(stamp)
        spoken = set().union(*(_numbers(t) | _spoken_numbers(t, lang) for t in head)) if head else set()
        if (nums and nums <= spoken) or (not nums and _text_ok(stamp, head_words, strict=True)):
            plan.stamp = stamp
        else:
            plan.dropped.append(f"carimbo '{stamp}': fora das primeiras frases")
    plan.visual_niche = [str(x)[:90] for x in (raw.get("visual_niche") or []) if str(x).strip()][:10]
    plan.visual_off = [str(x)[:90] for x in (raw.get("visual_off") or []) if str(x).strip()][:6]

    def idx(v: Any) -> int | None:
        try:
            i = int(v)
        except (TypeError, ValueError):
            return None
        return i if 0 <= i < n else None

    taken: set[int] = set()  # frases de cabeçalho (ranking/lista): card nenhum em cima
    # ---- seções do ranking
    ranks_seen: set[tuple[int, str]] = set()
    for s in raw.get("sections") or []:
        h, last = idx(s.get("header")), idx(s.get("last"))
        name = str(s.get("name") or "").strip()
        try:
            rank = int(s.get("rank"))
        except (TypeError, ValueError):
            rank = 0
        if h is None or not name or rank <= 0 or rank > 100:
            plan.dropped.append(f"seção inválida: {json.dumps(s, ensure_ascii=False)[:120]}")
            continue
        last = last if last is not None and last >= h else h
        last = min(last, h + 60)
        _, words = _span(texts, h, min(h + 4, n - 1), lang)
        if not _text_ok(name, words, strict=bool(s.get("show", True))):
            plan.dropped.append(f"seção [{h}] '{name}': o nome não aparece nas frases do item")
            continue
        verdict = str(s.get("verdict") or "").strip()[:20]
        key = (rank, normalize(verdict))
        if key in ranks_seen:
            plan.dropped.append(f"seção [{h}] '{name}': posição {rank} repetida")
            continue
        ranks_seen.add(key)
        plan.sections.append(Section(h, last, rank, name, verdict, bool(s.get("show", True))))
        taken.add(h)
    plan.sections.sort(key=lambda x: x.header)
    for a, b in zip(plan.sections, plan.sections[1:]):  # a seção acaba antes do próximo cabeçalho
        a.last = min(a.last, b.header - 1)
    if len(plan.sections) == 1:
        plan.dropped.append("ranking com um item só: ignorado")
        taken.discard(plan.sections[0].header)
        plan.sections = []

    # ---- listas numeradas
    for lst in raw.get("lists") or []:
        word = re.sub(r"[^\wÀ-ÿ ]", "", str(lst.get("word") or "")).strip()[:12]
        items = []
        for it in lst.get("items") or []:
            h = idx(it.get("header"))
            title = str(it.get("title") or "").strip().rstrip(".")
            if h is None or h in taken or not title:
                continue
            title = _step_title(title, h, texts, lang)
            _, words = _span(texts, h, min(h + 1, n - 1), lang)
            if not _text_ok(title, words):
                plan.dropped.append(f"passo [{h}] '{title}': o título não está na fala")
                continue
            items.append((h, title))
        items.sort()
        if len(items) < 2:
            if items:
                plan.dropped.append(f"lista com um passo só ([{items[0][0]}]): ignorada")
            continue
        total = len(items)
        for k, (h, title) in enumerate(items, 1):
            plan.steps.append(StepItem(h, k, total, title[:60], word))
            taken.add(h)
    for word, items in _ordinal_lists(texts, taken, lang):
        total = len(items)
        for k, (h, title) in enumerate(items, 1):
            plan.steps.append(StepItem(h, k, total, title[:60], word))
            taken.add(h)
        log.info("lista numerada achada no roteiro (a IA não marcou)", extra={
            "stage": "director", "palavra": word, "itens": [f"[{h}] {t}" for h, t in items]})

    # ---- cards
    last_at = -1e9
    cards = []
    for c in raw.get("cards") or []:
        kind = str(c.get("kind") or "")
        first, last = idx(c.get("first")), idx(c.get("last"))
        fields = c.get("fields") if isinstance(c.get("fields"), dict) else {}
        if kind not in CATALOG and kind not in EXTRA_CATALOG or first is None:
            plan.dropped.append(f"card desconhecido/sem frase: {kind or '?'}")
            continue
        last = last if last is not None and first <= last <= first + 6 else first
        if any(i in taken for i in range(first, last + 1)):
            plan.dropped.append(f"card [{first}] {kind}: em cima de um cabeçalho")
            continue
        raw_span, _ = _span(texts, first, last, lang)
        # textos (legenda, corpo da fonte) podem usar a frase vizinha ("Liebig publicou uma ideia" + "As plantas não
        # comem terra"); os números continuam presos ao trecho (logo abaixo)
        _, words = _span(texts, max(0, first - 1), min(n - 1, last + 2), lang)
        # o dado vem do trecho (com 2 frases antes e 1 depois: "it was 471" + "reported three sold");
        # número dentro de nome/legenda pode vir da seção ("Q4 e-tron", "January 1, 2027")
        near, _ = _span(texts, max(0, first - 2), min(n - 1, last + 1), lang)
        wide, wide_words = _span(texts, max(0, first - 8), min(n - 1, last + 2), lang)
        said, said_wide = _spoken_numbers(near, lang), _spoken_numbers(wide, lang)
        missing = [v for v in _field_numbers(kind, fields) if not any(abs(v - s) < 1e-3 for s in said)]
        if not missing:
            # um ano/número que ninguém disse no subtítulo ou no rótulo ("first half of 2026" numa frase que diz
            # "of the year"): sai só aquele texto, o card fica
            fields = dict(fields)
            for key in ("subtitle", "body"):
                if any(not any(abs(v - s) < 1e-3 for s in said_wide) for v in _numbers(str(fields.get(key, "")))):
                    plan.dropped.append(f"card [{first}] {kind}: {key} '{str(fields[key])[:40]}' saiu (número não dito)")
                    fields.pop(key)
            num = dict(fields.get("number") or {})
            if any(not any(abs(v - s) < 1e-3 for s in said_wide) for v in _numbers(str(num.get("label", "")))):
                plan.dropped.append(f"card [{first}] {kind}: rótulo '{str(num['label'])[:40]}' saiu (número não dito)")
                num.pop("label")
                fields["number"] = num
        missing += [v for v in _label_numbers(fields) if not any(abs(v - s) < 1e-3 for s in said_wide)]
        words |= wide_words if kind in ("price", "stat", "chart", "fromto", "receipt") else set()
        if missing:
            plan.dropped.append(f"card [{first}] {kind}: número que não foi dito ({missing[0]:g})")
            continue
        if kind == "diagram":  # anotação do esquema fora da fala sai sozinha; o esquema fica se sobrar desenho
            from .motion.templates_docu import clean_elements

            els = []
            for el in clean_elements(fields.get("elements")):
                said = el.get("text") or el.get("label") or ""
                if said and not _text_ok(said, words):
                    plan.dropped.append(f"card [{first}] diagram: texto '{said[:30]}' fora da fala")
                    if el["t"] == "text":
                        continue
                    el.pop("label", None)
                els.append(el)
            if len(els) < 3:
                plan.dropped.append(f"card [{first}] diagram: desenho de menos")
                continue
            fields = {**fields, "elements": els}
        if kind in ("map", "flagversus"):
            # o mapa pode mostrar o lugar dito umas frases antes (a abertura diz "Aywaille... au sud de Liège" na
            # frase 1 e a IA põe o mapa na 7, depois do gancho): até 6 frases (~25 s) para trás
            fields = _geo_fields(kind, fields, raw_span + " " + " ".join(texts[max(0, first - 6):last + 2]),
                                 first, plan.dropped)
            if fields is None:
                continue
        texts_shown = [str(fields.get(k, "")) for k in ("caption", "subtitle", "body")]
        texts_shown += [str(p.get("text", "")) for p in fields.get("points") or [] if isinstance(p, dict)]
        texts_shown += [str(st.get("label", "")) for st in fields.get("stats") or [] if isinstance(st, dict)]
        texts_shown += [str(h) for h in fields.get("highlights") or []]
        strict_name = kind in ("source", "profile")
        # o nome da pessoa/fonte pode ter sido dito algumas frases antes ("O nome dele é Alfredo Moser." e o
        # perfil entra quando a fala conta quem ele é); continua exigindo o nome inteiro
        # o perfil inteiro (nome e o fato sobre a pessoa: "Estudou só até o ensino fundamental") vem da biografia
        # que a fala acabou de contar
        if kind == "profile":
            words = words | wide_words
        name_words = words | wide_words if strict_name else words
        bad = next((t for t in texts_shown if not _text_ok(t, name_words if t == texts_shown[0] else words,
                                                            strict=strict_name and t == texts_shown[0])), None)
        if bad is not None:
            plan.dropped.append(f"card [{first}] {kind}: texto fora da fala ('{bad[:50]}')")
            continue
        cards.append(Card(first, last, kind, _clean_fields(kind, fields, lang)))
    for card in sorted(cards, key=lambda c: c.first):
        at = starts[card.first]
        if at - last_at < DATA_GAP:
            plan.dropped.append(f"card [{card.first}] {card.kind}: colado no anterior")
            continue
        if plan.cards and card.first <= plan.cards[-1].last:
            plan.dropped.append(f"card [{card.first}] {card.kind}: sobreposto ao anterior")
            continue
        plan.cards.append(card)
        last_at = at
    plan.named = _named(raw.get("named") or [], " ".join(texts), plan.dropped)
    return plan


def _geo_fields(kind: str, fields: dict[str, Any], spoken: str, first: int, dropped: list[str]) -> dict | None:
    """Mapa e país x país: só os lugares e países que a fala cita (em qualquer língua: "Niemcy" vale pela Alemanha);
    o nome na tela é o que a fala usa. Sem nada no mapa, ou sem os 2 países, o card sai."""
    from .motion.geo import find_country, fold

    tokens = fold(spoken).split()
    folded = " " + " ".join(tokens) + " "

    def said(word: str, stem_ok: bool = True) -> str:
        """A palavra como a fala diz: o nome inteiro, ou (nome de uma palavra) a mesma raiz declinada ("Polskę",
        "Niemiec", "Dieppe'u")."""
        key = fold(word)
        if not key:
            return ""
        if f" {key} " in folded:
            return word
        if stem_ok and " " not in key and len(key) >= 5:
            stem = key[:max(4, len(key) - 2)]
            hit = next((t for t in tokens if t.startswith(stem) and len(t) <= len(key) + 3), None)
            if hit is not None:
                return word
        return ""

    def said_as(*words: str) -> str:
        """O nome na língua da fala: primeiro o que ela diz igualzinho ("Belgique"), só depois pela raiz (senão o
        "Belgium" do inglês passava pela raiz "belgi" e saía em inglês num vídeo em francês)."""
        hit = next((w for w in words if w and said(w, stem_ok=False)), "")
        hit = hit or next((w for w in words if w and said(w)), "")
        return _bare_place(hit)

    if kind == "flagversus":
        items = []
        for it in (fields.get("items") or [])[:2]:
            if not isinstance(it, dict):
                continue
            c = find_country(str(it.get("query") or it.get("label") or ""))
            shown = said_as(str(it.get("label") or ""), *(c.names if c else []))
            if c is None or not shown:
                dropped.append(f"card [{first}] flagversus: país '{str(it.get('label'))[:30]}' não dito")
                return None
            items.append({"label": shown, "query": c.name})
        if len(items) != 2:
            dropped.append(f"card [{first}] flagversus: precisa de 2 países")
            return None
        return {**fields, "items": items}
    from .motion.templates_map import clean_map_elements

    kept, names = [], set()
    for el in clean_map_elements(fields.get("elements")):
        if el["t"] == "route":
            kept.append(el)
            continue
        c = find_country(el["name"]) if el["t"] == "country" else None
        shown = said_as(el.get("say", ""), el["name"], *(c.names if c else []))
        if not shown or (el["t"] == "country" and c is None):
            dropped.append(f"card [{first}] map: '{el['name'][:30]}' não dito")
            continue
        el["label"] = shown if shown.lower() != el["name"].lower() else el["name"]
        kept.append(el)
        names.add(el["name"])
    kept = [e for e in kept if e["t"] != "route" or (e["from"] in names and e["to"] in names)]
    if not any(e["t"] != "route" for e in kept):
        dropped.append(f"card [{first}] map: nenhum lugar dito")
        return None
    return {**fields, "elements": kept}


def _bare_place(word: str) -> str:
    """Nome na tela sem o artigo da fala ("l'Allemagne" -> "Allemagne", "la Pologne" -> "Pologne", "the UK" -> "UK"),
    a não ser que o artigo seja do nome ("Le Havre", "L'Aquila", "La Rochelle")."""
    from .motion.geo import find_country, find_place

    m = re.match(r"(?i)^(?:l['’]|d['’]|du\s+|de\s+la\s+|le\s+|la\s+|les\s+|the\s+)\s*(\S.*)$", word.strip())
    if not m or find_country(word) or find_place(word):
        return word
    rest = m.group(1)
    return rest if find_country(rest) or find_place(rest) or "'" in word[:3] or "’" in word[:3] else word


def _said(word: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.I))


def _named(items: list, script: str, dropped: list[str]) -> list[Named]:
    """Só o que a fala diz de verdade: ``say`` que não está no roteiro sai (a foto nunca acharia a cena)."""
    out: list[Named] = []
    for it in items[:15]:
        if not isinstance(it, dict):
            continue
        name = " ".join(str(it.get("name") or "").split())[:60]
        say = [w for w in (" ".join(str(x).split()) for x in it.get("say") or []) if w and _said(w, script)][:3]
        if not name or not say:
            dropped.append(f"foto de '{name[:40]}': o nome não aparece no roteiro")
            continue
        say_not = [w for w in (" ".join(str(x).split()) for x in it.get("say_not") or []) if w and _said(w, script)]
        query = " ".join(str(it.get("query") or name).split())[:100]
        mentions = len(re.findall("|".join(rf"(?<!\w){re.escape(w)}(?!\w)" for w in say), script, re.I))
        out.append(Named(name, query, say, say_not[:5], mentions))
    return out


_DANGLING = {"the", "a", "an", "of", "for", "to", "and", "or", "with", "in", "on", "at", "by", "from", "your", "their",
             "its", "my", "our", "this", "that", "le", "la", "les", "de", "du", "des", "o", "a", "os", "as", "do", "da"}


def _dangling(title: str) -> bool:
    words = title.split()
    last = words[-1].lower().strip(",;:") if words else ""
    return not words or last in _DANGLING or last.endswith(("'s", "’s"))


def _screen(text: str, lang: str) -> str:
    from .cues import dollar_symbol
    from .spoken import screen_text

    if not text:
        return text
    sym = dollar_symbol()
    if sym != "$":  # canal canadense: "$50,365" da IA vira "C$50,365" (US$ dito no roteiro fica)
        text = re.sub(r"(?<![A-Za-z$])\$(?=\s?\d)", sym, text)
    return screen_text(text, lang)


def _ordinal_table() -> dict[str, dict[str, int]]:
    forms = {
        "pl": ["pierwszy pierwsza pierwsze", "drugi druga drugie", "trzeci trzecia trzecie", "czwarty czwarta czwarte",
               "piaty piata piate", "szosty szosta szoste", "siodmy siodma siodme", "osmy osma osme",
               "dziewiaty dziewiata dziewiate", "dziesiaty dziesiata dziesiate"],
        "pt": ["primeiro primeira", "segundo segunda", "terceiro terceira", "quarto quarta", "quinto quinta",
               "sexto sexta", "setimo setima", "oitavo oitava", "nono nona", "decimo decima"],
        "es": ["primero primera primer", "segundo segunda", "tercero tercera tercer", "cuarto cuarta",
               "quinto quinta", "sexto sexta", "septimo septima", "octavo octava", "noveno novena", "decimo decima"],
        "fr": ["premier premiere", "deuxieme second seconde", "troisieme", "quatrieme", "cinquieme", "sixieme",
               "septieme", "huitieme", "neuvieme", "dixieme"],
        "en": ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"],
    }
    return {lang: {w: n for n, ws in enumerate(rows, 1) for w in ws.split()} for lang, rows in forms.items()}


_ORDINAL_WORDS = _ordinal_table()


def _ordinal_header(text: str, lang: str) -> tuple[str, int, str] | None:
    """ "Rzecz pierwsza." / "Primeira coisa: o seguro." -> (palavra, 1, resto da frase)."""
    table = _ORDINAL_WORDS.get((lang or "en").split("-")[0].lower())
    m = re.match(r"^\W*(\w+)\s+(\w+)\b[\s.:;,!–—-]*(.*)$", text.strip()) if table else None
    if not m:
        return None
    a, b, rest = m.groups()
    na, nb = normalize(a), normalize(b)
    if nb in table and na not in table:
        return a, table[nb], rest
    if na in table and nb not in table:
        return b, table[na], rest
    return None


def _ordinal_lists(texts: list[str], taken: set[int], lang: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Listas que o roteiro numera em ordem e a IA não marcou: "Rzecz pierwsza." ... "Rzecz piąta." (o vídeo
    polonês "5 rzeczy" saiu sem os cabeçalhos das 5 coisas). Mesma palavra, 1, 2, 3... em sequência, 3 itens
    ou mais (assim "a segunda vez" solta no meio do texto não vira lista)."""
    by_word: dict[str, list[tuple[int, int, str, str]]] = {}
    for h, text in enumerate(texts):
        if h in taken:
            continue
        hit = _ordinal_header(text, lang)
        if hit:
            word, num, rest = hit
            by_word.setdefault(normalize(word), []).append((h, num, word, rest))
    out = []
    for hits in by_word.values():
        runs: list[list[tuple[int, int, str, str]]] = []
        cur: list[tuple[int, int, str, str]] = []
        for hit in hits:
            if hit[1] == len(cur) + 1:
                cur.append(hit)
            elif hit[1] == 1:
                runs.append(cur)
                cur = [hit]
        runs.append(cur)
        for run in (r for r in runs if len(r) >= 3):
            items = []
            for h, _, word, rest in run:
                tip = rest if len(rest.split()) >= 2 else (texts[h + 1] if h + 1 < len(texts) else "")
                tip = re.split(r"(?<=[.!?])\s", tip.strip())[0].rstrip(".!?")
                first = tip.split(",")[0].strip()
                tip = first if len(first.split()) >= 3 else tip
                items.append((h, _step_title(" ".join(tip.split()[:7]), h, texts, lang)))
            word = run[0][2]
            out.append((word if len(normalize(word)) >= 3 else "", items))  # "Po pierwsze": sem palavra
    return out


def _step_title(title: str, header: int, texts: list[str], lang: str) -> str:
    """Título da dica como vai na tela: anos e números escritos ("twenty twenty-sixes" -> "2026s"); título cortado
    no meio ("Look up the manufacturer's") vira a oração da própria dica ("Look up the manufacturer's current
    Canadian offers yourself")."""
    title = _cap(_screen(title, lang).strip().rstrip("."))
    if not _dangling(title):
        return title
    head = texts[header].strip()
    inline = re.match(r"^[^:]{1,30}:\s*(.+)$", head)
    tip = inline.group(1) if inline else (texts[header + 1] if len(head.split()) <= 4 and header + 1 < len(texts)
                                          else re.sub(r"^\W*\w+[.!]?\s*", "", head))
    tip = _screen(re.split(r"(?<=[.!?])\s", tip.strip())[0], lang).rstrip(".!?")
    first = tip.split(",")[0].strip()
    tip = first if len(first.split()) >= 2 else tip
    words = tip.split()[:9]
    while len(words) > 2 and _dangling(" ".join(words)):
        words.pop()
    return _cap(" ".join(words)) if words else title


def _cap(text: str) -> str:
    """ "os biológicos explodiram" -> "Os biológicos explodiram" (título do quadro)."""
    return text[:1].upper() + text[1:] if text else text


def _screen_all(texts: list[str], lang: str) -> str:
    from .spoken import screen_text

    return screen_text(" ".join(texts), lang)


def _clean_fields(kind: str, fields: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    """Só os campos que o template usa, nos tipos certos (a IA às vezes manda número como texto), com os textos
    escritos como na tela ("twenty twenty-six" -> "2026")."""
    out: dict[str, Any] = {}
    for key in ("caption", "subtitle", "body"):
        if fields.get(key):
            out[key] = _screen(str(fields[key]).strip(), lang)[:140 if key == "body" else 70]
    num = fields.get("number")
    if isinstance(num, dict):
        clean: dict[str, Any] = {}
        for key, value in num.items():
            if key in ("prefix", "suffix", "label"):
                clean[key] = (_screen(str(value), lang) if key == "label" else str(value))[:40]
            elif key in ("value", "value_to", "low", "from", "max", "level", "total", "decimals"):
                try:
                    clean[key] = int(value) if key == "decimals" else float(value)
                except (TypeError, ValueError):
                    continue
        from .cues import dollar_symbol

        for key in ("prefix", "suffix"):  # "$" da IA num canal em C$ (ou £/€ quando a moeda do canal é outra)
            if clean.get(key, "").strip() == "$" and dollar_symbol() != "$":
                clean[key] = clean[key].replace("$", dollar_symbol())
        if kind == "stat" and clean.get("prefix") and clean["prefix"].strip() in ("-", "+", "−"):
            clean["prefix"] = clean["prefix"].strip().replace("−", "-")
        out["number"] = clean
    if isinstance(fields.get("points"), list):
        out["points"] = [{"text": _screen(str(p.get("text", "")), lang)[:60], "positive": bool(p.get("positive", True))}
                         for p in fields["points"] if isinstance(p, dict) and p.get("text")][:6]
    if isinstance(fields.get("stats"), list):
        # o gráfico de barras não desenha sinal: "-34" vira "34" (a queda está no título)
        out["stats"] = [{"label": str(s.get("label", ""))[:30],
                         "value": (str(s.get("value", "")).lstrip("-−+ ") if kind == "chart"
                                   else str(s.get("value", "")))[:16],
                         "unit": str(s.get("unit", ""))[:10], "bar": float(s.get("bar", 0) or 0),
                         "value_b": str(s.get("value_b", ""))[:16]}
                        for s in fields["stats"] if isinstance(s, dict)][:6]
    if isinstance(fields.get("items"), list):
        out["items"] = [{"label": str(i.get("label", ""))[:30], "query": str(i.get("query", ""))[:60],
                         "note": str(i.get("note", ""))[:30]} for i in fields["items"] if isinstance(i, dict)][:6]
    if isinstance(fields.get("highlights"), list):
        out["highlights"] = [str(h)[:90 if kind == "article" else 40] for h in fields["highlights"]][:3]
    if kind == "diagram" and isinstance(fields.get("elements"), list):
        from .motion.templates_docu import clean_elements

        out["elements"] = clean_elements(fields["elements"])
    if kind == "map" and isinstance(fields.get("elements"), list):
        from .motion.templates_map import clean_map_elements

        out["elements"] = clean_map_elements(fields["elements"])
    return out


# --------------------------------------------------------------------------------------------- chamada
def _provider(cfg) -> str:
    p = cfg.director.provider
    if p == "auto":
        return "openai" if cfg.analysis.provider == "openai" else "anthropic" \
            if cfg.analysis.provider == "anthropic" else ""
    return p


def _call(cfg, system: str, prompt: str) -> tuple[dict[str, Any], str]:
    provider = _provider(cfg)
    d = cfg.director
    if provider == "openai":
        from .llm_openai import OpenAIJSON

        llm = OpenAIJSON(d.openai_model, d.openai_effort, d.max_tokens, step="diretor de IA")
        return llm.complete(system, prompt), d.openai_model
    if provider == "anthropic":
        import anthropic

        from . import usage

        client = anthropic.Anthropic()
        resp = client.messages.create(model=d.model, max_tokens=d.max_tokens, system=system,
                                      messages=[{"role": "user", "content": prompt}],
                                      output_config={"effort": d.effort})
        usage.from_anthropic(resp, getattr(resp, "model", None) or d.model, "diretor de IA")
        text = "".join(getattr(b, "text", "") for b in resp.content)
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]), d.model
    raise RuntimeError("diretor de IA sem IA configurada (use OpenAI ou Claude no plano de cenas)")


def direct(sentences: list, cfg, cache=None, lang: str = "en") -> DirectorPlan | None:
    """Lê o roteiro com a IA e devolve o plano validado (ou ``None``: aí valem as regras de sempre)."""
    asked = ask(sentences, cfg, cache, lang)
    return finish(asked, sentences, lang)


def ask(sentences: list, cfg, cache=None, lang: str = "en") -> tuple[dict[str, Any], str] | None:
    """Só a chamada (não precisa do tempo das frases): o pipeline roda isto junto com a transcrição."""
    from .cache import make_key

    if not cfg.director.enabled or not sentences:
        return None
    styles = set(cfg.templates.styles) if cfg.templates.enabled else set()
    extra = tuple(k for k in EXTRA_CATALOG if k in styles)
    system = system_prompt(extra)
    prompt = user_prompt(sentences, cfg.analysis.niche, lang, extra)
    provider = _provider(cfg)
    model = cfg.director.openai_model if provider == "openai" else cfg.director.model
    key = make_key("director", PROMPT_VERSION, provider, model, system, prompt)
    raw = cache.get("director", key) if cache else None
    if raw is None:
        try:
            raw, model = _call(cfg, system, prompt)
        except Exception as exc:  # noqa: BLE001 - sem chave, API fora, JSON quebrado: seguem as regras
            log.warning("diretor de IA indisponível; seguem as regras do bot",
                        extra={"stage": "director", "error": str(exc)[-300:]})
            return None
        if not isinstance(raw, dict):
            log.warning("diretor de IA devolveu algo fora do formato", extra={"stage": "director"})
            return None
        if cache:
            cache.set("director", key, raw, ttl_seconds=30 * 86400)
    else:
        log.info("plano do diretor reaproveitado do cache", extra={"stage": "director"})
    return raw, model


def finish(asked: tuple[dict[str, Any], str] | None, sentences: list, lang: str = "en") -> DirectorPlan | None:
    """Resposta da IA -> plano validado (com o tempo das frases já sincronizado com a narração)."""
    if asked is None:
        return None
    raw, model = asked
    plan = validate(raw, sentences, lang)
    plan.model = model
    if not (plan.sections or plan.steps or plan.cards) and len(sentences) > 30:
        # nada aproveitável num roteiro longo: a resposta veio ruim (melhor as regras do que um vídeo sem gráfico)
        log.warning("plano do diretor vazio depois da validação; seguem as regras do bot",
                    extra={"stage": "director", "dropped": plan.dropped[:10]})
        return None
    log.info("plano do diretor de IA", extra={"stage": "director", **{k: v for k, v in plan.summary().items()
                                                                       if k != "cards"},
                                               "cards": len(plan.cards)})
    return plan


def strip_ai_styles(scenes: list) -> list[str]:
    """Com o diretor no comando, o gráfico de dado que a IA das cenas escolheu sai (quem decide card de
    dado, ranking e lista é o diretor); os decorativos ficam."""
    notes = []
    for sc in scenes:
        a = sc.analysis
        if a.style in DIRECTOR_KINDS or a.style in ("counter", "gauge", "specs", "price", "news"):
            notes.append(f"cena {sc.index}: {a.style} da IA das cenas saiu (o diretor decide os cards)")
            a.style, a.template_seconds = "normal", 0.0
    return notes


def apply_niche(cfg, plan: DirectorPlan) -> None:
    """Fase 2: o nicho é o do vídeo, não o do canal. Vídeo de carro continua com as checagens de carro (e ganha
    o vocabulário do assunto: "winter tire tread" também é imagem do nicho); outro nicho troca o "tem que ter
    carro" pelo que a IA disse que é imagem do assunto (e do que é fora dele)."""
    from . import visual_checks
    from .subject import is_automotive

    if plan.automotive:
        if not is_automotive(cfg.analysis.niche):
            cfg.analysis.niche = f"automotive ({plan.niche})"
        visual_checks.set_niche(plan.visual_niche, [])
    else:
        cfg.analysis.niche = plan.niche
        visual_checks.set_niche(plan.visual_niche, plan.visual_off)
    log.info("nicho do vídeo", extra={"stage": "director", "niche": cfg.analysis.niche,
                                      "imagens_do_nicho": plan.visual_niche[:4]})
