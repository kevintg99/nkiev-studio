"""Ritmo visual: nunca muito tempo seguido só de b-roll.

A IA põe templates onde a fala pede dados (preço, ficha, ranking...). Entre um e outro podem
passar minutos só de b-roll, e o vídeo fica monótono. Aqui, quando passam ``gap`` segundos sem
nenhum gráfico nem overlay, a próxima cena ganha um recurso de ritmo, sempre com texto da
própria fala:

* ``statement`` — frase curta de impacto ("Et c'est une erreur.") vira a tela preta com a
  frase no meio (no máximo uma a cada ``statement_every`` segundos);
* ``phrase`` / ``impact`` — frase de impacto que não virou tela preta: escrita sobre o b-roll com
  marca-texto amarelo, ou a palavra forte grande sobre um véu azul translúcido com a frase embaixo;
* ``showcase`` / ``gallery`` / ``sketch`` / ``dossier`` — cena da seção de um carro: a foto dele
  flutuando no blueprint, três fotos em camadas, a mão desenhando o carro (fala de design puxa o
  desenho) ou o dossiê em papel; nunca o mesmo duas vezes seguidas;
* ``keyword`` — a peça que a fala cita (motor, bancos, turbo...) ou a palavra-chave, escrita numa
  legenda minimalista (sem seta: a imagem nem sempre mostra a peça).

Além disso, ``add_fact_overlays`` põe o card de consumo onde a fala cita "5 litres aux cent" e
``add_file_cards`` põe a ficha de papel com o nome do carro no começo de cada seção do ranking.
"""

from __future__ import annotations

import copy
import random
import re

from .cues import C
from .motion.gfx import parse_number
from .motion.i18n import has_cta
from .log import get_logger
from .models import Scene, Sentence
from .subject import is_rank_line
from .text import keywords, normalize

log = get_logger("rhythm")

# peças que costumam aparecer além das dos templates (fr/pt/pl), sem acento
EXTRA_PARTS = {
    "banquette", "courroie", "chaine", "batterie", "embrayage", "chassis", "rouille", "soupapes", "turbo",
    "injecteurs", "differentiel", "suspension", "suspensions", "plaquettes", "disques", "carrosserie",
    "correia", "corrente", "bateria", "embreagem", "chassi", "ferrugem", "suspensao", "pastilhas", "discos",
    "pasek", "lancuch", "akumulator", "sprzeglo", "rama", "rdza", "zawieszenie", "klocki", "tarcze",
    "timing", "battery", "clutch", "chassis", "rust", "valves", "injectors", "differential", "pads", "rotors",
    "bodywork", "gearbox", "transmission", "radiator", "alternator", "gasket", "struts", "shocks",
}
_PUNCT = ".,!?…:;«»\"'()"


def _sentences_of(scene: Scene, sentences: list[Sentence]) -> list[Sentence]:
    return [s for s in sentences if scene.start - 0.05 <= (s.start + s.end) / 2 <= scene.end + 0.05]


def is_punchy(text: str, max_words: int = 9) -> bool:
    """Frase curta e afirmativa, boa para a tela preta: 3 a 9 palavras, sem número, sem pergunta.
    Com ``max_words`` maior serve para a frase de destaque sobre o b-roll."""
    words = text.split()
    if not 3 <= len(words) <= max_words or text.rstrip().endswith("?"):
        return False
    if has_cta(text):  # chamada para se inscrever já tem o botão
        return False
    if re.search(r"\d", text):
        return False
    return not is_rank_line(text)  # cabeçalho do ranking já tem o cartão


def part_word(text: str) -> str:
    parts = {normalize(p) for p in C("parts")} | EXTRA_PARTS
    for w in text.split():
        clean = w.strip(_PUNCT)
        if normalize(clean) in parts:
            return clean
    return ""


