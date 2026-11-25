"""Templates de review automotivo no estilo documentário (azul-marinho + amarelo).

* ``timeline``    — linha do tempo das gerações: anos numa linha, a foto de cada geração
  deslizando quando o ano é falado (a câmera anda pela linha se houver mais de 3).
* ``speedometer`` — velocímetro grande: no 0–100 o ponteiro sobe enquanto o cronômetro
  conta até o tempo falado; na velocidade máxima, o ponteiro vai até o valor.
* ``datasheet``   — ficha técnica: foto + linhas que entram quando cada dado é falado,
  com o número contando.
* ``price``       — etiqueta de preço balançando, valor contando e medidor barato ↔ caro.
* ``proscons``    — tela dividida verde/vermelha, prós e contras entrando no ritmo da fala.
* ``score``       — nota final: barras por quesito e o anel da nota contando.
* ``magnifier``   — lupa: círculo com zoom num detalhe da imagem, linha e nome.
* ``beforeafter`` — antes e depois: cortina deslizante entre duas fotos.
* ``news``        — recorte de jornal com a manchete marcada a marca-texto.
* ``comment``     — card de comentário de inscrito sendo digitado, com coração do canal.
* ``poll``        — enquete "qual você escolheria?": opções A/B/C e chamada para comentar.

Os rótulos fixos ("PRÓS", "NOTA FINAL"...) saem no idioma do roteiro (``i18n``).
Cada template diz quando tocar os efeitos sonoros (``cues``), com os tempos da animação.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..text import normalize
from . import gfx
from .i18n import text as tr
from .templates import MediaRef, Template, TemplateSpec
from .templates_auto import TextCache, _blur_frame, _blurred_source
from .templates_doc import TEAL, YELLOW, Cue, _navy_bg, _number, _sweep

GREEN = (46, 204, 113)
RED = (235, 72, 60)
WHITE = (255, 255, 255)
MUTED = (200, 210, 235)
NAVY_DARK = (8, 14, 34)


# ------------------------------------------------------------------ helpers
def item_starts(spec: TemplateSpec, n: int, first: float = 0.6, gap: float = 0.35) -> list[float]:
    """Quando cada item entra: no instante em que é falado (``captions``, vindas do
    planner) ou, sem isso, espalhados pelo template. Sempre em ordem e com respiro."""
    dur = spec.frames / spec.fps
    last_ok = max(first, dur - 0.9)
    caps = [float(c[0]) for c in spec.captions][:n]
    step = (min(dur * 0.8, last_ok) - first) / max(1, n - 1) if n > 1 else 0.0
    out: list[float] = []
    prev = -1e9
    for k in range(n):
        t = caps[k] if k < len(caps) else first + k * step
        t = min(max(t, first, prev + gap), last_ok)
        out.append(t)
        prev = t
    return out


class _MediaBackground:
    """Fundo: a mídia desfocada e escurecida (barato: lida em 1/4 e ampliada) ou o azul-marinho."""

    def __init__(self, tpl: Template, ref: MediaRef | None, dim: float = 0.42, zoom: float = 1.1) -> None:
        self.tpl, self.dim = tpl, dim
        self.src = _blurred_source(tpl, ref, zoom=zoom) if ref is not None else None
        self.cell = round(96 * tpl.u)
        self.navy = _navy_bg(tpl, self.cell) if self.src is None else None

    def frame(self, i: int, t: float) -> Image.Image:
        if self.src is None:
            assert self.navy is not None
            canvas = gfx.drift(self.navy, t, self.tpl.w, self.tpl.h, self.cell, speed=10).convert("RGBA")
            _sweep(canvas, t, self.tpl.w, self.tpl.h)
            return canvas
        return _blur_frame(self.src.frame(i), self.tpl.w, self.tpl.h, self.dim).convert("RGBA")


def _photo_card(tpl: Template, img: Image.Image, border: float = 6, shadow: int = 170) -> Image.Image:
    u = tpl.u
    card = gfx.bordered(img, max(3, round(border * u)), (245, 245, 245))
    return gfx.with_shadow(card, 0, round(16 * u), (0, round(10 * u)), shadow)


def _title_block(tpl: Template, text: str, size: int = 84) -> Image.Image | None:
    if not text:
        return None
    f = gfx.font("condensed", round(size * tpl.u))
    return gfx.text_block(gfx.wrap(text.upper(), f, round(tpl.w * 0.8), 1), f, WHITE, shadow=3)


def _draw_title(tpl: Template, canvas: Image.Image, title: Image.Image | None, t: float, x: float, y: float) -> None:
    if title is None:
        return
    u = tpl.u
    q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.4))
    gfx.paste_at(canvas, title, x - (1 - q) * 30 * u, y, alpha=q)
    ImageDraw.Draw(canvas).rectangle([x + 4 * u, y - 10 * u, x + 4 * u + 110 * u * q, y - 2 * u], fill=(*gfx.accent(), 255))


def _icon(kind: str, size: int, color: tuple[int, int, int], fill: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Ícones desenhados em 4x e reduzidos (bordas suaves): check, cross, thumb, thumb_down, bell, heart."""
    ss = 4
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(ss, round(S * 0.11))
    col = (*color, 255)
    if fill is not None:
        d.ellipse([0, 0, S - 1, S - 1], fill=fill)
    if kind == "check":
        d.line([(S * 0.27, S * 0.53), (S * 0.43, S * 0.69), (S * 0.74, S * 0.34)], fill=col, width=lw, joint="curve")
    elif kind == "cross":
        d.line([(S * 0.31, S * 0.31), (S * 0.69, S * 0.69)], fill=col, width=lw)
        d.line([(S * 0.69, S * 0.31), (S * 0.31, S * 0.69)], fill=col, width=lw)
    elif kind in ("thumb", "thumb_down", "thumb_filled"):
        solid = kind == "thumb_filled"
        w_ = max(ss, round(S * 0.07))
        palm = [S * 0.36, S * 0.42, S * 0.86, S * 0.9]
        cuff = [S * 0.12, S * 0.44, S * 0.3, S * 0.9]
        thumb = [(S * 0.36, S * 0.46), (S * 0.5, S * 0.22), (S * 0.55, S * 0.1), (S * 0.64, S * 0.12),
                 (S * 0.66, S * 0.26), (S * 0.62, S * 0.42)]
        if solid:
            d.rounded_rectangle(palm, radius=S * 0.08, fill=col)
            d.rectangle(cuff, fill=col)
            d.polygon(thumb, fill=col)
        else:
            d.rounded_rectangle(palm, radius=S * 0.08, outline=col, width=w_)
            d.rectangle(cuff, outline=col, width=w_)
            d.line(thumb + [thumb[0]], fill=col, width=w_, joint="curve")
        if kind == "thumb_down":
            img = img.rotate(180)
    elif kind == "bell":
        d.pieslice([S * 0.2, S * 0.12, S * 0.8, S * 0.78], 180, 360, fill=col)
        d.rectangle([S * 0.2, S * 0.44, S * 0.8, S * 0.7], fill=col)
        d.polygon([(S * 0.12, S * 0.76), (S * 0.2, S * 0.62), (S * 0.8, S * 0.62), (S * 0.88, S * 0.76)], fill=col)
        d.ellipse([S * 0.42, S * 0.76, S * 0.58, S * 0.92], fill=col)
        d.ellipse([S * 0.45, S * 0.04, S * 0.55, S * 0.14], fill=col)
    elif kind == "heart":
        d.ellipse([S * 0.1, S * 0.18, S * 0.52, S * 0.6], fill=col)
        d.ellipse([S * 0.48, S * 0.18, S * 0.9, S * 0.6], fill=col)
        d.polygon([(S * 0.12, S * 0.46), (S * 0.5, S * 0.9), (S * 0.88, S * 0.46), (S * 0.5, S * 0.35)], fill=col)
    return img.resize((size, size), Image.LANCZOS)


def _rotate_about(layer: Image.Image, angle: float, pivot: tuple[float, float]) -> tuple[Image.Image, tuple[float, float]]:
    """Gira ``layer`` em torno de ``pivot`` (coords do layer); devolve o layer e onde o pivô foi parar."""
    if abs(angle) < 0.05:
        return layer, pivot
    w, h = layer.size
    out = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    # gira em torno do centro; com expand, o centro do layer vai para o centro da nova imagem
    a = math.radians(angle)
    cx, cy = w / 2, h / 2
    px, py = pivot[0] - cx, pivot[1] - cy
    rx = px * math.cos(a) + py * math.sin(a)
    ry = -px * math.sin(a) + py * math.cos(a)
    return out, (out.width / 2 + rx, out.height / 2 + ry)


