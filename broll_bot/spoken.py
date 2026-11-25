"""Números falados por extenso nos roteiros feitos para voz de IA ("Number ten.", "on sale since twenty
nineteen", "the Audi Q two", "the Mercedes G L E").

Só para DETECTAR (cabeçalho do ranking, anos, nome do carro). O texto do roteiro em si não muda: a
transcrição e o alinhamento continuam com as palavras como foram faladas.
"""

from __future__ import annotations

import re

# 1–20 (+ dezenas) em cada idioma, para "Number ten", "Numéro dix", "Número dez", "Numer dziesięć"
_SMALL: dict[str, int] = {}
for _lang_words in (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty",
    "zéro un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize dix-sept "
    "dix-huit dix-neuf vingt",
    "zero um dois três quatro cinco seis sete oito nove dez onze doze treze catorze quinze dezesseis dezessete "
    "dezoito dezenove vinte",
    "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete "
    "dieciocho diecinueve veinte",
    "zero jeden dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć jedenaście dwanaście trzynaście "
    "czternaście piętnaście szesnaście siedemnaście osiemnaście dziewiętnaście dwadzieścia",
):
    for _n, _w in enumerate(_lang_words.split()):
        _SMALL.setdefault(_w, _n)
_SMALL.update({"une": 1, "uma": 1, "duas": 2, "quatorze": 14, "dezasseis": 16, "dezassete": 17, "dezanove": 19,
               "una": 1, "tres": 3, "dieciseis": 16, "piec": 5, "szesc": 6, "dziewiec": 9, "dziesiec": 10})
_TENS_EN = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
            "ninety": 90}


def word_int(word: str) -> int | None:
    """ "ten" -> 10; "twenty-one" -> 21; "dix-huit" -> 18; "7" -> 7; senão None."""
    w = word.lower().strip(".,;:!?")
    if w.isdigit():
        return int(w)
    if w in _SMALL:
        return _SMALL[w]
    if w in _TENS_EN:  # "Blizzak W-S ninety"
        return _TENS_EN[w]
    parts = re.split(r"[-\s]", w)
    if len(parts) == 2 and parts[0] in _TENS_EN and parts[1] in _SMALL and 0 < _SMALL[parts[1]] < 10:
        return _TENS_EN[parts[0]] + _SMALL[parts[1]]
    return None


_TEEN_OR_TWENTY = r"(?:nineteen|twenty)"
_UNIT = r"(?:one|two|three|four|five|six|seven|eight|nine)"
_TWO_DIGIT = (rf"(?:(?:ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"
              rf"|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[-\s]{_UNIT})?|oh[-\s]{_UNIT})")
_YEAR_WORDS = re.compile(rf"\b({_TEEN_OR_TWENTY})[-\s]({_TWO_DIGIT})\b", re.I)
_YEAR_THOUSAND = re.compile(rf"\btwo[-\s]thousand(?:[-\s]and)?(?:[-\s]({_TWO_DIGIT}|{_UNIT}))?\b", re.I)


def _two_digit(text: str) -> int | None:
    t = text.lower().replace("-", " ").split()
    if t[0] == "oh" and len(t) == 2:
        return _SMALL.get(t[1])
    if len(t) == 1:
        return _TENS_EN.get(t[0], _SMALL.get(t[0]))
    if len(t) == 2 and t[0] in _TENS_EN and t[1] in _SMALL:
        return _TENS_EN[t[0]] + _SMALL[t[1]]
    return None