# palavras genéricas demais para escrever na tela sozinhas
GENERIC = {"voiture", "voitures", "vehicule", "carro", "carros", "veiculo", "samochod", "samochody", "pojazd",
           "toujours", "vraiment", "quelque", "chose", "choses", "sempre", "realmente", "alguma", "zawsze",
           "naprawde", "comme", "parce", "porque", "because", "aujourd", "pendant", "depois", "antes",
           # ordinais e palavras de roteiro ("Deuxième", "critère") não dizem nada sozinhas na tela
           "premier", "premiere", "deuxieme", "troisieme", "quatrieme", "dernier", "derniere", "critere",
           "criteres", "primeiro", "primeira", "segundo", "segunda", "terceiro", "terceira", "ultimo", "ultima",
           "criterio", "criterios", "pierwszy", "pierwsza", "drugi", "druga", "trzeci", "trzecia", "ostatni",
           "kryterium",
           # inglês
           "really", "always", "something", "everything", "actually", "basically", "because", "vehicle",
           "vehicles", "people", "number", "today", "probably", "honestly", "pretty", "little", "another",
           "second", "third", "fourth", "criteria", "though", "through", "without", "should", "would", "could"}


def keyword(text: str) -> str:
    counts: dict[str, int] = {}
    for k in keywords(text, min_len=6):
        if k in GENERIC:
            continue
        counts[k] = counts.get(k, 0) + 1
    if not counts:
        return ""
    best = max(counts, key=lambda k: (counts[k], len(k)))
    # devolve a palavra como está escrita na fala (com acento)
    for w in text.split():
        if normalize(w.strip(_PUNCT)) == best:
            return w.strip(_PUNCT)
    return best


def fuel_fact(text: str) -> dict:
    """Overlay de consumo se a fala cita um ("5 litres aux cent", "8 à 9 l/100 km", "12 km/l")."""
    for pat, unit in C("fuel"):
        m = re.search(pat, text, re.I)
        if not m:
            continue
        vals = [g.strip() for g in m.groups()[:2] if g]
        nums = [parse_number(v) for v in vals]
        if not nums or any(n is None or not 1 <= n[0] <= 40 for n in nums):
            continue
        before = text[max(0, m.start() - 80):m.end() + 40]
        where = next((label for p, label in C("fuel_where") if re.search(p, before, re.I)), "")
        return {"kind": "fuel", "title": "–".join(vals), "detail": unit, "title_b": where, "detail_b": "",
                "lines": []}
    return {}


# overlays que o consumo pode substituir (o card de consumo diz mais que um rótulo solto)
_WEAK_OVERLAYS = {"label", "bignum", "badge", "keyword", "arrow"}


def add_fact_overlays(scenes: list[Scene]) -> list[str]:
    """Cena que fala de consumo ganha o card com a bomba de combustível e o número."""
    notes: list[str] = []
    for scene in scenes:
        a = scene.analysis
        if a.style != "normal" or (a.overlay and a.overlay.get("kind") not in _WEAK_OVERLAYS):
            continue
        fact = fuel_fact(scene.text)
        if fact:
            a.overlay = fact
            notes.append(f"cena {scene.index}: consumo em destaque ({fact['title']} {fact['detail']})")
    return notes


# templates com a foto do carro no blueprint (duração padrão e mínima da cena)
VISUALS = {"showcase": (4.5, 3.5), "gallery": (5.0, 3.5), "sketch": (5.0, 4.0), "dossier": (5.0, 4.0),
           # edição magnética (quebras de padrão): a palavra com o vídeo dentro, fotos caindo, tela em painéis,
           # a mão anotando a foto e as palavras pulando no tempo da fala
           "textmask": (3.5, 2.5), "photostack": (5.0, 3.5), "splitpanels": (4.0, 3.0), "annotate": (5.0, 4.0),
           "wordpop": (3.5, 2.5),
           # os vídeos da cena passando na prancheta azul, com a frase datilografada embaixo
           "filmstrip": (6.5, 4.5)}
# não precisam do carro da seção (servem em qualquer vídeo, com a palavra-chave da fala)
FREE_VISUALS = ("textmask", "photostack", "splitpanels", "annotate", "wordpop", "filmstrip")
_DESIGN = re.compile(r"\b(?:design|dessin\w*|con[cç]u\w*|silhouette|lignes?|styl\w*|carrosserie|desenh\w*|"
                     r"projet\w*|linhas|estilo|sylwetk\w*|projekt\w*|stylistyk\w*|lini\w*)\b", re.I)


