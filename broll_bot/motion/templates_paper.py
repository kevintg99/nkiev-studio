"""Família Dossiê (papel): o estilo "Dossiê" do app troca os recursos por estes, sempre com o texto da fala.

* ``paperfloat``     — a foto do carro numa polaroide flutuando no centro, presa com fita adesiva, sobre a
  própria imagem desfocada e lavada de bege; o nome datilografado na borda da foto e, se houver, a frase
  numa tira de papel. No lugar da foto no blueprint, do desenho e do card.
* ``paperpair``      — duas polaroides do mesmo carro presas com alfinete vermelho e ligadas por uma linha
  vermelha (quadro de investigação). No lugar da galeria e do mosaico.
* ``papertimeline``  — linha do tempo no papel quadriculado: traço de caneta, anos datilografados e a foto
  de cada geração presa com alfinete quando o ano é falado. No lugar da linha do tempo.
* ``papercompare``   — comparativo: 2–3 polaroides com o nome datilografado e o carimbo vermelho "VS".
  No lugar do comparativo e do versus.
* ``paperstatement`` — a frase de impacto datilografada no papel e o círculo de caneta vermelha em volta da
  palavra forte. No lugar da tela preta.
* ``paperrank``      — posição do ranking: o carimbo vermelho "Nº 10" batendo no papel, a polaroide do carro
  presa com fita e o nome datilografado. No lugar do cartão de ranking.

Paleta fixa do dossiê (bege, tinta marrom-escura, vermelho), como o template ``dossier``.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from . import gfx
from .i18n import text as tr
from .templates import Template, TemplateSpec
from .templates_auto import _blurred_source
from .templates_pro import item_starts

Cue = tuple[float, str, float]
PAPER = (233, 226, 211)
INK = (52, 44, 38)
RED = (196, 46, 34)
PRINT = (250, 248, 242)
PUNCT = ".,!?…:;«»\"'"


# ------------------------------------------------------------------ peças
def paper_background(tpl: Template) -> Image.Image:
    cell = round(64 * tpl.u)
    return gfx.background("dossier", tpl.w, tpl.h, cell).convert("RGBA")


def washed(frame: Image.Image, w: int, h: int, strength: float = 0.58) -> Image.Image:
    """Imagem desfocada "lavada" de bege (fica na paleta do dossiê, mas ainda dá para ver o carro)."""
    img = gfx.cover(frame.convert("RGB"), w, h)
    arr = np.asarray(img, np.float32)
    gray = arr.mean(axis=2, keepdims=True)
    sepia = gray * np.array([1.0, 0.93, 0.8], np.float32)
    out = sepia * (1 - strength) + np.array(PAPER, np.float32) * strength
    return gfx.vignette(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), 0.3).convert("RGBA")


class _WashedBackground:
    def __init__(self, tpl: Template, ref) -> None:
        self.tpl = tpl
        self.src = _blurred_source(tpl, ref, zoom=1.12) if ref is not None else None
        self.paper = paper_background(tpl) if self.src is None else None
        self.cache: tuple[int, Image.Image] | None = None

    def frame(self, i: int) -> Image.Image:
        if self.src is None:
            return self.paper.copy()
        if self.cache is None or i - self.cache[0] >= 3:  # o fundo desfocado não precisa mudar a cada quadro
            self.cache = (i, washed(self.src.frame(i), self.tpl.w, self.tpl.h))
        return self.cache[1].copy()


def _spaced_wrap(text: str, f, max_w: float, max_lines: int) -> list[str]:
    """Quebra medindo com o espaçamento da máquina de escrever; o que não couber em ``max_lines`` sai com "…"."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and gfx.spaced_width(trial, f, 0.06) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        head = lines[:max_lines]
        last = head[-1]
        while last and gfx.spaced_width(last + "…", f, 0.06) > max_w:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        head[-1] = last + "…"
        return head if max_lines > 1 else head + ["…"]  # 1 linha só não basta: sinaliza que precisa de 2
    return lines


