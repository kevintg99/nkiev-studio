"""Decide quais cenas viram motion graphics e prepara os dados de cada template:
tema (alternado), mídias extras, miniaturas do mosaico e legendas sincronizadas."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import AppConfig, TemplatesConfig
from ..cues import is_news_talk
from ..log import get_logger
from ..models import Candidate, Scene, SceneAnalysis, Sentence, Shot, Word
from ..text import STOPWORDS, keywords
from .gfx import THEMES, safe_text
from .templates import MediaRef
from .templates_news import NEWS_BASE
from .templates_paper import PAPER_BASE

if TYPE_CHECKING:  # pragma: no cover
    from ..ranking import ThumbnailFetcher
    from ..search import BrollSearcher, SceneSearchResult
    from ..selection import Selector

log = get_logger("templates")


# versões de família (papel do Dossiê, TV do Broadcast) -> template de origem, cuja lógica elas usam
SKIN_BASE = {**PAPER_BASE, **NEWS_BASE}
SKIN_FALLBACK = {"paper": {"card": "paperfloat", "showcase": "paperfloat", "gallery": "paperpair"},
                 "news": {"card": "newsfloat", "showcase": "newsfloat", "gallery": "videowall"}}
# templates que não usam mídia (não baixam b-roll para o shot)
MEDIA_FREE = {"headline", "statement", "paperstatement", "article", "docphrase", "docbars", "diagram",
              "map", "flagversus"}
# templates com visual próprio, fora do padrão do canal
OWN_LOOK = {"dossier": "dossier", **{k: "dossier" for k in PAPER_BASE}, "filmstrip": "blueprint_white"}
# templates sobre a prancheta (blueprint) do canal
BLUEPRINT_STYLES = {"showcase", "gallery", "sketch"}
# templates que desenham sem mídia (fundo azul-marinho) quando os dados deles existem
OPTIONAL_MEDIA = {"chart", "stat", "gauge", "counter", "speedometer", "price", "datasheet", "score", "proscons",
                  "poll", "comment", "bulletin", "percentbar", "peoplegrid", "receipt", "downtime", "warning",
                  "wordpop", "source", "fromto", "coverage", "steps", "unverified",
                  # qualquer nicho (templates_plus.py): com mídia, ela vira o fundo desfocado
                  "opener", "profile", "yearflash", "flow", "mythfact", "definition", "agenda"}
# templates cujos itens (anos, antes/depois, opções) buscam uma mídia própria cada
ITEM_TEMPLATES = {"comparison": 3, "versus": 2, "triptych": 3, "scrapbook": 3, "timeline": 6, "beforeafter": 2,
                  "papertimeline": 6, "papercompare": 3, "newstimeline": 6, "newscompare": 3,
                  "poll": 4, "profile": 1}  # perfil: a foto da pessoa citada (nunca a imagem da cena)


def style_min(style: str, cfg: TemplatesConfig) -> float:
    return float(cfg.style_min_seconds.get(style, cfg.min_seconds))


def template_length(scene: Scene, cfg: TemplatesConfig) -> float:
    """Duração do template: a que a IA pediu (ou o padrão do estilo), nos limites da config e da cena."""
    style = scene.analysis.style
    wanted = scene.analysis.template_seconds or cfg.default_seconds.get(style, 6.0)
    # nunca menos que o tempo de ler o texto dele (a IA às vezes pede 1,5 s para um título)
    wanted = max(wanted, reading_seconds(_template_text(scene.analysis), lo=0.0, hi=6.0))
    return max(style_min(style, cfg), min(cfg.max_seconds, wanted, scene.duration))


def reading_seconds(text: str, lo: float = 2.5, hi: float = 6.5, entrance: float = 0.7) -> float:
    """Tempo para ler um texto na tela: a animação de entrada + ~0,3 s por palavra + 0,8 s de respiro."""
    words = len((text or "").split())
    return round(max(lo, min(hi, entrance + 0.3 * words + 0.8)), 2)


# o texto que cada template mostra (para o tempo de leitura)
def _template_text(a: SceneAnalysis) -> str:
    style = a.style
    if style in ("statement", "bulletin", "paperstatement", "news", "comment", "article", "docphrase"):
        return a.body or a.caption
    if style in ("quote",):
        return " ".join(a.highlights) or a.caption
    return " ".join(x for x in (a.caption, a.subtitle) if x)


def extend_short_templates(scenes: list[Scene], chosen: dict[int, str], cfg: TemplatesConfig) -> list[str]:
    """Template numa cena curta (a frase de impacto de 2 s, o "Number ten." de 1 s) sumia antes de dar
    para ler: a cena dele avança sobre a seguinte (só b-roll) até o tempo de leitura do texto. Como na TV,
    o cartão fica mais um instante enquanto a fala segue."""
    notes: list[str] = []
    for k, scene in enumerate(scenes[:-1]):
        if scene.index not in chosen:
            continue
        a = scene.analysis
        need = max(style_min(a.style, cfg), reading_seconds(_template_text(a), lo=3.0, hi=6.0))
        short = need - scene.duration
        nxt = scenes[k + 1]
        if short <= 0.05:
            continue
        # a cena seguinte continua com pelo menos 2,5 s de b-roll; se ela também tem template (o "Number two."
        # colado num checklist), o dela só começa um pouco depois, desde que ainda caiba inteiro na cena
        room = nxt.duration - (template_length(nxt, cfg) if nxt.index in chosen else 2.5)
        if nxt.index in chosen and (a.style not in RANK_STYLES or chosen[nxt.index] in SHORT_STYLES):
            continue
        delta = min(short, room)
        if delta < 0.3:
            continue
        scene.end = round(scene.end + delta, 3)
        nxt.start = round(nxt.start + delta, 3)
        notes.append(f"cena {scene.index}: {a.style} ganhou {delta:.1f}s para dar tempo de ler")
    return notes


# quando não cabem todos (intervalo mínimo, fração máxima), ficam os que mais informam:
# gráficos com dados da fala primeiro, títulos e efeitos decorativos por último
STYLE_PRIORITY = {
    "timeline": 9, "datasheet": 9, "specs": 8, "versus": 9, "comparison": 8, "score": 8, "proscons": 8,
    "speedometer": 8, "price": 8, "chart": 7, "counter": 7, "stat": 7, "gauge": 7, "checklist": 7, "poll": 7,
    "comment": 7, "news": 6, "beforeafter": 6, "triptych": 6, "ranking": 6, "quote": 6, "magnifier": 5,
    "blueprint": 5, "scrapbook": 4, "headline": 4, "showcase": 4, "gallery": 4, "sketch": 4, "dossier": 4, "statement": 4, "bulletin": 4,
    "report": 4, "videowall": 4, "paperfloat": 4, "paperpair": 4, "paperstatement": 4, "papertimeline": 9,
    "papercompare": 8, "newsrank": 6, "paperrank": 6, "newsfloat": 4, "newstimeline": 9, "newscompare": 8,
    "percentbar": 7, "peoplegrid": 7, "receipt": 7, "downtime": 7, "warning": 7, "textmask": 4, "photostack": 4,
    "splitpanels": 4, "annotate": 4, "wordpop": 4,
    "source": 6, "fromto": 7, "coverage": 8, "steps": 8, "unverified": 6, "exploded": 5,
    "chapter": 3, "card": 3, "hud": 3, "mosaic": 3, "vhs": 3,
    "opener": 9, "profile": 7, "yearflash": 6, "flow": 8, "mythfact": 8, "definition": 7, "agenda": 7, "filmstrip": 4,
    "article": 7, "docphrase": 4, "docbars": 7, "diagram": 9,
    "map": 9, "flagversus": 8,
}
# cabeçalho do ranking ("Number six." = 1 s): entra mesmo curto e avança sobre a cena seguinte (extend_short_templates)
RANK_STYLES = ("ranking", "newsrank", "paperrank")
# entram mesmo curtos e avançam sobre a cena seguinte: o cabeçalho do ranking e o passo da lista ("And six. Never buy
# rust protection in the finance office." = 3,4 s; o passo 6 sumia por ter menos que o mínimo do estilo)
EXTENDABLE_STYLES = (*RANK_STYLES, "steps")
SHORT_STYLES = ("headline", "chapter", "ranking", "statement", "bulletin", "paperstatement", "newsrank", "paperrank")  # títulos curtos são separadores: só não podem sobrepor


def select_template_scenes(scenes: list[Scene], cfg: TemplatesConfig, audio_duration: float) -> dict[int, str]:
    """Aceita o estilo sugerido pelo plano respeitando intervalo mínimo e fração máxima do vídeo.
    Se não couberem todos, escolhe pelos mais informativos (``STYLE_PRIORITY``) e, no empate, pela ordem."""
    if not cfg.enabled:
        return {}
    cands = [s for s in scenes if s.analysis.style != "normal" and s.analysis.style in cfg.styles
             and s.duration >= (min(0.8, style_min(s.analysis.style, cfg)) if s.analysis.style in EXTENDABLE_STYLES
                                else style_min(s.analysis.style, cfg))]
    cands.sort(key=lambda s: (-STYLE_PRIORITY.get(s.analysis.style, 5), s.start))
    chosen: dict[int, tuple[float, float, str]] = {}
    by_index = {s.index: s for s in scenes}
    used = 0.0
    for scene in cands:
        style = scene.analysis.style
        est = template_length(scene, cfg)
        a0, a1 = scene.start, scene.start + est
        if style in RANK_STYLES:
            # cabeçalho do ranking colado num template já escolhido logo depois: fica só na própria cena
            # (extend_short_templates empurra o outro um pouco), em vez de o "Number two." sumir
            later = [b0 for b0, _, _ in chosen.values() if a0 < b0 < a1]
            if later and min(later) - a0 >= 0.8:
                a1 = min(later)
        ok = True
        shrink: dict[int, float] = {}
        for idx, (b0, b1, other) in chosen.items():
            gap = 0.0 if style in SHORT_STYLES or other in SHORT_STYLES else cfg.min_gap_seconds
            if a0 >= b1 + gap or b0 >= a1 + gap:
                continue
            # o template de antes termina colado neste: encurta o de antes se ele ainda couber
            new_len = a0 - gap - b0
            other_scene = by_index[idx]
            floor = 0.8 if other in RANK_STYLES else max(style_min(other, cfg), 0.65 * template_length(other_scene, cfg), 3.0)
            if b0 < a0 and new_len >= floor:
                shrink[idx] = new_len
                continue
            ok = False
            break
        # títulos curtos (1–3 s) não entram na conta da fração máxima do vídeo
        cost = 0.0 if style in SHORT_STYLES else est
        if not ok or (used + cost - sum(chosen[i][1] - chosen[i][0] - n for i, n in shrink.items())) \
                / max(1e-6, audio_duration) > cfg.max_share:
            continue
        for idx, new_len in shrink.items():
            b0, b1, other = chosen[idx]
            used -= (b1 - b0) - new_len
            chosen[idx] = (b0, b0 + new_len, other)
            by_index[idx].analysis.template_seconds = round(new_len, 2)
        chosen[scene.index] = (a0, a1, style)
        used += cost
    result = {i: chosen[i][2] for i in sorted(chosen)}
    if result:
        log.info("cenas com motion graphics", extra={"stage": "templates", "scenes": result})
    return result


def _ov(kind: str, title: str, detail: str = "", lines: list[str] | None = None) -> dict:
    return {"kind": kind, "title": title[:40], "detail": detail[:48], "title_b": "", "detail_b": "",
            "lines": [ln[:60] for ln in (lines or [])][:4]}


def overlay_fallbacks(scenes: list[Scene], chosen: dict[int, str], lang: str = "pt") -> list[str]:
    """Template que não coube (intervalo mínimo/fração do vídeo) vira um overlay com os
    mesmos dados sobre o b-roll, em vez de sumir: 0–100 vira selo, a lupa vira um
    callout no detalhe, prós e contras vira painel..."""
    from ..cues import CUES
    from .gfx import fmt_number
    from .i18n import text as tr

    notes: list[str] = []
    for scene in scenes:
        a = scene.analysis
        if a.style == "normal" or scene.index in chosen or a.overlay:
            continue
        n = a.number or {}
        value = float(n.get("value") or 0)
        num = fmt_number(value, int(n.get("decimals") or 0), str(n.get("prefix") or ""),
                         (" " + str(n.get("suffix")).strip()) if n.get("suffix") else "") if value else ""
        ov: dict = {}
        if a.style in ("speedometer", "gauge", "stat", "price", "counter") and num:
            ov = _ov("badge" if a.style in ("speedometer", "gauge") else "label", num, str(n.get("label") or a.caption))
        elif a.style in ("magnifier", "hud") and a.caption:
            ov = _ov("callout", a.caption)
        elif a.style in ("datasheet", "specs", "score") and len(a.stats) >= 2:
            lines = [f"{st.get('label', '')}: {st.get('value', '')} {st.get('unit', '')}".strip() for st in a.stats]
            title = CUES.get(lang, CUES["pt"])["specs_title"]
            ov = _ov("panel", a.caption or title, "", lines)
        elif a.style in ("proscons", "checklist") and len(a.points) >= 2:
            ov = _ov("panel", a.caption or f"{tr('pros', lang)} / {tr('cons', lang)}", "",
                     [("+ " if p.get("positive", True) else "– ") + str(p.get("text", "")) for p in a.points])
        elif a.style == "poll" and len(a.items) >= 2:
            ov = _ov("bullets", a.caption, "", [i.get("label", "") for i in a.items])
        elif a.style in ("headline", "chapter", "quote") and a.caption and len(a.caption.split()) <= 5:
            ov = _ov("keyword", a.caption)
        if ov:
            a.overlay = ov
            notes.append(f"cena {scene.index}: {a.style} não coube como template e virou overlay {ov['kind']}")
    return notes


# ----------------------------------------------------------------- legendas
def estimated_words(sentences: list[Sentence]) -> list[Word]:
    """Sem transcrição: distribui as palavras de cada frase pelo tempo dela."""
    out: list[Word] = []
    for s in sentences:
        parts = s.text.split()
        if not parts:
            continue
        step = (s.end - s.start) / len(parts)
        out.extend(Word(p, s.start + k * step, s.start + (k + 1) * step) for k, p in enumerate(parts))
    return out


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(",;:")


def _join(words: list[Word], max_words: int) -> str:
    text = " ".join(w.text.strip() for w in words[:max_words])
    return _clean(text) + ("…" if len(words) > max_words else "")


def _mention(inside: list[Word], label: str, after: float) -> float | None:
    """Instante em que o item é falado (casa a 1ª palavra relevante do rótulo)."""
    targets = keywords(label, min_len=3)
    if not targets:
        return None
    for w in inside:
        if w.start >= after and any(k.startswith(targets[0][:5]) for k in keywords(w.text, min_len=3)):
            return w.start
    return None


def _number_mention(inside: list[Word], label: str) -> float | None:
    """Instante em que o número do rótulo é falado ("8–9" -> a palavra "8")."""
    m = re.search(r"\d+(?:[.,]\d+)?", label)
    if not m:
        return None
    target = m.group(0).replace(".", ",")
    for w in inside:
        if w.text.strip(".,;:!?()«»\"'").replace(".", ",").startswith(target):
            return w.start
    return None


def caption_groups(words: list[Word], t0: float, t1: float, groups: int,
                   labels: list[str] | None = None) -> list[tuple[float, float, str]]:
    """Divide a fala de [t0, t1] em ``groups`` trechos (um por card do comparativo).

    Com ``labels``, cada card começa quando o item é mencionado na fala; sem
    menção, o tempo é dividido igualmente."""
    inside = [w for w in words if w.end > t0 and w.start < t1]
    span = (t1 - t0) / groups
    bounds = [t0 + g * span for g in range(groups)]
    if labels and len(labels) == groups:
        last = t0
        for g in range(1, groups):
            hit = _mention(inside, labels[g], last + 0.4)
            if hit is not None and hit < t1 - 0.6:
                bounds[g] = last = hit
            else:
                bounds[g] = last = max(bounds[g], last + 0.4)
    bounds[0] = t0
    out: list[tuple[float, float, str]] = []
    for g in range(groups):
        a, b = bounds[g], (bounds[g + 1] if g + 1 < groups else t1)
        chunk = [w for w in inside if a <= (w.start + w.end) / 2 < b]
        out.append((round(a - t0, 3), round(b - t0, 3), _join(chunk, 9)))
    return out


def point_times(words: list[Word], t0: float, t1: float, points: list[str]) -> list[tuple[float, float, str]]:
    """Checklist: cada ponto entra quando é mencionado na fala (senão, em intervalos iguais)."""
    inside = [w for w in words if w.end > t0 and w.start < t1]
    n = max(1, len(points))
    step = (t1 - t0 - 1.2) / n
    out: list[tuple[float, float, str]] = []
    last = t0 + 0.2
    for k, text in enumerate(points):
        hit = _mention(inside, text, last)
        start = hit if hit is not None and hit < t1 - 0.5 else max(last + 0.35, t0 + 0.6 + k * step)
        last = start
        out.append((round(start - t0, 3), round(t1 - t0, 3), text))
    return out


def caption_chunks(words: list[Word], t0: float, t1: float, max_words: int = 6) -> list[tuple[float, float, str]]:
    """Legenda corrida: quebra em pausas ou a cada ``max_words`` palavras."""
    inside = [w for w in words if w.end > t0 and w.start < t1]
    chunks: list[list[Word]] = []
    for w in inside:
        if not chunks or len(chunks[-1]) >= max_words or w.start - chunks[-1][-1].end > 0.35:
            chunks.append([w])
        else:
            chunks[-1].append(w)
    return [(max(0.0, round(c[0].start - t0, 3)), round(c[-1].end - t0, 3), _join(c, max_words)) for c in chunks]


def keyword_for(scene: Scene) -> str:
    if scene.analysis.caption:
        return scene.analysis.caption
    counts = Counter(k for k in keywords(scene.text, min_len=4) if k not in STOPWORDS)
    return counts.most_common(1)[0][0] if counts else ""


# ------------------------------------------------------------------- mídias
def _ref(cand: Candidate, path: Path | str, offset: float = 0.0) -> MediaRef:
    return MediaRef(str(path), duration=cand.duration, offset=offset)



def profile_photos(ranked: list[Candidate], label: str) -> list[Candidate]:
    """Retrato de uma pessoa: só foto que traz o nome dela (título/legenda da Wikimedia). Banco de stock não tem a
    pessoa: "Gerald Bull portrait" trazia um boi. Sem foto certa, o perfil sai com as iniciais. O arquivo seu marcado
    para a fala ("say") vem antes: no vídeo do hidrômetro, a Wikimedia devolvia a foto de um rebocador chamado
    "Reinhard Woltman" e ela ganhava do molinete original do Woltman que estava na biblioteca."""
    from ..providers.local_library import targeted

    name = [w for w in re.findall(r"\w+", label.lower()) if len(w) >= 3]
    found = [c for c in ranked if name and all(
        w in f"{c.description} {' '.join(c.tags)} {c.page_url}".lower() for w in name)]
    return sorted(found, key=lambda c: not targeted(c))


class TemplatePlanner:
    def __init__(self, cfg: AppConfig, selector: "Selector", searcher: "BrollSearcher",
                 thumbs: "ThumbnailFetcher | None") -> None:
        self.cfg = cfg
        self.tcfg = cfg.templates
        self.selector = selector
        self.searcher = searcher
        self.thumbs = thumbs
        # .cache (as mídias ficam em .cache/media): as bandeiras do país x país vão para .cache/flags
        media = getattr(selector, "media_dir", None)
        self.cache_dir = Path(media).parent if media else Path(cfg.paths.cache_dir)
        self.counters: Counter[str] = Counter()
        self.reserved: dict[int, list[MediaRef]] = {}
        self.reserved_labels: dict[int, list[str]] = {}

    def _theme(self, kind: str) -> str:
        n = self.counters[kind]
        self.counters[kind] += 1
        if kind in OWN_LOOK:  # visual próprio (dossiê em papel), mesmo com o padrão do canal
            return OWN_LOOK[kind]
        if self.tcfg.brand_theme:  # padrão do canal em tudo
            # a foto do carro, a galeria e o desenho ficam sobre a "prancheta" (blueprint) do canal
            if kind in BLUEPRINT_STYLES and f"{self.tcfg.brand_theme}_blueprint" in THEMES:
                return f"{self.tcfg.brand_theme}_blueprint"
            return self.tcfg.brand_theme
        cycles = {
            "comparison": self.tcfg.comparison_themes,
            "hud": self.tcfg.hud_themes,
            "specs": self.tcfg.dark_themes,
            "checklist": ["carbon", "paper"],
        }
        fixed = {"blueprint": "engineering", "mosaic": "blueprint", "card": "blueprint", "vhs": "blueprint",
                 "headline": "navy", "triptych": "navy", "counter": "navy", "chart": "navy",
                 "quote": "carbon", "scrapbook": "notebook"}
        fixed.update({k: "navy" for k in ("timeline", "speedometer", "datasheet", "price", "proscons", "score",
                                          "magnifier", "beforeafter", "news", "comment", "poll", "opener", "profile",
                                          "yearflash", "flow", "mythfact", "definition", "agenda", "article",
                                          "map", "flagversus")})
        if kind in cycles:
            options = cycles[kind]
            return options[n % len(options)]
        return fixed.get(kind, "carbon")

    def _skin(self, index: int) -> str:
        """Cara dos templates de edição magnética: a do estilo do vídeo; no Mix, sorteada por template."""
        from .templates_magnet import SKINS

        skin = self.tcfg.skin or "blueprint"
        if skin == "mix":  # papel só se o canal usa o visual de papel
            pool = [k for k in SKINS if k != "paper" or "paperfloat" in self.tcfg.styles]
            return pool[(self.cfg.seed * 31 + index * 7) % len(pool)]
        return skin if skin in SKINS else "blueprint"

    @staticmethod
    def _number_ok(a: SceneAnalysis) -> bool:
        return bool(float((a.number or {}).get("value") or 0))

    @classmethod
    def _data_ok(cls, kind: str, a: SceneAnalysis) -> bool:
        """O template tem os dados que precisa? (senão vira card/título; nunca inventa)"""
        from .gfx import parse_number

        if kind in ("stat", "gauge", "counter", "speedometer", "price"):
            return cls._number_ok(a)
        if kind in ("chart", "score"):
            return cls._chart_ok(a)
        if kind == "datasheet":
            return bool(a.stats)
        if kind == "proscons":
            return any(p.get("positive", True) for p in a.points) and not all(p.get("positive", True) for p in a.points)
        if kind == "poll":
            return len(a.items) >= 2 and bool(a.caption)
        if kind in ("news", "comment", "bulletin"):
            return bool(a.body or a.caption)
        if kind == "magnifier":
            return bool(a.caption)
        if kind in ("percentbar", "receipt", "downtime", "peoplegrid"):
            return cls._number_ok(a)
        if kind == "warning":
            return bool(a.caption)
        if kind in ("source", "unverified"):
            return bool(a.caption)
        if kind == "docbars":
            return cls._chart_ok(a)
        if kind == "docphrase":
            return bool(a.body or a.caption)
        if kind == "diagram":  # esquema com pelo menos 3 peças desenháveis
            from .templates_docu import clean_elements

            return len(clean_elements(a.elements)) >= 3
        if kind == "map":  # pelo menos um lugar ou país que o mapa sabe onde fica
            from .templates_map import resolve

            return any(e["t"] in ("place", "country") for e in resolve(a.elements))
        if kind == "flagversus":  # os dois países
            from .geo import find_country

            return sum(1 for i in a.items[:2] if find_country(i.get("query") or i.get("label", ""))) == 2
        if kind == "article":  # veículo e manchete ou o trecho citado
            return bool(a.caption) and bool(a.body or a.highlights)
        if kind == "fromto":
            return cls._number_ok(a) and (a.number or {}).get("from") is not None
        if kind == "coverage":
            return any(float(p.get("value") or 0) > 0 for p in a.points)
        if kind == "steps":
            return cls._number_ok(a) and bool(a.caption)
        if kind in ("opener", "profile"):
            return bool(a.caption)
        if kind == "yearflash":
            return 1000 <= float((a.number or {}).get("value") or 0) <= 2100
        if kind in ("flow", "agenda"):
            return sum(1 for p in a.points if str(p.get("text", "")).strip()) >= 2
        if kind == "mythfact":
            return any(not p.get("positive", True) for p in a.points) and any(p.get("positive", True) for p in a.points)
        if kind == "definition":
            return bool(a.caption) and bool(a.body)
        return True

    @staticmethod
    def _chart_ok(a: SceneAnalysis) -> bool:
        from .gfx import parse_number

        return sum(parse_number(str(st.get("value", ""))) is not None for st in a.stats) >= 2

    def reserve_items(self, shots: list[Shot], scenes: dict[int, Scene]) -> None:
        """Roda ANTES da distribuição normal: garante as mídias dos itens do comparativo
        (senão os clipes certos podem ir para outros shots)."""
        for shot in shots:
            limit = ITEM_TEMPLATES.get(shot.template or "")
            if limit is None:
                continue
            if shot.template in ("scrapbook", "poll") and not scenes[shot.scene_index].analysis.items:
                continue  # sem itens: usa as fotos da própria cena (no prepare)
            base = SKIN_BASE.get(shot.template or "", shot.template or "")  # papel/TV usam a mesma busca por item
            found = [r for k, it in enumerate(scenes[shot.scene_index].analysis.items[:limit])
                     if (r := self._item_media(scenes[shot.scene_index], it, k, base)) is not None]
            if len(found) >= min(2, limit):
                self.reserved[shot.index] = [ref for _, ref, _ in found]
                self.reserved_labels[shot.index] = [label for _, _, label in found]
                cand, ref, _ = found[0]
                shot.candidate, shot.media_path, shot.fallback_reason = cand, ref.path, "template_items"

    def _item_media(self, scene: Scene, item: dict[str, str], k: int,
                    kind: str = "") -> tuple[Candidate, MediaRef, str] | None:
        from ..cues import language
        from ..query_lang import is_foreign

        query = item.get("query") or item.get("label", "")
        own = [q for q in scene.analysis.queries if q and not is_foreign(q, language())]
        if own and (not query or is_foreign(query, language())):
            # opção da enquete com busca em português ("garrafa no telhado agora"): os bancos são em inglês,
            # vinha imagem qualquer; usa as buscas (em inglês) da própria cena, uma por item
            query = own[k % len(own)]
        fake = Scene(index=-(1000 + scene.index * 10 + k), text=item["label"], start=scene.start, end=scene.end,
                     analysis=SceneAnalysis(visual_summary=query, queries=[query],
                                            fallback_query=query,
                                            negative_terms=scene.analysis.negative_terms, tone=scene.analysis.tone))
        # ano/geração específico (linha do tempo, antes e depois): o banco de vídeo quase nunca tem o
        # carro daquele ano; foto sim (Wikimedia) — então busca foto também, em todos os bancos
        if kind in ("timeline", "beforeafter", "profile"):  # o retrato da pessoa: foto (Wikimedia), não vídeo
            result = self.searcher.search_scene(fake, 1, 1, self.selector.used_keys,
                                                budget=max(self.tcfg.item_search_budget, 5))
        else:
            result = self.searcher.search_scene(fake, 1, 0, self.selector.used_keys,
                                                budget=self.tcfg.item_search_budget)
        ranked = result.ranked
        year = re.search(r"\b(19|20)\d\d\b", f"{item.get('label', '')} {query}")
        if year:
            # foto cujo título/legenda traz o ano falado ("1966 Toyota Corolla") vem primeiro
            y = year.group(0)
            ranked = sorted(ranked, key=lambda c: y not in f"{c.description} {' '.join(c.tags)}")
        if kind == "profile":
            ranked = profile_photos(ranked, item.get("label") or "")
        for cand in ranked:
            path = self.selector.claim(cand)
            if path is not None:
                return cand, _ref(cand, path), item["label"]
        return None

    def _extra_from_pool(self, pool: list[Candidate], exclude: set[str], need: int) -> list[MediaRef]:
        out: list[MediaRef] = []
        for cand in sorted(pool, key=lambda c: -c.score):
            if len(out) >= need:
                break
            if cand.key in exclude:
                continue
            path = self.selector.claim(cand)
            if path is not None:
                out.append(_ref(cand, path))
                exclude.add(cand.key)
        return out

    def _tiles(self, own: list[Candidate], others: list[Candidate]) -> list[str]:
        own_keys = {c.key for c in own}
        pool = own + [c for c in others if c.key not in own_keys]
        if self.thumbs is not None:
            self.thumbs.fetch([c for c in pool[:24] if not c.thumbnail_path])
        tiles = [c.thumbnail_path for c in pool if c.thumbnail_path and Path(c.thumbnail_path).exists()]
        return list(dict.fromkeys(tiles))[:9]

    def prepare(self, shots: list[Shot], scenes: dict[int, Scene], results: dict[int, "SceneSearchResult"],
                words: list[Word]) -> None:
        fps = self.cfg.output.fps
        for shot in shots:
            if not shot.template:
                continue
            # a versão em papel (Dossiê) ou de TV (Broadcast) usa a lógica do template de origem e volta a
            # ser a dela no fim
            skin = shot.template if shot.template in SKIN_BASE else ""
            kind = SKIN_BASE.get(shot.template, shot.template)
            scene = scenes[shot.scene_index]
            a = scene.analysis
            has_media = shot.candidate is not None and shot.media_path is not None
            if not has_media and kind not in MEDIA_FREE:
                if kind in OPTIONAL_MEDIA and self._data_ok(kind, a):
                    pass  # desenham sem mídia (fundo azul-marinho)
                elif keyword_for(scene):
                    kind = "headline"  # sem mídia: vira um título de destaque em vez de sumir
                else:
                    log.warning("template sem mídia principal; shot fica normal",
                                extra={"stage": "templates", "shot": shot.index})
                    shot.template = None
                    continue
            primary = _ref(shot.candidate, shot.media_path, shot.source_offset) if has_media else None
            media = [primary] if primary else []
            tiles: list[str] = []
            if kind in ("comparison", "versus", "triptych") and primary is not None:
                media = self.reserved.get(shot.index, [])
                if len(media) < 2:  # itens não encontrados: usa a mídia da cena
                    own = results.get(scene.index)
                    media = [primary]
                    media += self._extra_from_pool(own.ranked if own else [], set(self.selector.used_keys),
                                                   max(0, 2 - len(media)))
                if len(media) < 2:
                    kind, media = "card", [primary]
                if kind == "versus":
                    media = media[:2]
                if kind == "triptych":
                    media = media[:3]
            elif kind in ("timeline", "beforeafter"):
                need = 2
                media = self.reserved.get(shot.index, [])
                if len(media) < need and kind == "beforeafter" and primary is not None:
                    own = results.get(scene.index)
                    media = [primary] + self._extra_from_pool(own.ranked if own else [],
                                                              set(self.selector.used_keys), 1)
                if len(media) < need:
                    kind, media = ("card", [primary]) if primary is not None else ("headline", [])
            elif kind == "poll":
                # fotos das opções só se achou todas (senão, botões com letra)
                found = self.reserved.get(shot.index, [])
                media = found if len(found) == len(a.items[:4]) else []
            elif kind in ("comment", "news", "price", "speedometer", "magnifier", "bulletin", "report") \
                    and primary is not None:
                media = [primary]
            elif kind in ("datasheet",) and primary is not None:
                media = [primary]
            elif kind in ("proscons", "score"):
                media = []
            elif kind == "scrapbook" and primary is not None:
                dur = shot.frames / fps
                want = 3 if dur >= 6.0 else 2 if dur >= 3.5 else 1
                media = self.reserved.get(shot.index, []) or [primary]
                if len(media) < want:
                    own = results.get(scene.index)
                    near = [c for i in (scene.index - 1, scene.index + 1) if i in results for c in results[i].ranked]
                    pool = (own.ranked if own else []) + near
                    media += self._extra_from_pool(pool, set(self.selector.used_keys), want - len(media))
                media = media[:want]
            elif kind == "profile":
                # a foto da pessoa (busca pelo nome) ou as iniciais: a imagem da cena num círculo com o nome de
                # alguém mentiria
                media = self.reserved.get(shot.index, [])[:1]
            elif kind in ("gallery", "videowall", "photostack", "splitpanels", "filmstrip") and primary is not None:
                # mais fotos do MESMO carro: a cena e as vizinhas da mesma seção (nunca outro carro)
                own = results.get(scene.index)
                near = [c for i in (scene.index - 1, scene.index + 1) if i in results and i in scenes
                        and scenes[i].analysis.subject == a.subject for c in results[i].ranked]
                pool = [c for c in (own.ranked if own else []) + near if not getattr(c, "veto", "")]
                exclude = set(self.selector.used_keys) | ({shot.candidate.key} if shot.candidate else set())
                media = [primary] + self._extra_from_pool(pool, exclude, 2)
                if len(media) < 2 and kind in ("gallery", "videowall"):
                    kind = "showcase" if kind == "gallery" else "report"
            elif kind == "mosaic":
                own = results.get(scene.index)
                others = [c for r in results.values() for c in r.ranked]
                tiles = self._tiles(own.ranked if own else [], others)
                if len(tiles) < 3:
                    kind = "card"
            # sem os dados que o template precisa, ele vira um card (nunca inventa números);
            # sem mídia, vira um título de destaque
            fallback = "card" if media else "headline"
            if kind == "specs" and not a.stats:
                kind = fallback
            if kind in ("stat", "gauge", "counter") and not self._number_ok(a):
                kind = fallback
            if kind == "chart" and not self._chart_ok(a):
                kind = fallback
            if kind == "checklist" and not a.points:
                kind = fallback
            if kind == "quote" and not (a.highlights or a.caption):
                kind = fallback
            if not self._data_ok(kind, a):
                kind = fallback
            if not media and kind in ("comparison", "versus", "triptych", "card", "hud", "vhs", "blueprint",
                                      "quote", "scrapbook", "magnifier", "beforeafter", "timeline", "news",
                                      "showcase", "gallery", "sketch", "dossier", "report", "videowall",
                                      "textmask", "photostack", "splitpanels", "annotate", "exploded", "filmstrip"):
                kind = "headline"  # precisam de imagem e não há nenhuma
            if kind in MEDIA_FREE:
                media = []
            t0, t1 = shot.start_frame / fps, shot.end_frame / fps
            labels = self.reserved_labels.get(shot.index, [])
            if kind == "poll":
                labels = [i.get("label", "") for i in a.items[:4]]
            if kind == "comparison":
                captions = caption_groups(words, t0, t1, len(media), labels or None)
            elif kind in ("checklist", "proscons"):
                captions = point_times(words, t0, t1, [str(p.get("text", "")) for p in a.points[:6]])
            elif kind in ("datasheet", "score"):
                captions = point_times(words, t0, t1, [str(st.get("label", "")) for st in a.stats[:6]])
            elif kind in ("timeline", "poll"):
                names = labels or [i.get("label", "") for i in a.items]
                captions = point_times(words, t0, t1, names[:6])
            elif kind in ("flow", "agenda", "mythfact"):  # cada caixa/item entra quando é falado
                captions = point_times(words, t0, t1, [str(p.get("text", "")) for p in a.points[:5]])
            else:
                captions = caption_chunks(words, t0, t1)
            if kind == "versus" and not labels:
                labels = [i.get("label", "") for i in a.items[:2]]
            if kind == "triptych":
                labels = labels or [i.get("label", "") for i in a.items[:3]]
            if kind in ("quote", "news", "statement", "bulletin", "article"):
                labels = list(a.highlights)
            if kind == "flagversus":  # o nome que a fala usa e a bandeira de cada país
                from .geo import find_country, flag_path

                labels = [i.get("label") or i.get("query", "") for i in a.items[:2]]
                found = [find_country(i.get("query") or i.get("label", "")) for i in a.items[:2]]
                flags = [flag_path(c.iso2, self.cache_dir) if c else None for c in found]
                tiles = [str(f) if f else "" for f in flags]
            if kind in ("timeline", "beforeafter", "poll") and not labels:
                labels = [i.get("label", "") for i in a.items]
            notes = []
            if kind == "timeline":
                by_label = {i.get("label", ""): i.get("note", "") for i in a.items}
                notes = [by_label.get(lb, "") for lb in labels]
            if skin:  # sem os itens (linha do tempo virou card...): o equivalente da mesma família
                family = SKIN_FALLBACK["paper" if skin in PAPER_BASE else "news"]
                kind = skin if kind == SKIN_BASE[skin] else family.get(kind, kind)
            shot.template = kind
            shot.template_data = {
                "theme": self._theme(kind),
                "seed": self.cfg.seed * 1009 + shot.index,
                "media": [asdict(m) for m in media],
                "tiles": tiles,
                "captions": captions,
                "keyword": safe_text(a.caption if kind == "quote" else keyword_for(scene)),
                "show_captions": bool(self.tcfg.captions.get(kind, True)),
                "subtitle": safe_text(a.subtitle),
                "labels": [safe_text(lb) for lb in labels],
                "stats": a.stats,
                "number": a.number,
                "points": a.points,
                "rank": a.rank,
                "index": self.counters["chapter"] if kind == "chapter" else 0,  # _theme() já contou este
                "notes": notes,
                "body": safe_text(a.body),
                "elements": a.elements,
                "lang": self.cfg.analysis.script_language,
                "skin": self._skin(shot.index),
                "tone": "news" if is_news_talk(scene.text) else "",
            }
            log.info("template preparado", extra={"stage": "templates", "shot": shot.index, "kind": kind,
                                                  "theme": shot.template_data["theme"], "media": len(media)})


# ----------------------------------------------------------------- overlays
def plan_overlays(shots: list[Shot], scenes: dict[int, Scene], cfg: AppConfig, words: list[Word]) -> int:
    """Coloca o overlay de cada cena no primeiro b-roll normal dela, começando quando o
    texto é falado (se der para achar) e respeitando um intervalo entre overlays."""
    ocfg = cfg.overlays
    if not ocfg.enabled:
        return 0
    fps = cfg.output.fps
    last_end, count = -1e9, 0
    done: set[int] = set()
    last_shot = {sh.scene_index: sh.index for sh in shots}
    if "crawl" in ocfg.kinds and ocfg.crawl_at:  # o letreiro reserva os trechos dele antes dos outros overlays
        count += plan_crawls(shots, scenes, cfg)
    for shot in shots:
        scene = scenes.get(shot.scene_index)
        if scene is None or shot.scene_index in done:
            continue
        ov = scene.analysis.overlay or {}
        kind = ov.get("kind")
        if kind not in ocfg.kinds or kind == "crawl" or (not ov.get("title") and kind != "subscribe"):
            continue
        dur = shot.frames / fps
        # o CTA de inscrição precisa de tempo para o cursor clicar nos três botões; o resto, de tempo para
        # ler o texto (uma tarja de 1 s na tela ninguém lê)
        text = " ".join([str(ov.get("title", "")), str(ov.get("detail", "")), *map(str, ov.get("lines") or [])])
        need = 2.4 if kind == "subscribe" else 0.0 if kind == "crawl" else reading_seconds(text, lo=2.4, hi=5.0)
        min_dur = max(ocfg.min_shot_seconds, need)
        if shot.template or shot.candidate is None or dur < min_dur or (shot.overlay or {}).get("kind") == "crawl":
            continue
        # legenda que nomeia algo da imagem ("TURBO", "banda de rodagem") só sobre a imagem escolhida
        # para esta cena; plano B (sobra de outra cena, repetição) pode ser uma cidade
        if kind in ("keyword", "arrow", "callout", "tag", "lowerthird") and shot.fallback_reason:
            continue
        t0 = shot.start_frame / fps
        if t0 - last_end < ocfg.min_gap_seconds and kind != "subscribe":
            continue  # o botão de inscrição é a chamada do vídeo: entra mesmo colado em outro overlay
        inside = [w for w in words if t0 <= w.start < t0 + dur]
        if kind == "fuel":
            spoken = _number_mention(inside, str(ov.get("title", "")))
        else:
            spoken = _mention(inside, str(ov.get("title", "")), t0) if kind != "subscribe" else None
        if kind in ("fuel", "phrase") and spoken is None and shot.index < last_shot.get(shot.scene_index, -1):
            continue  # o consumo/a frase entra no shot em que é falado
        start = (spoken - t0) if spoken is not None else 0.3
        # entra quando é falado, mas cedo o bastante para ficar o tempo de leitura na tela
        start = round(max(0.2, min(start, dur - max(need, 1.3))), 3)
        shot.overlay = {**ov, "start": start, "seed": cfg.seed * 7 + shot.index,
                        "lang": cfg.analysis.script_language, "tone": "news" if is_news_talk(scene.text) else ""}
        done.add(shot.scene_index)
        last_end = t0 + dur
        count += 1
    _continue_crawls(shots, fps)
    if count:
        log.info("overlays planejados", extra={"stage": "templates", "overlays": count})
    return count


def _crawl_windows(shots: list[Shot], scenes: dict[int, Scene], fps: int, at: list[float], seconds: float,
                   min_seconds: float = 14.0) -> list[tuple[int, int]]:
    """Trechos para o letreiro: sequências de shots só de b-roll (sem template, sem o botão de inscrição)
    com pelo menos ``min_seconds``; para cada posição pedida (fração do vídeo), o trecho que começa mais
    perto dela, com até ``seconds`` de duração. Dois trechos nunca se encostam."""
    if not shots:
        return []
    total = shots[-1].end_frame / fps

    def free(sh: Shot) -> bool:
        ov = (scenes[sh.scene_index].analysis.overlay or {}) if sh.scene_index in scenes else {}
        return not sh.template and ov.get("kind") != "subscribe"

    windows: list[tuple[int, int]] = []
    for frac in at:
        target = max(10.0, frac * total)  # nunca em cima do gancho dos primeiros segundos
        best: tuple[float, int, int] | None = None
        for i, sh in enumerate(shots):
            if not free(sh) or any(a - 2 <= i <= b + 2 for a, b in windows):
                continue
            t0 = sh.start_frame / fps
            j, length = i, 0.0
            while j < len(shots) and free(shots[j]) and length < seconds and not any(a - 2 <= j <= b + 2 for a, b in windows):
                length += shots[j].frames / fps
                j += 1
            if length < min_seconds:
                continue
            dist = abs(t0 - target)
            if best is None or dist < best[0]:
                best = (dist, i, j - 1)
        if best is not None:
            windows.append((best[1], best[2]))
    return sorted(windows)


def plan_crawls(shots: list[Shot], scenes: dict[int, Scene], cfg: AppConfig) -> int:
    """Letreiro do rodapé em blocos contínuos (~20–25 s), um logo no começo e outro no meio do vídeo
    (``overlays.crawl_at``), só em trechos de b-roll: enquanto ele passa não entra template nem outra
    tarja. As frases vêm da fala do próprio trecho, já com os números escritos ("76.4%")."""
    from ..rhythm import crawl_items
    from ..spoken import screen_text

    fps = cfg.output.fps
    placed = 0
    for i, j in _crawl_windows(shots, scenes, fps, cfg.overlays.crawl_at, cfg.overlays.crawl_seconds):
        chunk = shots[i:j + 1]
        ids = list(dict.fromkeys(sh.scene_index for sh in chunk))
        # números já escritos ("39%", "£1,500"): as frases com número têm prioridade no letreiro
        text = screen_text(" ".join(scenes[k].text for k in ids if k in scenes), cfg.analysis.script_language)
        car = next((scenes[k].analysis.subject for k in ids if k in scenes and scenes[k].analysis.subject), "")
        items = crawl_items(text, limit=6, subject=car)
        if len(items) < 2:
            continue
        t_first = chunk[0].start_frame / fps + 0.4
        for k, sh in enumerate(chunk):
            sh.overlay = {"kind": "crawl", "title": car, "detail": "", "title_b": "", "detail_b": "", "lines": items,
                          "start": 0.4 if k == 0 else 0.0, "enter": k == 0, "leave": k == len(chunk) - 1,
                          "offset": 0.0 if k == 0 else round(sh.start_frame / fps - t_first, 3),
                          "seed": cfg.seed * 7 + chunk[0].index, "lang": cfg.analysis.script_language}
        placed += 1
        log.info("letreiro do rodapé", extra={"stage": "templates", "inicio_s": round(t_first, 1),
                                              "segundos": round(sum(sh.frames for sh in chunk) / fps, 1)})
    return placed


def _continue_crawls(shots: list[Shot], fps: int) -> None:
    """O letreiro do rodapé continua pelos shots seguintes da mesma cena (como na TV, ele não some a
    cada corte): cada shot recebe quanto já rolou (``offset``); só o último sai."""
    for k, first in enumerate(shots):
        ov = first.overlay or {}
        if ov.get("kind") != "crawl" or not ov.get("enter", True):
            continue
        prev = first
        t_first = first.start_frame / fps + float(ov.get("start", 0.0))
        for nxt in shots[k + 1:]:
            if nxt.scene_index != first.scene_index or nxt.template or nxt.overlay:
                break
            prev.overlay = {**prev.overlay, "leave": False}
            nxt.overlay = {**ov, "start": 0.0, "enter": False, "leave": True,
                           "offset": round(nxt.start_frame / fps - t_first, 3), "seed": ov.get("seed", 0)}
            prev = nxt
