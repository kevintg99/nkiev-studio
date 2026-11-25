"""Templates de retenção ("edição magnética"): dados da fala virando gráfico e quebras de padrão.

Cada template tem a cara do estilo do vídeo (``skin``): ``blueprint`` (Minimal), ``paper`` (Dossiê),
``tv`` (Broadcast), ``data`` (Dados), ``kinetic`` e ``cinema``. O mesmo template sai diferente em cada
estilo: fundo, moldura das fotos, fontes, rótulos e acabamento (tarja, letterbox, grade...).

Dados (o bot acha na fala, ``auto_templates``):

* ``percentbar``  — "39% of owners reported a fault": o número contando, a barra enchendo e o rótulo.
* ``peoplegrid``  — "three in ten", "one in five": bonequinhos, os da conta acendendo um a um.
* ``receipt``     — "cost more than £1,500": nota da oficina imprimindo, as peças citadas e o TOTAL.
* ``downtime``    — "off the road for more than a week": calendário com os dias riscados em vermelho.
* ``warning``     — "the biggest area was non-engine electrics": luz de alerta do painel acesa e o defeito.

Ritmo (quebra de padrão a cada 20–40 s, ``rhythm``):

* ``textmask``    — a palavra gigante (o carro, o assunto) com o vídeo passando dentro das letras.
* ``photostack``  — 2–3 fotos caindo uma por cima da outra, girando, e a legenda.
* ``splitpanels`` — a tela em 3 painéis verticais entrando de cima e de baixo, com a palavra-chave.
* ``annotate``    — a mão desenha um círculo e uma seta na foto, com a anotação escrita à mão.
* ``wordpop``     — as palavras fortes da frase pulando grandes no meio da tela, no tempo da fala.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .i18n import text as tr
from .templates import Template, TemplateSpec
from .templates_auto import _blur_frame, _blurred_source

Cue = tuple[float, str, float]
RGB = tuple[int, int, int]
SKINS = ("blueprint", "paper", "tv", "data", "kinetic", "cinema")
WHITE: RGB = (255, 255, 255)


# ------------------------------------------------------------------ peles
@dataclass
class Skin:
    name: str
    ink: RGB  # texto principal
    muted: RGB  # texto secundário
    accent: RGB  # destaque (cor do canal; vermelho no papel)
    panel: tuple[int, int, int, int]  # caixa atrás dos textos
    title: str  # fonte dos títulos/números
    body: str  # fonte dos rótulos
    upper: bool = True

    def font(self, role: str, size: float, u: float):
        return gfx.font(self.title if role == "title" else self.body, round(size * u))

    def txt(self, s: str) -> str:
        s = " ".join((s or "").split())
        return s.upper() if self.upper else s


def make_skin(name: str, tpl: Template) -> Skin:
    acc = tuple(gfx.accent())
    if name == "paper":
        return Skin("paper", (52, 44, 38), (110, 100, 90), (196, 46, 34), (238, 231, 216, 240),
                    "typewriter", "typewriter")
    if name == "tv":
        return Skin("tv", WHITE, (200, 205, 215), acc, (14, 16, 22, 232), "oswald_bold", "montserrat_bold")
    if name == "data":
        return Skin("data", (226, 236, 242), (130, 150, 165), acc, (10, 14, 20, 225), "oswald", "montserrat")
    if name == "kinetic":
        return Skin("kinetic", WHITE, (190, 190, 190), acc, (0, 0, 0, 230), "black", "black")
    if name == "cinema":
        return Skin("cinema", (238, 234, 226), (170, 165, 155), (226, 196, 140), (0, 0, 0, 150),
                    "oswald_light", "montserrat", upper=True)
    th = tpl.th  # blueprint: o padrão do canal (Minimal)
    return Skin("blueprint", (246, 248, 255), tuple(th.get("muted", (170, 180, 210))), acc,
                (*th["bg"], 225), "black", "bold")


class Backdrop:
    """Fundo de cada pele; com mídia, cinema e kinetic usam a própria imagem desfocada."""

    def __init__(self, tpl: Template, skin: Skin, ref=None, use_media: bool = False) -> None:
        self.tpl, self.skin = tpl, skin
        w, h, u = tpl.w, tpl.h, tpl.u
        self.src = _blurred_source(tpl, ref, zoom=1.12) if ref is not None and use_media else None
        self.cache: tuple[int, Image.Image] | None = None
        name = skin.name
        if name == "paper":
            from .templates_paper import paper_background

            self.base = paper_background(tpl)
        elif name == "tv":
            from .templates_news import studio_background

            self.base = studio_background(tpl)
        elif name == "data":
            self.base = _data_background(w, h, u, skin.accent)
        elif name == "kinetic":
            self.base = Image.new("RGBA", (w, h), (10, 10, 10, 255))
        elif name == "cinema":
            self.base = gfx.vignette(Image.new("RGB", (w, h), (18, 16, 14)), 0.5).convert("RGBA")
        else:
            self.cell = round(90 * u)
            self.base = gfx.background(tpl.s.theme, w, h, self.cell, extra=self.cell).convert("RGBA")

    def frame(self, i: int) -> Image.Image:
        tpl, w, h = self.tpl, self.tpl.w, self.tpl.h
        if self.src is not None:
            if self.cache is None or i - self.cache[0] >= 2:
                dim = 0.42 if self.skin.name in ("cinema", "kinetic") else 0.5
                img = _blur_frame(self.src.frame(i), w, h, dim).convert("RGBA")
                if self.skin.name == "paper":
                    from .templates_paper import washed

                    img = washed(self.src.frame(i), w, h)
                self.cache = (i, img)
            return self.cache[1].copy()
        if self.skin.name == "blueprint":
            return gfx.drift(self.base, i / tpl.fps, w, h, self.cell, speed=5 * tpl.u)
        return self.base.copy()


def _data_background(w: int, h: int, u: float, acc: RGB) -> Image.Image:
    img = Image.new("RGBA", (w, h), (9, 12, 17, 255))
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))  # linhas translúcidas: camada própria, depois compõe
    d = ImageDraw.Draw(grid)
    step = round(48 * u)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(*acc, 22), width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(*acc, 22), width=1)
    img.alpha_composite(grid)
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([w * 0.25, h * 0.1, w * 0.75, h * 0.9], fill=(*acc, 30))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(round(120 * u))))
    return img


def card(skin: Skin, img: Image.Image, u: float, caption: str = "", angle: float = 0.0) -> Image.Image:
    """Moldura da foto em cada pele (polaroide no papel, monitor na TV, cantoneiras nos dados...)."""
    if skin.name == "paper":
        from .templates_paper import polaroid

        layer = polaroid(img, u * 0.9, caption)
        return gfx.with_shadow(layer, angle, round(18 * u), (round(8 * u), round(16 * u)), 110)
    if skin.name == "tv":
        from .templates_news import screen_frame

        return gfx.with_shadow(screen_frame(img, u), angle, round(22 * u), (0, round(18 * u)), 150)
    if skin.name == "data":
        pad = round(10 * u)
        layer = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
        layer.paste(img.convert("RGB"), (pad, pad))
        d = ImageDraw.Draw(layer)
        d.rectangle([pad, pad, layer.width - pad - 1, layer.height - pad - 1], outline=(*skin.accent, 200),
                    width=max(1, round(2 * u)))
        k, t = round(36 * u), max(2, round(5 * u))
        for cx, cy, sx, sy in ((0, 0, 1, 1), (layer.width, 0, -1, 1), (0, layer.height, 1, -1),
                               (layer.width, layer.height, -1, -1)):
            d.line([(cx, cy + sy * k), (cx, cy), (cx + sx * k, cy)], fill=(*skin.accent, 255), width=t)
        return layer.rotate(angle, expand=True, resample=Image.BICUBIC) if angle else layer
    if skin.name == "kinetic":
        layer = gfx.bordered(img, max(6, round(12 * u)), WHITE)
        return gfx.with_shadow(layer, angle, round(10 * u), (round(14 * u), round(14 * u)), 200)
    if skin.name == "cinema":
        return gfx.with_shadow(img.convert("RGBA"), angle, round(30 * u), (0, round(20 * u)), 170)
    layer = gfx.bordered(img, max(4, round(8 * u)), (250, 250, 255))
    return gfx.with_shadow(layer, angle, round(18 * u), (round(6 * u), round(14 * u)), 170)


def label(skin: Skin, text: str, u: float, max_w: float, size: float = 38) -> Image.Image | None:
    """Rótulo curto (legenda da foto, o que o número significa) no jeito de cada pele."""
    text = skin.txt(text)
    if not text:
        return None
    if skin.name == "paper":
        from .templates_paper import strip

        f = skin.font("body", size, u)
        while gfx.text_width(text, f) > max_w and f.size > 18 * u:
            f = gfx.font(skin.body, f.size - 2)
        return strip(text, u, None, f)
    if skin.name == "tv":
        from .templates_news import name_tag

        return name_tag(text, u, max_w, size)
    f = skin.font("body", size, u)
    lines = gfx.wrap(text, f, round(max_w - 40 * u), 2)
    asc, desc = f.getmetrics()
    lh = round((asc + desc) * 1.1)
    w = round(min(max_w, max(gfx.text_width(ln, f) for ln in lines) + 44 * u))
    h = round(lh * len(lines) + 22 * u)
    img = Image.new("RGBA", (max(1, w), h), skin.panel if skin.name != "kinetic" else (*skin.accent, 255))
    d = ImageDraw.Draw(img)
    fg = (10, 10, 10) if skin.name == "kinetic" else skin.ink
    if skin.name in ("blueprint", "data"):
        d.rectangle([0, 0, max(3, round(6 * u)), h], fill=(*skin.accent, 255))
    for k, ln in enumerate(lines):
        d.text((22 * u, 11 * u + k * lh - 2 * u), ln, font=f, fill=(*fg, 255))
    return img


def finish(skin: Skin, canvas: Image.Image, t: float, u: float, seed: int = 0) -> None:
    """Acabamento por cima de tudo: letterbox e grão no cinema, scanline nos dados."""
    w, h = canvas.size
    if skin.name == "cinema":
        bar = round(h * 0.1)
        d = ImageDraw.Draw(canvas)
        d.rectangle([0, 0, w, bar], fill=(0, 0, 0, 255))
        d.rectangle([0, h - bar, w, h], fill=(0, 0, 0, 255))
        rng = np.random.default_rng(seed * 31 + int(t * 24))
        grain = (rng.random((h // 4, w // 4)) * 36).astype(np.uint8)
        g = Image.fromarray(grain, "L").resize((w, h), Image.NEAREST)
        canvas.alpha_composite(Image.merge("RGBA", (g, g, g, g.point(lambda v: v // 2))))
    elif skin.name == "data":
        y = (t * 240 * u) % h
        line = Image.new("RGBA", (w, max(1, round(3 * u))), (*skin.accent, 40))
        canvas.alpha_composite(line, (0, round(y)))


def big_number(skin: Skin, text: str, u: float, size: float = 230) -> Image.Image:
    f = skin.font("title", size, u)
    if skin.name in ("blueprint", "kinetic"):
        fill = (*skin.accent, 255) if skin.name == "blueprint" else (*WHITE, 255)
        return gfx.outlined_text(text, f, fill, round(4 * u), (0, 0, 0, 255))
    color = skin.accent if skin.name in ("paper", "data") else skin.ink
    return gfx.text_block([text], f, color, shadow=3 if skin.name in ("tv", "cinema") else 0)


class Magnet(Template):
    """Base: pele, fundo e o "pulso" de entrada usados por todos os templates deste arquivo."""

    BG_MEDIA = False  # usa a própria mídia desfocada de fundo

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        name = spec.skin if spec.skin in SKINS else "blueprint"
        self.sk = make_skin(name, self)
        ref = spec.media[0] if spec.media else None
        self.back = Backdrop(self, self.sk, ref, use_media=self.BG_MEDIA or name in ("cinema",))
        self.dur = spec.frames / self.fps

    def canvas(self, i: int) -> Image.Image:
        return self.back.frame(i)

    def done(self, canvas: Image.Image, t: float) -> Image.Image:
        finish(self.sk, canvas, t, self.u, self.s.seed)
        return canvas


def _num(spec: TemplateSpec, key: str = "value", default: float = 0.0) -> float:
    try:
        return float((spec.number or {}).get(key) or default)
    except (TypeError, ValueError):
        return default


# -------------------------------------------------------------- percentbar
class PercentBar(Magnet):
    """``number.value`` (0–100) contando, a barra enchendo até ele e ``keyword`` (o que é) embaixo;
    com foto, ela entra à direita."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        self.value = max(0.0, min(100.0, _num(spec)))
        self.decimals = 1 if self.value != int(self.value) else 0
        self.has_img = bool(spec.media)
        self.img = self.src(spec.media[0], round(w * 0.34), round(w * 0.34 * 0.66), zoom=1.06) if self.has_img else None
        self.bar_w = round(w * (0.5 if self.has_img else 0.76))
        self.x0 = round(w * 0.07)
        self.lab = label(self.sk, spec.keyword or spec.subtitle, u, self.bar_w, 40)
        self.f_small = self.sk.font("body", 30, u)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh_soft", 0.4), (0.2, "tick", 0.2), (0.6, "tick", 0.2), (1.25, "impact_soft", 0.45),
                (1.5, "swipe", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, h = self.u, self.h
        c = self.canvas(i)
        q = gfx.ease_out_cubic(gfx.progress(t, 0.15, 1.1))
        v = self.value * q
        txt = (f"{v:.1f}" if self.decimals else f"{v:.0f}") + "%"
        num = big_number(self.sk, txt, u, 230)
        gfx.paste_at(c, num, self.x0, h * 0.2 - (1 - gfx.ease_out_back(gfx.progress(t, 0.1, 0.4))) * 40 * u,
                     alpha=gfx.progress(t, 0.1, 0.25))
        by, bh = h * 0.2 + num.height + 30 * u, round(46 * u)
        track = (*self.sk.ink, 45) if self.sk.name != "paper" else (52, 44, 38, 50)
        over = Image.new("RGBA", c.size, (0, 0, 0, 0))  # translúcido: desenha numa camada e compõe
        od = ImageDraw.Draw(over)
        od.rounded_rectangle([self.x0, by, self.x0 + self.bar_w, by + bh], radius=round(bh / 2), fill=track)
        for k in range(1, 10):  # marquinhas de 10 em 10
            x = self.x0 + self.bar_w * k / 10
            od.line([(x, by + bh + 8 * u), (x, by + bh + 18 * u)], fill=(*self.sk.muted, 160), width=max(1, round(2 * u)))
        c.alpha_composite(over)
        d = ImageDraw.Draw(c)
        fill_w = self.bar_w * self.value / 100 * q
        if fill_w > bh * 0.5:
            d.rounded_rectangle([self.x0, by, self.x0 + fill_w, by + bh], radius=round(bh / 2),
                                fill=(*self.sk.accent, 255))
        if self.lab is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, 0.9, 0.4))
            gfx.paste_at(c, self.lab, self.x0 - (1 - ql) * 60 * u, by + bh + 46 * u, alpha=ql)
        if self.img is not None:
            qi = gfx.ease_out_back(gfx.progress(t, 0.3, 0.5), 1.2)
            layer = card(self.sk, self.img.frame(i), u, angle=2.0 if self.sk.name in ("paper", "kinetic") else 0.0)
            gfx.paste_center(c, layer, self.w * 0.79 + (1 - qi) * self.w * 0.3, h * 0.46, alpha=min(1.0, qi * 2))
        return self.done(c, t)