_LEAD = re.compile(r"^(?:et|mais|car|donc|alors|e|mas|então|porque|i|ale|bo|więc)\s+", re.I)


def short_clause(text: str, lo: int = 3, hi: int = 6) -> str:
    """Trecho curto da fala para a tira do dossiê ("sans jamais faire de caprice"): de 3 a 6
    palavras, sem número; o que tiver a palavra-chave da cena, senão o mais curto."""
    key = normalize(keyword(text))
    parts = [_LEAD.sub("", p.strip(_PUNCT + " ")) for p in re.split(r"[.,;:!?…—–()]", text)]
    ok = [p for p in parts if lo <= len(p.split()) <= hi and not re.search(r"\d", p) and not p.endswith("?")
          and not is_rank_line(p)]
    if not ok:
        return ""
    with_key = [p for p in ok if key and key in normalize(p)]
    return (with_key or sorted(ok, key=lambda p: len(p.split())))[0]


def add_file_cards(scenes: list[Scene]) -> list[str]:
    """Ficha de papel com o nome do carro logo no começo de cada seção do ranking (a primeira cena
    de b-roll depois do cartão "Numéro N"), se ela ainda não tiver um overlay. Uma ficha por carro:
    a que a IA sugeriu de novo mais adiante na seção sai (duas fichas seguidas do Aygo)."""
    notes: list[str] = []
    done: set[str] = set()
    has_card: set[str] = set()
    tries: dict[str, int] = {}
    for scene in scenes:
        a = scene.analysis
        car = a.subject
        if (a.overlay or {}).get("kind") == "file":
            if car in has_card or not car:
                a.overlay = {}
                notes.append(f"cena {scene.index}: segunda ficha do mesmo carro removida")
            else:
                has_card.add(car)
                done.add(car)
            continue
        if not car or car in done:
            continue
        weak = (a.overlay or {}).get("kind") in ("keyword", "label", "arrow", "badge")  # a ficha diz mais
        if a.style != "normal" or (a.overlay and not weak) or scene.duration < 2.5:
            if a.style not in ("ranking", "chapter", "headline"):  # o cabeçalho não conta
                tries[car] = tries.get(car, 0) + 1
                if tries[car] >= 3:  # só no comecinho da seção; depois não faz mais sentido
                    done.add(car)
            continue
        a.overlay = {"kind": "file", "title": car, "detail": "", "title_b": "", "detail_b": "", "lines": []}
        done.add(car)
        has_card.add(car)
        notes.append(f"cena {scene.index}: ficha de papel com o nome do {car}")
    return notes


def pick_visual(rng: random.Random, last: str, duration: float, text: str = "",
                allowed: set[str] | None = None, has_subject: bool = True) -> str:
    """Showcase, galeria, desenho ou dossiê: sorteado, nunca repete o último; fala de design puxa o
    desenho. ``allowed``: os que o canal usa (sem nenhum, "")."""
    pool = [k for k in VISUALS if (allowed is None or k in allowed) and (has_subject or k in FREE_VISUALS)]
    if not pool:
        return ""
    options = [k for k in pool if duration >= VISUALS[k][1] and k != last] or [pool[0]]
    if "sketch" in options and _DESIGN.search(text):
        return "sketch"
    weights = {"showcase": 0.2, "gallery": 0.16, "sketch": 0.14, "dossier": 0.14, "textmask": 0.12,
               "photostack": 0.14, "splitpanels": 0.12, "annotate": 0.12, "wordpop": 0.14, "filmstrip": 0.18}
    return rng.choices(options, [weights[k] for k in options])[0]


