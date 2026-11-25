"""Pistas na fala para os templates de dado da edição magnética (``motion/templates_magnet.py``).

Cada detector lê as frases da cena já com os números escritos (``spoken.screen_text``: "thirty-nine per
cent" -> "39%") e devolve o template com os dados copiados da fala (nunca inventados):

* ``percentbar`` — "39% of owners reported a fault", "Reliability rating, 76.4%".
* ``peoplegrid`` — "three in ten", "one owner in five", "nearly three repairs in every ten".
* ``receipt``    — conserto com valor: "cost the owner more than £1,500", "paid between £51 and £500".
* ``downtime``   — tempo parado: "off the road for more than a week", "fixed in a day or less".
* ``warning``    — o defeito principal: "the biggest single area was non-engine electrics".

As pistas valem em inglês, francês e português: "entre 1 500 et 3 000 euros" (a moeda depois do número,
milhar com espaço), "un propriétaire sur trois", "Le défaut : une surconsommation d'huile", "23 pour cent".
"""

from __future__ import annotations

import re

from .spoken import screen_text, word_int

_PCT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*%")  # "0.37%" inteiro (não o "37%" do fim)
_PCT_WORDS = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*(?:pour\s*cent|por\s*cento|procent\w*)\b", re.I)
_FRACTION = re.compile(r"\b([a-z]+|\d{1,2})(?:\s+(owners?|people|drivers|repairs?|cars?|faults?|of\s+them))?"
                       r"\s+(?:in|out\s+of)\s+(?:every\s+)?([a-z]+|\d{1,2})\b", re.I)
# "un propriétaire sur trois", "une voiture sur cinq", "sept sur dix", "um em cada três", "3 em cada 10"
_FRACTION_FR_PT = re.compile(r"\b(\w+|\d{1,2})((?:\s+[\w'’-]+){0,2}?)\s+(?:sur|em\s+cada|de\s+cada)\s+(\w+|\d{1,2})\b",
                             re.I)
_MONEY = re.compile(r"(?:R\$|C\$|[£€$])\s?(\d[\d,]*(?:\.\d+)?)")
_NUM = r"\d{1,3}(?:[   .]\d{3})+|\d+"
_CUR = r"(?:€|euros?|reais|real|zł|złotych|zlotych|złote)"
# "1 500 euros", "3.000 reais", "entre 1 500 et 3 000 euros", "de 70 à plus de 100 euros", "od 2 000 do 5 000 zł"
_MONEY_AFTER = re.compile(rf"(?:\b(?:entre|de|between|od)\s+({_NUM})\s+(?:et|e|à|a|and|to|do)\s+(?:plus\s+de\s+|mais\s+de\s+)?)?"
                          rf"\b({_NUM})(?:,\d+)?\s*{_CUR}(?!\w)", re.I)
_REPAIR = re.compile(r"\b(?:repairs?|repaired|replace(?:ment|d)?|fix(?:ed|ing)?|bills?|cost(?:s|ing)?|paid|pay|charged?|invoice|"
                     r"workshop|garage|warranty|faults?|asked\s+for|"
                     r"conserto|reparo|oficina|trocar|troca|substituir|conta\s+de|"
                     r"réparation|réparer|facture|devis|remplacer|remplacement|changer|comptez|compter|lâche|casse|"
                     r"naprawa|wymiana)\b", re.I)
# as palavras de _REPAIR que falam de conserto mesmo (sem "cost", "pay", "paid", "charged")
_REPAIR_STRONG = re.compile(r"\b(?:repairs?|repaired|replace(?:ment|d)?|fix(?:ed|ing)?|bills?|invoice|workshop|garage|"
                            r"faults?|conserto|reparo|oficina|trocar|troca|substituir|conta\s+de|réparation|réparer|"
                            r"facture|devis|remplacer|remplacement|naprawa|wymiana)\b", re.I)