def spoken_years(text: str) -> str:
    """ "since twenty nineteen" -> "since 2019"; "twenty twenty-six" -> "2026"; "two thousand and nine" ->
    "2009"; "nineteen ninety-eight" -> "1998". Só anos de 1950 a 2039 (o resto fica como está)."""
    def repl(m: re.Match) -> str:
        century = 1900 if m.group(1).lower() == "nineteen" else 2000
        tail = _two_digit(m.group(2))
        year = century + tail if tail is not None else None
        return str(year) if year and 1950 <= year <= 2039 else m.group(0)

    def repl_thousand(m: re.Match) -> str:
        # "two thousand four hundred pounds", "more than two thousand cars": quantia, não ano
        after, before = m.string[m.end():], m.string[:m.start()]
        # "a hundred and fifty-two thousand pickups", "a hundred and two thousand": o "two thousand" é parte do
        # número, não o ano 2000
        if re.search(r"(?:-|\b(?:hundred|and|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety))\s*$", before, re.I):
            return m.group(0)
        if re.match(r"\s*(?:hundred|thousand|million|pounds?|quid|euros?|dollars?|miles|kilomet\w*|km|cars?|"
                    r"people|owners|units|vehicles|per\s*cent|percent)\b", after, re.I):
            return m.group(0)
        if re.search(r"\b(?:than|about|around|over|under|nearly|almost|roughly|some|costs?|paid|pay|spent|£|€|\$|"
                     r"as\s+(?:high|much|low|little)\s+as|at\s+least|up\s+to)\s*$",
                     before, re.I) or re.search(r"(?:pounds?|euros?|dollars?|quid)\s+(?:to|and|or)\s+$", before, re.I):
            return m.group(0)
        tail = _two_digit(m.group(1)) if m.group(1) else 0
        return str(2000 + tail) if tail is not None and tail < 40 else m.group(0)

    def repl_plural(m: re.Match) -> str:
        # "the twenty twenty-sevens arrive" -> "the 2027s"; "the nineteen nineties" -> "the 1990s"
        century = 1900 if m.group(1).lower() == "nineteen" else 2000
        tens, unit = (m.group(2) or "").lower(), m.group(3).lower()
        tail = (_TENS_EN.get(tens, 0) + _PLURAL_UNITS[unit]) if unit in _PLURAL_UNITS else _PLURAL_TENS.get(unit)
        year = century + tail if tail is not None else None
        return f"{year}s" if year and 1950 <= year <= 2039 else m.group(0)

    text = _YEAR_PLURAL.sub(repl_plural, text)
    return _YEAR_THOUSAND.sub(repl_thousand, _YEAR_WORDS.sub(repl, text))


_PLURAL_UNITS = {"ones": 1, "twos": 2, "threes": 3, "fours": 4, "fives": 5, "sixes": 6, "sevens": 7, "eights": 8,
                 "nines": 9}
_PLURAL_TENS = {"tens": 10, "twenties": 20, "thirties": 30, "forties": 40, "fifties": 50, "sixties": 60,
                "seventies": 70, "eighties": 80, "nineties": 90}
_YEAR_PLURAL = re.compile(rf"\b({_TEEN_OR_TWENTY})[-\s](?:(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-)?"
                          rf"({'|'.join(_PLURAL_UNITS)}|{'|'.join(_PLURAL_TENS)})\b", re.I)


# marcas que a voz soletra ("B M W X three"): separa a marca do modelo que vem grudado nas letras
_BRAND_LETTERS = ("BMW", "BYD", "GWM", "JAC", "VW", "MG", "DS")


def clean_model_name(name: str) -> str:
    """Nome do carro escrito para a voz -> como se escreve: "Mercedes G L E" -> "Mercedes GLE";
    "Audi Q two" -> "Audi Q2"; "Volkswagen I D four" -> "Volkswagen ID4"; "Jaecoo Seven" -> "Jaecoo 7";
    "B M W X three" -> "BMW X3"."""
    name = hyphen_spelled(name)
    merged: list[str] = []
    spelled: set[int] = set()  # tokens que vieram de letras soltas ("B M W X" -> "BMWX")
    in_run = False  # o último token veio de letras soltas ("G L E")
    for tok in name.split():
        letter = len(tok) == 1 and tok.isalpha() and tok.isupper()
        if letter and in_run:
            merged[-1] += tok  # letras soltas viram a sigla
            spelled.add(len(merged) - 1)
            continue
        merged.append(tok)
        in_run = letter
    letters: list[str] = []
    for k, tok in enumerate(merged):
        # só separa a marca da sigla soletrada ("BMWX" -> "BMW X"); "DSG" escrito junto fica "DSG"
        brand = next((b for b in _BRAND_LETTERS if k in spelled and tok.startswith(b) and len(tok) > len(b)), "")
        letters += [brand, tok[len(brand):]] if brand else [tok]
    out: list[str] = []
    for tok in letters:
        n = word_int(tok) if out and not tok[:1].isdigit() else None
        if n is not None and 0 < n < 100 and tok.lower() not in ("one", "un", "um", "uno", "une", "a"):
            tok = str(n)
            if len(out[-1]) <= 3 and out[-1].isalpha() and out[-1].isupper() and out[-1] not in _BRAND_LETTERS:
                out[-1] += tok  # "Q 2" -> "Q2", "ID 4" -> "ID4"
                continue
        out.append(tok)
    return " ".join(out)


# ------------------------------------------------------------ números na tela
# a voz de IA lê "seventy-six point four per cent": na tela tem que aparecer "76.4%"
_EN_UNITS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                         "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_EN_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
_SCALE_WORDS = ("hundred", "thousand", "million", "billion")


def _is_num_word(w: str) -> bool:
    parts = w.lower().split("-")
    return all(p in _EN_UNITS or p in _TENS_EN or p in _EN_SCALES for p in parts)