def polaroid(content: Image.Image, u: float, caption: str = "", chars: int | None = None,
             font=None) -> Image.Image:
    """Foto com borda branca de polaroide (mais grossa embaixo) e a legenda datilografada nela. Legenda longa
    ("The gas engine starts even when the battery is full") vai em duas linhas menores em vez de ser cortada."""
    side = round(16 * u)
    w = content.width + 2 * side
    lines: list[str] = []
    f = font
    if caption:
        f = font or gfx.font("typewriter", round(40 * u))
        lines = _spaced_wrap(caption.upper(), f, w - 2 * side, 1)
        if len(lines) > 1:
            f = font or gfx.font("typewriter", round(30 * u))
            lines = _spaced_wrap(caption.upper(), f, w - 2 * side, 2)
    asc, desc = f.getmetrics() if f else (0, 0)
    line_h = asc + desc
    bottom = round(16 * u) if not lines else max(round(74 * u), round(len(lines) * line_h + 26 * u))
    h = content.height + side + bottom
    img = Image.new("RGBA", (w, h), (*PRINT, 255))
    img.paste(content.convert("RGB"), (side, side))
    left = sum(len(t) for t in lines) if chars is None else max(0, chars)
    y = h - bottom + (bottom - len(lines) * line_h) / 2
    for text in lines:
        n = max(0, min(len(text), left))
        left -= len(text)
        tw = gfx.spaced_width(text, f, 0.06)
        gfx.draw_spaced(ImageDraw.Draw(img), ((w - tw) / 2, y), text, f, (*INK, 255), tracking=0.06, chars=n,
                        bold=max(1, round(1.2 * u)))
        y += line_h
    return img


def tape(u: float, width: float = 170, angle: float = 0.0, alpha: float = 1.0) -> Image.Image:
    img = Image.new("RGBA", (round(width * u), round(46 * u)), (226, 186, 84, round(200 * alpha)))
    d = ImageDraw.Draw(img)
    for x in range(0, img.width, max(2, round(9 * u))):  # bordas serrilhadas
        d.polygon([(x, 0), (x + 4 * u, 0), (x + 2 * u, 4 * u)], fill=(0, 0, 0, 0))
        d.polygon([(x, img.height), (x + 4 * u, img.height), (x + 2 * u, img.height - 4 * u)], fill=(0, 0, 0, 0))
    return img.rotate(angle, expand=True, resample=Image.BICUBIC)


def pin(u: float) -> Image.Image:
    r = round(15 * u)
    img = Image.new("RGBA", (4 * r, 4 * r), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = 2 * r
    d.ellipse([c - r + 4 * u, c - r + 6 * u, c + r + 4 * u, c + r + 6 * u], fill=(0, 0, 0, 70))  # sombra
    d.ellipse([c - r, c - r, c + r, c + r], fill=(*RED, 255))
    d.ellipse([c - r * 0.45, c - r * 0.55, c - r * 0.05, c - r * 0.15], fill=(255, 210, 200, 200))
    return img


def red_string(d: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], q: float, u: float) -> None:
    """Linha vermelha esticada entre dois alfinetes, desenhando de ``a`` até ``b`` (com uma leve barriga)."""
    if q <= 0:
        return
    pts = []
    steps = 24
    for k in range(int(steps * q) + 1):
        f = k / steps
        x = a[0] + (b[0] - a[0]) * f
        y = a[1] + (b[1] - a[1]) * f + math.sin(math.pi * f) * 26 * u
        pts.append((x, y))
    if len(pts) > 1:
        d.line(pts, fill=(*RED, 235), width=max(2, round(4 * u)), joint="curve")


