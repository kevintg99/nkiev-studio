"""Detector de templates: acha no roteiro as pistas claras que pedem um gráfico.

A IA barata (ou o modo sem IA) às vezes deixa como b-roll "normal" cenas que pedem
um template: uma sequência de anos, "de zero a cem em 9,2 segundos", "na tabela FIPE",
"os pontos fortes são...", "um inscrito comentou...". Aqui cada cena normal é lida
com regras simples e, quando a pista é inequívoca, recebe o template — com os dados
copiados da própria fala (nunca inventados, então já nasce coerente).

* Só mexe em cenas "normal" (a escolha da IA sempre vale).
* Se a cena tem duas pistas em frases diferentes (ficha técnica + 0–100), ela é
  dividida na fronteira das frases e cada parte ganha o seu template.
* Nunca repete o mesmo template em duas cenas seguidas.

As pistas existem em português, francês e polonês (``cues.py``); o idioma é o do roteiro.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .cues import C, language, set_language
from .log import get_logger
from .models import Scene, Sentence

log = get_logger("auto_templates")

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")


@dataclass
class Detection:
    kind: str
    first: int  # índice da 1ª frase da cena usada pelo template
    last: int
    priority: int
    fields: dict[str, Any] = field(default_factory=dict)


Detector = Callable[[list[str], str], "Detection | None"]


# ------------------------------------------------------------------ helpers
def _cap(text: str) -> str:
    text = text.strip(" ,;:-–—\"'")
    return text[:1].upper() + text[1:] if text else text


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def _value(tok: str) -> tuple[float, int]:
    tok = re.sub(r"[   ]", "", tok.strip())  # "89 900" (fr/pl)
    if re.fullmatch(r"\d{1,3}(,\d{3})+", tok) and (language() == "en" or tok.count(",") > 1):
        return float(tok.replace(",", "")), 0  # inglês: "12,000" = doze mil
    if "," in tok:
        whole, dec = tok.replace(".", "").split(",", 1)
        return float(f"{whole}.{dec}"), len(dec)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", tok):
        return float(tok.replace(".", "")), 0
    if "." in tok:
        return float(tok), len(tok.split(".", 1)[1])
    return float(tok), 0


def _list_items(text: str, max_words: int = 5) -> list[str]:
    """ "o consumo baixo e a revenda forte" -> ["Consumo baixo", "Revenda forte"]."""
    text = re.split(r"[.!?]", text, maxsplit=1)[0]
    out: list[str] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk or re.match(C("clause"), chunk, re.I):
            continue
        for part in re.split(C("sep_and"), chunk, flags=re.I):
            part = re.sub(C("article"), "", part.strip(), flags=re.I)
            words = part.split()
            if not words or len(words) > max_words:
                continue
            out.append(_cap(" ".join(words)))
    return out


def _span(ss: list[str], pattern: str) -> list[int]:
    return [i for i, s in enumerate(ss) if re.search(pattern, s, re.I)]


# ------------------------------------------------------------------ detectores
def timeline(ss: list[str], subject: str) -> Detection | None:
    hits = [(i, int(y)) for i, s in enumerate(ss) for y in YEAR_RE.findall(s)]
    years: list[int] = []
    idx: list[int] = []
    for i, y in hits:
        if not years or y > years[-1]:
            years.append(y)
            idx.append(i)
    if len(years) < 3:
        return None
    base = _query_name(subject)
    items = [{"label": str(y), "query": f"{base} {y}", "note": ""} for y in years[:6]]
    title = _display(subject) if subject else ""
    return Detection("timeline", idx[0], idx[min(5, len(idx) - 1)], 9, {"items": items, "caption": title})


def beforeafter(ss: list[str], subject: str) -> Detection | None:
    cue = _span(ss, C("beforeafter"))
    if not cue:
        return None
    i0 = cue[0]
    window = " ".join(ss[i0:i0 + 2])
    years = list(dict.fromkeys(YEAR_RE.findall(window)))
    if len(years) != 2:
        return None
    base = _query_name(subject)
    items = [{"label": y, "query": f"{base} {y}", "note": ""} for y in years]
    last = i0 + (1 if len(YEAR_RE.findall(ss[i0])) < 2 and i0 + 1 < len(ss) else 0)
    return Detection("beforeafter", i0, last, 7, {"items": items})


def speedometer(ss: list[str], subject: str) -> Detection | None:
    for i, s in enumerate(ss):
        m = re.search(C("accel"), s, re.I)
        if m:
            value, dec = _value(m.group(1))
            if 2 <= value <= 30:
                return Detection("speedometer", i, i, 8, {"number": {
                    "value": value, "decimals": max(1, dec), "suffix": "s", "label": C("accel_label")}})
        m = re.search(C("topspeed"), s, re.I)
        if m:
            return Detection("speedometer", i, i, 8, {"number": {
                "value": float(m.group(1)), "suffix": "km/h", "label": C("topspeed_label")}})
    return None


def datasheet(ss: list[str], subject: str) -> Detection | None:
    stats: list[dict[str, Any]] = []
    used: list[int] = []
    for label, pattern, unit in C("specs"):
        if any(st["label"] == label for st in stats):
            continue
        for i, s in enumerate(ss):
            if re.search(C("accel_guard"), s, re.I) and label != C("specs")[0][0]:
                continue  # a frase do 0–100 é do velocímetro
            m = re.search(pattern, s, re.I)
            if m:
                value = m.group(1)
                code = re.search(r"\b(cvt|dct|dsg|edc)\b", s, re.I)
                if code and label == C("specs")[-2][0]:  # câmbio: a sigla diz mais que "automático"
                    value = code.group(1)
                if value.lower() in ("cvt", "dct", "dsg", "edc"):
                    value = value.upper()
                value = re.sub(r"(automatyczn|manualn)\w*", r"\1a", value, flags=re.I)  # pl: forma do dicionário
                stats.append({"label": label, "value": value, "unit": unit, "bar": 0.0, "value_b": ""})
                used.append(i)
                break
    if len(stats) < 2:
        return None
    return Detection("datasheet", min(used), max(used), 6, {"stats": stats[:6], "caption": C("specs_title"),
                                                             "subtitle": _display(subject) if subject else ""})


THOUSAND_WORDS = r"mil|mille|tys\.?|tysi[aą]c\w*|k|grand|thousand"
MILLION_WORDS = r"milh[aãoõ]es|milh[aã]o|millions?|milion\w*"


def _money(num: str, mult: str | None) -> float:
    value, _ = _value(num)
    mult = (mult or "").lower()
    return value * (1000 if re.fullmatch(THOUSAND_WORDS, mult) else 1e6 if re.fullmatch(MILLION_WORDS, mult) else 1)


def _query_name(subject: str) -> str:
    from .subject import search_name

    return search_name(subject) if subject else "car"


def _display(subject: str) -> str:
    """ "toyota corolla" -> "Toyota Corolla"; nome já escrito com maiúsculas fica como está ("Lexus CT 200h")."""
    return subject if subject != subject.lower() else subject.title()


def _judge(ss: list[str], i: int) -> float:
    judge = " ".join(ss[i:i + 2]).lower()
    return 0.5 if re.search(C("fair"), judge) else 0.15 if re.search(C("cheap"), judge) else \
        0.85 if re.search(C("expensive"), judge) else -1.0


def price_range(ss: list[str], subject: str) -> Detection | None:
    """ "Les chiffres : entre 3 000 et 6 000 euros" -> etiqueta "3 000 – 6 000 €" (a moeda tem que ser dita)."""
    for i, s in enumerate(ss):
        m = re.search(C("price_range"), s, re.I)
        if not m:
            continue
        lo_mult = m.group(3) or m.group(6)  # "entre 50 e 70 mil": o "mil" vale para os dois
        lo, hi = _money(m.group(2), lo_mult), _money(m.group(5), m.group(6))
        if not 1000 <= lo < hi:
            continue
        level = _judge([s], 0)  # só a própria frase: a seguinte já costuma falar de outra coisa
        label = next((name for key, name in C("price_special") if re.search(key, s, re.I)), C("price_label"))
        prefix, suffix = C("currency")
        return Detection("price", i, i, 7, {"number": {
            "value": lo, "value_to": hi, "decimals": 0, "prefix": prefix, "suffix": suffix, "label": label,
            "level": level}, "caption": label, "subtitle": _display(subject) if subject else ""})
    return None


def price(ss: list[str], subject: str) -> Detection | None:
    ranged = price_range(ss, subject)
    if ranged is not None:
        return ranged
    for i, s in enumerate(ss):
        if not re.search(C("price_cue"), s, re.I):
            continue
        found = None
        for m in re.finditer(C("money"), s, re.I):
            if m.group(1) or m.group(4) or m.group(3):
                found = m
                break
        if found is None:
            continue
        value, _ = _value(found.group(2))
        mult = (found.group(3) or "").lower()
        value *= 1000 if re.fullmatch(THOUSAND_WORDS, mult) else 1e6 if re.fullmatch(MILLION_WORDS, mult) else 1
        if value < 1000:
            continue
        level = _judge(ss, i)
        last = i + 1 if level >= 0 and i + 1 < len(ss) and not re.search(C("judge_any"), s, re.I) else i
        label = next((name for key, name in C("price_special") if re.search(key, s, re.I)), C("price_label"))
        prefix, suffix = C("currency")
        return Detection("price", i, last, 7, {"number": {
            "value": value, "decimals": 0, "prefix": prefix, "suffix": suffix, "label": label, "level": level},
            "caption": label, "subtitle": _display(subject) if subject else ""})
    return None


def proscons(ss: list[str], subject: str) -> Detection | None:
    points: list[dict[str, Any]] = []
    used: list[int] = []
    for i, s in enumerate(ss):
        pos, neg = re.search(C("pos"), s, re.I), re.search(C("neg"), s, re.I)
        if pos and neg:
            if i + 1 < len(ss):
                used.append(i)  # "Vamos aos prós e contras." abre o template
            continue
        for m, positive in ((pos, True), (neg, False)):
            if not m:
                continue
            rest = re.sub(C("list_lead"), "", s[m.end():], flags=re.I)
            items = _list_items(rest)[:3]
            if items:
                points += [{"text": it, "positive": positive} for it in items]
                used.append(i)
    if not any(p["positive"] for p in points) or all(p["positive"] for p in points):
        return None
    return Detection("proscons", min(used), max(used), 8, {"points": points[:6]})


def score(ss: list[str], subject: str) -> Detection | None:
    idx = _span(ss, C("score_cue"))
    if not idx:
        return None
    stats: list[dict[str, Any]] = []
    final = None
    used = []
    for i in idx:
        s = ss[i]
        mf = re.search(C("score_final"), s, re.I)
        if mf:
            final = _value(next(g for g in mf.groups() if g))
            used.append(i)
        crit = r"\b(" + C("criteria") + r")\s*(?:" + C("score_word") + r"\s*)?[:=]?\s*(\d{1,2}(?:[.,]\d)?)\b"
        for m in re.finditer(crit, s, re.I):
            v, _ = _value(m.group(2))
            if v <= 10 and not any(st["label"].lower() == m.group(1).lower() for st in stats):
                stats.append({"label": _cap(m.group(1)), "value": m.group(2), "unit": "", "bar": 0.0, "value_b": ""})
                used.append(i)
    if len(stats) < 2:
        return None
    number = {"value": final[0], "decimals": final[1]} if final else {}
    return Detection("score", min(used), max(used), 8, {"stats": stats[:6], "number": number,
                                                         "caption": C("verdict")})


def magnifier(ss: list[str], subject: str) -> Detection | None:
    parts = C("parts")
    for i, s in enumerate(ss):
        m = re.search(C("look"), s, re.I)
        if not m:
            continue
        words = m.group(1).split()[:4]
        while words and words[-1].lower() in C("trail"):
            words.pop()
        if any(w.lower().strip(",.") in parts for w in words):
            return Detection("magnifier", i, i, 5, {"caption": _cap(" ".join(words))})
    return None


def news(ss: list[str], subject: str) -> Detection | None:
    for i, s in enumerate(ss):
        if not re.search(C("news"), s, re.I):
            continue
        body = s
        head, sep, tail = s.partition(":")
        if sep and len(head.split()) <= 6 and len(tail.split()) >= 4:
            body = tail
        body = re.sub(C("lead_and"), "", body.strip(), flags=re.I)
        body = _shorten(_cap(body.rstrip(".!")), 120)
        hl = re.search(C("first_world"), body, re.I)
        return Detection("news", i, i, 6, {"body": body, "highlights": [hl.group(1)] if hl else []})
    return None


def comment(ss: list[str], subject: str) -> Detection | None:
    for i, s in enumerate(ss):
        m = re.search(C("comment"), s, re.I)
        if m and len(m.group(1).split()) >= 4:
            body = _shorten(_cap(m.group(1).strip().strip('"“”«»„')), 140)
            return Detection("comment", i, i, 8, {"body": body})
    return None


def poll(ss: list[str], subject: str) -> Detection | None:
    for i, s in enumerate(ss):
        m = re.search(C("poll"), s, re.I)
        if not m:
            continue
        rest = s[m.end():]
        opts_txt = re.split(r"\?", rest.lstrip(" :,"), maxsplit=1)[0]
        options = [o for o in _list_items(opts_txt, 3) if o]
        if len(options) < 2:
            continue
        question = _cap(s[:m.end()].strip()) + "?"
        base = (subject or "").lower()
        options = [nominative(o) for o in options]
        items = [{"label": o, "query": base if o.lower() in base else f"{o} car", "note": ""} for o in options[:4]]
        return Detection("poll", i, i, 8, {"caption": question, "items": items})
    return None


def nominative(name: str) -> str:
    """Polonês declina nomes: "Corollę", "Civica", "Octavię" -> "Corolla", "Civic", "Octavia"."""
    from .cues import language

    if language() != "pl" or " " in name:
        return name
    if name.endswith("ę"):  # feminino no acusativo: Corollę -> Corolla
        return name[:-1] + "a"
    # masculino no genitivo/acusativo: Civica, Golfa -> Civic, Golf
    return re.sub(r"(?<=[bcdfghjklmnprstvwxz])a$", "", name) if len(name) > 4 else name


def chapter(ss: list[str], subject: str) -> Detection | None:
    if len(ss) != 1:
        return None
    m = re.match(C("chapter"), ss[0], re.I)
    if not m:
        return None
    caption = re.sub(C("article"), "", m.group(1).strip(), flags=re.I)
    if len(caption.split()) > 4:
        return None
    return Detection("chapter", 0, 0, 4, {"caption": _cap(caption)})


def ranking(ss: list[str], subject: str) -> Detection | None:
    """ "Numéro 4 : la Mazda 6, en version 2.0 essence Skyactiv." -> #4 gigante + foto + nome."""
    from .subject import rank_header

    head = rank_header(ss[0]) if ss else None
    if head is None:
        return None
    n, name, rest = head
    return Detection("ranking", 0, 0, 5, {"rank": n, "caption": name,
                                          "subtitle": _shorten(_cap(rest), 60) if rest else ""})


