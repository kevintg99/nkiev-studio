"""Ajustes estruturais no plano de cenas, depois da IA e antes da busca.

* ``split_long_scenes``: cena longa demais (a IA às vezes junta 40 s numa cena só)
  vira várias, nas fronteiras das frases. Cada parte começa por uma busca diferente,
  então o vídeo não fica alternando os mesmos dois clipes.
* ``fix_meta_queries``: buscas que descrevem o gráfico e não a filmagem ("title card",
  "number two", "icon") são trocadas pelas da cena vizinha — senão aparece a foto
  de um número "2" pintado depois do título.
* ``stretch_title_templates``: título/capítulo em cena curta ocupa a cena inteira.
* ``isolate_rank_headers``: "Numéro 8 : la Dacia Logan MCV." vira uma cena só dele (o
  cartão do ranking entra na hora exata em que a posição é anunciada).
* ``focus_queries``: cada cena (ou pedaço de cena dividida) busca primeiro o carro que ELA fala;
  busca por um carro que a cena não cita (o Lexus da cena do Audi) sai.
* ``add_cta_overlays``: cena com chamada para se inscrever/curtir ganha o botão de
  inscrição animado (overlay "subscribe"), mesmo que a IA não tenha pedido.
"""

from __future__ import annotations

import copy
import re

from .log import get_logger
from .models import Scene, Sentence

log = get_logger("plan_fix")

META_RE = re.compile(
    r"\b(title|titles|icon|icons|text|caption|captions|graphic|graphics|infographic|typography|lower third|"
    r"numeral|animation|animated|number (one|two|three|four|five|six|seven|eight|nine|ten|\d+)|"
    # chamada do canal ("deixa o like e se inscreve"): banco de vídeo não tem "youtube end card subscribe" e o
    # fim do vídeo virava fundo genérico e imagem repetida
    r"youtube|subscribe|subscribers?|subscription|call to action|cta|end card|end screen|like button|"
    r"notification bell|bell icon|comment (?:prompt|section|box)|on screen|overlay|next video|"
    r"video suggestion|suggested video)\b"
)


def is_meta(text: str) -> bool:
    return bool(META_RE.search((text or "").lower()))


# buscas por coisas que banco de imagem não tem (a IA pede o gráfico/o número em vez da filmagem):
# "car chart with bottom highlighted", "car 75 percent on paper", "car ranking board close up"
ABSTRACT_RE = re.compile(
    r"\b(charts?|graphs?|statistics?|stats?|spreadsheets?|percent(?:age)?|survey|data|figures?|methodology|"
    r"scoreboard|ranking board|rating graphic|list with|sales ranking|on paper|\d+\s*%|\d{2,}[,.]\d{3})\b")


def is_abstract(query: str) -> bool:
    return bool(ABSTRACT_RE.search((query or "").lower()))


def _sentences_of(scene: Scene, sentences: list[Sentence]) -> list[Sentence]:
    return [s for s in sentences if scene.start - 0.05 <= (s.start + s.end) / 2 <= scene.end + 0.05]


TITLE_STYLES = ("headline", "chapter", "ranking", "statement")


def closing_headers(sentences: list[Sentence]) -> set[int]:
    """Cabeçalhos que não abrem seção: logo depois vem outro cabeçalho. "Esso clears the bar. Number three."
    + "Number two." (fecha a seção do 3), "Right. Number seven." + "Number seven. Morrisons." (a voz repete).
    Devolve o ``index`` dessas frases: o cartão fica só no cabeçalho de verdade."""
    from .subject import rank_header

    heads = [rank_header(s.text) is not None for s in sentences]
    return {s.index for k, s in enumerate(sentences[:-1]) if heads[k] and heads[k + 1]}


def drop_closing_rank_cards(scenes: list[Scene], sentences: list[Sentence]) -> list[str]:
    """Cena cujo único cabeçalho é um de fechamento (``closing_headers``) não ganha cartão de ranking."""
    from .subject import rank_header

    dead = closing_headers(sentences)
    notes: list[str] = []
    for scene in scenes:
        if scene.analysis.style not in ("ranking", "newsrank", "paperrank"):
            continue
        heads = [s for s in _sentences_of(scene, sentences) if rank_header(s.text)]
        if heads and all(s.index in dead for s in heads):
            scene.analysis.style, scene.analysis.template_seconds = "normal", 0.0
            notes.append(f"cena {scene.index}: \"{heads[0].text.strip()}\" fecha a seção (sem cartão repetido)")
    return notes


