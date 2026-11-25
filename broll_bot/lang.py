"""Idioma do roteiro: detectado sozinho (português, polonês, francês; inglês e espanhol também).

Conta palavras funcionais típicas de cada idioma (artigos, preposições, pronomes) e
letras que só existem num deles (ą ę ł ż do polonês, ã õ do português, è ê œ do
francês). Com o idioma decidido, o bot ajusta a transcrição (Whisper), o formato dos
números e da moeda, as pistas do detector de templates e os rótulos dos gráficos.
"""

from __future__ import annotations

import re
from collections import Counter

SUPPORTED = ("pt", "pl", "fr", "en", "es")

# palavras curtas bem frequentes e pouco ambíguas entre esses idiomas
MARKERS: dict[str, set[str]] = {
    "pt": {"o", "os", "um", "uma", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em", "é", "não", "com",
           "para", "pra", "que", "você", "ele", "ela", "mais", "muito", "isso", "esse", "essa", "também", "são",
           "carro", "tem", "foi", "ao", "pelo", "pela", "já", "mas", "seu", "sua"},
    "pl": {"i", "w", "z", "na", "to", "jest", "nie", "się", "że", "do", "od", "po", "jak", "ale", "czy", "tak",
           "już", "jego", "jej", "ten", "ta", "są", "był", "była", "który", "która", "które", "samochód", "ma",
           "dla", "przez", "oraz", "bardzo", "tylko", "też"},
    "fr": {"le", "la", "les", "un", "une", "des", "du", "de", "et", "est", "pas", "ne", "que", "qui", "dans",
           "pour", "avec", "sur", "il", "elle", "vous", "nous", "ce", "cette", "au", "aux", "plus", "très",
           "voiture", "sont", "mais", "son", "sa", "ses", "en"},
    "en": {"the", "and", "is", "are", "of", "to", "in", "it", "that", "this", "with", "for", "you", "was",
           "car", "its", "has", "have", "on", "but", "not"},
    "es": {"el", "los", "las", "un", "una", "del", "y", "es", "no", "con", "para", "que", "por", "muy", "más",
           "coche", "auto", "este", "esta", "pero", "tiene", "su"},
}
LETTERS: dict[str, str] = {"pl": "ąćęłńśźż", "pt": "ãõ", "fr": "èêëîïœûùÿ", "es": "ñ¿¡"}


def detect_language(text: str, default: str = "pt") -> str:
    score = language_scores(text)
    if not score:
        return default
    lang, top = score.most_common(1)[0]
    return lang if top >= 3 else default


def language_scores(text: str) -> Counter[str]:
    words = re.findall(r"[^\W\d_]+", text.lower())
    score: Counter[str] = Counter()
    if not words:
        return score
    for w in words:
        for lang, markers in MARKERS.items():
            if w in markers:
                score[lang] += 1
    low = text.lower()
    for lang, letters in LETTERS.items():
        score[lang] += 3 * sum(low.count(ch) for ch in letters)
    return score


def resolve(configured: str | None, text: str) -> str:
    """ "auto" (ou vazio) -> idioma detectado no texto; senão, o configurado — a não ser que o roteiro esteja
    claramente em outro idioma (roteiro em inglês num canal em português: o canal errado foi escolhido no app,
    e transcrever o áudio em inglês como português estraga legenda, cards e tempos)."""
    value = (configured or "auto").strip().lower()
    if value in ("auto", ""):
        return detect_language(text)
    value = value.split("-")[0]
    score = language_scores(text)
    if score:
        best, top = score.most_common(1)[0]
        if best != value and top >= 8 and top >= 3 * max(1, score.get(value, 0)):
            return best
    return value