def headline(ss: list[str], subject: str) -> Detection | None:
    """ "Toyota Corolla: o sedã que atravessou gerações." -> título + subtítulo."""
    if len(ss) != 1 or len(ss[0].split()) > 12:
        return None
    head, sep, tail = ss[0].partition(":")
    if not sep or not 1 <= len(head.split()) <= 4 or not tail.strip():
        return None
    return Detection("headline", 0, 0, 3, {"caption": _cap(head), "subtitle": _shorten(tail.strip().rstrip(".!"), 60)})


DETECTORS: tuple[Detector, ...] = (timeline, proscons, score, comment, poll, speedometer, price, beforeafter,
                                   datasheet, news, magnifier, ranking, chapter, headline)


def _magnet_detectors() -> tuple[Detector, ...]:
    from .magnet_detect import MAGNET_DETECTORS
    from .story_detect import STORY_DETECTORS

    return (*MAGNET_DETECTORS, *STORY_DETECTORS)


# templates de dado da edição magnética: um a cada ``MAGNET_GAP`` s no máximo (senão o vídeo vira só gráfico)
MAGNET_DATA = ("percentbar", "peoplegrid", "receipt", "downtime", "warning",
               # reportagem (templates_story.py): fonte, de X para Y, garantia, passo a passo, não confirmado,
               # desmontagem
               "source", "fromto", "coverage", "steps", "unverified", "exploded")
