"""Números por extenso compostos em português, espanhol e francês -> valores, para conferir se um dado da tela
foi dito: "vinte e oito horas" (28), "setecentos milhões" (700 e 700.000.000), "dois mil e quinze" (2015),
"cento e quarenta mil" (140.000), "dois vírgula cinco" (2,5), "quatre-vingt-dix" (90).
Só confere (não reescreve a fala); o inglês tem o próprio caminho (spoken.py)."""

from __future__ import annotations

import re

from .text import normalize

_PT = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
    "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14,
    "quinze": 15, "dezesseis": 16, "dezasseis": 16, "dezessete": 17, "dezassete": 17, "dezoito": 18,
    "dezenove": 19, "dezanove": 19, "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60,
    "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200,
    "trezentos": 300, "trezentas": 300, "quatrocentos": 400, "quatrocentas": 400, "quinhentos": 500,
    "quinhentas": 500, "seiscentos": 600, "seiscentas": 600, "setecentos": 700, "setecentas": 700,
    "oitocentos": 800, "oitocentas": 800, "novecentos": 900, "novecentas": 900,
}
_ES = {
    "cero": 0, "un": 1, "una": 1, "uno": 1, "dos": 2, "cuatro": 4, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiuno": 21, "veintiun": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "cuarenta": 40, "sesenta": 60, "ochenta": 80, "cien": 100, "ciento": 100, "doscientos": 200,
    "doscientas": 200, "cuatrocientos": 400, "quinientos": 500, "quinientas": 500, "seiscientos": 600,
    "setecientos": 700, "ochocientos": 800, "novecientos": 900, "dos": 2, "tres": 3, "cinco": 5, "seis": 6, "catorce": 14, "quince": 15,
    "setenta": 70, "noventa": 90, "cincuenta": 50,
}
_FR = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
    "dix": 10, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50,
    "soixante": 60, "cent": 100, "cents": 100,
}
_FR.update({"zero": 0, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15})


def _pl_units() -> dict[str, int]:
    """Polonês sem acento (ł vira l), com as formas declinadas que a fala usa: "trzydzieści sześć litrów" (36),
    "dwustu metrów" (200), "dla czterech osób" (4). Os números se juntam sem conjunção: "sto dwadzieścia pięć"."""
    forms = {
        0: "zero", 1: "jeden jedna jedno jednego jednej jednym jednemu", 2: "dwa dwie dwoch dwom dwoma dwu",
        3: "trzy trzech trzem trzema", 4: "cztery czterech czterem czterema", 5: "piec pieciu piecioma",
        6: "szesc szesciu szescioma", 7: "siedem siedmiu siedmioma", 8: "osiem osmiu osmioma",
        9: "dziewiec dziewieciu", 10: "dziesiec dziesieciu", 11: "jedenascie jedenastu",
        12: "dwanascie dwunastu", 13: "trzynascie trzynastu", 14: "czternascie czternastu",
        15: "pietnascie pietnastu", 16: "szesnascie szesnastu", 17: "siedemnascie siedemnastu",
        18: "osiemnascie osiemnastu", 19: "dziewietnascie dziewietnastu", 20: "dwadziescia dwudziestu",
        30: "trzydziesci trzydziestu", 40: "czterdziesci czterdziestu", 50: "piecdziesiat piecdziesieciu",
        60: "szescdziesiat szescdziesieciu", 70: "siedemdziesiat siedemdziesieciu",
        80: "osiemdziesiat osiemdziesieciu", 90: "dziewiecdziesiat dziewiecdziesieciu", 100: "sto stu",
        200: "dwiescie dwustu", 300: "trzysta trzystu", 400: "czterysta czterystu", 500: "piecset pieciuset",
        600: "szescset szesciuset", 700: "siedemset siedmiuset", 800: "osiemset osmiuset",
        900: "dziewiecset dziewieciuset",
    }
    return {w: n for n, ws in forms.items() for w in ws.split()}