def _runs(text: str) -> list[tuple[int, int]]:
    """Trechos com um número falado: "thirty-four thousand, seven hundred and seventy-two", "seventy-six point
    four", "ninety-eight and a half", "a thousand". "fifty-one and five hundred" são dois números."""
    words = list(_WORD.finditer(text))
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(words):
        w = words[i]
        lw = w.group(0).lower()
        if not _is_num_word(lw):
            i += 1
            continue
        start, end, last = w.start(), w.end(), lw
        if lw in _SCALE_WORDS and i > 0 and words[i - 1].group(0).lower() == "a" \
                and text[words[i - 1].end():start].strip() == "":
            start = words[i - 1].start()  # "a thousand pounds"
        j = i + 1
        after_and = False  # "three hundred and five" acabou: "hundred" depois disso é outro número
        top = _EN_SCALES.get(lw, 0)  # maior escala já dita ("hundred" em "one hundred and twenty-five")
        while j < len(words):
            gap = text[end:words[j].start()]
            nxt = words[j].group(0).lower()
            # "three hundred and five hundred": dois números; "one hundred and twenty-five thousand": um só
            if after_and and any(p in _SCALE_WORDS for p in nxt.split("-")) and _EN_SCALES.get(nxt, 0) <= top:
                break
            top = max(top, _EN_SCALES.get(nxt, 0))
            if _is_num_word(nxt) and (gap.strip() == "" or gap.strip() == "," and last in _SCALE_WORDS):
                end, last, j = words[j].end(), nxt, j + 1
                continue
            # "two hundred and twenty-five", "a thousand and nine": "and" só depois de centena/milhar; em
            # "three hundred and five hundred" o "and" separa dois números
            if nxt == "and" and gap.strip() == "" and last.split("-")[-1] in _SCALE_WORDS and j + 1 < len(words) \
                    and _is_num_word(words[j + 1].group(0)) and text[words[j].end():words[j + 1].start()].strip() == "" \
                    and not any(p in _SCALE_WORDS for p in words[j + 1].group(0).lower().split("-")) \
                    and not (j + 2 < len(words) and _EN_SCALES.get(words[j + 2].group(0).lower(), 0) <= top
                             and words[j + 2].group(0).lower() in _SCALE_WORDS
                             and text[words[j + 1].end():words[j + 2].start()].strip() == ""):
                end, last, j = words[j + 1].end(), words[j + 1].group(0).lower(), j + 2
                after_and = True
                continue
            if nxt == "point" and gap.strip() == "":
                k = j + 1
                while k < len(words) and words[k].group(0).lower() in _EN_UNITS \
                        and _EN_UNITS[words[k].group(0).lower()] < 10 and text[words[k - 1].end():words[k].start()].strip() == "":
                    k += 1
                if k > j + 1:
                    end, j = words[k - 1].end(), k
                    last = "point"
                break
            break
        half = re.match(r"\s+and\s+a\s+half(?:\s+(?:thousand|million|billion))?\b", text[end:], re.I)
        if half:
            end += half.end()
            while j < len(words) and words[j].start() < end:  # "and a half thousand" já entrou no número
                j += 1
        out.append((start, end))
        i = j
    return out


_PERCENT = re.compile(r"^\s*(?:per\s*cent|percent)\b", re.I)
_DEGREES = re.compile(r"^\s*degrees?(?:\s+(Celsius|Centigrade|Fahrenheit))?\b", re.I)
_MONEY = {"pounds": "£", "pound": "£", "quid": "£", "euros": "€", "euro": "€", "dollars": "$", "dollar": "$"}
_MONEY_AFTER = re.compile(r"^\s*(?:US\s+|(Canadian|CA)\s+)?(pounds?|quid|euros?|dollars?|bucks)\b", re.I)