# ----------------------------------------------------------------- timeline
class Timeline(Template):
    FIRST = 0.7

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(96 * u)
        self.bg = _navy_bg(self, self.cell)
        self.n = n = max(1, min(6, len(spec.media)))
        labels = [str(x) for x in spec.labels][:n]
        labels += [""] * (n - len(labels))
        notes = [str(x) for x in spec.notes][:n]
        notes += [""] * (n - len(notes))
        self.scroll = n > 3
        spacing = w * 0.31 if self.scroll else w * 0.84 / n
        self.xs = [k * spacing for k in range(n)]
        self.cw = round(min(w * 0.27, spacing * 0.9))
        self.ch = round(self.cw * 0.62)
        self.border = max(3, round(6 * u))
        self.cards = [self.src(m, self.cw - 2 * self.border, self.ch - 2 * self.border, zoom=1.06)
                      for m in spec.media[:n]]
        self.starts = item_starts(spec, n, first=self.FIRST)
        self.line_y = h * 0.66
        f_year = gfx.font("condensed", round((110 if n <= 3 else 92) * u))
        f_note = gfx.font("oswald_light", round(38 * u))
        self.years = [gfx.text_block([lb.upper()], f_year, gfx.accent(), shadow=3) if lb else None for lb in labels]
        self.notes = [gfx.text_block([nt.upper()], f_note, MUTED, shadow=2) if nt else None for nt in notes]
        self.title = _title_block(self, spec.keyword)
        self.frozen: dict[int, Image.Image] = {}

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(6, len(spec.media)))
        out: list[Cue] = [(0.0, "whoosh_soft", 0.45)]
        for s in item_starts(spec, n, first=cls.FIRST):
            out += [(s - 0.15, "whoosh", 0.35), (s + 0.2, "pop", 0.45)]
        return out

    def _active(self, t: float) -> int:
        return max([k for k, s in enumerate(self.starts) if t >= s - 0.15] or [0])

    def _cam(self, t: float) -> float:
        if not self.scroll:
            return (self.xs[0] + self.xs[-1]) / 2
        k = self._active(t)
        if k == 0:
            return self.xs[0]
        p = gfx.ease_in_out(gfx.progress(t, self.starts[k] - 0.3, 0.6))
        return self.xs[k - 1] + (self.xs[k] - self.xs[k - 1]) * p

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=12).copy()
        _sweep(canvas, t, w, h)
        d = ImageDraw.Draw(canvas)
        _draw_title(self, canvas, self.title, t, w * 0.06, h * 0.08)
        off = w / 2 - self._cam(t)
        ly = self.line_y
        lw = max(2, round(5 * u))
        grow = gfx.ease_out_cubic(gfx.progress(t, 0.1, 0.6))
        d.rectangle([0, ly - lw / 2, w * grow, ly + lw / 2], fill=(255, 255, 255, 70))
        k_act = self._active(t)
        # trilha amarela até o ano atual (cresce junto com a câmera)
        x0 = self.xs[0] + off
        if t >= self.starts[0] - 0.3:
            head = self.xs[k_act] + off
            if k_act > 0:
                p = gfx.ease_in_out(gfx.progress(t, self.starts[k_act] - 0.3, 0.6))
                head = self.xs[k_act - 1] + off + (self.xs[k_act] - self.xs[k_act - 1]) * p
            d.rectangle([min(x0, head), ly - lw / 2, head, ly + lw / 2], fill=(*gfx.accent(), 255))
        for k in range(self.n):
            st = self.starts[k]
            if t < st - 0.2:
                continue
            sx = self.xs[k] + off
            if sx < -self.cw or sx > w + self.cw:
                continue
            q = gfx.progress(t, st, 0.45)
            e = gfx.ease_out_cubic(q)
            pop = gfx.ease_out_back(q, 2.0)
            r = 16 * u * max(0.01, pop)
            d.ellipse([sx - r - 6 * u, ly - r - 6 * u, sx + r + 6 * u, ly + r + 6 * u], fill=(*gfx.accent(), 255))
            d.ellipse([sx - r, ly - r, sx + r, ly + r], fill=(*NAVY_DARK, 255))
            active = k == k_act
            if active or k not in self.frozen:  # só a geração da vez anda; as outras ficam no último quadro
                layer = self.frozen[k] = _photo_card(self, self.cards[k].frame(i))
            else:
                layer = self.frozen[k]
            scale = 1.0 if active else 0.82
            card_y = ly - 70 * u - self.ch * scale / 2 + (1 - e) * 90 * u
            d.line([(sx, ly - 20 * u), (sx, ly - 70 * u + (1 - e) * 90 * u)], fill=(255, 255, 255, 140),
                   width=max(1, round(2 * u)))
            gfx.paste_center(canvas, gfx.scaled(layer, scale), sx, card_y, alpha=e * (1.0 if active else 0.72))
            year = self.years[k]
            if year is not None:
                gfx.paste_center(canvas, gfx.scaled(year, max(0.05, 0.7 + 0.3 * pop)), sx, ly + 72 * u, alpha=e)
            note = self.notes[k]
            if note is not None:
                qn = gfx.ease_out_cubic(gfx.progress(t, st + 0.25, 0.35))
                gfx.paste_center(canvas, note, sx, ly + 140 * u + (1 - qn) * 16 * u, alpha=qn)
        return canvas


# -------------------------------------------------------------- speedometer
class Speedometer(Template):
    START, SWEEP = 135.0, 270.0
    SWEEP_T0 = 0.5

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        value, self.dec, prefix, suffix, label, mx = _number(spec)
        self.accel, self.target, self.max, self.unit = self.mode(spec)
        self.value = value
        self.bg = _MediaBackground(self, spec.media[0] if spec.media else None, dim=0.38)
        self.R = round(h * 0.33)
        self.cx, self.cy = w * 0.32, h * 0.53
        self.face = self._face()
        self.needles: dict[int, Image.Image] = {}
        self.t1 = self.SWEEP_T0 + self.sweep_seconds(spec)
        final = self._big_text(value, suffix, prefix)
        size = 250
        while size > 120 and gfx.text_width(final, gfx.font("condensed", round(size * u))) > w * 0.36:
            size -= 10
        self.big = TextCache(gfx.font("condensed", round(size * u)), gfx.accent(), shadow=6, shadow_blur=14)
        self.read = TextCache(gfx.font("condensed", round(120 * u)), WHITE)
        self.dial_unit = gfx.text_block([self.unit.upper()], gfx.font("oswald_bold", round(34 * u)), gfx.accent())
        f_lbl = gfx.font("oswald_bold", round(56 * u))
        head = label or spec.keyword or (f"0–100 {self.unit}" if self.accel else tr("top_speed", spec.lang))
        self.head = gfx.text_block(gfx.wrap(head.upper(), f_lbl, round(w * 0.36), 2), f_lbl, WHITE, shadow=3)
        self.sub = gfx.text_block([tr("timer", spec.lang)], gfx.font("oswald_light", round(40 * u)), MUTED) \
            if self.accel else None
        self.suffix = suffix.strip() or ("s" if self.accel else self.unit)
        self.prefix = prefix
        self.streaks = self._streaks()

    def _big_text(self, value: float, suffix: str, prefix: str) -> str:
        if self.accel:
            return gfx.fmt_number(value, max(1, self.dec)) + f" {suffix.strip() or 's'}"
        return prefix + gfx.fmt_number(value, self.dec) + (f" {suffix.strip() or self.unit}")

    @staticmethod
    def mode(spec: TemplateSpec) -> tuple[bool, float, float, str]:
        """(é aceleração?, valor do ponteiro, fundo de escala, unidade do mostrador)."""
        value, _, _, suffix, label, mx = _number(spec)
        text = normalize(f"{label} {spec.keyword} {suffix}")
        mph = "mph" in text
        accel = suffix.strip().lower() in ("s", "seg", "segundos", "sec", "secondes", "sekund", "sekundy") \
            or bool(re.search(r"\b0\s*(-|–|a|à|to|do)\s*(100|60)\b", text))
        if accel:
            target = 60.0 if (mph and "60" in text) else 100.0
            return True, target, 120.0 if mph else 200.0, "mph" if mph else "km/h"
        unit = "rpm" if "rpm" in text else "mph" if mph else "km/h"
        return False, value, mx or Speedometer._nice_max(value), unit

    @staticmethod
    def _nice_max(v: float) -> float:
        if v <= 0:
            return 200.0
        mag = 10 ** math.floor(math.log10(v))
        for step in (1, 1.2, 1.4, 1.6, 2, 2.5, 3, 4, 5, 6, 8, 10):
            if step * mag >= v * 1.12:
                return step * mag
        return v * 1.3

    @classmethod
    def sweep_seconds(cls, spec: TemplateSpec) -> float:
        dur = spec.frames / spec.fps
        accel = cls.mode(spec)[0]
        return max(1.2, min(3.2 if accel else 1.8, dur - cls.SWEEP_T0 - 1.2))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        t1 = cls.SWEEP_T0 + cls.sweep_seconds(spec)
        return [(0.0, "whoosh_soft", 0.45), (cls.SWEEP_T0 - 0.05, "riser", 0.5), (t1, "impact_soft", 0.55),
                (t1 + 0.05, "ding", 0.4)]

    def _angle(self, frac: float) -> float:
        return math.radians(self.START + self.SWEEP * frac)

    def _face(self) -> Image.Image:
        ss, R, u = 2, self.R, self.u
        size = (R * 2 + round(60 * u)) * ss
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        c, rr = size / 2, R * ss
        d.ellipse([c - rr - 26 * u * ss, c - rr - 26 * u * ss, c + rr + 26 * u * ss, c + rr + 26 * u * ss],
                  fill=(6, 10, 24, 225), outline=(255, 255, 255, 60), width=round(3 * u * ss))
        d.arc([c - rr, c - rr, c + rr, c + rr], self.START, self.START + self.SWEEP, fill=(50, 58, 80, 255),
              width=round(16 * u * ss))
        d.arc([c - rr, c - rr, c + rr, c + rr], self.START + self.SWEEP * 0.82, self.START + self.SWEEP,
              fill=(*RED, 200), width=round(16 * u * ss))
        step = self._step(self.max)
        majors = int(round(self.max / step))
        f = gfx.font("condensed", round(46 * u * ss))
        for k in range(majors * 5 + 1):
            frac = min(1.0, k * step / 5 / self.max)
            a = self._angle(frac)
            major = k % 5 == 0
            r0, r1 = rr - (46 if major else 30) * u * ss, rr - 20 * u * ss
            d.line([(c + r0 * math.cos(a), c + r0 * math.sin(a)), (c + r1 * math.cos(a), c + r1 * math.sin(a))],
                   fill=(235, 238, 248, 255 if major else 120), width=round((5 if major else 2) * u * ss))
            if major:
                txt = gfx.fmt_number(k // 5 * step, 1 if step < 1 else 0)
                rt = rr - 92 * u * ss
                tw = gfx.text_width(txt, f)
                d.text((c + rt * math.cos(a) - tw / 2, c + rt * math.sin(a) - 26 * u * ss), txt, font=f,
                       fill=(215, 222, 240, 255))
        return img.resize((size // ss, size // ss), Image.LANCZOS)

    @staticmethod
    def _step(mx: float) -> float:
        mag = 10 ** math.floor(math.log10(mx))
        for base in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10):
            step = base * mag
            if 6 <= mx / step <= 12:
                return step
        return mx / 10

    def _needle(self, frac: float) -> Image.Image:
        u, ss = self.u, 2
        size = self.face.width * ss
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        c, rr = size / 2, self.R * ss
        if frac > 0.002:
            d.arc([c - rr, c - rr, c + rr, c + rr], self.START, self.START + self.SWEEP * frac, fill=(*gfx.accent(), 255),
                  width=round(16 * u * ss))
        a = self._angle(frac)
        tip, back, wid = rr - 34 * u * ss, 30 * u * ss, 8 * u * ss
        perp = a + math.pi / 2
        pts = [(c + tip * math.cos(a), c + tip * math.sin(a)),
               (c - back * math.cos(a) + wid * math.cos(perp), c - back * math.sin(a) + wid * math.sin(perp)),
               (c - back * math.cos(a) - wid * math.cos(perp), c - back * math.sin(a) - wid * math.sin(perp))]
        d.polygon(pts, fill=(*RED, 255))
        d.ellipse([c - 26 * u * ss, c - 26 * u * ss, c + 26 * u * ss, c + 26 * u * ss], fill=(20, 24, 36, 255),
                  outline=(*RED, 255), width=round(4 * u * ss))
        return layer.resize((size // ss, size // ss), Image.BILINEAR)

    def _streaks(self) -> Image.Image:
        """Linhas de velocidade saindo do centro (desenhadas 1 vez; a opacidade segue o ponteiro)."""
        w, h = self.w, self.h
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        rng = self.rng
        cx, cy = w * 0.55, h * 0.5
        for _ in range(90):
            a = float(rng.uniform(0, 2 * math.pi))
            r0 = float(rng.uniform(0.3, 0.6)) * w
            r1 = r0 + float(rng.uniform(0.1, 0.4)) * w
            d.line([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)), (cx + r1 * math.cos(a), cy + r1 * math.sin(a))],
                   fill=(255, 255, 255, int(rng.integers(40, 120))), width=max(1, round(float(rng.uniform(1, 3)) * self.u)))
        return img.filter(ImageFilter.GaussianBlur(1.2))

    def frac_at(self, t: float) -> float:
        p = gfx.progress(t, self.SWEEP_T0, self.t1 - self.SWEEP_T0)
        if self.accel:
            e = 1 - (1 - p) ** 1.8  # arrancada forte que vai perdendo fôlego, como um carro de verdade
        else:
            e = gfx.ease_out_back(p, 1.15)
        frac = self.target / self.max * e
        if p >= 1:
            frac += math.sin(t * 36) * 0.0035  # "vibra" como motor em giro
        return max(0.0, min(1.04, frac))

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.frame(i, t)
        frac = self.frac_at(t)
        canvas.alpha_composite(gfx.fade(self.streaks, min(1.0, frac / max(0.01, self.target / self.max)) * 0.8))
        pf = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.4))
        gfx.paste_center(canvas, gfx.scaled(self.face, 0.9 + 0.1 * pf), self.cx, self.cy, alpha=pf)
        key = round(frac * 1500)
        needle = self.needles.get(key)
        if needle is None:
            needle = self.needles[key] = self._needle(frac)
        gfx.paste_center(canvas, needle, self.cx, self.cy, alpha=pf)
        speed = frac * self.max
        done = gfx.progress(t, self.SWEEP_T0, self.t1 - self.SWEEP_T0) >= 1
        read = self.read.get(gfx.fmt_number(self.target if done else min(speed, self.target), 0))
        gfx.paste_center(canvas, read, self.cx, self.cy + self.R * 0.48, alpha=pf)
        gfx.paste_center(canvas, self.dial_unit, self.cx, self.cy + self.R * 0.48 + read.height * 0.55, alpha=pf)
        # à direita: rótulo e o número grande (cronômetro no 0–100)
        x = w * 0.6
        qh = gfx.ease_out_cubic(gfx.progress(t, 0.2, 0.45))
        gfx.paste_at(canvas, self.head, x - (1 - qh) * 40 * u, h * 0.24, alpha=qh)
        ImageDraw.Draw(canvas).rectangle([x + 6 * u, h * 0.24 - 14 * u, x + 6 * u + 120 * u * qh, h * 0.24 - 6 * u],
                                         fill=(*gfx.accent(), 255))
        p = gfx.progress(t, self.SWEEP_T0, self.t1 - self.SWEEP_T0)
        if self.accel:
            shown = self.value * p
            txt = gfx.fmt_number(shown, max(1, self.dec)) + f" {self.suffix}"
        else:
            txt = self.prefix + gfx.fmt_number(min(speed, self.value) if p < 1 else self.value, self.dec) + \
                (f" {self.suffix}" if self.suffix else "")
        pop = 1 + 0.07 * math.sin(math.pi * gfx.progress(t, self.t1, 0.3))
        big = gfx.scaled(self.big.get(txt), pop)
        gfx.paste_at(canvas, big, x - 10 * u, h * 0.24 + self.head.height + 10 * u, alpha=qh)
        if self.sub is not None:
            gfx.paste_at(canvas, self.sub, x, h * 0.24 + self.head.height + big.height, alpha=qh * 0.9)
        return canvas


