"""Templates para qualquer nicho (história, ciência, agro, finanças...), não só carro:

* ``filmstrip``  — prancheta azul-escura com traços brancos; os vídeos da cena passam da direita para a
  esquerda, um de cada vez no centro (como uma esteira), com "FIG. 1", régua e a frase falada sendo
  datilografada embaixo.
* ``opener``     — abertura do vídeo: fundo desfocado, cantoneiras se desenhando, um selo pequeno, uma linha
  que se abre do centro e o título grande em serifa (o assunto do vídeo).
* ``profile``    — quem é a pessoa citada: foto num círculo com anel se desenhando, nome em serifa, função e um
  selo com o feito (ou as iniciais, se não houver foto dela).
* ``yearflash``  — o ano gigante rolando até a data falada ("1909"), com a frase embaixo e o ano fantasma
  enorme ao fundo.
* ``flow``       — processo em etapas (A → B → C): caixas entrando no tempo da fala, setas se desenhando.
* ``mythfact``   — mito x fato: o mito aparece e é riscado; o fato entra com o visto.
* ``definition`` — verbete de dicionário: a palavra em serifa num cartão de papel e o significado digitado.
* ``agenda``     — "neste vídeo": os assuntos que vêm pela frente, numerados (loop aberto, segura a audiência).

Todos seguem a cor de destaque do canal (``gfx.accent()``); o ``filmstrip`` tem visual próprio (blueprint).
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .i18n import text as tr
from .templates import Template, TemplateSpec
from .templates_auto import TextCache, _blur_frame, _blurred_source

Cue = tuple[float, str, float]
WHITE = (246, 248, 255)


def _plain(lines: list[str], fnt, fill: tuple, line_gap: float = 1.12, stroke: int = 0) -> Image.Image:
    """Texto sem a marca do canal (sem contorno preto nem troca de cor): tinta no papel, texto escuro sobre o
    destaque, o ano "fantasma" vazado."""
    lines = [gfx.safe_text(x) for x in lines] or [""]
    asc, desc = fnt.getmetrics()
    lh = int((asc + desc) * line_gap)
    w = max((gfx.text_width(x, fnt) for x in lines), default=1) + 4 + 2 * stroke
    img = Image.new("RGBA", (w + 4, lh * len(lines) + 4 + 2 * stroke), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for k, line in enumerate(lines):
        if stroke:
            d.text((2 + stroke, 2 + stroke + k * lh), line, font=fnt, fill=(0, 0, 0, 0), stroke_width=stroke,
                   stroke_fill=fill)
        else:
            d.text((2, 2 + k * lh), line, font=fnt, fill=fill if len(fill) == 4 else (*fill, 255))
    return img


def _centered(blocks: list[Image.Image], gap: int = 0) -> Image.Image:
    """Linhas de texto empilhadas, cada uma centralizada (título em duas linhas)."""
    w = max(b.width for b in blocks)
    h = sum(b.height for b in blocks) + gap * (len(blocks) - 1)
    img = Image.new("RGBA", (w, max(1, h)), (0, 0, 0, 0))
    y = 0
    for b in blocks:
        img.alpha_composite(b, ((w - b.width) // 2, max(0, y)))
        y += b.height + gap
    return img


def _ease(t: float, start: float, dur: float) -> float:
    return gfx.ease_out_cubic(gfx.progress(t, start, dur))


class _Base(Template):
    """Fundo comum: a mídia da cena desfocada e escurecida, ou a grade do tema andando devagar."""

    DIM = 0.42

    def __init__(self, spec: TemplateSpec, dim: float | None = None) -> None:
        super().__init__(spec)
        self.cell = round(96 * self.u)
        self.dim = self.DIM if dim is None else dim
        self.blur = _blurred_source(self, spec.media[0], zoom=1.12) if spec.media else None
        self.grid = None if self.blur is not None else gfx.background(spec.theme, self.w, self.h, self.cell,
                                                                      extra=self.cell).convert("RGBA")
        self.acc = gfx.accent() if gfx.BRAND.get("on") else self.th["accent"]

    def base(self, i: int, t: float) -> Image.Image:
        if self.blur is not None:
            img = _blur_frame(self.blur.frame(i), self.w, self.h, self.dim)
            return gfx.vignette(img, 0.4).convert("RGBA")
        return gfx.drift(self.grid, t, self.w, self.h, self.cell, speed=6 * self.u).copy()

    def times(self, n: int, start: float = 0.35, step: float = 0.9) -> list[float]:
        """Quando cada item entra: no tempo da fala (``captions`` de ``point_times``) ou em sequência."""
        total = self.s.frames / self.fps
        caps = [c[0] for c in self.s.captions][:n]
        if len(caps) == n and all(b >= a for a, b in zip(caps, caps[1:])):
            return [max(start, min(c, total - 0.8)) for c in caps]
        step = min(step, max(0.35, (total - start - 1.2) / max(1, n)))
        return [start + k * step for k in range(n)]


# ------------------------------------------------------------------ filmstrip
class Filmstrip(Template):
    SLIDE = 0.7

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(108 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        media = list(spec.media[:3]) or []
        self.cw = round(w * 0.5)
        self.ch = round(self.cw * 9 / 16)
        self.border = max(3, round(5 * u))
        self.cards = [self.src(m, self.cw - 2 * self.border, self.ch - 2 * self.border, zoom=1.06,
                               pan=(float(self.rng.uniform(-1, 1)), 0.0)) for m in media]
        self.n = max(1, len(self.cards))
        total = spec.frames / self.fps
        self.slots = [total * k / self.n for k in range(self.n)]
        self.spacing = self.cw + w * 0.09
        self.cy = h * 0.42
        self.f_fig = gfx.font("semibold", round(24 * u))
        self.figs = [gfx.text_block([f"FIG. {k + 1}"], self.f_fig, WHITE) for k in range(self.n)]
        self.f_cap = gfx.font("semibold", round(50 * u))
        self.typed = TextCache(self.f_cap, WHITE, shadow=2)
        self.cap_lines = [gfx.wrap(txt, self.f_cap, round(w * 0.78), 2) if txt and spec.show_captions else []
                          for _, _, txt in spec.captions]
        kw = (spec.keyword or "").upper()
        self.title = gfx.text_block([kw], gfx.font("semibold", round(38 * u)), WHITE) if kw else None
        self.acc = gfx.accent()

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(3, len(spec.media)))
        total = spec.frames / spec.fps
        out: list[Cue] = [(total * k / n, "whoosh", 0.4) for k in range(n)]
        for t0, _t1, txt in spec.captions[:4]:
            if txt:
                out.append((max(0.2, t0) + 0.1, f"typing:{min(1.4, 0.035 * len(txt)):.2f}", 0.3))
        return out

    def _pos(self, t: float) -> float:
        """Índice "contínuo" do cartão no centro: anda um inteiro a cada troca (com aceleração suave)."""
        p = -1.15 + 1.15 * gfx.ease_out_cubic(gfx.progress(t, 0.0, self.SLIDE))
        for k in range(1, self.n):
            p += gfx.ease_in_out(gfx.progress(t, self.slots[k] - self.SLIDE / 2, self.SLIDE))
        return p

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=8 * u).copy()
        d = ImageDraw.Draw(canvas)
        p = self._pos(t)
        drift = -t * 6 * u  # esteira sempre andando um pouco
        # régua de prancheta sob os cartões
        ry = self.cy + self.ch / 2 + 46 * u
        d.line([(0, ry), (w, ry)], fill=(*WHITE, 120), width=max(1, round(2 * u)))
        step = 40 * u
        off = (t * 30 * u) % step
        for k in range(int(w / step) + 2):
            x = k * step - off
            tall = 14 * u if k % 5 == 0 else 7 * u
            d.line([(x, ry), (x, ry + tall)], fill=(*WHITE, 110), width=max(1, round(1.5 * u)))
        for k, src in enumerate(self.cards):
            dist = k - p
            if abs(dist) > 1.6:
                continue
            x = w / 2 + dist * self.spacing + drift
            near = max(0.0, 1 - min(1.0, abs(dist)))
            photo = gfx.bordered(src.frame(i), self.border, (250, 250, 255))
            card = gfx.with_shadow(photo, 0.0, round(18 * u), (round(8 * u), round(16 * u)), 160)
            card = gfx.scaled(card, 0.82 + 0.18 * near)
            alpha = 0.35 + 0.65 * near
            gfx.paste_center(canvas, card, x, self.cy, alpha=alpha)
            # marca de figura e cantoneiras (desenho técnico)
            fx = x - self.cw / 2 * (0.82 + 0.18 * near)
            fy = self.cy - self.ch / 2 * (0.82 + 0.18 * near) - 40 * u
            gfx.paste_at(canvas, self.figs[k], fx, fy, alpha=alpha * 0.9)
            if near > 0.6:
                L = 30 * u
                hw, hh = self.cw / 2 + 14 * u, self.ch / 2 + 14 * u
                col = (*self.acc, round(255 * (near - 0.6) / 0.4))
                lw = max(2, round(4 * u))
                for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    cx, cy = x + sx * hw, self.cy + sy * hh
                    d.line([(cx, cy), (cx - sx * L, cy)], fill=col, width=lw)
                    d.line([(cx, cy), (cx, cy - sy * L)], fill=col, width=lw)
        if self.title is not None:
            q = _ease(t, 0.2, 0.4)
            d.rectangle([56 * u, 56 * u, 62 * u, 56 * u + self.title.height - 8 * u], fill=(*self.acc, round(255 * q)))
            gfx.paste_at(canvas, self.title, 74 * u - (1 - q) * 20 * u, 52 * u, alpha=q)
        idx, _ = self.caption_at(t)
        if 0 <= idx < len(self.cap_lines) and self.cap_lines[idx]:
            t0 = max(0.25, self.s.captions[idx][0])
            full = " ".join(self.cap_lines[idx])
            n = int(len(full) * gfx.clamp01((t - t0) / max(0.4, 0.035 * len(full))))
            if n > 0:
                shown = full[:n]
                lines = gfx.wrap(shown, self.f_cap, round(w * 0.78), 2)
                y = h * 0.8
                for k, line in enumerate(lines):
                    gfx.paste_center(canvas, self.typed.get(line), w / 2, y + k * 58 * u)
                if n < len(full) and int(t * 4) % 2 == 0:  # cursor piscando
                    last = lines[-1] if lines else ""
                    cx = w / 2 + gfx.text_width(last, self.f_cap) / 2 + 8 * u
                    cy = y + (len(lines) - 1) * 58 * u
                    d.rectangle([cx, cy - 22 * u, cx + 4 * u, cy + 22 * u], fill=(*self.acc, 255))
        return canvas


# --------------------------------------------------------------------- opener
class Opener(_Base):
    DIM = 0.4

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        f_title = gfx.font("serif", round(104 * u))
        title = spec.keyword or ""
        lines = gfx.wrap(title, f_title, round(w * 0.8), 2) if title else []
        self.title = _centered([gfx.text_block([ln], f_title, WHITE, shadow=3) for ln in lines], round(-12 * u)) \
            if lines else None
        kicker = (spec.subtitle or "").upper()
        self.f_kick = gfx.font("semibold", round(26 * u))
        self.kicker = kicker
        self.kick_w = gfx.spaced_width(kicker, self.f_kick, 0.32) if kicker else 0
        self.zoom_src = self.blur

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh_soft", 0.4), (0.35, "riser", 0.25), (0.75, "impact_soft", 0.45)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base(i, t)
        d = ImageDraw.Draw(canvas)
        # cantoneiras se desenhando
        q = _ease(t, 0.05, 0.6)
        m, L, lw = 70 * u, 90 * u * q, max(2, round(3 * u))
        col = (*WHITE, round(220 * q))
        for sx, sy, x0, y0 in ((1, 1, m, m), (-1, 1, w - m, m), (1, -1, m, h - m), (-1, -1, w - m, h - m)):
            d.line([(x0, y0), (x0 + sx * L, y0)], fill=col, width=lw)
            d.line([(x0, y0), (x0, y0 + sy * L)], fill=col, width=lw)
        cy = h * 0.5
        title_h = self.title.height if self.title is not None else 0
        # selo pequeno (espaçado) acima
        if self.kicker:
            qk = _ease(t, 0.25, 0.5)
            gfx.draw_spaced(d, (w / 2 - self.kick_w / 2, cy - title_h / 2 - 92 * u + (1 - qk) * 10 * u), self.kicker,
                            self.f_kick, (*self.acc, round(255 * qk)), 0.32)
        # linha que se abre do centro, com um losango no meio
        ql = gfx.ease_in_out(gfx.progress(t, 0.4, 0.7))
        ly = cy - title_h / 2 - 34 * u
        half = w * 0.2 * ql
        if half > 1:
            d.line([(w / 2 - half, ly), (w / 2 - 14 * u, ly)], fill=(*WHITE, 200), width=max(1, round(2 * u)))
            d.line([(w / 2 + 14 * u, ly), (w / 2 + half, ly)], fill=(*WHITE, 200), width=max(1, round(2 * u)))
            r = 7 * u * ql
            d.polygon([(w / 2, ly - r), (w / 2 + r, ly), (w / 2, ly + r), (w / 2 - r, ly)], fill=(*self.acc, 255))
        if self.title is not None:
            qt = _ease(t, 0.7, 0.7)
            layer = gfx.scaled(self.title, 1.06 - 0.06 * qt)
            gfx.paste_center(canvas, layer, w / 2, cy + (1 - qt) * 24 * u, alpha=qt)
        return canvas


# --------------------------------------------------------------------- profile
class Profile(_Base):
    DIM = 0.3

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.r = round(h * 0.2)
        self.photo = self.src(spec.media[0], 2 * self.r, 2 * self.r, zoom=1.05) if spec.media else None
        mask = Image.new("L", (2 * self.r, 2 * self.r), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 2 * self.r - 1, 2 * self.r - 1], fill=255)
        self.mask = mask
        name = spec.keyword or ""
        initials = "".join(p[0] for p in name.split()[:2]).upper()
        self.initials = gfx.text_block([initials], gfx.font("serif", round(self.r * 0.8)), WHITE) if initials else None
        f_name = gfx.font("serif", round(78 * u))
        self.name = gfx.text_block(gfx.wrap(name, f_name, round(w * 0.46), 2), f_name, WHITE, shadow=3,
                                   line_gap=1.02) if name else None
        f_role = gfx.font("semibold", round(34 * u))
        self.role = gfx.text_block(gfx.wrap(spec.subtitle, f_role, round(w * 0.44), 2), f_role, (205, 212, 226),
                                   shadow=2) if spec.subtitle else None
        body = (spec.body or "").strip()
        self.badge = gfx.chip(body, gfx.font("bold", round(28 * u)), self.acc, (14, 16, 22), round(w * 0.42),
                              round(16 * u), round(9 * u)) if body else None
        self.cx, self.cy = w * 0.3, h * 0.5

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.05, "whoosh_soft", 0.4), (0.3, "pop", 0.45), (0.75, "swipe", 0.3), (1.25, "pop", 0.35)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base(i, t)
        q = gfx.ease_out_back(gfx.progress(t, 0.1, 0.55), 1.4)
        r = self.r
        if q > 0:
            disc = Image.new("RGBA", (2 * r, 2 * r), (24, 28, 40, 255))
            if self.photo is not None:
                disc = self.photo.frame(i).convert("RGBA")
            elif self.initials is not None:
                gfx.paste_center(disc, self.initials, r, r)
            disc.putalpha(self.mask)
            layer = Image.new("RGBA", (2 * r + 40, 2 * r + 40), (0, 0, 0, 0))
            layer.alpha_composite(disc, (20, 20))
            dl = ImageDraw.Draw(layer)
            dl.ellipse([16, 16, 2 * r + 23, 2 * r + 23], outline=(*WHITE, 230), width=max(2, round(4 * u)))
            sweep = 360 * _ease(t, 0.45, 0.9)
            if sweep > 1:
                dl.arc([4, 4, 2 * r + 35, 2 * r + 35], -90, -90 + sweep, fill=(*self.acc, 255),
                       width=max(3, round(7 * u)))
            gfx.paste_center(canvas, gfx.scaled(layer, 0.7 + 0.3 * min(1.2, q)), self.cx, self.cy,
                             alpha=min(1.0, q * 1.6))
        x = w * 0.5
        top = self.cy - ((self.name.height if self.name else 0) + (self.role.height if self.role else 0)) / 2 - 20 * u
        if self.name is not None:
            qn = _ease(t, 0.55, 0.5)
            gfx.paste_at(canvas, self.name, x + (1 - qn) * 60 * u, top, alpha=qn)
            qb = _ease(t, 0.7, 0.5)
            ImageDraw.Draw(canvas).rectangle([x + 6 * u, top + self.name.height + 2 * u,
                                              x + 6 * u + 180 * u * qb, top + self.name.height + 8 * u],
                                             fill=(*self.acc, 255))
            top += self.name.height + 22 * u
        if self.role is not None:
            gfx.paste_at(canvas, self.role, x, top, alpha=_ease(t, 0.85, 0.45))
            top += self.role.height + 12 * u
        if self.badge is not None:
            qc = gfx.ease_out_back(gfx.progress(t, 1.2, 0.45), 1.5)
            if qc > 0:
                gfx.paste_at(canvas, gfx.scaled(self.badge, 0.8 + 0.2 * min(1.0, qc)), x + 4 * u, top + 6 * u,
                             alpha=min(1.0, qc * 1.5))
        return canvas


# -------------------------------------------------------------------- yearflash
class YearFlash(_Base):
    DIM = 0.32
    ROLL = 1.0

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        self.year = int(float((spec.number or {}).get("value") or 0))
        self.start_year = self.year - min(60, max(12, self.year % 100 + 10))
        self.digits = TextCache(gfx.font("black", round(250 * u)), WHITE, shadow=4)
        self.ghost = _plain([str(self.year)], gfx.font("black", round(560 * u)), (*WHITE, 55), stroke=max(2, round(3 * u)))
        f_cap = gfx.font("semibold", round(42 * u))
        cap = spec.subtitle or spec.keyword or ""
        self.cap = gfx.text_block(gfx.wrap(cap, f_cap, round(w * 0.7), 2), f_cap, WHITE, shadow=2) if cap else None

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.05, "whoosh_soft", 0.4)] + [(0.12 + 0.08 * k, "tick", 0.22) for k in range(11)] + \
            [(cls.ROLL + 0.08, "impact_soft", 0.5)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base(i, t)
        gfx.paste_center(canvas, self.ghost, w / 2 - t * 18 * u, h * 0.5, alpha=0.9)
        q = gfx.ease_out_cubic(gfx.progress(t, 0.08, self.ROLL))
        shown = round(self.start_year + (self.year - self.start_year) * q)
        pop = 1 + 0.06 * math.sin(math.pi * gfx.progress(t, self.ROLL + 0.05, 0.3))
        layer = gfx.scaled(self.digits.get(str(shown)), pop)
        gfx.paste_center(canvas, layer, w / 2, h * 0.44, alpha=_ease(t, 0.0, 0.25))
        ql = gfx.ease_in_out(gfx.progress(t, self.ROLL, 0.5))
        y = h * 0.44 + 150 * u
        if ql > 0:
            ImageDraw.Draw(canvas).rectangle([w / 2 - 150 * u * ql, y, w / 2 + 150 * u * ql, y + 6 * u],
                                             fill=(*self.acc, 255))
        if self.cap is not None:
            qc = _ease(t, self.ROLL + 0.2, 0.45)
            gfx.paste_center(canvas, self.cap, w / 2, y + 70 * u + (1 - qc) * 14 * u, alpha=qc)
        return canvas


# ------------------------------------------------------------------------ flow
class Flow(_Base):
    DIM = 0.28

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        texts = [str(p.get("text", "")) for p in spec.points if str(p.get("text", "")).strip()][:5]
        self.n = max(2, len(texts))
        gap = 70 * u
        self.bw = min(w * 0.24, (w * 0.88 - gap * (self.n - 1)) / self.n)
        f = gfx.font("bold", round((48 if self.n <= 3 else 40) * u))
        self.boxes = []
        for k, txt in enumerate(texts):
            lines = gfx.wrap(txt, f, round(self.bw - 36 * u), 3)
            last = k == len(texts) - 1
            self.boxes.append(_plain(lines, f, (14, 16, 22)) if last else gfx.text_block(lines, f, WHITE))
        self.bh = max(max((b.height for b in self.boxes), default=60) + 56 * u, h * 0.2)
        x0 = w / 2 - (self.n * self.bw + (self.n - 1) * gap) / 2
        self.xs = [x0 + k * (self.bw + gap) for k in range(self.n)]
        self.cy = h * 0.54
        self.starts = self.times(len(self.boxes), 0.35, 0.85)
        kw = (spec.keyword or "").upper()
        self.title = gfx.text_block([kw], gfx.font("semibold", round(40 * u)), WHITE) if kw else None

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(2, min(5, len(spec.points)))
        total = spec.frames / spec.fps
        starts = [c[0] for c in spec.captions][:n] if len(spec.captions) >= n else \
            [0.35 + k * min(0.85, max(0.35, (total - 1.55) / n)) for k in range(n)]
        return [(max(0.0, s), "pop", 0.4) for s in starts] + [(max(0.0, s) - 0.25, "whoosh_soft", 0.25)
                                                               for s in starts[1:]]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base(i, t)
        d = ImageDraw.Draw(canvas)
        if self.title is not None:
            q = _ease(t, 0.1, 0.4)
            d.rectangle([w * 0.06, h * 0.2, w * 0.06 + 6 * u, h * 0.2 + self.title.height - 8 * u],
                        fill=(*self.acc, round(255 * q)))
            gfx.paste_at(canvas, self.title, w * 0.06 + 18 * u, h * 0.2 - 4 * u, alpha=q)
        for k, box in enumerate(self.boxes):
            s0 = self.starts[k]
            q = gfx.ease_out_back(gfx.progress(t, s0, 0.45), 1.4)
            if q <= 0:
                continue
            x, y0 = self.xs[k], self.cy - self.bh / 2
            last = k == len(self.boxes) - 1
            # seta da caixa anterior até esta (desenhada antes da caixa entrar)
            if k > 0:
                qa = gfx.ease_in_out(gfx.progress(t, s0 - 0.3, 0.35))
                ax0 = self.xs[k - 1] + self.bw + 10 * u
                ax1 = ax0 + (x - 20 * u - ax0) * qa
                d.line([(ax0, self.cy), (ax1, self.cy)], fill=(*WHITE, 230), width=max(2, round(4 * u)))
                if qa > 0.95:
                    d.polygon([(ax1 + 12 * u, self.cy), (ax1 - 4 * u, self.cy - 11 * u), (ax1 - 4 * u, self.cy + 11 * u)],
                              fill=(*WHITE, 230))
            a = round(255 * min(1.0, q * 1.5))
            sc = 0.8 + 0.2 * min(1.0, q)
            bw, bh = self.bw * sc, self.bh * sc
            cx = x + self.bw / 2
            rect = [cx - bw / 2, self.cy - bh / 2, cx + bw / 2, self.cy + bh / 2]
            if last:
                d.rounded_rectangle(rect, radius=round(14 * u), fill=(*self.acc, a))
            else:
                d.rounded_rectangle(rect, radius=round(14 * u), fill=(10, 14, 26, round(a * 0.78)),
                                    outline=(*WHITE, a), width=max(2, round(3 * u)))
            gfx.paste_center(canvas, gfx.scaled(box, sc), cx, self.cy, alpha=min(1.0, q * 1.5))
            num = gfx.text_block([f"{k + 1:02d}"], gfx.font("serif", round(40 * u)), self.acc)
            gfx.paste_center(canvas, num, cx, y0 - 40 * u, alpha=min(1.0, q))
        return canvas


# --------------------------------------------------------------------- mythfact
MYTH_RED = (232, 84, 84)
FACT_GREEN = (88, 206, 132)


class MythFact(_Base):
    DIM = 0.26

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        myth = next((str(p.get("text", "")) for p in spec.points if not p.get("positive", True)), "")
        fact = next((str(p.get("text", "")) for p in spec.points if p.get("positive", True)), "")
        f = gfx.font("bold", round(56 * u))
        self.pw = round(w * 0.38)
        self.myth = gfx.text_block(gfx.wrap(myth, f, self.pw, 4), f, WHITE, shadow=2)
        self.fact = gfx.text_block(gfx.wrap(fact, f, self.pw, 4), f, WHITE, shadow=2)
        self.myth_lines = gfx.wrap(myth, f, self.pw, 4)
        self.font = f
        lang = spec.lang
        fl = gfx.font("black", round(54 * u))
        self.lab_myth = gfx.chip(tr("myth", lang), fl, MYTH_RED, (255, 255, 255), round(w * 0.3), round(18 * u),
                                 round(8 * u))
        self.lab_fact = gfx.chip(tr("fact", lang), fl, FACT_GREEN, (10, 24, 16), round(w * 0.3), round(18 * u),
                                 round(8 * u))
        total = spec.frames / self.fps
        caps = [c[0] for c in spec.captions]
        self.t_strike = caps[1] - 0.2 if len(caps) >= 2 and caps[1] > 1.2 else max(1.3, total * 0.42)
        self.t_fact = self.t_strike + 0.45

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        total = spec.frames / spec.fps
        caps = [c[0] for c in spec.captions]
        strike = caps[1] - 0.2 if len(caps) >= 2 and caps[1] > 1.2 else max(1.3, total * 0.42)
        return [(0.1, "pop", 0.4), (strike, "swipe", 0.4), (strike + 0.45, "impact_soft", 0.45),
                (strike + 0.9, "ding", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base(i, t)
        d = ImageDraw.Draw(canvas)
        xl, xr = w * 0.27, w * 0.73
        cy = h * 0.54
        # divisória
        qd = _ease(t, 0.05, 0.5)
        d.line([(w / 2, cy - h * 0.28 * qd), (w / 2, cy + h * 0.28 * qd)], fill=(*WHITE, 90), width=max(1, round(2 * u)))
        qm = _ease(t, 0.1, 0.45)
        gfx.paste_center(canvas, self.lab_myth, xl, h * 0.24, alpha=qm)
        dim = 1.0 - 0.45 * _ease(t, self.t_strike + 0.3, 0.4)
        gfx.paste_center(canvas, self.myth, xl, cy + (1 - qm) * 20 * u, alpha=qm * dim)
        qs = gfx.ease_in_out(gfx.progress(t, self.t_strike, 0.45))
        if qs > 0:
            asc, desc = self.font.getmetrics()
            lh = (asc + desc) * 1.12
            top = cy - self.myth.height / 2 + 8 * u
            for k, line in enumerate(self.myth_lines):
                lw_ = gfx.text_width(line, self.font)
                part = max(0.0, min(1.0, qs * len(self.myth_lines) - k))
                if part <= 0:
                    continue
                y = top + k * lh + lh * 0.52
                x0 = xl - lw_ / 2
                d.line([(x0, y), (x0 + lw_ * part, y)], fill=(*MYTH_RED, 255), width=max(3, round(6 * u)))
        qf = gfx.ease_out_back(gfx.progress(t, self.t_fact, 0.45), 1.4)
        if qf > 0:
            gfx.paste_center(canvas, gfx.scaled(self.lab_fact, 0.8 + 0.2 * min(1.0, qf)), xr, h * 0.24,
                             alpha=min(1.0, qf * 1.5))
            qt = _ease(t, self.t_fact + 0.15, 0.45)
            gfx.paste_center(canvas, self.fact, xr, cy + (1 - qt) * 20 * u, alpha=qt)
            qc = gfx.progress(t, self.t_fact + 0.5, 0.35)
            if qc > 0:  # visto se desenhando
                cx, cyc, s = xr, cy + self.fact.height / 2 + 70 * u, 34 * u
                p1, p2, p3 = (cx - s, cyc), (cx - s * 0.3, cyc + s * 0.7), (cx + s * 1.1, cyc - s * 0.8)
                lw = max(4, round(8 * u))
                if qc < 0.4:
                    k = qc / 0.4
                    d.line([p1, (p1[0] + (p2[0] - p1[0]) * k, p1[1] + (p2[1] - p1[1]) * k)], fill=(*FACT_GREEN, 255), width=lw)
                else:
                    k = (qc - 0.4) / 0.6
                    d.line([p1, p2], fill=(*FACT_GREEN, 255), width=lw)
                    d.line([p2, (p2[0] + (p3[0] - p2[0]) * k, p2[1] + (p3[1] - p2[1]) * k)], fill=(*FACT_GREEN, 255), width=lw)
        return canvas


class _PlainCache:
    def __init__(self, fnt, fill) -> None:
        self.fnt, self.fill, self.cache = fnt, fill, {}

    def get(self, text: str) -> Image.Image:
        img = self.cache.get(text)
        if img is None:
            img = self.cache[text] = _plain([text], self.fnt, self.fill)
        return img


# ------------------------------------------------------------------- definition
class Definition(_Base):
    DIM = 0.3

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cw, self.chh = round(w * 0.56), round(h * 0.5)
        ink = (34, 30, 26)
        f_term = gfx.font("serif", round(84 * u))
        self.term = _plain(gfx.wrap(spec.keyword or "", f_term, self.cw - round(110 * u), 2), f_term, ink)
        self.f_body = gfx.font("serif_italic", round(44 * u))
        self.body_lines = gfx.wrap(spec.body or "", self.f_body, self.cw - round(110 * u), 4)
        self.typed = _PlainCache(self.f_body, (70, 62, 54))
        self.label = tr("definition", spec.lang)
        self.f_lab = gfx.font("bold", round(28 * u))
        paper = Image.new("RGBA", (self.cw, self.chh), (244, 239, 228, 255))
        dp = ImageDraw.Draw(paper)
        dp.rectangle([0, 0, self.cw - 1, self.chh - 1], outline=(220, 212, 196, 255), width=max(1, round(2 * u)))
        noise = np.random.default_rng(5).normal(0, 4, (self.chh, self.cw, 1)).astype(np.float32)
        arr = np.asarray(paper, np.float32)
        arr[..., :3] = (arr[..., :3] + noise).clip(0, 255)
        self.paper = Image.fromarray(arr.astype(np.uint8), "RGBA")
        self.card = gfx.with_shadow(self.paper, -1.2, round(24 * u), (round(10 * u), round(22 * u)), 150)
        total = len(" ".join(self.body_lines))
        self.type_dur = max(0.8, min(2.6, 0.04 * total))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len(spec.body or "")
        return [(0.05, "whoosh_soft", 0.4), (0.35, "pop", 0.35),
                (0.9, f"typing:{max(0.8, min(2.6, 0.04 * n)):.2f}", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base(i, t)
        q = gfx.ease_out_back(gfx.progress(t, 0.05, 0.55), 1.3)
        if q <= 0:
            return canvas
        cy = h * 0.5 + (1 - min(1.0, q)) * 80 * u
        gfx.paste_center(canvas, gfx.scaled(self.card, 0.9 + 0.1 * min(1.0, q)), w / 2, cy, alpha=min(1.0, q * 1.6))
        x0, y0 = w / 2 - self.cw / 2 + 55 * u, cy - self.chh / 2 + 44 * u
        d = ImageDraw.Draw(canvas)
        ql = _ease(t, 0.3, 0.4)
        gfx.draw_spaced(d, (x0, y0), self.label.upper(), self.f_lab, (168, 54, 40, round(255 * ql)), 0.3)
        gfx.paste_at(canvas, self.term, x0 - 4 * u, y0 + 30 * u, alpha=_ease(t, 0.35, 0.4))
        ry = y0 + 40 * u + self.term.height
        qr = gfx.ease_in_out(gfx.progress(t, 0.6, 0.45))
        d.line([(x0, ry), (x0 + (self.cw - 110 * u) * qr, ry)], fill=(150, 138, 120, 255), width=max(1, round(2 * u)))
        full = " ".join(self.body_lines)
        n = int(len(full) * gfx.clamp01((t - 0.9) / self.type_dur))
        if n > 0:
            shown = gfx.wrap(full[:n], self.f_body, self.cw - round(110 * u), 4)
            asc, desc = self.f_body.getmetrics()
            for k, line in enumerate(shown):
                gfx.paste_at(canvas, self.typed.get(line), x0, ry + 22 * u + k * (asc + desc) * 1.15)
        return canvas


# ----------------------------------------------------------------------- agenda
class Agenda(_Base):
    DIM = 0.3

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        texts = [str(p.get("text", "")) for p in spec.points if str(p.get("text", "")).strip()][:4]
        f = gfx.font("bold", round(58 * u))
        self.items = [gfx.text_block(gfx.wrap(txt, f, round(w * 0.62), 2), f, WHITE, shadow=2) for txt in texts]
        self.nums = [gfx.text_block([f"{k + 1:02d}"], gfx.font("serif", round(68 * u)), self.acc)
                     for k in range(len(texts))]
        self.head = tr("in_this_video", spec.lang).upper()
        self.f_head = gfx.font("bold", round(34 * u))
        self.starts = self.times(len(self.items), 0.55, 0.8)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(4, len(spec.points)))
        total = spec.frames / spec.fps
        starts = [c[0] for c in spec.captions][:n] if len(spec.captions) >= n else \
            [0.55 + k * min(0.8, max(0.35, (total - 1.75) / n)) for k in range(n)]
        return [(0.05, "whoosh_soft", 0.4)] + [(max(0.0, s), "pop", 0.38) for s in starts]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base(i, t)
        d = ImageDraw.Draw(canvas)
        x0 = w * 0.16
        qh = _ease(t, 0.1, 0.45)
        gfx.draw_spaced(d, (x0, h * 0.17), self.head, self.f_head, (*self.acc, round(255 * qh)), 0.3)
        d.line([(x0, h * 0.17 + 48 * u), (x0 + w * 0.3 * qh, h * 0.17 + 48 * u)], fill=(*WHITE, 150),
               width=max(1, round(2 * u)))
        total_h = sum(b.height for b in self.items) + 40 * u * max(0, len(self.items) - 1)
        y = max(h * 0.3, h * 0.58 - total_h / 2)
        for k, (item, num) in enumerate(zip(self.items, self.nums)):
            q = _ease(t, self.starts[k], 0.45)
            if q > 0:
                gfx.paste_at(canvas, num, x0 - (1 - q) * 30 * u, y - 6 * u, alpha=q)
                gfx.paste_at(canvas, item, x0 + 130 * u + (1 - q) * 50 * u, y, alpha=q)
            y += item.height + 40 * u
        return canvas


PLUS_TEMPLATES: dict[str, type[Template]] = {
    "filmstrip": Filmstrip, "opener": Opener, "profile": Profile, "yearflash": YearFlash, "flow": Flow,
    "mythfact": MythFact, "definition": Definition, "agenda": Agenda,
}


def plus_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = PLUS_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