MAGNET_GAP = 12.0
# alguns não podem se repetir tanto (a fonte a cada frase que cita a UBS, a desmontagem em todo "teardown")
KIND_GAP = {"source": 30.0, "unverified": 45.0, "exploded": 90.0, "fromto": 20.0, "news": 45.0}
# o passo a passo vem em sequência ("One." / "Two."...): não espera o intervalo dos gráficos nem evita repetir
SEQUENCE_KINDS = ("steps",)


# ------------------------------------------------------------------ aplicação
def _sentences_of(scene: Scene, sentences: list[Sentence]) -> list[Sentence]:
    return [s for s in sentences if scene.start - 0.05 <= (s.start + s.end) / 2 <= scene.end + 0.05]


def _apply(scene: Scene, det: Detection) -> None:
    a = scene.analysis
    a.style = det.kind
    a.template_seconds = 0.0
    for key, value in det.fields.items():
        setattr(a, key, copy.deepcopy(value))
    if det.kind in ("proscons", "score", "datasheet", "speedometer", "price", "poll", "comment", "news", "timeline",
                    "beforeafter", "magnifier", "chapter", "ranking", *MAGNET_DATA):
        a.overlay = {}  # o gráfico já é o destaque da cena


# aviso legal do fim ("This video is based on publicly available...", "Your results will vary"): não é notícia
_DISCLAIMER = re.compile(r"\b(?:this\s+video\s+is\s+based\s+on|at\s+the\s+time\s+of\s+recording|"
                         r"your\s+results\s+will\s+vary|nothing\s+here\s+replaces|vary\s+by\s+model|"
                         r"vary\s+by\s+market|individual\s+vehicles\s+vary|check\s+current\s+recall|"
                         r"cette\s+vidéo\s+(?:est\s+)?bas[ée]e\s+sur|au\s+moment\s+de\s+l['’]enregistrement|"
                         r"este\s+v[íi]deo\s+(?:é\s+)?baseado\s+em|no\s+momento\s+da\s+grava[çc][ãa]o)", re.I)