def _en_value(run: str) -> float | None:
    """ "thirty-four thousand, seven hundred and seventy-two" -> 34772; "fifteen hundred" -> 1500;
    "seventy-six point four" -> 76.4; "ninety-eight and a half" -> 98.5."""
    text = run.lower().replace(",", " ")
    if text.startswith("a "):
        text = "one " + text[2:]
    scaled = re.fullmatch(r"(.+?)\s+and a half\s+(thousand|million|billion)", text.strip())
    if scaled:  # "ten and a half thousand" -> 10500
        base = _en_value(scaled.group(1))
        return None if base is None else (base + 0.5) * _EN_SCALES[scaled.group(2)]
    half = text.endswith("and a half")
    if half:
        text = text[: -len("and a half")]
    whole, _, frac = text.partition(" point ")
    parts = whole.split()
    if not frac and len(parts) == 2 and parts[0] in _EN_UNITS and 0 < _EN_UNITS[parts[0]] < 10:
        tail = _two_digit_en(parts[1])
        if tail is not None:  # "seven ninety-five" é 795 (o preço falado), não 7 + 95
            return float(_EN_UNITS[parts[0]] * 100 + tail)
    if not frac and len(parts) >= 3 and "hundred" in parts[:-1]:
        cents = _two_digit_en(parts[-1])
        dollars = _en_value(" ".join(parts[:-1]))
        if cents is not None and dollars is not None and 10 <= dollars % 100 <= 99 and dollars == int(dollars):
            # "two hundred and thirty-seven fifty" é 237,50 (a multa falada), não 237 + 50 = 287
            return round(dollars + cents / 100, 2)
    total, current = 0, 0
    seen = False
    for w in re.split(r"[\s-]+", whole):
        if not w or w == "and":
            continue
        if w in _EN_UNITS:
            current += _EN_UNITS[w]
        elif w in _TENS_EN:
            current += _TENS_EN[w]
        elif w == "hundred":
            current = max(1, current) * 100
        elif w in _EN_SCALES:
            total += max(1, current) * _EN_SCALES[w]
            current = 0
        else:
            return None
        seen = True
    if not seen:
        return None
    value: float = total + current
    if frac:
        digits = "".join(str(_EN_UNITS[w]) for w in frac.split() if w in _EN_UNITS and _EN_UNITS[w] < 10)
        if digits:
            value = float(f"{int(value)}.{digits}")
    if half:
        value += 0.5
    return value


def _two_digit_en(word: str) -> int | None:
    """ "ninety-five" -> 95, "fifty" -> 50, "twelve" -> 12 (10 a 99); senão None."""
    w = word.lower()
    if w in _EN_UNITS and _EN_UNITS[w] >= 10:
        return _EN_UNITS[w]
    if w in _TENS_EN:
        return _TENS_EN[w]
    a, _, b = w.partition("-")
    if a in _TENS_EN and b in _EN_UNITS and 0 < _EN_UNITS[b] < 10:
        return _TENS_EN[a] + _EN_UNITS[b]
    return None


def _fmt(value: float) -> str:
    if value == int(value):
        n = int(value)
        return f"{n:,}"  # "1,500", "34,772"
    return f"{value:g}"


def numbers_to_digits(text: str, lang: str = "en") -> str:
    """Números falados por extenso viram algarismos no que vai escrito na tela (só inglês, que é como
    a voz de IA lê): "seventy-six point four per cent" -> "76.4%"; "fifteen hundred pounds" -> "£1500";
    "two hundred and twenty-five models" -> "225 models"; "twenty nineteen" -> "2019".
    Número pequeno solto fica por extenso ("one owner", "the two things"); com % ou moeda vira algarismo."""
    if not text or (lang or "en").split("-")[0].lower() != "en":
        return text
    text = spoken_years(_hyphen_money(text))
    out: list[str] = []
    pos = 0
    for start0, end0 in _runs(text):
        run = text[start0:end0]
        value = _en_value(run)
        if value is None:
            continue
        after = text[end0:]
        pct = _PERCENT.match(after)
        money = _MONEY_AFTER.match(after)
        deg = _DEGREES.match(after)
        pence = re.match(r"\s*pence\b", after, re.I)
        minus = re.search(r"\bminus\s+$", text[:start0], re.I)  # "minus thirty" (temperatura) -> "-30"
        if minus and word_int(text[:minus.start()].split()[-1] if text[:minus.start()].split() else "") is not None:
            minus = None  # "ten minus two": conta, não temperatura
        if re.search(r"\b(?:couple|few|several)\s+of\s+$|\b(?:a\s+few|several|many|few)\s+$", text[:start0], re.I) and \
                run.strip().lower() in ("hundred", "thousand"):
            continue  # "a couple of hundred dollars": não é 100
        if run.strip().lower().startswith("a ") and re.search(r"\b(?:half|quarter|third)\s+(?:of\s+)?$", text[:start0], re.I):
            continue  # "half a million", "a quarter of a million": fica por extenso (não "half 1 million")
        words = len(re.split(r"[\s-]+", run.strip()))
        # "one owner", "seven in ten", "the two things": número pequeno solto fica por extenso
        if value < 13 and words == 1 and not (pct or money or deg or minus or pence):
            continue
        num = _fmt(value)
        if value != int(value) and " point " not in run.lower() and "half" not in run.lower():
            num = f"{value:,.2f}"  # "two hundred and thirty-seven fifty" -> "237.50" (não "237.5")
        scale = next((w for w in ("billion", "million") if re.search(rf"\b{w}\b", run, re.I)), "")
        if scale and value >= _EN_SCALES[scale]:  # "two and a half billion pounds" -> "£2.5 billion"
            num = f"{value / _EN_SCALES[scale]:g} {scale}"
        start, end = start0, end0
        if minus:
            num, start = "-" + num, minus.start()
        if deg:  # "seven degrees Celsius" -> "7°C"
            num += "°F" if (deg.group(1) or "").lower() == "fahrenheit" else "°C" if deg.group(1) else "°"
            end += deg.end()
        elif pct:
            num += "%"
            end += pct.end()
        elif money:
            from .cues import dollar_symbol

            word = money.group(2).lower()
            symbol = _MONEY.get(word, "$")
            if symbol == "$":  # "Canadian dollars" sempre C$; "dollars" num canal canadense também
                symbol = "C$" if money.group(1) else dollar_symbol()
                if symbol == "C$" and not money.group(1) and re.search(r"\b(?:American|Americans|U\.S\.?|US)\b", text):
                    symbol = "US$"  # "AAA estimated American drivers spend about three billion dollars"
            num = symbol + num
            end += money.end()
        out.append(text[pos:start])
        out.append(num)
        pos = end
    out.append(text[pos:])
    return _bare_thousands(_percent_ranges(_money_cents(_money_ranges("".join(out)))))