def stamp(text: str, u: float, size: float = 120) -> Image.Image:
    """Carimbo vermelho com moldura e falhas de tinta."""
    f = gfx.font("oswald_bold", round(size * u))
    tw = gfx.text_width(text, f)
    asc, desc = f.getmetrics()
    pad = round(26 * u)
    img = Image.new("RGBA", (tw + 2 * pad, asc + desc + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(4, round(9 * u))
    d.rounded_rectangle([lw / 2, lw / 2, img.width - lw / 2, img.height - lw / 2], radius=round(14 * u),
                        outline=(*RED, 235), width=lw)
    d.text((pad, pad - desc * 0.4), text, font=f, fill=(*RED, 235))
    rng = np.random.default_rng(len(text) * 13 + round(size))
    alpha = np.asarray(img.getchannel("A"), np.float32)
    holes = rng.random(alpha.shape) < 0.12
    alpha[holes] *= 0.35
    img.putalpha(Image.fromarray(alpha.astype(np.uint8)))
    return img


def strip(text: str, u: float, chars: int | None = None, font=None) -> Image.Image:
    """Tira de papel com a frase datilografada."""
    f = font or gfx.font("typewriter", round(46 * u))
    pad_x, pad_y = round(28 * u), round(16 * u)
    asc, desc = f.getmetrics()
    tw = gfx.text_width(text, f)
    img = Image.new("RGBA", (tw + 2 * pad_x, asc + desc + 2 * pad_y), (238, 231, 216, 255))
    gfx.draw_spaced(ImageDraw.Draw(img), (pad_x, pad_y), text, f, (*INK, 255), chars=chars,
                    bold=max(1, round(1.4 * u)))
    return img


# -------------------------------------------------------------- paperfloat
class PaperFloat(Template):
    """Uma polaroide flutuando no centro sobre a própria imagem desfocada. ``keyword`` na borda da foto,
    ``subtitle`` (trecho da fala) numa tira de papel."""

    DROP, CPS = 0.5, 18.0

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        ref = spec.media[0] if spec.media else None
        self.bg = _WashedBackground(self, ref)
        self.pw = round(w * 0.44)
        self.ph = round(self.pw * 0.64)
        self.img = self.src(ref, self.pw, self.ph, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0)) \
            if ref is not None else None
        self.tilt = float(self.rng.uniform(1.2, 2.8)) * (1 if self.rng.random() < 0.5 else -1)
        self.caption = " ".join((spec.keyword or "").split())
        self.sub = " ".join((spec.subtitle or "").split()).upper()
        self.f_sub = gfx.font("typewriter", round(44 * u))
        while self.sub and gfx.text_width(self.sub, self.f_sub) > w * 0.7 and self.f_sub.size > 26 * u:
            self.f_sub = gfx.font("typewriter", self.f_sub.size - 2)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(0.05, "whoosh_soft", 0.4), (cls.DROP, "snap", 0.35), (cls.DROP + 0.15, "tape", 0.35)]
        if spec.keyword:
            out.append((0.75, f"typing:{max(0.2, len(spec.keyword) / cls.CPS):.3f}", 0.5))
        if spec.subtitle:
            out.append((1.3, f"typing:{max(0.2, len(spec.subtitle) / 26):.3f}", 0.45))
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.frame(i)
        if self.img is not None:
            q = gfx.progress(t, 0.05, self.DROP)
            e = gfx.ease_out_back(q, 1.4)
            chars = int(max(0.0, t - 0.75) * self.CPS)
            card = polaroid(self.img.frame(i), u, self.caption, chars)
            if t >= self.DROP:
                qt = gfx.progress(t, self.DROP, 0.15)
                card.alpha_composite(tape(u, 190, -3, qt), (round(card.width / 2 - 95 * u), round(-6 * u)))
            angle = self.tilt * (1 + (1 - e) * 2.5) + math.sin(t * 0.9) * 0.5
            layer = gfx.with_shadow(card, angle, round(22 * u), (round(10 * u), round(20 * u)), 120)
            bob = math.sin(t * 1.2) * 8 * u
            y = h * 0.46 - (1 - e) * h * 0.25 + bob
            gfx.paste_center(canvas, gfx.scaled(layer, 1.0 + 0.012 * math.sin(t * 0.7)), w / 2, y, alpha=min(1.0, q * 2))
        if self.sub:
            qs = gfx.ease_out_cubic(gfx.progress(t, 1.15, 0.35))
            if qs > 0:
                s = strip(self.sub, u, int(max(0.0, t - 1.3) * 26), self.f_sub)
                card = gfx.with_shadow(s, -1.2, round(10 * u), (round(4 * u), round(8 * u)), 80)
                gfx.paste_center(canvas, card, w / 2 + (1 - qs) * 60 * u, h * 0.88, alpha=qs)
        return canvas