_DOWN_CONTEXT = re.compile(r"\b(?:workshop|garage|off\s+the\s+road|out\s+of\s+action|without\s+(?:the|their|your)\s+car|"
                           r"fixed|sorted|resolved|repaired|put\s+right|in\s+the\s+shop|waiting|dealer|"
                           r"immobilisée?s?|immobilisé|au\s+garage|sans\s+voiture|parado|parada|na\s+oficina|"
                           r"sem\s+carro)\b", re.I)
_DOWNTIME = [
    (re.compile(r"\b(?:more\s+than|over|longer\s+than|at\s+least)\s+a\s+month\b|\bplus\s+d['’]un\s+mois\b|"
                r"\bmais\s+de\s+um\s+m[eê]s\b", re.I), 31),
    (re.compile(r"\b(?:a\s+)?fortnight\b|\btwo\s+weeks\b|\bdeux\s+semaines\b|\bquinze\s+jours\b|\bduas\s+semanas\b",
                re.I), 14),
    (re.compile(r"\b(?:more\s+than|over|longer\s+than|at\s+least)\s+a\s+week\b|\bplus\s+d['’]une\s+semaine\b|"
                r"\bmais\s+de\s+uma\s+semana\b", re.I), 8),
    (re.compile(r"\b(?:in|inside|within)\s+a\s+(?:single\s+)?day(?:\s+or\s+less)?\b|\bthe\s+same\s+day\b|"
                r"\ble\s+jour\s+même\b|\bno\s+mesmo\s+dia\b", re.I), 1),
    (re.compile(r"\b(\d{1,2})\s+(?:days|jours|dias)\b", re.I), -1),
    (re.compile(r"\b(\d)\s+(?:weeks|semaines|semanas)\b", re.I), -7),
]
_AREA_WORDS = (r"electrics|electrical\s+system|electronics|(?:12|twelve)-volt\s+battery|battery|batteries|engine|"
               r"brakes|gearbox|transmission|suspension|infotainment(?:\s+system)?|sat-nav|air\s+conditioning|"
               r"hybrid\s+system|hybrid\s+battery(?:\s+pack)?|electric\s+motor|motor|sensors|bodywork|steering|"
               r"fuel\s+system|exhaust|clutch|turbo|injectors?|DPF|(?:diesel\s+)?particulate\s+filter|EGR\s+valve")
_AREA = re.compile(rf"\b((?:non-engine|engine|the|its)\s+)?({_AREA_WORDS})\b", re.I)
# francês e português: o nome da peça como foi falado ("chaîne de distribution", "surconsommation d'huile")
_AREA_FR_PT = re.compile(
    r"\b((?:sur)?consommation\s+d['’]huile|cha[iî]ne(?:\s+de\s+distribution)?|courroie(?:\s+(?:de\s+distribution|humide|"
    r"d['’]accessoires))?|embrayages?|m[ée]catronique|turbo(?:compresseur)?|injecteurs?|pompe\s+(?:à|a)\s+"
    r"(?:carburant|eau|huile|injection)(?:\s+haute\s+pression)?|pompe\s+haute\s+pression|réservoir\s+d['’]adblue|"
    r"adblue|filtre\s+(?:à|a)\s+particules|vanne\s+egr|joint\s+de\s+culasse|volant\s+moteur(?:\s+bi-?masse)?|"
    r"bo[iî]te\s+(?:de\s+vitesses|pilotée|robotisée|automatique)|actionneur|batterie|freins|électronique|"
    r"circuit\s+de\s+refroidissement|"
    r"consumo\s+(?:excessivo\s+)?de\s+[óo]leo|corrente\s+de\s+distribui[çc][ãa]o|correia\s+dentada|correia|"
    r"embreagem|c[âa]mbio(?:\s+automatizado)?|bomba\s+de\s+combust[íi]vel|injetores|bateria|freios|"
    r"eletr[ôo]nica|suspens[ãa]o|junta\s+do\s+cabe[çc]ote)\b", re.I)
_MAIN = re.compile(r"\b(?:biggest|main|most\s+common|worst|single\s+area|trouble\s+spot|complaints?|"
                   r"problem\s+area|area\s+was|top\s+(?:fault|problem|complaint|issue|concern)s?|"
                   r"(?:fault|problem)\s+group)\b", re.I)