_BARE_THOUSANDS = re.compile(r"(\b(?:to|and|or|about|around|roughly|nearly|almost|at)\s)(\d{1,3}(?:,\d{3})+)(?=\s+(?:on|off|in|for|after|before|at|"
                             r"from|with|including|plus)\b|\s*[.,;!?]|\s*$)")


def _bare_thousands(text: str) -> str:
    """O segundo valor sem a palavra "dollars" leva a moeda do primeiro: "from C$5,000 to 4,000 on January first"
    -> "to C$4,000"; "C$10,000 after tax on the bZ Limited, and 7,000 on the bZ Woodland" -> "C$7,000"."""
    def repl(m: re.Match) -> str:
        before = re.findall(r"(C\$|US\$|[£€$])\d[\d,]*", text[:m.start()])
        return m.group(1) + before[-1] + m.group(2) if before else m.group(0)

    return _BARE_THOUSANDS.sub(repl, text)


def _percent_ranges(text: str) -> str:
    """ "around two to 5%" -> "around 2 to 5%" (o primeiro número da faixa também em algarismo)."""
    def repl(m: re.Match) -> str:
        n = word_int(m.group(1))
        return f"{n}{m.group(2)}{m.group(3)}" if n is not None and 0 <= n < float(m.group(3)[:-1]) else m.group(0)

    return re.sub(r"\b([a-z]+(?:-[a-z]+)?)(\s+(?:to|or|and)\s+)(\d+(?:\.\d+)?%)", repl, text, flags=re.I)


_HYPHEN_MONEY = re.compile(r"\b((?:[A-Za-z]+-)+)(pound|euro|dollar)\b")


def _hyphen_money(text: str) -> str:
    """ "a hundred-and-ten-pound tank" -> "a £110 tank"; "a fifty-pound note" -> "a £50 note"."""
    def repl(m: re.Match) -> str:
        parts = [p for p in m.group(1).strip("-").split("-")]
        if not all(p.lower() == "and" or _is_num_word(p) for p in parts):
            return m.group(0)
        value = _en_value(" ".join(parts))
        if value is None or value < 2:
            return m.group(0)
        sym = _MONEY.get(m.group(2).lower() + "s", "$")
        if sym == "$":
            from .cues import dollar_symbol

            sym = dollar_symbol()
        return sym + _fmt(value)

    return _HYPHEN_MONEY.sub(repl, text)


# o que vem depois de "£800 to 2,000" para ainda ser dinheiro (não "2,000 miles")
_AFTER_AMOUNT = r"(?=\s+(?:or|and|more|less|extra|a|per|each|every|for|on|in|to|depending|cheaper|dearer|off|back)\b|" \
                r"\s*[.,;:!?)]|\s*$)"