def isolate_rank_headers(scenes: list[Scene], sentences: list[Sentence], min_part: float = 1.0) -> list[str]:
    """Separa o cabeçalho do ranking do resto da cena (a IA costuma grudar "Numéro 8 : ..." no fim
    da cena anterior ou no começo da seguinte). Pedaço com menos de ``min_part`` fica com o cabeçalho.
    Repete até não sobrar cabeçalho grudado: na recapitulação ("Number eight. The independent. Number seven.
    Morrisons...") a mesma cena tem vários."""
    notes: list[str] = []
    for _ in range(12):
        new = _isolate_once(scenes, sentences, min_part)
        if not new:
            break
        notes += new
    return notes


def _isolate_once(scenes: list[Scene], sentences: list[Sentence], min_part: float) -> list[str]:
    from .subject import lookahead_name, rank_header

    dead = closing_headers(sentences)
    out: list[Scene] = []
    notes: list[str] = []
    for scene in scenes:
        sents = _sentences_of(scene, sentences)
        k = next((n for n, s in enumerate(sents) if rank_header(s.text) and s.index not in dead), -1)
        if k < 0 or len(sents) < 2:
            out.append(scene)
            continue
        # "Number ten. Number ten. The Range Rover Evoque, ...": a repetição e a frase com o nome
        # ficam junto do cabeçalho (o cartão mostra o nome enquanto ele é dito)
        head = rank_header(sents[k].text)
        kk = k
        while kk + 1 < len(sents):
            nxt = rank_header(sents[kk + 1].text)
            if nxt and nxt[0] == head[0] and nxt[1] == head[1]:
                kk += 1
                continue
            if not nxt and lookahead_name(sents[kk + 1].text) == head[1]:
                kk += 1
            break
        t_head0 = scene.start if k == 0 else sents[k].start
        t_head1 = scene.end if kk == len(sents) - 1 else sents[kk + 1].start
        a0, a1 = 0 if t_head0 - scene.start < min_part else k, kk if scene.end - t_head1 >= min_part else len(sents) - 1
        t0 = scene.start if a0 == 0 else t_head0
        t1 = scene.end if a1 == len(sents) - 1 else t_head1
        pieces = []
        if a0 > 0:
            pieces.append((scene.start, t0, sents[:a0], False))
        pieces.append((t0, t1, sents[a0:a1 + 1], True))
        if a1 < len(sents) - 1:
            pieces.append((t1, scene.end, sents[a1 + 1:], False))
        if len(pieces) == 1:
            out.append(scene)
            continue
        for start, end, chunk, is_head in pieces:
            a = copy.deepcopy(scene.analysis)
            if not is_head and a.style in TITLE_STYLES or is_head and a.style not in TITLE_STYLES:
                a.style, a.template_seconds = "normal", 0.0
            out.append(Scene(0, " ".join(x.text for x in chunk), start, end, a))
        notes.append(f"cena {scene.index}: cabeçalho do ranking separado em cena própria")
    scenes[:] = out
    for i, scene in enumerate(scenes):
        scene.index = i
    return notes


def _template_home(a, chunks: list[list[Sentence]]) -> int:
    """Parte da cena dividida que fala os dados do template ("entre 3 000 et 6 000 euros" pode
    estar no fim da cena; a etiqueta de preço não pode ficar na 1ª parte, que não cita o valor)."""
    from .coherence import template_fits

    for k, chunk in enumerate(chunks):
        probe = Scene(0, " ".join(s.text for s in chunk), chunk[0].start, chunk[-1].end, a)
        if template_fits(probe):
            return k
    return 0


