"""Templates no estilo documentário (fonte condensada, azul-marinho, amarelo de destaque).

* ``headline`` — título de destaque SEM imagem: fundo azul-marinho com grade, título
  digitado letra a letra (com cursor) e subtítulo que sobe. 1–3 s, para um nome,
  um dado forte ou a virada do roteiro.
* ``triptych`` — 3 (ou 2) vídeos lado a lado, cada um com um valor grande em amarelo
  ("47 PSI · 42 PSI · 37 PSI").
* ``counter`` — porcentagem contando com barra vertical enchendo, sobre a mídia em
  duotone azul, e a frase-título no topo.
* ``chart``   — gráfico de barras animado com os números do roteiro.
* ``quote``   — frase sobre a imagem escurecida com as palavras fortes em marca-texto amarelo.
* ``scrapbook`` — fotos com borda picotada entrando uma a uma num papel quadriculado.

Cada template também diz quando tocar os efeitos sonoros (``cues``), com os mesmos
tempos da animação — assim o som bate com o que aparece na tela.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .templates import Template, TemplateSpec
from .templates_auto import TextCache, _blurred_source

YELLOW = (255, 206, 38)
TEAL = (64, 214, 200)
Cue = tuple[float, str, float]  # (segundos no shot, efeito, ganho)


def _navy_bg(tpl: Template, extra: int) -> Image.Image:
    return gfx.background("navy", tpl.w, tpl.h, round(96 * tpl.u), extra=extra).convert("RGBA")


@lru_cache(maxsize=4)
def _sweep_band(w: int, h: int) -> Image.Image:
    """Faixa de luz já desfocada (desenhada 1 vez; depois só muda de lugar)."""
    band, slant, pad = round(w * 0.12), round(h * 0.35), 90
    layer = Image.new("RGBA", (band + slant + 2 * pad, h), (90, 130, 255, 0))
    d = ImageDraw.Draw(layer)
    x = slant + pad
    d.polygon([(x, 0), (x + band, 0), (x + band - slant, h), (x - slant, h)], fill=(90, 130, 255, 18))
    return layer.filter(ImageFilter.GaussianBlur(30))


def _sweep(canvas: Image.Image, t: float, w: int, h: int, period: float = 3.2) -> None:
    """Faixa de luz diagonal bem sutil atravessando o fundo."""
    band = _sweep_band(w, h)
    x = round(((t / period) % 1.0) * (w * 1.6) - w * 0.3) - round(h * 0.35) - 90
    if x >= w or x + band.width <= 0:
        return
    left, right = max(0, -x), min(band.width, w - x)
    canvas.alpha_composite(band, (x + left, 0), (left, 0, right, h))


def _number(spec: TemplateSpec) -> tuple[float, int, str, str, str, float]:
    n = spec.number or {}
    value = float(n.get("value") or 0)
    dec = int(n.get("decimals") or 0)
    prefix, suffix = str(n.get("prefix") or ""), str(n.get("suffix") or "")
    label = str(n.get("label") or "")
    mx = float(n.get("max") or 0)
    return value, dec, prefix, suffix, label, mx


# ---------------------------------------------------------------- headline
class Headline(Template):
    TYPE_START = 0.12
    MAX_CPS = 24.0  # letras por segundo no máximo
    PAD = 16 * 2 + 5  # margem que o text_block deixa para a sombra (shadow=5, blur=16)

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(96 * u)
        self.bg = _navy_bg(self, self.cell)
        self.title = (spec.keyword or "").strip().upper()
        size = 190 if len(self.title) <= 14 else 150 if len(self.title) <= 24 else 118
        self.f_title = gfx.font("condensed", round(size * u))
        self.lines = gfx.wrap(self.title, self.f_title, round(w * 0.84), 2) or [""]
        self.type_end = self.type_window(spec)[1]
        self.sub = gfx.text_block([spec.subtitle.upper()], gfx.font("oswald_light", round(50 * u)), (205, 214, 240),
                                  shadow=2) if spec.subtitle else None
        asc, desc = self.f_title.getmetrics()
        self.lh = int((asc + desc) * 0.98)
        self.cache = TextCache(self.f_title, (255, 255, 255), line_gap=0.98, shadow=5, shadow_blur=16)

    @classmethod
    def type_window(cls, spec: TemplateSpec) -> tuple[float, float]:
        n = max(1, len((spec.keyword or "").strip()))
        dur = spec.frames / spec.fps
        speed = max(n / max(0.3, dur * 0.45), 1.0)
        return cls.TYPE_START, cls.TYPE_START + n / min(cls.MAX_CPS, max(speed, 10.0))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        t0, t1 = cls.type_window(spec)
        out: list[Cue] = [(0.0, "whoosh_soft", 0.5), (t0, f"typing:{t1 - t0:.3f}", 0.8)]
        if spec.subtitle:
            out.append((t1 + 0.08, "pop", 0.55))
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=14).copy()
        _sweep(canvas, t, w, h)
        t0, t1 = self.TYPE_START, self.type_end
        shown = len(self.title) if t >= t1 else int(len(self.title) * gfx.progress(t, t0, t1 - t0))
        # distribui as letras digitadas pelas linhas
        remaining, visible = shown, []
        for line in self.lines:
            visible.append(line[:max(0, remaining)])
            remaining -= len(line) + 1
        block_h = self.lh * len(self.lines)
        has_sub = self.sub is not None
        y0 = h / 2 - block_h / 2 - (40 * u if has_sub else 0)
        d = ImageDraw.Draw(canvas)
        # barra de destaque acima do título
        pb = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.35))
        d.rectangle([w / 2 - 70 * u * pb, y0 - 34 * u, w / 2 + 70 * u * pb, y0 - 24 * u], fill=(*gfx.accent(), 255))
        cursor_at = None
        for k, (full, part) in enumerate(zip(self.lines, visible)):
            full_w = gfx.text_width(full, self.f_title)
            x = w / 2 - full_w / 2
            y = y0 + k * self.lh
            if part:
                layer = self.cache.get(part)
                gfx.paste_at(canvas, layer, x - self.PAD, y - self.PAD)  # desconta a margem da sombra
            if part != full and cursor_at is None:
                cursor_at = (x + gfx.text_width(part, self.f_title) + 8 * u, y)
        if cursor_at is None and t < t1 + 0.6:
            last = self.lines[-1]
            cursor_at = (w / 2 + gfx.text_width(last, self.f_title) / 2 + 10 * u, y0 + (len(self.lines) - 1) * self.lh)
        if cursor_at is not None and (t < t1 or int(t * 4) % 2 == 0):
            cx, cy = cursor_at
            d.rectangle([cx, cy + self.lh * 0.12, cx + 14 * u, cy + self.lh * 0.86], fill=(*gfx.accent(), 255))
        if self.sub is not None:
            q = gfx.ease_out_cubic(gfx.progress(t, t1 + 0.08, 0.45))
            gfx.paste_center(canvas, self.sub, w / 2, y0 + block_h + 44 * u + (1 - q) * 26 * u, alpha=q)
        return canvas


# ---------------------------------------------------------------- triptych
class Triptych(Template):
    STAGGER = 0.12
    LABEL_START = 0.5
    LABEL_STEP = 0.28

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.n = max(1, min(3, len(spec.media)))
        self.gap = max(4, round(8 * u))
        self.pw = (w - self.gap * (self.n - 1)) // self.n
        self.panels = [self.src(ref, self.pw, h, zoom=1.06) for ref in spec.media[:self.n]]
        labels = [str(x).upper() for x in (spec.labels or [])][:self.n]
        size = round((150 if self.n == 3 else 190) * u)
        fnt = gfx.font("condensed", size)
        self.labels = [gfx.outlined_text(lbl, fnt, (*gfx.accent(), 255), max(3, round(6 * u)), (12, 12, 16, 255))
                       if lbl else None for lbl in labels]
        self.labels += [None] * (self.n - len(self.labels))
        # gradiente escuro embaixo para o número ler bem
        grad = np.linspace(0, 1, h, dtype=np.float32)
        a = (np.clip((grad - 0.55) / 0.45, 0, 1) ** 1.4 * 170).astype(np.uint8)
        self.shade = Image.fromarray(np.repeat(a[:, None], self.pw, axis=1), "L")

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(3, len(spec.media)))
        out: list[Cue] = [(k * cls.STAGGER, "whoosh", 0.45) for k in range(n)]
        out += [(cls.LABEL_START + k * cls.LABEL_STEP, "impact_soft", 0.55) for k in range(n) if k < len(spec.labels)]
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = Image.new("RGBA", (w, h), (240, 240, 240, 255))
        for k, src in enumerate(self.panels):
            p = gfx.ease_out_cubic(gfx.progress(t, k * self.STAGGER, 0.45))
            x = k * (self.pw + self.gap)
            direction = -1 if k % 2 == 0 else 1
            y = round(direction * (1 - p) * h * 0.35)
            panel = src.frame(i).convert("RGBA")
            panel.putalpha(255)
            shade = Image.new("RGBA", panel.size, (0, 0, 0, 0))
            shade.putalpha(self.shade)
            panel.alpha_composite(shade)
            canvas.paste(panel, (x, y))
            label = self.labels[k]
            if label is not None:
                q = gfx.progress(t, self.LABEL_START + k * self.LABEL_STEP, 0.3)
                if q > 0:
                    pop = gfx.ease_out_back(q, 2.2)
                    lay = gfx.scaled(label, max(0.05, pop))
                    gfx.paste_center(canvas, lay, x + self.pw / 2, h * 0.84, alpha=min(1.0, q * 2))
        return canvas


# ----------------------------------------------------------------- counter
class Counter(Template):
    COUNT_START, COUNT_DUR = 0.35, 1.3

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bgsrc = _blurred_source(self, spec.media[0], zoom=1.12) if spec.media else None
        self.fallback = _navy_bg(self, 0)
        self.value, self.dec, self.prefix, self.suffix, label, mx = _number(spec)
        if "%" in self.suffix:
            self.frac = min(1.0, self.value / 100)
        else:
            self.frac = min(1.0, self.value / mx) if mx > 0 else 0.75
        self.nums = TextCache(gfx.font("condensed", round(230 * u)), (255, 255, 255), shadow=5, shadow_blur=14)
        self.final = gfx.fmt_number(self.value, self.dec, self.prefix, self.suffix)
        title = (spec.keyword or "").upper()
        f_title = gfx.font("condensed", round(96 * u))
        self.title = gfx.text_block(gfx.wrap(title, f_title, round(w * 0.9), 1), f_title, (255, 255, 255),
                                    shadow=3) if title else None
        self.label = gfx.text_block([label.upper()], gfx.font("oswald", round(44 * u)), (225, 232, 255), shadow=2) \
            if label else None
        self.palette = np.array([(6, 16, 60), (40, 90, 230), (150, 225, 245)], np.float32)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(0.0, "whoosh_soft", 0.45)]
        out += [(cls.COUNT_START + k * 0.09, "tick", 0.35) for k in range(int(cls.COUNT_DUR / 0.09))]
        out.append((cls.COUNT_START + cls.COUNT_DUR, "ding", 0.55))
        return out

    def _background(self, i: int, t: float) -> Image.Image:
        if self.bgsrc is None:
            return self.fallback.copy()
        small = self.bgsrc.frame(i).filter(ImageFilter.GaussianBlur(2))
        lum = np.asarray(small.convert("L"), np.float32) / 255
        lum = 0.15 + lum * 0.75
        # duotone azul (como a "lavagem de cor" do vídeo de referência)
        rgb = np.stack([np.interp(lum, [0, 0.5, 1], self.palette[:, c]) for c in range(3)], axis=-1)
        return Image.fromarray(rgb.clip(0, 255).astype(np.uint8)).resize((self.w, self.h), Image.BILINEAR).convert("RGBA")

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self._background(i, t)
        d = ImageDraw.Draw(canvas)
        if self.title is not None:
            q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.4))
            gfx.paste_center(canvas, self.title, w / 2, h * 0.14 - (1 - q) * 20 * u, alpha=q * 0.92)
        q = gfx.ease_out_cubic(gfx.progress(t, self.COUNT_START, self.COUNT_DUR))
        # barra vertical: contorno fino, enche de baixo para cima
        bx, bw, top, bot = w * 0.36, 56 * u, h * 0.3, h * 0.78
        pa = gfx.progress(t, 0.1, 0.3)
        if pa > 0:
            alpha = int(255 * pa)
            d.rectangle([bx, top, bx + bw, bot], outline=(255, 255, 255, alpha), width=max(2, round(3 * u)))
            fill_top = min(bot - 6 * u, bot - (bot - top) * self.frac * q)
            if fill_top < bot - 6 * u - 1:
                d.rectangle([bx + 6 * u, fill_top, bx + bw - 6 * u, bot - 6 * u], fill=(*gfx.accent(), alpha))
        txt = gfx.fmt_number(self.value * q, self.dec, self.prefix, self.suffix) if q < 1 else self.final
        pop = 1 + 0.06 * math.sin(math.pi * gfx.progress(t, self.COUNT_START + self.COUNT_DUR, 0.3))
        layer = gfx.scaled(self.nums.get(txt), pop)
        gfx.paste_at(canvas, layer, w * 0.44, (top + bot) / 2 - layer.height / 2, alpha=gfx.progress(t, 0.15, 0.3))
        if self.label is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, self.COUNT_START + self.COUNT_DUR * 0.6, 0.4))
            gfx.paste_at(canvas, self.label, w * 0.445, bot - self.label.height + (1 - ql) * 18 * u, alpha=ql)
        return canvas


# ------------------------------------------------------------------- chart
class Chart(Template):
    BAR_START, BAR_STEP, BAR_DUR = 0.45, 0.3, 0.8

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(96 * u)
        self.bg = _navy_bg(self, self.cell)
        rows = []
        for st in (spec.stats or [])[:5]:
            parsed = gfx.parse_number(str(st.get("value", "")))
            if parsed is None:
                continue
            rows.append((str(st.get("label", "")), parsed[0], parsed[1], str(st.get("unit", "") or "")))
        self.rows = rows
        self.max = max((r[1] for r in rows), default=1.0) or 1.0
        f_lbl = gfx.font("oswald", round(46 * u))
        self.labels = [gfx.text_block([r[0].upper()], f_lbl, (230, 236, 255)) for r in rows]
        self.nums = TextCache(gfx.font("condensed", round(76 * u)), (255, 255, 255))
        title = (spec.keyword or "").upper()
        self.title = gfx.text_block([title], gfx.font("oswald_bold", round(64 * u)), (255, 255, 255), shadow=2) \
            if title else None
        self.label_w = max((l.width for l in self.labels), default=0)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len([s for s in (spec.stats or [])[:5] if gfx.parse_number(str(s.get("value", ""))) is not None])
        out: list[Cue] = [(0.0, "whoosh_soft", 0.4)]
        for k in range(n):
            out.append((cls.BAR_START + k * cls.BAR_STEP, "whoosh", 0.3))
            out.append((cls.BAR_START + k * cls.BAR_STEP + cls.BAR_DUR, "pop", 0.45))
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=10).copy()
        d = ImageDraw.Draw(canvas)
        x0 = w * 0.08
        if self.title is not None:
            q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.4))
            gfx.paste_at(canvas, self.title, x0 - (1 - q) * 30 * u, h * 0.1, alpha=q)
            d.rectangle([x0, h * 0.1 + self.title.height + 4 * u, x0 + 120 * u * q, h * 0.1 + self.title.height + 12 * u],
                        fill=(*gfx.accent(), 255))
        n = max(1, len(self.rows))
        top, bot = h * 0.3, h * 0.88
        row_h = (bot - top) / n
        bar_x = x0 + self.label_w + 36 * u
        bar_max = w * 0.9 - bar_x - 170 * u
        for k, (label, value, dec, unit) in enumerate(self.rows):
            st = self.BAR_START + k * self.BAR_STEP
            q = gfx.ease_out_cubic(gfx.progress(t, st, self.BAR_DUR))
            y = top + k * row_h
            bh = min(row_h * 0.52, 74 * u)
            cy = y + row_h / 2
            gfx.paste_at(canvas, self.labels[k], x0, cy - self.labels[k].height / 2, alpha=gfx.progress(t, st - 0.2, 0.3))
            length = bar_max * (value / self.max) * q
            color = gfx.accent() if k % 2 == 0 else TEAL
            if length > 1:
                d.rectangle([bar_x, cy - bh / 2, bar_x + length, cy + bh / 2], fill=(*color, 255))
            if q > 0:
                txt = gfx.fmt_number(value * q, dec) + (f" {unit}" if unit else "")
                gfx.paste_at(canvas, self.nums.get(txt), bar_x + length + 16 * u, cy - 44 * u)
        return canvas



# ------------------------------------------------------------------- quote
HIGHLIGHT = (240, 196, 40)


class Quote(Template):
    """Frase de impacto sobre a imagem escurecida: começo em texto branco e as palavras
    fortes em "marca-texto" amarelo com letra cursiva, uma de cada vez."""

    WORD_START, WORD_STEP = 0.2, 0.09
    BOX_STEP, BOX_WIPE = 0.38, 0.22
    PAD = 6 * 2 + 2  # margem do text_block (shadow=2, blur=6)

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bgsrc = self.src(spec.media[0], w // 2, h // 2, zoom=1.08) if spec.media else None
        self.fallback = gfx.background("carbon", w, h, round(90 * u)).convert("RGBA")
        cell = max(8, round(22 * u))
        grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(grid)
        for x in range(0, w, cell):
            d.line([(x, 0), (x, h)], fill=(255, 255, 255, 14))
        for y in range(0, h, cell):
            d.line([(0, y), (w, y)], fill=(255, 255, 255, 14))
        self.grid = grid
        self.f_lead = gfx.font("montserrat", round(72 * u))
        self.f_mark = gfx.font("script", round(84 * u))
        self.x0 = round(70 * u)
        self.words = (spec.keyword or "").split()
        self.word_layers = [gfx.text_block([wd], self.f_lead, (255, 255, 255), shadow=2, shadow_blur=6)
                            for wd in self.words]
        self.space = gfx.text_width(" ", self.f_lead) + 2 * u
        chunks = [str(x) for x in spec.labels if str(x).strip()][:3]
        self.kicker = spec.subtitle.strip()
        self.boxes = [self._box(c) for c in chunks] + ([self._box(self.kicker)] if self.kicker else [])
        self.lead_end = self.WORD_START + self.WORD_STEP * max(0, len(self.words) - 1) + 0.2
        self.layout = self._layout(len(chunks))

    @classmethod
    def timings(cls, spec: TemplateSpec) -> tuple[float, list[float]]:
        n_words = len((spec.keyword or "").split())
        lead_end = cls.WORD_START + cls.WORD_STEP * max(0, n_words - 1) + 0.2
        n = len([x for x in spec.labels if str(x).strip()][:3]) + (1 if spec.subtitle.strip() else 0)
        starts = [lead_end + 0.1 + k * cls.BOX_STEP for k in range(n)]
        if spec.subtitle.strip() and starts:
            starts[-1] += 0.2  # a frase final entra com uma pausa
        return lead_end, starts

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        lead_end, starts = cls.timings(spec)
        out: list[Cue] = []
        if spec.keyword:
            out.append((cls.WORD_START, f"typing:{max(0.15, lead_end - cls.WORD_START - 0.1):.3f}", 0.35))
        for k, st in enumerate(starts):
            last = k == len(starts) - 1 and bool(spec.subtitle.strip())
            out.append((st, "impact_soft" if last else "swipe", 0.45 if last else 0.5))
        return out

    def _box(self, text: str) -> Image.Image:
        u = self.u
        pad_x, pad_y = round(18 * u), round(10 * u)
        tw = gfx.text_width(text, self.f_mark)
        asc, desc = self.f_mark.getmetrics()
        img = Image.new("RGBA", (tw + 2 * pad_x + round(8 * u), asc + desc + 2 * pad_y), (*HIGHLIGHT, 238))
        ImageDraw.Draw(img).text((pad_x, pad_y - round(4 * u)), text, font=self.f_mark, fill=(22, 20, 18, 255))
        return img

    def _layout(self, n_chunks: int) -> list[tuple[float, float]]:
        """Posições das caixas: as frases fortes fluem na linha de baixo; a final vai sozinha."""
        u, w = self.u, self.w
        y_lead = self.h * 0.575
        y = y_lead + (self.word_layers[0].height if self.word_layers else 0) + 6 * u
        x, pos = float(self.x0), []
        for k, box in enumerate(self.boxes):
            new_line = (k == n_chunks and self.kicker) or x + box.width > w * 0.66
            if new_line and k > 0:
                x, y = float(self.x0), y + box.height + 14 * u
            pos.append((x, y))
            x += box.width + 18 * u
        return pos

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        if self.bgsrc is not None:
            small = np.asarray(self.bgsrc.frame(i), np.float32) * 0.42
            canvas = Image.fromarray(small.clip(0, 255).astype(np.uint8)).resize((w, h), Image.BILINEAR).convert("RGBA")
        else:
            canvas = self.fallback.copy()
        canvas.alpha_composite(self.grid)
        x, y = float(self.x0), h * 0.575
        for k, layer in enumerate(self.word_layers):
            q = gfx.ease_out_cubic(gfx.progress(t, self.WORD_START + k * self.WORD_STEP, 0.22))
            pad = self.PAD
            gfx.paste_at(canvas, layer, x - pad, y - pad + (1 - q) * 14 * u, alpha=q)
            x += layer.width - 2 * pad - 4 + self.space
        _, starts = self.timings(self.s)
        for box, (bx, by), st in zip(self.boxes, self.layout, starts):
            q = gfx.ease_out_cubic(gfx.progress(t, st, self.BOX_WIPE))
            if q <= 0:
                continue
            cut = box.crop((0, 0, max(1, round(box.width * q)), box.height))  # marca-texto passando
            gfx.paste_at(canvas, cut, bx, by)
        return canvas


# --------------------------------------------------------------- scrapbook
class Scrapbook(Template):
    """Fotos com borda picotada (como selo) entrando uma a uma num papel quadriculado."""

    LAYOUTS = {
        1: [(0.012, 0.13, 0.56, 0.745)],
        2: [(0.012, 0.19, 0.47, 0.62), (0.605, 0.14, 0.29, 0.72)],
        3: [(0.02, 0.12, 0.44, 0.56), (0.53, 0.07, 0.32, 0.46), (0.5, 0.58, 0.38, 0.37)],
    }

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(98 * u)
        self.bg = gfx.background("channel" if gfx.BRAND["on"] else "notebook", w, h, self.cell).convert("RGBA")
        self.n = max(1, min(3, len(spec.media))) if spec.media else 0
        self.boxes = [(round(x * w), round(y * h), round(bw * w), round(bh * h))
                      for x, y, bw, bh in self.LAYOUTS.get(self.n, [])]
        self.photos = [self.src(ref, bw, bh, zoom=1.06) for ref, (_, _, bw, bh) in zip(spec.media, self.boxes)]
        self.frames_ = [self._frame_layer(bw, bh) for _, _, bw, bh in self.boxes]

    @classmethod
    def starts(cls, spec: TemplateSpec) -> list[float]:
        n = max(1, min(3, len(spec.media))) if spec.media else 0
        dur = spec.frames / spec.fps
        step = min(1.6, max(0.5, dur * 0.7 / max(1, n)))
        return [0.1 + k * step for k in range(n)]

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(t, "snap", 0.5) for t in cls.starts(spec)]

    def _frame_layer(self, bw: int, bh: int) -> Image.Image:
        """Moldura branca com contorno tracejado (picote)."""
        u = self.u
        pad = round(6 * u)
        img = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        dash, gap, lw = round(9 * u), round(5 * u), max(2, round(3 * u))
        W, H = img.size
        for x in range(0, W, dash + gap):
            d.line([(x, lw // 2), (min(W, x + dash), lw // 2)], fill=(252, 252, 250, 255), width=lw)
            d.line([(x, H - 1 - lw // 2), (min(W, x + dash), H - 1 - lw // 2)], fill=(252, 252, 250, 255), width=lw)
        for y in range(0, H, dash + gap):
            d.line([(lw // 2, y), (lw // 2, min(H, y + dash))], fill=(252, 252, 250, 255), width=lw)
            d.line([(W - 1 - lw // 2, y), (W - 1 - lw // 2, min(H, y + dash))], fill=(252, 252, 250, 255), width=lw)
        d.rectangle([pad - lw, pad - lw, W - pad + lw - 1, H - pad + lw - 1], outline=(252, 252, 250, 255), width=lw)
        return img

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        canvas = self.bg.copy()
        for (x, y, bw, bh), src, border, st in zip(self.boxes, self.photos, self.frames_, self.starts(self.s)):
            q = gfx.progress(t, st, 0.35)
            if q <= 0:
                continue
            layer = border.copy()
            pad = (layer.width - bw) // 2
            layer.paste(src.frame(i).convert("RGB"), (pad, pad))
            layer = gfx.with_shadow(layer, 0, round(10 * self.u), (0, round(6 * self.u)), 70)
            s = 0.94 + 0.06 * gfx.ease_out_back(q, 1.4)
            lay = gfx.scaled(layer, s)
            gfx.paste_center(canvas, lay, x + bw / 2, y + bh / 2, alpha=min(1.0, q * 2.5))
        return canvas


DOC_TEMPLATES: dict[str, type[Template]] = {
    "headline": Headline, "triptych": Triptych, "counter": Counter, "chart": Chart,
    "quote": Quote, "scrapbook": Scrapbook,
}


def template_cues(kind: str, spec: TemplateSpec) -> list[Cue]:
    from .templates_pro import pro_cues  # import tardio: templates_pro importa este módulo

    cls: Any = DOC_TEMPLATES.get(kind)
    if cls is not None:
        return cls.cues(spec)
    from .templates_brand import brand_cues
    from .templates_news import news_cues
    from .templates_magnet import magnet_cues
    from .templates_paper import paper_cues
    from .templates_story import story_cues
    from .templates_plus import plus_cues
    from .templates_article import article_cues
    from .templates_docu import docu_cues
    from .templates_map import map_cues

    return (pro_cues(kind, spec) or brand_cues(kind, spec) or news_cues(kind, spec) or paper_cues(kind, spec)
            or magnet_cues(kind, spec) or story_cues(kind, spec) or plus_cues(kind, spec)
            or article_cues(kind, spec) or docu_cues(kind, spec)
            or map_cues(kind, spec) or [])
