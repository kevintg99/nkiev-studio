"""Família Broadcast (telejornal): o estilo "Broadcast" do app troca os recursos de ritmo por estes.

* ``bulletin``  — plantão: tarja vermelha "BREAKING NEWS" entrando na diagonal, a frase falada numa
  faixa branca palavra por palavra, selo "AO VIVO" com o relógio e a faixa de notícias rolando
  embaixo. A imagem da cena fica desfocada atrás (sem imagem, o fundo do canal). No lugar da tela preta.
* ``report``    — reportagem: estúdio no tom do canal, um telão com a imagem do carro entrando pela
  direita, rótulo "REPORTAGEM" e o nome grande à esquerda, tarja com um trecho da fala embaixo.
  No lugar da foto no blueprint.
* ``videowall`` — parede de monitores: 2 ou 3 telas com imagens do mesmo carro ligando uma a uma
  (efeito de TV de tubo) e a tarja com o nome. No lugar da galeria.
* ``newsrank``  — posição do ranking: faixas vermelhas cruzando, o "#10" gigante num bloco do canal, o
  carro no telão com o selo "AO VIVO" e a tarja com o nome. No lugar do cartão de ranking.
* ``newsfloat`` — foto flutuando sobre a própria imagem desfocada (como a foto que aparece ao lado do
  apresentador), com etiqueta "EM FOCO", o nome grande e a tarja. No lugar da foto no blueprint e do card.
* ``newstimeline`` — linha do tempo de TV: trilho de luz no tom do canal, um monitor por ano subindo quando
  o ano é falado e o ano numa etiqueta vermelha; a câmera anda se houver mais de 3.
* ``newscompare`` — tela dividida: 2–3 monitores com o nome de cada carro numa tarja, o selo "VS" piscando
  e o título na barra de cima. No lugar do comparativo e do versus.

Todos usam o fundo e o destaque do canal (paleta de ``canais/``); o vermelho do "BREAKING" é fixo,
como nos telejornais. Rótulos no idioma do roteiro (``i18n``).
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .i18n import text as tr
from .templates import Template, TemplateSpec
from .templates_auto import _blur_frame, _blurred_source
from .templates_pro import item_starts

Cue = tuple[float, str, float]
NEWS_RED = (206, 24, 34)
NEWS_DARK = (14, 16, 22)
WHITE = (255, 255, 255)
INK = (18, 18, 22)


# ------------------------------------------------------------------ peças
def clock_text(seed: int) -> str:
    """Hora do selo "AO VIVO" (fixa por vídeo: o relógio de verdade mudaria a cada render)."""
    return f"{8 + seed % 12:02d}:{(seed * 7) % 60:02d}"


def live_bug(u: float, lang: str, t: float, clock: str = "", news: bool = True) -> Image.Image:
    """Selo vermelho "● AO VIVO" (o ponto pisca) + o relógio numa caixa escura. Fala que não é notícia
    (``news=False``): selo "ANÁLISE" na cor do canal, sem piscar e sem relógio."""
    f = gfx.font("oswald_bold", round(34 * u))
    if not news:
        label = tr("analysis", lang)
        pad_x, h = round(18 * u), round(54 * u)
        bar = max(4, round(8 * u))
        img = Image.new("RGBA", (round(gfx.text_width(label, f) + 2 * pad_x + bar), h), (*NEWS_DARK, 235))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, bar, h], fill=(*gfx.accent(), 255))
        asc, desc = f.getmetrics()
        d.text((bar + pad_x, (h - asc - desc) / 2 - 2 * u), label, font=f, fill=(*WHITE, 255))
        return img
    label = tr("live", lang)
    pad_x, h = round(18 * u), round(54 * u)
    dot = round(14 * u)
    lw = round(gfx.text_width(label, f) + 2 * pad_x + dot + 12 * u)
    cw = round(gfx.text_width(clock, f) + 2 * pad_x) if clock else 0
    img = Image.new("RGBA", (lw + cw, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, lw, h], fill=(*NEWS_RED, 255))
    a = round(255 * (0.55 + 0.45 * (0.5 + 0.5 * math.cos(t * 5.0))))
    cy = h / 2
    d.ellipse([pad_x, cy - dot / 2, pad_x + dot, cy + dot / 2], fill=(255, 255, 255, a))
    asc, desc = f.getmetrics()
    d.text((pad_x + dot + 12 * u, (h - asc - desc) / 2 - 2 * u), label, font=f, fill=(*WHITE, 255))
    if clock:
        d.rectangle([lw, 0, lw + cw, h], fill=(*NEWS_DARK, 235))
        d.text((lw + pad_x, (h - asc - desc) / 2 - 2 * u), clock, font=f, fill=(*WHITE, 255))
    return img


def studio_background(tpl: Template) -> Image.Image:
    """Estúdio no tom do canal: degradê, brilho atrás do telão, feixes de luz e pontos desfocados."""
    w, h, u = tpl.w, tpl.h, tpl.u
    base = np.array(tpl.th["bg"], np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    shade = 1.25 - 0.6 * (yy / h)
    glow = np.exp(-(((xx - w * 0.66) / (w * 0.35)) ** 2 + ((yy - h * 0.42) / (h * 0.45)) ** 2))
    acc = np.array(gfx.accent(), np.float32)
    arr = base[None, None, :] * shade[..., None] + acc[None, None, :] * glow[..., None] * 0.10
    arr += 255 * glow[..., None] * 0.04
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    beams = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(beams)
    rng = tpl.rng
    for k in range(5):  # feixes de luz descendo na diagonal
        x = w * (0.12 + 0.2 * k) + float(rng.uniform(-40, 40)) * u
        spread = float(rng.uniform(60, 140)) * u
        d.polygon([(x - 18 * u, 0), (x + 18 * u, 0), (x + spread + 240 * u, h), (x + 120 * u, h)],
                  fill=(255, 255, 255, 14))
    for _ in range(14):  # pontos de luz desfocados (bokeh)
        cx, cy = float(rng.uniform(0, w)), float(rng.uniform(0, h * 0.7))
        r = float(rng.uniform(10, 36)) * u
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*gfx.accent(), 26))
    beams = beams.filter(ImageFilter.GaussianBlur(round(18 * u)))
    img.alpha_composite(beams)
    # piso: linhas finas em perspectiva no terço de baixo
    floor = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(floor)
    for k in range(7):
        y = h * 0.78 + (k ** 1.7) * 9 * u
        fd.line([(0, y), (w, y)], fill=(255, 255, 255, 10), width=max(1, round(u)))
    img.alpha_composite(floor)
    return img


def screen_frame(content: Image.Image, u: float, live: Image.Image | None = None) -> Image.Image:
    """Moldura de monitor: borda escura com cantos arredondados e um filete de luz embaixo."""
    pad = max(6, round(12 * u))
    w, h = content.width + 2 * pad, content.height + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=round(16 * u), fill=(20, 22, 28, 255),
                        outline=(70, 74, 84, 255), width=max(1, round(2 * u)))
    img.paste(content.convert("RGB"), (pad, pad))
    d.rectangle([pad, h - pad + 2 * u, w - pad, h - pad + 5 * u], fill=(*gfx.accent(), 255))
    if live is not None:
        img.alpha_composite(live, (pad + round(16 * u), pad + round(16 * u)))
    return img


def chyron(u: float, w: int, label: str, text: str, q: float) -> Image.Image | None:
    """Tarja de baixo: bloco vermelho com o rótulo + faixa escura com o texto, abrindo da esquerda."""
    if q <= 0:
        return None
    h = round(84 * u)
    f_lab = gfx.font("oswald_bold", round(40 * u))
    f_txt = gfx.font("montserrat_bold", round(38 * u))
    lab_w = round(gfx.text_width(label, f_lab) + 56 * u)
    text = gfx.wrap(text.upper(), f_txt, round(w - lab_w - 60 * u), 1)[0] if text else ""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    full = round(w * gfx.ease_out_cubic(q))
    d.rectangle([0, 0, full, h], fill=(*NEWS_DARK, 232))
    d.rectangle([0, 0, min(full, lab_w), h], fill=(*NEWS_RED, 255))
    d.rectangle([0, h - max(3, round(5 * u)), full, h], fill=(*gfx.accent(), 255))
    asc, desc = f_lab.getmetrics()
    qt = gfx.progress(q, 0.45, 0.55)
    if qt > 0:
        a = round(255 * qt)
        d.text((28 * u, (h - asc - desc) / 2 - 2 * u), label, font=f_lab, fill=(*WHITE, a))
        a2, d2 = f_txt.getmetrics()
        d.text((lab_w + 30 * u, (h - a2 - d2) / 2), text, font=f_txt, fill=(*WHITE, a))
    return img


# ---------------------------------------------------------------- bulletin
class Bulletin(Template):
    """Plantão "BREAKING NEWS" com a frase falada (``body``; sem ele, ``keyword``). Só é plantão quando a
    fala é notícia (``tone == "news"``); senão é o "PONTO-CHAVE" do estúdio, na cor do canal, sem
    "AO VIVO" e com a faixa "EM FOCO"."""

    SLAB, PANEL = 0.12, 0.34

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.src_bg = _blurred_source(self, spec.media[0], zoom=1.12) if spec.media else None
        self.plain = None if self.src_bg is not None else studio_background(self)
        self.text = " ".join((spec.body or spec.keyword or "").split())
        self.f_slab = gfx.font("oswald_bold", round(78 * u))
        self.news = spec.tone == "news"
        self.slab_text = tr("breaking" if self.news else "key_point", spec.lang)
        self.slab_color = NEWS_RED if self.news else tuple(gfx.accent())
        self.x0, self.y0 = round(w * 0.07), round(h * 0.29)
        self.panel_w = round(w * 0.86)
        size = 84
        self.f = gfx.font("black", round(size * u))
        lines = gfx.wrap(self.text, self.f, self.panel_w - round(80 * u), 2)
        if len(" ".join(lines)) < len(self.text):  # não coube em 2 linhas: letra menor, até 3
            self.f = gfx.font("black", round(66 * u))
            lines = gfx.wrap(self.text, self.f, self.panel_w - round(80 * u), 3)
        asc, desc = self.f.getmetrics()
        self.lh = round((asc + desc) * 1.06)
        self.lines = lines
        self.panel_h = round(self.lh * len(lines) + 70 * u)
        self.words: list[tuple[str, float, float]] = []
        space = gfx.text_width(" ", self.f)
        top = self.y0 + round(150 * u)
        for k, ln in enumerate(lines):
            x = self.x0 + 40 * u
            for wd in ln.split():
                self.words.append((wd, x, top + 35 * u + k * self.lh))
                x += gfx.text_width(wd, self.f) + space
        self.panel_y = top
        dur = spec.frames / self.fps
        self.step = min(0.16, max(0.05, dur * 0.35 / max(1, len(self.words))))
        self.clock = clock_text(spec.seed)
        crawl = " • ".join(x for x in (spec.keyword.upper(), self.text.upper()) if x)
        self.f_crawl = gfx.font("montserrat_bold", round(34 * u))
        self.crawl = (crawl + "   •   ") * 4
        self.crawl_w = gfx.text_width(crawl + "   •   ", self.f_crawl)
        self.latest = tr("latest" if self.news else "spotlight", spec.lang)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len((spec.body or spec.keyword or "").split())
        dur = spec.frames / spec.fps
        step = min(0.16, max(0.05, dur * 0.35 / max(1, n)))
        out: list[Cue] = [(0.0, "whoosh", 0.5), (cls.SLAB + 0.1, "impact_soft", 0.5), (cls.PANEL, "swipe", 0.3)]
        return out + [(cls.PANEL + 0.2 + k * step, "tick", 0.14) for k in range(min(n, 12))]

    def _slab(self, q: float) -> Image.Image:
        u = self.u
        asc, desc = self.f_slab.getmetrics()
        tw = gfx.spaced_width(self.slab_text, self.f_slab, 0.04)
        sw, sh = round(tw + 120 * u), round(asc + desc + 30 * u)
        slant = round(34 * u)
        img = Image.new("RGBA", (sw + slant, sh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.polygon([(slant, 0), (sw + slant, 0), (sw, sh), (0, sh)], fill=(*self.slab_color, 255))
        if q > 0.5:
            gfx.draw_spaced(d, (slant + 50 * u, (sh - asc - desc) / 2 - 3 * u), self.slab_text, self.f_slab,
                            (*WHITE, round(255 * min(1.0, (q - 0.5) * 3))), tracking=0.04)
        return img

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        if self.src_bg is not None:
            canvas = _blur_frame(self.src_bg.frame(i), w, h, 0.38).convert("RGBA")
        else:
            canvas = self.plain.copy()
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 70))
        canvas.alpha_composite(shade)
        d = ImageDraw.Draw(canvas)
        # faixas vermelhas cruzando a tela na entrada
        qs = gfx.progress(t, 0.0, 0.32)
        if 0 < qs < 1:
            x = -w * 0.3 + qs * w * 1.6
            for k, (dy, th) in enumerate(((0.2, 22), (0.62, 10))):
                y = h * dy
                d.polygon([(x - 400 * u, y), (x, y), (x - 60 * u, y + th * u * 2), (x - 460 * u, y + th * u * 2)],
                          fill=(*self.slab_color, 200 - 70 * k))
        # tarja "BREAKING NEWS"
        q1 = gfx.ease_out_cubic(gfx.progress(t, self.SLAB, 0.3))
        if q1 > 0:
            slab = self._slab(gfx.progress(t, self.SLAB, 0.45))
            gfx.paste_at(canvas, slab, self.x0 - (1 - q1) * (slab.width + self.x0), self.y0)
        # faixa branca com a frase
        q2 = gfx.ease_out_cubic(gfx.progress(t, self.PANEL, 0.3))
        if q2 > 0:
            pw = self.panel_w * q2
            d.rectangle([self.x0, self.panel_y, self.x0 + pw, self.panel_y + self.panel_h], fill=(*WHITE, 246))
            d.rectangle([self.x0, self.panel_y + self.panel_h, self.x0 + pw, self.panel_y + self.panel_h + 9 * u],
                        fill=(*gfx.accent(), 255))
            for k, (wd, x, y) in enumerate(self.words):
                qw = gfx.progress(t, self.PANEL + 0.2 + k * self.step, 0.16)
                if qw > 0 and x < self.x0 + pw:
                    dy = (1 - gfx.ease_out_cubic(qw)) * 18 * u
                    d.text((x, y + dy), gfx.safe_text(wd), font=self.f, fill=(*INK, round(255 * qw)))
        # selo AO VIVO + relógio
        qb = gfx.progress(t, 0.15, 0.25)
        if qb > 0:
            gfx.paste_at(canvas, live_bug(u, self.s.lang, t, self.clock, news=self.news), 60 * u, 56 * u, alpha=qb)
        self._ticker(canvas, t)
        return canvas

    def _ticker(self, canvas: Image.Image, t: float) -> None:
        u, w, h = self.u, self.w, self.h
        q = gfx.ease_out_cubic(gfx.progress(t, 0.25, 0.3))
        if q <= 0:
            return
        sh = round(64 * u)
        y = h - round(118 * u) + (1 - q) * 90 * u
        strip = Image.new("RGBA", (w, sh), (*NEWS_DARK, 235))
        d = ImageDraw.Draw(strip)
        asc, desc = self.f_crawl.getmetrics()
        x = w * 0.2 - ((t * 170 * u) % max(1.0, self.crawl_w))
        d.text((x, (sh - asc - desc) / 2), self.crawl, font=self.f_crawl, fill=(*WHITE, 255))
        lab_f = gfx.font("oswald_bold", round(36 * u))
        lab_w = round(gfx.text_width(self.latest, lab_f) + 50 * u)
        d.rectangle([0, 0, lab_w, sh], fill=(*self.slab_color, 255))
        la, ld = lab_f.getmetrics()
        d.text((25 * u, (sh - la - ld) / 2 - 2 * u), self.latest, font=lab_f, fill=(*WHITE, 255))
        canvas.alpha_composite(strip, (0, round(y)))


# ------------------------------------------------------------------ report
class Report(Template):
    """Reportagem: telão com a imagem (``media[0]``), rótulo e nome (``keyword``) à esquerda e a
    tarja com ``subtitle`` (trecho da fala) embaixo."""

    SCREEN = 0.1

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = studio_background(self)
        self.sw = round(w * 0.54)
        self.sh = round(self.sw * 9 / 16)
        self.img = self.src(spec.media[0], self.sw, self.sh, zoom=1.08,
                            pan=(float(self.rng.uniform(-1, 1)), 0.0)) if spec.media else None
        self.cx, self.cy = w * 0.665, h * 0.43
        f_name = gfx.font("black", round(92 * u))
        name = (spec.keyword or "").upper()
        lines = gfx.wrap(name, f_name, round(w * 0.26), 3)
        if len(lines) == 3:
            f_name = gfx.font("black", round(74 * u))
            lines = gfx.wrap(name, f_name, round(w * 0.27), 3)
        self.name = gfx.text_block(lines, f_name, WHITE, shadow=3) if name else None
        f_lab = gfx.font("oswald_bold", round(34 * u))
        self.news = spec.tone == "news"  # "REPORTAGEM" só quando a fala é notícia; senão "ANÁLISE"
        self.label = gfx.chip(tr("report" if self.news else "analysis", spec.lang), f_lab,
                              NEWS_RED if self.news else tuple(gfx.accent()), WHITE, round(w * 0.25), round(16 * u),
                              round(6 * u))
        self.clock = clock_text(spec.seed) if self.news else tr("spotlight", spec.lang)
        self.bottom = " ".join((spec.subtitle or spec.keyword or "").split())

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh", 0.45), (cls.SCREEN + 0.4, "impact_soft", 0.35), (0.55, "swipe", 0.25),
                (0.95, "whoosh_soft", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.copy()
        q = gfx.ease_out_back(gfx.progress(t, self.SCREEN, 0.5), 1.2)
        if self.img is not None and q > 0:
            content = self.img.frame(i)
            screen = screen_frame(content, u, live_bug(u * 0.8, self.s.lang, t) if self.news else None)
            screen = gfx.with_shadow(screen, 0.0, round(26 * u), (0, round(22 * u)), 160)
            x = self.cx + (1 - q) * w * 0.45
            gfx.paste_center(canvas, gfx.scaled(screen, 0.94 + 0.06 * min(1.0, q)), x, self.cy,
                             alpha=min(1.0, gfx.progress(t, self.SCREEN, 0.25)))
        x0 = w * 0.065
        ql = gfx.ease_out_cubic(gfx.progress(t, 0.35, 0.3))
        if ql > 0:
            gfx.paste_at(canvas, self.label, x0 - (1 - ql) * 80 * u, h * 0.24, alpha=ql)
        qn = gfx.ease_out_cubic(gfx.progress(t, 0.5, 0.35))
        if self.name is not None and qn > 0:
            y = h * 0.24 + self.label.height + 26 * u
            gfx.paste_at(canvas, self.name, x0 - (1 - qn) * 120 * u, y, alpha=qn)
            d = ImageDraw.Draw(canvas)
            by = y + self.name.height + 20 * u
            d.rectangle([x0, by, x0 + 150 * u * qn, by + 9 * u], fill=(*gfx.accent(), 255))
        strap = chyron(u, round(w * 0.87), self.clock, self.bottom, gfx.progress(t, 0.85, 0.55))
        if strap is not None:
            gfx.paste_at(canvas, strap, x0, h * 0.84)
        return canvas


# --------------------------------------------------------------- videowall
class VideoWall(Template):
    """Parede de monitores: 2–3 imagens do mesmo carro (``media``) ligando uma a uma, nome na tarja."""

    ON = (0.1, 0.4, 0.7)
    POWER = 0.32

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = studio_background(self)
        media = spec.media[:3]
        gap = round(26 * u)
        top, bottom = round(h * 0.09), round(h * 0.78)
        left, right = round(w * 0.06), round(w * 0.94)
        if len(media) >= 3:  # um grande à esquerda e dois empilhados à direita
            big_w = round((right - left - gap) * 0.64)
            small_w = right - left - gap - big_w
            small_h = round((bottom - top - gap) / 2)
            self.boxes = [(left, top, big_w, bottom - top), (left + big_w + gap, top, small_w, small_h),
                          (left + big_w + gap, top + small_h + gap, small_w, small_h)]
        else:  # dois lado a lado
            bw = round((right - left - gap) / 2)
            bh = min(bottom - top, round(bw * 9 / 16))
            y = top + round((bottom - top - bh) / 2)
            self.boxes = [(left, y, bw, bh), (left + bw + gap, y, bw, bh)][: max(1, len(media))]
        pad = max(6, round(12 * u))
        self.srcs = [self.src(m, bw - 2 * pad, bh - 2 * pad, zoom=1.08, pan=(float(self.rng.uniform(-1, 1)), 0.0))
                     for m, (_, _, bw, bh) in zip(media, self.boxes)]
        self.pad = pad
        self.clock = clock_text(spec.seed) if spec.tone == "news" else tr("spotlight", spec.lang)
        self.name = " ".join((spec.keyword or "").split())

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = min(3, max(1, len(spec.media)))
        return [(t, "snap", 0.3) for t in cls.ON[:n]] + [(1.05, "whoosh_soft", 0.35)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.copy()
        d = ImageDraw.Draw(canvas)
        for k, ((x, y, bw, bh), src) in enumerate(zip(self.boxes, self.srcs)):
            on = gfx.progress(t, self.ON[k], self.POWER)
            d.rounded_rectangle([x, y, x + bw, y + bh], radius=round(14 * u), fill=(20, 22, 28, 255),
                                outline=(70, 74, 84, 255), width=max(1, round(2 * u)))
            if on <= 0:
                continue
            ix, iy, iw, ih = x + self.pad, y + self.pad, bw - 2 * self.pad, bh - 2 * self.pad
            if on < 0.45:  # TV de tubo ligando: linha branca que abre na vertical
                q = on / 0.45
                lh = max(2, round(ih * q * q))
                d.rectangle([ix + iw * (0.5 - 0.5 * min(1.0, q * 3)), iy + ih / 2 - lh / 2,
                             ix + iw * (0.5 + 0.5 * min(1.0, q * 3)), iy + ih / 2 + lh / 2], fill=(235, 240, 255, 255))
                continue
            content = src.frame(i).convert("RGBA")
            flash = 1 - gfx.progress(on, 0.45, 0.55)
            if flash > 0:
                content.alpha_composite(Image.new("RGBA", content.size, (255, 255, 255, round(150 * flash))))
            canvas.paste(content.convert("RGB"), (round(ix), round(iy)))
            d.rectangle([ix, iy + ih - 5 * u, ix + iw, iy + ih], fill=(*gfx.accent(), 255))
            if k == 0 and self.s.tone == "news":
                gfx.paste_at(canvas, live_bug(u * 0.8, self.s.lang, t), ix + 16 * u, iy + 16 * u)
        strap = chyron(u, round(w * 0.88), self.clock, self.name, gfx.progress(t, 0.95, 0.55))
        if strap is not None:
            gfx.paste_at(canvas, strap, w * 0.06, h * 0.845)
        return canvas


# ---------------------------------------------------------------- newsrank
def red_sweep(canvas: Image.Image, t: float, u: float, t0: float = 0.0, dur: float = 0.34) -> None:
    """Duas faixas vermelhas cruzando a tela na diagonal (abertura de bloco do telejornal)."""
    q = gfx.progress(t, t0, dur)
    if not 0 < q < 1:
        return
    w, h = canvas.size
    d = ImageDraw.Draw(canvas)
    x = -w * 0.3 + q * w * 1.6
    for k, (dy, th) in enumerate(((0.18, 26), (0.66, 12))):
        y = h * dy
        d.polygon([(x - 460 * u, y), (x, y), (x - 60 * u, y + th * u * 2), (x - 520 * u, y + th * u * 2)],
                  fill=(*NEWS_RED, 210 - 70 * k))


def name_tag(text: str, u: float, max_w: float, size: float = 40) -> Image.Image:
    """Tarja pequena com o nome (faixa escura + filete do canal), para cada monitor."""
    f = gfx.font("montserrat_bold", round(size * u))
    text = gfx.wrap(" ".join(text.split()).upper(), f, round(max_w - 40 * u), 1)[0] if text else ""
    asc, desc = f.getmetrics()
    w = round(min(max_w, gfx.text_width(text, f) + 44 * u))
    h = round(asc + desc + 26 * u)
    img = Image.new("RGBA", (max(1, w), h), (*NEWS_DARK, 236))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, max(3, round(8 * u)), h], fill=(*NEWS_RED, 255))
    d.rectangle([0, h - max(3, round(5 * u)), w, h], fill=(*gfx.accent(), 255))
    d.text((24 * u, (h - asc - desc) / 2 - 2 * u), text, font=f, fill=(*WHITE, 255))
    return img


class NewsRank(Template):
    """Posição do ranking no telejornal: "#N" (``rank``) gigante, o carro (``media[0]``) no telão e o
    nome (``keyword``) na tarja; ``subtitle`` vai junto na tarja."""

    NUM, SCREEN = 0.18, 0.35

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = studio_background(self)
        self.sw = round(w * 0.5)
        self.sh = round(self.sw * 9 / 16)
        self.img = self.src(spec.media[0], self.sw, self.sh, zoom=1.08,
                            pan=(float(self.rng.uniform(-1, 1)), 0.0)) if spec.media else None
        num = f"#{spec.rank}" if spec.rank else "#"
        f_num = gfx.font("black", round((300 if len(num) <= 2 else 250) * u))
        tw = gfx.text_width(num, f_num)
        asc, desc = f_num.getmetrics()
        self.block_w, self.block_h = round(max(tw + 110 * u, w * 0.3)), round(h * 0.56)
        block = Image.new("RGBA", (self.block_w, self.block_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(block)
        slant = round(40 * u)
        d.polygon([(slant, 0), (self.block_w, 0), (self.block_w - slant, self.block_h), (0, self.block_h)],
                  fill=(*NEWS_DARK, 240))
        d.rectangle([0, self.block_h - round(14 * u), self.block_w, self.block_h], fill=(*gfx.accent(), 255))
        d.text(((self.block_w - tw) / 2, (self.block_h - asc - desc) / 2 - 10 * u), num, font=f_num,
               fill=(*WHITE, 255))
        self.block = block
        self.top = gfx.chip(tr("ranking", spec.lang), gfx.font("oswald_bold", round(40 * u)), NEWS_RED, WHITE,
                            round(w * 0.3), round(18 * u), round(6 * u))
        self.clock = clock_text(spec.seed)
        name = " ".join((spec.keyword or "").split())
        sub = " ".join((spec.subtitle or "").split())
        self.strap_text = f"{name} — {sub}" if name and sub else name or sub

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh", 0.5), (cls.NUM + 0.2, "impact", 0.6), (cls.SCREEN + 0.3, "whoosh_soft", 0.35),
                (0.9, "swipe", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.copy()
        if self.img is not None:
            q = gfx.ease_out_back(gfx.progress(t, self.SCREEN, 0.5), 1.15)
            if q > 0:
                screen = screen_frame(self.img.frame(i), u,
                                      live_bug(u * 0.8, self.s.lang, t) if self.s.tone == "news" else None)
                screen = gfx.with_shadow(screen, 0.0, round(26 * u), (0, round(22 * u)), 160)
                gfx.paste_center(canvas, gfx.scaled(screen, 0.94 + 0.06 * min(1.0, q)),
                                 w * 0.665 + (1 - q) * w * 0.45, h * 0.43,
                                 alpha=min(1.0, gfx.progress(t, self.SCREEN, 0.25)))
        qn = gfx.ease_out_back(gfx.progress(t, self.NUM, 0.45), 1.3)
        if qn > 0:
            x = w * 0.05 - (1 - qn) * (self.block_w + w * 0.05)
            gfx.paste_at(canvas, self.block, x, h * 0.16)
            punch = 1 + 0.12 * (1 - gfx.progress(t, self.NUM + 0.2, 0.25)) if t > self.NUM + 0.2 else 1
            if punch > 1.001:  # o número "bate" na tela
                glow = Image.new("RGBA", (w, h), (*gfx.accent(), round(60 * (punch - 1) / 0.12)))
                canvas.alpha_composite(glow)
        qt = gfx.ease_out_cubic(gfx.progress(t, self.NUM + 0.25, 0.3))
        if qt > 0:
            gfx.paste_at(canvas, self.top, w * 0.05 + 40 * u, h * 0.16 - self.top.height - 14 * u + (1 - qt) * 20 * u,
                         alpha=qt)
        red_sweep(canvas, t, u)
        strap = chyron(u, round(w * 0.9), f"#{self.s.rank}" if self.s.rank else self.clock, self.strap_text,
                       gfx.progress(t, 0.75, 0.55))
        if strap is not None:
            gfx.paste_at(canvas, strap, w * 0.05, h * 0.845)
        return canvas


# --------------------------------------------------------------- newsfloat
class NewsFloat(Template):
    """Foto flutuando (``media[0]``) sobre ela mesma desfocada, etiqueta "EM FOCO", nome (``keyword``)
    grande e a tarja com ``subtitle``."""

    DROP = 0.08

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        ref = spec.media[0] if spec.media else None
        self.src_bg = _blurred_source(self, ref, zoom=1.15) if ref is not None else None
        self.plain = None if self.src_bg is not None else studio_background(self)
        self.pw = round(w * 0.46)
        self.ph = round(self.pw * 9 / 16)
        self.img = self.src(ref, self.pw, self.ph, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0)) \
            if ref is not None else None
        self.side = 1 if self.rng.random() < 0.5 else -1  # foto à direita ou à esquerda do nome
        self.tilt = float(self.rng.uniform(1.5, 3.0)) * -self.side
        self.tag = gfx.chip(tr("spotlight", spec.lang), gfx.font("oswald_bold", round(34 * u)), NEWS_RED, WHITE,
                            round(w * 0.3), round(16 * u), round(6 * u))
        f_name = gfx.font("black", round(86 * u))
        name = (spec.keyword or "").upper()
        lines = gfx.wrap(name, f_name, round(w * 0.36), 3)
        if len(lines) == 3:
            f_name = gfx.font("black", round(68 * u))
            lines = gfx.wrap(name, f_name, round(w * 0.38), 3)
        self.name = gfx.text_block(lines, f_name, WHITE, shadow=4) if name else None
        self.clock = clock_text(spec.seed) if spec.tone == "news" else tr("spotlight", spec.lang)
        self.bottom = " ".join((spec.subtitle or spec.keyword or "").split())

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh_soft", 0.4), (cls.DROP + 0.4, "snap", 0.35), (0.6, "swipe", 0.25),
                (0.95, "whoosh_soft", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        if self.src_bg is not None:
            canvas = _blur_frame(self.src_bg.frame(i), w, h, 0.42).convert("RGBA")
            canvas.alpha_composite(Image.new("RGBA", (w, h), (*NEWS_DARK, 90)))
        else:
            canvas = self.plain.copy()
        photo_x = w * (0.5 + 0.2 * self.side)
        name_x = w * 0.06 if self.side > 0 else w * 0.58  # o nome fica do lado oposto da foto
        if self.img is not None:
            q = gfx.progress(t, self.DROP, 0.5)
            e = gfx.ease_out_back(q, 1.3)
            pad = round(12 * u)
            frame = self.img.frame(i).convert("RGB")
            card = Image.new("RGBA", (frame.width + 2 * pad, frame.height + 2 * pad), (*WHITE, 255))
            card.paste(frame, (pad, pad))
            ImageDraw.Draw(card).rectangle([pad, card.height - pad - 6 * u, card.width - pad, card.height - pad],
                                           fill=(*gfx.accent(), 255))
            if q >= 0.6:
                card.alpha_composite(self.tag, (pad + round(18 * u), pad + round(18 * u)))
            angle = self.tilt * (1 + (1 - e) * 2) + math.sin(t * 0.9) * 0.6
            layer = gfx.with_shadow(card, angle, round(30 * u), (round(12 * u), round(26 * u)), 150)
            bob = math.sin(t * 1.3) * 9 * u
            gfx.paste_center(canvas, gfx.scaled(layer, (0.8 + 0.2 * e) * (1 + 0.01 * math.sin(t * 0.7))),
                             photo_x, h * 0.44 + bob + (1 - e) * h * 0.2, alpha=min(1.0, q * 2))
        qn = gfx.ease_out_cubic(gfx.progress(t, 0.5, 0.4))
        if self.name is not None and qn > 0:
            y = h * 0.44 - self.name.height / 2
            gfx.paste_at(canvas, self.name, name_x - (1 - qn) * 100 * u * self.side, y, alpha=qn)
            d = ImageDraw.Draw(canvas)
            by = y + self.name.height + 18 * u
            d.rectangle([name_x, by, name_x + 160 * u * qn, by + 9 * u], fill=(*gfx.accent(), 255))
        strap = chyron(u, round(w * 0.88), self.clock, self.bottom, gfx.progress(t, 0.85, 0.55))
        if strap is not None:
            gfx.paste_at(canvas, strap, w * 0.06, h * 0.845)
        return canvas


# ------------------------------------------------------------ newstimeline
class NewsTimeline(Template):
    """Linha do tempo de TV: trilho de luz, um monitor por item (``media``/``labels``/``notes``) subindo
    quando o ano é falado (``captions``) e o ano numa etiqueta vermelha. Título (``keyword``) no alto."""

    FIRST = 0.6

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = studio_background(self)
        self.n = n = max(1, min(6, len(spec.media)))
        self.labels = ([str(x) for x in spec.labels] + [""] * n)[:n]
        self.notes = ([str(x) for x in spec.notes] + [""] * n)[:n]
        self.scroll = n > 3
        spacing = w * 0.32 if self.scroll else w * 0.84 / n
        self.xs = [k * spacing for k in range(n)]
        self.cw = round(min(w * 0.25, spacing * 0.84))
        self.ch = round(self.cw * 9 / 16)
        self.imgs = [self.src(m, self.cw, self.ch, zoom=1.06) for m in spec.media[:n]]
        self.starts = item_starts(spec, n, first=self.FIRST)
        self.rail_y = h * 0.66
        self.f_year = gfx.font("oswald_bold", round((64 if n <= 3 else 54) * u))
        self.f_note = gfx.font("montserrat_bold", round(28 * u))
        title = " ".join((spec.keyword or "").split()) or tr("timeline", spec.lang)
        self.title = chyron(u, round(w * 0.9), tr("timeline", spec.lang), title, 1.0) if title else None
        self.frozen: dict[int, Image.Image] = {}

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(6, len(spec.media)))
        out: list[Cue] = [(0.0, "whoosh", 0.4), (0.25, "swipe", 0.3)]
        for s in item_starts(spec, n, first=cls.FIRST):
            out += [(s - 0.05, "whoosh_soft", 0.3), (s + 0.3, "snap", 0.35)]
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
        acc = gfx.accent()
        grow = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.6))
        ry = self.rail_y
        glow = Image.new("RGBA", (w, round(60 * u)), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rectangle([0, round(24 * u), w * grow, round(36 * u)], fill=(*acc, 120))
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(round(10 * u))), (0, round(ry - 30 * u)))
        d.rectangle([0, ry - 3 * u, w * grow, ry + 3 * u], fill=(*acc, 255))
        off = w / 2 - self._cam(t)
        k_act = self._active(t)
        for k in range(self.n):
            st = self.starts[k]
            if t < st - 0.2:
                continue
            sx = self.xs[k] + off
            if sx < -self.cw or sx > w + self.cw:
                continue
            q = gfx.progress(t, st, 0.45)
            e = gfx.ease_out_back(q, 1.3)
            active = k == k_act
            r = (11 if active else 8) * u
            d.ellipse([sx - r, ry - r, sx + r, ry + r], fill=(*(WHITE if active else acc), 255))
            if active or k not in self.frozen:
                self.frozen[k] = gfx.with_shadow(screen_frame(self.imgs[k].frame(i), u * 0.8), 0.0,
                                                 round(18 * u), (0, round(14 * u)), 140)
            layer = self.frozen[k]
            scale = 1.0 if active else 0.84
            cy = ry - 40 * u - layer.height * scale / 2 + (1 - e) * 80 * u
            gfx.paste_center(canvas, gfx.scaled(layer, scale), sx, cy, alpha=min(1.0, q * 2) * (1 if active else 0.8))
            year = self.labels[k].upper()
            if year:
                tag = gfx.chip(year, self.f_year, NEWS_RED if active else NEWS_DARK, WHITE, round(self.cw),
                               round(18 * u), round(4 * u))
                gfx.paste_center(canvas, tag, sx, ry + 30 * u + tag.height / 2, alpha=min(1.0, q * 2))
            note = self.notes[k].upper()
            if note and t >= st + 0.5:
                qn = gfx.progress(t, st + 0.5, 0.3)
                nt = gfx.text_block(gfx.wrap(note, self.f_note, round(self.cw), 2), self.f_note, WHITE, shadow=2)
                gfx.paste_center(canvas, nt, sx, ry + 120 * u + nt.height / 2, alpha=qn)
        if self.title is not None:
            qt = gfx.ease_out_cubic(gfx.progress(t, 0.15, 0.4))
            gfx.paste_at(canvas, self.title, w * 0.05 - (1 - qt) * w, h * 0.07)
        news = self.s.tone == "news"
        bug = live_bug(u * 0.8, self.s.lang, t, clock_text(self.s.seed) if news else "", news=news)
        gfx.paste_at(canvas, bug, w * 0.95 - bug.width, h * 0.07 + 90 * u, alpha=gfx.progress(t, 0.3, 0.25))
        return canvas


# ------------------------------------------------------------- newscompare
class NewsCompare(Template):
    """Tela dividida: 2–3 monitores (``media``) com o nome de cada carro (``labels``) entrando quando é
    falado (``captions``), o selo "VS" e o título (``keyword``) na barra de cima."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.bg = studio_background(self)
        self.n = n = max(2, min(3, len(spec.media)))
        media = (spec.media * 3)[:n]
        self.sw = round(w * (0.28 if n == 3 else 0.41))
        self.sh = round(self.sw * 9 / 16)
        self.imgs = [self.src(m, self.sw, self.sh, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0))
                     for m in media]
        self.xs = [w * x for x in ((0.18, 0.5, 0.82) if n == 3 else (0.27, 0.73))]
        self.y = h * 0.47
        self.labels = ([str(x) for x in spec.labels] + [""] * n)[:n]
        self.starts = item_starts(spec, n, first=0.2, gap=0.4)
        self.vs_t = self.starts[1] + 0.3
        f_vs = gfx.font("black", round(92 * u))
        asc, desc = f_vs.getmetrics()
        r = round(max(gfx.text_width("VS", f_vs), asc + desc) * 0.85)
        vs = Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vs)
        vd.ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=(*NEWS_RED, 255), outline=(*WHITE, 255),
                   width=max(3, round(6 * u)))
        vd.text((r - gfx.text_width("VS", f_vs) / 2, r - (asc + desc) / 2 - 4 * u), "VS", font=f_vs, fill=(*WHITE, 255))
        self.vs = vs
        title = " ".join((spec.keyword or "").split())
        self.title = chyron(u, round(w * 0.9), tr("compare", spec.lang), title, 1.0)
        self.tags = [name_tag(lb, u, self.sw) if lb else None for lb in self.labels]
        self.frozen: dict[int, Image.Image] = {}

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(2, min(3, len(spec.media)))
        starts = item_starts(spec, n, first=0.2, gap=0.4)
        out: list[Cue] = [(0.0, "whoosh", 0.4)]
        out += [(s, "snap", 0.3) for s in starts]
        return out + [(starts[1] + 0.3, "impact", 0.55)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.copy()
        for k in range(self.n):
            st = self.starts[k]
            q = gfx.progress(t, st, 0.45)
            if q <= 0:
                continue
            e = gfx.ease_out_back(q, 1.2)
            active = max([j for j, s in enumerate(self.starts) if t >= s] or [0]) == k
            screen = screen_frame(self.imgs[k].frame(i), u, live_bug(u * 0.7, self.s.lang, t)
                                  if k == 0 and self.s.tone == "news" else None)
            layer = gfx.with_shadow(screen, 0.0, round(22 * u), (0, round(18 * u)), 150)
            dy = (1 - e) * h * 0.35 * (1 if k % 2 else -1)
            gfx.paste_center(canvas, gfx.scaled(layer, 1.0 if active else 0.95), self.xs[k], self.y + dy,
                             alpha=min(1.0, q * 2))
            tag = self.tags[k]
            qt = gfx.ease_out_cubic(gfx.progress(t, st + 0.3, 0.3))
            if tag is not None and qt > 0:
                gfx.paste_at(canvas, tag, self.xs[k] - self.sw / 2 - (1 - qt) * 60 * u,
                             self.y + layer.height / 2 - 10 * u, alpha=qt)
        qv = gfx.progress(t, self.vs_t, 0.3)
        if qv > 0 and self.n >= 2:
            e = gfx.ease_out_back(qv, 2.0)
            cx = (self.xs[0] + self.xs[1]) / 2
            pulse = 1 + 0.04 * math.sin(t * 6)
            gfx.paste_center(canvas, gfx.scaled(self.vs, max(0.01, e * pulse)), cx, self.y, alpha=min(1.0, qv * 2))
        qt = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.4))
        gfx.paste_at(canvas, self.title, w * 0.05 - (1 - qt) * w, h * 0.06)
        red_sweep(canvas, t, u)
        return canvas


NEWS_TEMPLATES: dict[str, type[Template]] = {"bulletin": Bulletin, "report": Report, "videowall": VideoWall,
                                             "newsrank": NewsRank, "newsfloat": NewsFloat,
                                             "newstimeline": NewsTimeline, "newscompare": NewsCompare}
# os novos usam a lógica do template de origem no planejador (mídias, itens, legendas sincronizadas)
NEWS_BASE = {"newsrank": "ranking", "newsfloat": "showcase", "newstimeline": "timeline",
             "newscompare": "comparison"}


def news_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = NEWS_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