def split_long_scenes(scenes: list[Scene], sentences: list[Sentence], max_seconds: float,
                      target_seconds: float) -> tuple[list[Scene], list[str]]:
    out: list[Scene] = []
    notes: list[str] = []
    for scene in scenes:
        a = scene.analysis
        has_template = a.style != "normal"
        if scene.duration <= max_seconds or (has_template and scene.duration <= 2 * max_seconds):
            out.append(scene)
            continue
        sents = _sentences_of(scene, sentences)
        # a 1ª parte segura o template inteiro; as demais ficam em torno do alvo
        first_min = (a.template_seconds + 1.5) if has_template else 0.0
        chunks: list[list[Sentence]] = []
        for s in sents:
            if not chunks:
                chunks.append([s])
                continue
            cur = chunks[-1]
            cur_len = cur[-1].end - cur[0].start
            limit = max(target_seconds, first_min) if len(chunks) == 1 else target_seconds
            if cur_len + (s.end - s.start) <= limit or cur_len < 3.0:
                cur.append(s)
            else:
                chunks.append([s])
        if len(chunks) < 2:
            out.append(scene)
            continue
        n_q = max(1, len(a.queries))
        home = _template_home(a, chunks) if has_template else 0
        for k, chunk in enumerate(chunks):
            start = scene.start if k == 0 else chunk[0].start
            end = scene.end if k == len(chunks) - 1 else chunks[k + 1][0].start
            part = copy.deepcopy(a)
            if k != home:  # o template fica na parte em que os dados dele são falados
                part.style, part.template_seconds = "normal", 0.0
            if k > 0:
                part.overlay = {}
                # cada parte começa por uma busca diferente (variedade de clipes)
                r = k % n_q
                part.queries = a.queries[r:] + a.queries[:r]
            # cena grande (3+ partes): as mesmas buscas em todas as partes esgotam o banco e o vídeo
            # repete imagens; cada parte ganha buscas próprias depois (query_lang.requery_parts)
            part.requery = len(chunks) >= 3
            out.append(Scene(0, " ".join(s.text for s in chunk), start, end, part))
        notes.append(f"cena de {scene.duration:.0f}s dividida em {len(chunks)}")
    for i, scene in enumerate(out):
        scene.index = i
    if notes:
        log.info("cenas longas divididas", extra={"stage": "plan_fix", "detalhes": notes})
    return out, notes


def _car_in_query(query: str) -> tuple[str, str] | None:
    """ "audi q2 parked driveway" -> ("audi", "q2"); "range rover evoque driving" -> ("rover", "evoque");
    "car showroom interior" -> None."""
    from .subject import _GENERIC_AFTER_BRAND, BRANDS
    from .text import normalize

    toks = re.findall(r"[a-z0-9]+", normalize(query))
    for i, t in enumerate(toks):
        if t in BRANDS:
            nxt = next((x for x in toks[i + 1:] if x not in BRANDS), "")
            model = "" if not nxt or nxt in _GENERIC_AFTER_BRAND or re.fullmatch(r"(19|20)\d\d", nxt) else nxt
            return t, model
    return None


def _spoken_tokens(text: str) -> set[str]:
    """Palavras da fala com os nomes falados pela voz já juntados ("Audi Q two" -> "audi", "q2")."""
    from .spoken import clean_model_name, spoken_years
    from .text import normalize

    return set(re.findall(r"[a-z0-9]+", normalize(clean_model_name(spoken_years(text)))))


def focus_queries(scenes: list[Scene]) -> list[str]:
    """A IA faz uma cena de 100 s com Lexus, Audi e Nissan e o bot divide em pedaços: cada pedaço
    herdava as 4 buscas da cena inteira (o pedaço do Audi abria com a busca do Lexus, o do Nissan
    com a do Audi — o carro aparecia uma cena depois de ser falado). Aqui cada cena põe primeiro a
    busca do carro que ela fala e tira a busca de um carro que ela não cita e que não é o da seção."""
    from .subject import model_word
    from .text import normalize

    notes: list[str] = []
    for scene in scenes:
        a = scene.analysis
        if len(a.queries) < 2 or a.query_language != "en":
            continue
        spoken = _spoken_tokens(scene.text)
        section = set(re.findall(r"[a-z0-9]+", normalize(a.subject))) if a.subject else set()
        sec_model = model_word(a.subject) if a.subject else ""

        def score(q: str) -> int:
            car = _car_in_query(q)
            if car is None:
                return 0  # oficina, estrada, painel...
            brand, model = car
            if model and model in spoken or not model and brand in spoken:
                return 2  # o carro que esta cena fala
            if section and (model == sec_model or not model and brand in section):
                return 1  # o carro da seção (ranking)
            return -1  # outro carro, que esta cena nem cita

        scored = [(score(q), i, q) for i, q in enumerate(a.queries)]
        keep = [x for x in scored if x[0] >= 0]
        if not keep:
            continue
        new = [q for _, _, q in sorted(keep, key=lambda x: (-x[0], x[1]))]
        if new != a.queries:
            dropped = [q for sc, _, q in scored if sc < 0]
            a.queries = new
            notes.append(f"cena {scene.index}: buscas na ordem do que a fala cita"
                         + (f" (sem {', '.join(dropped)})" if dropped else ""))
    return notes