def add_rhythm(scenes: list[Scene], sentences: list[Sentence], gap: float = 11.0,
               statement_every: float = 40.0, lead_in: float = 6.0, seed: int = 0,
               statement_chance: float = 0.5, phrase_chance: float = 0.45,
               styles: set[str] | None = None, kinds: set[str] | None = None) -> tuple[list[Scene], list[str]]:
    """Devolve as cenas (a tela preta ganha cena própria) e o que foi feito.

    Nada em compasso fixo: o intervalo até o próximo recurso varia (0,75x a 1,4x ``gap``), a tela
    preta só volta depois de um intervalo sorteado (``statement_every`` a 2,2x) e, mesmo aí, cada frase
    de impacto só vira tela preta com chance ``statement_chance``. Mesma seed, mesmo resultado."""
    rng = random.Random(seed * 7907 + 17)
    out: list[Scene] = []
    notes: list[str] = []
    last = lead_in  # os primeiros segundos ficam com o b-roll do gancho
    next_statement = lead_in + rng.uniform(0.3, 1.0) * statement_every
    gap_now = gap * rng.uniform(0.75, 1.4)
    last_visual, last_kind = "", ""
    for scene in scenes:
        a = scene.analysis
        if a.style != "normal" or a.overlay:
            last = max(last, scene.start + (a.template_seconds or 3.0))
            out.append(scene)
            continue
        if scene.start - last < gap_now or scene.duration < 1.0:
            out.append(scene)
            continue
        sents = _sentences_of(scene, sentences)
        punch = next((s for s in sents if is_punchy(s.text) and 1.0 <= s.end - s.start <= 4.5), None)
        if punch is not None and scene.start >= next_statement and (styles is None or "statement" in styles) \
                and (out[-1].analysis.style if out else "") != "statement" and rng.random() < statement_chance:
            pieces = _split_at(scene, sents, punch)
            out.extend(pieces)
            last = punch.end
            next_statement = punch.end + rng.uniform(1.0, 2.2) * statement_every
            gap_now = gap * rng.uniform(0.75, 1.4)
            notes.append(f"cena {scene.index}: tela preta com a frase \"{punch.text[:40]}\"")
            continue
        if scene.duration < 2.0:  # curta demais para foto ou overlay
            out.append(scene)
            continue
        prev = out[-1].analysis.style if out else ""
        part = part_word(scene.text)
        # frase de destaque sobre o b-roll (a que não virou tela preta)
        quote = next((s for s in sents if is_punchy(s.text, 13) and 1.2 <= s.end - s.start <= 6.0), None)
        can = {k for k in ("phrase", "impact") if kinds is None or k in kinds}
        if quote is not None and can and last_kind not in ("phrase", "impact") and scene.duration >= 2.5 \
                and rng.random() < phrase_chance:
            key = keyword(quote.text)
            # metade vira a palavra grande sobre o véu azul ("ERREUR" + a frase), metade a frase com marca-texto
            if key and len(key) >= 5 and "impact" in can and ("phrase" not in can or rng.random() < 0.5):
                a.overlay = {"kind": "impact", "title": key, "detail": quote.text.strip(), "title_b": "",
                             "detail_b": "", "lines": []}
            else:
                a.overlay = {"kind": "phrase", "title": quote.text.strip(), "detail": "", "title_b": "",
                             "detail_b": "", "lines": [key] if key else []}
            last, last_kind = quote.end, a.overlay["kind"]
            gap_now = gap * rng.uniform(0.75, 1.4)
            notes.append(f"cena {scene.index}: {a.overlay['kind']} \"{quote.text[:40]}\"")
            out.append(scene)
            continue
        # foto no blueprint (3 jeitos) na maioria das vezes; às vezes a setinha (se a fala cita uma peça)
        style = pick_visual(rng, last_visual, scene.duration, scene.text, styles, has_subject=bool(a.subject)) \
            if scene.duration >= 3.5 and prev not in VISUALS and not (part and rng.random() < 0.3) \
            and (a.subject or rng.random() < 0.7) else ""  # sem assunto único na cena: 30% vira só legenda
        if style:
            a.style, a.caption, a.subtitle = style, a.subject or keyword(scene.text), ""
            if style == "dossier":  # a tira de papel leva um trecho curto da própria fala
                a.subtitle = short_clause(scene.text)
            if style == "annotate":  # a anotação escrita à mão: a peça citada ou a palavra-chave
                a.caption = part or keyword(scene.text) or a.subject
            if style == "textmask":  # a palavra gigante: o modelo ("Tiguan"), não o nome inteiro
                from .subject import model_word

                a.caption = (model_word(a.subject).title() if a.subject else "") or keyword(scene.text)
                a.subtitle = short_clause(scene.text)
            if style == "wordpop":
                a.body = scene.text.strip()
            if not a.caption and style != "wordpop":
                a.style = "normal"
                out.append(scene)
                continue
            a.template_seconds = min(VISUALS[style][0], scene.duration)
            last, last_visual = scene.start + a.template_seconds, style
            gap_now = gap * rng.uniform(0.75, 1.4)
            notes.append(f"cena {scene.index}: {style} do {a.subject}")
            out.append(scene)
            continue
        word = part or keyword(scene.text)
        if word and scene.duration >= 2.5 and (kinds is None or "keyword" in kinds):
            a.overlay = {"kind": "keyword", "title": word, "detail": "", "title_b": "",
                         "detail_b": "", "lines": []}
            last, last_kind = scene.start + 3.0, a.overlay["kind"]
            gap_now = gap * rng.uniform(0.75, 1.4)
            notes.append(f"cena {scene.index}: {a.overlay['kind']} \"{word}\"")
        out.append(scene)
    for i, s in enumerate(out):
        s.index = i
    if notes:
        log.info("ritmo: gráficos entre os trechos longos de b-roll", extra={"stage": "rhythm", "detalhes": notes[:20],
                                                                             "total": len(notes)})
    return out, notes