def detect(sents: list[str], subject: str, avoid: set[str] | None = None) -> list[Detection]:
    """Pistas de uma cena, sem sobreposição de frases (a de maior prioridade vence)."""
    # o aviso legal do fim e o que vem depois dele na mesma cena não viram notícia/fonte
    legal = next((k for k, t in enumerate(sents) if _DISCLAIMER.search(t)), len(sents))
    from . import director

    if director.active():
        # o diretor de IA decidiu os cards e as listas; o cabeçalho do ranking também é dele (via rank_header)
        found = [d for d in (ranking(sents, subject), *director.detections(sents))
                 if d is not None and d.kind not in (avoid or set()) - {"steps"} and min(d.last, len(sents) - 1) < legal]
        return _no_overlap(found)
    found = [d for fn in (*DETECTORS, *_magnet_detectors())
             if (d := fn(sents, subject)) is not None and d.kind not in (avoid or set())
             and min(d.last, len(sents) - 1) < legal]
    return _no_overlap(found)


def _no_overlap(found: list[Detection]) -> list[Detection]:
    chosen: list[Detection] = []
    for d in sorted(found, key=lambda d: -d.priority):
        if all(d.last < c.first or d.first > c.last for c in chosen):
            chosen.append(d)
    return sorted(chosen, key=lambda d: d.first)


