"""Templates voltados a retenção (pensados para o nicho automotivo, mas genéricos).

* ``specs``     — ficha técnica: foto + linhas com número contando e barra animada.
* ``stat``      — número gigante contando (preço, FIPE, 0–100) sobre o vídeo desfocado.
* ``gauge``     — velocímetro: ponteiro varrendo até o valor, leitura digital.
* ``versus``    — tela dividida na diagonal, selo VS, nomes e linhas de comparação.
* ``blueprint`` — desenho técnico: traço de engenharia sendo "plotado", cotas e revelação da foto.
* ``checklist`` — prós e contras com ✓/✗ entrando no ritmo da fala.
* ``ranking``   — posição gigante (#3) com foto e nome.
* ``chapter``   — título de capítulo animado sobre o vídeo escurecido.

Regra de desempenho: tudo que não muda entre frames é desenhado uma vez só.
"""

from __future__ import annotations

import math
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .templates import Template, TemplateSpec

LOWER_IS_BETTER = ("0-100", "0 a 100", "preço", "preco", "tempo", "peso", "frenagem", "custo", "consumo urbano l")


class TextCache:
    """Camadas de texto já renderizadas (números contando repetem muito)."""

    def __init__(self, fnt, fill, **kw) -> None:
        self.fnt, self.fill, self.kw, self.cache = fnt, fill, kw, {}

    def get(self, text: str) -> Image.Image:
        img = self.cache.get(text)
        if img is None:
            img = self.cache[text] = gfx.text_block([text], self.fnt, self.fill, **self.kw)
        return img


def _number_value(stat: dict) -> tuple[float, int] | None:
    return gfx.parse_number(str(stat.get("value", "")))