# ---------------------------------------------------------------- datasheet
class Datasheet(Template):
    FIRST = 0.8

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(96 * u)
        self.bg = _navy_bg(self, self.cell)
        self.rows = [st for st in (spec.stats or []) if str(st.get("value", "")).strip()][:6]
        self.n = len(self.rows)
        self.has_img = bool(spec.media)
        self.img = self.src(spec.media[0], round(w * 0.36), round(h * 0.5), zoom=1.08) if spec.media else None
        self.x0 = w * (0.47 if self.has_img else 0.14)
        self.x1 = w * (0.94 if self.has_img else 0.86)
        self.title = _title_block(self, spec.keyword, 110)
        self.sub = gfx.text_block([spec.subtitle.upper()], gfx.font("oswald_light", round(50 * u)), MUTED, shadow=2) \
            if spec.subtitle else None
        self.top = h * (0.37 if self.sub else 0.29)
        self.row_h = min(h * 0.14, (h * 0.92 - self.top) / max(1, self.n))
        self.f_label = gfx.font("oswald", round(min(54, self.row_h / u * 0.42) * u))
        self.f_value = gfx.font("condensed", round(min(120, self.row_h / u * 0.86) * u))
        self.f_unit = gfx.font("oswald_bold", round(min(46, self.row_h / u * 0.36) * u))
        self.parsed = [gfx.parse_number(str(r.get("value", ""))) for r in self.rows]
        self.starts = item_starts(spec, self.n, first=self.FIRST, gap=0.3)
        self.done: dict[int, Image.Image] = {}

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len([st for st in (spec.stats or []) if str(st.get("value", "")).strip()][:6])
        out: list[Cue] = [(0.0, "whoosh_soft", 0.45)]
        for s in item_starts(spec, n, first=cls.FIRST, gap=0.3):
            out += [(s, "swipe", 0.3), (s + 0.75, "pop", 0.35)]
        return out

    def _row(self, k: int, count: float) -> Image.Image:
        u = self.u
        row = self.rows[k]
        W, H = round(self.x1 - self.x0), round(self.row_h)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        label = str(row.get("label", "")).upper()
        asc, desc = self.f_value.getmetrics()
        base_y = (H - (asc + desc)) / 2
        d.text((0, base_y + (asc - self.f_label.getmetrics()[0]) * 0.9), label, font=self.f_label, fill=(*MUTED, 255))
        parsed = self.parsed[k]
        raw = str(row.get("value", ""))
        if parsed and not re.search(r"[^\d.,\s]", raw):
            value, dec = parsed
            txt = gfx.fmt_number(value * count, dec)
        else:
            txt = raw
        unit = str(row.get("unit", "") or "")
        uw = gfx.text_width(unit, self.f_unit) + 10 * u if unit else 0
        vw = gfx.text_width(txt, self.f_value)
        vx = W - uw - vw
        d.text((vx, base_y), txt, font=self.f_value, fill=(255, 255, 255, 255))
        if unit:
            d.text((W - uw + 10 * u, base_y + asc - self.f_unit.getmetrics()[0] - 4 * u), unit, font=self.f_unit,
                   fill=(*gfx.accent(), 255))
        # pontilhado entre o nome e o valor
        lx = gfx.text_width(label, self.f_label) + 16 * u
        y = base_y + asc - 6 * u
        x = lx
        while x < vx - 16 * u:
            d.ellipse([x, y, x + 4 * u, y + 4 * u], fill=(255, 255, 255, 90))
            x += 14 * u
        d.rectangle([0, H - 3 * u, W, H - 1], fill=(255, 255, 255, 30))
        return layer

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=10).copy()
        _sweep(canvas, t, w, h)
        if self.img is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.6))
            layer = gfx.with_shadow(gfx.bordered(self.img.frame(i), max(3, round(6 * u)), (245, 245, 245)), -2.0,
                                    round(18 * u), (0, round(12 * u)), 170)
            gfx.paste_center(canvas, layer, w * 0.235 - (1 - p) * 160 * u, h * 0.55 + math.sin(t * 1.2) * 4 * u, alpha=p)
        _draw_title(self, canvas, self.title, t, self.x0, h * 0.1)
        if self.sub is not None and self.title is not None:
            gfx.paste_at(canvas, self.sub, self.x0, h * 0.1 + self.title.height - 6 * u,
                         alpha=gfx.progress(t, 0.3, 0.4))
        for k in range(self.n):
            st = self.starts[k]
            q = gfx.ease_out_cubic(gfx.progress(t, st, 0.4))
            if q <= 0:
                continue
            count = gfx.ease_out_cubic(gfx.progress(t, st + 0.1, 0.7))
            layer = self.done.get(k)
            if layer is None:
                layer = self._row(k, count)
                if count >= 1:
                    self.done[k] = layer
            y = self.top + k * self.row_h
            gfx.paste_at(canvas, layer, self.x0 + (1 - q) * 50 * u, y, alpha=q)
            d = ImageDraw.Draw(canvas)
            d.rectangle([self.x0, y + self.row_h - 3 * u, self.x0 + (self.x1 - self.x0) * count, y + self.row_h],
                        fill=(*gfx.accent(), 200))
        return canvas