# pistas que costumam se espalhar por várias frases (e a IA às vezes põe uma frase por cena)
def _coverage(ss: list[str], subject: str) -> "Detection | None":
    from .story_detect import coverage

    return coverage(ss, subject)


CROSS_DETECTORS: tuple[Detector, ...] = (timeline, proscons, price, score, _coverage)
# o que pode ser absorvido por um template maior: b-roll, títulos e efeitos decorativos, ou pedaços
# do mesmo template (a IA fez a linha do tempo só com 2 dos 5 anos)
DECORATIVE = {"normal", "headline", "chapter", "card", "vhs", "hud", "mosaic", "quote", "scrapbook", "showcase",
              "gallery", "sketch", "dossier", "statement", "bulletin", "report", "videowall",
              "paperfloat", "paperpair", "paperstatement", "newsfloat",
              "textmask", "photostack", "splitpanels", "annotate", "wordpop"}
ABSORBS = {"steps": {"checklist", "bulletin", "quote", "news", "comment"},
           "coverage": {"specs", "datasheet", "checklist", "stat", "counter", "chart", "price"},
           # o defeito principal falado ("the biggest complaint was the infotainment, at 24%") vence a lista da IA
           "warning": {"checklist", "stat", "counter", "chart"},
           "proscons": {"checklist"}, "price": {"stat", "counter"}, "timeline": {"comparison", "triptych"},
           "score": {"chart", "specs"}, "speedometer": {"stat", "gauge"},
           # o número da fala num gráfico que diz o que ele é (conta da oficina, barra, gente, dias parado)
           "receipt": {"price", "stat", "counter"}, "percentbar": {"stat", "gauge", "counter"},
           "peoplegrid": {"stat", "counter"}, "downtime": {"stat", "counter"}}


def _director_on() -> bool:
    from . import director

    return director.active()


def _mergeable(style: str, kind: str) -> bool:
    return style in DECORATIVE or style == kind or style in ABSORBS.get(kind, set())