def _blurred_source(tpl: Template, ref, zoom: float = 1.08):
    """Vídeo lido em baixa resolução: desfocar pequeno e ampliar é ~20x mais barato."""
    return tpl.src(ref, max(64, tpl.w // 4), max(36, tpl.h // 4), zoom=zoom)


def _blur_frame(small: Image.Image, w: int, h: int, dim: float) -> Image.Image:
    small = small.filter(ImageFilter.GaussianBlur(3))
    arr = np.asarray(small, np.float32) * dim
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)


def _card_layer(tpl: Template, img: Image.Image, angle: float = 0.0) -> Image.Image:
    u = tpl.u
    card = gfx.bordered(img, max(4, round(7 * u)), tpl.th["border"])
    return gfx.with_shadow(card, angle, round(16 * u), (round(6 * u), round(14 * u)), tpl.th["shadow"])


# ------------------------------------------------------------------- specs
class Specs(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(90 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        self.cw, self.ch = round(w * 0.4), round(h * 0.54)
        self.img = self.src(spec.media[0], self.cw, self.ch, zoom=1.08) if spec.media else None
        self.rows = spec.stats[:5] or [{"label": spec.keyword or "", "value": "", "unit": "", "bar": 0}]
        dur = spec.frames / self.fps
        n = len(self.rows)
        self.stagger = max(0.35, min(0.9, (dur - 2.2) / max(1, n)))
        f_title, f_sub = gfx.font("black", round(70 * u)), gfx.font("semibold", round(34 * u))
        self.title = gfx.text_block(gfx.wrap(spec.keyword, f_title, round(w * 0.4)), f_title, self.th["text"]) \
            if spec.keyword else None
        self.sub = gfx.text_block([spec.subtitle], f_sub, self.th["muted"]) if spec.subtitle else None
        self.f_label = gfx.font("semibold", round(30 * u))
        self.f_value = gfx.font("black", round(58 * u))
        self.f_unit = gfx.font("bold", round(30 * u))
        self.x0, self.x1 = w * 0.53, w * 0.94
        top = h * (0.36 if self.sub else 0.32)
        self.row_h = min(h * 0.12, (h * 0.86 - top) / n)
        self.top = top
        self.parsed = [_number_value(r) for r in self.rows]
        self.done: dict[int, Image.Image] = {}

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=6 * u)
        d = ImageDraw.Draw(canvas)
        acc = self.th["accent"]
        if self.img is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.7))
            layer = _card_layer(self, self.img.frame(i))
            gfx.paste_center(canvas, layer, w * 0.27 - (1 - p) * 120 * u, h * 0.52 + math.sin(t * 1.3) * 5 * u, alpha=p)
        if self.title is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.15, 0.5))
            gfx.paste_at(canvas, self.title, self.x0 - 6 * u, h * 0.1 + (1 - p) * 24 * u, alpha=p)
            if self.sub is not None:
                gfx.paste_at(canvas, self.sub, self.x0 - 2 * u, h * 0.1 + self.title.height, alpha=gfx.progress(t, 0.35, 0.4))
            d.rectangle([self.x0, h * 0.1 - 16 * u, self.x0 + (self.x1 - self.x0) * 0.18 * p, h * 0.1 - 10 * u],
                        fill=(*acc, 255))
        for k, row in enumerate(self.rows):
            t0 = 0.55 + k * self.stagger
            p = gfx.progress(t, t0, 0.45)
            if p <= 0:
                continue
            e = gfx.ease_out_cubic(p)
            y = self.top + k * self.row_h
            dx = (1 - e) * 50 * u
            done = self.done.get(k)
            if done is not None:
                canvas.alpha_composite(done, (round(self.x0), round(y)))
                continue
            layer = Image.new("RGBA", (round(self.x1 - self.x0) + 4, round(self.row_h)), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            ld.text((0, 0), str(row.get("label", "")), font=self.f_label, fill=(*self.th["muted"], 255))
            parsed = self.parsed[k]
            q = gfx.ease_out_cubic(gfx.progress(t, t0 + 0.1, 0.9))
            if parsed:
                value, dec = parsed
                raw = str(row.get("value", ""))
                txt = gfx.fmt_number(value * q, dec) if not re.search(r"[^\d.,\s]", raw) else raw
            else:
                txt = str(row.get("value", ""))
            unit = f" {row.get('unit', '')}".rstrip()
            vw = gfx.text_width(txt, self.f_value)
            uw = gfx.text_width(unit, self.f_unit) if unit else 0
            right = layer.width - 4
            ld.text((right - vw - uw, self.row_h * 0.08), txt, font=self.f_value, fill=(*self.th["text"], 255))
            if unit:
                ld.text((right - uw, self.row_h * 0.08 + 22 * u), unit, font=self.f_unit, fill=(*acc, 255))
            bar = float(row.get("bar") or 0)
            by = self.row_h * 0.78
            ld.rectangle([0, by, right, by + 8 * u], fill=(*self.th["text"], 38))
            if bar > 0:
                ld.rectangle([0, by, right * min(1.0, bar) * q, by + 8 * u], fill=(*acc, 255))
            gfx.paste_at(canvas, layer, self.x0 + dx, y, alpha=e)
            if e >= 1 and q >= 1:
                self.done[k] = layer
        return canvas


# -------------------------------------------------------------------- stat
class Stat(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bgsrc = _blurred_source(self, spec.media[0]) if spec.media else None
        n = spec.number or {}
        self.value = float(n.get("value") or 0)
        self.dec = int(n.get("decimals") or 0)
        self.prefix, self.suffix = str(n.get("prefix") or ""), str(n.get("suffix") or "")
        label = str(n.get("label") or spec.keyword or "")
        self.f_num = gfx.font("black", round(210 * u))
        self.label = gfx.text_block([label], gfx.font("bold", round(46 * u)), self.th["text"], shadow=3) if label else None
        self.final = gfx.fmt_number(self.value, self.dec, self.prefix, self.suffix)
        self.num_w = gfx.text_width(self.final, self.f_num)
        sw, sh = max(64, w // 4), max(36, h // 4)
        yy, xx = np.ogrid[0:sh, 0:sw]
        r = np.sqrt(((xx - sw / 2) / (sw / 2)) ** 2 + ((yy - sh / 2) / (sh / 2)) ** 2)
        self.shade = ((0.35 + 0.65 * np.clip(1 - r * 0.55, 0, 1)) * 0.55).astype(np.float32)[..., None]
        self.nums = TextCache(self.f_num, (255, 255, 255), shadow=6, shadow_blur=14)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        if self.bgsrc is not None:
            small = np.asarray(self.bgsrc.frame(i).filter(ImageFilter.GaussianBlur(3)), np.float32) * self.shade
            canvas = Image.fromarray(small.clip(0, 255).astype(np.uint8)).resize((w, h), Image.BILINEAR).convert("RGBA")
        else:
            if not hasattr(self, "_fallback"):
                self._fallback = gfx.background(self.s.theme, w, h, 100).convert("RGBA")
            canvas = self._fallback.copy()
        acc = self.th["accent"]
        q = gfx.ease_out_cubic(gfx.progress(t, 0.15, 1.3))
        txt = gfx.fmt_number(self.value * q, self.dec, self.prefix, self.suffix) if q < 1 else self.final
        pop = 1 + 0.07 * math.sin(math.pi * gfx.progress(t, 1.45, 0.35))
        layer = gfx.scaled(self.nums.get(txt), pop)
        cy = h * 0.46
        gfx.paste_center(canvas, layer, w / 2, cy, alpha=gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.35)))
        d = ImageDraw.Draw(canvas)
        line = self.num_w * 0.5 * gfx.ease_out_cubic(gfx.progress(t, 0.5, 0.7))
        y = cy + 130 * u
        d.rectangle([w / 2 - line, y, w / 2 + line, y + 9 * u], fill=(*acc, 255))
        if self.label is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.7, 0.45))
            gfx.paste_center(canvas, self.label, w / 2, y + 70 * u + (1 - p) * 20 * u, alpha=p)
        return canvas