_MAIN_FR_PT = re.compile(r"\b(?:le|son|leur|o|seu)\s+(?:gros\s+|grand\s+|principal\s+|maior\s+)?"
                         r"(?:problème|défaut|souci|point\s+faible|talon\s+d['’]achille|problema|defeito|ponto\s+fraco)\b"
                         r"|\b(?:le|o)\s+(?:premier|second|deuxième|primeiro|segundo)\s*:|\bfragile\b|\bfrágil\b",
                         re.I)


def _lang() -> str:
    from .cues import language

    return language()


def _text(raw: str) -> str:
    """Como vai escrito na tela: números por extenso viram algarismos (inglês) e "23 pour cent" vira "23%"."""
    t = screen_text(raw, _lang())
    return _PCT_WORDS.sub(lambda m: m.group(1) + "%", t)


def _pct_value(m: re.Match) -> float:
    return float(m.group(1).replace(",", "."))


def _amount(raw: str) -> float:
    return float(re.sub(r"[   .]", "", raw))


def _clause(text: str, start: int, end: int, max_words: int = 7) -> str:
    """O pedaço da frase em volta do número que diz o que ele é."""
    after = re.split(r"[,;:!?]|\.(?!\d)", text[end:])[0].strip()
    before = next((p.strip() for p in reversed(re.split(r"[,;:!?]|\.(?!\d)", text[:start])) if p.strip()), "")
    words = after.split()
    if len(words) >= 2:
        return " ".join(words[:max_words])
    return " ".join(before.split()[-max_words:])


def _detection(kind: str, i: int, priority: int, fields: dict):
    from .auto_templates import Detection

    return Detection(kind, i, i, priority, fields)


def _areas(text: str) -> list[str]:
    """As peças/defeitos citados, na ordem da fala, em qualquer idioma."""
    found = [(m.start(), m.group(1)) for m in _AREA_FR_PT.finditer(text)]
    found += [(m.start(), m.group(0)) for m in _AREA.finditer(text)]
    out: list[str] = []
    for _, a in sorted(found):
        a = re.sub(r"^(?:the|its)\s+", "", a.strip(), flags=re.I)
        if a and not any(a.lower() in x.lower() or x.lower() in a.lower() for x in out):
            out.append(a)
    return out


def _short_name(subject: str, limit: int = 22) -> str:
    """O carro no cabeçalho da nota cabendo numa linha: "Volkswagen / Audi / Seat / Skoda DSG" -> "Volkswagen DSG"."""
    name = (subject or "").strip()
    if " / " in name:  # a 1ª marca + o que vem depois da última ("Citroën / Peugeot boîte pilotée")
        brands = name.split(" / ")
        name = " ".join([brands[0].split()[0]] + brands[-1].split()[1:])
    if len(name) > limit:
        name = " ".join(name.split()[:2])
    return name[:limit]


# "15% less to build", "25% more than petrol", "come down by 10%", "a cost advantage of 25%": é diferença,
# não parte de um todo; uma barra enchendo até 15% diria outra coisa
_RELATIVE_AFTER = re.compile(r"^\s*(?:less|more|cheaper|dearer|longer|shorter|higher|lower|faster|slower|bigger|"
                             r"smaller|fewer|extra|up|down|moins|plus|mais|menos)\b", re.I)
_RELATIVE_BEFORE = re.compile(r"\b(?:by|gap|difference|advantage|discount|increase|rise|drop|fall|cut|saving|"
                              r"cheaper|rose|fell|grew|dropped|declined|decreased|increased|climbed|jumped|"
                              # "sales were down 37%", "it's down another 68%", "after plummeting 75%": variação
                              r"down|plummet\w*|plung\w*|slump\w*|falling|fallen|dropping|rising|risen|soar\w*|surg\w*|"
                              r"baiss\w*|hausse|caiu|subiu)\b(?:\s+\w+){0,3}\s*$", re.I)