def merge_across_scenes(scenes: list[Scene], sentences: list[Sentence], subject: str = "",
                        max_seconds: float = 30.0) -> tuple[list[Scene], list[str]]:
    """Junta cenas vizinhas quando uma pista atravessa as fronteiras: "Em 1966…" / "Em 1983…" /
    "Em 2002…" em cenas separadas viram UMA linha do tempo; "pontos fortes…" + "pontos fracos…"
    viram um prós e contras; "130 mil reais" + "é um preço justo" viram a etiqueta com o medidor.
    As frases que sobram antes/depois do trecho continuam como cenas próprias."""
    out: list[Scene] = []
    notes: list[str] = []
    i = 0
    while i < len(scenes):
        best: tuple[int, Detection, list[Sentence], list[int]] | None = None
        for j in range(i + 1, min(len(scenes), i + 7)):
            if scenes[j].end - scenes[i].start > max_seconds:
                break
            sents: list[Sentence] = []
            owner: list[int] = []
            for k in range(i, j + 1):
                ss = _sentences_of(scenes[k], sentences)
                sents += ss
                owner += [k] * len(ss)
            if not sents:
                continue
            texts = [s.text for s in sents]
            from . import director

            for fn in (director.cross_detection,) if director.active() else CROSS_DETECTORS:
                d = fn(texts, scenes[i].analysis.subject or subject)
                if d is None or owner[d.first] != i or owner[d.last] == i:
                    continue  # tem que começar nesta cena e atravessar para a seguinte
                spanned = range(owner[d.first], owner[d.last] + 1)
                if not all(_mergeable(scenes[k].analysis.style, d.kind) for k in spanned):
                    continue
                if best is None or owner[d.last] >= best[3][best[1].last]:
                    best = (owner[d.last], d, sents, owner)
        if best is None:
            out.append(scenes[i])
            i += 1
            continue
        last_scene, d, sents, owner = best
        first_sc, last_sc = scenes[i], scenes[last_scene]
        pre = [s for n, s in enumerate(sents) if n < d.first and owner[n] == i]
        post = [s for n, s in enumerate(sents) if n > d.last and owner[n] == last_scene]
        core = sents[d.first:d.last + 1]
        if pre:
            out.append(Scene(0, " ".join(s.text for s in pre), first_sc.start, core[0].start,
                             copy.deepcopy(first_sc.analysis)))
            out[-1].analysis.style, out[-1].analysis.template_seconds = "normal", 0.0
        merged = copy.deepcopy(first_sc.analysis)
        queries = [q for k in range(i, last_scene + 1) for q in scenes[k].analysis.queries]
        merged.queries = list(dict.fromkeys(queries))[:4]
        t0 = core[0].start if pre else first_sc.start
        t1 = post[0].start if post else last_sc.end
        part = Scene(0, " ".join(s.text for s in core), t0, t1, merged)
        _apply(part, d)
        out.append(part)
        if post:
            out.append(Scene(0, " ".join(s.text for s in post), post[0].start, last_sc.end,
                             copy.deepcopy(last_sc.analysis)))
            out[-1].analysis.style, out[-1].analysis.template_seconds = "normal", 0.0
        notes.append(f"cenas {i}-{last_scene}: juntadas num só {d.kind} (a pista atravessava as cenas)")
        i = last_scene + 1
    for k, scene in enumerate(out):
        scene.index = k
    return out, notes


def regroup_steps(scenes: list[Scene], sentences: list[Sentence]) -> tuple[list[Scene], list[str]]:
    """Cada dica numerada ("One." / "Two."...) começa uma cena, junto com a frase da dica. A IA às vezes
    deixa o número no fim da cena anterior ("…after a knock. Three.") ou no meio de outra cena."""
    from .story_detect import _STEPS, step_of

    if not _STEPS:
        return scenes, []
    out, notes = list(scenes), []
    sents = sorted(sentences, key=lambda x: x.start)
    for k, s in enumerate(sents):
        if step_of(s.text, sents[k + 1].text if k + 1 < len(sents) else None) is None:
            continue
        mid = (s.start + s.end) / 2
        idx = next((i for i, sc in enumerate(out) if sc.start - 0.05 <= mid <= sc.end + 0.05), None)
        if idx is None:
            continue
        sc = out[idx]
        own = _sentences_of(sc, sents)
        nxt = sents[k + 1] if k + 1 < len(sents) else None
        tip_here = nxt is not None and nxt in own
        if not tip_here and idx + 1 < len(out) and own and own[-1] is s:
            # o número ficou no fim da cena (ou numa cena só dele): passa para a cena da dica
            nb = out[idx + 1]
            if len(own) == 1:
                out[idx:idx + 2] = [Scene(0, f"{s.text} {nb.text}", sc.start, nb.end, nb.analysis)]
            else:
                out[idx] = Scene(0, " ".join(x.text for x in own[:-1]), sc.start, s.start, sc.analysis)
                out[idx + 1] = Scene(0, f"{s.text} {nb.text}", s.start, nb.end, nb.analysis)
            notes.append(f"'{s.text.strip()}' passou para a cena da dica")
        elif tip_here and own and own[0] is not s:
            # o número no meio da cena: corta antes dele (a dica ganha a cena dela)
            before = [x for x in own if x.end <= s.start + 0.01]
            after = [x for x in own if x.start >= s.start - 0.01]
            if not before or not after:
                continue
            tail = copy.deepcopy(sc.analysis)
            tail.style, tail.template_seconds = "normal", 0.0
            out[idx:idx + 1] = [Scene(0, " ".join(x.text for x in before), sc.start, s.start, sc.analysis),
                                Scene(0, " ".join(x.text for x in after), s.start, sc.end, tail)]
            notes.append(f"cena cortada antes de '{s.text.strip()}'")
    for i, sc in enumerate(out):
        sc.index = i
    return out, notes