# ------------------------------------------------------------------- gauge
class Gauge(Template):
    START, SWEEP = 135.0, 270.0

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(90 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        n = spec.number or {}
        self.value = float(n.get("value") or 0)
        self.max = float(n.get("max") or 0) or self._nice_max(self.value)
        self.dec = int(n.get("decimals") or 0)
        self.suffix = str(n.get("suffix") or "").strip()
        self.label = str(n.get("label") or spec.keyword or "")
        self.R = round(h * 0.31)
        self.cx, self.cy = w * (0.34 if spec.media else 0.5), h * 0.53
        self.face = self._face()
        self.img = self.src(spec.media[0], round(w * 0.36), round(h * 0.5), zoom=1.08) if spec.media else None
        self.f_read = gfx.font("black", round(92 * u))
        self.f_unit = gfx.font("bold", round(34 * u))
        self.reads = TextCache(self.f_read, self.th["text"])
        self.needles: dict[int, Image.Image] = {}
        self.lbl = gfx.text_block([self.label], gfx.font("bold", round(40 * u)), self.th["text"]) if self.label else None

    @staticmethod
    def _nice_max(v: float) -> float:
        if v <= 0:
            return 100.0
        mag = 10 ** math.floor(math.log10(v))
        for step in (1, 1.2, 1.4, 1.6, 2, 2.5, 3, 4, 5, 6, 8, 10):
            if step * mag >= v * 1.15:
                return step * mag
        return v * 1.3

    @staticmethod
    def _step(mx: float) -> float:
        """Passo "redondo" entre as marcações principais (6 a 13 marcações)."""
        mag = 10 ** math.floor(math.log10(mx))
        for base in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10):
            step = base * mag
            if 6 <= mx / step <= 13:
                return step
        return mx / 10

    def _angle(self, frac: float) -> float:
        return math.radians(self.START + self.SWEEP * frac)

    def _face(self) -> Image.Image:
        """Mostrador desenhado 1 vez, em 2x, e reduzido (bordas suaves)."""
        ss, R = 2, self.R
        size = (R * 2 + 120) * ss
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        c = size / 2
        rr = R * ss
        d.ellipse([c - rr - 18 * ss, c - rr - 18 * ss, c + rr + 18 * ss, c + rr + 18 * ss], fill=(10, 11, 14, 235))
        d.arc([c - rr, c - rr, c + rr, c + rr], self.START, self.START + self.SWEEP, fill=(60, 62, 70, 255),
              width=round(14 * self.u * ss))
        red = self.START + self.SWEEP * 0.85
        d.arc([c - rr, c - rr, c + rr, c + rr], red, self.START + self.SWEEP, fill=(*self.th["accent"], 170),
              width=round(14 * self.u * ss))
        f = gfx.font("bold", round(30 * self.u * ss))
        step = self._step(self.max)
        majors = int(round(self.max / step))
        ticks = majors * 5
        for k in range(0, ticks + 1):
            frac = min(1.0, k * step / 5 / self.max)
            a = self._angle(frac)
            major = k % 5 == 0
            r0 = rr - (40 if major else 26) * self.u * ss
            r1 = rr - 18 * self.u * ss
            d.line([(c + r0 * math.cos(a), c + r0 * math.sin(a)), (c + r1 * math.cos(a), c + r1 * math.sin(a))],
                   fill=(235, 235, 240, 255 if major else 140), width=round((4 if major else 2) * self.u * ss))
            if major:
                val = k // 5 * step
                txt = gfx.fmt_number(val, 1 if step < 1 else 0)
                rt = rr - 82 * self.u * ss
                tw = gfx.text_width(txt, f)
                d.text((c + rt * math.cos(a) - tw / 2, c + rt * math.sin(a) - 18 * self.u * ss), txt, font=f,
                       fill=(210, 212, 220, 255))
        return img.resize((size // ss, size // ss), Image.LANCZOS)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=5 * u)
        if self.img is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.1, 0.6))
            gfx.paste_center(canvas, _card_layer(self, self.img.frame(i)), w * 0.74 + (1 - p) * 140 * u, h * 0.5, alpha=p)
        pf = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.4))
        gfx.paste_center(canvas, gfx.scaled(self.face, 0.9 + 0.1 * pf), self.cx, self.cy, alpha=pf)
        # ponteiro: sobe até o valor com leve overshoot e depois "vibra" como motor em giro
        q = gfx.ease_out_back(gfx.progress(t, 0.45, 1.5), s=1.2)
        frac = max(0.0, min(1.04, self.value / self.max * q))
        if gfx.progress(t, 0.45, 1.5) >= 1:
            frac += math.sin(t * 38) * 0.004
        key = round(frac * 2000)
        layer = self.needles.get(key)
        if layer is None:
            layer = self.needles[key] = self._needle(frac)
        gfx.paste_center(canvas, layer, self.cx, self.cy, alpha=pf)
        shown = max(0.0, self.value * min(1.0, q)) if q < 1 else self.value
        read = self.reads.get(gfx.fmt_number(shown, self.dec))
        gfx.paste_center(canvas, read, self.cx, self.cy + self.R * 0.52, alpha=pf)
        if self.suffix:
            unit = gfx.text_block([self.suffix], self.f_unit, self.th["accent"])
            gfx.paste_center(canvas, unit, self.cx, self.cy + self.R * 0.52 + 62 * u, alpha=pf)
        if self.lbl is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.6, 0.4))
            gfx.paste_center(canvas, self.lbl, self.cx, self.cy + self.R + 70 * u, alpha=p)
        return canvas

    def _needle(self, frac: float) -> Image.Image:
        u = self.u
        ss = 2
        R = self.R
        size = (R * 2 + 120) * ss
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        c = size / 2
        rr = R * ss
        acc = self.th["accent"]
        if frac > 0.002:
            d.arc([c - rr, c - rr, c + rr, c + rr], self.START, self.START + self.SWEEP * max(0.0, frac),
                  fill=(*acc, 255), width=round(14 * u * ss))
        a = self._angle(max(0.0, frac))
        tip = rr - 30 * u * ss
        back = 26 * u * ss
        perp = a + math.pi / 2
        wid = 7 * u * ss
        pts = [(c + tip * math.cos(a), c + tip * math.sin(a)),
               (c - back * math.cos(a) + wid * math.cos(perp), c - back * math.sin(a) + wid * math.sin(perp)),
               (c - back * math.cos(a) - wid * math.cos(perp), c - back * math.sin(a) - wid * math.sin(perp))]
        d.polygon(pts, fill=(*acc, 255))
        d.ellipse([c - 22 * u * ss, c - 22 * u * ss, c + 22 * u * ss, c + 22 * u * ss], fill=(28, 29, 34, 255),
                  outline=(*acc, 255), width=round(4 * u * ss))
        return layer.resize((size // ss, size // ss), Image.BILINEAR)


# ------------------------------------------------------------------ versus
class Versus(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        media = (spec.media * 2)[:2]
        self.pw = round(w * 0.58)
        self.left = self.src(media[0], self.pw, h, zoom=1.08)
        self.right = self.src(media[1], self.pw, h, zoom=1.08, pan=(0.5, 0.0))
        top_x, bot_x = w * 0.555, w * 0.445
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).polygon([(0, 0), (top_x, 0), (bot_x, h), (0, h)], fill=255)
        self.mask_l = m
        self.mask_r = Image.eval(m, lambda v: 255 - v)
        self.line = (top_x, bot_x)
        grad = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        alpha = (np.clip((grad - 0.55) / 0.45, 0, 1) ** 1.3 * 220).astype(np.uint8)
        shade = np.zeros((h, w, 4), np.uint8)
        shade[..., 3] = alpha
        self.shade_y = round(h * 0.55)  # só a parte de baixo tem sombra: compõe só ela
        self.shade_low = Image.fromarray(shade, "RGBA").crop((0, self.shade_y, w, h))
        self.vs = gfx.text_block(["VS"], gfx.font("black", round(92 * u)), (255, 255, 255))
        labels = (spec.labels + ["", ""])[:2]
        f = gfx.font("black", round(74 * u))
        self.names = [gfx.text_block([lb.upper()], f, (255, 255, 255), shadow=4) if lb else None for lb in labels]
        self.f_vs = gfx.font("black", round(92 * u))
        self.rows = []
        f_row, f_lbl = gfx.font("black", round(38 * u)), gfx.font("semibold", round(26 * u))
        for st in spec.stats[:3]:
            a, b = str(st.get("value", "")), str(st.get("value_b", ""))
            unit = str(st.get("unit", ""))
            pa, pb = gfx.parse_number(a), gfx.parse_number(b)
            win = None
            if pa and pb and pa[0] != pb[0]:
                lower = any(k in str(st.get("label", "")).lower() for k in LOWER_IS_BETTER)
                win = 0 if (pa[0] < pb[0]) == lower else 1
            self.rows.append(self._row(f"{a} {unit}".strip(), str(st.get("label", "")), f"{b} {unit}".strip(), win, f_row, f_lbl))

    def _row(self, a: str, label: str, b: str, win: int | None, f_row, f_lbl) -> Image.Image:
        u = self.u
        wd, ht = round(self.w * 0.34), round(78 * u)
        img = Image.new("RGBA", (wd, ht), (8, 9, 12, 215))
        d = ImageDraw.Draw(img)
        acc, white = (*self.th["accent"], 255), (245, 245, 247, 255)
        d.text((20 * u, 16 * u), a, font=f_row, fill=acc if win == 0 else white)
        bw = gfx.text_width(b, f_row)
        d.text((wd - bw - 20 * u, 16 * u), b, font=f_row, fill=acc if win == 1 else white)
        lw = gfx.text_width(label, f_lbl)
        d.text(((wd - lw) / 2, 26 * u), label, font=f_lbl, fill=(170, 172, 180, 255))
        return img

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = Image.new("RGBA", (w, h), (10, 10, 12, 255))
        pl = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.55))
        pr = gfx.ease_out_cubic(gfx.progress(t, 0.12, 0.55))
        for src, mask, x in ((self.left, self.mask_l, round(-(1 - pl) * self.pw)),
                             (self.right, self.mask_r, round(w - self.pw + (1 - pr) * self.pw))):
            x0, x1 = max(0, x), min(w, x + self.pw)
            if x1 > x0:
                frame = src.frame(i).crop((x0 - x, 0, x1 - x, h))
                canvas.paste(frame, (x0, 0), mask.crop((x0, 0, x1, h)))
        canvas.alpha_composite(self.shade_low, (0, self.shade_y))
        d = ImageDraw.Draw(canvas)
        acc = self.th["accent"]
        pd = gfx.ease_out_cubic(gfx.progress(t, 0.5, 0.4))
        if pd > 0:
            top_x, bot_x = self.line
            d.line([(top_x, 0), (top_x + (bot_x - top_x) * pd, h * pd)], fill=(*acc, 255), width=max(4, round(9 * u)))
        pv = gfx.ease_out_back(gfx.progress(t, 0.7, 0.45))
        if pv > 0:
            r = 92 * u * pv * (1 + 0.03 * math.sin(t * 5))
            cx, cy = w / 2, h * 0.44
            d.ellipse([cx - r - 8 * u, cy - r - 8 * u, cx + r + 8 * u, cy + r + 8 * u], fill=(255, 255, 255, 255))
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*acc, 255))
            gfx.paste_center(canvas, gfx.scaled(self.vs, pv), cx, cy - 4 * u)
        for k, name in enumerate(self.names):
            if name is None:
                continue
            p = gfx.ease_out_cubic(gfx.progress(t, 0.9 + 0.15 * k, 0.45))
            x = w * 0.05 if k == 0 else w * 0.95 - name.width
            gfx.paste_at(canvas, name, x + (1 - p) * (-60 if k == 0 else 60) * u, h * 0.8, alpha=p)
        for k, row in enumerate(self.rows):
            p = gfx.ease_out_cubic(gfx.progress(t, 1.4 + 0.35 * k, 0.4))
            gfx.paste_center(canvas, row, w / 2, h * 0.62 + k * 90 * u + (1 - p) * 20 * u, alpha=p)
        return canvas