# --------------------------------------------------------------- paperpair
class PaperPair(Template):
    """Duas polaroides presas com alfinete e ligadas pela linha vermelha; ``keyword`` na tira de baixo."""

    ENTER = (0.1, 0.45)

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = _WashedBackground(self, spec.media[0] if spec.media else None)
        media = (spec.media * 2)[:2]
        self.pw = round(w * 0.34)
        self.ph = round(self.pw * 0.66)
        self.imgs = [self.src(m, self.pw, self.ph, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0))
                     for m in media]
        self.pos = [(w * 0.3, h * 0.45), (w * 0.7, h * 0.5)]
        self.tilts = [-float(self.rng.uniform(2.5, 4.5)), float(self.rng.uniform(2.0, 4.0))]
        self.pin = pin(u)
        self.name = " ".join((spec.keyword or "").split()).upper()
        self.cache: dict[int, Image.Image] = {}

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(t, "whoosh_soft", 0.35) for t in cls.ENTER] + [(t + 0.35, "snap", 0.35) for t in cls.ENTER]
        out.append((0.95, "swipe", 0.3))
        if spec.keyword:
            out.append((1.3, f"typing:{max(0.2, len(spec.keyword) / 22):.3f}", 0.45))
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.frame(i)
        pins = []
        for k, (img, (cx, cy), tilt) in enumerate(zip(self.imgs, self.pos, self.tilts)):
            q = gfx.progress(t, self.ENTER[k], 0.45)
            if q <= 0:
                continue
            e = gfx.ease_out_back(q, 1.3)
            card = polaroid(img.frame(i), u)
            layer = gfx.with_shadow(card, tilt * (1 + (1 - e)), round(20 * u), (round(8 * u), round(18 * u)), 110)
            dx = (-1 if k == 0 else 1) * (1 - e) * w * 0.3
            y = cy + math.sin(t * 1.1 + k) * 6 * u
            gfx.paste_center(canvas, layer, cx + dx, y, alpha=min(1.0, q * 2))
            if q >= 1:
                pins.append((cx, y - self.ph / 2 - 6 * u))
        d = ImageDraw.Draw(canvas)
        if len(pins) == 2:
            red_string(d, pins[0], pins[1], gfx.ease_out_cubic(gfx.progress(t, 0.95, 0.45)), u)
        for p in pins:
            gfx.paste_center(canvas, self.pin, p[0], p[1])
        if self.name:
            qs = gfx.ease_out_cubic(gfx.progress(t, 1.15, 0.3))
            if qs > 0:
                s = strip(self.name, u, int(max(0.0, t - 1.3) * 22))
                gfx.paste_center(canvas, gfx.with_shadow(s, 1.0, round(10 * u), (round(4 * u), round(8 * u)), 80),
                                 w / 2, h * 0.88 + (1 - qs) * 40 * u, alpha=qs)
        return canvas


