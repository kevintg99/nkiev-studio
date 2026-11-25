"""Matéria de site citada na fala (como nos canais de comentário político/economia):

* ``article`` — página branca de matéria sobre o fundo azul-marinho: etiqueta vermelha "FONTE" com o veículo e a
  data (``keyword`` e ``subtitle``), a manchete grande (``body``), linhas de texto cinza e o parágrafo com a
  fala daquele trecho (``captions``). O trecho citado (``labels``) ganha o marca-texto amarelo e a câmera
  aproxima devagar até ele.

O texto do parágrafo é sempre o da narração (nada inventado); a manchete passa pela checagem do diretor (as
palavras dela foram ditas).
"""

from __future__ import annotations

import re

from PIL import Image, ImageDraw

from . import gfx
from .i18n import text as tr
from .templates import Template, TemplateSpec

Cue = tuple[float, str, float]
SHEET = (252, 252, 250)
INK = (20, 22, 28)
GREY = (104, 110, 122)
LINE = (214, 218, 226)
RED = (204, 28, 38)
MARK = (255, 222, 60)
SS = 1.6  # a página é desenhada maior: a câmera aproxima sem perder a nitidez


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def mark_span(words: list[str], quote: str) -> tuple[int, int] | None:
    """Índices [início, fim) das palavras do parágrafo que formam o trecho citado (sem pontuação/caixa)."""
    target = [w for w in (_norm(x) for x in quote.split()) if w]
    norm = [_norm(w) for w in words]
    if not target:
        return None
    n = len(target)
    for k in range(len(norm) - n + 1):
        if norm[k:k + n] == target:
            return k, k + n
    # a IA às vezes corta uma palavra do começo/fim: fica o maior pedaço que casa (mínimo 2 palavras)
    for size in range(n - 1, 1, -1):
        for a in range(n - size + 1):
            part = target[a:a + size]
            for k in range(len(norm) - size + 1):
                if norm[k:k + size] == part:
                    return k, k + size
    return None