def add_opener(scenes: list[Scene], title: str, kicker: str = "", after: float = 0.0, until: float = 45.0,
               styles: set[str] | None = None) -> list[str]:
    """Cartão de abertura (template "opener") com o título do vídeo: na primeira cena de b-roll depois do
    apresentador da abertura (``after``), nos primeiros ``until`` segundos. O gancho falado continua por baixo."""
    if not title or (styles is not None and "opener" not in styles):
        return []
    for scene in scenes:
        if scene.start < after - 0.2 or scene.duration < 3.0:
            continue
        if scene.start > until:
            break
        if scene.analysis.style != "normal":
            continue
        a = scene.analysis
        a.style, a.caption, a.subtitle, a.template_seconds = "opener", title, kicker, min(4.5, scene.duration)
        a.overlay = {}
        return [f"cena {scene.index}: abertura \"{title}\""]
    return []


def _split_at(scene: Scene, sents: list[Sentence], punch: Sentence) -> list[Scene]:
    """[antes] [frase na tela preta] [depois]; pedaço com menos de 1 s fica junto da frase."""
    k = sents.index(punch)
    t_head0 = scene.start if k == 0 else punch.start
    t_head1 = scene.end if k == len(sents) - 1 else sents[k + 1].start
    pieces: list[Scene] = []
    if t_head0 - scene.start >= 1.0 and k > 0:
        pre = copy.deepcopy(scene.analysis)
        pieces.append(Scene(0, " ".join(s.text for s in sents[:k]), scene.start, t_head0, pre))
    else:
        t_head0 = scene.start
    stat = copy.deepcopy(scene.analysis)
    stat.style, stat.body, stat.caption = "statement", punch.text.strip(), ""
    stat.highlights, stat.overlay = [], {}
    end = t_head1 if scene.end - t_head1 >= 1.0 else scene.end
    stat.template_seconds = round(end - t_head0, 3)
    pieces.append(Scene(0, punch.text, t_head0, end, stat))
    if end < scene.end:
        post = copy.deepcopy(scene.analysis)
        pieces.append(Scene(0, " ".join(s.text for s in sents[k + 1:]), end, scene.end, post))
    return pieces


