"""Pistas na fala para os templates de reportagem (``motion/templates_story.py``).

Cada detector lê as frases da cena (com os números escritos, ``spoken.screen_text``) e copia os dados
da fala, nunca inventa:

* ``source``     — quem disse: "analysts at UBS, the Swiss investment bank", "a Reuters investigation in
  2023", "figures from the Association of British Insurers", "Thatcham Research ... published",
  "according to BYD", "selon l'UFC-Que Choisir", "segundo a Fenabrave".
* ``fromto``     — "it has gone from five showrooms to well over 140", "est passé de 3 à 12 modèles".
* ``coverage``   — a garantia por item: "six years of general cover, up to 93,750 miles", "the battery is
  covered for eight years".
* ``steps``      — dica numerada: a frase é só o número ("One.", "Two."...) e a seguinte é a dica.
  ``prime_steps`` vê o roteiro inteiro para saber o total (precisa de 1, 2, 3... em sequência).
* ``unverified`` — "I have not been able to confirm…", "we do not know yet", "an educated guess".
* ``exploded``   — desmontagem: "taken apart down to the last bolt", "teardown", "démontée".
"""

from __future__ import annotations

import re
from collections import Counter

from .spoken import screen_text, word_int
from .text import normalize

_WORD_NUMS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|un|une|deux|trois|quatre|cinq|six|"
              "sept|huit|neuf|dix|um|uma|dois|duas|três|tres|quatro|cinco|seis|sete|oito|nove|dez")


def _lang() -> str:
    from .cues import language

    return language()


def _text(raw: str) -> str:
    return screen_text(raw, _lang())


def _det(kind: str, first: int, last: int, priority: int, fields: dict):
    from .auto_templates import Detection

    return Detection(kind, first, last, priority, fields)