def auto_templates(scenes: list[Scene], sentences: list[Sentence], subject: str = "",
                   min_part_seconds: float = 3.0, lang: str = "pt") -> tuple[list[Scene], list[str]]:
    """Aplica os templates detectados nas cenas "normal". Devolve as cenas (algumas
    divididas) e a lista do que foi feito (vai para o report)."""
    from .story_detect import prime_steps

    set_language(lang)
    prime_steps([s.text for s in sentences])
    scenes, step_notes = regroup_steps(scenes, sentences)
    scenes, notes = merge_across_scenes(scenes, sentences, subject)
    notes = step_notes + notes
    from .plan_fix import closing_headers

    dead = closing_headers(sentences)  # "Number three." que só fecha a seção: não conta como cabeçalho
    out: list[Scene] = []
    last_magnet = -1e9
    last_kind: dict[str, float] = {}
    for scene in scenes:
        style = scene.analysis.style
        sents = _sentences_of(scene, sentences)
        if any(s.index in dead for s in sents):  # "Number two. And number one. Costco.": vale o "number one"
            sents = [s for s in sents if s.index not in dead]
            if not sents:  # a cena é só o "Number three." de fechamento: b-roll (e não segura o próximo cartão)
                if style in ("ranking", "newsrank", "paperrank"):
                    scene.analysis.style = "normal"
                out.append(scene)
                continue
        texts = [s.text for s in sents] or [scene.text]
        prev_style = out[-1].analysis.style if out else ""
        avoid = {prev_style} - set(SEQUENCE_KINDS) if prev_style != "normal" else set()
        dets = detect(texts, scene.analysis.subject or subject, avoid=avoid or None)

        def said_at(d: Detection, sents=sents, scene=scene) -> float:
            return sents[d.first].start if d.first < len(sents) else scene.start

        # o intervalo entre gráficos conta da FRASE do número, não do começo da cena (a IA às vezes junta
        # três parágrafos numa cena só e o gráfico do terceiro sumia)
        dets = [d for d in dets if d.kind in SEQUENCE_KINDS
                or (said_at(d) - last_kind.get(d.kind, -1e9) >= KIND_GAP.get(d.kind, 0.0)  # notícia também
                    and (d.kind not in MAGNET_DATA or said_at(d) - last_magnet >= MAGNET_GAP))]
        if style != "normal":
            # gráfico de dado só no lugar de um template decorativo da IA ou de um número mais pobre
            # ("Reliability rating, 76.4%" com um card da IA vira a barra de porcentagem)
            # o passo de uma lista numerada ("Round eight. Ice.") vale mesmo sobre o template da IA: senão a
            # sequência fica sem um dos números (o 8 de 8 sumiu no vídeo dos pneus)
            dets = [d for d in dets if d.kind not in MAGNET_DATA or d.kind in SEQUENCE_KINDS
                    or _mergeable(style, d.kind) or _director_on()]
        if any(d.kind in MAGNET_DATA for d in dets):
            magnets = [d for d in dets if d.kind in MAGNET_DATA]
            # um gráfico de dado por trecho: dois na mesma cena só se bem espaçados (a cena é dividida)
            kept: list[Detection] = []
            for d in sorted(magnets, key=said_at):
                if not kept or said_at(d) - said_at(kept[-1]) >= MAGNET_GAP or d.kind in SEQUENCE_KINDS:
                    kept.append(d)
            dets = [d for d in dets if d.kind not in MAGNET_DATA] + kept
            dets.sort(key=lambda d: d.first)
        if style != "normal":
            # a escolha da IA vale, a não ser que seja decorativa ou uma versão mais pobre do mesmo
            # gráfico (um "stat" para o 0–100 vira velocímetro; um checklist vira prós e contras)
            from . import director

            # com o diretor de IA, o card dele vale sobre qualquer template da IA das cenas
            better = [d for d in dets if style != d.kind and (_mergeable(style, d.kind) or d.kind in SEQUENCE_KINDS
                                                               or director.active())]
            # preço da IA com um valor só, mas a fala dá a faixa ("entre 3 000 et 6 000 euros")
            better += [d for d in dets if style == d.kind == "price" and d.fields["number"].get("value_to")
                       and not (scene.analysis.number or {}).get("value_to")]
            seq = [d for d in better if d.kind in SEQUENCE_KINDS]
            if (seq or len(dets) == 1 and better) and style not in ("normal",):
                pick = seq[0] if seq else better[0]
                _apply(scene, pick)
                if pick.kind in MAGNET_DATA and pick.kind not in SEQUENCE_KINDS:
                    last_magnet = last_kind[pick.kind] = said_at(pick)
                notes.append(f"cena {scene.index}: {style} da IA trocado por {pick.kind}")
            out.append(scene)
            continue
        if not dets:
            out.append(scene)
            continue
        for d in dets:
            if d.kind in MAGNET_DATA:
                if d.kind not in SEQUENCE_KINDS:  # o passo a passo não segura o próximo gráfico
                    last_magnet = max(last_magnet, said_at(d))
                last_kind[d.kind] = said_at(d)
            elif d.kind in KIND_GAP:  # "news": um cartão de notícia a cada 45 s, no máximo
                last_kind[d.kind] = said_at(d)
        d0 = dets[0]
        if len(dets) == 1 and d0.kind in MAGNET_DATA and 0 < d0.first < len(sents) \
                and sents[d0.first].start - scene.start >= min_part_seconds:
            # o gráfico entra quando o número é falado: b-roll antes, o template a partir da frase
            cut = sents[d0.first].start
            before = Scene(0, " ".join(s.text for s in sents[:d0.first]), scene.start, cut,
                           copy.deepcopy(scene.analysis))
            part = Scene(0, " ".join(s.text for s in sents[d0.first:]), cut, scene.end, copy.deepcopy(scene.analysis))
            _apply(part, d0)
            out += [before, part]
            notes.append(f"cena {scene.index}: {d0.kind} detectado na fala (entra na frase do número)")
            continue
        if len(dets) == 1 or len(sents) < 2:
            _apply(scene, dets[0])
            out.append(scene)
            notes.append(f"cena {scene.index}: {dets[0].kind} detectado na fala")
            continue
        # várias pistas em frases diferentes: uma parte da cena para cada template
        starts = [0] + [d.first for d in dets[1:]]
        parts: list[tuple[int, int, Detection]] = []
        for k, d in enumerate(dets):
            a0, a1 = starts[k], (starts[k + 1] - 1 if k + 1 < len(dets) else len(sents) - 1)
            t0 = scene.start if a0 == 0 else sents[a0].start
            t1 = scene.end if a1 == len(sents) - 1 else sents[a1 + 1].start
            if parts and t1 - t0 < min_part_seconds:
                continue  # parte curta demais: fica com a anterior
            parts.append((a0, a1, d))
        for k, (a0, a1, d) in enumerate(parts):
            a1 = parts[k + 1][0] - 1 if k + 1 < len(parts) else len(sents) - 1
            t0 = scene.start if k == 0 else sents[a0].start
            t1 = scene.end if k == len(parts) - 1 else sents[a1 + 1].start
            part = Scene(0, " ".join(s.text for s in sents[a0:a1 + 1]), t0, t1, copy.deepcopy(scene.analysis))
            _apply(part, d)
            out.append(part)
        notes.append(f"cena {scene.index}: dividida em {len(parts)} ({', '.join(p[2].kind for p in parts)})")
    for i, scene in enumerate(out):
        scene.index = i
    if notes:
        log.info("templates detectados no roteiro", extra={"stage": "auto_templates", "detalhes": notes})
    return out, notes