def fix_meta_queries(scenes: list[Scene]) -> list[str]:
    notes: list[str] = []
    for i, scene in enumerate(scenes):
        a = scene.analysis
        bad = [q for q in a.queries if is_meta(q) or is_abstract(q)]
        if not bad and not is_meta(a.visual_summary):
            continue
        good = [q for q in a.queries if not is_meta(q) and not is_abstract(q)]
        # o título apresenta o que vem a seguir: usa as buscas da próxima cena (ou da anterior); no fim do vídeo
        # (chamada do canal em várias cenas seguidas) procura a cena de conteúdo mais perto
        order = [j for d in range(1, 7) for j in (i + d, i - d)]
        donor = next((scenes[j] for j in order if 0 <= j < len(scenes)
                      and not is_meta(scenes[j].analysis.visual_summary)
                      and any(not is_meta(q) and not is_abstract(q) for q in scenes[j].analysis.queries)), None)
        if donor is not None:
            d = donor.analysis
            a.queries = list(dict.fromkeys(good + [q for q in d.queries if not is_meta(q) and not is_abstract(q)]))[:4]
            if is_meta(a.visual_summary):
                a.visual_summary = d.visual_summary
            if is_meta(a.fallback_query):
                a.fallback_query = d.fallback_query
        else:
            a.queries = good or [a.fallback_query]
        if good and len(good) >= 2 and not is_meta(a.visual_summary):  # sobrou o bastante: só tira as abstratas
            a.queries = good
        notes.append(f"cena {scene.index}: buscas de 'título/ícone' trocadas por filmagem real")
    return notes


# templates com uma lista falada ao longo da cena: cada item entra quando é dito, então o
# template precisa durar a cena toda (senão o último ano da linha do tempo nem aparece)
ITEM_STYLES = ("timeline", "proscons", "datasheet", "specs", "score", "poll", "checklist", "comparison", "chart")


def stretch_item_templates(scenes: list[Scene], max_seconds: float = 16.0) -> None:
    for scene in scenes:
        a = scene.analysis
        if a.style in ITEM_STYLES:
            a.template_seconds = round(min(max_seconds, max(a.template_seconds, scene.duration - 0.2)), 3)


def stretch_title_templates(scenes: list[Scene], max_seconds: float = 6.5) -> None:
    """Cena curta de título ("Número 1: abrir os vidros") fica inteira no título animado."""
    for scene in scenes:
        a = scene.analysis
        if a.style in TITLE_STYLES and scene.duration <= max_seconds:
            a.template_seconds = round(scene.duration, 3)


def add_cta_overlays(scenes: list[Scene], subject: str = "") -> list[str]:
    """"Se inscreva e ativa o sininho" -> cursor clicando em like, inscrever-se e sininho.

    Vale também quando a IA pôs um título/card nessa cena: na chamada para o canal o botão
    animado sobre um take bonito do carro funciona melhor. As buscas da cena viram takes do
    próprio carro (a IA costuma pedir "pessoa digitando no celular", que ninguém aprova)."""
    from .motion.i18n import has_cta

    notes: list[str] = []
    for scene in scenes:
        a = scene.analysis
        if (a.overlay or {}).get("kind") == "subscribe":
            continue
        if a.style not in ("normal", "headline", "chapter", "quote", "card", "hud", "mosaic", "vhs"):
            continue
        if not has_cta(scene.text):
            continue
        a.style, a.template_seconds = "normal", 0.0
        a.overlay = {"kind": "subscribe", "title": "", "detail": "", "title_b": "", "detail_b": "", "lines": []}
        car = (a.subject or subject).lower()
        if car:
            a.queries = [f"{car} driving", f"{car} exterior", f"{car} front", f"{car} road"]
            a.visual_summary = f"{car} driving, beauty shot"
            a.fallback_query = car
        notes.append(f"cena {scene.index}: botão de inscrição animado na chamada para o canal")
    return notes
