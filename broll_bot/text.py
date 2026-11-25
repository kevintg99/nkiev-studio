"""Utilitários de texto: normalização, tokenização e stopwords (PT/EN/FR/PL)."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_WORD_RE = re.compile(r"[a-z0-9]+")

STOPWORDS_PT = set(
    """
    a à às ao aos as o os um uma uns umas de da das do dos dum duma em na nas no nos num numa
    por pela pelas pelo pelos para pra pro com sem sob sobre entre até após desde contra
    e ou mas porém contudo todavia que se como quando onde porque porquê pois então logo assim
    também já ainda só apenas mais menos muito muita muitos muitas pouco pouca bem mal
    eu tu ele ela nós vós eles elas você vocês me te se nos lhe lhes meu minha meus minhas
    teu tua seu sua seus suas nosso nossa nossos nossas isso isto aquilo esse essa esses essas
    este esta estes estas aquele aquela aqueles aquelas ser é são era eram foi foram será serão
    seja sejam sido estar está estão estava estavam esteve ter tem têm tinha tinham teve há
    haver fazer faz fazem fez vai vão ir vamos pode podem poder deve devem dever cada todo toda
    todos todas outro outra outros outras qual quais quem cujo cuja aqui ali lá hoje agora sempre
    nunca não sim tão tanto tanta coisa coisas vez vezes dia dias ano anos gente ponto tipo
    """.split()
)

STOPWORDS_EN = set(
    """
    a an the and or but if then else of to in on at by for with without from into onto over under
    is are was were be been being am do does did done have has had having it its this that these
    those there here i you he she we they me him her us them my your his our their what which who
    whom when where why how all any both each few more most other some such no nor not only own
    same so than too very can will just should now also up down out about as again very shot
    """.split()
)

STOPWORDS_FR = set(
    """
    le la les l un une des du de d au aux et ou mais donc or ni car que qu qui quoi dont où ce cet cette ces
    ça cela ceci il elle ils elles on nous vous je tu me te se lui leur leurs son sa ses mon ma mes ton ta tes
    notre nos votre vos y en ne pas plus moins très trop bien peu tout tous toute toutes est sont était étaient
    été être avoir a ont avait avaient eu fait faire va vont aller peut peuvent doit dans sur sous avec sans pour
    par entre vers chez depuis pendant avant après comme quand si aussi encore déjà toujours jamais ici là
    alors ainsi voilà c s n m t j chaque autre autres même quel quelle quels quelles an ans fois chose
    """.split()
)

STOPWORDS_PL = set(
    """
    i oraz a ale lub albo czy że żeby bo więc to ten ta te tego tej tym tymi temu tą w we z ze na do od po o
    u przy przez za pod nad przed dla bez między jest są był była było byli były być będzie będą ma mają miał
    miała mieć nie tak już jeszcze też także tylko bardzo bardziej mniej się sobie jak jako gdy kiedy gdzie
    który która które którego której którym co coś ktoś nic jego jej ich nasz nasza nasze wasz mój moja moje
    twój on ona ono oni one my wy ja ty go mu mi mnie ci cię może można każdy każda każde inny inna inne rok
    lat lata razy raz tu tam teraz zawsze nigdy
    """.split()
)

STOPWORDS = STOPWORDS_PT | STOPWORDS_EN | STOPWORDS_FR | STOPWORDS_PL

FILENAME_NOISE = {"mixkit", "hd", "ready", "4k", "uhd", "large", "medium", "small", "preview", "free", "stock", "video", "footage", "clip"}


# letras que o Unicode não decompõe em "letra + acento" (ł do polonês, œ do francês...)
_EXTRA = str.maketrans({"ł": "l", "Ł": "L", "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O",
                        "ß": "ss", "đ": "d", "Đ": "D"})


def strip_accents(text: str) -> str:
    text = text.translate(_EXTRA)
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


@lru_cache(maxsize=1 << 16)
def normalize(text: str) -> str:
    # guardado: o ranking normaliza as mesmas tags e frases milhares de vezes por vídeo
    return strip_accents(text.lower())


def tokens(text: str) -> list[str]:
    return _WORD_RE.findall(normalize(text))


def stem(word: str) -> str:
    """Stem bem leve (plural) — suficiente para casar tags."""
    for suffix in ("oes", "aes", "ies"):
        if word.endswith(suffix) and len(word) > 4:
            return word[: -len(suffix)] + ("ao" if suffix != "ies" else "y")
    if word.endswith("s") and len(word) > 3 and not word.endswith("ss"):
        return word[:-1]
    return word


def keywords(text: str, min_len: int = 3) -> list[str]:
    return list(_keywords(text, min_len))


@lru_cache(maxsize=1 << 16)
def _keywords(text: str, min_len: int) -> tuple[str, ...]:
    return tuple(stem(t) for t in tokens(text) if len(t) >= min_len and t not in STOPWORDS and not t.isdigit())


def overlap_score(query_terms: list[str], doc_terms: list[str]) -> float:
    """Fração dos termos da consulta presentes no documento (0..1)."""
    q = {stem(t) for t in query_terms}
    if not q:
        return 0.0
    d = {stem(t) for t in doc_terms}
    return len(q & d) / len(q)