# -------------------------------------------------------------- peoplegrid
def person(size: int, fill: tuple) -> Image.Image:
    img = Image.new("RGBA", (size, round(size * 1.6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size * 0.24
    d.ellipse([size / 2 - r, 0, size / 2 + r, 2 * r], fill=fill)
    d.rounded_rectangle([size * 0.14, 2 * r + size * 0.08, size * 0.86, size * 1.6], radius=round(size * 0.3),
                        fill=fill)
    return img


class PeopleGrid(Magnet):
    """``number.value`` de ``number.total`` ("3 in 10"): os bonequinhos da conta acendem um a um na cor
    de destaque; ``keyword`` diz quem são."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        self.total = int(max(2, min(20, _num(spec, "total", 10))))
        self.k = int(max(0, min(self.total, round(_num(spec)))))
        cols = self.total if self.total <= 10 else 10
        self.rows = math.ceil(self.total / cols)
        self.cols = cols
        self.size = round(min(w * 0.7 / cols * 0.72, 110 * u))
        off = (*self.sk.muted, 120) if self.sk.name != "paper" else (150, 140, 128, 150)
        self.off = person(self.size, off)
        self.on = person(self.size, (*self.sk.accent, 255))
        self.big = f"{self.k}/{self.total}" if self.total not in (10, 5, 4, 3, 2, 20) else \
            f"{self.k} {tr('in_of', spec.lang)} {self.total}"
        self.lab = label(self.sk, spec.keyword, u, w * 0.8, 40)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        k = int(max(0, min(20, round(float((spec.number or {}).get("value") or 0)))))
        return [(0.0, "whoosh_soft", 0.35)] + [(0.7 + j * 0.12, "pop", 0.22) for j in range(min(k, 10))]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        gap = self.size * 0.35
        gw = self.cols * self.size + (self.cols - 1) * gap
        x0 = (w - gw) / 2
        y0 = h * 0.42 - self.rows * self.size * 0.9
        for n in range(self.total):
            r, col = divmod(n, self.cols)
            appear = gfx.ease_out_back(gfx.progress(t, 0.1 + n * 0.02, 0.3), 1.4)
            if appear <= 0:
                continue
            lit = n < self.k and t >= 0.7 + n * 0.12
            icon = self.on if lit else self.off
            if lit:
                pop = 1 + 0.18 * (1 - gfx.progress(t, 0.7 + n * 0.12, 0.25))
                icon = gfx.scaled(icon, pop)
            gfx.paste_center(c, gfx.scaled(icon, max(0.05, appear)), x0 + col * (self.size + gap) + self.size / 2,
                             y0 + r * self.size * 1.9 + self.size * 0.8)
        qb = gfx.ease_out_cubic(gfx.progress(t, 0.5, 0.4))
        num = big_number(self.sk, self.sk.txt(self.big), u, 120)
        gfx.paste_center(c, num, w / 2, h * 0.12 + num.height / 2 + (1 - qb) * 30 * u, alpha=qb)
        if self.lab is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, 1.0, 0.4))
            gfx.paste_center(c, self.lab, w / 2, h * 0.8 + (1 - ql) * 30 * u, alpha=ql)
        return self.done(c, t)


# ------------------------------------------------------------------ receipt
class Receipt(Magnet):
    """Nota da oficina: o papel sai da impressora com as peças da fala (``points``), a linha pontilhada
    e o TOTAL (``number``: value + prefix ou suffix; ``low`` vira faixa), com ``keyword`` (o carro) no cabeçalho."""

    BG_MEDIA = True

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u = self.u
        self.rw = round(640 * u)
        self.items = [str(p.get("text", "")) for p in (spec.points or []) if str(p.get("text", "")).strip()][:4]
        value = _num(spec)
        prefix = str((spec.number or {}).get("prefix") or "")
        suffix = str((spec.number or {}).get("suffix") or "")  # "1 500 €" (fr/pt: moeda depois do número)
        low = _num(spec, "low")
        cents = 2 if value and round(value % 1, 2) else 0  # "£110.01" (o tanque) não vira "£110"
        self.total = gfx.fmt_number(value, cents, prefix, suffix) if value else ""
        if value and 0 < low < value:  # "entre 1 500 et 3 000 euros" -> "1 500–3 000 €"
            self.total = gfx.fmt_number(low, 0, prefix) + "–" + gfx.fmt_number(value, 0, "", suffix)
        self.f_mono = gfx.font("typewriter", round(40 * u))
        self.f_head = gfx.font("typewriter", round(46 * u))
        size = round(80 * u)
        self.f_total = gfx.font("black", size)
        while size > 30 * u and gfx.text_width(self.total, self.f_total) > self.rw - 60 * u:
            size = round(size * 0.9)
            self.f_total = gfx.font("black", size)
        self.head = (spec.keyword or tr("repair", spec.lang)).upper()
        self.paper = self._paper()

    def _paper(self) -> Image.Image:
        u, rw = self.u, self.rw
        lines: list[tuple[str, str]] = [(self.head, "head"), ("-" * 26, "rule")]
        for it in self.items:
            lines.append((it.upper()[:24], "item"))
        lines += [("-" * 26, "rule"), ("TOTAL", "totlabel"), (self.total, "total")]
        lh = round(54 * u)
        rh = round(lh * (len(lines) + 1) + 60 * u)
        img = Image.new("RGBA", (rw, rh), (250, 248, 240, 255))
        d = ImageDraw.Draw(img)
        y = round(30 * u)
        for text, kind in lines:
            if kind == "total":
                f = self.f_total
                tw = gfx.text_width(text, f)
                d.text((rw - tw - 30 * u, y - 10 * u), text, font=f, fill=(*self.sk.accent, 255)
                       if self.sk.name != "kinetic" else (20, 20, 20, 255))
                y += round(lh * 1.4)
                continue
            f = self.f_head if kind == "head" else self.f_mono
            d.text((30 * u, y), text, font=f, fill=(40, 36, 32, 255))
            if kind == "item":
                d.text((rw - 70 * u, y), "•", font=f, fill=(120, 110, 100, 255))
            y += lh
        # borda serrilhada embaixo
        for x in range(0, rw, max(4, round(18 * u))):
            d.polygon([(x, rh), (x + 9 * u, rh - 12 * u), (x + 18 * u, rh)], fill=(0, 0, 0, 0))
        return img

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.1, "whoosh_soft", 0.3), (0.3, "typing:1.400", 0.35), (1.8, "impact", 0.5)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        # impressora: a nota sai de uma fenda escura, de cima para baixo
        slot_y = h * 0.12
        d = ImageDraw.Draw(c)
        d.rounded_rectangle([w / 2 - self.rw * 0.62, slot_y - 26 * u, w / 2 + self.rw * 0.62, slot_y + 8 * u],
                            radius=round(10 * u), fill=(20, 20, 24, 240))
        q = gfx.ease_out_cubic(gfx.progress(t, 0.2, 1.6))
        show = max(1, round(self.paper.height * q))
        piece = self.paper.crop((0, self.paper.height - show, self.rw, self.paper.height))
        layer = gfx.with_shadow(piece, -1.5, round(14 * u), (round(6 * u), round(12 * u)), 120)
        gfx.paste_at(c, layer, w / 2 - layer.width / 2, slot_y - 10 * u)
        if t >= 1.8 and self.total:  # carimbo "PAID"/"TOTAL" batendo
            from .templates_paper import stamp

            qs = gfx.progress(t, 1.8, 0.2)
            st = stamp(tr("paid", self.s.lang), u, 80)
            gfx.paste_center(c, gfx.scaled(gfx.with_shadow(st, -8, 2, (2, 3), 40), 1 + 0.6 * (1 - qs)),
                             w / 2 + self.rw * 0.62, h * 0.62, alpha=min(1.0, qs * 2))
        return self.done(c, t)


# ----------------------------------------------------------------- downtime
class Downtime(Magnet):
    """Calendário do mês com os dias parado na oficina riscados em vermelho, um por um
    (``number.value`` dias; 0–1 = resolvido no mesmo dia: o dia circulado em verde) e ``keyword``."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        self.days = int(max(0, min(31, round(_num(spec)))))
        self.cell = round(min(w * 0.06, 96 * u))
        self.start_col = int(self.rng.integers(0, 4))
        self.f_day = self.sk.font("body", 34, u)
        self.lab = label(self.sk, spec.keyword, u, w * 0.8, 42)
        self.red = (214, 40, 40) if self.sk.name != "paper" else (196, 46, 34)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = int(max(0, min(31, round(float((spec.number or {}).get("value") or 0)))))
        return [(0.0, "whoosh_soft", 0.35)] + [(0.6 + j * 0.14, "pencil:0.10", 0.25) for j in range(min(n, 14))]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        cs = self.cell
        gw, gh = 7 * cs, 5 * cs
        y0 = h * 0.2
        qa = gfx.ease_out_back(gfx.progress(t, 0.05, 0.45), 1.2)
        sheet = Image.new("RGBA", (round(gw + 40 * u), round(gh + 110 * u)), (*self.sk.panel[:3], 235))
        d = ImageDraw.Draw(sheet)
        d.rectangle([0, 0, sheet.width, round(70 * u)], fill=(*self.red, 255))
        ink = self.sk.ink if self.sk.name != "paper" else (52, 44, 38)
        for n in range(31):
            r, col = divmod(n + self.start_col, 7)
            if r >= 5:
                r, col = 4, col  # o último dia divide a linha (mês que ocupa 6 semanas)
            cx, cy = 20 * u + col * cs + cs / 2, 90 * u + r * cs + cs / 2
            d.text((cx - gfx.text_width(str(n + 1), self.f_day) / 2, cy - 18 * u), str(n + 1), font=self.f_day,
                   fill=(*ink, 210))
        layer = gfx.with_shadow(sheet, 0, round(16 * u), (0, round(12 * u)), 140)
        gfx.paste_center(c, gfx.scaled(layer, max(0.05, qa)), w / 2, y0 + gh / 2 - 20 * u, alpha=min(1.0, qa * 2))
        d = ImageDraw.Draw(c)
        lx0 = w / 2 - (gw + 40 * u) / 2 + 20 * u
        ly0 = y0 + gh / 2 - 20 * u - (gh + 110 * u) / 2 + 90 * u
        lw = max(3, round(7 * u))
        if self.days <= 1:
            q = gfx.progress(t, 0.6, 0.5)
            r, col = divmod(self.start_col, 7)
            cx, cy = lx0 + col * cs + cs / 2, ly0 + r * cs + cs / 2
            if q > 0:
                d.arc([cx - cs * 0.45, cy - cs * 0.45, cx + cs * 0.45, cy + cs * 0.45], -90, -90 + 360 * q,
                      fill=(40, 190, 90, 255), width=lw)
        for n in range(self.days):
            q = gfx.progress(t, 0.6 + n * 0.14, 0.12)
            if q <= 0:
                break
            r, col = divmod(n + self.start_col, 7)
            r = min(r, 4)
            cx, cy = lx0 + col * cs + cs / 2, ly0 + r * cs + cs / 2
            s = cs * 0.36
            d.line([(cx - s, cy - s), (cx - s + 2 * s * q, cy - s + 2 * s * q)], fill=(*self.red, 255), width=lw)
            if q >= 1:
                d.line([(cx + s, cy - s), (cx - s, cy + s)], fill=(*self.red, 255), width=lw)
        if self.lab is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, 0.9, 0.4))
            gfx.paste_center(c, self.lab, w / 2, h * 0.86 + (1 - ql) * 30 * u, alpha=ql)
        return self.done(c, t)


# ------------------------------------------------------------------ warning
def warning_icon(kind: str, size: int, color: RGB) -> Image.Image:
    """Símbolo do painel: bateria, freio, motor ou o triângulo de alerta."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(3, size // 14)
    c = (*color, 255)
    if kind == "battery":
        d.rounded_rectangle([size * 0.1, size * 0.3, size * 0.9, size * 0.82], radius=size // 16, outline=c, width=lw)
        d.rectangle([size * 0.22, size * 0.2, size * 0.36, size * 0.3], fill=c)
        d.rectangle([size * 0.64, size * 0.2, size * 0.78, size * 0.3], fill=c)
        d.line([(size * 0.22, size * 0.52), (size * 0.38, size * 0.52)], fill=c, width=lw)
        d.line([(size * 0.6, size * 0.52), (size * 0.76, size * 0.52)], fill=c, width=lw)
        d.line([(size * 0.68, size * 0.44), (size * 0.68, size * 0.6)], fill=c, width=lw)
    elif kind == "brake":
        d.ellipse([size * 0.2, size * 0.2, size * 0.8, size * 0.8], outline=c, width=lw)
        d.arc([size * 0.05, size * 0.12, size * 0.4, size * 0.88], 120, 240, fill=c, width=lw)
        d.arc([size * 0.6, size * 0.12, size * 0.95, size * 0.88], -60, 60, fill=c, width=lw)
        d.line([(size / 2, size * 0.32), (size / 2, size * 0.56)], fill=c, width=lw)
        d.ellipse([size / 2 - lw, size * 0.64, size / 2 + lw, size * 0.64 + 2 * lw], fill=c)
    elif kind == "engine":
        pts = [(0.12, 0.42), (0.28, 0.42), (0.34, 0.32), (0.62, 0.32), (0.68, 0.42), (0.8, 0.42), (0.8, 0.36),
               (0.9, 0.36), (0.9, 0.72), (0.8, 0.72), (0.8, 0.66), (0.66, 0.78), (0.3, 0.78), (0.24, 0.68),
               (0.12, 0.68)]
        d.line([(x * size, y * size) for x, y in pts + pts[:1]], fill=c, width=lw, joint="curve")
        d.line([(size * 0.4, size * 0.32), (size * 0.4, size * 0.22), (size * 0.58, size * 0.22)], fill=c, width=lw)
    else:
        d.polygon([(size / 2, size * 0.12), (size * 0.92, size * 0.86), (size * 0.08, size * 0.86)], outline=c,
                  width=lw)
        d.line([(size / 2, size * 0.38), (size / 2, size * 0.62)], fill=c, width=lw + 1)
        d.ellipse([size / 2 - lw, size * 0.7, size / 2 + lw, size * 0.7 + 2 * lw], fill=c)
    return img


def icon_for(text: str) -> str:
    low = (text or "").lower()
    if "batter" in low or "batteri" in low or "bateria" in low:
        return "battery"
    if "brake" in low or "frein" in low or "freio" in low:
        return "brake"
    if any(k in low for k in ("engine", "moteur", "motor", "huile", "oil", "óleo", "chaîne", "chaine", "courroie",
                              "timing", "turbo", "injecteur", "injector")):
        return "engine"
    return "alert"


class Warning(Magnet):  # noqa: A001 - nome do template
    """Luz de alerta do painel acendendo (âmbar, com brilho) e o defeito citado (``keyword``) +
    ``subtitle`` (o número, se a fala deu)."""

    BG_MEDIA = True

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        self.kind = icon_for(spec.keyword)
        self.size = round(260 * u)
        self.amber = (255, 176, 0)
        self.icon = warning_icon(self.kind, self.size, self.amber)
        self.glow = self.icon.filter(ImageFilter.GaussianBlur(round(22 * u)))
        self.head = tr("fault", spec.lang)
        self.lab = label(self.sk, spec.keyword, u, w * 0.6, 50)
        self.sub = label(self.sk, spec.subtitle, u, w * 0.6, 34) if spec.subtitle else None

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh_soft", 0.3), (0.35, "ding", 0.35), (0.95, "ding", 0.25), (1.2, "swipe", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        # mostrador escuro redondo
        r = self.size * 0.95
        cx, cy = w * 0.3, h * 0.47
        dial = Image.new("RGBA", (round(2 * r), round(2 * r)), (0, 0, 0, 0))
        dd = ImageDraw.Draw(dial)
        dd.ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=(12, 13, 16, 235), outline=(70, 74, 84, 255),
                   width=max(2, round(5 * u)))
        qd = gfx.ease_out_back(gfx.progress(t, 0.0, 0.4), 1.3)
        gfx.paste_center(c, gfx.scaled(dial, max(0.05, qd)), cx, cy, alpha=min(1.0, qd * 2))
        on = t >= 0.35 and (t < 0.6 or t >= 0.95 or (0.75 <= t < 0.85))  # pisca duas vezes e fica
        pulse = 0.75 + 0.25 * math.sin(t * 5.0)
        if on:
            gfx.paste_center(c, self.glow, cx, cy, alpha=0.9 * pulse)
            gfx.paste_center(c, self.icon, cx, cy)
        else:
            gfx.paste_center(c, self.icon, cx, cy, alpha=0.12)
        x0 = w * 0.52
        qh = gfx.ease_out_cubic(gfx.progress(t, 0.5, 0.4))
        head = big_number(self.sk, self.sk.txt(self.head), u, 80)
        gfx.paste_at(c, head, x0 + (1 - qh) * 60 * u, h * 0.3, alpha=qh)
        y = h * 0.3 + head.height + 20 * u
        if self.lab is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, 0.7, 0.4))
            gfx.paste_at(c, self.lab, x0 + (1 - ql) * 60 * u, y, alpha=ql)
            y += self.lab.height + 16 * u
        if self.sub is not None:
            qs = gfx.ease_out_cubic(gfx.progress(t, 0.95, 0.4))
            gfx.paste_at(c, self.sub, x0 + (1 - qs) * 60 * u, y, alpha=qs)
        return self.done(c, t)


# ----------------------------------------------------------------- textmask
class TextMask(Magnet):
    """A palavra gigante (``keyword``) com o vídeo (``media[0]``) passando dentro das letras; ela entra
    grande e se afasta, com contorno na cor de destaque; ``subtitle`` pequeno embaixo."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        word = self.sk.txt(spec.keyword or "") or "•"
        f = gfx.font("black" if self.sk.name != "paper" else "typewriter", round(420 * u))
        while gfx.text_width(word, f) > w * 0.9 and f.size > 90 * u:
            f = gfx.font("black" if self.sk.name != "paper" else "typewriter", f.size - 12)
        asc, desc = f.getmetrics()
        tw = gfx.text_width(word, f)
        self.mw, self.mh = tw + round(20 * u), asc + desc
        mask = Image.new("L", (self.mw, self.mh), 0)
        ImageDraw.Draw(mask).text((round(10 * u), 0), word, font=f, fill=255)
        self.mask = mask
        edge = mask.filter(ImageFilter.MaxFilter(max(3, round(7 * u)) | 1))
        self.outline = Image.new("RGBA", mask.size, (*self.sk.accent, 255))
        self.outline.putalpha(Image.eval(edge, lambda v: v))
        pad = round(40 * u)  # sombra com o formato das letras (não um retângulo)
        sh = Image.new("L", (self.mw + 2 * pad, self.mh + 2 * pad), 0)
        sh.paste(edge, (pad, pad + round(14 * u)))
        sh = sh.filter(ImageFilter.GaussianBlur(round(18 * u))).point(lambda v: v * 3 // 4)
        self.shadow = Image.new("RGBA", sh.size, (0, 0, 0, 255))
        self.shadow.putalpha(sh)
        self.video = self.src(spec.media[0], self.mw, self.mh, zoom=1.15) if spec.media else None
        self.sub = label(self.sk, spec.subtitle, u, w * 0.7, 36) if spec.subtitle else None

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh", 0.5), (0.45, "impact_soft", 0.45)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        fill = self.video.frame(i).convert("RGB") if self.video is not None else \
            Image.new("RGB", (self.mw, self.mh), self.sk.accent)
        word = Image.new("RGBA", (self.mw, self.mh), (0, 0, 0, 0))
        word.paste(fill, (0, 0), self.mask)
        pad = round(40 * u)
        layer = Image.new("RGBA", (self.mw + 2 * pad, self.mh + 2 * pad), (0, 0, 0, 0))
        layer.alpha_composite(self.shadow)
        layer.alpha_composite(self.outline, (pad, pad))
        layer.alpha_composite(word, (pad, pad))
        q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.7))
        scale = 1.6 - 0.6 * q - 0.04 * gfx.progress(t, 0.7, max(0.1, self.dur - 0.7))
        gfx.paste_center(c, gfx.scaled(layer, scale), w / 2, h * 0.47, alpha=min(1.0, q * 2))
        if self.sub is not None:
            qs = gfx.ease_out_cubic(gfx.progress(t, 0.8, 0.4))
            gfx.paste_center(c, self.sub, w / 2, h * 0.82 + (1 - qs) * 30 * u, alpha=qs)
        return self.done(c, t)