# ------------------------------------------------------------ troca de família
def apply_swaps(scenes: list[Scene], template_swap: dict[str, str], overlay_swap: dict[str, str],
                styles: set[str], kinds: set[str], chance: float = 1.0, seed: int = 0) -> list[str]:
    """Troca cada recurso pelo da família do estilo (Broadcast: tela preta -> plantão, foto no
    blueprint -> reportagem/telão, legenda -> tarja, frase -> barra de manchete). Os textos continuam
    os da fala. Só troca para o que está liberado (``styles``/``kinds``); ``chance`` < 1 troca só uma
    parte (estilo Mix)."""
    if not template_swap and not overlay_swap:
        return []
    rng = random.Random(seed * 131 + 7)
    notes: list[str] = []

    def pick(value: "str | list[str] | None", allowed: set[str]) -> str:
        """Troca fixa ("bulletin") ou uma entre várias (Mix: ["bulletin", "paperstatement"])."""
        options = [v for v in (value if isinstance(value, list) else [value]) if v and v in allowed]
        return rng.choice(options) if options else ""

    for scene in scenes:
        a = scene.analysis
        new = pick(template_swap.get(a.style), styles)
        if new and rng.random() < chance:
            old = a.style
            if new in ("report", "videowall", "paperfloat", "paperpair", "newsfloat"):
                a.caption = a.caption or a.subject or keyword(scene.text)
                a.subtitle = a.subtitle or short_clause(scene.text) or short_clause(scene.text, 3, 9)
            if new in ("bulletin", "paperstatement"):
                a.body = a.body or a.caption or scene.text.strip()
                a.caption = a.subject or ""  # a faixa rolando leva o carro da seção (nunca uma palavra solta)
            a.style = new
            notes.append(f"cena {scene.index}: {old} -> {new}")
        ov = a.overlay or {}
        kind = ov.get("kind", "")
        new = pick(overlay_swap.get(kind), kinds)
        if not new or rng.random() >= chance:
            continue
        if new == "lowerthird" and kind == "years":  # tarja com o carro e os anos
            title, years = ov.get("title", ""), ov.get("detail", "")
            a.overlay = {"kind": "lowerthird", "title": title or years, "detail": years if title else "",
                         "title_b": "", "detail_b": "", "lines": []}
        elif new == "lowerthird":
            title = ov.get("title", "")
            clause = short_clause(scene.text) or short_clause(scene.text, 3, 9)  # a tarja aceita um pouco mais
            detail = clause if clause and normalize(title) not in normalize(clause) else ""
            a.overlay = {"kind": "lowerthird", "title": title, "detail": detail, "title_b": "", "detail_b": "",
                         "lines": []}
        elif new == "ticker":
            sentence = ov.get("detail", "") if kind == "impact" else ov.get("title", "")
            if not sentence:
                continue
            a.overlay = {"kind": "ticker", "title": sentence, "detail": a.subject or "", "title_b": "",
                         "detail_b": "", "lines": []}
        else:
            a.overlay = {**ov, "kind": new}
        notes.append(f"cena {scene.index}: overlay {kind} -> {new}")
    if notes:
        log.info("estilo do vídeo: recursos trocados pela família", extra={"stage": "rhythm", "total": len(notes),
                                                                            "detalhes": notes[:12]})
    return notes


# ------------------------------------------------------------- letreiro (crawl)
_NUMBERISH = re.compile(r"\d|£|€|\$|zł|mpg|km|litre|liter|litro|pounds|euros|dollars|reais", re.I)


def crawl_items(text: str, limit: int = 4, subject: str = "") -> list[str]:
    """Frases curtas da fala para o letreiro (3 a 9 palavras), com prioridade para as que têm número
    (preço, consumo, anos) e as que citam uma peça; na ordem em que foram faladas. Fica de fora o que
    só repete o nome do carro (ele já está no bloco vermelho)."""
    parts = [" ".join(_LEAD.sub("", p.strip(_PUNCT + " ")).split())
             for p in re.split(r"[.;:!?…—–()]|,\s+(?:and|et|e|i|y)\s+", text)]
    name = set(normalize(subject).split()) | {"the", "la", "le", "a", "o", "the", "l"}
    scored = []
    for k, p in enumerate(parts):
        n = len(p.split())
        if not 3 <= n <= 9 or p.endswith("?") or is_rank_line(p) or is_cta(p):
            continue
        if subject and set(normalize(p).split()) <= name:
            continue
        score = 3 * bool(_NUMBERISH.search(p)) + bool(part_word(p)) + (1 if 4 <= n <= 7 else 0)
        scored.append((score, k, p))
    best = sorted(scored, key=lambda x: (-x[0], x[1]))[:limit]
    return [p for _, _, p in sorted(best, key=lambda x: x[1])]