_TRIM = re.compile(r"^(?:and|but|so|then|or|e|mas|et|mais)\s+|\s+(?:at|around|about|of|was|is|were|are|the|a|an|by|"
                   r"nearly|roughly|almost|up|to|with|than|just|only|over|under|and|some|just|scored|got|"
                   r"em|de|do|da|cerca|quase|aproximadamente|mais|é|são|foi|com|"  # pt: "falam em cerca de"
                   r"environ|près|est|sont)$", re.I)  # fr
# "Estimativas do próprio governo falam em cerca de 85%": a oração só diz quem falou; o assunto do número está
# na frase anterior ("compra lá fora a grande maioria do adubo que usa")
_SAYS = re.compile(r"\b(?:falam|fala|dizem|diz|apontam|aponta|estimam|estima|indicam|mostram|say|says|said|estimate|"
                   r"estimates|suggest|suggests|show|shows|disent|dit|estiment|indiquent)\b", re.I)


def _trim(label: str) -> str:
    """ "the figure for a Tesla Model 3 at around" -> "the figure for a Tesla Model 3"."""
    prev = None
    while prev != label:
        prev, label = label, _TRIM.sub("", label.strip()).strip()
    return label


def percentbar(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        t = _text(raw)
        m = _PCT.search(t)
        if not m:
            continue
        value = _pct_value(m)
        if not 0.5 <= value <= 100:  # "0.37% of insured CR-Vs": a barra nem aparece
            continue
        if re.search(r"\b(?:quarter|half|third|tenth|fraction)s?\s+of\s+(?:a\s+|one\s+)?$", t[:m.start()], re.I):
            continue  # "a quarter of 1%" não é 1%
        if re.search(r"(?:\b[a-z]+|\d+)\s+(?:to|or|-|–)\s*$", t[:m.start()], re.I) and \
                re.search(r"(?:\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten))\s+(?:to|or|-|–)\s*$",
                          t[:m.start()], re.I):
            continue  # "around two to five percent": faixa, não uma parte do todo
        if _RELATIVE_AFTER.match(t[m.end():]) or _RELATIVE_BEFORE.search(t[:m.start()]):
            continue
        lab = _clause(t, m.start(), m.end())
        if "%" in lab:  # "98% with a 5% fault rate": o outro número confunde; vale o que vem antes
            lab = next((p.strip() for p in reversed(re.split(r"[,;:!?]|\.(?!\d)", t[:m.start()])) if p.strip()), "")
            lab = " ".join(lab.split()[-7:])
        lab = _trim(lab)
        if _SAYS.search(lab) and i > 0:
            prev = re.split(r"[,;:]", _text(ss[i - 1]).rstrip(".!?"))[-1]
            lab = _trim(" ".join(prev.split()[-8:])) or lab
        if len(lab.split()) < 2:
            continue
        return _detection("percentbar", i, 4, {"number": {"value": value, "suffix": "%", "decimals": 1 if value % 1 else 0},
                                               "caption": lab})
    return None


def _fractions(t: str):
    if _lang() == "en":
        for m in _FRACTION.finditer(t):
            yield m, m.group(2) or ""
        return
    for m in _FRACTION_FR_PT.finditer(t):
        yield m, (m.group(2) or "").strip()


def peoplegrid(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        t = _text(raw)
        for m, noun in _fractions(t):
            k, n = word_int(m.group(1)), word_int(m.group(3))
            if k is None or n is None or not (0 < k < n <= 20) or n < 2:
                continue
            if _lang() != "en" and re.search(r"\d", m.group(1) + m.group(3)) and not noun:
                continue  # "1 000 sur 2 000", "notes de 7 sur 10": só fração de gente/carro
            after = re.split(r"[,;:!?]|\.(?!\d)", t[m.end():])[0].strip()
            lab = " ".join(f"{noun} {after}".split()[:8]) if after else (noun or _clause(t, m.start(), m.end()))
            if len(lab.split()) < 2:
                lab = ""  # "more than": melhor só o "1 IN 10" grande
            return _detection("peoplegrid", i, 5, {"number": {"value": k, "total": n}, "caption": lab})
    return None


def _money(t: str) -> list[tuple[float, float, dict]]:
    """(valor, menor da faixa, prefixo/sufixo) de cada quantia citada."""
    out = []
    prev_end, prev_value = -1, 0.0
    for m in _MONEY.finditer(t):
        sym = t[m.start():m.start() + 2] if t[m.start():m.start() + 2] in ("R$", "C$") else t[m.start()]
        if sym == "$":
            from .cues import dollar_symbol

            sym = dollar_symbol()
        value = float(m.group(1).replace(",", ""))
        if prev_end >= 0 and re.fullmatch(r"\s*(?:to|and|-|–)\s*", t[prev_end:m.start()]) and prev_value < value:
            out[-1] = (value, prev_value, {"prefix": sym})  # "£800 to £2,000": uma faixa só
        else:
            out.append((value, 0.0, {"prefix": sym}))
        prev_end, prev_value = m.end(), value
    for m in _MONEY_AFTER.finditer(t):
        cur = t[m.end(2):m.end()].strip().lower()
        style = ({"prefix": "R$"} if cur.startswith(("real", "reais")) else
                 {"suffix": " zł"} if cur.startswith("z") else {"suffix": " €"})
        low = _amount(m.group(1)) if m.group(1) else 0.0
        out.append((_amount(m.group(2)), low, style))
    return out


def _brand_named(text: str) -> str:
    """A marca citada na frase ("on the Fords" -> "Ford"), se houver só uma."""
    from .subject import BRANDS
    from .text import normalize

    found: list[str] = []
    for w in re.findall(r"\b[A-Z][\w-]+", text):
        base = w[:-1] if w.endswith("s") and normalize(w[:-1]) in BRANDS else w
        if normalize(base) in BRANDS and base not in found:
            found.append(base)
    return found[0] if len(found) == 1 else ""


# "costs C$200 more than", "within a hundred", "saved a hundred bucks": diferença de preço, não conta de oficina
_COMPARE = re.compile(r"\d[\d,.]*\s*(?:€|zł)?\s+(?:more|less|cheaper|extra)\b|"
                      r"\b(?:dollars?|pounds?|euros?|bucks|quid)\s+(?:more|less|cheaper|extra)\b|"
                      r"\b(?:within|difference|saved?|saving|de\s+plus|de\s+moins|a\s+mais|a\s+menos)\b", re.I)


_FUEL = re.compile(r"\b(?:fill(?:-up)?|tank(?:ful)?|litres?|liters?|pump|forecourt|plein|réservoir|pompe|tanque|"
                   r"abastec\w*|bomba|tankowanie|bak)\b", re.I)
_LITRES = re.compile(r"\d+(?:[.,]\d+)?\s*(?:-\s*)?(?:litres?|liters?|L)\b")
_FUEL_HEAD = {"en": "Fuel", "fr": "Carburant", "pt": "Combustível", "pl": "Paliwo"}


def receipt(ss: list[str], subject: str):
    from .rhythm import part_word

    for i, raw in enumerate(ss):
        t = _text(raw)
        amounts = _money(t)
        if not amounts or not _REPAIR.search(t) or _COMPARE.search(t):
            continue
        if re.search(r"\b(?:storage|store\s+them|subscription|membership|insurance|premium|discount|stockage|"
                     r"armazenagem|assinatura|seguro)\b", t, re.I) and not _areas(t):
            continue  # "storage can run from $50 to $250 a season": preço de serviço, não conserto
        if not _REPAIR_STRONG.search(t) and not _areas(t) and not _brand_named(t) and not subject \
                and not _FUEL.search(t):
            # só "cost"/"pay" sem peça, marca ou carro: preço de um produto/serviço ("the dealer's cost for that
            # treatment is $150", "say you pay $150 a year"), não a nota da oficina
            continue
        value, low, style = max(amounts, key=lambda x: x[0])
        if not 100 <= value <= 12000:  # conta de oficina de verdade (não "£50" de diferença nem o preço do carro)
            continue
        parts: list[str] = []
        for a in [a for s in ss for a in _areas(_text(s))]:  # "D P F" -> DPF  # as peças citadas na cena viram as linhas da nota
            if not any(a.lower() in x.lower() or x.lower() in a.lower() for x in parts):
                parts.append(a)
        for s in ss:
            p = part_word(s)
            if p and not any(p.lower() in x.lower() for x in parts):
                parts.append(p)
        number = {"value": value, **style}
        if 0 < low < value:
            number["low"] = low
        # conserto com valor: a nota da oficina diz mais que a etiqueta de preço ("La facture : entre 1 500
        # et 3 000 euros" era um "price" no vídeo do Bernard)
        named = _brand_named(t)  # "£800 on the Fords" na seção do PureTech: a conta é dos Ford
        caption = named if named and named.lower() not in subject.lower() else _short_name(subject)
        # "injectors ... depending on the engine": a peça é o injetor ("engine" é genérico demais)
        main = next((p for p in parts if p.lower() not in ("engine", "car", "moteur", "motor", "silnik")), parts[0]) \
            if parts else "Repair bill"
        fields = {"number": number, "caption": caption or main,
                  "points": [{"text": p} for p in parts[:3]]}
        fuel = _FUEL.search(t)
        if fuel and not _areas(t):  # "a fifty-five litre fill now costs £110": é a bomba, não a oficina
            from .cues import language

            kind = next((w for w in ("diesel", "petrol", "gazole", "essence", "gasolina", "etanol") if w in t.lower()), "")
            # o cabeçalho da nota é a legenda: "DIESEL" (ou "FUEL"), não "REPAIR"
            fields.update(caption=kind.capitalize() or _FUEL_HEAD.get(language(), "Fuel"),
                          points=[{"text": m.group(0)} for m in [_LITRES.search(t)] if m])
        return _detection("receipt", i, 8, fields)
    return None


def downtime(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        t = _text(raw)
        if not _DOWN_CONTEXT.search(t):
            continue
        for pat, days in _DOWNTIME:
            m = pat.search(t)
            if not m:
                continue
            if days < 0:
                days = int(m.group(1)) * -days
            if not 0 < days <= 31:
                continue
            # a oração com o tempo parado, terminando nele ("cars were off the road for more than a week")
            clause = re.split(r"[,;:!?]|\.(?!\d)", t[:m.end()])[-1]
            lab = " ".join(clause.split()[-9:])
            return _detection("downtime", i, 5, {"number": {"value": days}, "caption": lab})
    return None


def warning(ss: list[str], subject: str):
    for i, raw in enumerate(ss):
        t = _text(raw)
        main = _MAIN.search(t)
        if main and main.group(0).lower().startswith("most") and not re.match(
                r"\s+(?:faults?|problems?|complaints?|issues?|failures?|reasons?)\b", t[main.end():], re.I):
            main = None  # "two of the most common vans in Britain" não é defeito
        if main and (m := _AREA.search(t)):
            area = (m.group(1) or "").strip()
            area = (area + " " if area.lower() in ("non-engine", "engine") else "") + m.group(2)
        elif (cue := _MAIN_FR_PT.search(t)) and (near := list(_AREA_FR_PT.finditer(t))):
            # a peça mais perto da pista: "Le problème, c'est sa chaîne…", "Une pompe … fragile."
            m2 = min(near, key=lambda a: min(abs(a.start() - cue.end()), abs(cue.start() - a.end())))
            area = m2.group(1)
            if re.fullmatch(r"cha[iî]ne", area, re.I):
                area += " de distribution"
            area = area[0].upper() + area[1:]
        else:
            continue
        pct = _PCT.search(t)
        sub = f"{pct.group(1)}%" if pct else ""
        return _detection("warning", i, 7 if pct else 5, {"caption": area, "subtitle": sub})
    return None


MAGNET_DETECTORS = (warning, receipt, peoplegrid, downtime, percentbar)