# -------------------------------------------------------------------- price
class Price(Template):
    COUNT_T0, COUNT_DUR = 0.55, 1.2
    METER_T0 = 1.95

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.value, self.dec, self.prefix, self.suffix, label, _ = _number(spec)
        # faixa de preço ("entre 3 000 et 6 000 euros"): os dois valores contam juntos
        try:
            self.value_to = float((spec.number or {}).get("value_to") or 0)
        except (TypeError, ValueError):
            self.value_to = 0.0
        if self.value_to <= self.value:
            self.value_to = 0.0
        self.level = self.level_of(spec)
        self.bg = _MediaBackground(self, spec.media[0] if spec.media else None, dim=0.36)
        final = self.text(1.0)
        size = 210 * u
        self.f_num = gfx.font("condensed", round(size))
        tw = gfx.text_width(final, self.f_num)
        room = w * 0.9 - 220 * u  # a etiqueta nunca passa da tela
        if tw > room:
            self.f_num = gfx.font("condensed", round(size * room / tw))
            tw = gfx.text_width(final, self.f_num)
        self.tag_w, self.tag_h = max(round(w * 0.42), tw + round(220 * u)), round(h * 0.32)
        self.tag = self._tag()
        head = label or spec.keyword
        f_head = gfx.font("oswald_bold", round(70 * u))
        self.head = gfx.text_block([head.upper()], f_head, WHITE, shadow=3) if head else None
        self.sub = gfx.text_block([spec.subtitle.upper()], gfx.font("oswald_light", round(48 * u)), MUTED, shadow=2) \
            if spec.subtitle else None
        self.meter = self._meter() if self.level >= 0 else None
        f_m = gfx.font("oswald_bold", round(46 * u))
        self.meter_labels = [gfx.text_block([tr(k, spec.lang)], f_m, c, shadow=2)
                             for k, c in (("cheap", GREEN), ("fair", YELLOW), ("expensive", RED))]

    def text(self, q: float) -> str:
        if not self.value_to:
            return gfx.fmt_number(self.value * q, self.dec, self.prefix, self.suffix)
        return (gfx.fmt_number(self.value * q, self.dec, self.prefix, "") + " – "
                + gfx.fmt_number(self.value_to * q, self.dec, self.prefix, self.suffix))

    @staticmethod
    def level_of(spec: TemplateSpec) -> float:
        try:
            lvl = float((spec.number or {}).get("level", -1))
        except (TypeError, ValueError):
            return -1.0
        return max(0.0, min(1.0, lvl)) if lvl >= 0 else -1.0

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(0.08, "whoosh", 0.5), (0.45, "impact_soft", 0.35)]
        out += [(cls.COUNT_T0 + 0.1 * k, "tick", 0.25) for k in range(int(cls.COUNT_DUR / 0.1))]
        out.append((cls.COUNT_T0 + cls.COUNT_DUR, "ding", 0.5))
        if cls.level_of(spec) >= 0 and spec.frames / spec.fps > cls.METER_T0 + 0.8:
            out += [(cls.METER_T0, "swipe", 0.35), (cls.METER_T0 + 0.7, "pop", 0.4)]
        return out

    def _tag(self) -> Image.Image:
        """Etiqueta amarela com furo e bico (desenhada em 2x)."""
        ss, u = 2, self.u
        W, H = self.tag_w * ss, self.tag_h * ss
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        notch = H * 0.42
        d.polygon([(0, H / 2), (notch, 0), (W, 0), (W, H), (notch, H)], fill=(*gfx.accent(), 255))
        d.rounded_rectangle([notch * 0.6, 0, W, H], radius=round(22 * u * ss), fill=(*gfx.accent(), 255))
        hole = H * 0.09
        d.ellipse([notch * 0.62 - hole, H / 2 - hole, notch * 0.62 + hole, H / 2 + hole], fill=(0, 0, 0, 0))
        d.rounded_rectangle([notch * 0.95, 14 * u * ss, W - 14 * u * ss, H - 14 * u * ss], radius=round(16 * u * ss),
                            outline=(30, 24, 8, 120), width=round(3 * u * ss))
        return img.resize((self.tag_w, self.tag_h), Image.LANCZOS)

    def _meter(self) -> Image.Image:
        u = self.u
        mw, mh = round(self.w * 0.6), round(30 * u)
        x = np.linspace(0, 1, mw, dtype=np.float32)
        stops = np.array([GREEN, YELLOW, RED], np.float32)
        rgb = np.stack([np.interp(x, [0, 0.5, 1], stops[:, c]) for c in range(3)], axis=-1)
        arr = np.repeat(rgb[None], mh, axis=0).astype(np.uint8)
        bar = Image.fromarray(arr, "RGB").convert("RGBA")
        mask = Image.new("L", (mw, mh), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, mw - 1, mh - 1], radius=mh // 2, fill=255)
        bar.putalpha(mask)
        return bar

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.frame(i, t)
        # etiqueta cai do alto e balança até parar (pêndulo amortecido)
        drop = gfx.ease_out_back(gfx.progress(t, 0.05, 0.5), 1.3)
        swing = 13 * math.exp(-3.0 * max(0.0, t - 0.3)) * math.cos(8.5 * max(0.0, t - 0.3))
        tag = self.tag.copy()
        q = gfx.ease_out_cubic(gfx.progress(t, self.COUNT_T0, self.COUNT_DUR))
        txt = self.text(q)
        d = ImageDraw.Draw(tag)
        tw = gfx.text_width(txt, self.f_num)
        asc, desc = self.f_num.getmetrics()
        notch = self.tag_h * 0.42
        d.text((notch + (self.tag_w - notch - tw) / 2, (self.tag_h - asc - desc) / 2 + 8 * u), txt, font=self.f_num,
               fill=(22, 18, 8, 255))
        pivot = (notch * 0.62, self.tag_h / 2)
        rot, pv = _rotate_about(tag, swing, pivot)
        anchor_x = w / 2 - self.tag_w / 2 + pivot[0]
        anchor_y = h * 0.5 - (1 - drop) * h * 0.75
        dd = ImageDraw.Draw(canvas)
        dd.line([(anchor_x, anchor_y), (anchor_x - 40 * u, -10)], fill=(235, 235, 235, 220), width=max(2, round(3 * u)))
        # sombra com o formato da etiqueta (bico e furo incluídos)
        blur = round(14 * u)
        sh_a = Image.new("L", (rot.width + 4 * blur, rot.height + 4 * blur), 0)
        sh_a.paste(rot.getchannel("A").point(lambda a: a * 150 // 255), (2 * blur, 2 * blur))
        shadow = Image.new("RGBA", sh_a.size, (0, 0, 0, 255))
        shadow.putalpha(sh_a.filter(ImageFilter.GaussianBlur(blur)))
        x, y = anchor_x - pv[0], anchor_y - pv[1]
        gfx.paste_at(canvas, shadow, x - 2 * blur, y - 2 * blur + 12 * u)
        gfx.paste_at(canvas, rot, x, y)
        if self.head is not None:
            qh = gfx.ease_out_cubic(gfx.progress(t, 0.35, 0.4))
            gfx.paste_center(canvas, self.head, w / 2, h * 0.13 + (1 - qh) * 20 * u, alpha=qh)
            if self.sub is not None:
                gfx.paste_center(canvas, self.sub, w / 2, h * 0.13 + self.head.height * 0.8, alpha=qh * 0.9)
        if self.meter is not None:
            qm = gfx.ease_out_cubic(gfx.progress(t, self.METER_T0 - 0.3, 0.35))
            if qm > 0:
                mx0, my = (w - self.meter.width) / 2, h * 0.82
                gfx.paste_at(canvas, self.meter, mx0, my + (1 - qm) * 30 * u, alpha=qm)
                for lab, pos in zip(self.meter_labels, (0.0, 0.5, 1.0)):
                    gfx.paste_center(canvas, lab, mx0 + self.meter.width * pos, my + 76 * u, alpha=qm)
                pp = gfx.ease_out_back(gfx.progress(t, self.METER_T0, 0.7), 1.2)
                px = mx0 + self.meter.width * max(0.0, min(1.0, self.level * pp))
                s = 26 * u
                dd = ImageDraw.Draw(canvas)
                dd.polygon([(px - s, my - s * 1.6), (px + s, my - s * 1.6), (px, my - 2 * u)],
                           fill=(255, 255, 255, round(255 * qm)))
        return canvas


# ----------------------------------------------------------------- proscons
class ProsCons(Template):
    FIRST = 0.7

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.points = [p for p in (spec.points or []) if str(p.get("text", "")).strip()][:6]
        self.starts = item_starts(spec, len(self.points), first=self.FIRST, gap=0.3)
        self.halves = [self._half(GREEN, (6, 40, 24), (12, 70, 40)), self._half(RED, (46, 8, 10), (92, 20, 20))]
        f_head = gfx.font("condensed", round(124 * u))
        self.heads = [gfx.text_block([tr("pros", spec.lang)], f_head, WHITE, shadow=4),
                      gfx.text_block([tr("cons", spec.lang)], f_head, WHITE, shadow=4)]
        isz = round(92 * u)
        self.head_icons = [_icon("check", isz, WHITE, (*GREEN, 255)), _icon("cross", isz, WHITE, (*RED, 255))]
        rsz = round(64 * u)
        self.row_icons = [_icon("check", rsz, WHITE, (*GREEN, 255)), _icon("cross", rsz, WHITE, (*RED, 255))]
        self.f_row = gfx.font("montserrat_bold", round(54 * u))
        cols = [[k for k, p in enumerate(self.points) if p.get("positive", True)],
                [k for k, p in enumerate(self.points) if not p.get("positive", True)]]
        self.slot: dict[int, tuple[int, int]] = {}
        for c, ks in enumerate(cols):
            for r, k in enumerate(ks):
                self.slot[k] = (c, r)
        n_rows = max(1, max(len(c) for c in cols))
        self.row_h = min(h * 0.19, h * 0.66 / n_rows)
        self.rows = [self._row(str(p.get("text", "")), 0 if p.get("positive", True) else 1) for p in self.points]

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        pts = [p for p in (spec.points or []) if str(p.get("text", "")).strip()][:6]
        out: list[Cue] = [(0.0, "whoosh", 0.5), (0.4, "impact_soft", 0.45)]
        for p, s in zip(pts, item_starts(spec, len(pts), first=cls.FIRST, gap=0.3)):
            out.append((s, "pop", 0.45) if p.get("positive", True) else (s, "impact_soft", 0.35))
        return out

    def _half(self, accent: tuple[int, int, int], top: tuple[int, int, int], bot: tuple[int, int, int]) -> Image.Image:
        w2, h = self.w // 2 + 2, self.h
        g = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        rgb = np.stack([top[c] + (bot[c] - top[c]) * g for c in range(3)], axis=-1)
        arr = np.repeat(rgb, w2, axis=1)
        # grade sutil
        cell = max(8, round(48 * self.u))
        arr[::cell, :, :] += 10
        arr[:, ::cell, :] += 10
        img = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w2, round(8 * self.u)], fill=(*accent, 255))
        return img

    def _row(self, text: str, col: int) -> Image.Image:
        u = self.u
        W = round(self.w * 0.45)
        lines = gfx.wrap(text, self.f_row, W - round(130 * u), 2)
        asc, desc = self.f_row.getmetrics()
        H = max(round(self.row_h * 0.84), round((asc + desc) * len(lines) + 30 * u))
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=round(18 * u), fill=(255, 255, 255, 26),
                            outline=(255, 255, 255, 50), width=max(1, round(2 * u)))
        icon = self.row_icons[col]
        img.alpha_composite(icon, (round(20 * u), (H - icon.height) // 2))
        y = (H - (asc + desc) * len(lines)) / 2
        for ln in lines:
            d.text((round(112 * u), y), ln, font=self.f_row, fill=(255, 255, 255, 255))
            y += asc + desc
        return img

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.45))
        canvas.alpha_composite(self.halves[0], (round(-(1 - q) * w / 2), 0))
        canvas.alpha_composite(self.halves[1], (round(w / 2 + (1 - q) * w / 2), 0))
        d = ImageDraw.Draw(canvas)
        if q >= 0.99:
            lw = max(2, round(4 * u))
            glow = gfx.ease_out_cubic(gfx.progress(t, 0.4, 0.4))
            d.rectangle([w / 2 - lw / 2, h * (0.5 - 0.46 * glow), w / 2 + lw / 2, h * (0.5 + 0.46 * glow)],
                        fill=(255, 255, 255, 230))
        for c in range(2):
            cx = w * (0.25 + 0.5 * c)
            qh = gfx.ease_out_back(gfx.progress(t, 0.3 + 0.1 * c, 0.4), 1.6)
            if qh <= 0:
                continue
            icon, head = self.head_icons[c], self.heads[c]
            total = icon.width + 18 * u + head.width
            x = cx - total / 2
            gfx.paste_center(canvas, gfx.scaled(icon, max(0.05, qh)), x + icon.width / 2, h * 0.15)
            gfx.paste_at(canvas, head, x + icon.width + 18 * u - 20 * u, h * 0.15 - head.height / 2,
                         alpha=min(1.0, qh))
        for k, row in enumerate(self.rows):
            qr = gfx.progress(t, self.starts[k], 0.35)
            if qr <= 0:
                continue
            col, r = self.slot[k]
            e = gfx.ease_out_cubic(qr)
            cx = w * (0.25 + 0.5 * col)
            y = h * 0.27 + r * self.row_h
            dx = (-1 if col == 0 else 1) * (1 - e) * 60 * u
            gfx.paste_at(canvas, row, cx - row.width / 2 + dx, y, alpha=e)
        return canvas