# --------------------------------------------------------------- photostack
class PhotoStack(Magnet):
    """2–3 fotos (``media``) caindo uma por cima da outra, girando, no centro; ``keyword`` na legenda."""

    BG_MEDIA = True
    DROPS = (0.1, 0.65, 1.2)

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        media = spec.media[:3] or []
        self.pw = round(w * 0.42)
        self.ph = round(self.pw * 0.64)
        self.imgs = [self.src(m, self.pw, self.ph, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0)) for m in media]
        self.angles = [float(self.rng.uniform(-9, 9)) for _ in media]
        self.offs = [(float(self.rng.uniform(-60, 60)) * u, float(self.rng.uniform(-30, 30)) * u) for _ in media]
        self.lab = label(self.sk, spec.keyword, u, w * 0.6, 40)
        self.frozen: dict[int, Image.Image] = {}

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(3, len(spec.media)))
        return [(t + 0.3, "snap", 0.35) for t in cls.DROPS[:n]] + [(1.5, "swipe", 0.25)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        last = max([k for k, s in enumerate(self.DROPS[:len(self.imgs)]) if t >= s] or [0])
        for k, (src, ang, (dx, dy)) in enumerate(zip(self.imgs, self.angles, self.offs)):
            q = gfx.progress(t, self.DROPS[k], 0.4)
            if q <= 0:
                continue
            e = gfx.ease_out_back(q, 1.2)
            if k == last or k not in self.frozen:
                self.frozen[k] = card(self.sk, src.frame(i), u, angle=ang)
            layer = self.frozen[k]
            scale = 1.0 + 0.5 * (1 - e)
            gfx.paste_center(c, gfx.scaled(layer, scale), w / 2 + dx, h * 0.45 + dy - (1 - e) * 40 * u,
                             alpha=min(1.0, q * 3))
        if self.lab is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, 1.2, 0.4))
            gfx.paste_center(c, self.lab, w / 2, h * 0.86 + (1 - ql) * 30 * u, alpha=ql)
        return self.done(c, t)