# ----------------------------------------------------------- papertimeline
class PaperTimeline(Template):
    """Linha do tempo no papel: traço de caneta, ano datilografado e a foto de cada item (``media``/``labels``/
    ``notes``) presa com alfinete quando é falado; a câmera anda se houver mais de 3."""

    FIRST = 0.7

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = paper_background(self)
        self.n = n = max(1, min(6, len(spec.media)))
        self.labels = ([str(x) for x in spec.labels] + [""] * n)[:n]
        self.notes = ([str(x) for x in spec.notes] + [""] * n)[:n]
        self.scroll = n > 3
        spacing = w * 0.31 if self.scroll else w * 0.8 / n
        self.xs = [k * spacing for k in range(n)]
        self.cw = round(min(w * 0.22, spacing * 0.78))
        self.ch = round(self.cw * 0.66)
        self.imgs = [self.src(m, self.cw, self.ch, zoom=1.06) for m in spec.media[:n]]
        self.starts = item_starts(spec, n, first=self.FIRST)
        self.line_y = h * 0.68
        self.f_year = gfx.font("typewriter", round((86 if n <= 3 else 72) * u))
        self.f_note = gfx.font("typewriter", round(32 * u))
        self.f_title = gfx.font("typewriter", round(50 * u))
        self.title = " ".join((spec.keyword or "").split()).upper()
        self.tilts = [float(self.rng.uniform(-3, 3)) for _ in range(n)]
        self.pin = pin(u)
        span = (self.xs[-1] if n > 1 else 0) + w * 1.2
        xs = np.linspace(-w * 0.2, span, 80)
        self.wobble = [(float(x), float(self.rng.normal(0, 2.2)) * u) for x in xs]  # traço de caneta
        self.frozen: dict[int, Image.Image] = {}

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(6, len(spec.media)))
        out: list[Cue] = [(0.0, "pencil:0.8", 0.35)]
        if spec.keyword:
            out.append((0.15, f"typing:{max(0.2, len(spec.keyword) / 20):.3f}", 0.45))
        for s in item_starts(spec, n, first=cls.FIRST):
            out += [(s - 0.1, "whoosh_soft", 0.3), (s + 0.3, "snap", 0.35), (s + 0.35, "typing:0.3", 0.4)]
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
        canvas = self.bg.copy()
        d = ImageDraw.Draw(canvas)
        if self.title:
            gfx.draw_spaced(d, (w * 0.06, h * 0.08), self.title, self.f_title, (*INK, 255), tracking=0.35,
                            chars=int(max(0.0, t - 0.15) * 20), bold=max(1, round(1.2 * u)))
        off = w / 2 - self._cam(t)
        ly = self.line_y
        grow = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.8))
        pts = [(x + off, ly + dy) for x, dy in self.wobble if x + off <= w * grow + 10]
        if len(pts) > 1:
            d.line(pts, fill=(*INK, 230), width=max(3, round(5 * u)), joint="curve")
        k_act = self._active(t)
        for k in range(self.n):
            st = self.starts[k]
            if t < st - 0.2:
                continue
            sx = self.xs[k] + off
            if sx < -self.cw or sx > w + self.cw:
                continue
            q = gfx.progress(t, st, 0.45)
            e = gfx.ease_out_back(q, 1.4)
            active = k == k_act
            # marca de caneta no ponto do ano
            d.line([(sx, ly - 14 * u), (sx, ly + 14 * u)], fill=(*INK, 255), width=max(3, round(5 * u)))
            if active or k not in self.frozen:
                self.frozen[k] = gfx.with_shadow(polaroid(self.imgs[k].frame(i), u * 0.8), self.tilts[k],
                                                 round(14 * u), (round(6 * u), round(12 * u)), 100)
            layer = self.frozen[k]
            scale = 1.0 if active else 0.86
            cy = ly - 60 * u - layer.height * scale / 2 + (1 - e) * 60 * u
            gfx.paste_center(canvas, gfx.scaled(layer, scale), sx, cy, alpha=min(1.0, q * 2) * (1 if active else 0.85))
            if q >= 1:  # alfinete na borda de cima da foto (a camada tem a margem da sombra)
                card_h = (self.ch + 32 * u * 0.8) * scale
                gfx.paste_center(canvas, self.pin, sx, cy - card_h / 2 + 4 * u)
            year = self.labels[k].upper()
            if year:
                chars = int(max(0.0, t - st - 0.35) * 16)
                yw = gfx.spaced_width(year, self.f_year, 0.05)
                gfx.draw_spaced(d, (sx - yw / 2, ly + 30 * u), year, self.f_year, (*(RED if active else INK), 255),
                                tracking=0.05, chars=chars, bold=max(1, round(1.6 * u)))
            note = self.notes[k].upper()
            if note and t >= st + 0.6:
                nw = gfx.spaced_width(note, self.f_note, 0.1)
                gfx.draw_spaced(d, (sx - nw / 2, ly + 130 * u), note, self.f_note, (*INK, 200), tracking=0.1,
                                chars=int((t - st - 0.6) * 24))
        return canvas