# -------------------------------------------------------------------- score
class Score(Template):
    FIRST = 0.6

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(96 * u)
        self.bg = _navy_bg(self, self.cell)
        rows = []
        for st in (spec.stats or [])[:6]:
            parsed = gfx.parse_number(str(st.get("value", "")))
            if parsed is not None:
                rows.append((str(st.get("label", "")), parsed[0], parsed[1]))
        self.rows = rows
        value, dec, _, _, _, mx = _number(spec)
        self.final = value if value > 0 else None
        self.final_dec = dec if dec else (1 if self.final and self.final != int(self.final) else 0)
        self.scale = self.scale_of(spec)
        self.starts = item_starts(spec, len(rows), first=self.FIRST, gap=0.35)
        self.ring_t0 = self.ring_start(spec)
        self.f_label = gfx.font("oswald", round(52 * u))
        self.nums = TextCache(gfx.font("condensed", round(86 * u)), WHITE)
        self.big = TextCache(gfx.font("condensed", round(210 * u)), WHITE, shadow=5, shadow_blur=12)
        self.labels = [gfx.text_block([lb.upper()], self.f_label, MUTED) for lb, _, _ in rows]
        self.ring_label = gfx.text_block([tr("final_score", spec.lang)], gfx.font("oswald_bold", round(46 * u)),
                                         gfx.accent(), shadow=2)
        self.of = gfx.text_block([f"/{gfx.fmt_number(self.scale, 0)}"], gfx.font("condensed", round(64 * u)), MUTED)
        self.title = _title_block(self, spec.keyword)
        self.R = round(h * 0.22)

    @staticmethod
    def scale_of(spec: TemplateSpec) -> float:
        value, _, _, _, _, mx = _number(spec)
        if mx > 0:
            return mx
        vals = [p[0] for st in (spec.stats or []) if (p := gfx.parse_number(str(st.get("value", ""))))]
        top = max(vals + [value, 0.0])
        return 5.0 if top <= 5 else 10.0 if top <= 10 else 100.0

    @classmethod
    def ring_start(cls, spec: TemplateSpec) -> float:
        n = len([1 for st in (spec.stats or [])[:6] if gfx.parse_number(str(st.get("value", "")))])
        dur = spec.frames / spec.fps
        starts = item_starts(spec, n, first=cls.FIRST, gap=0.35)
        t = (starts[-1] + 0.7) if starts else 0.5
        return min(t, max(0.5, dur - 1.8))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len([1 for st in (spec.stats or [])[:6] if gfx.parse_number(str(st.get("value", "")))])
        out: list[Cue] = [(0.0, "whoosh_soft", 0.45)]
        for s in item_starts(spec, n, first=cls.FIRST, gap=0.35):
            out += [(s, "whoosh_soft", 0.3), (s + 0.6, "pop", 0.4)]
        if float((spec.number or {}).get("value") or 0) > 0:
            r0 = cls.ring_start(spec)
            out += [(r0, "riser", 0.4), (r0 + 1.1, "impact", 0.5)]
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=10).copy()
        _sweep(canvas, t, w, h)
        d = ImageDraw.Draw(canvas)
        _draw_title(self, canvas, self.title, t, w * 0.07, h * 0.08)
        x0 = w * 0.07
        bar_w = w * (0.42 if self.final else 0.7)
        n = max(1, len(self.rows))
        top = h * 0.27
        row_h = min(h * 0.16, h * 0.68 / n)
        for k, (label, value, dec) in enumerate(self.rows):
            st = self.starts[k]
            q = gfx.ease_out_cubic(gfx.progress(t, st, 0.35))
            if q <= 0:
                continue
            fill = gfx.ease_out_cubic(gfx.progress(t, st + 0.1, 0.6))
            y = top + k * row_h
            gfx.paste_at(canvas, self.labels[k], x0 + (1 - q) * 30 * u, y, alpha=q)
            by = y + self.labels[k].height + 4 * u
            bh = 26 * u
            d.rounded_rectangle([x0, by, x0 + bar_w, by + bh], radius=bh / 2, fill=(255, 255, 255, round(40 * q)))
            length = bar_w * min(1.0, value / self.scale) * fill
            if length > bh:
                color = gfx.accent() if value / self.scale >= 0.7 else TEAL
                d.rounded_rectangle([x0, by, x0 + length, by + bh], radius=bh / 2, fill=(*color, 255))
            num = self.nums.get(gfx.fmt_number(value * fill, dec))
            gfx.paste_at(canvas, num, x0 + bar_w + 24 * u, by + bh / 2 - num.height / 2 - 4 * u, alpha=q)
        if self.final is not None:
            cx, cy, R = w * 0.78, h * 0.52, self.R
            qa = gfx.ease_out_cubic(gfx.progress(t, self.ring_t0 - 0.2, 0.4))
            if qa > 0:
                lw = round(26 * u)
                d.arc([cx - R, cy - R, cx + R, cy + R], 0, 360, fill=(255, 255, 255, round(45 * qa)), width=lw)
                qf = gfx.ease_out_cubic(gfx.progress(t, self.ring_t0, 1.1))
                if qf > 0.002:
                    d.arc([cx - R, cy - R, cx + R, cy + R], -90, -90 + 360 * min(1.0, self.final / self.scale) * qf,
                          fill=(*gfx.accent(), 255), width=lw)
                pop = 1 + 0.08 * math.sin(math.pi * gfx.progress(t, self.ring_t0 + 1.1, 0.3))
                big = gfx.scaled(self.big.get(gfx.fmt_number(self.final * qf, self.final_dec)), pop)
                gfx.paste_center(canvas, big, cx, cy - 6 * u, alpha=qa)
                gfx.paste_center(canvas, self.of, cx, cy + R * 0.55, alpha=qa)
                gfx.paste_center(canvas, self.ring_label, cx, cy + R + 60 * u, alpha=qa)
        return canvas