# -------------------------------------------------------------- splitpanels
class SplitPanels(Magnet):
    """A tela em 3 painéis verticais (fotos diferentes, ou a mesma foto fatiada) entrando de cima e de
    baixo, alternados, com fendas na cor de destaque; ``keyword`` no meio, embaixo."""

    ENTER = (0.0, 0.18, 0.36)

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.gap = round(12 * u)
        self.pw = (w - 2 * self.gap) // 3
        if len(spec.media) >= 3:
            self.srcs = [self.src(m, self.pw, h, zoom=1.08) for m in spec.media[:3]]
            self.single = None
        else:
            self.srcs = []
            self.single = self.src(spec.media[0], w, h, zoom=1.06) if spec.media else None
        self.lab = label(self.sk, spec.keyword, u, w * 0.6, 44)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(t, "whoosh_soft", 0.3) for t in cls.ENTER] + [(0.8, "impact_soft", 0.35)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        w, h = self.w, self.h
        c = Image.new("RGBA", (w, h), (*self.sk.accent, 255)) if self.sk.name != "paper" else self.canvas(i)
        whole = self.single.frame(i).convert("RGB") if self.single is not None else None
        for k in range(3):
            q = gfx.ease_out_cubic(gfx.progress(t, self.ENTER[k], 0.5))
            if q <= 0:
                continue
            x = k * (self.pw + self.gap)
            if whole is not None:
                panel = whole.crop((x, 0, x + self.pw, h))
            elif k < len(self.srcs):
                panel = self.srcs[k].frame(i).convert("RGB")
            else:
                continue
            dy = (1 - q) * h * (1 if k % 2 else -1)
            c.paste(panel, (x, round(dy)))
        if self.lab is not None:
            ql = gfx.ease_out_back(gfx.progress(t, 0.7, 0.45), 1.3)
            gfx.paste_center(c, gfx.scaled(self.lab, max(0.05, ql)), w / 2, h * 0.8, alpha=min(1.0, ql * 2))
        return self.done(c, t)


# ----------------------------------------------------------------- annotate
class Annotate(Magnet):
    """A foto grande (``media[0]``); a mão com a caneta desenha um círculo num ponto dela, depois a seta
    e escreve a anotação (``keyword``) ao lado — como quem explica no quadro."""

    BG_MEDIA = True

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.pw, self.ph = round(w * 0.62), round(w * 0.62 * 0.6)
        self.img = self.src(spec.media[0], self.pw, self.ph, zoom=1.04) if spec.media else None
        self.cx, self.cy = w * 0.42, h * 0.48
        # o círculo num ponto do terço central da foto
        self.tx = self.cx + float(self.rng.uniform(-0.18, 0.18)) * self.pw
        self.ty = self.cy + float(self.rng.uniform(-0.12, 0.12)) * self.ph
        self.r = float(self.rng.uniform(0.11, 0.15)) * self.pw
        self.pen = (196, 46, 34) if self.sk.name == "paper" else self.sk.accent
        self.f_note = gfx.font("script", round(64 * u))
        self.note = " ".join((spec.keyword or "").split())
        self.lx, self.ly = w * 0.8, h * 0.22
        from .templates_brand import HAND_FILE, draw_hand, load_hand

        self.hand, self.tip = load_hand(HAND_FILE, h * 0.42) if HAND_FILE.exists() else draw_hand(u, self.pen)
        self.t_circle, self.t_arrow, self.t_text = 0.5, 1.4, 1.9

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh_soft", 0.3), (0.5, "pencil:0.850", 0.4), (1.4, "pencil:0.450", 0.35),
                (1.9, f"pencil:{max(0.3, min(1.5, len(spec.keyword) / 14)):.3f}", 0.3)]

    def _pen_at(self, t: float) -> tuple[float, float] | None:
        if self.t_circle <= t < self.t_arrow:
            p = (t - self.t_circle) / (self.t_arrow - self.t_circle - 0.05)
            a = -math.pi * 0.6 + 2.1 * math.pi * min(1.0, p)
            return self.tx + self.r * 1.15 * math.cos(a), self.ty + self.r * 0.85 * math.sin(a)
        if self.t_arrow <= t < self.t_text:
            p = min(1.0, (t - self.t_arrow) / (self.t_text - self.t_arrow - 0.05))
            x0, y0 = self.lx - 40 * self.u, self.ly + 50 * self.u
            x1, y1 = self.tx + self.r * 0.9, self.ty - self.r * 0.6
            return x0 + (x1 - x0) * p, y0 + (y1 - y0) * p
        if self.t_text <= t < self.t_text + 1.2 and self.note:
            p = (t - self.t_text) / 1.2
            return self.lx - 60 * self.u + 380 * self.u * p, self.ly + math.sin(t * 18) * 6 * self.u
        return None

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u = self.u
        c = self.canvas(i)
        if self.img is not None:
            q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.4))
            layer = card(self.sk, self.img.frame(i), u)
            gfx.paste_center(c, layer, self.cx, self.cy + (1 - q) * 40 * u, alpha=q)
        d = ImageDraw.Draw(c)
        lw = max(4, round(8 * u))
        pc = (*self.pen, 255)
        pc_ = gfx.progress(t, self.t_circle, self.t_arrow - self.t_circle - 0.05)
        if pc_ > 0:
            pts = []
            steps = 48
            for k in range(int(steps * pc_) + 1):
                a = -math.pi * 0.6 + 2.1 * math.pi * k / steps
                wob = 1 + 0.05 * math.sin(a * 3)
                pts.append((self.tx + self.r * 1.15 * wob * math.cos(a), self.ty + self.r * 0.85 * wob * math.sin(a)))
            if len(pts) > 1:
                d.line(pts, fill=pc, width=lw, joint="curve")
        pa = gfx.progress(t, self.t_arrow, self.t_text - self.t_arrow - 0.05)
        if pa > 0:
            x0, y0 = self.lx - 40 * u, self.ly + 50 * u
            x1, y1 = self.tx + self.r * 0.9, self.ty - self.r * 0.6
            xe, ye = x0 + (x1 - x0) * pa, y0 + (y1 - y0) * pa
            d.line([(x0, y0), (xe, ye)], fill=pc, width=lw)
            if pa >= 1:  # ponta da seta
                ang = math.atan2(y1 - y0, x1 - x0)
                for side in (0.5, -0.5):
                    d.line([(x1, y1), (x1 - 36 * u * math.cos(ang + side), y1 - 36 * u * math.sin(ang + side))],
                           fill=pc, width=lw)
        if self.note and t >= self.t_text:
            n = int(len(self.note) * min(1.0, (t - self.t_text) / 1.2)) + 1
            txt = self.note[:n]
            d.text((self.lx - 60 * u + 3 * u, self.ly - 40 * u + 3 * u), txt, font=self.f_note, fill=(0, 0, 0, 150))
            d.text((self.lx - 60 * u, self.ly - 40 * u), txt, font=self.f_note, fill=pc)
        tip = self._pen_at(t)
        if tip is not None:
            gfx.paste_at(c, self.hand, tip[0] - self.tip[0], tip[1] - self.tip[1])
        return self.done(c, t)