def _money_cents(text: str) -> str:
    """ "thirty-one pounds seventy more" -> "£31.70 more"; "£110 and one penny" -> "£110.01";
    "£800 to 2,000 or more" (eight hundred pounds to two thousand) -> "£800 to £2,000 or more"."""
    text = re.sub(rf"((?:C\$|[£€$])\d+) ([1-9]\d){_AFTER_AMOUNT}", r"\1.\2", text)
    text = re.sub(r"((?:C\$|[£€$])\d+) and (?:one|a) penny\b", r"\1.01", text)
    text = re.sub(r"((?:C\$|[£€$])\d+) and (\d) pence\b", r"\1.0\2", text)
    text = re.sub(r"((?:C\$|[£€$])\d+) and ([1-9]\d) pence\b", r"\1.\2", text)

    def rng(m: re.Match) -> str:
        sym, lo, sep, hi = m.groups()
        if float(hi.replace(",", "")) <= float(lo.replace(",", "")):
            return m.group(0)
        return f"{sym}{lo}{sep}{sym}{hi}"

    return re.sub(rf"(C\$|[£€$])(\d[\d,]*)(\s+(?:to|and|or)\s+)(\d[\d,]*){_AFTER_AMOUNT}", rng, text)


_RANGE_MONEY = re.compile(r"\b([A-Za-z]+(?:-[A-Za-z]+)*|\d[\d,.]*)(\s+(?:and|to)\s+|,\s+(?:or\s+)?even\s+)(C\$|[£€$])(\d[\d,]*)")


def _money_ranges(text: str) -> str:
    """ "between 51 and £500" -> "between £51 and £500"; "between two and £300" (two and three hundred
    pounds) -> "between £200 and £300"; "taking ten, even C$12,000 off" -> "C$10,000, even C$12,000"."""
    def repl(m: re.Match) -> str:
        first, sep, sym, second = m.groups()
        hi = int(second.replace(",", ""))
        if first[0].isdigit():
            return f"{sym}{first}{sep}{sym}{second}"
        n = word_int(first)
        if n is None or not 0 < n < 100:
            return m.group(0)
        scale = next((s for s in (1_000_000, 1000, 100) if hi >= s and hi % s == 0), 1)
        lo = n * scale if scale > 1 and n * scale < hi else n
        return f"{sym}{_fmt(lo)}{sep}{sym}{second}"

    return _RANGE_MONEY.sub(repl, text)


# siglas que a voz soletra e que valem sozinhas: "A B S" é ABS, mas "A B Y D Atto" é "a BYD Atto"
_KNOWN_ACRONYMS = {"BYD", "BMW", "SUV", "EV", "MG", "UBS", "GWM", "VW", "DS", "ABS", "DSG", "CVT", "MPV", "UK",
                   "USA", "US", "BP", "GLE", "ID", "ET", "UX", "NX", "RX", "CX", "HEV", "PHEV", "DPF", "EGR", "AA",
                   "RAC", "DVLA", "MOT", "VAT", "GPS", "LED", "TV", "CEO", "AI", "CAP"}
# siglas que nunca são o começo de um nome de modelo com número ("an EV one day", "the SUV two years later")
_NOT_MODEL_ACRONYMS = {"SUV", "SUVS", "EV", "EVS", "UK", "US", "USA", "UBS", "BP", "ABS", "MPV", "CEO", "TV", "GPS",
                       "HEV", "PHEV", "VAT", "MOT", "AA", "RAC", "DVLA", "AI", "LED", "DPF", "EGR", "CAP", "NCAP"}
_NUM_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
              "seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety")
_MODEL_NUMBER = re.compile(rf"(?<!-)\b([A-Z]{{1,3}})\s+((?:{_NUM_WORDS})(?:-(?:one|two|three|four|five|six|seven|eight|nine))?)"
                           r"\b(?!\s+(?:hundred|thousand|million|per\s*cent|percent|years?|times|days|weeks|months|"
                           r"miles|of)\b)", re.I)
_CAP_NUMBER = re.compile(rf"\b([A-Z][a-z]+)\s+((?:{_NUM_WORDS.title()})(?:-(?:One|Two|Three|Four|Five|Six|Seven|Eight|"
                         r"Nine|one|two|three|four|five|six|seven|eight|nine))?)\b(?!\s+(?:hundred|thousand|million|"
                         r"per\s*cent|percent)\b)")


_HYPHEN_FIX = {"CRV": "CR-V", "HRV": "HR-V", "WRV": "WR-V", "BRV": "BR-V", "CHR": "C-HR", "EXL": "EX-L"}
_NUM_EN = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|" \
          r"seventy|eighty|ninety)"