# ------------------------------------------------------------- papercompare
class PaperCompare(Template):
    """Comparativo: 2–3 polaroides com o nome datilografado (``labels``) entrando quando cada uma é falada
    (``captions``) e o carimbo vermelho "VS" entre as duas."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = paper_background(self)
        self.n = n = max(2, min(3, len(spec.media)))
        media = (spec.media * 3)[:n]
        self.pw = round(w * (0.26 if n == 3 else 0.36))
        self.ph = round(self.pw * 0.66)
        self.imgs = [self.src(m, self.pw, self.ph, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0))
                     for m in media]
        self.xs = [w * x for x in ((0.2, 0.5, 0.8) if n == 3 else (0.28, 0.72))]
        self.y = h * 0.5
        self.tilts = [float(self.rng.uniform(1.5, 3.5)) * (1 if k % 2 else -1) for k in range(n)]
        self.labels = ([str(x) for x in spec.labels] + [""] * n)[:n]
        self.starts = item_starts(spec, n, first=0.15, gap=0.4)
        self.vs_t = (self.starts[1] + 0.35) if n >= 2 else 1.0
        self.vs = stamp("VS", u)
        self.title = " ".join((spec.keyword or "").split()).upper()
        self.f_title = gfx.font("typewriter", round(48 * u))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(2, min(3, len(spec.media)))
        starts = item_starts(spec, n, first=0.15, gap=0.4)
        out: list[Cue] = []
        for s in starts:
            out += [(s, "whoosh_soft", 0.35), (s + 0.4, "typing:0.4", 0.4)]
        return out + [(starts[1] + 0.35, "impact", 0.55)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.copy()
        d = ImageDraw.Draw(canvas)
        if self.title:
            tw = gfx.spaced_width(self.title, self.f_title, 0.3)
            gfx.draw_spaced(d, (w / 2 - tw / 2, h * 0.09), self.title, self.f_title, (*INK, 255), tracking=0.3,
                            chars=int(max(0.0, t - 0.1) * 22), bold=max(1, round(1.2 * u)))
        for k in range(self.n):
            q = gfx.progress(t, self.starts[k], 0.45)
            if q <= 0:
                continue
            e = gfx.ease_out_back(q, 1.3)
            chars = int(max(0.0, t - self.starts[k] - 0.4) * 18)
            card = polaroid(self.imgs[k].frame(i), u, self.labels[k], chars)
            layer = gfx.with_shadow(card, self.tilts[k] * (1 + (1 - e)), round(18 * u), (round(8 * u), round(16 * u)), 110)
            gfx.paste_center(canvas, layer, self.xs[k], self.y + (1 - e) * 120 * u + math.sin(t + k) * 5 * u,
                             alpha=min(1.0, q * 2))
        qv = gfx.progress(t, self.vs_t, 0.22)
        if qv > 0:
            scale = 1.0 + (1 - gfx.ease_out_cubic(qv)) * 0.8
            mark = gfx.scaled(self.vs.rotate(-9, expand=True, resample=Image.BICUBIC), scale)
            cx = (self.xs[0] + self.xs[1]) / 2
            gfx.paste_center(canvas, mark, cx, self.y, alpha=min(1.0, qv * 1.5))
        return canvas


# ----------------------------------------------------------- paperstatement
class PaperStatement(Template):
    """A frase de impacto (``body``) datilografada no papel e o círculo de caneta vermelha em volta da
    palavra forte (``labels``; sem ela, a mais longa do fim da frase)."""

    CPS = 24.0

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = paper_background(self)
        self.text = " ".join((spec.body or spec.keyword or "").split())
        size = 92
        self.f = gfx.font("typewriter", round(size * u))
        lines = gfx.wrap(self.text, self.f, round(w * 0.78), 3)
        while len(" ".join(lines)) < len(self.text) and size > 56:
            size -= 8
            self.f = gfx.font("typewriter", round(size * u))
            lines = gfx.wrap(self.text, self.f, round(w * 0.8), 3)
        self.lines = lines
        asc, desc = self.f.getmetrics()
        self.lh = round((asc + desc) * 1.25)
        self.x0 = w * 0.1
        self.y0 = h / 2 - self.lh * len(lines) / 2
        marks = {gfx.safe_text(m).lower().strip(PUNCT) for lb in spec.labels for m in str(lb).split()}
        words = [wd for ln in lines for wd in ln.split()]
        if not marks and words:
            marks = {max(words[-4:], key=lambda s: len(s.strip(PUNCT))).lower().strip(PUNCT)}
        self.box = None
        for k, ln in enumerate(lines):  # caixa da palavra marcada (para o círculo)
            x = self.x0
            for wd in ln.split():
                ww = gfx.text_width(wd + " ", self.f)
                if self.box is None and wd.lower().strip(PUNCT) in marks:
                    self.box = (x - 18 * u, self.y0 + k * self.lh - 10 * u, x + gfx.text_width(wd, self.f) + 18 * u,
                                self.y0 + k * self.lh + asc + desc * 0.6)
                x += ww
        self.done_t = 0.15 + len(self.text) / self.CPS

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        text = " ".join((spec.body or spec.keyword or "").split())
        t = 0.15 + len(text) / cls.CPS
        return [(0.15, f"typing:{max(0.2, len(text) / cls.CPS):.3f}", 0.55), (t + 0.1, "pencil:0.5", 0.45)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u = self.u
        canvas = self.bg.copy()
        d = ImageDraw.Draw(canvas)
        left = int(max(0.0, t - 0.15) * self.CPS)
        for k, ln in enumerate(self.lines):
            if left <= 0:
                break
            gfx.draw_spaced(d, (self.x0, self.y0 + k * self.lh), ln, self.f, (*INK, 255), chars=left,
                            bold=max(1, round(1.6 * u)))
            left -= len(ln) + 1
        q = gfx.ease_in_out(gfx.progress(t, self.done_t + 0.1, 0.55))
        if q > 0 and self.box is not None:
            x0, y0, x1, y1 = self.box
            cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 + 10 * u, (y1 - y0) / 2 + 12 * u
            pts = []
            total = 2 * math.pi * 1.12 * q  # dá uma volta e passa um pouco, como à mão
            for s in range(max(2, int(60 * q))):
                a = -2.2 + total * s / max(1, int(60 * q) - 1)
                wob = 1 + 0.04 * math.sin(a * 3)
                pts.append((cx + rx * wob * math.cos(a), cy + ry * wob * math.sin(a)))
            d.line(pts, fill=(*RED, 240), width=max(4, round(7 * u)), joint="curve")
        return canvas



# --------------------------------------------------------------- paperrank
class PaperRank(Template):
    """Posição do ranking no papel: carimbo "Nº N" (``rank``) batendo, a polaroide do carro (``media[0]``)
    presa com fita, o nome (``keyword``) datilografado na foto e ``subtitle`` numa tira de papel."""

    STAMP, DROP, CPS = 0.15, 0.55, 18.0

    @classmethod
    def cps(cls, text: str) -> float:
        """Legenda longa (ranking de motivos) é datilografada mais rápido: termina em até 1,3 s."""
        return max(cls.CPS, len(text or "") / 1.3)

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        ref = spec.media[0] if spec.media else None
        self.bg = _WashedBackground(self, ref)
        self.pw = round(w * 0.4)
        self.ph = round(self.pw * 0.66)
        self.img = self.src(ref, self.pw, self.ph, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0)) \
            if ref is not None else None
        label = f"{tr('number', spec.lang)} {spec.rank}" if spec.rank else tr("number", spec.lang)
        self.stamp = stamp(label, u, 150 if len(label) <= 5 else 120)
        self.stamp_angle = float(self.rng.uniform(-9, -4))
        self.tilt = float(self.rng.uniform(1.5, 3.0))
        self.caption = " ".join((spec.keyword or "").split())
        self.sub = " ".join((spec.subtitle or "").split()).upper()
        self.f_sub = gfx.font("typewriter", round(40 * u))
        while self.sub and gfx.text_width(self.sub, self.f_sub) > w * 0.62 and self.f_sub.size > 24 * u:
            self.f_sub = gfx.font("typewriter", self.f_sub.size - 2)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(cls.STAMP + 0.18, "impact", 0.6), (cls.DROP - 0.1, "whoosh_soft", 0.35),
                          (cls.DROP + 0.3, "tape", 0.35)]
        if spec.keyword:
            out.append((cls.DROP + 0.45, f"typing:{max(0.2, len(spec.keyword) / cls.cps(spec.keyword)):.3f}", 0.5))
        if spec.subtitle:
            out.append((1.5, f"typing:{max(0.2, len(spec.subtitle) / 26):.3f}", 0.45))
        return out

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.frame(i)
        # carimbo: chega grande e "bate" no papel (encolhe de 1.8x para 1x)
        qs = gfx.progress(t, self.STAMP, 0.2)
        if qs > 0:
            scale = 1.0 + 0.8 * (1 - gfx.ease_out_cubic(qs))
            layer = gfx.with_shadow(self.stamp, self.stamp_angle, round(4 * u), (round(2 * u), round(3 * u)), 40)
            gfx.paste_center(canvas, gfx.scaled(layer, scale), w * 0.26, h * 0.42, alpha=min(1.0, qs * 2))
        if self.img is not None:
            q = gfx.progress(t, self.DROP - 0.1, 0.45)
            e = gfx.ease_out_back(q, 1.4)
            chars = int(max(0.0, t - self.DROP - 0.45) * self.cps(self.caption))
            card = polaroid(self.img.frame(i), u, self.caption, chars)
            if t >= self.DROP + 0.3:
                qt = gfx.progress(t, self.DROP + 0.3, 0.15)
                card.alpha_composite(tape(u, 180, 4, qt), (round(card.width / 2 - 90 * u), round(-6 * u)))
            angle = self.tilt * (1 + (1 - e) * 2.5) + math.sin(t * 0.9) * 0.5
            layer = gfx.with_shadow(card, angle, round(22 * u), (round(10 * u), round(20 * u)), 120)
            bob = math.sin(t * 1.2) * 7 * u
            gfx.paste_center(canvas, layer, w * 0.68 + (1 - e) * w * 0.3, h * 0.44 + bob, alpha=min(1.0, q * 2))
        if self.sub:
            qb = gfx.ease_out_cubic(gfx.progress(t, 1.35, 0.35))
            if qb > 0:
                s = strip(self.sub, u, int(max(0.0, t - 1.5) * 26), self.f_sub)
                card = gfx.with_shadow(s, -1.0, round(10 * u), (round(4 * u), round(8 * u)), 80)
                gfx.paste_center(canvas, card, w / 2 + (1 - qb) * 60 * u, h * 0.88, alpha=qb)
        return canvas


PAPER_TEMPLATES: dict[str, type[Template]] = {
    "paperfloat": PaperFloat, "paperpair": PaperPair, "papertimeline": PaperTimeline, "papercompare": PaperCompare,
    "paperstatement": PaperStatement, "paperrank": PaperRank,
}
# o que cada um usa da lógica do planejador (mídias, itens, legendas sincronizadas)
PAPER_BASE = {"paperfloat": "showcase", "paperpair": "gallery", "papertimeline": "timeline",
              "papercompare": "comparison", "paperstatement": "statement", "paperrank": "ranking"}


def paper_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = PAPER_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