# ------------------------------------------------------------------ wordpop
_STOP = set("the a an and or but of to in on at for with is are was were be been it its this that these those "
            "there here i you we they he she my your our their as by from so if not no do does did have has had "
            "o a os as e ou mas de do da dos das em no na para com que um uma é le la les et ou mais de du des un "
            "une est en pour avec que qui".split())


def strong_words(text: str, limit: int = 6) -> list[str]:
    words = [w.strip(".,;:!?…\"'()") for w in (text or "").split()]
    return [w for w in words if w and w.lower() not in _STOP][:limit]


class WordPop(Magnet):
    """As palavras fortes da fala (``captions``: trechos com o tempo de cada um) pulando uma a uma,
    grandes, no meio da tela; número na cor de destaque. Fundo: a imagem desfocada (ou a pele)."""

    BG_MEDIA = True

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        w = self.w
        words: list[tuple[float, str]] = []
        for t0, t1, txt in spec.captions or []:
            ws = strong_words(txt, 3)
            for k, wd in enumerate(ws):
                words.append((t0 + (t1 - t0) * k / max(1, len(ws)), wd))
        if not words:
            ws = strong_words(spec.body or spec.keyword, 6)
            words = [(0.2 + k * 0.45, wd) for k, wd in enumerate(ws)]
        self.words = words[:8]
        self.cache: dict[str, Image.Image] = {}
        self.max_w = w * 0.86

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = sum(len(strong_words(txt, 3)) for _, _, txt in (spec.captions or [])) or 4
        dur = spec.frames / spec.fps
        return [(min(dur - 0.2, 0.2 + k * max(0.3, dur / (n + 1))), "pop", 0.25) for k in range(min(n, 8))]

    def _word(self, wd: str) -> Image.Image:
        if wd not in self.cache:
            u = self.u
            txt = self.sk.txt(wd)
            numeric = bool(re.search(r"\d", txt))
            size = 230
            f = self.sk.font("title", size, u)
            while gfx.text_width(txt, f) > self.max_w and f.size > 60 * u:
                f = gfx.font(self.sk.title, f.size - 10)
            if self.sk.name in ("blueprint", "kinetic", "tv"):
                fill = (*self.sk.accent, 255) if numeric else (*WHITE, 255)
                img = gfx.outlined_text(txt, f, fill, round(5 * u), (0, 0, 0, 255))
            else:
                img = gfx.text_block([txt], f, self.sk.accent if numeric else self.sk.ink, shadow=3)
            self.cache[wd] = img
        return self.cache[wd]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        if self.sk.name == "kinetic":
            c.alpha_composite(Image.new("RGBA", (w, h), (0, 0, 0, 120)))
        cur = [k for k, (st, _) in enumerate(self.words) if t >= st]
        if cur:
            k = cur[-1]
            st, wd = self.words[k]
            q = gfx.progress(t, st, 0.22)
            e = gfx.ease_out_back(q, 2.2)
            img = self._word(wd)
            if k > 0:  # a anterior sobe e some
                pst, pwd = self.words[k - 1]
                qo = gfx.progress(t, st, 0.2)
                gfx.paste_center(c, gfx.scaled(self._word(pwd), 0.55), w / 2, h * 0.47 - 140 * u * qo - 60 * u,
                                 alpha=0.45 * (1 - qo))
            gfx.paste_center(c, gfx.scaled(img, max(0.05, 0.6 + 0.4 * e)), w / 2, h * 0.5, alpha=min(1.0, q * 3))
            if self.sk.name in ("kinetic", "blueprint", "data"):  # sublinhado que corre
                d = ImageDraw.Draw(c)
                lw = img.width * 0.6 * gfx.ease_out_cubic(gfx.progress(t, st + 0.08, 0.25))
                y = h * 0.5 + img.height * 0.55
                d.rectangle([w / 2 - lw / 2, y, w / 2 + lw / 2, y + 10 * u], fill=(*self.sk.accent, 255))
        return self.done(c, t)