def _num_value(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{1,3}(?:[,\s  .]\d{3})+|\d+(?:\.\d+)?", raw):
        digits = re.sub(r"[,\s  ]", "", raw)
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", digits):  # "3.000" (pt) = três mil
            digits = digits.replace(".", "")
        return float(digits)
    n = word_int(raw)
    return float(n) if n is not None else None


def _trim_words(text: str, limit: int) -> str:
    text = " ".join(text.split()).strip(" ,;:.")
    words = text.split()
    if len(words) <= limit:
        return text
    head = " ".join(words[:limit])
    cut = max(head.rfind(","), head.rfind(";"))
    return (head[:cut] if cut > len(head) * 0.5 else head).strip(" ,;:")


# ------------------------------------------------------------------ source
_CONNECT = {"of", "for", "and", "de", "du", "des", "da", "do", "das", "dos", "&"}
_NOT_SOURCE = {"I", "We", "It", "They", "He", "She", "You", "This", "That", "These", "Those", "The", "A", "An",
               "And", "But", "So", "Now", "Then", "In", "On", "At", "By", "For", "If", "When", "What", "Where",
               "Which", "Who", "Here", "There", "One", "Two", "Three", "Four", "Five", "Some", "Most", "Nobody",
               "Le", "La", "Les", "Un", "Une", "Et", "Mais", "O", "Os", "As", "Um", "Uma", "E", "Mas", "Em",
               "Britain", "British", "Europe", "European", "China", "Chinese", "Japan", "Japanese", "France",
               "French", "Brazil", "Swiss", "German", "Germany", "America", "American", "UK", "US", "USA",
               "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"}
_ORG_NOUNS = (r"bank|publisher|paper|newspaper|magazine|institute|research|centre|center|agency|association|"
              r"insurers?|company|group|team|firm|survey|university|regulator|government|bureau|journal|"
              r"organisation|organization|charity|watchdog|consultancy|analysts?|banque|journal|institut|"
              r"association|revue|magazine|instituto|associação|revista|jornal|banco|agência")
_DOC_WORDS = (r"investigation|report|study|survey|analysis|enquête|étude|rapport|pesquisa|estudo|relatório|"
              r"levantamento")
_CUES = [
    # "according to BYD", "selon l'UFC", "segundo a Fenabrave", "de acordo com o Inmetro", "as reported by ..."
    re.compile(r"\b(?:according\s+to|as\s+reported\s+by|reported\s+by|figures\s+from|data\s+from|analysts\s+at|"
               r"engineers\s+at|researchers\s+at|selon|d['’]apr[èe]s|segundo|de\s+acordo\s+com|dados\s+d[aoe])\s+"
               r"([^,.;:!?]{2,80})", re.I),
]
_LEAD_VERB = re.compile(r"\s+(?:also\s+)?(?:published|says|said|found|reported|surveyed|warned|estimates|estimated|"
                        r"reckoned|calculated|worked\s+out|concluded|has\s+said|told|revealed|a\s+publié|"
                        r"publicou|divulgou|afirma|diz|estima)\b", re.I)


def _cap_runs(segment: str) -> list[tuple[int, str]]:
    """Sequências de Palavras Com Maiúscula (com "of"/"for"/"de" no meio): "Association of British Insurers"."""
    toks = list(re.finditer(r"[\w&'’.-]+", segment))
    runs: list[tuple[int, str]] = []
    k = 0
    while k < len(toks):
        w = toks[k].group(0)
        if not (w[:1].isupper() or w[:1].isdigit() and k > 0):
            k += 1
            continue
        start, j, last = toks[k].start(), k, k
        while j + 1 < len(toks):
            nxt = toks[j + 1].group(0)
            if nxt[:1].isupper():
                j += 1
                last = j
            elif nxt.lower() in _CONNECT and j + 2 < len(toks) and toks[j + 2].group(0)[:1].isupper():
                j += 1
            else:
                break
        runs.append((start, segment[start:toks[last].end()].strip("’'.")))
        k = last + 1
    return runs


def _source_name(segment: str) -> tuple[str, str]:
    """Do trecho depois da pista: o nome (o último grupo com maiúscula) e o que vem antes ("the Japanese
    business paper Nikkei" -> ("Nikkei", "Japanese business paper"))."""
    # o que a fonte disse não faz parte do nome: "the Globe and Mail found all-weathers stopping about 20%..."
    segment = re.split(r"\s+(?:found|finds|says|said|reported|reports|estimated|estimates|published|shows|showed|"
                       r"concluded|warned|a\s+publié|publicou|diz|disse)\b", segment)[0]
    runs = [(p, r) for p, r in _cap_runs(segment) if r.split()[0] not in _NOT_SOURCE or len(r.split()) > 1]
    runs = [(p, r) for p, r in runs if not r[:1].isdigit()]  # "20%" não é fonte
    runs = [(p, re.sub(r"^(?:The|Le|La|Les|O|A|Os|As)\s+", "", r)) for p, r in runs]
    runs = [(p, r) for p, r in runs if r and r not in _NOT_SOURCE]
    if not runs:
        return "", ""
    pos, name = runs[-1]
    before = re.sub(r"^\s*(?:the|a|an|le|la|les|l['’]|o|a|os|as)\s+", "", segment[:pos], flags=re.I).strip(" ,")
    return name, before if 1 <= len(before.split()) <= 5 else ""


def _appositive(text: str, end: int) -> str:
    """", the Swiss investment bank," depois do nome -> "Swiss investment bank"."""
    m = re.match(r"\s*,\s*(?:the|a|an|un|une|le|la|o|a|um|uma)\s+([^,.;]{3,60}?)\s*(?:,|\.|;|$)", text[end:], re.I)
    if m and re.search(rf"\b(?:{_ORG_NOUNS})\b", m.group(1), re.I):
        return m.group(1).strip()
    return ""


def _when(text: str) -> str:
    m = re.search(r"\b(?:in\s+)?((?:early|late|mid-)?\s*(?:January|February|March|April|May|June|July|August|"
                  r"September|October|November|December)?\s*(?:19[5-9]\d|20[0-3]\d))\b", text)
    return " ".join(m.group(1).split()) if m else ""


def _is_brand_or_car(name: str) -> bool:
    from .subject import BRANDS
    from .text import normalize

    first = normalize(name.split()[0]) if name.split() else ""
    return first in BRANDS


def source(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        t = _text(raw)
        name, desc = "", ""
        for pat in _CUES:
            m = pat.search(t)
            if m:
                seg = m.group(1)
                name, desc = _source_name(seg)
                if name:
                    end = m.start(1) + seg.find(name) + len(name)
                    desc = _appositive(t, end) or desc
                    if "according" not in m.group(0).lower() and _is_brand_or_car(name):
                        name = ""  # "analysts at BYD" não é fonte independente
                    break
        if not name:  # "a Reuters investigation", "the What Car Reliability Survey"
            m = re.search(r"\b(?:an?|the|une?|um|uma)\s+((?:[A-Z][\w'’-]*\s+){1,4})(" + _DOC_WORDS + r")\b", t)
            if m and not _is_brand_or_car(m.group(1)):
                cand = m.group(1).strip()
                if cand.split()[0] not in _NOT_SOURCE or len(cand.split()) > 1:
                    name, desc = cand, m.group(2).lower()
                    if m.group(2)[:1].isupper():  # "What Car Reliability Survey": o nome inclui "Survey"
                        name, desc = f"{cand} {m.group(2)}", ""
        if not name:  # "Thatcham Research, which works for ..., published", "Nikkei BP, a Japanese publisher,"
            for p, run in _cap_runs(t)[:3]:
                first = re.sub(r"['’][a-z]+$", "", run.split()[0])  # "I've said", "We'd found": é o narrador
                if first in _NOT_SOURCE and len(run.split()) == 1 or _is_brand_or_car(run):
                    continue
                end = p + len(run)
                app = _appositive(t, end)
                after = re.sub(r"^\s*,[^,]{3,80},", "", t[end:])
                if app or _LEAD_VERB.match(after):
                    name, desc = re.sub(r"^(?:The)\s+", "", run), app
                    break
        name = re.sub(r"['’]s$", "", name)  # "BYD's own terms" -> "BYD"
        name = name.split(",")[-1].strip()  # "In the States, AAA found" -> "AAA"
        name = re.sub(r"^(?:And|But|So|Then|Now|Et|Mais|E|Mas)\s+", "", name)  # "And Équité says" -> "Équité"
        if len(name.split()) == 1 and name.lower().endswith(("ly", "ment")):
            continue  # "Honestly, a company that ..." / "Évidemment, ...": advérbio, não fonte
        if re.match(r"\s*(?:here['’]s|this\s+is|that['’]s)\s+what\b", t, re.I):
            continue  # "Here's what the APA says about it.": a chamada, o que a fonte diz vem depois
        if not name or len(name) > 48:
            continue
        # a data da fonte vem antes do nome ("In 2023, analysts at UBS"); a que vem depois é do assunto
        # ("Parkers ... says Ford switched ... around 2018" não é de quando a Parkers falou)
        at = t.find(name)
        when = _when(t[:at]) if at > 0 else ""
        if not when and at >= 0:  # "a Reuters investigation in 2023 quoted experts"
            near = re.match(r"\s*(?:[a-z]+\s+){0,2}in\s+((?:19[5-9]|20[0-3])\d)\b", t[at + len(name):])
            when = near.group(1) if near else ""
        sub = " · ".join(x for x in (desc, when) if x)
        return _det("source", i, i, 4, {"caption": name, "subtitle": sub, "body": _trim_words(t, 26)})
    return None


# ------------------------------------------------------------------ fromto
_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|" + _WORD_NUMS + r")"
_QUAL = r"(?:(?:well|just|a\s+little)\s+)?(?:over|under|around|about|nearly|almost|more\s+than|roughly|some|plus\s+de|" \
        r"près\s+de|environ|mais\s+de|quase|cerca\s+de)\s+"
_GROW_EN = re.compile(r"\b(?:gone|grown|grew|went|risen|rose|jumped|increased|climbed|expanded|fallen|fell|dropped|"
                      r"shrunk|shrank|moved)\s+from\s+" + _NUM + r"\s+((?:[a-z]+\s+){0,2}?)to\s+(?:" + _QUAL + r")?" +
                      _NUM + r"(?:\s+([a-z]+))?", re.I)
_GROW_FR_PT = re.compile(r"\b(?:pass[ée]\w*|grimp[ée]\w*|bondi|chut[ée]\w*|augment[ée]\w*|passou|saltou|subiu|caiu|"
                         r"cresceu|foi)\s+de\s+" + _NUM + r"\s+((?:[\wà-ÿ]+\s+){0,2}?)(?:à|a|para|pra)\s+(?:" + _QUAL +
                         r")?" + _NUM + r"(?:\s+([\wà-ÿ]+))?", re.I)
_SUBJ_STOP = {"and", "the", "has", "have", "had", "it", "its", "this", "that", "so", "now", "then", "a", "an", "et",
              "le", "la", "les", "a", "e", "o", "os", "as", "il", "elle", "ele", "ela", "est", "is", "was"}


def fromto(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        t = _text(raw)
        m = _GROW_EN.search(t) or _GROW_FR_PT.search(t)
        if not m:
            continue
        a, b = _num_value(m.group(1)), _num_value(m.group(3))
        if a is None or b is None or a == b:
            continue
        if any(1950 <= v <= 2039 and float(v).is_integer() and len(str(int(v))) == 4 for v in (a, b)):
            continue  # anos: isso é linha do tempo
        lo, hi = min(a, b), max(a, b)
        if lo <= 0 and hi < 2 or (lo > 0 and hi / lo < 1.5):
            continue
        noun_a = (m.group(2) or "").strip()
        noun_b = (m.group(4) or "").strip()
        stop = {"in", "by", "over", "since", "and", "en", "em", "depuis", "desde", "e", "et", "per"}
        noun = noun_b if noun_b and noun_b.lower() not in stop else noun_a
        if noun and noun == noun_a and b > 1 and _lang() == "en" and not noun.endswith("s"):
            noun += "s"  # "from one model to ten" -> "models"
        before = re.split(r"[,;:]", t[:m.start()])[-1]
        who = [w for w in re.findall(r"[\w'’-]+", before) if w.lower() not in _SUBJ_STOP]
        sub = " ".join(who[-3:])
        prefix = "£" if "£" in t[m.start():m.end()] else "€" if "€" in t[m.start():m.end()] else ""
        number = {"from": a, "value": b, **({"prefix": prefix} if prefix else {})}
        return _det("fromto", i, i, 6, {"number": number, "caption": noun, "subtitle": sub})
    return None


# ---------------------------------------------------------------- coverage
_COVER = re.compile(r"\b(?:warrant\w*|cover(?:ed|s)?|guarantee\w*|garanti\w*|gwarancj\w*)\b", re.I)
_YEARS = re.compile(r"\b(\d{1,2}|" + _WORD_NUMS + r")[\s-]+(?:years?|ans|anos|lat)\b", re.I)
_DIST = re.compile(r"\b(?:up\s+to\s+|jusqu['’]à\s+|até\s+)?(\d{1,3}(?:[,\s .]\d{3})+|\d+)\s*(miles|km|kilom[eè]tres|"
                   r"quil[oô]metros)\b", re.I)
_SAME = re.compile(r"\bsame\s+mileage\b|\bm[êe]me\s+kilom[ée]trage\b|\bmesma\s+quilometragem\b", re.I)
_ROW = [
    re.compile(r"\byears?\s+of\s+([a-z]+(?:\s+[a-z]+)?)\s+cover\b", re.I),  # "six years of general cover"
    re.compile(r"\b(?:the\s+|la\s+|le\s+|a\s+|o\s+)?([a-zà-ÿ]+(?:\s+[a-zà-ÿ]+)?)\s+(?:is|are|est|sont|é|são)\s+"
               r"(?:covered|couverte?s?|garantie?s?|coberta?s?|garantida?s?)\b", re.I),
]


def coverage(ss: list[str], subject: str):
    from .motion.i18n import text as tr

    rows: list[dict] = []
    first = last = -1
    for i, raw in enumerate(ss):
        t = _text(raw)
        if not _COVER.search(t):
            continue
        ym = _YEARS.search(t)
        if not ym:
            continue
        years = _num_value(ym.group(1))
        if not years or not 1 <= years <= 15:
            continue
        name = ""
        for pat in _ROW:
            m = pat.search(t)
            if m:
                name = m.group(1).strip()
                name = name + " cover" if pat is _ROW[0] and _lang() == "en" else name
                break
        name = re.sub(r"^(?:and|so|but|et|e)\s+", "", name, flags=re.I)
        dist = _DIST.search(t)
        detail = f"{dist.group(1)} {dist.group(2)}" if dist else (rows[-1]["detail"] if rows and _SAME.search(t) else "")
        rows.append({"text": (name or tr("warranty", _lang())).capitalize(), "value": years, "detail": detail})
        first = i if first < 0 else first
        last = i
    if len(rows) < 2 and not (rows and rows[0]["detail"]):
        return None
    brand = (subject or "").split()[0] if subject else ""
    brand = brand.upper() if len(brand) <= 3 else brand.title()
    title = f"{brand} · {tr('warranty', _lang())}" if brand else tr("warranty", _lang())
    return _det("coverage", first, last, 8, {"points": rows[:4], "caption": title})


# ------------------------------------------------------------------- steps
# a palavra da lista vira o rótulo do quadro: "Round one." -> "ROUND 1 OF 8"; "Layer one." -> "LAYER 1 OF 5"
_STEP_WORD = r"(?:(?P<w>step|tip|round|layer|rule|étape|conseil|manche|règle|passo|dica|rodada|regra|camada)\s+)?"
_STEP_HEAD = re.compile(r"^\s*(?:and\s+|et\s+|e\s+)?" + _STEP_WORD +
                        r"(?P<n>\d{1,2}|" + _WORD_NUMS + r")\s*[.:!]?\s*$", re.I)
# a dica na mesma frase: "Un : le moteur exact, pas seulement le modèle.", "Two: the invoices."
_STEP_INLINE = re.compile(r"^\s*(?:and\s+|et\s+|e\s+)?" + _STEP_WORD +
                          r"(?P<n>\d{1,2}|" + _WORD_NUMS + r")\s*:\s*(?P<tip>\S.{4,})$", re.I)
_STEPS: dict[tuple[str, str], tuple[int, int]] = {}
_STEPS_ONE: dict[str, tuple[int, int]] = {}


def prime_steps(texts: list[str]) -> dict[str, tuple[int, int]]:
    """Acha as dicas numeradas do roteiro: frases que são só "One.", "Two."... em sequência (1, 2, 3...,
    pelo menos 3). Guarda o número e o total de cada uma para o detector. A chave é a frase + a seguinte:
    o mesmo "One." pode abrir dois trechos ("two things the dealer might tell you. One. ... Two. ..." e, mais
    tarde, os seis conselhos "One. Timing." ... "And six."); só a sequência de 3 ou mais vira passos."""
    from . import director

    _STEPS.clear()
    _STEPS_ONE.clear()
    if director.active():  # as listas que o diretor de IA marcou
        director.fill_steps(_STEPS, _STEPS_ONE)
        return {text: v for (text, _), v in _STEPS.items()}
    heads = []
    for i, t in enumerate(texts):
        m = _STEP_HEAD.match(t or "") or _STEP_INLINE.match(t or "")
        if m and (n := _num_value(m.group("n"))) is not None:
            heads.append((i, t.strip(), int(n)))
    runs: list[list[tuple[int, str, int]]] = []
    run: list[tuple[int, str, int]] = []
    for i, text, n in heads:
        if n == 1:
            run = [(i, text, 1)]
            runs.append(run)
        elif run and n == run[-1][2] + 1:
            run.append((i, text, n))
    seen = Counter(text for _, text, _ in heads)
    out: dict[str, tuple[int, int]] = {}
    for run in runs:
        if len(run) < 3:
            continue
        for i, text, k in run:
            nxt = texts[i + 1].strip() if i + 1 < len(texts) else ""
            _STEPS[(text, nxt)] = out[text] = (k, len(run))
            if seen[text] == 1:
                _STEPS_ONE[text] = (k, len(run))
    return out


def step_of(text: str, nxt: str | None) -> tuple[int, int] | None:
    """(número, total) da dica que ``text`` abre, sabendo a frase seguinte (ou sozinha, se ela é única)."""
    t = (text or "").strip()
    return _STEPS.get((t, (nxt or "").strip())) or _STEPS_ONE.get(t)


def steps(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        hit = step_of(raw, ss[i + 1] if i + 1 < len(ss) else None)
        inline = _STEP_INLINE.match(raw.strip()) if hit else None
        if not hit or (i + 1 >= len(ss) and not inline):
            continue
        tip = _text(inline.group("tip") if inline else ss[i + 1])
        tip = tip[:1].upper() + tip[1:]
        tip = _trim_words(re.split(r"(?<=[.!?])\s", tip)[0], 12)
        head_part = tip.split(",")[0].strip()
        if len(tip.split()) > 9 and len(head_part.split()) >= 2:
            tip = head_part  # "Slush and cold rain, the kind of mess you get..." -> "Slush and cold rain"
        # "One. Timing." vale (uma palavra que é o título da dica); "One. Yes." não
        if not tip.split() or len(tip.split()) < 2 and (len(tip.strip(" .!")) < 3 or normalize(tip.strip(" .!"))
                                                         in ("yes", "okay", "right", "sure", "well", "voila", "bon")):
            continue
        k, n = hit
        head = _STEP_HEAD.match(raw.strip()) or inline
        word = (head.group("w") or "") if head else ""
        # "ROUND 1 OF 8" no lugar de "STEP 1 OF 8" (sempre definido: não herda o subtítulo que a IA pôs na cena)
        custom = word and word.lower() not in ("step", "étape", "passo")
        fields = {"number": {"value": k, "total": n}, "caption": tip, "subtitle": word.upper() if custom else ""}
        return _det("steps", i, i if inline else i + 1, 9, fields)
    return None


# -------------------------------------------------------------- unverified
_UNSURE = [
    ("unconfirmed", re.compile(
        r"\b(?:(?:have|has|had)\s+not|haven['’]t|hasn['’]t|could\s+not|couldn['’]t|cannot|can['’]t|wasn['’]t\s+able\s+to|"
        r"was\s+not\s+able\s+to)\s+(?:been\s+able\s+to\s+)?(?:confirm|verify)\w*\s*|\bunconfirmed\b\s*|"
        r"\bpas\s+(?:pu|réussi\s+à)\s+(?:confirmer|vérifier)\s*|"
        r"\bn[ãa]o\s+(?:consegui|conseguimos|foi\s+poss[íi]vel)\s+(?:confirmar|verificar)\s*", re.I)),
    ("no_data", re.compile(
        r"\b(?:we|nobody|no\s+one|i)\s+(?:(?:do|does)\s+not|don['’]t|doesn['’]t)?\s*know(?:s)?\s+(?:yet|quite)\b\s*|"
        r"\bnobody\s+(?:in\s+\w+\s+)?(?:yet\s+)?knows\b(?!\s+(?:you|you['’]re|you\s+are|it['’]s\s+you))\s*|\bnot\s+enough\b|\black\s+of\s+data\b|"
        r"\bno\s+(?:reliable\s+|published\s+)?data\b|\bnot\s+going\s+to\s+give\s+you\s+a\s+number\b|"
        r"\bon\s+ne\s+sait\s+pas\b\s*|\bpersonne\s+ne\s+sait\b\s*|\bpas\s+(?:encore\s+)?(?:de|assez\s+de)\s+donn[ée]es\b|"
        r"\bn[ãa]o\s+sabemos\b\s*|\bningu[ée]m\s+sabe\b\s*|\bn[ãa]o\s+h[áa]\s+dados\b|\bsem\s+dados\b", re.I)),
    ("estimate", re.compile(r"\beducated\s+guess\b|\bestimation\b|\bestimativa\b|\bpalpite\b", re.I)),
]


def unverified(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        t = _text(raw)
        if re.match(r"\s*(?:where|if|when|whenever|quand|si|quando|se)\b", t, re.I):
            continue  # "Where it is an educated guess, I will tell you": promessa, não o palpite
        for verdict, pat in _UNSURE:
            m = pat.search(t)
            if not m:
                continue
            after = re.sub(r"^(?:quite|exactly|yet|for\s+sure)\s+", "", t[m.end():].strip(" ,.;:"), flags=re.I)
            before = re.sub(r"[,;]?\s*(?:so|and|but|donc|então)?\s*$", "", t[:m.start()])
            prev = _text(ss[i - 1]) if i > 0 else ""
            prev = re.sub(r"^(?:but|so|and|now)\s+(?:so\s+far,?\s+)?", "", prev, flags=re.I)
            if verdict == "no_data" and m.group(0).lower().startswith("not enough"):
                options = [t[m.start():]]
            else:
                # o que vem depois da pista; senão o que vem antes ("I have not seen a price list, so I am not
                # going to give you a number"); senão a frase anterior; senão a frase inteira
                options = [after, before, prev, t]
            clause = ""
            for opt in options:
                c = _trim_words(opt, 14)
                # "As for how reliable they will be after ten years, the honest answer is" -> sem o rabo
                c = re.sub(r"[,;]?\s*(?:and\s+)?(?:the\s+)?(?:honest|short|simple|real)\s+answer\s+is$|\s+(?:is|are|was)$",
                           "", c, flags=re.I).strip(" ,;.")
                if len(c.split()) >= 3:
                    clause = c
                    break
            if not clause:
                continue
            return _det("unverified", i, i, 5, {"caption": clause[:1].upper() + clause[1:], "subtitle": verdict})
    return None


# ---------------------------------------------------------------- exploded
_TEARDOWN = re.compile(r"\b(?:teardown\s+team\s+took|took\s+(?:[\w'’-]+\s+){0,4}apart|taken\s+(?:[\w'’-]+\s+){0,4}apart|"
                       r"taking\s+(?:[\w'’-]+\s+){0,4}apart|dismantl\w*|stripped\s+down|démont[ée]\w*|désoss[ée]\w*|"
                       r"desmont\w*|rozebra\w*)", re.I)
_EV_PARTS = re.compile(r"\b(battery(?:\s+pack)?|motor|inverter|gearbox|power\s+chips?|cells|screens?|busbars|"
                       r"modules|wiring|connectors|batterie|moteur|onduleur|bo[iî]te|bateria|inversor|c[âa]mbio|"
                       r"chicote)\b", re.I)


def exploded(ss: list[str], subject: str):
    from .magnet_detect import _areas
    from .subject import BRANDS

    for i, raw in enumerate(ss):
        t = _text(raw)
        m = _TEARDOWN.search(t)
        if not m or re.search(r"\b(?:nobody|no\s+one|never|not|personne|jamais|ningu[ée]m|nunca)\b", t[:m.start()], re.I):
            continue  # "Nobody has taken a BYD apart in a British garage" não é desmontagem
        parts: list[str] = []
        for s in ss:
            st = _text(s)
            for p in [m.group(1) for m in _EV_PARTS.finditer(st)] + _areas(st):
                p = p.lower()
                if p not in parts and not any(p in x or x in p for x in parts):
                    parts.append(p)
        car = ""
        brands = "|".join(sorted(BRANDS, key=len, reverse=True))
        m = re.search(rf"\b(?i:{brands})\s+[A-Z0-9][\w-]*(?:\s+\d+)?", t)
        if m:
            car = m.group(0)
        return _det("exploded", i, i, 4, {"caption": car, "points": [{"text": p} for p in parts[:4]]})
    return None


STORY_DETECTORS = (steps, coverage, fromto, unverified, source, exploded)