def is_cta(text: str) -> bool:
    return has_cta(text)


# ---------------------------------------------------------------- ano/modelo
_Y = r"(?:19[5-9]\d|20[0-3]\d)"
_RANGE = re.compile(rf"\b({_Y})\s*(?:-|–|—|to|until|till|through|thru|à|au|a|até|ate|do|bis|hasta|et|e)\s*({_Y}|\d\d)\b",
                    re.I)
_MODEL_YEAR = re.compile(
    rf"\b(?:model|version|year|modèle|modele|version|année|annee|modelo|ano|rocznik|model z|wersja)\s+({_Y})\b"
    rf"|\b({_Y})\s+(?:model|version|modèle|modele|modelo|rocznik)\b"
    rf"|\b(?:built|made|produced|launched|released|fabricad[oa]|lançad[oa]|produzid[oa]|produit|sorti|lancé|lance|"
    rf"produkowan\w*|wyprodukowan\w*)\s+(?:in|en|em|w)\s+({_Y})\b"
    rf"|\b(?:from|since|depuis|desde|od)\s+({_Y})\s+(?:onwards|onward|on|en avant|em diante|para cá|wzwyż)\b"
    rf"|\b(?:on sale|sold|available|en vente|vendu|vendue|à venda|vendid[oa]|w sprzedaży)\s+(?:since|from|depuis|"
    rf"desde|od)\s+({_Y})\b", re.I)


def year_fact(text: str) -> str:
    """Anos do carro citados na fala: "2014 to 2019" -> "2014–2019"; "the 2008 model" -> "2008";
    "sold from twenty fourteen to twenty twenty-one" (voz de IA) -> "2014–2021"."""
    from .spoken import spoken_years

    text = spoken_years(text)
    m = _RANGE.search(text)
    if m:
        a, b = m.group(1), m.group(2)
        if len(b) == 2:
            b = a[:2] + b
        if 0 < int(b) - int(a) <= 30:
            return f"{a}–{b}"
    m = _MODEL_YEAR.search(text)
    if m:
        return next(g for g in m.groups() if g)
    return ""


def add_year_overlays(scenes: list[Scene], min_gap: float = 20.0) -> list[str]:
    """Quando a fala cita os anos do carro ("2014 to 2019", "the 2008 model"), entra o card de ano/modelo
    com o nome do carro — na própria cena ou, se ela já for um título/gráfico, na próxima cena de b-roll
    da mesma seção. Uma vez por carro e por ano; nunca colado em outro card de ano."""
    notes: list[str] = []
    seen: set[tuple[str, str]] = set()
    last = -1e9
    from .subject import other_brands

    for i, scene in enumerate(scenes):
        car = scene.analysis.subject or ""
        # os anos têm que ser do carro da seção: "The older Hyundai Kona hybrid, 2019 to 2023" na seção
        # do Qashqai é a alternativa sugerida, não o Qashqai
        years = next((y for p in re.split(r"(?<=[.!?])\s+", scene.text)
                      if (y := year_fact(p)) and not (car and other_brands(p, "", car))), "")
        if not years:
            continue
        if (car, years) in seen or scene.start - last < min_gap:
            continue
        target = None
        for cand in scenes[i:i + 3]:
            a = cand.analysis
            if a.subject != scene.analysis.subject:
                break
            if a.style == "normal" and cand.duration >= 2.5 and (not a.overlay or a.overlay.get("kind") == "keyword"):
                target = cand
                break
        if target is None:
            continue
        target.analysis.overlay = {"kind": "years", "title": car, "detail": years, "title_b": "", "detail_b": "",
                                   "lines": []}
        seen.add((car, years))
        last = target.start
        notes.append(f"cena {target.index}: ano/modelo {years}" + (f" ({car})" if car else ""))
    return notes