def hyphen_spelled(text: str) -> str:
    """Siglas que o roteiro soletra com hífen: "C-R-V" -> "CR-V", "A-B-S" -> "ABS", "G-S-I six" -> "GSI six";
    letra(s) + número por extenso: "R-five" -> "R5", "RAV-four" -> "RAV4"."""
    def letters(m: re.Match) -> str:
        word = m.group(0).replace("-", "")
        return _HYPHEN_FIX.get(word, word)

    # "C-X-three" -> "CX-3", "M-X-five" -> "MX-5" (Mazda escreve com hífen); "S-X-four" -> "SX4"
    text = re.sub(rf"\b([A-Z](?:-[A-Z])+)-({_NUM_EN})\b",
                  lambda m: (lambda w, n: f"{w}-{n}" if w in ("CX", "MX") else f"{w}{n}")(
                      m.group(1).replace("-", ""), word_int(m.group(2))), text)
    text = re.sub(r"\b[A-Z](?:-[A-Z])+(?=s?\b)(?!-[a-z])", letters, text)  # "R-Xs" -> "RXs" também
    text = re.sub(r"\b([a-z])-([A-Z])\b", r"\1\2", text)  # "the Toyota b-Z" -> "bZ"
    # "four-Runner" -> "4Runner"
    text = re.sub(r"\b((?i:one|two|three|four|five|six|seven|eight|nine))-([A-Z][a-z]+)\b",
                  lambda m: f"{word_int(m.group(1))}{m.group(2)}", text)
    # "F-one-fifty" -> "F-150", "F-two-fifty" -> "F-250", "F-one-fifties" -> "F-150s"
    def f_series(m: re.Match) -> str:
        tens, plural = m.group(3), ""
        if tens.lower().endswith("ies"):
            tens, plural = tens[:-3] + "y", "s"
        return f"{m.group(1)}-{word_int(m.group(2))}{word_int(tens):02d}{plural}"

    text = re.sub(r"\b([A-Z])-(one|two|three|four|five|six|seven|eight|nine)-(ten|eleven|twelve|thirteen|fourteen|"
                  r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
                  r"twenties|thirties|forties|fifties|sixties|seventies|eighties|nineties)\b", f_series, text)
    # "Ram fifteen hundred" -> "Ram 1500", "Sierra twenty-five hundred" -> "Sierra 2500" (modelo, sem vírgula)
    text = re.sub(r"\b(?!(?:The|A|An|About|Over|Nearly|Almost|Around|Some|Only|Just|And|But)\b)([A-Z][a-z]+)\s+"
                  r"(twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty-five|thirty-five)"
                  r"\s+hundred\b", lambda m: f"{m.group(1)} {word_int(m.group(2)) * 100}", text)
    # "RX three-fifty" -> "RX 350", "GX four-sixty" -> "GX 460" (Lexus: letras + centena falada em duas partes)
    text = re.sub(r"\b([A-Z]{2,3})\s+(one|two|three|four|five|six|seven|eight|nine)-(ten|eleven|twelve|thirteen|"
                  r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
                  r"eighty|ninety)\b", lambda m: f"{m.group(1)} {word_int(m.group(2))}{word_int(m.group(3)):02d}", text)
    # só letras maiúsculas ("RAV-four"); "hundred-and-ten-pound" não é sigla
    return re.sub(rf"\b((?-i:[A-Z]{{1,4}}))-({_NUM_EN})\b", lambda m: f"{m.group(1)}{word_int(m.group(2))}", text,
                  flags=re.I)


def letters_to_acronyms(text: str) -> str:
    """Letras soltas que a voz soletra viram a sigla: "S U V" -> "SUV", "B M W" -> "BMW", "B Y Ds" -> "BYDs",
    "A B Y D Atto" -> "A BYD Atto", "Seal U D M i" -> "Seal U DM-i", "Euro N CAP" -> "Euro NCAP"."""
    text = hyphen_spelled(text)
    text = re.sub(r"\bM plus S\b", "M+S", text)  # pneu "mud and snow"
    text = re.sub(r"\b(?:([A-Z]) )?D M[ -]i\b", lambda m: (m.group(1) + " " if m.group(1) else "") + "DM-i", text)
    text = re.sub(r"\bN CAP\b", "NCAP", text)
    text = re.sub(r"\bB S E N\b", "BS EN", text)  # norma britânica/europeia ("BS EN 590")

    def join(m: re.Match) -> str:
        word = m.group(0).replace(" ", "")
        plural = word.endswith("s")
        core = word[:-1] if plural else word
        # "A" ou "I" soltos na frente de uma sigla conhecida: artigo/pronome, não letra da sigla
        if core[:1] in ("A", "I") and len(core) >= 3 and core not in _KNOWN_ACRONYMS and core[1:] in _KNOWN_ACRONYMS:
            return f"{core[0]} {core[1:]}" + ("s" if plural else "")
        if core[:1] == "A" and len(core) == 2 and core not in _KNOWN_ACRONYMS and not plural:
            return f"A {core[1]}"  # "A J means Japan": artigo + a letra (não a sigla "AJ")
        return word

    return re.sub(r"\b(?:[A-Z] )+[A-Z]s?\b", join, text)