# --------------------------------------------------------------- blueprint
class Blueprint(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(96 * u)
        self.bg = gfx.vignette(gfx.background("engineering", w, h, self.cell), 0.35).convert("RGBA")
        self.iw, self.ih = round(w * 0.64), round(h * 0.56)
        self.ix, self.iy = round((w - self.iw) / 2), round(h * 0.2)
        still = Image.new("RGB", (self.iw, self.ih), (40, 60, 90))
        if spec.media:
            src = self.src(spec.media[0], self.iw, self.ih, zoom=1.0)
            still = src.frame(0).copy()
        self.photo = still.convert("RGBA")
        self.lines = self._lines(still)
        self.dur = spec.frames / self.fps
        self.draw_end = min(3.2, self.dur * 0.42)
        f = gfx.font("black", round(64 * u))
        self.title = gfx.text_block([spec.keyword.upper()], f, (255, 255, 255)) if spec.keyword else None
        self.sub = gfx.text_block([spec.subtitle], gfx.font("semibold", round(30 * u)), self.th["muted"]) \
            if spec.subtitle else None
        dims = [f"{st.get('value', '')} {st.get('unit', '')}".strip() for st in spec.stats[:2]]
        self.dims = dims + [""] * (2 - len(dims))
        self.f_dim = gfx.font("semibold", round(28 * u))
        self.stamp = self._stamp()

    def _lines(self, img: Image.Image) -> Image.Image:
        """Detecção de bordas (Sobel) -> traço branco de engenharia."""
        g = np.asarray(img.convert("L").filter(ImageFilter.GaussianBlur(1.6)), np.float32)
        gx = np.zeros_like(g)
        gy = np.zeros_like(g)
        gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
        gy[1:-1, :] = g[2:, :] - g[:-2, :]
        mag = np.hypot(gx, gy)
        b = 6  # o desfoque cria bordas falsas na moldura
        mag[:b], mag[-b:], mag[:, :b], mag[:, -b:] = 0, 0, 0, 0
        thr = np.percentile(mag, 86)
        alpha = np.clip((mag - thr) / max(1.0, thr) * 255 * 1.4, 0, 255).astype(np.uint8)
        a = Image.fromarray(alpha).filter(ImageFilter.MaxFilter(3))
        lines = Image.new("RGBA", img.size, (240, 246, 255, 0))
        lines.putalpha(a)
        return lines

    def _stamp(self) -> Image.Image:
        u = self.u
        wd, ht = round(330 * u), round(96 * u)
        img = Image.new("RGBA", (wd, ht), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, wd - 1, ht - 1], outline=(230, 240, 255, 200), width=2)
        d.line([(0, ht / 2), (wd, ht / 2)], fill=(230, 240, 255, 160), width=1)
        f = gfx.font("semibold", round(22 * u))
        d.text((12 * u, 10 * u), "DES. Nº 01", font=f, fill=(230, 240, 255, 230))
        d.text((12 * u, ht / 2 + 10 * u), "ESC. 1:20   UN: mm", font=f, fill=(230, 240, 255, 230))
        return img

    def _dim_h(self, d: ImageDraw.ImageDraw, x0: float, x1: float, y: float, p: float, label: str) -> None:
        u = self.u
        col = (240, 246, 255, 235)
        xm = (x0 + x1) / 2
        half = (x1 - x0) / 2 * p
        d.line([(xm - half, y), (xm + half, y)], fill=col, width=2)
        for x in (x0, x1):
            d.line([(x, y - 14 * u), (x, y + 14 * u)], fill=col, width=2)
        if p >= 1:
            for x, s in ((x0, 1), (x1, -1)):
                d.polygon([(x, y), (x + s * 16 * u, y - 6 * u), (x + s * 16 * u, y + 6 * u)], fill=col)
            if label:
                tw = gfx.text_width(label, self.f_dim)
                d.rectangle([xm - tw / 2 - 10 * u, y - 18 * u, xm + tw / 2 + 10 * u, y + 18 * u], fill=(16, 62, 148, 255))
                d.text((xm - tw / 2, y - 16 * u), label, font=self.f_dim, fill=col)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.copy()
        # foto aparece por baixo do traço na segunda metade
        pp = gfx.ease_in_out(gfx.progress(t, self.dur * 0.62, 0.9))
        if pp > 0:
            gfx.paste_at(canvas, self.photo, self.ix, self.iy, alpha=pp * 0.92)
        # "plotter": o traço vai sendo desenhado da esquerda para a direita
        pd = gfx.ease_in_out(gfx.progress(t, 0.25, self.draw_end))
        vis = round(self.iw * pd)
        if vis > 0:
            lines = self.lines.crop((0, 0, vis, self.ih))
            gfx.paste_at(canvas, lines, self.ix, self.iy, alpha=1 - 0.55 * pp)
        d = ImageDraw.Draw(canvas)
        if 0 < pd < 1:
            x = self.ix + vis
            d.line([(x, self.iy - 10 * u), (x, self.iy + self.ih + 10 * u)], fill=(255, 214, 64, 230), width=max(2, round(3 * u)))
        # cotas
        pc = gfx.ease_out_cubic(gfx.progress(t, 0.25 + self.draw_end, 0.6))
        if pc > 0:
            self._dim_h(d, self.ix, self.ix + self.iw, self.iy + self.ih + 46 * u, pc, self.dims[0])
            if self.dims[1]:
                xv = self.ix + self.iw + 46 * u
                y0, y1 = self.iy, self.iy + self.ih
                ym, half = (y0 + y1) / 2, (y1 - y0) / 2 * pc
                col = (240, 246, 255, 235)
                d.line([(xv, ym - half), (xv, ym + half)], fill=col, width=2)
                if pc >= 1:
                    lbl = gfx.text_block([self.dims[1]], self.f_dim, (240, 246, 255)).rotate(90, expand=True)
                    gfx.paste_center(canvas, lbl, xv + 26 * u, ym)
        if self.title is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.5))
            gfx.paste_at(canvas, self.title, w * 0.18, h * 0.06 + (1 - p) * 20 * u, alpha=p)
            if self.sub is not None:
                gfx.paste_at(canvas, self.sub, w * 0.18 + 4 * u, h * 0.06 + self.title.height, alpha=p)
        gfx.paste_at(canvas, self.stamp, w * 0.97 - self.stamp.width, h * 0.95 - self.stamp.height,
                     alpha=gfx.progress(t, 0.3, 0.4))
        return canvas