# ---------------------------------------------------------------- magnifier
class Magnifier(Template):
    LENS_T0, LABEL_T0 = 0.35, 0.85

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        from .overlays import salient_point  # import tardio: overlays importa os templates

        u, w, h = self.u, self.w, self.h
        self.base = self.src(spec.media[0], w, h, zoom=1.05)
        first = self.base.frame(0)
        tx, ty = salient_point(first)
        self.tx, self.ty = min(0.75, max(0.25, tx)) * w, min(0.72, max(0.3, ty)) * h
        self.R = round(h * 0.2)
        ss = 3
        mask = Image.new("L", (2 * self.R * ss, 2 * self.R * ss), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 2 * self.R * ss - 1, 2 * self.R * ss - 1], fill=255)
        self.mask = mask.resize((2 * self.R, 2 * self.R), Image.LANCZOS)
        ring = Image.new("RGBA", (2 * self.R * ss + 40 * ss, 2 * self.R * ss + 40 * ss), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        o = 20 * ss
        rd.ellipse([o, o, o + 2 * self.R * ss, o + 2 * self.R * ss], outline=(255, 255, 255, 255),
                   width=round(8 * u * ss))
        rd.ellipse([o - 6 * u * ss, o - 6 * u * ss, o + 2 * self.R * ss + 6 * u * ss, o + 2 * self.R * ss + 6 * u * ss],
                   outline=(*gfx.accent(), 255), width=round(4 * u * ss))
        self.ring = ring.resize((ring.width // ss, ring.height // ss), Image.LANCZOS)
        self.left = self.tx > w / 2  # rótulo do lado com mais espaço
        title = (spec.keyword or "").upper()
        self.label = self._label(title, spec.subtitle) if title else None
        self.dim = Image.new("RGBA", (w, h), (0, 0, 0, 255))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(cls.LENS_T0 - 0.1, "whoosh_soft", 0.45), (cls.LENS_T0 + 0.25, "pop", 0.45)]
        if spec.keyword:
            out.append((cls.LABEL_T0, "swipe", 0.4))
        return out

    def _label(self, title: str, sub: str) -> Image.Image:
        u = self.u
        f1, f2 = gfx.font("condensed", round(110 * u)), gfx.font("oswald_light", round(46 * u))
        lines1 = gfx.wrap(title, f1, round(self.w * 0.3), 2)
        tw = max(gfx.text_width(x, f1) for x in lines1)
        sw = gfx.text_width(sub.upper(), f2) if sub else 0
        a1, d1 = f1.getmetrics()
        a2, d2 = f2.getmetrics()
        W = round(max(tw, sw) + 70 * u)
        H = round((a1 + d1) * 0.92 * len(lines1) + ((a2 + d2) if sub else 0) + 40 * u)
        img = Image.new("RGBA", (W, H), (8, 12, 26, 215))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, round(10 * u), H], fill=(*gfx.accent(), 255))
        y = 18 * u
        for ln in lines1:
            d.text((40 * u, y), ln, font=f1, fill=(255, 255, 255, 255))
            y += (a1 + d1) * 0.92
        if sub:
            d.text((40 * u, y), sub.upper(), font=f2, fill=(*MUTED, 255))
        return img

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        img = self.base.frame(i)
        canvas = img.convert("RGBA")
        qd = gfx.ease_out_cubic(gfx.progress(t, 0.2, 0.4))
        if qd > 0:
            canvas.alpha_composite(gfx.fade(self.dim, 0.38 * qd))
        q = gfx.progress(t, self.LENS_T0, 0.45)
        if q <= 0:
            return canvas
        pop = gfx.ease_out_back(q, 1.6)
        mag = 1 + 1.3 * gfx.ease_in_out(gfx.progress(t, self.LENS_T0 + 0.1, 0.8))
        cx = self.tx + math.sin(t * 0.9) * 6 * u
        cy = self.ty + math.cos(t * 0.7) * 4 * u
        half = self.R / mag
        lens = img.resize((2 * self.R, 2 * self.R), Image.BICUBIC,
                          box=(cx - half, cy - half, cx + half, cy + half)).convert("RGBA")
        lens.putalpha(self.mask)
        lens = gfx.with_shadow(lens, 0, round(20 * u), (0, round(10 * u)), 160)
        s = max(0.05, pop)
        gfx.paste_center(canvas, gfx.scaled(lens, s), cx, cy)
        gfx.paste_center(canvas, gfx.scaled(self.ring, s), cx, cy)
        if self.label is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, self.LABEL_T0, 0.4))
            if ql > 0:
                lx = w * 0.06 if self.left else w * 0.94 - self.label.width
                ly = min(h * 0.82 - self.label.height, max(h * 0.1, cy - self.R - self.label.height * 0.3))
                edge_x = lx + self.label.width if self.left else lx
                ang = math.atan2(ly + self.label.height / 2 - cy, edge_x - cx)
                sx, sy = cx + (self.R + 10 * u) * math.cos(ang), cy + (self.R + 10 * u) * math.sin(ang)
                ex = sx + (edge_x - sx) * ql
                ey = sy + (ly + self.label.height / 2 - sy) * ql
                ImageDraw.Draw(canvas).line([(sx, sy), (ex, ey)], fill=(*gfx.accent(), 255), width=max(2, round(4 * u)))
                gfx.paste_at(canvas, self.label, lx + (-1 if self.left else 1) * (1 - ql) * 40 * u, ly, alpha=ql)
        return canvas


# -------------------------------------------------------------- beforeafter
class BeforeAfter(Template):
    KEYS = [(0.0, 1.0), (0.25, 1.0), (1.0, 0.5), (1.9, 0.5), (2.6, 0.2), (3.3, 0.2), (4.0, 0.5)]

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        media = (spec.media * 2)[:2]
        self.a = self.src(media[0], w, h, zoom=1.05)
        self.b = self.src(media[1], w, h, zoom=1.05, pan=(0.3, 0.0))
        labels = [str(x) for x in spec.labels if str(x).strip()][:2]
        if len(labels) < 2:
            labels = [tr("before", spec.lang), tr("after", spec.lang)]
        f = gfx.font("condensed", round(64 * u))
        self.chips = [gfx.chip(lb.upper(), f, (8, 12, 26), WHITE, round(w * 0.3), round(24 * u), round(8 * u))
                      for lb in labels]
        hr = round(46 * u)
        handle = Image.new("RGBA", (hr * 2 * 3, hr * 2 * 3), (0, 0, 0, 0))
        hd = ImageDraw.Draw(handle)
        S = hr * 2 * 3
        hd.ellipse([0, 0, S - 1, S - 1], fill=(255, 255, 255, 255))
        a = S * 0.14
        hd.polygon([(S * 0.2, S / 2), (S * 0.2 + a, S / 2 - a), (S * 0.2 + a, S / 2 + a)], fill=(*NAVY_DARK, 255))
        hd.polygon([(S * 0.8, S / 2), (S * 0.8 - a, S / 2 - a), (S * 0.8 - a, S / 2 + a)], fill=(*NAVY_DARK, 255))
        self.handle = gfx.with_shadow(handle.resize((hr * 2, hr * 2), Image.LANCZOS), 0, round(10 * u), (0, 4), 150)

    @classmethod
    def keys(cls, spec: TemplateSpec) -> list[tuple[float, float]]:
        dur = spec.frames / spec.fps
        k = min(1.0, max(0.45, (dur - 0.4) / 4.0))
        return [(t * k, p) for t, p in cls.KEYS]

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        keys = cls.keys(spec)
        return [(t0, "swipe", 0.4) for (t0, p0), (_, p1) in zip(keys, keys[1:]) if abs(p1 - p0) > 0.05]

    def pos(self, t: float) -> float:
        keys = self.keys(self.s)
        for (t0, p0), (t1, p1) in zip(keys, keys[1:]):
            if t <= t1:
                return p0 + (p1 - p0) * gfx.ease_in_out(gfx.progress(t, t0, t1 - t0))
        return keys[-1][1]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        a, b = self.a.frame(i), self.b.frame(i)
        x = round(self.pos(t) * w)
        canvas = a.convert("RGBA")
        if x < w:
            canvas.paste(b.crop((x, 0, w, h)), (x, 0))
        d = ImageDraw.Draw(canvas)
        lw = max(2, round(5 * u))
        if 0 < x < w:
            d.rectangle([x - lw / 2, 0, x + lw / 2, h], fill=(255, 255, 255, 255))
            gfx.paste_center(canvas, self.handle, x, h / 2)
        qa = min(1.0, max(0.0, (x - 0.12 * w) / (0.12 * w)))
        qb = min(1.0, max(0.0, (0.88 * w - x) / (0.12 * w)))
        gfx.paste_at(canvas, self.chips[0], 40 * u, 40 * u, alpha=qa)
        gfx.paste_at(canvas, self.chips[1], w - 40 * u - self.chips[1].width, 40 * u, alpha=qb)
        return canvas