_PL = _pl_units()
_UNITS_BY_LANG = {"pt": _PT, "es": _ES, "fr": _FR, "pl": _PL}
# "vinte e oito", "treinta y dos", "vingt et un"; o polonês junta sem conjunção ("trzydzieści sześć")
_JOIN_BY_LANG = {"pt": {"e"}, "es": {"y"}, "fr": {"et"}, "pl": set()}
_THOUSAND = {"mil", "mille", "tysiac", "tysiace", "tysiecy", "tysiaca", "tysiacu"}
_SCALES = {"milhao": 1e6, "milhoes": 1e6, "millon": 1e6, "millones": 1e6, "million": 1e6, "millions": 1e6,
           "bilhao": 1e9, "bilhoes": 1e9, "milliard": 1e9, "milliards": 1e9, "billon": 1e12, "billones": 1e12,
           "milion": 1e6, "miliony": 1e6, "milionow": 1e6, "miliona": 1e6, "miliard": 1e9, "miliardy": 1e9,
           "miliardow": 1e9}
_POINT = {"virgula", "coma", "virgule", "ponto", "punto", "przecinek"}  # "dois vírgula cinco", "dwa przecinek pięć"
_HUNDREDS = {"cento", "ciento", "cent", "cents"}


def _is_num(w: str, units: dict[str, int]) -> bool:
    return w in units or w in _THOUSAND or w in _SCALES


def _value(words: list[str], units: dict[str, int]) -> tuple[float, float | None]:
    """Valor de uma sequência de palavras-número; o segundo é a contagem antes da escala final
    ("setecentos milhões" -> 700), para o card que mostra "700 milhões"."""
    total, current, before_scale = 0.0, 0.0, None
    k = 0
    while k < len(words):
        w = words[k]
        # francês: "quatre-vingt(s)" = 80 (multiplica), "deux cents" = 200
        if w in ("vingt", "vingts") and current and current % 100 == 4 and k and words[k - 1] == "quatre":
            current += 76  # 4 já somado: 4 -> 80
        elif w in _HUNDREDS and current and current < 10 and k and words[k - 1] not in _HUNDREDS:
            current *= 100  # "deux cents", "dos cientos"
        elif w in units:
            current += units[w]
        elif w in _THOUSAND:
            current = max(current, 1.0) * 1000
            total += current
            current = 0.0
        elif w in _SCALES:
            before_scale = total + max(current, 1.0)
            total = before_scale * _SCALES[w]
            current = 0.0
        k += 1
    return total + current, (before_scale if words and words[-1] in _SCALES else None)


def _runs(words: list[str], units: dict[str, int], join: set[str]) -> list[list[str]]:
    runs: list[list[str]] = []
    cur: list[str] = []
    for k, w in enumerate(words):
        if _is_num(w, units):
            cur.append(w)
        elif w in join and cur and k + 1 < len(words) and _is_num(words[k + 1], units):
            # "cento e cinquenta" (50 < 100) é um número só; "entre cento e cinquenta e duzentos" são dois (o
            # "e" só junta uma parte menor que a anterior, ou depois de "mil"/"milhões")
            last, nxt = cur[-1], words[k + 1]
            if last in _THOUSAND or last in _SCALES or units.get(nxt, 10**12) < units.get(last, 0):
                continue
            runs.append(cur)
            cur = []
        else:
            if cur:
                runs.append(cur)
            cur = []
            if w in _POINT and runs and k + 1 < len(words) and _is_num(words[k + 1], units):
                runs.append([w])  # marcador de decimal
    if cur:
        runs.append(cur)
    return runs


def spelled_values(text: str, lang: str = "pt") -> set[float]:
    """Todos os valores ditos por extenso no texto (sequências inteiras e, para "X milhões", também X)."""
    lang = (lang or "").split("-")[0].lower()
    units = _UNITS_BY_LANG.get(lang)
    if not units or not text:
        return set()
    # pontuação separa números ("treinta y dos, dos mil" são dois)
    words = re.findall(r"[a-z]+|[,;:!?()]|\.(?!\d)", normalize(text))
    out: set[float] = set()
    runs = _runs(words, units, _JOIN_BY_LANG[lang])
    prev: float | None = None
    k = 0
    while k < len(runs):
        run = runs[k]
        if run[0] in _POINT:
            if prev is not None and k + 1 < len(runs):
                frac, _ = _value(runs[k + 1], units)
                digits = f"{int(frac)}"
                out.add(round(prev + int(digits) / 10 ** len(digits), 4))
                k += 2
                continue
            k += 1
            continue
        value, count = _value(run, units)
        out.add(round(value, 4))
        if count is not None:
            out.add(round(count, 4))
        prev = value
        k += 1
    return out
