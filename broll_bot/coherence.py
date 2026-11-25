"""Verificador de coerência do plano de cenas (vale para Claude, ChatGPT e modo chat).

A IA às vezes escolhe um template que "fica bonito" mas não tem a ver com o que é
falado: um gráfico com números que ninguém disse, um versus de carros não citados,
um título que não aparece na fala. Aqui cada template e cada overlay é conferido
contra o texto narrado DAQUELA cena:

* números (ficha técnica, gráfico, contador, selo, número gigante...) precisam
  aparecer na fala — por extenso também vale ("cem", "noventa", "primeiro");
* itens de versus/comparativo/tríptico precisam ser citados;
* títulos, palavras-chave, tópicos e trechos em marca-texto precisam ter palavras da fala;
* VHS pede uma referência ao passado; nunca o mesmo template em duas cenas seguidas.

O que não bate é corrigido (linhas soltas saem) ou rebaixado para b-roll normal —
o bot nunca inventa dado na tela.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from .log import get_logger
from .models import Scene, SceneAnalysis
from .text import keywords, normalize, tokens

log = get_logger("coherence")

SPELLED = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
    "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100,
    "cento": 100, "duzentos": 200, "trezentos": 300, "quinhentos": 500, "mil": 1000, "metade": 50,
    "primeiro": 1, "primeira": 1, "segundo": 2, "segunda": 2, "terceiro": 3, "terceira": 3, "quarto": 4,
    "quarta": 4, "quinto": 5, "quinta": 5, "sexto": 6, "sexta": 6, "setimo": 7, "setima": 7, "oitavo": 8,
    "oitava": 8, "nono": 9, "nona": 9, "decimo": 10, "decima": 10,
    # francês (sem acento)
    "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
    "onze": 11, "douze": 12, "quinze": 15, "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50,
    "soixante": 60, "cent": 100, "cents": 100, "mille": 1000, "moitie": 50, "premier": 1, "premiere": 1,
    "deuxieme": 2, "second": 2, "seconde": 2, "troisieme": 3, "quatrieme": 4, "cinquieme": 5, "sixieme": 6,
    "septieme": 7, "huitieme": 8, "neuvieme": 9, "dixieme": 10,
    # polonês (sem acento; ł vira l)
    "jeden": 1, "jedna": 1, "dwa": 2, "dwie": 2, "trzy": 3, "cztery": 4, "piec": 5, "szesc": 6, "siedem": 7,
    "osiem": 8, "dziewiec": 9, "dziesiec": 10, "dwadziescia": 20, "trzydziesci": 30, "czterdziesci": 40,
    "piecdziesiat": 50, "sto": 100, "stu": 100, "zera": 0, "tysiac": 1000, "polowa": 50, "pierwszy": 1, "pierwsza": 1, "pierwsze": 1,
    "drugi": 2, "druga": 2, "trzeci": 3, "trzecia": 3, "czwarty": 4, "piaty": 5, "szosty": 6, "siodmy": 7,
    "osmy": 8, "dziewiaty": 9, "dziesiaty": 10,
    # inglês
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "hundred": 100, "thousand": 1000, "half": 50, "first": 1, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
# multiplicadores falados depois do número ("110 mil", "30 mille", "90 tys.")
THOUSAND = r"mil|mille|tys|tysiac|tysiace|tysiecy|thousand|grand|k"
MILLION = r"milhao|milhoes|million|millions|milion|miliony|milionow"
BILLION = r"bilhao|bilhoes|milliard|milliards|miliard|miliardy|miliardow|billion|billions"
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
PAST_CUES = ("anos", "antig", "lembra", "primeiro", "primeira", "decada", "epoca", "passado", "naquela", "infancia",
             "classico", "velho", "geracao", "noventa", "oitenta", "setenta",
             "annees", "ancien", "souvenir", "epoque", "passe", "generation", "autrefois", "enfance", "classique",
             "lat", "star", "pamieta", "dawn", "kiedys", "generacj", "dziecinstw", "klasyk", "wspomnien",
             "years ago", "back in", "remember", "classic", "childhood", "old school", "decade", "nineties",
             "eighties", "seventies")
DATA_STYLES = {"specs", "chart", "stat", "gauge", "counter", "speedometer", "price", "datasheet", "score"}
CHANGE_CUES = ("antes", "depois", "novo", "nova", "antig", "facelift", "reestiliz", "mudou", "mudanca", "evolu",
               "before", "after", "avant", "apres", "przed", "nowy", "nowa", "stary")


def _to_float(token: str) -> list[float]:
    """'4.630' -> 4630; '21,4' -> 21.4; '2.0' -> 2.0 (mais de uma leitura quando é ambíguo)."""
    out = []
    if re.fullmatch(r"\d{1,3}(,\d{3})+", token):
        from .cues import language

        thousands = float(token.replace(",", ""))
        if token.count(",") > 1:
            return [thousands]
        decimal = float(token.replace(",", "."))
        return [thousands, decimal] if language() == "en" else [decimal, thousands]  # "12,000" / "7,250"
    if "," in token:
        out.append(float(token.replace(".", "").replace(",", ".")))
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", token):
        out.append(float(token.replace(".", "")))
        if token.count(".") == 1:
            out.append(float(token))  # também pode ser decimal em inglês ("4.630" -> 4,63)
    else:
        try:
            out.append(float(token))
        except ValueError:
            pass
    return out


def numbers_in(text: str) -> set[float]:
    # "89 900" (fr/pl, espaço normal ou fino) é um número só
    norm = re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))", "", normalize(text))
    nums: set[float] = set()
    for tok in _NUM_RE.findall(norm):
        nums.update(_to_float(tok))
    for word in re.findall(r"[a-z]+", norm):
        if word in SPELLED:
            nums.add(float(SPELLED[word]))
    from .cues import language
    from .spelled import spelled_values

    nums |= spelled_values(text, language())  # "vinte e oito", "dois mil e quinze", "setecentos milhões"
    # "110 mil" -> 110000; "2 milhões" -> 2000000
    for m in re.finditer(rf"(\d+(?:[.,]\d+)?)\s*({THOUSAND}|{MILLION}|{BILLION})\b\.?", norm):
        base = _to_float(m.group(1))[0]
        word = m.group(2)
        mult = 1000 if re.fullmatch(THOUSAND, word) else 1e6 if re.fullmatch(MILLION, word) else 1e9
        nums.add(base * mult)
    return nums


def number_ok(value: float, spoken: set[float]) -> bool:
    if value == int(value) and 1900 <= value <= 2100:  # ano: tem que ser o mesmo (1983 não é 2002)
        return value in spoken
    for n in spoken:
        if abs(value - n) <= max(0.011, abs(n) * 0.01):
            return True
        if n and (abs(value - n * 1000) < 1 or abs(value * 1000 - n) < 1):  # "110 mil" x 110.000
            return True
    return False


def text_numbers(text: str) -> list[float]:
    norm = re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))", "", normalize(text))
    return [v for tok in _NUM_RE.findall(norm) for v in _to_float(tok)[:1]]


def words_ok(phrase: str, spoken_words: set[str], need: float = 0.5) -> bool:
    """Pelo menos ``need`` das palavras relevantes da frase aparecem na fala."""
    kws = keywords(phrase)
    if not kws:
        return True
    hit = sum(k in spoken_words or any(s.startswith(k[:5]) and len(k) >= 5 for s in spoken_words) for k in kws)
    return hit / len(kws) >= need


def value_ok(text: str, spoken_nums: set[float], spoken_words: set[str]) -> bool:
    """Um valor exibido ("51 PSI", "R$ 110 mil", "1º lugar", "Corolla") bate com a fala?"""
    nums = text_numbers(text)
    if nums:
        return all(number_ok(v, spoken_nums) for v in nums) or number_ok(
            nums[0] * (1000 if re.search(rf"\b({THOUSAND})\b", normalize(text)) else 1), spoken_nums)
    return words_ok(text, spoken_words)


@dataclass
class Fixes:
    notes: list[str] = field(default_factory=list)

    def add(self, scene: Scene, msg: str) -> None:
        self.notes.append(f"cena {scene.index}: {msg}")


def _demote(a: SceneAnalysis) -> None:
    a.style, a.template_seconds = "normal", 0.0


def _check_template(scene: Scene, nums: set[float], words: set[str], fx: Fixes) -> None:
    a = scene.analysis
    style = a.style
    if style == "normal":
        return
    reason = ""
    if style in ("specs", "chart", "datasheet", "score"):
        kept = [st for st in a.stats if value_ok(str(st.get("value", "")), nums, words)]
        if len(kept) < len(a.stats):
            fx.add(scene, f"{style}: {len(a.stats) - len(kept)} linha(s) com número não falado removida(s)")
        a.stats = kept
        if len(kept) < (2 if style in ("chart", "score") else 1):
            reason = "números que não estão na fala"
        if style == "score" and a.number.get("value") and not number_ok(float(a.number["value"]), nums):
            a.number = {**a.number, "value": 0}  # nota final não falada: o anel não aparece
    elif style in ("stat", "gauge", "counter", "speedometer", "price"):
        value = float((a.number or {}).get("value") or 0)
        if not value or not number_ok(value, nums):
            reason = f"número {value:g} não é falado"
        elif (a.number or {}).get("value_to") and not number_ok(float(a.number["value_to"]), nums):
            a.number = {k: v for k, v in a.number.items() if k != "value_to"}  # fim da faixa não falado
            fx.add(scene, "price: fim da faixa não falado removido")
        elif style == "counter" and not ("%" in (a.number or {}).get("suffix", "") or "%" in scene.text or re.search(
                r"por cento|em cada|pour ?cent|sur \w+|procent|na \w+ ", normalize(scene.text))):
            reason = "contador sem porcentagem/proporção na fala"
    elif style in ("percentbar", "receipt", "peoplegrid"):
        # números da voz de IA por extenso ("thirty-nine per cent") também valem
        from .spoken import screen_text

        spoken = nums | numbers_in(screen_text(scene.text, "en"))
        value = float((a.number or {}).get("value") or 0)
        if not value or not number_ok(value, spoken):
            reason = f"número {value:g} não é falado"
    elif style == "yearflash":
        year = float((a.number or {}).get("value") or 0)
        if not year or not number_ok(year, nums):
            reason = f"ano {year:g} não é falado"
    elif style == "downtime":
        if not float((a.number or {}).get("value") or 0):
            reason = "tempo parado não citado"
    elif style == "warning":
        if not a.caption or not words_ok(a.caption, words, 0.5):
            reason = "defeito não citado na fala"
    elif style in ("source", "unverified", "steps"):
        from .spoken import screen_text

        spoken_words = words | set(tokens(screen_text(scene.text, "en")))  # "U B S" -> "ubs" (sem a vírgula)
        if not a.caption or not words_ok(a.caption, spoken_words, 0.5):
            reason = "texto que não está na fala"
    elif style == "fromto":
        from .spoken import screen_text, word_int

        spoken = nums | numbers_in(screen_text(scene.text, "en"))
        spoken |= {float(n) for w in re.findall(r"[a-zà-ÿ-]+", scene.text.lower()) if (n := word_int(w)) is not None}
        n = a.number or {}
        if not all(number_ok(float(n.get(k) or 0), spoken) for k in ("from", "value")):
            reason = "números do crescimento não são falados"
    elif style == "coverage":
        if not any(float(p.get("value") or 0) > 0 for p in a.points):
            reason = "garantia sem anos"
    elif style in ("versus", "comparison"):
        hits = [it for it in a.items if value_ok(it.get("label", ""), nums, words)]
        if len(hits) < 2:
            reason = "itens comparados não são citados"
        if style == "versus" and not reason:
            kept = [st for st in a.stats if value_ok(str(st.get("value", "")), nums, words)
                    and (not st.get("value_b") or value_ok(str(st.get("value_b")), nums, words))]
            a.stats = kept
    elif style == "triptych":
        hits = [it for it in a.items if value_ok(it.get("label", ""), nums, words)]
        if len(hits) < 2:
            reason = "valores do tríptico não são falados"
        else:
            a.items = hits
    elif style in ("timeline", "poll"):
        hits = [it for it in a.items if value_ok(it.get("label", ""), nums, words)]
        if len(hits) < len(a.items):
            fx.add(scene, f"{style}: {len(a.items) - len(hits)} item(ns) não falado(s) removido(s)")
        a.items = hits
        if len(hits) < 2:
            reason = "anos/opções que não estão na fala"
    elif style == "beforeafter":
        spoken_label = any(value_ok(it.get("label", ""), nums, words) for it in a.items)
        if not spoken_label and not any(c in normalize(scene.text) for c in CHANGE_CUES):
            reason = "antes/depois sem mudança citada na fala"
    elif style == "proscons":
        kept = [p for p in a.points if words_ok(str(p.get("text", "")), words, 0.34)]
        if len(kept) < len(a.points):
            fx.add(scene, f"proscons: {len(a.points) - len(kept)} ponto(s) fora da fala removido(s)")
        a.points = kept
        if len(kept) < 2:
            reason = "prós/contras que não estão na fala"
        elif all(p.get("positive", True) for p in kept) or not any(p.get("positive", True) for p in kept):
            a.style = "checklist"  # só prós (ou só contras): lista simples
            fx.add(scene, "proscons sem os dois lados virou checklist")
    elif style in ("news", "comment"):
        text = a.body or a.caption
        if not text or not words_ok(text, words, 0.5):
            reason = "texto que não é lido na fala"
        elif style == "news":
            a.highlights = [h for h in a.highlights if words_ok(h, words, 0.6)]
    elif style == "magnifier":
        if not a.caption or not words_ok(a.caption, words, 0.5):
            reason = "detalhe da lupa não é citado"
    elif style == "checklist":
        kept = [p for p in a.points if words_ok(str(p.get("text", "")), words, 0.34)]
        if len(kept) < len(a.points):
            fx.add(scene, f"checklist: {len(a.points) - len(kept)} ponto(s) fora da fala removido(s)")
        a.points = kept
        if len(kept) < 2:
            reason = "prós/contras que não estão na fala"
    elif style == "ranking":
        if not a.rank or not number_ok(float(a.rank), nums):
            reason = "posição do ranking não é falada"
    elif style == "quote":
        kept = [h for h in a.highlights if words_ok(h, words, 0.6) and value_ok(h, nums, words)]
        if len(kept) < len(a.highlights):
            fx.add(scene, "quote: trecho(s) em marca-texto que não estão na fala removido(s)")
        a.highlights = kept
        if a.caption and not words_ok(a.caption, words, 0.5):
            a.caption = ""
        if not kept:
            reason = "frase destacada não está na fala"
    elif style in ("headline", "chapter"):
        if not a.caption or not value_ok(a.caption, nums, words):
            reason = "título não tem relação com a fala"
    elif style == "statement":
        if not a.body or not words_ok(a.body, words, 0.8):
            reason = "frase da tela preta não é a da fala"
    elif style == "dossier":
        # título: o carro da seção ou palavras da fala; a frase da tira só se for falada
        if a.caption and not _names_subject(a.caption, a.subject) and not words_ok(a.caption, words, 0.5):
            a.caption = a.subject
            fx.add(scene, "dossier: título trocado pelo carro da seção")
        if a.subtitle and not words_ok(a.subtitle, words, 0.6):
            a.subtitle = ""
            fx.add(scene, "dossier: frase da tira que não está na fala removida")
        if not a.caption:
            reason = "dossiê sem título"
    elif style == "vhs":
        norm = normalize(scene.text)
        old_year = any(1900 < n < 2012 for n in nums)
        if not old_year and not any(c in norm for c in PAST_CUES):
            reason = "VHS sem referência ao passado"
    if style == "blueprint" and a.stats:
        a.stats = [st for st in a.stats if value_ok(str(st.get("value", "")), nums, words)]
    if reason:
        fx.add(scene, f"template {style} virou b-roll normal ({reason})")
        _demote(a)


def _names_subject(text: str, subject: str) -> bool:
    if not subject:
        return False
    from .subject import names_car

    return names_car(text, subject)


def _check_overlay(scene: Scene, nums: set[float], words: set[str], fx: Fixes) -> None:
    ov = scene.analysis.overlay or {}
    kind = ov.get("kind")
    if not kind:
        return
    ok = True
    if kind in ("label", "pair", "badge", "bignum", "tag", "fuel"):
        texts = [ov.get("title", "")] + ([ov.get("title_b", "")] if kind == "pair" else [])
        ok = all(value_ok(t, nums, words) for t in texts if t)
        if ok and kind == "tag" and ov.get("detail") and text_numbers(ov["detail"]):
            ok = value_ok(ov["detail"], nums, words)
    elif kind in ("keyword", "callout", "arrow"):
        ok = words_ok(ov.get("title", ""), words, 0.5)
    elif kind == "file":  # ficha: o nome do carro desta seção ou um nome que a fala cita
        title = ov.get("title", "")
        ok = bool(title) and (_names_subject(title, scene.analysis.subject) or words_ok(title, words, 0.5))
    elif kind == "impact":  # palavra grande + a frase: as duas da fala
        ok = bool(ov.get("title")) and words_ok(ov["title"], words, 1.0) \
            and (not ov.get("detail") or words_ok(ov["detail"], words, 0.8))
    elif kind == "phrase":  # frase de destaque: é a própria fala
        ok = bool(ov.get("title")) and words_ok(ov["title"], words, 0.8)
    elif kind == "subscribe":
        from .motion.i18n import has_cta

        ok = has_cta(scene.text)
    elif kind in ("bullets", "panel"):
        lines = [ln for ln in ov.get("lines", []) if words_ok(ln, words, 0.34)]
        ov["lines"] = lines
        ok = len(lines) >= 2
    if not ok:
        fx.add(scene, f"overlay {kind} removido (texto/número que não está na fala)")
        scene.analysis.overlay = {}


# moeda falada -> (prefixo, sufixo). A IA às vezes copia o "R$ " do exemplo num roteiro em francês
CURRENCIES = ((r"\beuros?\b|€", ("", " €")), (r"r\$|\breais\b", ("R$ ", "")),
              (r"\bz[łl]\w*|\bpln\b", ("", " zł")), (r"£|\bpounds?\b|\bquid\b", ("£", "")),
              (r"\bc\$|\bcad\b|\bcanadian\s+dollars?\b", ("C$", "")),
              (r"\$|\bdollars?\b|\bbucks\b|\bd[oó]lares\b", ("$", "")))
MONEY_MARK = re.compile(r"r\$|€|zł|£|\$|reais|euros?|pln|pounds?|dollars?", re.I)


def fix_currency(scene: Scene, fx: "Fixes | None" = None) -> None:
    """Etiqueta/número de dinheiro usa a moeda que a fala cita ("euros" -> " €", nunca "R$ ")."""
    a = scene.analysis
    num = a.number or {}
    prefix, suffix = str(num.get("prefix", "")), str(num.get("suffix", ""))
    if not num or (a.style != "price" and not MONEY_MARK.search(prefix + suffix)):
        return
    from .cues import dollar_symbol

    spoken = next((pair for pat, pair in CURRENCIES if re.search(pat, scene.text, re.I)), None)
    if spoken == ("$", "") or (spoken is None and prefix.strip() == "$"):
        spoken = (dollar_symbol(), "")  # "dollars" (ou "$" da IA sem moeda na fala) num canal canadense = C$
    if spoken is None or (prefix.strip(), suffix.strip()) == (spoken[0].strip(), spoken[1].strip()):
        return
    a.number = {**num, "prefix": spoken[0], "suffix": spoken[1]}
    if fx is not None:
        fx.add(scene, f"{a.style}: moeda trocada para a da fala ({(spoken[0] + spoken[1]).strip()})")


def template_fits(scene: Scene) -> bool:
    """O template da cena passa na checagem de coerência com o texto dela (sem mexer na cena)?"""
    probe = Scene(scene.index, scene.text, scene.start, scene.end, copy.deepcopy(scene.analysis))
    _check_template(probe, numbers_in(probe.text), set(keywords(probe.text)), Fixes())
    return probe.analysis.style == scene.analysis.style


def enforce_coherence(scenes: list[Scene]) -> list[str]:
    """Corrige o plano no lugar e devolve a lista do que foi ajustado (vai para o report)."""
    fx = Fixes()
    prev_style = ""
    from .spoken import screen_text

    for scene in scenes:
        # números por extenso da voz de IA ("seventy-five per cent") também contam como falados
        nums = numbers_in(scene.text) | numbers_in(screen_text(scene.text, "en"))
        words = set(keywords(scene.text))
        fix_currency(scene, fx)
        _check_template(scene, nums, words, fx)
        _check_overlay(scene, nums, words, fx)
        style = scene.analysis.style
        # o passo a passo vem em sequência de propósito ("One." / "Two." / "Three.")
        if style != "normal" and style == prev_style and style != "steps":
            fx.add(scene, f"template {style} repetido em cenas seguidas virou b-roll normal")
            _demote(scene.analysis)
        prev_style = scene.analysis.style
    if fx.notes:
        log.info("plano de cenas ajustado para ficar coerente com a fala",
                 extra={"stage": "coherence", "fixes": len(fx.notes), "detalhes": fx.notes[:12]})
    return fx.notes