# --------------------------------------------------------------- checklist
class Checklist(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(64 * u) if spec.theme == "paper" else round(90 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        self.img = self.src(spec.media[0], round(w * 0.36), round(h * 0.56), zoom=1.08) if spec.media else None
        pts = spec.points[:5] or [{"text": spec.keyword or "", "positive": True}]
        dur = spec.frames / self.fps
        caps = list(spec.captions)
        if len(caps) == len(pts):  # tempos sincronizados com a fala (planner)
            self.times = [max(0.4, c[0]) for c in caps]
        else:
            step = max(0.4, (dur - 1.5) / len(pts))
            self.times = [0.6 + k * step for k in range(len(pts))]
        f = gfx.font("bold", round(42 * u))
        self.rows = [(gfx.text_block(gfx.wrap(str(p["text"]), f, round(w * 0.42), 2), f, self.th["text"]),
                      bool(p.get("positive", True))) for p in pts]
        ft = gfx.font("black", round(68 * u))
        self.title = gfx.text_block(gfx.wrap(spec.keyword, ft, round(w * 0.46)), ft, self.th["text"]) if spec.keyword else None
        self.x0 = w * 0.47 if self.img else w * 0.2
        self.row_h = min(h * 0.13, h * 0.62 / len(pts))
        self.icons = {True: self._icon(True), False: self._icon(False)}

    def _icon(self, ok: bool) -> Image.Image:
        u, ss = self.u, 2
        r = round(30 * u) * ss
        img = Image.new("RGBA", (2 * r + 4, 2 * r + 4), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([2, 2, 2 * r, 2 * r], fill=(34, 197, 94, 255) if ok else (239, 68, 68, 255))
        wd = round(7 * u * ss)
        c = r + 1
        if ok:
            d.line([(c - r * 0.45, c + r * 0.02), (c - r * 0.12, c + r * 0.35), (c + r * 0.48, c - r * 0.32)],
                   fill=(255, 255, 255, 255), width=wd, joint="curve")
        else:
            k = r * 0.36
            d.line([(c - k, c - k), (c + k, c + k)], fill=(255, 255, 255, 255), width=wd)
            d.line([(c - k, c + k), (c + k, c - k)], fill=(255, 255, 255, 255), width=wd)
        return img.resize((img.width // ss, img.height // ss), Image.LANCZOS)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=6 * u)
        if self.img is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.6))
            gfx.paste_center(canvas, _card_layer(self, self.img.frame(i)), w * 0.24 - (1 - p) * 110 * u, h * 0.52, alpha=p)
        if self.title is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.1, 0.5))
            gfx.paste_at(canvas, self.title, self.x0, h * 0.1 + (1 - p) * 20 * u, alpha=p)
        top = h * 0.3
        for k, (txt, ok) in enumerate(self.rows):
            t0 = self.times[k]
            p = gfx.progress(t, t0, 0.4)
            if p <= 0:
                continue
            y = top + k * self.row_h
            icon = gfx.scaled(self.icons[ok], max(0.01, gfx.ease_out_back(p)))
            gfx.paste_center(canvas, icon, self.x0 + 30 * u, y + 32 * u)
            e = gfx.ease_out_cubic(p)
            gfx.paste_at(canvas, txt, self.x0 + 78 * u - (1 - e) * 30 * u, y + 6 * u, alpha=e)
        return canvas


# ----------------------------------------------------------------- ranking
class Ranking(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(90 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        self.img = self.src(spec.media[0], round(w * 0.46), round(h * 0.56), zoom=1.08) if spec.media else None
        acc = self.th["accent"]
        txt = f"#{spec.rank}" if spec.rank else "#"
        self.num = gfx.outlined_text(txt, gfx.font("black", round(360 * u)), (*acc, 255), round(4 * u), (255, 255, 255, 255))
        self.top = gfx.text_block(["TOP"], gfx.font("black", round(44 * u)), self.th["muted"])
        fn = gfx.font("black", round(64 * u))
        self.name = gfx.text_block(gfx.wrap(spec.keyword, fn, round(w * 0.5)), fn, self.th["text"], shadow=3) if spec.keyword else None
        self.sub = gfx.text_block([spec.subtitle], gfx.font("semibold", round(32 * u)), self.th["muted"]) if spec.subtitle else None
        stripe = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(stripe).polygon([(0, h * 0.72), (w * 0.42, h * 0.2), (w * 0.52, h * 0.2), (w * 0.1, h * 0.72)],
                                       fill=(*acc, 38))
        self.stripe = stripe

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=6 * u)
        canvas.alpha_composite(self.stripe)
        p = gfx.ease_out_back(gfx.progress(t, 0.05, 0.55))
        gfx.paste_center(canvas, gfx.scaled(self.num, 0.7 + 0.3 * p), w * 0.23 - (1 - p) * 90 * u, h * 0.5,
                         alpha=gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.3)))
        gfx.paste_center(canvas, self.top, w * 0.23, h * 0.2, alpha=gfx.progress(t, 0.3, 0.3))
        if self.img is not None:
            q = gfx.ease_out_cubic(gfx.progress(t, 0.25, 0.6))
            gfx.paste_center(canvas, _card_layer(self, self.img.frame(i)), w * 0.67 + (1 - q) * 140 * u,
                             h * 0.43 + math.sin(t * 1.4) * 5 * u, alpha=q)
        if self.name is not None:
            q = gfx.ease_out_cubic(gfx.progress(t, 0.6, 0.45))
            gfx.paste_center(canvas, self.name, w * 0.67, h * 0.82 + (1 - q) * 20 * u, alpha=q)
            if self.sub is not None:
                gfx.paste_center(canvas, self.sub, w * 0.67, h * 0.82 + self.name.height * 0.8, alpha=q)
        return canvas