def spoken_model_numbers(text: str) -> str:
    """Número de modelo falado por extenso: "Model Three" -> "Model 3", "I D three" -> "ID3",
    "Nio E T Five" -> "Nio ET5", "M G S five" -> "MG S5", "Jaecoo Seven" -> "Jaecoo 7"."""
    def brand_split(m: re.Match) -> str:  # "MGS five" -> "MG S five" (a marca soletrada grudou no modelo)
        tok = m.group(1)
        brand = next((b for b in _BRAND_LETTERS if tok.startswith(b) and len(tok) > len(b)), "")
        return f"{brand} {tok[len(brand):]} {m.group(2)}" if brand else m.group(0)

    text = re.sub(rf"\b([A-Z]{{3,4}})\s+((?:{_NUM_WORDS})\b)", brand_split, text, flags=re.I)

    def acronym(m: re.Match) -> str:
        tok, n = m.group(1), word_int(m.group(2))
        if not tok.isupper() or tok.upper() in _NOT_MODEL_ACRONYMS or tok in _BRAND_LETTERS or n is None:
            return m.group(0)
        if tok in ("A", "I"):  # "a five", "I two" não são modelos
            return m.group(0)
        return f"{tok}{n}"

    def capital(m: re.Match) -> str:
        n = word_int(m.group(2))
        if n is None or m.group(1) in ("Number", "Chapter", "Step", "Part", "Tip", "Level"):
            return m.group(0)
        return f"{m.group(1)} {n}"

    def long_acronym(m: re.Match) -> str:  # "the IONIQ five Preferred" -> "IONIQ 5"
        if m.group(1).upper() in _NOT_MODEL_ACRONYMS or m.group(1).upper() in _KNOWN_ACRONYMS:
            return m.group(0)
        return f"{m.group(1)} {word_int(m.group(2))}"

    text = _LONG_ACRONYM.sub(long_acronym, text)
    return _CAP_NUMBER.sub(capital, _MODEL_NUMBER.sub(acronym, text))


_LONG_ACRONYM = re.compile(r"\b((?-i:[A-Z]{4,6}))\s+(one|two|three|four|five|six|seven|eight|nine)\b(?!\s+(?:hundred|"
                           r"thousand|million|per\s*cent|percent|years?|times|days|weeks|months|miles|of|stars?|"
                           r"points?|seats?|doors?|people|cars|vehicles)\b)", re.I)


_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                "seven": "7", "eight": "8", "nine": "9"}
_CODE = re.compile(r"\b((?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
                   r"(?:-(?:double-oh|oh|zero|one|two|three|four|five|six|seven|eight|nine))+)\b", re.I)


def spoken_codes(text: str) -> str:
    """Modelos lidos dígito a dígito: "two-oh-eight" -> "208"; "five-double-oh-eight" -> "5008";
    "one-oh-eight" -> "108". Só quando tem "oh" no meio (senão "twenty-five" etc. viram outra coisa)."""
    def repl(m: re.Match) -> str:
        parts = m.group(1).lower().replace("double-oh", "oh-oh").split("-")
        if "oh" not in parts and len(parts) < 3:  # "five-nine-zero" (norma) vale; "one-two" sozinho não
            return m.group(0)
        return "".join(_DIGIT_WORDS[p] for p in parts)

    return _CODE.sub(repl, text)


_DIGIT = r"(?:zero|one|two|three|four|five|six|seven|eight|nine)"
_HYPHEN_DECIMAL = re.compile(rf"\b({_DIGIT}|ten|eleven|twelve)-point-({_DIGIT})\b", re.I)


def hyphen_decimals(text: str) -> str:
    """ "the two-point-four" -> "the 2.4"; "three-point-five V-six" -> "3.5 V6"; "ten-point-one-inch" -> "10.1-inch"."""
    return _HYPHEN_DECIMAL.sub(lambda m: f"{word_int(m.group(1))}.{_DIGIT_WORDS[m.group(2).lower()]}", text)


def screen_text(text: str, lang: str = "en") -> str:
    """O que a voz de IA diz -> como se escreve na tela (números, %, moeda, siglas soletradas)."""
    if not text or (lang or "en").split("-")[0].lower() != "en":
        return text
    text = numbers_to_digits(spoken_model_numbers(letters_to_acronyms(spoken_codes(hyphen_decimals(text)))), lang)
    return re.sub(r"\b(Fiat\s+500)\s+e\b", r"\1e", text)  # "Fiat five hundred e" -> "Fiat 500e"