class Article(Template):
    T_IN, T_MARK, MARK_DUR, T_PUSH = 0.0, 1.0, 0.7, 1.15

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.dur = spec.frames / self.fps
        self.bg = gfx.background("navy", w, h, round(64 * u), extra=round(64 * u)).convert("RGBA")
        self.cell = round(64 * u)
        para = " ".join(" ".join(c[2] for c in spec.captions).split()) or " ".join(spec.labels)
        quote = next((x for x in spec.labels if str(x).strip()), "")
        self.sheet, self.marks, self.focus = self._sheet(spec, para, quote)
        self.sw, self.sh = self.sheet.width / SS, self.sheet.height / SS  # tamanho na tela (zoom 1)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(0.0, "whoosh_soft", 0.4), (0.35, "snap", 0.35)]
        if any(str(x).strip() for x in spec.labels):
            out.append((cls.T_MARK, "swipe", 0.3))
        return out

    # ------------------------------------------------------------------ página (desenhada uma vez)
    def _sheet(self, spec: TemplateSpec, para: str, quote: str) -> tuple[Image.Image, list, tuple[float, float]]:
        u = self.u * SS
        pw = round(self.w * 0.62 * SS)
        pad = round(64 * u)
        inner = pw - 2 * pad
        f_tag = gfx.font("bold", round(22 * u))
        f_src = gfx.font("bold", round(26 * u))
        f_date = gfx.font("semibold", round(24 * u))
        f_head = gfx.font("black", round(64 * u))
        f_body = gfx.font("semibold", round(31 * u))
        asc, desc = f_body.getmetrics()
        lh = round((asc + desc) * 1.42)

        head = gfx.wrap(" ".join((spec.body or "").split()), f_head, inner, 3)
        words = para.split()
        # o parágrafo cabe em até 5 linhas: com fala longa fica o pedaço em volta do trecho citado
        span = mark_span(words, quote) if quote else None
        lines = self._layout(words, f_body, inner)
        if len(lines) > 5:
            first = 0
            if span is not None:
                first = next((k for k, ln in enumerate(lines) if ln[-1][0] >= span[0]), 0)
                first = max(0, min(first - 1, len(lines) - 5))
            lines = lines[first:first + 5]
        ha, hd = f_head.getmetrics()
        hlh = round((ha + hd) * 1.06)
        top_h = round(48 * u)
        ph = (pad + top_h + round(34 * u) + len(head) * hlh + round(26 * u) + round(30 * u)
              + round(40 * u) + 2 * round(26 * u) + round(24 * u) + len(lines) * lh + round(30 * u)
              + 2 * round(26 * u) + pad)
        img = Image.new("RGBA", (pw, ph), (*SHEET, 255))
        d = ImageDraw.Draw(img)

        # etiqueta "FONTE" + veículo · data
        y = pad
        label = tr("source", spec.lang).upper()
        tw = gfx.text_width(label, f_tag) + round(28 * u)
        d.rounded_rectangle([pad, y, pad + tw, y + top_h], radius=round(6 * u), fill=(*RED, 255))
        ta, td = f_tag.getmetrics()
        d.text((pad + round(14 * u), y + (top_h - ta - td) / 2), label, font=f_tag, fill=(255, 255, 255, 255))
        x = pad + tw + round(18 * u)
        sa, sd = f_src.getmetrics()
        src = gfx.safe_text(" ".join((spec.keyword or "").split()))
        d.text((x, y + (top_h - sa - sd) / 2), src, font=f_src, fill=(*INK, 255))
        if spec.subtitle:
            x += gfx.text_width(src, f_src) + round(10 * u)
            da, dd = f_date.getmetrics()
            d.text((x, y + (top_h - da - dd) / 2), gfx.safe_text(f"·  {spec.subtitle.strip()}"), font=f_date,
                   fill=(*GREY, 255))
        y += top_h + round(34 * u)

        for line in head:  # manchete
            d.text((pad, y), gfx.safe_text(line), font=f_head, fill=(*INK, 255))
            y += hlh
        y += round(26 * u)
        d.rectangle([pad, y, pad + round(inner * 0.34), y + round(14 * u)], fill=(*LINE, 255))  # linha do autor
        y += round(30 * u) + round(40 * u)
        for frac in (1.0, 0.86):  # texto antes do trecho (cinza claro, como a matéria desfocada)
            d.rounded_rectangle([pad, y, pad + round(inner * frac), y + round(12 * u)], radius=round(6 * u),
                                fill=(*LINE, 255))
            y += round(26 * u)
        y += round(24 * u)

        marks: list[tuple[float, float, float, float]] = []  # retângulos do marca-texto, na ordem da leitura
        para_top = y
        for line in lines:
            for k, (idx, wx, wd) in enumerate(line):
                if span is not None and span[0] <= idx < span[1]:
                    ww = gfx.text_width(wd, f_body)
                    # o espaço até a próxima palavra marcada também fica amarelo (uma faixa só)
                    nxt = line[k + 1] if k + 1 < len(line) else None
                    x1 = pad + (nxt[1] if nxt is not None and nxt[0] < span[1] else wx + ww) + round(4 * u)
                    marks.append((pad + wx - round(4 * u), y + round(2 * u), x1, y + asc + desc - round(2 * u)))
            y += lh
        para_bottom = y
        y += round(30 * u)
        for frac in (0.95, 0.7):
            d.rounded_rectangle([pad, y, pad + round(inner * frac), y + round(12 * u)], radius=round(6 * u),
                                fill=(*LINE, 255))
            y += round(26 * u)
        self._para = (lines, f_body, pad, para_top, lh, span)
        if marks:
            fx = (min(m[0] for m in marks) + max(m[2] for m in marks)) / 2
            fy = (marks[0][1] + marks[-1][3]) / 2
        else:
            fx, fy = pw / 2, (para_top + para_bottom) / 2
        return img, marks, (fx / SS, fy / SS)

    @staticmethod
    def _layout(words: list[str], fnt, max_w: int) -> list[list[tuple[int, int, str]]]:
        """Palavras em linhas: (índice, x, palavra)."""
        space = gfx.text_width(" ", fnt)
        lines: list[list[tuple[int, int, str]]] = [[]]
        x = 0
        for k, wd in enumerate(words):
            wd = gfx.safe_text(wd)
            ww = gfx.text_width(wd, fnt)
            if lines[-1] and x + ww > max_w:
                lines.append([])
                x = 0
            lines[-1].append((k, x, wd))
            x += ww + space
        return [ln for ln in lines if ln]

    def _page(self, t: float) -> Image.Image:
        """A página com o marca-texto passando (palavra por palavra, da esquerda para a direita)."""
        q = gfx.progress(t, self.T_MARK, self.MARK_DUR)
        page = self.sheet.copy()
        if self.marks and q > 0:
            d = ImageDraw.Draw(page)
            total = sum(m[2] - m[0] for m in self.marks)
            left = total * gfx.ease_in_out(q)
            for x0, y0, x1, y1 in self.marks:
                if left <= 0:
                    break
                seg = min(x1 - x0, left)
                d.rectangle([x0, y0, x0 + seg, y1], fill=(*MARK, 255))
                left -= seg
        lines, fnt, pad, top, lh, span = self._para
        d = ImageDraw.Draw(page)
        y = top
        for line in lines:  # o texto por cima do amarelo
            for idx, wx, wd in line:
                inside = span is not None and span[0] <= idx < span[1]
                d.text((pad + wx, y), wd, font=fnt, fill=(*(INK if inside else GREY), 255))
            y += lh
        return page

    # ------------------------------------------------------------------ quadro
    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=6 * u)
        q = gfx.ease_out_cubic(gfx.progress(t, self.T_IN, 0.5))
        # câmera: a página inteira na tela e, depois do marca-texto, aproxima até o trecho citado
        fit = min(1.0, (h * 0.86) / self.sh)
        zmax = 1.5 if self.marks else 1.18
        p = gfx.ease_in_out(gfx.progress(t, self.T_PUSH, max(1.0, self.dur - self.T_PUSH - 0.3)))
        zoom = fit * (0.94 + 0.06 * q) * (1 + (zmax - 1) * p)
        page = self._page(t)
        sw, sh = max(1, round(self.sw * zoom)), max(1, round(self.sh * zoom))
        page = page.resize((sw, sh), Image.LANCZOS)
        layer = gfx.with_shadow(page, 0.0, round(24 * u), (0, round(14 * u)), 120)
        # ponto da página que fica no centro da tela: vai do centro da página até o trecho citado
        fx = self.sw / 2 + (self.focus[0] - self.sw / 2) * p
        fy = self.sh / 2 + (self.focus[1] - self.sh / 2) * p
        cx, cy = w / 2 - (fx * zoom - sw / 2), h / 2 - (fy * zoom - sh / 2)
        # sem sobrar fundo dentro da tela quando a página já é maior que ela
        if sw > w:
            cx = min(max(cx, w - sw / 2), sw / 2)
        if sh > h:
            cy = min(max(cy, h - sh / 2), sh / 2)
        gfx.paste_center(canvas, layer, cx, cy + (1 - q) * 70 * u, alpha=min(1.0, q * 1.6))
        return canvas


ARTICLE_TEMPLATES: dict[str, type[Template]] = {"article": Article}


def article_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = ARTICLE_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