# ----------------------------------------------------------------- chapter
class Chapter(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bgsrc = _blurred_source(self, spec.media[0], zoom=1.12) if spec.media else None
        self.fallback = gfx.background("carbon", w, h, round(90 * u)).convert("RGBA")
        ft = gfx.font("black", round(150 * u))
        title = (spec.keyword or "").upper()
        self.title = gfx.text_block(gfx.wrap(title, ft, round(w * 0.84), 2), ft, (255, 255, 255), shadow=4) if title else None
        self.sub = gfx.text_block([spec.subtitle], gfx.font("semibold", round(40 * u)), (220, 222, 228), shadow=2) \
            if spec.subtitle else None
        self.idx = gfx.text_block([f"{spec.index:02d}"], gfx.font("black", round(54 * u)), self.th["accent"]) if spec.index else None

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = _blur_frame(self.bgsrc.frame(i), w, h, 0.38).convert("RGBA") if self.bgsrc else self.fallback.copy()
        d = ImageDraw.Draw(canvas)
        acc = self.th["accent"]
        x0, y0 = w * 0.08, h * 0.4
        pb = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.35))
        d.rectangle([x0, y0 - 26 * u, x0 + w * 0.2 * pb, y0 - 14 * u], fill=(*acc, 255))
        if self.idx is not None:
            gfx.paste_at(canvas, self.idx, x0, y0 - 100 * u, alpha=gfx.progress(t, 0.1, 0.3))
        if self.title is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.15, 0.45))
            # revelação por máscara: o título sobe de trás de uma "linha"
            full = self.title
            shift = round((1 - p) * full.height)
            win = full.crop((0, 0, full.width, full.height - shift))
            gfx.paste_at(canvas, win, x0 - 10 * u, y0 + shift, alpha=min(1.0, p * 1.5))
            if self.sub is not None:
                q = gfx.progress(t, 0.5, 0.4)
                gfx.paste_at(canvas, self.sub, x0, y0 + full.height + 6 * u, alpha=q)
        return canvas


AUTO_TEMPLATES: dict[str, type[Template]] = {
    "specs": Specs, "stat": Stat, "gauge": Gauge, "versus": Versus, "blueprint": Blueprint,
    "checklist": Checklist, "ranking": Ranking, "chapter": Chapter,
}