MAGNET_TEMPLATES: dict[str, type[Template]] = {
    "percentbar": PercentBar, "peoplegrid": PeopleGrid, "receipt": Receipt, "downtime": Downtime,
    "warning": Warning, "textmask": TextMask, "photostack": PhotoStack, "splitpanels": SplitPanels,
    "annotate": Annotate, "wordpop": WordPop,
}
# templates de dado (a fala dá o número): desenham sem foto
MAGNET_DATA = ("percentbar", "peoplegrid", "receipt", "downtime", "warning")
# templates de ritmo (quebra de padrão): precisam de imagem (o wordpop usa a imagem só de fundo)
MAGNET_RHYTHM = ("textmask", "photostack", "splitpanels", "annotate", "wordpop")


def magnet_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = MAGNET_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None


def skin_for_style(style: str, rng: Any = None) -> str:
    """Estilo do app -> pele dos templates ("mix": uma pele sorteada por template)."""
    fixed = {"minimal": "blueprint", "dossie": "paper", "documentario": "paper", "broadcast": "tv", "dados": "data",
             "kinetic": "kinetic", "cinema": "cinema"}
    if style in fixed:
        return fixed[style]
    if style in SKINS:
        return style
    return SKINS[int(rng.integers(0, len(SKINS)))] if rng is not None else "blueprint"