# --------------------------------------------------------------------- news
class News(Template):
    MARK_T0, MARK_STEP, MARK_WIPE = 1.0, 0.7, 0.45

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = _MediaBackground(self, spec.media[0] if spec.media else None, dim=0.3, zoom=1.15)
        self.headline = (spec.body or spec.keyword or "").strip()
        self.pw, self.ph = round(w * 0.6), round(h * 0.84)
        photo = None
        if spec.media:
            src = self.src(spec.media[0], self.pw - round(120 * u), round(h * 0.3), zoom=1.0)
            photo = src.frame(0)
        self.paper, self.boxes = self._paper(spec, photo)
        self.marks = self.boxes[:3]
        self._cache_key: tuple | None = None
        self._cache: Image.Image | None = None

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len([x for x in spec.labels if str(x).strip()][:3])
        out: list[Cue] = [(0.0, "whoosh", 0.45), (0.38, "snap", 0.45)]
        out += [(cls.MARK_T0 + k * cls.MARK_STEP, "swipe", 0.35) for k in range(n)]
        return out

    def _paper(self, spec: TemplateSpec, photo: Image.Image | None) -> tuple[Image.Image, list[list[tuple]]]:
        u, pw, ph = self.u, self.pw, self.ph
        rng = self.rng
        base = np.full((ph, pw, 3), (243, 239, 228), np.float32)
        base += rng.normal(0, 4, (ph, pw, 1)).astype(np.float32)
        paper = Image.fromarray(base.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
        # bordas de cima e de baixo rasgadas
        mask = Image.new("L", (pw, ph), 255)
        md = ImageDraw.Draw(mask)
        step = max(6, round(14 * u))
        top = [(x, float(rng.uniform(0, 12 * u))) for x in range(0, pw + step, step)]
        bot = [(x, ph - float(rng.uniform(0, 12 * u))) for x in range(0, pw + step, step)]
        md.polygon([(0, 0)] + top + [(pw, 0)], fill=0)
        md.polygon([(0, ph)] + bot + [(pw, ph)], fill=0)
        paper.putalpha(mask)
        d = ImageDraw.Draw(paper)
        pad = round(60 * u)
        y = round(46 * u)
        chip = gfx.chip(tr("news", spec.lang), gfx.font("oswald_bold", round(30 * u)), (200, 30, 30), WHITE,
                        round(pw * 0.4), round(14 * u), round(6 * u))
        paper.alpha_composite(chip, (pad, y))
        if spec.subtitle:
            d.text((pad + chip.width + 20 * u, y + 4 * u), spec.subtitle.upper(), font=gfx.font("oswald", round(32 * u)),
                   fill=(90, 90, 90, 255))
        y += chip.height + round(18 * u)
        d.rectangle([pad, y, pw - pad, y + 3 * u], fill=(30, 30, 30, 255))
        d.rectangle([pad, y + 8 * u, pw - pad, y + 9 * u], fill=(30, 30, 30, 255))
        y += round(30 * u)
        # manchete com a posição de cada palavra (para o marca-texto)
        size = 76 if len(self.headline) <= 60 else 62
        f = gfx.font("black", round(size * u))
        lines = gfx.wrap(self.headline, f, pw - 2 * pad, 4)
        asc, desc = f.getmetrics()
        lh = round((asc + desc) * 1.02)
        words: list[tuple[str, tuple[float, float, float, float]]] = []
        space = gfx.text_width(" ", f)
        y_head = y
        for ln in lines:
            x = pad
            for word in ln.split():
                ww = gfx.text_width(word, f)
                words.append((word, (x - 4 * u, y + asc * 0.12, x + ww + 4 * u, y + asc + desc * 0.6)))
                x += ww + space
            y += lh
        self.words_boxes = words
        for ln_i, ln in enumerate(lines):
            d.text((pad, y_head + ln_i * lh), ln, font=f, fill=(18, 18, 18, 255))
        y += round(20 * u)
        if photo is not None:
            ph_img = photo.convert("RGB")
            paper.paste(ph_img, (pad, y))
            y += ph_img.height + round(24 * u)
        # linhas de texto (corpo da notícia)
        while y < ph - round(60 * u):
            ln_w = (pw - 2 * pad) * float(rng.uniform(0.7, 1.0))
            d.rounded_rectangle([pad, y, pad + ln_w, y + 12 * u], radius=6 * u, fill=(150, 150, 150, 255))
            y += round(30 * u)
        marks = [self._find(str(x)) for x in spec.labels if str(x).strip()][:3]
        return paper, [m for m in marks if m]

    def _find(self, phrase: str) -> list[tuple[float, float, float, float]]:
        """Caixas (por linha) das palavras da manchete que formam ``phrase``."""
        target = [normalize(x).strip(".,;:!?\"'") for x in phrase.split()]
        norm = [normalize(wd).strip(".,;:!?\"'") for wd, _ in self.words_boxes]
        n = len(target)
        for i in range(len(norm) - n + 1):
            if norm[i:i + n] == target:
                boxes = [b for _, b in self.words_boxes[i:i + n]]
                merged: list[list[float]] = []
                for b in boxes:
                    if merged and abs(merged[-1][1] - b[1]) < 1:
                        merged[-1][2] = b[2]
                    else:
                        merged.append(list(b))
                return [tuple(m) for m in merged]
        return []

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.frame(i, t)
        q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.45))
        angle = round(-2.0 - 4.0 * (1 - q), 1)
        marks = tuple(round(gfx.progress(t, self.MARK_T0 + k * self.MARK_STEP, self.MARK_WIPE), 2)
                      for k in range(len(self.marks)))
        key = (angle, marks)
        if self._cache_key != key:  # o papel só muda na entrada e enquanto o marca-texto passa
            self._cache_key = key
            self._cache = gfx.with_shadow(self._marked(t), angle, round(24 * u), (round(8 * u), round(18 * u)), 170)
        zoom = (1.12 - 0.12 * q) * (1 + 0.04 * gfx.ease_in_out(gfx.progress(t, 0.5, self.s.frames / self.fps)))
        gfx.paste_center(canvas, gfx.scaled(self._cache, zoom * 0.98), w / 2, h / 2 + (1 - q) * 40 * u, alpha=q)
        return canvas

    def _marked(self, t: float) -> Image.Image:
        paper = self.paper
        active = [k for k in range(len(self.marks)) if t >= self.MARK_T0 + k * self.MARK_STEP]
        if active:
            rgb = paper.convert("RGB")
            marker = Image.new("RGB", rgb.size, (255, 255, 255))
            md = ImageDraw.Draw(marker)
            for k in active:
                q = gfx.ease_out_cubic(gfx.progress(t, self.MARK_T0 + k * self.MARK_STEP, self.MARK_WIPE))
                boxes = self.marks[k]
                total = sum(b[2] - b[0] for b in boxes)
                left = total * q
                for x0, y0, x1, y1 in boxes:
                    seg = min(x1 - x0, left)
                    if seg <= 0:
                        break
                    md.rectangle([x0, y0, x0 + seg, y1], fill=(255, 226, 60))
                    left -= seg
            rgb = ImageChops.multiply(rgb, marker)
            paper = rgb.convert("RGBA")
            paper.putalpha(self.paper.getchannel("A"))
        return paper


# ------------------------------------------------------------------ comment
AVATAR_COLORS = [(229, 57, 53), (30, 136, 229), (67, 160, 71), (251, 140, 0), (142, 36, 170), (0, 137, 123)]


