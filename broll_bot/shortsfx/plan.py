"""Onde entra cada efeito do vídeo curto: tudo sai da fala (palavras com tempo) e da linha do tempo dos shots.

Regras de ouro (o que os editores de Shorts fazem e o que segura a pessoa):

* nunca passar ~3 s sem novidade na tela (corte, template, efeito): o que sobrar vira um zoom lento ou um soco;
* o efeito cai no tempo da palavra que ele mostra (o soco no "NUNCA", o contador no "300 km/h");
* efeito grande (círculo, lupa, congelamento, preto e branco, contador) tem espaço entre um e outro: se repete
  demais, vira ruído e cansa;
* em cima de template (que já é um gráfico na tela inteira) não entra nada, só a ênfase da legenda.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

Cue = tuple[float, str, float]

MAX_GAP = 3.0  # segundos sem novidade na tela, no máximo
BIG = ("circle", "magnify", "freeze", "mono", "count", "stack")  # efeitos que pedem espaço entre si
DRAWN = ("circle", "magnify", "sticker", "count", "teaser", "hook", "freeze", "cta")  # o que desenha por cima
CAMERA = ("punch", "drift", "shake")


@dataclass
class FxEvent:
    t0: float
    t1: float
    kind: str  # hook teaser punch drift shake circle magnify freeze mono stack sticker count loop cta
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"t0": round(self.t0, 3), "t1": round(self.t1, 3), "kind": self.kind, **self.data}


@dataclass
class Segment:
    """Um shot na linha do tempo (segundos da narração)."""

    t0: float
    t1: float
    template: bool = False
    overlay: bool = False
    avatar: bool = False
    effect: str = ""  # efeito de corte na entrada (whip, punch...): o soco do corte já está lá

    @property
    def clean(self) -> bool:  # b-roll puro: pode receber desenho por cima
        return not (self.template or self.overlay or self.avatar)


@dataclass
class FxPlan:
    events: list[FxEvent]
    emphasis: list[float]  # início das palavras em destaque (a legenda muda de cor e cresce nelas)
    total: float
    segments: list[Segment] = field(default_factory=list)

    def of(self, kind: str) -> list[FxEvent]:
        return [e for e in self.events if e.kind == kind]

    def cues(self) -> list[Cue]:
        out: list[Cue] = []
        for e in self.events:
            out += _cues(e)
        return sorted(out)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.kind] = counts.get(e.kind, 0) + 1
        hook = next((e.data.get("text", "") for e in self.events if e.kind == "hook"), "")
        return {"events": counts, "emphasis": len(self.emphasis), "hook": hook,
                "max_gap": round(max_gap(self, self.segments), 2)}


def _cues(e: FxEvent) -> list[Cue]:
    t = e.t0
    k = e.kind
    if k == "hook":
        out: list[Cue] = [(0.0, "bass", 0.7)]
        if e.data.get("stamp"):
            out.append((e.data["stamp_at"], "stamp", 0.7))
        return out
    if k == "teaser":
        return [(t, "whoosh_soft", 0.4), (t + 0.12, "pop", 0.35)]
    if k == "punch":
        return [(t, "impact_soft", 0.42)]
    if k == "shake":
        return [(t, "impact", 0.5)]
    if k == "circle":
        return [(t, "pencil:0.45", 0.5)]
    if k == "magnify":
        return [(t, "whoosh_soft", 0.4), (t + 0.12, "pop", 0.35)]
    if k == "freeze":
        return [(t, "snap", 0.65), (t + 0.04, "impact_soft", 0.4)]
    if k == "mono":
        return [(t, "scratch", 0.55)]
    if k == "stack":
        return [(max(0.0, t - 0.12), "whoosh", 0.42)]
    if k == "sticker":
        return [(t + 0.02, "pop", 0.5)]
    if k == "count":
        return [(t + 0.08 * i, "tick", 0.22) for i in range(8)] + [(t + 0.72, "ding", 0.38)]
    if k == "cta":
        return [(t, "whoosh_soft", 0.4)]
    return []


# ------------------------------------------------------------------------------------------- palavras
def _set(text: str) -> frozenset[str]:
    return frozenset(text.split())


STRONG = {
    "en": _set("never only nobody everyone always zero double triple half secret banned illegal free dead deadly "
               "insane crazy shocking huge massive record million millions billion billions warning mistake truth "
               "worst best biggest first last impossible forbidden"),
    "pt": _set("nunca único única ninguém todos sempre zero dobro triplo metade segredo proibido proibida ilegal "
               "grátis morte morto absurdo absurda chocante enorme recorde milhão milhões bilhão bilhões cuidado "
               "erro verdade pior melhor maior primeiro primeira último última impossível"),
    "es": _set("nunca único única nadie todos siempre cero doble triple mitad secreto prohibido ilegal gratis muerte "
               "absurdo enorme récord millón millones cuidado error verdad peor mejor mayor primero último imposible"),
    "fr": _set("jamais seul seule personne toujours zéro double triple moitié secret interdit illégal gratuit mort "
               "énorme record million millions milliard attention erreur vérité pire meilleur premier dernier "
               "impossible"),
    "pl": _set("nigdy jedyny nikt wszyscy zawsze zero podwójnie połowa sekret zakazany nielegalny darmowy śmierć "
               "ogromny rekord milion miliony miliard uwaga błąd prawda najgorszy najlepszy pierwszy ostatni "
               "niemożliwe"),
}
_EST_SKIP = _set("interest honest forest suggest request test latest contest protest modest invest harvest digest")

CATEGORIES: dict[str, dict[str, frozenset[str]]] = {
    "money": {"en": _set("price prices cost costs dollars dollar cash money expensive cheap cheapest pay paid fee tax "
                         "taxes rebate budget profit"),
              "pt": _set("preço preços custa custo dólares reais dinheiro caro cara barato barata pagar paga imposto "
                         "lucro"),
              "es": _set("precio cuesta costo dólares dinero caro barato pagar impuesto"),
              "fr": _set("prix coûte coût dollars euros argent cher chère payer impôt"),
              "pl": _set("cena kosztuje koszt złotych pieniądze drogi drogo tani tanio płacić podatek")},
    "speed": {"en": _set("fast fastest speed mph quick quickest"),
              "pt": _set("rápido rápida velocidade veloz"), "es": _set("rápido velocidad"),
              "fr": _set("rapide vitesse"), "pl": _set("szybki szybko prędkość")},
    "danger": {"en": _set("danger dangerous warning crash crashes recall fire risk risky problem problems broken scam"),
               "pt": _set("perigo perigoso perigosa cuidado acidente recall risco problema problemas quebrado golpe"),
               "es": _set("peligro peligroso accidente riesgo problema estafa"),
               "fr": _set("danger dangereux accident risque problème arnaque"),
               "pl": _set("niebezpieczeństwo niebezpieczny wypadek ryzyko problem oszustwo")},
    "record": {"en": _set("record best winner win wins champion top"),
               "pt": _set("recorde melhor vencedor campeão ganhou"), "es": _set("récord mejor ganador campeón"),
               "fr": _set("record meilleur gagnant champion"), "pl": _set("rekord najlepszy zwycięzca mistrz")},
    "time": {"en": _set("years hours minutes days weeks months decades"),
             "pt": _set("anos horas minutos dias semanas meses décadas"),
             "es": _set("años horas minutos días semanas meses"), "fr": _set("ans heures minutes jours semaines mois"),
             "pl": _set("lat lata godzin minut dni tygodni miesięcy")},
    "fire": {"en": _set("viral popular hottest demand sold"), "pt": _set("viral sucesso esgotado febre"),
             "es": _set("viral éxito agotado"), "fr": _set("viral succès épuisé"), "pl": _set("hit wyprzedany")},
    "up": {"en": _set("rise rising rose increase increased grew growth soared jumped doubled"),
           "pt": _set("subiu sobe aumentou aumento cresceu dobrou disparou"), "es": _set("subió aumentó creció"),
           "fr": _set("hausse augmenté grimpé doublé"), "pl": _set("wzrost wzrosła podwoiła")},
    "down": {"en": _set("drop dropped fell fall falling decline lost crashed plunged"),
             "pt": _set("caiu queda diminuiu perdeu despencou"), "es": _set("cayó caída bajó perdió"),
             "fr": _set("chute baisse perdu"), "pl": _set("spadek spadła straciła")},
    "check": {"en": _set("works approved safe yes"), "pt": _set("funciona aprovado seguro sim"),
              "es": _set("funciona aprobado seguro"), "fr": _set("marche approuvé sûr"),
              "pl": _set("działa zatwierdzony bezpieczny")},
    "cross": {"en": _set("banned illegal wrong fails failed rejected"),
              "pt": _set("proibido proibida ilegal errado falhou reprovado"),
              "es": _set("prohibido ilegal equivocado falló"), "fr": _set("interdit illégal faux raté"),
              "pl": _set("zakazany nielegalny błędny")},
}
CONTRAST = {"en": _set("but however except although yet"), "pt": _set("mas porém contudo entretanto"),
            "es": _set("pero embargo aunque"), "fr": _set("mais pourtant cependant"), "pl": _set("ale jednak")}
CLAUSE = _set("that which who because when where while so que porque quando onde enquanto então qui parce quand "
              "où cuando donde który która które bo kiedy gdzie")
CURRENCY_WORDS = _set("dollars dollar bucks reais real euros euro pounds złotych zł pesos")
PERCENT_WORDS = _set("percent per cento por ciento pour %")
SPEED_UNITS = {"km/h": "km/h", "kmh": "km/h", "mph": "mph", "kilometres": "km/h", "kilometers": "km/h",
               "quilômetros": "km/h", "kilómetros": "km/h", "miles": "mph"}
YEAR = re.compile(r"^(1[89]\d\d|20\d\d)s?$")


def norm(text: str) -> str:
    return re.sub(r"^[^\w$€£%]+|[^\w%]+$", "", (text or "").lower())


def _lang(lang: str) -> str:
    lang = (lang or "en").split("-")[0].lower()
    return lang if lang in STRONG else "en"


def category(word: str, lang: str) -> str:
    w = norm(word)
    if re.match(r"^(r\$|\$|€|£|zł)", (word or "").strip().lower()):
        return "money"
    for name, table in CATEGORIES.items():
        if w in table.get(_lang(lang), ()):
            return name
    return ""


def is_strong(word: str, lang: str) -> bool:
    w = norm(word)
    if w in STRONG[_lang(lang)]:
        return True
    return _lang(lang) == "en" and len(w) >= 6 and w.endswith("est") and w not in _EST_SKIP


@dataclass
class Number:
    value: float
    decimals: int
    prefix: str
    suffix: str
    kind: str  # money | percent | speed | number


def read_number(words: list, i: int, lang: str, currency: str = "") -> Number | None:
    """O número dito na palavra ``i`` (só algarismos: "300", "$45,000", "75%"), com a unidade das palavras do lado."""
    from ..motion import gfx

    raw = (words[i].text or "").strip()
    if not re.search(r"\d", raw) or YEAR.match(norm(raw)):
        return None
    gfx.set_number_locale(lang)  # "45,000" em inglês é milhar; "4,5" em português é decimal
    got = gfx.parse_number(raw)
    if got is None:
        return None
    value, dec = got
    nxt = " ".join(norm(w.text) for w in words[i + 1:i + 3])
    first_next = nxt.split(" ")[0] if nxt else ""
    m = re.match(r"^(r\$|\$|€|£)", raw.lower())
    if m or first_next in CURRENCY_WORDS:
        sym = {"r$": "R$", "$": "$", "€": "€", "£": "£"}.get(m.group(1) if m else "", "") or currency or "$"
        mult = {"thousand": 1e3, "mil": 1e3, "million": 1e6, "milhões": 1e6, "milhão": 1e6, "billion": 1e9}
        if first_next in mult and value < 1000:
            value *= mult[first_next]
            dec = 0
        return Number(value, dec if value < 1000 else 0, sym, "", "money")
    if raw.endswith("%") or first_next in PERCENT_WORDS:
        return Number(value, dec, "", "%", "percent")
    unit = SPEED_UNITS.get(first_next) or SPEED_UNITS.get(norm(raw).lstrip("0123456789.,"))
    if unit:
        return Number(value, dec, "", f" {unit}", "speed")
    if value < 10:
        return None
    return Number(value, dec, "", "", "number")


def _sentence_starts(words: list) -> set[int]:
    out = {0}
    for k in range(1, len(words)):
        prev = (words[k - 1].text or "").strip()
        if re.search(r"[.!?…:]$", prev) or words[k].start - words[k - 1].end > 0.35:
            out.add(k)
    return out


# --------------------------------------------------------------------------------------------- plano
def segments_from_shots(shots: Iterable) -> list[Segment]:
    return [Segment(float(s.cut_in), float(s.cut_out), bool(s.template), bool(s.overlay), bool(s.avatar),
                    str(s.effect_in or "")) for s in shots]


def _seg_at(segments: list[Segment], t: float) -> Segment | None:
    for s in segments:
        if s.t0 - 1e-6 <= t < s.t1:
            return s
    return segments[-1] if segments and t >= segments[-1].t1 else None


def _busy(segments: list[Segment], a: float, b: float) -> bool:
    """Algum template na tela entre ``a`` e ``b``?"""
    return any(s.template and s.t0 < b and a < s.t1 for s in segments)


def hook_text(hook: str, title: str, words: list, max_words: int = 8) -> str:
    """O texto do gancho: o da IA; senão a 1ª frase se for curta, ou o 1º pedaço dela (até a vírgula ou antes de
    "that/que/porque..."); senão o título. Frase cortada no meio ("This car was banned in three") não entra."""
    hook = " ".join((hook or "").split())
    if hook and len(hook.split()) <= 9:
        return hook
    first: list[str] = []
    for w in words:
        t = (w.text or "").strip()
        if t:
            first.append(t)
            if re.search(r"[.!?…]$", t):
                break
    chunk: list[str] = []
    for t in first:
        if len(chunk) >= 3 and norm(t) in CLAUSE:
            break
        chunk.append(t)
        if len(chunk) >= 3 and re.search(r"[,:;]$", t):
            break
    for cand, limit in ((first, max_words), (chunk, 9)):
        if 3 <= len(cand) <= limit:
            return re.sub(r"[.,;:…!?]+$", "", " ".join(cand)) + ("?" if cand[-1].endswith("?") else "")
    title = " ".join((title or "").split())
    return title if title and len(title.split()) <= 9 else ""


def stamp_text(stamp: str, words: list, lang: str, currency: str = "", within: float = 6.0) -> str:
    """O carimbo do gancho: o da IA (1-3 palavras); senão o 1º valor em dinheiro/porcentagem dos primeiros
    segundos ("$45.000", "75%")."""
    from ..motion import gfx

    stamp = " ".join((stamp or "").split())
    if stamp and len(stamp.split()) <= 3 and len(stamp) <= 18:
        return stamp.upper()
    for i, w in enumerate(words):
        if w.start > within:
            break
        n = read_number(words, i, lang, currency)
        if n and n.kind in ("money", "percent"):
            return gfx.fmt_number(n.value, n.decimals, n.prefix, n.suffix.strip())
    return ""


TEASER = {
    "pt": ("FICA ATÉ O FINAL", "o nº 1 é absurdo", "o último é o mais importante"),
    "en": ("WATCH TILL THE END", "number 1 is insane", "the last one matters most"),
    "es": ("QUÉDATE HASTA EL FINAL", "el nº 1 es una locura", "el último es el más importante"),
    "fr": ("RESTE JUSQU'À LA FIN", "le n°1 est fou", "le dernier est le plus important"),
    "pl": ("ZOSTAŃ DO KOŃCA", "numer 1 to szok", "ostatni jest najważniejszy"),
}
CTA = {"pt": "SEGUE PARA MAIS", "en": "FOLLOW FOR MORE", "es": "SÍGUEME PARA MÁS", "fr": "ABONNE-TOI",
       "pl": "OBSERWUJ PO WIĘCEJ"}


def plan_fx(words: list, segments: list[Segment], total: float, lang: str = "en", *, hook: str = "",
            title: str = "", stamp: str = "", teaser: str = "", currency: str = "", seed: int = 0,
            density: float = 1.0, ending: str = "loop", hook_on: bool = True) -> FxPlan:
    """Decide os efeitos. ``teaser``: "rank" (ranking, o nº 1 no fim) | "list" (lista numerada) | "" (sem aviso)."""
    lang = _lang(lang)
    rng = random.Random(seed * 977 + 13)
    density = max(0.3, min(2.0, density or 1.0))
    words = [w for w in words if (w.text or "").strip() and w.end <= total + 0.5]
    events: list[FxEvent] = []
    emphasis: list[float] = []
    big_gap, punch_gap = 2.2 / density, 0.9 / density
    last_big, last_punch = -1e9, -1e9
    last_kind = {"freeze": -1e9, "mono": -1e9, "stack": -1e9, "magnify": -1e9, "circle": -1e9, "shake": -1e9,
                 "sticker": -1e9}

    # gancho -----------------------------------------------------------------------------------------
    hook_end = 0.0
    if hook_on and words:
        starts = sorted(_sentence_starts(words))
        first_end = next((words[k - 1].end for k in starts if k > 0), words[-1].end)
        hook_end = min(3.0, max(1.8, first_end))
        text = "" if _busy(segments, 0.0, hook_end) else hook_text(hook, title, words)
        st = stamp_text(stamp, words, lang, currency) if text else ""
        events.append(FxEvent(0.0, hook_end, "hook", {"text": text, "stamp": st,
                                                       "stamp_at": round(min(hook_end - 0.5, 0.95), 3)}))
        events.append(FxEvent(0.0, 0.55, "punch", {"zoom": 0.22, "intro": True}))
        if st:  # o carimbo bate e a tela treme
            at = events[-2].data["stamp_at"]
            events.append(FxEvent(at, at + 0.4, "shake", {}))
        last_punch = 0.0
        if teaser in ("rank", "list"):
            head, rank_line, list_line = TEASER.get(lang, TEASER["en"])
            for a in (hook_end + 0.25, hook_end + 2.5, hook_end + 4.5):
                if a + 2.4 < total and not _busy(segments, a, a + 2.4):
                    events.append(FxEvent(a, a + 2.4, "teaser", {"title": head,
                                                                 "text": rank_line if teaser == "rank" else list_line}))
                    break

    # interrupções de padrão: congelamento na pergunta, preto e branco no "mas" -------------------------
    starts = _sentence_starts(words)
    for i, w in enumerate(words):
        raw = (w.text or "").strip()
        if w.start < hook_end + 1.0:
            continue
        if raw.endswith("?") and w.end - last_kind["freeze"] >= 9.0 / density:
            a = w.end - 0.05
            seg = _seg_at(segments, a)
            if seg and seg.clean and a + 0.8 <= seg.t1 and _free(events, a - 0.3, a + 0.8, kinds=BIG + DRAWN):
                events.append(FxEvent(a, a + 0.8, "freeze", {"icon": "question"}))
                last_kind["freeze"] = a
        elif i in starts and norm(raw) in CONTRAST[lang] and w.start - last_kind["mono"] >= 8.0 / density:
            seg = _seg_at(segments, w.start)
            if seg and not seg.template and w.start + 0.6 <= seg.t1 and _free(events, w.start - 0.2, w.start + 0.6,
                                                                               kinds=BIG + DRAWN):
                events.append(FxEvent(w.start, w.start + 0.6, "mono", {}))
                last_kind["mono"] = w.start

    # ênfase nas palavras fortes ---------------------------------------------------------------------
    starts = _sentence_starts(words)
    scored: list[tuple[float, int, str, Number | None]] = []
    for i, w in enumerate(words):
        if w.start < hook_end + 0.2:
            continue
        num = read_number(words, i, lang, currency)
        cat = category(w.text, lang)
        score = 3.0 if num else 2.0 if is_strong(w.text, lang) else 1.4 if cat else 0.0
        if not score:
            continue
        if (w.text or "").strip().endswith("!"):
            score += 1.0
        scored.append((score + rng.random() * 0.3, i, cat, num))
    picked: list[tuple[int, str, Number | None]] = []
    taken: list[float] = []
    for score, i, cat, num in sorted(scored, reverse=True):
        t = words[i].start
        if all(abs(t - x) >= 1.15 / density for x in taken):
            taken.append(t)
            picked.append((i, cat, num))
    picked.sort()

    drawn = ["circle", "sticker", "magnify"]
    rng.shuffle(drawn)
    for i, cat, num in picked:
        w = words[i]
        t = w.start
        emphasis.append(round(t, 3))
        seg = _seg_at(segments, t)
        if seg is None or seg.template:
            continue  # template na tela: só a legenda em destaque
        room = seg.t1 - t  # quanto falta do shot
        crowded = not _free(events, t - 0.1, t + 1.4, kinds=DRAWN + ("mono", "stack"))  # já tem efeito grande
        if num and (num.kind != "number" or num.value >= 100):
            if t - last_big >= big_gap and room >= 1.2 and not seg.overlay and not crowded:
                events.append(FxEvent(t, min(t + 2.0, seg.t1 - 0.05), "count", {
                    "value": num.value, "decimals": num.decimals, "prefix": num.prefix, "suffix": num.suffix,
                    "unit": num.kind}))
                last_big = t
            if t - last_punch >= punch_gap:
                events.append(FxEvent(t, t + 0.55, "punch", {"zoom": 0.1}))
                last_punch = t
            continue

        def allowed(kind: str) -> bool:
            if crowded:
                return False
            if kind == "sticker":
                return bool(cat) and t - last_kind["sticker"] >= 3.0 / density
            return seg.clean and room >= 1.1 and t - last_big >= big_gap and t - last_kind[kind] >= 6.0 / density

        options = [k for k in drawn if allowed(k)]
        if options and rng.random() < 0.8:  # um desenho (o que faz mais tempo que não aparece)
            choice = options[0]
            drawn.remove(choice)
            drawn.append(choice)
            if choice == "sticker":
                side = "right" if len(of_kind(events, "sticker")) % 2 == 0 else "left"
                events.append(FxEvent(t, t + 1.1, "sticker", {"icon": cat, "side": side,
                                                              "symbol": currency if cat == "money" else ""}))
                last_kind["sticker"] = t
                if t - last_punch >= punch_gap:
                    events.append(FxEvent(t, t + 0.55, "punch", {"zoom": 0.08}))
                    last_punch = t
            else:
                dur = 1.4 if choice == "circle" else 1.3
                events.append(FxEvent(t, min(t + dur, seg.t1 - 0.05), choice, {}))
                last_big = last_kind[choice] = t
            continue
        if t - last_punch < punch_gap * 1.6:
            continue  # soco em toda palavra cansa: a legenda já marca esta
        danger = cat in ("danger", "cross") or (w.text or "").strip().endswith("!")
        shake = danger and t - last_kind["shake"] >= 5.0 / density
        events.append(FxEvent(t, t + 0.55, "punch", {"zoom": 0.07 if shake else 0.12}))
        last_punch = t
        if shake:
            events.append(FxEvent(t, t + 0.4, "shake", {}))
            last_kind["shake"] = t

    # cartas empilhando em alguns cortes ------------------------------------------------------------------
    for k in range(1, len(segments)):
        prev, cur = segments[k - 1], segments[k]
        c = cur.t0
        if prev.template or cur.template or cur.effect or c < hook_end + 1.0 or c - last_kind["stack"] < 4.5 / density:
            continue
        if cur.t1 - c < 0.6 or not _free(events, c - 0.35, c + 0.4, kinds=BIG + DRAWN):
            continue
        if rng.random() < 0.45:
            events.append(FxEvent(c, c + 0.34, "stack", {}))
            last_kind["stack"] = c

    # buraco sem novidade: zoom lento e, se for longo, um soco no meio -------------------------------------
    events += _fill_gaps(events, segments, words, total, emphasis)

    # final ------------------------------------------------------------------------------------------------
    if total > 4 and ending in ("cta", "both"):
        a = max(hook_end + 1, total - 2.6)
        if not _busy(segments, a, total - 0.3):
            events.append(FxEvent(a, total - 0.3, "cta", {"text": CTA.get(lang, CTA["en"])}))
    if total > 4 and ending in ("loop", "both"):
        events.append(FxEvent(total - 0.4, total, "loop", {}))
    # o corte com efeito (zoom de soco, chicote) já mexe a câmera: soco nosso colado nele vira tranco duplo
    hits = [s.t0 for s in segments if s.effect]
    events = [e for e in events if not (e.kind in ("punch", "shake") and not e.data.get("intro")
                                        and any(-0.15 <= e.t0 - c < 0.4 for c in hits))]
    events.sort(key=lambda e: (e.t0, e.kind))
    return FxPlan(events, sorted(set(emphasis)), total, segments)


def of_kind(events: list[FxEvent], kind: str) -> list[FxEvent]:
    return [e for e in events if e.kind == kind]


def _free(events: list[FxEvent], a: float, b: float, kinds: Iterable[str] = BIG + ("hook",)) -> bool:
    return not any(e.kind in kinds and e.t0 < b and a < e.t1 for e in events)


def novelty(events: list[FxEvent], segments: list[Segment]) -> list[float]:
    """Momentos em que algo novo aparece na tela (corte, template, efeito)."""
    pts = [s.t0 for s in segments] + [e.t0 for e in events if e.kind not in ("drift", "loop")]
    return sorted(set(round(p, 3) for p in pts))


def max_gap(plan: FxPlan, segments: list[Segment] | None = None) -> float:
    """O maior trecho sem novidade na tela (template conta como novidade o tempo todo: ele é animado)."""
    segments = segments or []
    pts = sorted(set([0.0, plan.total] + [e.t0 for e in plan.events if e.kind not in ("loop",)]
                     + [s.t0 for s in segments] + [s.t1 for s in segments if s.template]))
    return max((b - a for a, b in zip(pts, pts[1:]) if not _busy(segments, a + 0.05, b - 0.05)), default=0.0)


def _fill_gaps(events: list[FxEvent], segments: list[Segment], words: list, total: float,
               emphasis: list[float]) -> list[FxEvent]:
    out: list[FxEvent] = []
    pts = sorted(set([0.0, total] + novelty(events, segments)))
    for a, b in zip(pts, pts[1:]):
        if b - a <= MAX_GAP or _busy(segments, a + 0.1, b - 0.1):
            continue
        out.append(FxEvent(a + 0.05, b, "drift", {"zoom": min(0.09, 0.025 * (b - a))}))
        # um soco a cada ~2,5 s dentro do buraco, numa palavra (cai no ritmo da fala)
        n = int((b - a) // 2.5)
        for k in range(1, n + 1):
            target = a + (b - a) * k / (n + 1)
            cand = [w.start for w in words if a + 1.0 < w.start < b - 0.8]
            if not cand:
                continue
            t = min(cand, key=lambda x: abs(x - target))
            if all(abs(t - e.t0) > 0.8 for e in out if e.kind == "punch"):
                out.append(FxEvent(t, t + 0.55, "punch", {"zoom": 0.07}))
    return out
