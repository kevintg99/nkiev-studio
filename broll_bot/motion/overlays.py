"""Overlays sobre o b-roll (a imagem continua aparecendo por baixo — estilo documentário).

* ``label``    — caixa escura translúcida com borda fina: "51 PSI" / "MÁXIMO".
* ``pair``     — duas caixas ligadas por uma linha tracejada ("51 PSI MAX" ↔ "40 PSI FRIO").
* ``callout``  — etiqueta com linha curva apontando um detalhe da imagem (o ponto é
  achado por saliência visual) e um arco fino em volta.
* ``badge``    — selo de dado no canto (ícone de velocímetro + "50,3 KM/H").
* ``bullets``  — a mesma lista minimalista do ``panel``, do lado direito.
* ``bignum``   — número gigante no centro com uma frase embaixo ("90%").
* ``tag``      — título + detalhe com linha guia até um marcador amarelo no detalhe da imagem.
* ``panel``    — lista minimalista: véu azul esmaecendo, filete amarelo, título em creme e itens
  numerados entrando um a um.
* ``subscribe`` — CTA: o cursor clica no like, no botão de inscrição (que fica cinza,
  "INSCRITO") e no sininho, que balança. Texto no idioma do roteiro.
* ``keyword``  — legenda minimalista: filete amarelo e a palavra subindo de trás dele.
* ``fuel``     — consumo em destaque: bomba de combustível + "5 L/100 km" contando.
* ``impact``   — véu azul translúcido, filete vertical, palavra grande em creme e a frase embaixo.
* ``file``     — ficha de papel com fita adesiva: "DOSSIER" e o nome do carro datilografado.
* ``phrase``   — frase de destaque embaixo, palavra por palavra, com marca-texto amarelo.
* ``lowerthird`` — tarja de telejornal: bloco vermelho com o nome/palavra e a faixa branca com um
  trecho da fala, abrindo da esquerda (estilo Broadcast).
* ``crawl``    — letreiro do rodapé rolando da direita para a esquerda com frases curtas da fala sobre o
  carro (preço, consumo, peças), continuando entre os cortes da cena (estilo Broadcast).
* ``years``    — ano/modelo do carro: o nome pequeno, os anos grandes subindo de trás de um filete na cor
  de destaque (no canto de cima). Entra sozinho quando a fala cita os anos do carro.
* ``paperyears`` — o mesmo numa ficha de papel datilografada (estilo Dossiê).
* ``papertag`` — legenda numa etiqueta de papel com fita adesiva, datilografada (estilo Dossiê).
* ``ticker``   — barra de manchete embaixo: rótulo "ÚLTIMAS" e a frase de destaque entrando, com a
  faixa fina de notícias rolando (estilo Broadcast).

O shot é renderizado normalmente e depois recebe o overlay numa 2ª passada do FFmpeg
(frames RGBA do Pillow por stdin + filtro ``overlay``).
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..media import FFmpegError
from . import gfx
from .templates_auto import TextCache

OVERLAY_KINDS = ("label", "pair", "callout", "badge", "keyword", "bullets", "bignum", "tag", "panel", "subscribe",
                 "arrow", "fuel", "phrase", "file", "impact", "lowerthird", "ticker", "crawl", "years",
                 "paperyears", "papertag")
YELLOW = (255, 206, 38)
BOX_FILL = (10, 14, 26, 150)
Cue = tuple[float, str, float]


VERTICAL_SAFE_BOTTOM = 0.84  # vídeo em pé: até onde o overlay desenha (fração da altura)


@dataclass
class OverlaySpec:
    kind: str
    w: int
    h: int
    fps: int
    frames: int
    title: str = ""
    detail: str = ""
    title_b: str = ""
    detail_b: str = ""
    lines: list[str] = field(default_factory=list)
    start: float = 0.25  # segundos dentro do shot
    target: tuple[float, float] | None = None  # (x, y) em fração da tela, para o callout
    seed: int = 0
    lang: str = "pt"
    tone: str = ""  # "news": a fala é notícia (a barra diz "ÚLTIMAS"); "": "PONTO-CHAVE"
    # letreiro que atravessa vários shots: segundos já rolados antes deste shot e se ele entra/sai aqui
    offset: float = 0.0
    enter: bool = True
    leave: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OverlaySpec":
        from ..spoken import screen_text

        data = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if data.get("target") is not None:
            data["target"] = tuple(data["target"])
        # texto tirado da fala da voz de IA: "seventy-six point four per cent" vai para a tela como "76.4%"
        lang = str(data.get("lang") or "pt")
        for key in ("title", "detail", "title_b", "detail_b"):
            if isinstance(data.get(key), str):
                data[key] = screen_text(data[key], lang)
        if isinstance(data.get("lines"), list):
            data["lines"] = [screen_text(x, lang) if isinstance(x, str) else x for x in data["lines"]]
        return cls(**data)

    @property
    def seconds(self) -> float:
        return self.frames / self.fps


# ------------------------------------------------------------------- base
class Overlay:
    def __init__(self, spec: OverlaySpec) -> None:
        self.s = spec
        self.w, self.h, self.fps = spec.w, spec.h, spec.fps
        self.u = spec.h / 1080
        if spec.h > spec.w:  # tela em pé: letra na escala da largura (pela altura ficaria larga demais)
            self.u = spec.w / 1080 * 1.15
        self.rng = np.random.default_rng(spec.seed)
        self.t0 = spec.start
        self.t_out = max(self.t0 + 0.8, spec.seconds - 0.3)  # some 0,3 s antes do fim do shot
        gfx.set_number_locale(spec.lang)

    def fit_font(self, weight: str, size: float, text: str, frac: float = 0.88):
        """Fonte no tamanho pedido, menor se o texto não couber na largura (tela em pé é estreita)."""
        fnt = gfx.font(weight, round(size))
        width = gfx.text_width(text or " ", fnt)
        if width > self.w * frac:
            fnt = gfx.font(weight, max(8, round(size * self.w * frac / width)))
        return fnt

    def fade_out(self, t: float) -> float:
        return 1 - gfx.progress(t, self.t_out, 0.28)

    def draw(self, canvas: Image.Image, t: float) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def frame(self, i: int) -> Image.Image:
        canvas = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        t = i / self.fps
        if t >= self.t0 - 1e-6:
            self.draw(canvas, t)
        return canvas

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "pop", 0.5)]

    # ----------------------------------------------------------- helpers
    def box(self, title: str, detail: str = "") -> Image.Image:
        """Caixa escura translúcida com borda branca fina e texto condensado."""
        u = self.u
        f1 = gfx.font("condensed", round(92 * u))
        f2 = gfx.font("condensed", round(62 * u))
        lines = [(title.upper(), f1)] + ([(detail.upper(), f2)] if detail else [])
        pad_x, pad_y = round(34 * u), round(20 * u)
        widths = [gfx.text_width(txt, f) for txt, f in lines]
        heights = [sum(f.getmetrics()) for _, f in lines]
        bw = max(widths) + 2 * pad_x
        bh = sum(heights) + 2 * pad_y
        img = Image.new("RGBA", (bw, bh), BOX_FILL)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, bw - 1, bh - 1], outline=(255, 255, 255, 215), width=max(1, round(2 * u)))
        y = pad_y
        for (txt, f), tw, th in zip(lines, widths, heights):
            d.text(((bw - tw) / 2, y), txt, font=f, fill=(255, 255, 255, 255))
            y += th
        return img

    @staticmethod
    def pop_in(layer: Image.Image, q: float) -> Image.Image:
        return gfx.scaled(layer, 0.86 + 0.14 * gfx.ease_out_back(q, 1.8)) if q < 1 else layer


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# ------------------------------------------------------------------ label
class Label(Overlay):
    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        self.layer = self.box(spec.title, spec.detail)
        self.cx = self.w * (0.5 + float(self.rng.uniform(-0.16, 0.16)))
        self.cy = self.h * 0.46

    def draw(self, canvas: Image.Image, t: float) -> None:
        q = gfx.progress(t, self.t0, 0.3)
        gfx.paste_center(canvas, self.pop_in(self.layer, q), self.cx, self.cy, alpha=min(1.0, q * 1.6) * self.fade_out(t))


# ------------------------------------------------------------------- pair
class Pair(Overlay):
    LINE_DUR = 0.45

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        self.a = self.box(spec.title, spec.detail)
        self.b = self.box(spec.title_b, spec.detail_b)
        self.xa, self.xb, self.y = self.w * 0.27, self.w * 0.73, self.h * 0.5

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "pop", 0.5), (spec.start + 0.3, "whoosh_soft", 0.35),
                (spec.start + 0.3 + cls.LINE_DUR, "pop", 0.55)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        qa = gfx.progress(t, self.t0, 0.3)
        gfx.paste_center(canvas, self.pop_in(self.a, qa), self.xa, self.y, alpha=min(1.0, qa * 1.6) * out)
        ql = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.3, self.LINE_DUR))
        x0, x1 = self.xa + self.a.width / 2, self.xb - self.b.width / 2
        if ql > 0 and x1 > x0:
            d = ImageDraw.Draw(canvas)
            end = x0 + (x1 - x0) * ql
            dash, x = 14 * u, x0
            while x < end:
                d.line([(x, self.y), (min(end, x + dash * 0.6), self.y)], fill=(255, 255, 255, int(220 * out)),
                       width=max(1, round(2 * u)))
                x += dash
        qb = gfx.progress(t, self.t0 + 0.3 + self.LINE_DUR, 0.3)
        gfx.paste_center(canvas, self.pop_in(self.b, qb), self.xb, self.y, alpha=min(1.0, qb * 1.6) * out)


# ---------------------------------------------------------------- callout
class Callout(Overlay):
    LINE_DUR = 0.5

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        tx, ty = spec.target or (0.5, 0.52)
        self.tx, self.ty = tx * w, ty * h
        f = gfx.font("condensed", round(58 * u))
        text = spec.title.upper()
        pad_x, pad_y = round(18 * u), round(8 * u)
        tw = gfx.text_width(text, f)
        chip = Image.new("RGBA", (tw + 2 * pad_x, sum(f.getmetrics()) + 2 * pad_y), (246, 240, 232, 235))
        ImageDraw.Draw(chip).text((pad_x, pad_y), text, font=f, fill=(150, 30, 20, 255))
        self.chip = gfx.with_shadow(chip, 0, round(8 * u), (0, round(4 * u)), 90)
        # a etiqueta vai para o lado com mais espaço, um pouco acima do ponto
        side = -1 if self.tx > w / 2 else 1
        self.cx = _clamp(self.tx + side * w * 0.2, self.chip.width / 2 + 30 * u, w - self.chip.width / 2 - 30 * u)
        self.cy = _clamp(self.ty - h * 0.16, self.chip.height / 2 + 30 * u, h - self.chip.height / 2 - 30 * u)
        self.side = side
        self.radius = h * 0.2

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "pop", 0.5), (spec.start + 0.2, "whoosh_soft", 0.35)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        d = ImageDraw.Draw(canvas)
        white = (255, 255, 255, int(225 * out))
        # arco fino em volta do ponto (desenha-se aos poucos)
        qa = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.7))
        if qa > 0:
            r = self.radius
            start = -110 if self.side > 0 else 70
            d.arc([self.tx - r, self.ty - r, self.tx + r, self.ty + r], start, start + 200 * qa, fill=white,
                  width=max(1, round(2 * u)))
        # linha curva da etiqueta até o ponto
        ql = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.2, self.LINE_DUR))
        if ql > 0:
            x0 = self.cx - self.side * self.chip.width * 0.42
            y0 = self.cy + self.chip.height * 0.3
            mx, my = (x0 + self.tx) / 2, max(y0, self.ty) + 40 * u
            pts = []
            steps = 40
            for k in range(int(steps * ql) + 1):
                s = k / steps
                pts.append(((1 - s) ** 2 * x0 + 2 * (1 - s) * s * mx + s * s * self.tx,
                            (1 - s) ** 2 * y0 + 2 * (1 - s) * s * my + s * s * self.ty))
            if len(pts) > 1:
                d.line(pts, fill=white, width=max(2, round(3 * u)), joint="curve")
            if ql >= 1:
                rr = 7 * u
                d.ellipse([self.tx - rr, self.ty - rr, self.tx + rr, self.ty + rr], fill=white)
        q = gfx.progress(t, self.t0, 0.28)
        gfx.paste_center(canvas, self.pop_in(self.chip, q), self.cx, self.cy, alpha=min(1.0, q * 1.6) * out)


# ------------------------------------------------------------------ badge
class Badge(Overlay):
    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u = self.u
        text = " ".join(x for x in (spec.title, spec.detail.upper()) if x).strip()
        f = gfx.font("condensed", round(78 * u))
        icon = round(70 * u)
        pad = round(18 * u)
        tw = gfx.text_width(text, f)
        img = Image.new("RGBA", (icon + tw + 3 * pad, icon + 2 * pad), (10, 14, 26, 120))
        d = ImageDraw.Draw(img)
        # ícone de velocímetro: arco, marcações e ponteiro
        cx, cy, r = pad + icon / 2, pad + icon * 0.62, icon * 0.48
        d.arc([cx - r, cy - r, cx + r, cy + r], 180, 360, fill=(255, 255, 255, 255), width=max(2, round(6 * u)))
        for k in range(5):
            a = math.radians(180 + k * 45)
            d.line([(cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62),
                    (cx + math.cos(a) * r * 0.82, cy + math.sin(a) * r * 0.82)], fill=(255, 255, 255, 255),
                   width=max(1, round(3 * u)))
        a = math.radians(315)
        d.line([(cx, cy), (cx + math.cos(a) * r * 0.75, cy + math.sin(a) * r * 0.75)], fill=(*gfx.accent(), 255),
               width=max(2, round(5 * u)))
        d.text((icon + 2 * pad, pad + (icon - sum(f.getmetrics())) / 2 + 4 * u), text, font=f, fill=(255, 255, 255, 255))
        self.layer = img

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "whoosh_soft", 0.4), (spec.start + 0.25, "tick", 0.5)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        q = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.35))
        x = self.w - self.layer.width - 60 * u + (1 - q) * (self.layer.width + 80 * u)
        gfx.paste_at(canvas, self.layer, x, 70 * u, alpha=self.fade_out(t))


# ---------------------------------------------------------------- keyword
class Keyword(Overlay):
    """Legenda minimalista no canto de baixo: um filete amarelo se desenha e a palavra sobe de
    trás dele (amarelo com contorno preto no padrão do canal). Sem seta: não depende de a
    imagem mostrar exatamente a peça."""

    LINE = 0.3
    RISE = 0.32

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u = self.u
        self.yellow = gfx.accent()
        fill = (*gfx.BRAND["text"], 255) if gfx.BRAND["on"] else (255, 255, 255, 255)  # texto do canal; filete no destaque
        self.text = gfx.outlined_text(spec.title.upper(), gfx.font("condensed", round(96 * u)), fill,
                                      max(3, round(5 * u)), (0, 0, 0, 255))
        self.x = round(self.w * 0.06)
        self.base = round(self.h * 0.86)  # o filete fica aqui; a palavra logo acima
        self.line_w = self.text.width + round(18 * u)

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "swipe", 0.3), (spec.start + cls.LINE, "whoosh_soft", 0.25)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        ql = gfx.ease_out_cubic(gfx.progress(t, self.t0, self.LINE))
        if ql > 0:
            d = ImageDraw.Draw(canvas)
            lw = max(3, round(6 * u))
            d.rectangle([self.x - 2 * u, self.base - 2 * u, self.x + self.line_w * ql + 2 * u, self.base + lw + 2 * u],
                        fill=(0, 0, 0, round(200 * out)))
            d.rectangle([self.x, self.base, self.x + self.line_w * ql, self.base + lw], fill=(*self.yellow, round(255 * out)))
        qt = gfx.ease_out_cubic(gfx.progress(t, self.t0 + self.LINE * 0.6, self.RISE))
        if qt <= 0:
            return
        # a palavra sobe de trás do filete (recorte na altura dele)
        rise = round((1 - qt) * self.text.height * 0.9)
        visible = self.text.height - rise
        if visible <= 0:
            return
        layer = self.text.crop((0, 0, self.text.width, visible))
        gfx.paste_at(canvas, layer, self.x, self.base - 8 * u - visible, alpha=min(1.0, qt * 1.5) * out)


# ------------------------------------------------------------ panel/list
class Panel(Overlay):
    """Lista minimalista sobre o b-roll: véu azul-escuro que esmaece em direção ao centro (a imagem
    continua aparecendo), filete amarelo, título em creme com um traço amarelo curto e os itens
    numerados ("01", "02"...) entrando um a um. ``title`` + ``lines`` (2-4 itens)."""

    SIDE = "left"
    ROW_STEP = 0.38
    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.CREAM, self.NAVY = gfx.BRAND["cream"], gfx.BRAND["veil"]  # creme e véu do canal
        self.yellow = gfx.accent()
        left = self.SIDE == "left"
        # véu: firme até passar do texto (~48% da largura), some até ~75%
        ramp = np.linspace(0, 1, w, dtype=np.float32)
        ramp = ramp if left else ramp[::-1]
        a = np.clip((0.75 - ramp) / 0.27, 0, 1) ** 1.2 * 200
        self.veil = Image.new("RGBA", (w, h), (*self.NAVY, 0))
        self.veil.putalpha(Image.fromarray(np.repeat(a[None, :], h, axis=0).astype(np.uint8), "L"))
        self.f_title = gfx.font("oswald_bold", round(66 * u))
        self.f_row = gfx.font("semibold", round(42 * u))
        self.f_num = gfx.font("condensed", round(46 * u))
        max_w = round(w * 0.4)
        self.title = gfx.wrap(spec.title.upper(), self.f_title, max_w, 2) if spec.title else []
        self.rows = [gfx.wrap(ln, self.f_row, max_w - round(80 * u), 2) for ln in spec.lines[:4] if ln]
        th = sum(self.f_title.getmetrics()) * 1.05
        rh = sum(self.f_row.getmetrics()) * 1.12
        self.row_h = [len(r) * rh + 26 * u for r in self.rows]
        block = len(self.title) * th + 46 * u + sum(self.row_h)
        self.y0 = h / 2 - block / 2
        self.th, self.rh = th, rh
        self.x0 = w * 0.07 if left else w * 0.93 - max_w

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        out: list[Cue] = [(spec.start, "whoosh_soft", 0.35)]
        out += [(spec.start + 0.55 + k * cls.ROW_STEP, "tick", 0.3) for k, ln in enumerate(spec.lines[:4]) if ln]
        return out

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        qv = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.35))
        canvas.alpha_composite(gfx.fade(self.veil, qv * out))
        d = ImageDraw.Draw(canvas)
        x, y = self.x0, self.y0
        total = len(self.title) * self.th + 46 * u + sum(self.row_h)
        ql = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.1, 0.5))
        if ql > 0:  # filete vertical à esquerda do bloco
            lw = max(2, round(4 * u))
            fx = x - 28 * u
            d.rectangle([fx, y, fx + lw, y + total * ql], fill=(*self.yellow, round(255 * out)))
        qt = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.15, 0.35))
        if qt > 0:
            for k, line in enumerate(self.title):
                self._text(d, (x + (1 - qt) * -18 * u, y + k * self.th), line, self.f_title, self.CREAM,
                           round(255 * qt * out))
        y += len(self.title) * self.th + 10 * u
        qb = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.35, 0.3))
        if qb > 0:
            d.rectangle([x, y, x + 70 * u * qb, y + max(3, 5 * u)], fill=(*self.yellow, round(255 * out)))
        y += 36 * u
        for k, (row, rh) in enumerate(zip(self.rows, self.row_h)):
            qr = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.55 + k * self.ROW_STEP, 0.3))
            if qr > 0:
                a = round(255 * qr * out)
                dx = (1 - qr) * -16 * u
                self._text(d, (x + dx, y - 2 * u), f"{k + 1:02d}", self.f_num, self.yellow, a)
                for j, line in enumerate(row):
                    self._text(d, (x + 70 * u + dx, y + j * self.rh), line, self.f_row, self.CREAM, a)
            y += rh


    def _text(self, d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fnt, color: tuple, a: int) -> None:
        """Texto com uma sombra curta (legível mesmo onde o véu já está fraco)."""
        off = max(1, round(2 * self.u))
        d.text((xy[0] + off, xy[1] + off), gfx.safe_text(text), font=fnt, fill=(0, 0, 0, round(a * 0.55)))
        d.text(xy, gfx.safe_text(text), font=fnt, fill=(*color, a))


class Bullets(Panel):
    """A mesma lista minimalista, do lado direito (a imagem fica livre à esquerda)."""

    SIDE = "right"


# ----------------------------------------------------------------- bignum
class BigNum(Overlay):
    COUNT_DUR = 0.9

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u = self.u
        self.nums = TextCache(self.fit_font("condensed", 230 * u, spec.title), (255, 255, 255), shadow=6, shadow_blur=18)
        parsed = gfx.parse_number(spec.title)
        self.value = parsed
        m = spec.title
        # prefixo/sufixo em volta do número ("R$ 110 mil", "90%")
        digits = [i for i, ch in enumerate(m) if ch.isdigit()]
        self.prefix = m[:digits[0]] if digits else ""
        self.suffix = m[digits[-1] + 1:] if digits else ""
        self.cap = gfx.text_block([spec.detail.upper()], gfx.font("condensed", round(64 * u)), (255, 255, 255),
                                  shadow=3, shadow_blur=10) if spec.detail else None

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        out: list[Cue] = [(spec.start + k * 0.08, "tick", 0.3) for k in range(int(cls.COUNT_DUR / 0.08))]
        out.append((spec.start + cls.COUNT_DUR, "impact_soft", 0.5))
        return out

    def draw(self, canvas: Image.Image, t: float) -> None:
        u, w, h = self.u, self.w, self.h
        out = self.fade_out(t)
        q = gfx.ease_out_cubic(gfx.progress(t, self.t0, self.COUNT_DUR))
        if self.value is not None and q < 1:
            txt = gfx.fmt_number(self.value[0] * q, self.value[1], self.prefix, self.suffix)
        else:
            txt = self.s.title
        layer = self.nums.get(txt)
        gfx.paste_center(canvas, layer, w / 2, h * 0.44, alpha=min(1.0, q * 2) * out)
        if self.cap is not None:
            qc = gfx.ease_out_cubic(gfx.progress(t, self.t0 + self.COUNT_DUR * 0.7, 0.35))
            gfx.paste_center(canvas, self.cap, w / 2, h * 0.44 + 150 * u + (1 - qc) * 16 * u, alpha=qc * out)


# -------------------------------------------------------------------- tag
class Tag(Overlay):
    """Título + detalhe com linha guia até um marcador amarelo no ponto de destaque
    ("CORREIA DENTADA / troca a cada 60 mil km"), com a imagem levemente escurecida."""

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.title = gfx.text_block([spec.title.upper()], self.fit_font("condensed", 118 * u, spec.title.upper(), 0.55),
                                    (255, 255, 255),
                                    shadow=4, shadow_blur=12)
        self.detail = gfx.text_block([spec.detail.upper()], gfx.font("condensed", round(60 * u)), (240, 240, 240),
                                     shadow=3, shadow_blur=8) if spec.detail else None
        tx, ty = spec.target or (0.42, 0.62)
        text_h = self.title.height + (self.detail.height if self.detail else 0)
        self.mx = _clamp(tx * w, 120 * u, w * 0.62)
        self.my = _clamp(ty * h, text_h + 60 * u, h - 90 * u)
        self.tx0 = self.mx - 60 * u  # canto esquerdo do texto
        self.ty1 = self.my - 60 * u  # base do bloco de texto
        self.dim = Image.new("RGBA", (w, h), (0, 0, 0, 80))

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "pop", 0.45), (spec.start + 0.35, "whoosh_soft", 0.3)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        qd = gfx.progress(t, self.t0, 0.3)
        if qd > 0:
            canvas.alpha_composite(gfx.fade(self.dim, qd * out))
        d = ImageDraw.Draw(canvas)
        qm = gfx.ease_out_back(gfx.progress(t, self.t0, 0.22), 2.0)
        if qm > 0:
            r = 11 * u * qm
            d.rectangle([self.mx - r, self.my - r, self.mx + r, self.my + r], fill=(*gfx.accent(), int(200 * out)),
                        outline=(*gfx.accent(), int(255 * out)), width=max(1, round(2 * u)))
        ql = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.12, 0.35))
        if ql > 0:
            p0 = (self.mx + 12 * u, self.my)
            p1 = (self.mx + 70 * u, self.my)
            p2 = (self.mx + 100 * u, self.my - 30 * u)
            seg1 = min(1.0, ql * 1.8)
            pts = [p0, (p0[0] + (p1[0] - p0[0]) * seg1, p0[1])]
            if ql > 0.55:
                q2 = (ql - 0.55) / 0.45
                pts.append((p1[0] + (p2[0] - p1[0]) * q2, p1[1] + (p2[1] - p1[1]) * q2))
            d.line(pts, fill=(255, 255, 255, int(235 * out)), width=max(2, round(3 * u)))
        qt = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.3, 0.4))
        if qt > 0:
            full = self.title
            shift = round((1 - qt) * full.height * 0.6)  # sobe de trás de uma linha
            win = full.crop((0, 0, full.width, full.height - shift))
            base_y = self.ty1 - (self.detail.height if self.detail else 0) - full.height
            gfx.paste_at(canvas, win, self.tx0 - 28, base_y + shift, alpha=min(1.0, qt * 1.4) * out)
        if self.detail is not None:
            qs = gfx.progress(t, self.t0 + 0.5, 0.3)
            gfx.paste_at(canvas, self.detail, self.tx0 - 22, self.ty1 - self.detail.height, alpha=qs * out)


# -------------------------------------------------------------- subscribe
class Subscribe(Overlay):
    """Like -> inscrever-se -> sininho, com o cursor clicando em cada um."""

    # (chegada do cursor, clique) em segundos depois do início; escalam com o tempo do shot
    STEPS = ((0.75, 0.9), (1.35, 1.5), (1.95, 2.1))

    @staticmethod
    def speed(spec: OverlaySpec) -> float:
        room = spec.seconds - spec.start - 0.5
        return max(0.55, min(1.0, room / 3.0))

    @classmethod
    def clicks(cls, spec: OverlaySpec) -> list[float]:
        k = cls.speed(spec)
        return [spec.start + c * k for _, c in cls.STEPS]

    def __init__(self, spec: OverlaySpec) -> None:
        from .i18n import text as tr
        from .templates_pro import _icon

        super().__init__(spec)
        u = self.u
        self.k = self.speed(spec)
        self.t_out = max(self.t_out, self.clicks(spec)[-1] + 0.9)
        self.bh = round(112 * u)
        f = gfx.font("montserrat_bold", round(40 * u))
        self.sub_on = self._pill(tr("subscribe", spec.lang), f, (230, 33, 23), (255, 255, 255))
        self.sub_off = self._pill(tr("subscribed", spec.lang), f, (72, 72, 72), (235, 235, 235))
        isz = round(62 * u)
        self.like_off = _icon("thumb", isz, (245, 245, 245))
        self.like_on = _icon("thumb_filled", isz, (62, 166, 255))
        self.bell = _icon("bell", isz, (245, 245, 245))
        self.bell_on = _icon("bell", isz, (255, 206, 38))
        self.cursor = self._cursor()
        sw = max(self.sub_on.width, self.sub_off.width)
        gap = round(26 * u)
        self.pw = round(40 * u) * 2 + isz * 2 + sw + gap * 2 + round(40 * u)
        self.ph = self.bh
        self.x0 = (self.w - self.pw) / 2
        self.y0 = self.h * 0.8 - self.ph / 2
        pad = round(40 * u)
        self.like_c = (self.x0 + pad + isz / 2, self.y0 + self.ph / 2)
        self.sub_x = self.x0 + pad + isz + gap
        self.sub_c = (self.sub_x + sw / 2, self.y0 + self.ph / 2)
        self.bell_c = (self.sub_x + sw + gap + round(20 * u) + isz / 2, self.y0 + self.ph / 2)
        self.sw = sw

    def _pill(self, text: str, fnt, bg: tuple[int, int, int], fg: tuple[int, int, int]) -> Image.Image:
        u = self.u
        asc, desc = fnt.getmetrics()
        tw = gfx.text_width(text, fnt)
        W, H = tw + round(84 * u), round(78 * u)
        img = Image.new("RGBA", (W * 3, H * 3), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle([0, 0, W * 3 - 1, H * 3 - 1], radius=H * 3 // 2, fill=(*bg, 255))
        img = img.resize((W, H), Image.LANCZOS)
        ImageDraw.Draw(img).text(((W - tw) / 2, (H - asc - desc) / 2 + 2 * u), text, font=fnt, fill=(*fg, 255))
        return img

    def _cursor(self) -> Image.Image:
        u, ss = self.u, 4
        s = round(58 * u) * ss
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pts = [(s * 0.1, s * 0.05), (s * 0.1, s * 0.8), (s * 0.3, s * 0.63), (s * 0.45, s * 0.95),
               (s * 0.58, s * 0.89), (s * 0.44, s * 0.58), (s * 0.7, s * 0.58)]
        d.polygon(pts, fill=(255, 255, 255, 255), outline=(10, 10, 10, 255))
        d.line(pts + [pts[0]], fill=(10, 10, 10, 255), width=max(ss, round(3 * u * ss)))
        return img.resize((s // ss, s // ss), Image.LANCZOS)

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        c1, c2, c3 = cls.clicks(spec)
        return [(spec.start, "whoosh_soft", 0.4), (c1, "click", 0.6), (c1 + 0.02, "pop", 0.35), (c2, "click", 0.6),
                (c2 + 0.05, "pop", 0.45), (c3, "click", 0.6), (c3 + 0.05, "ding", 0.45)]

    def _press(self, t: float, click: float) -> float:
        """Escala do botão: afunda um pouco no clique e volta."""
        p = gfx.progress(t, click - 0.04, 0.22)
        return 1 - 0.1 * math.sin(math.pi * p) if 0 < p < 1 else 1.0

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        q = gfx.ease_out_back(gfx.progress(t, self.t0, 0.4), 1.4)
        if q <= 0:
            return
        c1, c2, c3 = self.clicks(self.s)
        dy = (1 - min(1.0, q)) * 60 * u
        pill = Image.new("RGBA", (self.pw, self.ph), (0, 0, 0, 0))
        ImageDraw.Draw(pill).rounded_rectangle([0, 0, self.pw - 1, self.ph - 1], radius=self.ph // 2,
                                               fill=(15, 15, 15, 215), outline=(255, 255, 255, 40),
                                               width=max(1, round(2 * u)))
        gfx.paste_at(canvas, pill, self.x0, self.y0 + dy, alpha=min(1.0, q) * out)
        like = self.like_on if t >= c1 else self.like_off
        gfx.paste_center(canvas, gfx.scaled(like, self._press(t, c1)), self.like_c[0], self.like_c[1] + dy, alpha=out)
        if t >= c1:  # estouro de partículas no like
            pb = gfx.progress(t, c1, 0.35)
            if pb < 1:
                d = ImageDraw.Draw(canvas)
                for k in range(8):
                    a = k * math.pi / 4
                    r0, r1 = (40 + 30 * pb) * u, (50 + 55 * pb) * u
                    cx, cy = self.like_c[0], self.like_c[1] + dy
                    d.line([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)), (cx + r1 * math.cos(a), cy + r1 * math.sin(a))],
                           fill=(62, 166, 255, round(255 * (1 - pb) * out)), width=max(2, round(4 * u)))
        sub = self.sub_off if t >= c2 else self.sub_on
        gfx.paste_center(canvas, gfx.scaled(sub, self._press(t, c2)), self.sub_c[0], self.sub_c[1] + dy, alpha=out)
        bell = self.bell_on if t >= c3 else self.bell
        ang = 0.0
        if t >= c3:
            x = t - c3
            ang = 22 * math.exp(-4.5 * x) * math.sin(x * 26)
        b = bell.rotate(ang, resample=Image.BICUBIC, center=(bell.width / 2, bell.height * 0.1)) if abs(ang) > 0.1 else bell
        gfx.paste_center(canvas, gfx.scaled(b, self._press(t, c3)), self.bell_c[0], self.bell_c[1] + dy, alpha=out)
        # cursor: entra pela direita e vai de botão em botão
        path = [(self.w * 0.98, self.h * 1.02), self.like_c, self.sub_c, self.bell_c]
        k = self.k
        starts = [self.t0 + 0.3 * k, self.t0 + 0.95 * k, self.t0 + 1.55 * k]
        arrive = [self.t0 + a * k for a, _ in self.STEPS]
        pos = path[0]
        for j in range(3):
            if t >= starts[j]:
                e = gfx.ease_in_out(gfx.progress(t, starts[j], arrive[j] - starts[j]))
                a, b_ = path[j], path[j + 1]
                pos = (a[0] + (b_[0] - a[0]) * e, a[1] + (b_[1] - a[1]) * e)
        if t >= starts[0]:
            gone = gfx.progress(t, c3 + 0.4, 0.3)
            gfx.paste_at(canvas, self.cursor, pos[0] - 4 * u, pos[1] + dy - 2 * u, alpha=(1 - gone) * out)


# ------------------------------------------------------------------- fuel
class Fuel(Overlay):
    """Consumo em destaque: card no canto de baixo com a bomba de combustível, o número
    contando em amarelo com contorno ("5", "8–9") e a unidade ("L/100 km", "km/l").
    ``title`` = número (ou faixa), ``detail`` = unidade, ``title_b`` = onde ("EN VILLE")."""

    COUNT = 0.8
    SLIDE = 0.35

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        from .i18n import text as tr

        u = self.u
        self.yellow = gfx.accent()
        parts = [p for p in re.split(r"\s*[–—-]\s*|\s+(?:à|a|e|et|do|ou)\s+", spec.title.strip()) if p]
        self.values = [v for v in (gfx.parse_number(p) for p in parts) if v is not None][:2]
        self.f_num = self.fit_font("condensed", 150 * u, spec.title, 0.62)
        self.stroke = max(3, round(7 * u))
        self.nums: dict[str, Image.Image] = {}
        final = self._num_text(1.0)
        num_img = self._num(final)
        self.unit = gfx.outlined_text(spec.detail or "", gfx.font("condensed", round(62 * u)), (222, 230, 246, 255),
                                      max(2, round(4 * u)), (0, 0, 0, 255)) if spec.detail else None
        head = tr("fuel", spec.lang) + (f"  ·  {spec.title_b.upper()}" if spec.title_b else "")
        self.head = gfx.outlined_text(head, gfx.font("condensed", round(42 * u)), (170, 190, 228, 255), 0, (0, 0, 0, 0))
        self.icon_s = round(128 * u)
        self.icon = self._pump(self.icon_s)
        pad, gap = round(28 * u), round(26 * u)
        self.pad, self.gap, self.bar = pad, gap, max(6, round(12 * u))
        text_w = max(self.head.width, num_img.width + (self.unit.width + round(14 * u) if self.unit else 0))
        self.cw = self.bar + pad + self.icon_s + gap + text_w + pad
        self.ch = pad + self.head.height + round(4 * u) + num_img.height + pad
        self.card = self._card(self.cw, self.ch)
        self.x = round(64 * u)
        self.y = self.h - self.ch - round(80 * u)

    def _num_text(self, q: float) -> str:
        if not self.values:
            return self.s.title
        return "–".join(gfx.fmt_number(v * q, dec) for v, dec in self.values)

    def _num(self, txt: str) -> Image.Image:
        img = self.nums.get(txt)
        if img is None:
            img = self.nums[txt] = gfx.outlined_text(txt, self.f_num, (*self.yellow, 255), self.stroke, (0, 0, 0, 255))
        return img

    def _card(self, w: int, h: int) -> Image.Image:
        u = self.u
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        r = round(16 * u)
        veil = tuple(min(255, c + 4) for c in gfx.BRAND["veil"])
        d.rounded_rectangle([0, 0, w - 1, h - 1], r, fill=(*veil, 222), outline=(255, 255, 255, 46),
                            width=max(1, round(2 * u)))
        d.rounded_rectangle([0, 0, self.bar + r, h - 1], r, fill=(*self.yellow, 255))
        d.rectangle([self.bar, 0, self.bar + r, h - 1], fill=(*veil, 222))
        return gfx.with_shadow(img, 0, round(18 * u), (0, round(8 * u)), 110)

    def _pump(self, s: int) -> Image.Image:
        """Bomba de combustível desenhada (amarela, contorno preto)."""
        u = self.u
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        yl, bk = (*self.yellow, 255), (0, 0, 0, 255)
        ow = max(2, round(4 * u))
        hose = [(0.6 * s, 0.34 * s), (0.8 * s, 0.34 * s), (0.84 * s, 0.78 * s), (0.93 * s, 0.78 * s),
                (0.93 * s, 0.3 * s), (0.8 * s, 0.14 * s)]
        d.line(hose, fill=bk, width=max(4, round(12 * u)), joint="curve")
        d.line(hose, fill=yl, width=max(2, round(6 * u)), joint="curve")
        d.rounded_rectangle([0.1 * s, 0.08 * s, 0.64 * s, 0.9 * s], round(8 * u), fill=yl, outline=bk, width=ow)
        d.rectangle([0.2 * s, 0.18 * s, 0.54 * s, 0.42 * s], fill=(*gfx.BRAND["veil"], 255), outline=bk, width=ow)
        d.rounded_rectangle([0.03 * s, 0.86 * s, 0.71 * s, 0.98 * s], round(4 * u), fill=yl, outline=bk, width=ow)
        return img

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        ticks = [(spec.start + 0.3 + k * 0.08, "tick", 0.25) for k in range(int(cls.COUNT / 0.08))]
        return [(spec.start, "whoosh_soft", 0.4)] + ticks + [(spec.start + 0.3 + cls.COUNT, "impact_soft", 0.45)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        q = gfx.ease_out_cubic(gfx.progress(t, self.t0, self.SLIDE))
        x = self.x - (1 - q) * (self.cw + 90 * u)
        gfx.paste_center(canvas, self.card, x + self.cw / 2, self.y + self.ch / 2, alpha=min(1.0, q * 1.4) * out)
        qi = gfx.progress(t, self.t0 + 0.15, 0.3)
        if qi > 0:
            icon = gfx.scaled(self.icon, 0.6 + 0.4 * gfx.ease_out_back(qi, 2.0))
            gfx.paste_center(canvas, icon, x + self.bar + self.pad + self.icon_s / 2, self.y + self.ch / 2,
                             alpha=min(1.0, qi * 2) * out)
        tx = x + self.bar + self.pad + self.icon_s + self.gap
        gfx.paste_at(canvas, self.head, tx, self.y + self.pad, alpha=q * out)
        qn = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.3, self.COUNT))
        if t >= self.t0 + 0.25:
            num = self._num(self._num_text(qn) if qn < 1 else self._num_text(1.0))
            ny = self.y + self.pad + self.head.height + round(4 * u)
            gfx.paste_at(canvas, num, tx, ny, alpha=out)
            if self.unit is not None:
                qu = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.3 + self.COUNT * 0.6, 0.3))
                final_w = self._num(self._num_text(1.0)).width
                gfx.paste_at(canvas, self.unit, tx + final_w + 14 * u, ny + num.height - self.unit.height - 10 * u,
                             alpha=qu * out)


# ----------------------------------------------------------------- phrase
class Phrase(Overlay):
    """Frase de destaque por cima do b-roll: as palavras sobem uma a uma no terço de baixo
    (branco com contorno preto) e um marca-texto amarelo passa atrás da palavra forte.
    ``title`` = a frase falada; ``lines`` = palavras a destacar (sem elas, a mais longa do fim)."""

    MARK = 0.28

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        text = " ".join(spec.title.split())
        self.font = gfx.font("black", round(84 * u))
        lines = gfx.wrap(text, self.font, round(w * 0.8), 2)
        if len(lines) == 2 and lines[-1].endswith("…"):
            self.font = gfx.font("black", round(66 * u))
            lines = gfx.wrap(text, self.font, round(w * 0.86), 2)
        words = [wd for ln in lines for wd in ln.split()]
        marks = {gfx.safe_text(m).lower().strip(_WPUNCT) for ln in spec.lines for m in str(ln).split()}
        if not marks and words:
            marks = {max(words[-4:], key=lambda s: len(s.strip(_WPUNCT))).lower().strip(_WPUNCT)}
        self.yellow = gfx.accent()
        self.stroke = max(3, round(6 * u))
        asc, desc = self.font.getmetrics()
        self.asc = asc
        self.lh = round((asc + desc) * 1.12)
        y0 = h * 0.8 - self.lh * len(lines) / 2
        space = gfx.text_width(" ", self.font)
        self.words: list[tuple[str, float, float, float, bool]] = []  # palavra, x, y, largura, destaque
        for k, ln in enumerate(lines):
            x = w / 2 - gfx.text_width(ln, self.font) / 2
            for wd in ln.split():
                ww = gfx.text_width(wd, self.font)
                core = gfx.text_width(wd.rstrip(_WPUNCT), self.font) or ww  # o marca-texto não pega a vírgula
                self.words.append((wd, x, y0 + k * self.lh, core, wd.lower().strip(_WPUNCT) in marks))
                x += ww + space
        avail = max(0.6, spec.seconds - spec.start - 1.2)
        self.step = min(0.16, max(0.05, avail * 0.45 / max(1, len(self.words))))
        grad = np.clip((np.linspace(0, 1, h, dtype=np.float32) - 0.55) / 0.35, 0, 1) ** 1.4 * 170
        self.shade = Image.fromarray(np.repeat(grad[:, None], w, axis=1).astype(np.uint8), "L")

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        n = max(1, len(spec.title.split()))
        avail = max(0.6, spec.seconds - spec.start - 1.2)
        step = min(0.16, max(0.05, avail * 0.45 / n))
        out: list[Cue] = [(spec.start, "whoosh_soft", 0.3)]
        out += [(spec.start + 0.1 + k * step, "tick", 0.14) for k in range(0, min(n, 14), 2)]
        out.append((spec.start + 0.1 + n * step, "swipe", 0.3))
        return out

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        qs = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.3))
        dark = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        dark.putalpha(self.shade.point(lambda a: int(a * qs * out)))
        canvas.alpha_composite(dark)
        d = ImageDraw.Draw(canvas)
        a = round(255 * out)
        end = self.t0 + 0.1 + len(self.words) * self.step
        for k, (wd, x, y, ww, hot) in enumerate(self.words):
            q = gfx.progress(t, self.t0 + 0.1 + k * self.step, 0.2)
            if q <= 0:
                continue
            dy = (1 - gfx.ease_out_back(q, 2.0)) * 22 * u
            aw = round(a * gfx.ease_out_cubic(q))
            if hot:  # marca-texto: faixa inclinada que varre da esquerda para a direita
                m = gfx.ease_out_cubic(gfx.progress(t, end + 0.05, self.MARK))
                if m > 0:
                    top, bot = y + self.asc * 0.22, y + self.asc * 1.08
                    x0, x1 = x - 7 * u, x - 7 * u + (ww + 14 * u) * m
                    sk = 5 * u
                    d.polygon([(x0 + sk, top), (x1 + sk, top), (x1 - sk, bot), (x0 - sk, bot)],
                              fill=(*self.yellow, round(235 * out)))
                if gfx.progress(t, end + 0.05, self.MARK * 0.5) >= 1:  # palavra em preto sobre o amarelo
                    core = wd.rstrip(_WPUNCT)
                    d.text((x, y + dy), gfx.safe_text(core), font=self.font, fill=(0, 0, 0, aw))
                    if core != wd:
                        d.text((x + ww, y + dy), wd[len(core):], font=self.font, fill=(255, 255, 255, aw),
                               stroke_width=self.stroke, stroke_fill=(0, 0, 0, aw))
                    continue
                fill, stroke = (255, 255, 255, aw), self.stroke
            else:
                fill, stroke = (255, 255, 255, aw), self.stroke
            d.text((x, y + dy), gfx.safe_text(wd), font=self.font, fill=fill, stroke_width=stroke,
                   stroke_fill=(0, 0, 0, aw) if stroke else None)



# ------------------------------------------------------------------- file
class FileCard(Overlay):
    """Ficha de papel sobre o b-roll (visual de dossiê): cartão bege com fita adesiva no canto,
    "DOSSIER" pequeno e espaçado, o nome grande datilografado e um traço vermelho embaixo.
    ``title`` = o nome (o carro); ``detail`` = o rótulo de cima (padrão: "FILE" no idioma)."""

    CPS = 16.0
    SLIDE = 0.4

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        from .i18n import text as tr

        u = self.u
        self.ink, self.red = (40, 34, 30), (196, 46, 34)
        self.f_name = gfx.font("typewriter", round(96 * u))
        self.f_tag = gfx.font("typewriter", round(30 * u))
        self.name = gfx.wrap(spec.title.upper(), self.f_name, round(self.w * 0.5), 1)[0]
        self.tag = (spec.detail or tr("file", spec.lang)).upper()
        pad = round(46 * u)
        name_w = gfx.spaced_width(self.name, self.f_name, 0.02)
        cw = round(max(name_w, gfx.spaced_width(self.tag, self.f_tag, 0.5)) + 2 * pad + 60 * u)
        ch = round(250 * u)
        self.pad, self.cw, self.ch = pad, cw, ch
        self.paper = self._paper(cw, ch)
        self.x = round(self.w * 0.07)
        self.y = round(self.h * 0.27)
        self.tilt = float(self.rng.uniform(-1.2, 0.4))

    def _paper(self, w: int, h: int) -> Image.Image:
        img = Image.new("RGBA", (w, h), (237, 230, 214, 255))
        arr = np.asarray(img).astype(np.int16)
        arr[..., :3] += (self.rng.standard_normal((h, w)) * 3).astype(np.int16)[..., None]
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "whoosh_soft", 0.35), (spec.start + 0.3, "snap", 0.3),
                (spec.start + 0.45, f"typing:{max(0.2, len(spec.title) / cls.CPS):.3f}", 0.55)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        q = gfx.ease_out_cubic(gfx.progress(t, self.t0, self.SLIDE))
        if q <= 0:
            return
        card = self.paper.copy()
        d = ImageDraw.Draw(card)
        pad = self.pad
        gfx.draw_spaced(d, (pad, pad * 0.75), self.tag, self.f_tag, (*self.ink, 210), tracking=0.5)
        line_y = pad * 0.75 + sum(self.f_tag.getmetrics()) + 10 * u
        d.line([(pad, line_y), (self.cw - pad, line_y)], fill=(*self.ink, 90), width=max(1, round(2 * u)))
        n = int(max(0.0, t - self.t0 - 0.45) * self.CPS)
        name_y = line_y + 16 * u
        gfx.draw_spaced(d, (pad, name_y), self.name, self.f_name, (*self.ink, 255), tracking=0.02, chars=n,
                        bold=max(1, round(1.5 * u)))
        qr = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.45 + len(self.name) / self.CPS, 0.4))
        if qr > 0:
            ry = self.ch - pad * 0.85
            d.line([(pad, ry), (pad + (self.cw - 2 * pad) * qr, ry)], fill=(*self.red, 255),
                   width=max(3, round(5 * u)))
        layer = gfx.with_shadow(card, self.tilt * q, round(14 * u), (round(5 * u), round(10 * u)), 90)
        # fita adesiva no canto de cima à esquerda (entra logo depois do cartão)
        qt = gfx.progress(t, self.t0 + 0.3, 0.15)
        if qt > 0:
            tape = Image.new("RGBA", (round(190 * u), round(52 * u)), (226, 186, 84, round(205 * qt)))
            tape = tape.rotate(4, expand=True, resample=Image.BILINEAR)
            layer.alpha_composite(tape, (round(layer.width * 0.02), round(layer.height * 0.02)))
        x = self.x - (1 - q) * (self.cw + 120 * u)
        gfx.paste_at(canvas, layer, x, self.y, alpha=out)



# ----------------------------------------------------------------- impact
class Impact(Overlay):
    """Palavra de impacto por cima do b-roll: véu azul-escuro translúcido (a imagem continua
    aparecendo), um filete vertical no meio, a palavra grande em creme com as letras levemente
    desalinhadas e a frase embaixo em máquina de escrever, espaçada. Minimalista.
    ``title`` = a palavra forte (1-2 palavras da fala); ``detail`` = a frase."""

    CPS = 30.0

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.CREAM, self.VEIL = gfx.BRAND["cream"], (*gfx.BRAND["veil"], 172)  # creme e véu do canal
        self.line_col = gfx.accent()
        word = " ".join(spec.title.upper().split())
        size = 250 * u
        f = gfx.font("oswald_bold", round(size))
        while gfx.text_width(word, f) > w * 0.7 and size > 90 * u:  # cabe em 70% da largura
            size *= 0.92
            f = gfx.font("oswald_bold", round(size))
        self.word = self._jittered(word, f)
        self.f_sub = gfx.font("typewriter", round(46 * u))
        sub_text = " ".join(spec.detail.split())
        while sub_text and gfx.spaced_width(sub_text, self.f_sub, 0.28) > w * 0.8 and self.f_sub.size > 22 * u:
            self.f_sub = gfx.font("typewriter", self.f_sub.size - 2)
        self.sub = sub_text
        self.sub_w = gfx.spaced_width(sub_text, self.f_sub, 0.28) if sub_text else 0
        self.cy = h * 0.5

    def _jittered(self, word: str, f) -> Image.Image:
        """Letras com um tiquinho de rotação e altura diferentes (como no recorte de revista)."""
        u = self.u
        asc, desc = f.getmetrics()
        pad = round(30 * u)
        W = round(gfx.text_width(word, f) + 2 * pad + len(word) * 4 * u)
        img = Image.new("RGBA", (W, asc + desc + 2 * pad), (0, 0, 0, 0))
        x = pad
        for ch in word:
            if ch == " ":
                x += f.getlength(" ")
                continue
            cw = round(f.getlength(ch))
            letter = Image.new("RGBA", (cw + 2 * pad, asc + desc + 2 * pad), (0, 0, 0, 0))
            ImageDraw.Draw(letter).text((pad, pad), ch, font=f, fill=(*self.CREAM, 255))
            letter = letter.rotate(float(self.rng.uniform(-2.5, 2.5)), resample=Image.BICUBIC)
            dy = round(float(self.rng.uniform(-0.025, 0.025)) * asc)
            img.alpha_composite(letter, (round(x - pad), dy))
            x += cw + 2 * u
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow.putalpha(img.getchannel("A").point(lambda a: a * 0.45).filter(ImageFilter.GaussianBlur(10 * u)))
        out = Image.new("RGBA", img.size, (0, 0, 0, 0))
        out.alpha_composite(shadow, (0, round(6 * u)))
        out.alpha_composite(img)
        return out

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        out: list[Cue] = [(spec.start, "whoosh_soft", 0.35), (spec.start + 0.3, "impact_soft", 0.5)]
        if spec.detail:
            out.append((spec.start + 0.75, f"typing:{max(0.2, len(spec.detail) / cls.CPS):.3f}", 0.45))
        return out

    def draw(self, canvas: Image.Image, t: float) -> None:
        u, w, h = self.u, self.w, self.h
        out = self.fade_out(t)
        qv = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.3))
        veil = Image.new("RGBA", canvas.size, (*self.VEIL[:3], round(self.VEIL[3] * qv * out)))
        canvas.alpha_composite(veil)
        d = ImageDraw.Draw(canvas)
        ql = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.1, 0.45))
        if ql > 0:  # filete vertical descendo
            lw = max(2, round(3 * u))
            d.rectangle([w / 2 - lw / 2, 0, w / 2 + lw / 2, h * ql], fill=(*self.line_col, round(230 * out)))
        qw = gfx.progress(t, self.t0 + 0.25, 0.35)
        if qw > 0:
            e = gfx.ease_out_cubic(qw)
            gfx.paste_center(canvas, gfx.scaled(self.word, 1.06 - 0.06 * e), w / 2, self.cy - 30 * u,
                             alpha=min(1.0, qw * 1.8) * out)
        if self.sub:
            n = int(max(0.0, t - self.t0 - 0.75) * self.CPS)
            if n > 0:
                y = self.cy + self.word.height * 0.36
                gfx.draw_spaced(d, (w / 2 - self.sub_w / 2, y), self.sub, self.f_sub, (*self.CREAM, round(255 * out)),
                                tracking=0.28, chars=n, bold=max(1, round(1.2 * u)))


# ------------------------------------------------------- broadcast (TV)
NEWS_RED = (206, 24, 34)
NEWS_DARK = (14, 16, 22)


class LowerThird(Overlay):
    """Tarja de telejornal no canto de baixo: o bloco vermelho abre da esquerda com ``title`` em
    branco (caixa alta, condensada) e, logo depois, a faixa branca com ``detail`` (trecho da fala).
    Um filete na cor de destaque do canal fecha a tarja. Sai fechando para a esquerda."""

    OPEN, SUB = 0.3, 0.32

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u, w = self.u, self.w
        self.f1 = gfx.font("oswald_bold", round(66 * u))
        self.f2 = gfx.font("montserrat_bold", round(34 * u))
        self.title = gfx.wrap(spec.title.upper(), self.f1, round(w * 0.6), 1)[0]
        self.detail = gfx.wrap(spec.detail.upper(), self.f2, round(w * 0.62), 1)[0] if spec.detail else ""
        pad = round(30 * u)
        self.w1 = round(gfx.text_width(self.title, self.f1) + 2 * pad + 16 * u)
        self.w2 = round(gfx.text_width(self.detail, self.f2) + 2 * pad) if self.detail else 0
        self.h1 = round(sum(self.f1.getmetrics()) + 18 * u)
        self.h2 = round(sum(self.f2.getmetrics()) + 22 * u) if self.detail else 0
        self.pad = pad
        self.x, self.y = round(w * 0.06), round(self.h * 0.76)

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        out: list[Cue] = [(spec.start, "whoosh_soft", 0.35), (spec.start + cls.OPEN * 0.8, "swipe", 0.25)]
        if spec.detail:
            out.append((spec.start + cls.OPEN + 0.1, "tick", 0.2))
        return out

    def _close(self, t: float) -> float:
        return gfx.ease_out_cubic(gfx.progress(t, self.t_out - 0.05, 0.3))

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        close = self._close(t)
        d = ImageDraw.Draw(canvas)
        q1 = gfx.ease_out_cubic(gfx.progress(t, self.t0, self.OPEN)) * (1 - close)
        if q1 <= 0:
            return
        x, y = self.x, self.y
        d.rectangle([x, y, x + 10 * u, y + self.h1 + self.h2], fill=(*gfx.accent(), 255))
        w1 = self.w1 * q1
        d.rectangle([x + 10 * u, y, x + 10 * u + w1, y + self.h1], fill=(*NEWS_RED, 245))
        qt = gfx.progress(t, self.t0 + self.OPEN * 0.6, 0.25) * (1 - close)
        if qt > 0:
            asc, desc = self.f1.getmetrics()
            layer = Image.new("RGBA", (self.w1, self.h1), (0, 0, 0, 0))
            dy = (1 - gfx.ease_out_cubic(qt)) * self.h1 * 0.6
            ImageDraw.Draw(layer).text((self.pad, (self.h1 - asc - desc) / 2 + dy - 2 * u), self.title, font=self.f1,
                                       fill=(255, 255, 255, round(255 * min(1.0, qt * 1.6))))
            canvas.alpha_composite(layer.crop((0, 0, max(1, round(w1)), self.h1)), (round(x + 10 * u), round(y)))
        if not self.detail:
            return
        q2 = gfx.ease_out_cubic(gfx.progress(t, self.t0 + self.OPEN, self.SUB)) * (1 - close)
        if q2 <= 0:
            return
        w2 = self.w2 * q2
        y2 = y + self.h1
        d.rectangle([x + 10 * u, y2, x + 10 * u + w2, y2 + self.h2], fill=(246, 246, 246, 240))
        asc, desc = self.f2.getmetrics()
        layer = Image.new("RGBA", (self.w2, self.h2), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((self.pad, (self.h2 - asc - desc) / 2), self.detail, font=self.f2,
                                   fill=(*NEWS_DARK, 255))
        canvas.alpha_composite(layer.crop((0, 0, max(1, round(w2)), self.h2)), (round(x + 10 * u), round(y2)))


class Ticker(Overlay):
    """Barra de manchete embaixo: rótulo vermelho ("ÚLTIMAS") + a frase (``title``) entrando da
    direita e parando, e embaixo uma faixa fina rolando com o nome/assunto (``detail``)."""

    ENTER = 0.35

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        from .i18n import text as tr

        u, w = self.u, self.w
        self.label = tr("latest" if spec.tone == "news" else "key_point", spec.lang)
        self.f_lab = gfx.font("oswald_bold", round(44 * u))
        self.lab_w = round(gfx.text_width(self.label, self.f_lab) + 56 * u)
        size = 46
        self.f = gfx.font("montserrat_bold", round(size * u))
        avail = round(w * 0.88 - self.lab_w - 60 * u)
        while gfx.text_width(spec.title, self.f) > avail and size > 30:
            size -= 2
            self.f = gfx.font("montserrat_bold", round(size * u))
        self.text = gfx.wrap(spec.title, self.f, avail, 1)[0]
        self.bar_h = round(92 * u)
        self.crawl_h = round(44 * u)
        self.f_crawl = gfx.font("montserrat_bold", round(26 * u))
        crawl = (spec.detail or spec.title).upper()
        self.crawl = (crawl + "   •   ") * 6
        self.crawl_w = max(1.0, gfx.text_width(crawl + "   •   ", self.f_crawl))
        self.x = round(w * 0.06)
        self.width = round(w * 0.88)
        self.y = round(self.h - self.bar_h - self.crawl_h - 56 * u)

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "whoosh_soft", 0.35), (spec.start + cls.ENTER, "swipe", 0.25)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        q = gfx.ease_out_cubic(gfx.progress(t, self.t0, self.ENTER))
        if q <= 0:
            return
        layer = Image.new("RGBA", (self.width, self.bar_h + self.crawl_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        full = round(self.width * q)
        d.rectangle([0, 0, full, self.bar_h], fill=(246, 246, 246, 242))
        d.rectangle([0, 0, min(full, self.lab_w), self.bar_h], fill=(*NEWS_RED, 255))
        la, ld = self.f_lab.getmetrics()
        d.text((28 * u, (self.bar_h - la - ld) / 2 - 2 * u), self.label, font=self.f_lab, fill=(255, 255, 255, 255))
        qt = gfx.ease_out_cubic(gfx.progress(t, self.t0 + self.ENTER * 0.7, 0.45))
        if qt > 0:
            a, dsc = self.f.getmetrics()
            tx = self.lab_w + 30 * u + (1 - qt) * self.width * 0.5
            text_layer = Image.new("RGBA", (self.width - self.lab_w, self.bar_h), (0, 0, 0, 0))
            ImageDraw.Draw(text_layer).text((tx - self.lab_w, (self.bar_h - a - dsc) / 2), self.text, font=self.f,
                                            fill=(*NEWS_DARK, round(255 * min(1.0, qt * 1.5))))
            layer.alpha_composite(text_layer, (self.lab_w, 0))
        # faixa fina rolando
        d.rectangle([0, self.bar_h, full, self.bar_h + self.crawl_h], fill=(*NEWS_DARK, 230))
        d.rectangle([0, self.bar_h, full, self.bar_h + max(2, round(4 * u))], fill=(*gfx.accent(), 255))
        ca, cd = self.f_crawl.getmetrics()
        cx = 20 * u - ((t - self.t0) * 120 * u) % self.crawl_w
        crawl = Image.new("RGBA", (full or 1, self.crawl_h), (0, 0, 0, 0))
        ImageDraw.Draw(crawl).text((cx, (self.crawl_h - ca - cd) / 2 + 2 * u), self.crawl, font=self.f_crawl,
                                   fill=(255, 255, 255, 235))
        layer.alpha_composite(crawl, (0, self.bar_h))
        gfx.paste_at(canvas, layer, self.x, self.y + (1 - q) * 40 * u, alpha=out)


class Crawl(Overlay):
    """Letreiro do rodapé (um pouco acima da borda): faixa escura translúcida, bloco vermelho com o
    nome do carro (``title``) e as frases (``lines``) passando da direita para a esquerda, separadas por
    um losango na cor de destaque. Atravessa os cortes da cena: cada shot continua de onde o anterior
    parou (``offset``); só o primeiro entra (``enter``) e só o último sai (``leave``)."""

    SPEED = 165  # px/s em 1080p

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        if not spec.leave:
            self.t_out = spec.seconds + 10  # continua no próximo shot
        self.f_lab = gfx.font("oswald_bold", round(38 * u))
        self.f = gfx.font("montserrat_bold", round(34 * u))
        self.label = gfx.wrap(spec.title.upper(), self.f_lab, round(w * 0.3), 1)[0] if spec.title else ""
        self.lab_w = round(gfx.text_width(self.label, self.f_lab) + 56 * u) if self.label else 0
        self.bar_h = round(62 * u)
        self.y = round(h * 0.79)
        items = [" ".join(x.upper().split()) for x in spec.lines if x.strip()]
        self.sep = "   ◆   "
        self.text = self.sep.join(items) + self.sep
        self.text_w = max(1.0, gfx.text_width(self.text, self.f))
        self.strip = self._strip()

    def _strip(self) -> Image.Image:
        """O texto todo numa tira só (desenhado uma vez; cada quadro só recorta e desloca)."""
        u = self.u
        reps = 2 + int(self.w // self.text_w)
        img = Image.new("RGBA", (round(self.text_w * reps) + 4, self.bar_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        a, dsc = self.f.getmetrics()
        y = (self.bar_h - a - dsc) / 2 + u
        x = 0.0
        acc = (*gfx.accent(), 255)
        for _ in range(reps):
            for k, part in enumerate(self.text.split(self.sep)):
                if part:
                    d.text((x, y), part, font=self.f, fill=(255, 255, 255, 245))
                    x += gfx.text_width(part, self.f)
                if k < len(self.text.split(self.sep)) - 1:
                    sw = gfx.text_width(self.sep, self.f)
                    cx, cy, r = x + sw / 2, self.bar_h / 2, 7 * u
                    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=acc)
                    x += sw
        return img

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "whoosh_soft", 0.3)] if spec.enter else []

    def draw(self, canvas: Image.Image, t: float) -> None:
        u, w = self.u, self.w
        q = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.35)) if self.s.enter else 1.0
        out = self.fade_out(t) if self.s.leave else 1.0
        if q <= 0 or out <= 0:
            return
        layer = Image.new("RGBA", (w, self.bar_h), (*NEWS_DARK, 205))
        elapsed = max(0.0, t - self.t0) + self.s.offset
        # o texto já entra com um pedaço à vista (shots de 3–5 s) e anda para a esquerda; depois de uma
        # volta, emenda
        x = w * 0.62 - elapsed * self.SPEED * u
        if x < self.lab_w:
            x = self.lab_w - ((self.lab_w - x) % self.text_w)
        layer.alpha_composite(self.strip.crop((0, 0, min(self.strip.width, max(1, round(w - x))), self.bar_h)),
                              (round(x), 0))
        d = ImageDraw.Draw(layer)
        d.rectangle([0, 0, w, max(2, round(3 * u))], fill=(*gfx.accent(), 255))
        if self.label:
            d.rectangle([0, 0, self.lab_w, self.bar_h], fill=(*NEWS_RED, 255))
            la, ld = self.f_lab.getmetrics()
            d.text((28 * u, (self.bar_h - la - ld) / 2 - 2 * u), self.label, font=self.f_lab, fill=(255, 255, 255, 255))
        gfx.paste_at(canvas, layer, 0, self.y + (1 - q) * 30 * u, alpha=q * out)


# ------------------------------------------------------------ ano/modelo
class Years(Overlay):
    """Ano/modelo do carro no canto de cima: o nome (``title``) pequeno em caixa alta e os anos
    (``detail``: "2014–2019") grandes subindo de trás de um filete na cor de destaque."""

    LINE, RISE = 0.3, 0.35

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u = self.u
        fill = (*gfx.BRAND["text"], 255) if gfx.BRAND["on"] else (255, 255, 255, 255)
        self.years = gfx.outlined_text(spec.detail.upper(), gfx.font("condensed", round(118 * u)), fill,
                                       max(3, round(5 * u)), (0, 0, 0, 255))
        self.name = gfx.outlined_text(spec.title.upper(), gfx.font("oswald_bold", round(40 * u)),
                                      (255, 255, 255, 255), max(2, round(3 * u)), (0, 0, 0, 255)) if spec.title else None
        self.x, self.y = round(self.w * 0.06), round(self.h * 0.1)
        self.line_w = max(self.years.width, self.name.width if self.name else 0) + round(16 * u)

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "swipe", 0.3), (spec.start + cls.LINE, "whoosh_soft", 0.3)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        if self.name is not None:
            qn = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.3))
            gfx.paste_at(canvas, self.name, self.x - (1 - qn) * 40 * u, self.y, alpha=qn * out)
        base = self.y + (self.name.height if self.name else 0) + 10 * u + self.years.height
        ql = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.1, self.LINE))
        if ql > 0:
            d = ImageDraw.Draw(canvas)
            lw = max(3, round(6 * u))
            d.rectangle([self.x, base, self.x + self.line_w * ql, base + lw], fill=(*gfx.accent(), round(255 * out)))
        qt = gfx.ease_out_cubic(gfx.progress(t, self.t0 + 0.25, self.RISE))
        if qt <= 0:
            return
        visible = round(self.years.height * qt)
        if visible > 0:  # os anos sobem de trás do filete
            layer = self.years.crop((0, 0, self.years.width, visible))
            gfx.paste_at(canvas, layer, self.x, base - visible, alpha=out)


class PaperYears(FileCard):
    """Ficha de papel datilografada com os anos (``detail``) grandes e o carro (``title``) no rótulo."""

    def __init__(self, spec: OverlaySpec) -> None:
        swapped = OverlaySpec(**{**spec.__dict__, "title": spec.detail, "detail": spec.title or " "})
        super().__init__(swapped)
        self.y = round(self.h * 0.1)

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return FileCard.cues(OverlaySpec(**{**spec.__dict__, "title": spec.detail}))


class PaperTag(Overlay):
    """Legenda datilografada numa etiqueta de papel com fita adesiva, no canto de baixo (estilo Dossiê)."""

    CPS = 18.0

    def __init__(self, spec: OverlaySpec) -> None:
        super().__init__(spec)
        u = self.u
        self.f = gfx.font("typewriter", round(64 * u))
        self.text = gfx.wrap(spec.title.upper(), self.f, round(self.w * 0.6), 1)[0]
        pad_x, pad_y = round(34 * u), round(20 * u)
        asc, desc = self.f.getmetrics()
        self.size = (round(gfx.spaced_width(self.text, self.f, 0.08) + 2 * pad_x), asc + desc + 2 * pad_y)
        self.pad = (pad_x, pad_y)
        self.x, self.y = round(self.w * 0.06), round(self.h * 0.78)
        self.tilt = float(self.rng.uniform(-2.0, 1.0))

    @classmethod
    def cues(cls, spec: OverlaySpec) -> list[Cue]:
        return [(spec.start, "whoosh_soft", 0.3), (spec.start + 0.25, "tape", 0.3),
                (spec.start + 0.35, f"typing:{max(0.2, len(spec.title) / cls.CPS):.3f}", 0.5)]

    def draw(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        out = self.fade_out(t)
        q = gfx.ease_out_cubic(gfx.progress(t, self.t0, 0.3))
        if q <= 0:
            return
        card = Image.new("RGBA", self.size, (238, 231, 216, 255))
        d = ImageDraw.Draw(card)
        gfx.draw_spaced(d, self.pad, self.text, self.f, (52, 44, 38, 255), tracking=0.08,
                        chars=int(max(0.0, t - self.t0 - 0.35) * self.CPS), bold=max(1, round(1.5 * u)))
        layer = gfx.with_shadow(card, self.tilt, round(10 * u), (round(4 * u), round(8 * u)), 90)
        if t >= self.t0 + 0.25:
            tp = Image.new("RGBA", (round(120 * u), round(40 * u)), (226, 186, 84, 200)).rotate(-8, expand=True)
            layer.alpha_composite(tp, (round(layer.width - tp.width - 20 * u), 0))
        gfx.paste_at(canvas, layer, self.x - (1 - q) * 60 * u, self.y, alpha=q * out)


_WPUNCT = ".,!?…:;«»\"'()"


OVERLAYS: dict[str, type[Overlay]] = {
    "label": Label, "pair": Pair, "callout": Callout, "badge": Badge, "keyword": Keyword,
    "bullets": Bullets, "bignum": BigNum, "tag": Tag, "panel": Panel, "subscribe": Subscribe,
    "fuel": Fuel, "phrase": Phrase, "file": FileCard, "impact": Impact, "lowerthird": LowerThird, "ticker": Ticker,
    "crawl": Crawl, "years": Years, "paperyears": PaperYears, "papertag": PaperTag,
    "arrow": Keyword,  # a setinha saiu: planos antigos/da IA viram a legenda minimalista
}


def overlay_cues(data: dict[str, Any], frames: int, fps: int) -> list[Cue]:
    spec = OverlaySpec.from_dict({**data, "w": 1920, "h": 1080, "fps": fps, "frames": frames})
    cls = OVERLAYS.get(spec.kind)
    return cls.cues(spec) if cls else []


# -------------------------------------------------------------- saliência
def salient_point(frame: Image.Image) -> tuple[float, float]:
    """Ponto de maior destaque visual, em fração da tela, evitando as bordas.

    Soma o resíduo espectral (o que "foge do padrão" da imagem) com o contraste local
    (o que se destaca do entorno), com um leve viés para o centro."""
    gray = frame.convert("L").resize((96, 54), Image.BILINEAR)
    small = np.asarray(gray, np.float32) / 255
    spec = np.fft.fft2(small)
    log_amp = np.log(np.abs(spec) + 1e-6)
    phase = np.angle(spec)
    pad = np.pad(log_amp, 1, mode="edge")
    avg = sum(pad[dy:dy + log_amp.shape[0], dx:dx + log_amp.shape[1]] for dy in range(3) for dx in range(3)) / 9
    sr = np.abs(np.fft.ifft2(np.exp(log_amp - avg + 1j * phase))) ** 2
    sr_img = Image.fromarray((sr / (sr.max() + 1e-9) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
    surround = np.asarray(gray.filter(ImageFilter.GaussianBlur(12)), np.float32) / 255
    contrast = np.asarray(Image.fromarray((np.abs(small - surround) * 255).astype(np.uint8))
                          .filter(ImageFilter.GaussianBlur(2)), np.float32)
    sal = np.asarray(sr_img, np.float32) / 255 + contrast / (contrast.max() + 1e-6)
    yy, xx = np.mgrid[0:54, 0:96]
    sal *= 0.6 + 0.4 * np.exp(-(((xx - 48) / 55) ** 2 + ((yy - 27) / 32) ** 2))
    sal[:9], sal[-9:], sal[:, :15], sal[:, -15:] = 0, 0, 0, 0
    y, x = np.unravel_index(int(np.argmax(sal)), sal.shape)
    return round(float(x) / 96, 3), round(float(y) / 54, 3)


def _read_frame(path: Path, at: float, w: int, h: int) -> Image.Image | None:
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                              "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    if len(out) < w * h * 3:
        return None
    return Image.frombytes("RGB", (w, h), out[: w * h * 3])


# ----------------------------------------------------------------- render
def apply_overlay(base: Path, data: dict[str, Any], dest: Path, w: int, h: int, fps: int, frames: int,
                  encode_args: list[str]) -> Path:
    """Aplica o overlay sobre o shot já renderizado (``base``) e grava em ``dest``."""
    # tela em pé: o overlay é desenhado numa área que termina antes da faixa de baixo (onde o TikTok/Shorts põe a
    # descrição e os botões); rodapé, tarja e letreiro ficam visíveis
    safe_h = (round(h * VERTICAL_SAFE_BOTTOM) // 2 * 2) if h > w else h
    spec = OverlaySpec.from_dict({**data, "w": w, "h": safe_h, "fps": fps, "frames": frames})
    if spec.kind in ("callout", "tag") and spec.target is None:
        frame = _read_frame(base, min(spec.seconds * 0.5, spec.start + 0.4), 192, 108)
        spec.target = salient_point(frame) if frame is not None else (0.5, 0.52)
    ov = OVERLAYS[spec.kind](spec)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-i", str(base), "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,setsar=1,format=yuv420p[v]",
         "-map", "[v]", "-frames:v", str(frames), "-an", *encode_args, str(dest)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    blank = bytes(w * h * 4)
    try:
        for i in range(frames):
            t = i / fps
            if t < spec.start - 1e-6 or t > ov.t_out + 0.3:
                data_bytes = blank
            elif safe_h != h:
                full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                full.paste(ov.frame(i), (0, 0))
                data_bytes = full.tobytes()
            else:
                data_bytes = ov.frame(i).tobytes()
            proc.stdin.write(data_bytes)
        proc.stdin.close()
    except BrokenPipeError as exc:
        proc.kill()
        raise FFmpegError(f"ffmpeg encerrou ao aplicar overlay: {proc.stderr.read().decode(errors='ignore')[-400:]}") from exc
    rc = proc.wait(timeout=600)
    if rc != 0:
        raise FFmpegError(f"ffmpeg falhou ao aplicar overlay: {proc.stderr.read().decode(errors='ignore')[-400:]}")
    return dest