class Comment(Template):
    TYPE_T0 = 0.55

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = _MediaBackground(self, spec.media[0] if spec.media else None, dim=0.32)
        self.text = (spec.body or spec.keyword or "").strip()
        self.user = spec.subtitle.strip() or tr("user", spec.lang)
        likes = float((spec.number or {}).get("value") or 0)
        self.likes = gfx.fmt_number(likes, 0) if likes > 0 else ""
        self.cw = round(w * 0.72)
        self.pad = round(48 * u)
        self.av = round(110 * u)
        self.f_text = gfx.font("montserrat", round(56 * u))
        self.f_user = gfx.font("montserrat_bold", round(42 * u))
        self.f_small = gfx.font("montserrat", round(36 * u))
        self.text_x = self.pad + self.av + round(28 * u)
        self.lines = gfx.wrap(self.text, self.f_text, self.cw - self.text_x - self.pad, 5)
        asc, desc = self.f_text.getmetrics()
        self.lh = round((asc + desc) * 1.1)
        self.ch = self.pad * 2 + round(66 * u) + self.lh * len(self.lines) + round(90 * u)
        self.type_t1 = self.type_end(spec)
        color = AVATAR_COLORS[sum(map(ord, self.user)) % len(AVATAR_COLORS)]
        avatar = Image.new("RGBA", (self.av * 3, self.av * 3), (0, 0, 0, 0))
        ad = ImageDraw.Draw(avatar)
        ad.ellipse([0, 0, self.av * 3 - 1, self.av * 3 - 1], fill=(*color, 255))
        letter = (self.user.lstrip("@")[:1] or "?").upper()
        fl = gfx.font("montserrat_bold", round(self.av * 3 * 0.5))
        lw = gfx.text_width(letter, fl)
        la, ldsc = fl.getmetrics()
        ad.text(((self.av * 3 - lw) / 2, (self.av * 3 - la - ldsc) / 2), letter, font=fl, fill=(255, 255, 255, 255))
        self.avatar = avatar.resize((self.av, self.av), Image.LANCZOS)
        isz = round(48 * u)
        self.thumb = _icon("thumb", isz, (241, 241, 241))
        self.thumb_down = _icon("thumb_down", isz, (241, 241, 241))
        self.heart = _icon("heart", round(40 * u), (255, 48, 64))

    @classmethod
    def type_end(cls, spec: TemplateSpec) -> float:
        n = len((spec.body or spec.keyword or "").strip())
        dur = spec.frames / spec.fps
        cps = min(48.0, max(18.0, n / max(0.5, dur * 0.5)))
        return cls.TYPE_T0 + n / cps

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        t1 = cls.type_end(spec)
        return [(0.05, "pop", 0.45), (cls.TYPE_T0, f"typing:{t1 - cls.TYPE_T0:.3f}", 0.6), (t1 + 0.35, "pop", 0.45)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.frame(i, t)
        card = Image.new("RGBA", (self.cw, self.ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        d.rounded_rectangle([0, 0, self.cw - 1, self.ch - 1], radius=round(28 * u), fill=(18, 18, 18, 245),
                            outline=(255, 255, 255, 30), width=max(1, round(2 * u)))
        card.alpha_composite(self.avatar, (self.pad, self.pad))
        d.text((self.text_x, self.pad), self.user, font=self.f_user, fill=(241, 241, 241, 255))
        uw = gfx.text_width(self.user, self.f_user)
        d.text((self.text_x + uw + 16 * u, self.pad + 3 * u), tr("ago", self.s.lang), font=self.f_small,
               fill=(170, 170, 170, 255))
        shown = len(self.text) if t >= self.type_t1 else int(len(self.text) * gfx.progress(t, self.TYPE_T0,
                                                                                            self.type_t1 - self.TYPE_T0))
        y = self.pad + round(66 * u)
        left = shown
        cursor = None
        for ln in self.lines:
            part = ln[:max(0, left)]
            d.text((self.text_x, y), part, font=self.f_text, fill=(241, 241, 241, 255))
            if 0 <= left < len(ln) and cursor is None:
                cursor = (self.text_x + gfx.text_width(part, self.f_text) + 4 * u, y)
            left -= len(ln) + 1
            y += self.lh
        if cursor is not None and int(t * 4) % 2 == 0:
            d.rectangle([cursor[0], cursor[1] + 6 * u, cursor[0] + 4 * u, cursor[1] + self.lh - 10 * u],
                        fill=(241, 241, 241, 255))
        ay = self.ch - self.pad - round(48 * u)
        x = self.text_x
        card.alpha_composite(self.thumb, (round(x), round(ay)))
        x += self.thumb.width + 12 * u
        if self.likes:
            d.text((x, ay + 2 * u), self.likes, font=self.f_small, fill=(170, 170, 170, 255))
            x += gfx.text_width(self.likes, self.f_small) + 30 * u
        card.alpha_composite(self.thumb_down, (round(x), round(ay + 4 * u)))
        x += self.thumb_down.width + 40 * u
        d.text((x, ay + 2 * u), tr("reply", self.s.lang), font=self.f_small, fill=(241, 241, 241, 255))
        qh = gfx.progress(t, self.type_t1 + 0.35, 0.35)
        if qh > 0:
            hx = x + gfx.text_width(tr("reply", self.s.lang), self.f_small) + 50 * u
            small = gfx.scaled(self.avatar, 0.42)
            card.alpha_composite(small, (round(hx), round(ay - 2 * u)))
            heart = gfx.scaled(self.heart, max(0.05, gfx.ease_out_back(qh, 2.4)) * 0.8)
            gfx.paste_center(card, heart, hx + small.width - 2 * u, ay + small.height - 4 * u)
        q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.45))
        layer = gfx.with_shadow(card, 0, round(24 * u), (0, round(14 * u)), 190)
        gfx.paste_center(canvas, layer, w / 2, h / 2 + (1 - q) * 50 * u, alpha=q)
        return canvas


# --------------------------------------------------------------------- poll
class Poll(Template):
    Q_T0 = 0.15
    LETTERS = "ABCD"

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(96 * u)
        self.bg = _navy_bg(self, self.cell)
        self.options = [str(x) for x in spec.labels if str(x).strip()][:4]
        self.n = n = max(2, len(self.options))
        self.options += [""] * (n - len(self.options))
        self.question = (spec.keyword or "").strip().upper()
        self.f_q = gfx.font("condensed", round(112 * u))
        self.q_lines = gfx.wrap(self.question, self.f_q, round(w * 0.86), 2) or [""]
        self.q_cache = TextCache(self.f_q, WHITE, shadow=4, shadow_blur=12)
        self.q_t1 = self.question_end(spec)
        gap = round(34 * u)
        self.cw = round(min(w * 0.3, (w * 0.88 - gap * (n - 1)) / n))
        self.ph = round(self.cw * 0.62)
        self.gap = gap
        self.photos = [self.src(m, self.cw - 12, self.ph - 12, zoom=1.06) for m in spec.media[:n]]
        f_opt = gfx.font("oswald_bold", round(58 * u))
        self.opt_labels = [gfx.text_block(gfx.wrap(o.upper(), f_opt, self.cw - round(20 * u), 2), f_opt, WHITE)
                           if o else None for o in self.options]
        self.badges = [self._badge(self.LETTERS[k]) for k in range(n)]
        self.starts = item_starts(spec, n, first=self.q_t1 + 0.2, gap=0.3)
        self.cta_t0 = self.cta_start(spec)
        f_cta = gfx.font("oswald_bold", round(46 * u))
        self.cta = gfx.chip(tr("comment_cta", spec.lang), f_cta, gfx.accent(), (20, 18, 10), round(w * 0.6), round(30 * u),
                            round(12 * u))
        self.frozen: dict[int, Image.Image] = {}

    def _badge(self, letter: str) -> Image.Image:
        u = self.u
        s = round(70 * u)
        img = Image.new("RGBA", (s * 3, s * 3), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([0, 0, s * 3 - 1, s * 3 - 1], fill=(*gfx.accent(), 255))
        f = gfx.font("condensed", round(s * 3 * 0.7))
        lw = gfx.text_width(letter, f)
        a, dsc = f.getmetrics()
        d.text(((s * 3 - lw) / 2, (s * 3 - a - dsc) / 2 + s * 0.2), letter, font=f, fill=(20, 18, 10, 255))
        return img.resize((s, s), Image.LANCZOS)

    @classmethod
    def question_end(cls, spec: TemplateSpec) -> float:
        n = len((spec.keyword or "").strip())
        return cls.Q_T0 + n / 26.0

    @classmethod
    def cta_start(cls, spec: TemplateSpec) -> float:
        n = max(2, len([x for x in spec.labels if str(x).strip()][:4]))
        starts = item_starts(spec, n, first=cls.question_end(spec) + 0.2, gap=0.3)
        return min(starts[-1] + 0.6, max(starts[-1], spec.frames / spec.fps - 1.2))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(2, len([x for x in spec.labels if str(x).strip()][:4]))
        q1 = cls.question_end(spec)
        out: list[Cue] = [(0.0, "whoosh_soft", 0.4)]
        if spec.keyword:
            out.append((cls.Q_T0, f"typing:{q1 - cls.Q_T0:.3f}", 0.5))
        out += [(s, "pop", 0.45) for s in item_starts(spec, n, first=q1 + 0.2, gap=0.3)]
        out.append((cls.cta_start(spec), "ding", 0.45))
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=10).copy()
        _sweep(canvas, t, w, h)
        total = len(self.question)
        shown = total if t >= self.q_t1 else int(total * gfx.progress(t, self.Q_T0, self.q_t1 - self.Q_T0))
        left, y = shown, h * 0.1
        for ln in self.q_lines:
            part = ln[:max(0, left)]
            if part:
                layer = self.q_cache.get(part)
                full_w = gfx.text_width(ln, self.f_q)
                gfx.paste_at(canvas, layer, w / 2 - full_w / 2 - 36 * u, y - 36 * u)
            left -= len(ln) + 1
            y += self.f_q.getmetrics()[0] * 1.05
        n = self.n
        total_w = self.cw * n + self.gap * (n - 1)
        x0 = (w - total_w) / 2
        cy = h * 0.52
        for k in range(n):
            q = gfx.progress(t, self.starts[k], 0.4)
            if q <= 0:
                continue
            e = gfx.ease_out_back(q, 1.4)
            cx = x0 + k * (self.cw + self.gap) + self.cw / 2
            if k < len(self.photos):
                if q < 1 or k not in self.frozen:
                    card = _photo_card(self, self.photos[k].frame(i), border=6)
                    if q >= 1:
                        self.frozen[k] = card
                else:
                    card = self.frozen[k]
            else:
                card = Image.new("RGBA", (self.cw, self.ph), (255, 255, 255, 30))
                ImageDraw.Draw(card).rounded_rectangle([0, 0, self.cw - 1, self.ph - 1], radius=round(20 * u),
                                                       outline=(255, 255, 255, 120), width=max(2, round(3 * u)))
            gfx.paste_center(canvas, gfx.scaled(card, max(0.05, 0.85 + 0.15 * e)), cx, cy, alpha=min(1.0, q * 2))
            gfx.paste_center(canvas, self.badges[k], cx - self.cw / 2 + 20 * u, cy - self.ph / 2 + 14 * u,
                             alpha=min(1.0, q * 2))
            lab = self.opt_labels[k]
            if lab is not None:
                gfx.paste_center(canvas, lab, cx, cy + self.ph / 2 + 30 * u + lab.height / 2, alpha=min(1.0, q * 2))
        qc = gfx.progress(t, self.cta_t0, 0.4)
        if qc > 0:
            pulse = 1 + 0.035 * math.sin((t - self.cta_t0) * 6)
            chip = gfx.scaled(self.cta, gfx.ease_out_back(qc, 1.8) * pulse)
            gfx.paste_center(canvas, chip, w / 2, h * 0.9)
            ax = w / 2 + self.cta.width / 2 + 50 * u
            ay = h * 0.9 + math.sin((t - self.cta_t0) * 7) * 10 * u
            s = 22 * u
            ImageDraw.Draw(canvas).polygon([(ax - s, ay - s * 0.8), (ax + s, ay - s * 0.8), (ax, ay + s * 0.8)],
                                           fill=(*gfx.accent(), round(255 * min(1.0, qc * 2))))
        return canvas


PRO_TEMPLATES: dict[str, type[Template]] = {
    "timeline": Timeline, "speedometer": Speedometer, "datasheet": Datasheet, "price": Price,
    "proscons": ProsCons, "score": Score, "magnifier": Magnifier, "beforeafter": BeforeAfter,
    "news": News, "comment": Comment, "poll": Poll,
}


def pro_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls: Any = PRO_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
