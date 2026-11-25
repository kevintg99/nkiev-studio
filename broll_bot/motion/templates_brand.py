"""Templates de ritmo (no padrão do canal), para o vídeo não ficar só em b-roll:

* ``showcase``  — a foto do carro flutuando sobre o blueprint azul-escuro, com cotas de
  desenho técnico se desenhando em volta e o nome do carro em amarelo.
  Duas composições sorteadas pela seed: foto no centro com o nome embaixo, ou foto à
  esquerda com o nome ao lado.
* ``gallery``   — três fotos do carro flutuando em camadas (parallax) sobre o blueprint.
* ``sketch``    — uma mão com lápis desenha o carro no blueprint (os traços saem da própria
  foto) e depois a foto aparece por cima do desenho, com as cotas.
* ``dossier``   — dossiê em papel bege: título datilografado, a foto com borda tracejada e alças
  nos cantos, e uma tira de papel com a frase (visual próprio, fora do azul do canal).
* ``statement`` — tela preta com a frase falada no meio, palavra por palavra (a palavra
  forte em amarelo). Um respiro curto e marcante, como nos canais de referência.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .templates import Template, TemplateSpec

Cue = tuple[float, str, float]


# ----------------------------------------------------------------- showcase
class Showcase(Template):
    ENTER = 0.55
    VARIANTS = ("center", "side")

    @classmethod
    def variant(cls, spec: TemplateSpec) -> str:
        return cls.VARIANTS[random.Random(spec.seed * 31 + 5).randrange(len(cls.VARIANTS))]

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.mode = self.variant(spec) if type(self) is Showcase else "center"
        self.cell = round(90 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        side = self.mode == "side"
        self.pw, self.ph = (round(w * 0.5), round(h * 0.58)) if side else (round(w * 0.56), round(h * 0.56))
        self.img = self._photo_source(spec)
        self.cx, self.cy = (w * 0.31, h * 0.5) if side else (w * 0.5, h * 0.44)
        self.border = max(4, round(7 * u))
        self.tilt = float(self.rng.uniform(-1.6, 1.6))
        f_name = gfx.font("black", round((84 if side else 78) * u))
        name_lines = gfx.wrap(spec.keyword, f_name, round(w * (0.3 if side else 0.8)), 3 if side else 1)
        self.name = gfx.text_block(name_lines, f_name, self.th["text"], shadow=3) if spec.keyword else None
        f_sub = gfx.font("semibold", round(34 * u))
        self.sub = gfx.text_block([spec.subtitle], f_sub, self.th["muted"], shadow=2) if spec.subtitle else None
        self.line = self.th.get("muted", (150, 174, 226))
        self.acc = self.th["accent"]
        self.name_x = w * 0.645

    def _photo_source(self, spec: TemplateSpec):
        return self.src(spec.media[0], self.pw, self.ph, zoom=1.06,
                        pan=(float(self.rng.uniform(-1, 1)), 0.0)) if spec.media else None

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.05, "whoosh", 0.5), (cls.ENTER - 0.05, "impact_soft", 0.4), (0.9, "swipe", 0.25)]

    def _dimensions(self, canvas: Image.Image, box: tuple[float, float, float, float], p: float) -> None:
        """Cotas de desenho técnico em volta da foto (linhas com setas e marcas de canto)."""
        if p <= 0:
            return
        u = self.u
        x0, y0, x1, y1 = box
        d = ImageDraw.Draw(canvas)
        lw = max(2, round(3 * u))
        a = round(255 * min(1.0, p * 1.5))
        col = (*self.line, a)
        # cota horizontal embaixo, desenhando do meio para as pontas
        yb = y1 + 46 * u
        half = (x1 - x0) / 2 * p
        xm = (x0 + x1) / 2
        d.line([(xm - half, yb), (xm + half, yb)], fill=col, width=lw)
        for x in (xm - half, xm + half):
            d.line([(x, yb - 10 * u), (x, yb + 10 * u)], fill=col, width=lw)
        # cota vertical à direita
        xr = x1 + 46 * u
        halfv = (y1 - y0) / 2 * p
        ym = (y0 + y1) / 2
        d.line([(xr, ym - halfv), (xr, ym + halfv)], fill=col, width=lw)
        for y in (ym - halfv, ym + halfv):
            d.line([(xr - 10 * u, y), (xr + 10 * u, y)], fill=col, width=lw)
        # marcas de canto em amarelo
        L = 52 * u * min(1.0, p * 1.4)
        cw = max(3, round(6 * u))
        acc = (*self.acc, a)
        g = 20 * u
        for (cx, cy, sx, sy) in ((x0 - g, y0 - g, 1, 1), (x1 + g, y0 - g, -1, 1), (x0 - g, y1 + g, 1, -1),
                                 (x1 + g, y1 + g, -1, -1)):
            d.line([(cx, cy), (cx + sx * L, cy)], fill=acc, width=cw)
            d.line([(cx, cy), (cx, cy + sy * L)], fill=acc, width=cw)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=5 * u)
        p = gfx.ease_out_back(gfx.progress(t, 0.05, self.ENTER), 1.3)
        if self.img is not None:
            photo = gfx.bordered(self.img.frame(i), self.border, self.th["border"])
            bob = math.sin(t * 1.3) * 9 * u
            angle = self.tilt + math.sin(t * 0.9) * 0.6
            card = gfx.with_shadow(photo, angle, round(22 * u), (round(10 * u), round(22 * u)), self.th["shadow"])
            scale = 0.82 + 0.18 * p
            card = gfx.scaled(card, scale)
            y = self.cy + bob + (1 - min(1.0, p)) * 60 * u
            box = (self.cx - self.pw / 2, y - self.ph / 2, self.cx + self.pw / 2, y + self.ph / 2)
            self._dimensions(canvas, box, gfx.ease_out_cubic(gfx.progress(t, self.ENTER, 0.8)))
            gfx.paste_center(canvas, card, self.cx, y, alpha=gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.3)))
        self._draw_name(canvas, t)
        return canvas

    def _draw_name(self, canvas: Image.Image, t: float, start: float = 0.5) -> None:
        if self.name is None:
            return
        u, w, h = self.u, self.w, self.h
        q = gfx.ease_out_cubic(gfx.progress(t, start, 0.45))
        if self.mode == "side":  # nome ao lado da foto, com barra amarela crescendo
            sub_h = self.sub.height if self.sub is not None else 0
            top = h * 0.5 - (self.name.height + sub_h) / 2
            x = self.name_x + (1 - q) * 70 * u
            qb = gfx.ease_out_cubic(gfx.progress(t, start - 0.1, 0.4))
            if qb > 0:
                ImageDraw.Draw(canvas).rectangle(
                    [self.name_x - 30 * u, top + 12 * u, self.name_x - 18 * u,
                     top + 12 * u + (self.name.height + sub_h - 24 * u) * qb], fill=(*self.acc, 255))
            gfx.paste_at(canvas, self.name, x, top, alpha=q)
            if self.sub is not None:
                gfx.paste_at(canvas, self.sub, x, top + self.name.height - 6 * u, alpha=q * 0.9)
            return
        ny = h * 0.86 + (1 - q) * 24 * u
        gfx.paste_center(canvas, self.name, w / 2, ny, alpha=q)
        if self.sub is not None:
            gfx.paste_center(canvas, self.sub, w / 2, ny + self.name.height * 0.62, alpha=q * 0.9)


# ----------------------------------------------------------------- gallery
class Gallery(Template):
    """Até três fotos flutuando em camadas sobre o blueprint: cada uma entra com um pulo, gira
    de leve e desliza numa velocidade (a da frente mais rápido = profundidade). Nome embaixo."""

    STAGGER = 0.26
    # (centro x, centro y, largura em fração da tela, ângulo, profundidade)
    LAYOUTS = {
        1: ((0.5, 0.44, 0.56, -1.5, 1.0),),
        2: ((0.33, 0.42, 0.44, -2.6, 0.5), (0.68, 0.52, 0.42, 2.2, 1.0)),
        3: ((0.28, 0.38, 0.4, -3.0, 0.3), (0.73, 0.34, 0.36, 2.6, 0.6), (0.57, 0.64, 0.34, -1.4, 1.0)),
    }

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(90 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        media = list(spec.media[:3])
        self.border = max(4, round(7 * u))
        self.cards = []
        mirror = self.rng.random() < 0.5  # espelha a composição para não repetir o mesmo desenho
        for k, (x, y, frac, ang, depth) in enumerate(self.LAYOUTS.get(len(media), ())):
            cw = round(w * frac)
            ch = round(cw * 0.62)
            src = self.src(media[k], cw - 2 * self.border, ch - 2 * self.border, zoom=1.08,
                           pan=(float(self.rng.uniform(-1, 1)), 0.0))
            self.cards.append(dict(src=src, x=w * ((1 - x) if mirror else x), y=h * y,
                                   ang=-ang if mirror else ang, depth=depth, phase=float(self.rng.uniform(0, 6.28)),
                                   dir=1 if k % 2 == 0 else -1))
        f_name = gfx.font("black", round(72 * u))
        self.name = gfx.text_block(gfx.wrap(spec.keyword, f_name, round(w * 0.34), 2), f_name, self.th["text"],
                                   shadow=3) if spec.keyword else None
        self.name_right = mirror
        self.acc = self.th["accent"]

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = max(1, min(3, len(spec.media)))
        return [(0.02, "whoosh", 0.45)] + [(0.1 + k * cls.STAGGER, "pop", 0.4) for k in range(n)] + \
            [(0.1 + n * cls.STAGGER + 0.2, "swipe", 0.25)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=4 * u)
        for k, c in enumerate(self.cards):
            q = gfx.progress(t, 0.1 + k * self.STAGGER, 0.5)
            if q <= 0:
                continue
            e = gfx.ease_out_back(q, 1.5)
            photo = gfx.bordered(c["src"].frame(i), self.border, self.th["border"])
            angle = c["ang"] + math.sin(t * 0.8 + c["phase"]) * 0.7
            card = gfx.with_shadow(photo, angle, round((14 + 14 * c["depth"]) * u),
                                   (round(8 * u), round((10 + 16 * c["depth"]) * u)), self.th["shadow"])
            card = gfx.scaled(card, (0.7 + 0.3 * e) * (0.94 + 0.06 * c["depth"]))
            x = c["x"] + c["dir"] * t * (5 + 12 * c["depth"]) * u + math.sin(t * 0.6 + c["phase"]) * 6 * u
            y = c["y"] + math.sin(t * 1.1 + c["phase"]) * (6 + 6 * c["depth"]) * u + (1 - min(1.0, e)) * 90 * u
            gfx.paste_center(canvas, card, x, y, alpha=min(1.0, q * 2.5))
        if self.name is not None:
            start = 0.1 + len(self.cards) * self.STAGGER
            q = gfx.ease_out_cubic(gfx.progress(t, start, 0.45))
            x = (w * 0.95 - self.name.width) if self.name_right else w * 0.05
            y = h * 0.93 - self.name.height
            gfx.paste_at(canvas, self.name, x + (1 - q) * 50 * u * (1 if self.name_right else -1), y, alpha=q)
            if q > 0:
                bar_w = min(self.name.width, 260 * u) * q
                bx = (x + self.name.width - bar_w - 10 * u) if self.name_right else x + 10 * u
                ImageDraw.Draw(canvas).rectangle([bx, y + self.name.height - 4 * u, bx + bar_w,
                                                  y + self.name.height + 6 * u], fill=(*self.acc, 255))
        return canvas


# ------------------------------------------------------------------ sketch
HAND_FILE = Path(__file__).resolve().parents[2] / "assets" / "sketch_hand.png"


class Sketch(Showcase):
    """Uma mão com lápis desenha o carro no blueprint e a foto aparece por cima do desenho.

    Os traços são as bordas da própria foto (diferença de gaussianas: sai com cara de lápis),
    revelados da esquerda para a direita numa frente irregular; a ponta do lápis acompanha a
    frente, subindo e descendo como quem hachura. A mão é a foto de ``assets/sketch_hand.png`` (PNG
    com fundo transparente, lápis apontando para a esquerda; para trocar, é só substituir o
    arquivo). Sem o arquivo, uma mão de desenho animado feita em código (``draw_hand``)."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        dur = spec.frames / self.fps
        self.t_draw0 = 0.2
        self.draw_len = max(1.0, min(2.8, dur * 0.55))
        self.t_photo = self.t_draw0 + self.draw_len + 0.1
        self.lines_img, self.order = self._line_art()
        self.hand, self.tip = self._hand()
        self.pencil = self.acc

    def _photo_source(self, spec: TemplateSpec):  # parada: o desenho casa com a foto
        return self.src(spec.media[0], self.pw, self.ph, zoom=1.0) if spec.media else None

    def _line_art(self) -> tuple[np.ndarray, np.ndarray]:
        u = self.u
        pw, ph = self.pw, self.ph
        if self.img is None:
            return np.zeros((ph, pw), np.float32), np.zeros((ph, pw), np.float32)
        gray = self.img.frame(0).convert("L")
        # traço de lápis: só as bordas fortes (contorno, faróis, rodas), sem a textura do fundo —
        # cascalho/folhas/telhados viravam um pontilhado que enchia o quadro inteiro
        small = gray.resize((max(8, pw // 2), max(8, ph // 2)), Image.BILINEAR)
        small = small.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
        a = np.asarray(small, np.float32)
        gy, gx = np.gradient(a)
        mag = np.hypot(gx, gy)
        lo, hi = np.percentile(mag, 87), np.percentile(mag, 99.5)
        e = np.clip((mag - lo) / max(1e-3, hi - lo), 0, 1)
        # pontinho solto (textura) some; linha tem vizinhos de traço
        dens = np.asarray(Image.fromarray((e * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(3)), np.float32) / 255
        e *= np.clip((dens - 0.05) / 0.1, 0, 1)
        line = Image.fromarray((e * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
        edges = np.asarray(line.resize((pw, ph), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.6 * max(1.0, u))),
                           np.float32) / 255
        # contorno do quadro, como folha presa na prancheta
        b = max(2, round(3 * u))
        edges[:b], edges[-b:], edges[:, :b], edges[:, -b:] = 1, 1, 1, 1
        # ordem de revelação: esquerda -> direita, com frente ondulada e um pouco de ruído
        yy, xx = np.mgrid[0:ph, 0:pw].astype(np.float32)
        noise = np.asarray(Image.fromarray((self.rng.random((6, 10)) * 255).astype(np.uint8))
                           .resize((pw, ph), Image.BICUBIC), np.float32) / 255
        order = xx / pw * 0.82 + np.sin(yy / ph * 7.0) * 0.04 + noise * 0.14
        order = (order - order.min()) / max(1e-6, order.max() - order.min())
        return edges.astype(np.float32), order.astype(np.float32)

    def _hand(self) -> tuple[Image.Image, tuple[int, int]]:
        if HAND_FILE.exists():
            return load_hand(HAND_FILE, self.h * 0.5)
        return draw_hand(self.u, self.acc)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        dur = spec.frames / spec.fps
        draw_len = max(1.0, min(2.8, dur * 0.55))
        return [(0.1, "whoosh_soft", 0.3), (0.2, f"pencil:{draw_len:.3f}", 0.35),
                (0.3 + draw_len, "impact_soft", 0.4), (0.4 + draw_len, "swipe", 0.25)]

    def _pen(self, p: float, t: float) -> tuple[float, float]:
        """Ponta do lápis sobre a frente do desenho, hachurando para cima e para baixo."""
        x0, y0 = self.cx - self.pw / 2, self.cy - self.ph / 2
        # vai e volta na vertical num ritmo de mão (não tremendo), acompanhando a frente do desenho
        x = x0 + self.pw * min(1.0, p * 0.96) + math.sin(t * 7.0) * 5 * self.u
        y = y0 + self.ph * (0.5 + 0.36 * math.sin(t * 4.2) + 0.06 * math.sin(t * 11.0))
        return x, y

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=4 * u)
        p = gfx.ease_in_out(gfx.progress(t, self.t_draw0, self.draw_len))
        x0, y0 = round(self.cx - self.pw / 2), round(self.cy - self.ph / 2)
        qphoto = gfx.ease_out_cubic(gfx.progress(t, self.t_photo, 0.55))
        # traços de lápis na cor clara do blueprint, somem quando a foto entra
        if p > 0 and qphoto < 1:
            mask = self.lines_img * (self.order <= p) * (1 - qphoto * 0.9)
            layer = Image.new("RGBA", (self.pw, self.ph), (232, 240, 255, 0))
            layer.putalpha(Image.fromarray((mask * 255).astype(np.uint8), "L"))
            canvas.alpha_composite(layer, (x0, y0))
        if self.img is not None and qphoto > 0:
            photo = gfx.bordered(self.img.frame(i), self.border, self.th["border"])
            card = gfx.with_shadow(photo, 0, round(20 * u), (round(10 * u), round(20 * u)), self.th["shadow"])
            gfx.paste_center(canvas, gfx.scaled(card, 0.97 + 0.03 * qphoto), self.cx, self.cy, alpha=qphoto)
            self._dimensions(canvas, (x0, y0, x0 + self.pw, y0 + self.ph),
                             gfx.ease_out_cubic(gfx.progress(t, self.t_photo + 0.2, 0.7)))
        # a mão entra desenhando e sai pela direita quando termina
        gone = gfx.ease_in_out(gfx.progress(t, self.t_photo - 0.1, 0.45))
        arrive = gfx.ease_out_cubic(gfx.progress(t, 0.0, self.t_draw0 + 0.15))
        if gone < 1:
            px, py = self._pen(p, t)
            px += (1 - arrive) * 0.3 * w + gone * 0.5 * w
            py += (1 - arrive) * 0.3 * h + gone * 0.35 * h
            gfx.paste_at(canvas, self.hand, px - self.tip[0], py - self.tip[1])
        self._draw_name(canvas, t, start=self.t_photo + 0.1)
        return canvas


def load_hand(path: Path, height: float) -> tuple[Image.Image, tuple[int, int]]:
    """Mão de verdade (PNG com fundo transparente, lápis apontando para a esquerda). A ponta do
    lápis é o ponto opaco mais à esquerda da imagem: não precisa marcar nada."""
    img = Image.open(path).convert("RGBA")
    scale = height / max(1, img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    alpha = np.asarray(img.getchannel("A")) > 128
    cols = np.flatnonzero(alpha.any(axis=0))
    if not len(cols):
        return img, (0, 0)
    x = int(cols[0])
    ys = np.flatnonzero(alpha[:, x])
    return img, (x, int(ys.mean()))


def draw_hand(u: float, pencil: tuple[int, int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """Mão de luva (estilo das animações de quadro branco) segurando o lápis: ponta do lápis à
    esquerda, indicador por cima dele, polegar por baixo, os outros dedos dobrados e o resto do lápis
    saindo por cima dos nós. Cada parte é uma silhueta com contorno só por fora (sem "bolhas").
    Devolve a imagem e a posição da ponta do lápis nela."""
    k = u * 1.15
    out = Image.new("RGBA", (round(900 * k), round(900 * k)), (0, 0, 0, 0))  # a manga sai do quadro
    tip = np.array([24.0, 250.0])
    d = np.array([math.cos(math.radians(-30)), math.sin(math.radians(-30))])  # lápis subindo p/ a direita
    n = np.array([-d[1], d[0]])  # perpendicular (para baixo)
    fore = np.array([math.cos(math.radians(48)), math.sin(math.radians(48))])  # antebraço p/ baixo-direita
    ow, thin = max(2, round(5 * k)), max(1, round(2.6 * k))
    glove, sleeve, crease = (250, 250, 252, 255), (205, 212, 228, 255), (52, 56, 74, 255)

    def P(a: float, b: float = 0.0) -> tuple[float, float]:
        p = tip + d * a + n * b
        return (p[0] * k, p[1] * k)

    def outlined(mask: Image.Image, fill: tuple) -> None:
        out.paste(Image.new("RGBA", out.size, (0, 0, 0, 255)), (0, 0), mask.filter(ImageFilter.MaxFilter(2 * ow + 1)))
        out.paste(Image.new("RGBA", out.size, fill), (0, 0), mask)

    def capsule(m: ImageDraw.ImageDraw, p0, p1, r: float) -> None:
        rr = r * k
        m.line([p0, p1], fill=255, width=round(2 * rr))
        for (x, y) in (p0, p1):
            m.ellipse([x - rr, y - rr, x + rr, y + rr], fill=255)

    def shape(draw) -> Image.Image:
        mask = Image.new("L", out.size, 0)
        draw(ImageDraw.Draw(mask))
        return mask

    # manga, atrás de tudo
    wrist = tip + d * 215 + n * 95
    a0, a1, side = wrist + fore * 40, wrist + fore * 900, np.array([-fore[1], fore[0]])
    outlined(shape(lambda m: m.polygon([tuple((a0 + side * 70) * k), tuple((a1 + side * 95) * k),
                                        tuple((a1 - side * 95) * k), tuple((a0 - side * 70) * k)], fill=255)), sleeve)
    # lápis inteiro (a mão cobre o meio)
    pen, W = ImageDraw.Draw(out), 13
    pen.polygon([P(42, -W), P(318, -W), P(318, W), P(42, W)], fill=(*pencil, 255), outline=(0, 0, 0, 255), width=ow)
    pen.line([P(42, -W / 3), P(318, -W / 3)], fill=(222, 160, 0, 255), width=max(1, round(3 * k)))
    pen.polygon([P(318, -W), P(342, -W), P(342, W), P(318, W)], fill=(236, 128, 138, 255),
                outline=(0, 0, 0, 255), width=ow)
    pen.polygon([P(0, 0), P(42, -W), P(42, W)], fill=(240, 206, 160, 255), outline=(0, 0, 0, 255), width=ow)
    pen.polygon([P(0, 0), P(15, -W / 3), P(15, W / 3)], fill=(38, 38, 46, 255))

    def back(m: ImageDraw.ImageDraw) -> None:  # costas da mão + dedos dobrados
        c, rot = tip + d * 205 + n * 70, math.radians(-12)
        m.polygon([((c[0] + 100 * math.cos(q) * math.cos(rot) - 82 * math.sin(q) * math.sin(rot)) * k,
                    (c[1] + 100 * math.cos(q) * math.sin(rot) + 82 * math.sin(q) * math.cos(rot)) * k)
                   for q in np.linspace(0, 2 * math.pi, 60)], fill=255)
        capsule(m, P(150, 60), P(118, 92), 30)
        capsule(m, P(190, 98), P(160, 118), 28)
        capsule(m, P(228, 124), P(205, 140), 25)

    outlined(shape(back), glove)
    outlined(shape(lambda m: capsule(m, P(170, 40), P(78, 16), 22)), glove)    # polegar por baixo do lápis
    outlined(shape(lambda m: capsule(m, P(210, -6), P(66, -14), 22)), glove)   # indicador por cima
    lines = ImageDraw.Draw(out)
    x, y = P(150, -10)  # nó do indicador
    lines.arc([x - 14 * k, y - 16 * k, x + 14 * k, y + 16 * k], 200, 340, fill=crease, width=thin)
    for (a, b) in ((74, -14), (86, 16)):  # unhas
        x, y = P(a, b)
        lines.arc([x - 9 * k, y - 9 * k, x + 9 * k, y + 9 * k], 110, 250, fill=(160, 168, 190, 255), width=thin)
    return out, (round(tip[0] * k), round(tip[1] * k))



# ----------------------------------------------------------------- dossier
class Dossier(Template):
    """Dossiê em papel (visual próprio, fora do azul do canal): papel bege quadriculado, título
    datilografado e espaçado em cima, a foto com borda tracejada vermelha e branca e alças nos
    cantos (como seleção num editor) e uma tira de papel embaixo com a frase.
    ``keyword`` = título (o carro ou o assunto); ``subtitle`` = a frase da tira."""

    TITLE_CPS = 18.0
    LABEL_CPS = 24.0

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(64 * u)
        self.bg = gfx.background("dossier", w, h, self.cell).convert("RGBA")
        self.ink = (52, 44, 38)
        self.red = (196, 46, 34)
        self.box = (round(w * 0.19), round(h * 0.16), round(w * 0.6), round(h * 0.69))  # x, y, largura, altura
        _, _, bw, bh = self.box
        self.img = self.src(spec.media[0], bw, bh, zoom=1.06, pan=(float(self.rng.uniform(-1, 1)), 0.0)) \
            if spec.media else None
        self.tilt = float(self.rng.uniform(-0.8, 0.8))
        self.border_pad = round(24 * u)  # espaço para as alças dos cantos
        self.f_title = gfx.font("typewriter", round(50 * u))
        self.title = spec.keyword.upper()
        self.f_label = gfx.font("typewriter", round(58 * u))
        self.label = spec.subtitle.upper()
        lw = gfx.text_width(self.label, self.f_label) if self.label else 0
        self.strip = self._strip(lw) if self.label else None

    def _strip(self, text_w: int) -> Image.Image:
        """Tira de papel para a frase (um pouco torta, com sombra)."""
        u = self.u
        pad_x, pad_y = round(30 * u), round(18 * u)
        asc, desc = self.f_label.getmetrics()
        img = Image.new("RGBA", (text_w + 2 * pad_x, asc + desc + 2 * pad_y), (238, 231, 216, 255))
        noise = (self.rng.standard_normal((img.height, img.width)) * 3).astype(np.int16)
        arr = np.asarray(img).astype(np.int16)
        arr[..., :3] = np.clip(arr[..., :3] + noise[..., None], 0, 255)
        img = Image.fromarray(arr.astype(np.uint8), "RGBA")
        self.strip_pad = (pad_x, pad_y)
        return img

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        out: list[Cue] = [(0.05, "whoosh_soft", 0.35), (0.45, "snap", 0.35)]
        if spec.keyword:
            out.append((0.25, f"typing:{max(0.2, len(spec.keyword) / cls.TITLE_CPS):.3f}", 0.5))
        if spec.subtitle:
            out.append((0.8, "swipe", 0.25))
            out.append((1.0, f"typing:{max(0.2, len(spec.subtitle) / cls.LABEL_CPS):.3f}", 0.5))
        return out

    def _border(self, size: tuple[int, int], t: float) -> Image.Image:
        """Moldura tracejada vermelha e branca andando devagar ("formiguinhas") + alças nos cantos."""
        u = self.u
        W, H = size
        m = self.border_pad
        img = Image.new("RGBA", (W + 2 * m, H + 2 * m), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        lw, dash = max(4, round(7 * u)), round(26 * u)
        off = int(t * 30 * u) % (2 * dash)
        per = 2 * (W + H)
        pos = -off
        while pos < per:
            for k, color in enumerate((self.red, (250, 248, 242))):
                a, b = pos + k * dash, pos + (k + 1) * dash
                self._seg(d, max(0, a), min(per, b), W, H, color, lw, m)
            pos += 2 * dash
        hs = round(30 * u)
        for (cx, cy) in ((m, m), (m + W, m), (m, m + H), (m + W, m + H)):
            d.rectangle([cx - hs / 2, cy - hs / 2, cx + hs / 2, cy + hs / 2], fill=(250, 248, 242, 255),
                        outline=(30, 26, 24, 255), width=max(2, round(4 * u)))
        return img

    @staticmethod
    def _seg(d: ImageDraw.ImageDraw, a: float, b: float, W: int, H: int, color: tuple, lw: int, m: int) -> None:
        """Pedaço [a, b] do perímetro (sentido horário a partir do canto de cima à esquerda)."""
        if b <= a:
            return
        edges = (((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0)))
        start = 0.0
        for (x0, y0), (x1, y1) in edges:
            L = abs(x1 - x0) + abs(y1 - y0)
            s0, s1 = max(a, start), min(b, start + L)
            if s1 > s0:
                f0, f1 = (s0 - start) / L, (s1 - start) / L
                d.line([(m + x0 + (x1 - x0) * f0, m + y0 + (y1 - y0) * f0),
                        (m + x0 + (x1 - x0) * f1, m + y0 + (y1 - y0) * f1)], fill=(*color, 255), width=lw)
            start += L

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u = self.u
        canvas = self.bg.copy()
        x, y, bw, bh = self.box
        # título datilografado e espaçado
        if self.title:
            n = int(max(0.0, t - 0.25) * self.TITLE_CPS)
            gfx.draw_spaced(ImageDraw.Draw(canvas), (x + 8 * u, y - 82 * u), self.title, self.f_title,
                            (*self.ink, 255), tracking=0.42, chars=n, bold=max(1, round(1.2 * u)))
        # foto subindo, com a moldura tracejada
        q = gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.45))
        if self.img is not None and q > 0:
            m = self.border_pad
            layer = Image.new("RGBA", (bw + 2 * m, bh + 2 * m), (0, 0, 0, 0))
            layer.paste(self.img.frame(i).convert("RGB"), (m, m))
            if t >= 0.35:
                layer.alpha_composite(self._border((bw, bh), t), (0, 0))
            card = gfx.with_shadow(layer, self.tilt, round(16 * u), (round(6 * u), round(12 * u)), 60)
            gfx.paste_center(canvas, gfx.scaled(card, 0.96 + 0.04 * q), x + bw / 2, y + bh / 2 + (1 - q) * 50 * u,
                             alpha=q)
        # tira de papel com a frase
        if self.strip is not None:
            qs = gfx.ease_out_cubic(gfx.progress(t, 0.8, 0.35))
            if qs > 0:
                strip = self.strip.copy()
                n = int(max(0.0, t - 1.0) * self.LABEL_CPS)
                gfx.draw_spaced(ImageDraw.Draw(strip), self.strip_pad, self.label, self.f_label,
                                (*self.ink, 255), tracking=0.0, chars=n, bold=max(1, round(1.5 * u)))
                card = gfx.with_shadow(strip, -0.6, round(10 * u), (round(4 * u), round(8 * u)), 70)
                sx = x + 10 * u - (1 - qs) * 80 * u
                gfx.paste_at(canvas, card, sx, y + bh - strip.height * 0.05 + 6 * u, alpha=qs)
        return canvas


# ---------------------------------------------------------------- statement
class Statement(Template):
    """Tela preta com a frase falada. ``body`` = a frase; ``labels`` = palavras em destaque
    (amarelo); sem destaque, a palavra mais longa do fim da frase. Para não ficar repetitivo,
    cada tela preta sorteia (pela seed) uma de quatro animações:

    * ``pop``    — centralizada, palavra por palavra saltando, traço amarelo embaixo;
    * ``type``   — máquina de escrever, letra por letra, com cursor amarelo piscando;
    * ``zoom``   — a frase inteira surge e se aproxima devagar, entre dois filetes amarelos;
    * ``slide``  — alinhada à esquerda, as palavras deslizam da esquerda, barra amarela ao lado.
    """

    VARIANTS = ("pop", "type", "zoom", "slide")
    CPS = 22.0  # letras por segundo no "type"

    @classmethod
    def variant(cls, spec: TemplateSpec) -> str:
        return cls.VARIANTS[(spec.seed * 2654435761 >> 7) % len(cls.VARIANTS)]

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.mode = self.variant(spec)
        self.text = " ".join((spec.body or spec.keyword or "").split())
        left = self.mode in ("type", "slide")
        width = round(w * (0.76 if left else 0.8))
        self.font = gfx.font("black", round(124 * u))
        lines = gfx.wrap(self.text, self.font, width, 3)
        if len(lines) == 3:  # frase longa: letra menor
            self.font = gfx.font("black", round(96 * u))
            lines = gfx.wrap(self.text, self.font, round(width * 1.05), 3)
        self.lines = lines
        words = [wd for ln in lines for wd in ln.split()]
        marks = {gfx.safe_text(m).lower().strip(PUNCT) for lb in spec.labels for m in lb.split()}
        if not marks and words:
            key = max(words[-4:], key=lambda s: len(s.strip(PUNCT)))
            marks = {key.lower().strip(PUNCT)}
        self.hi = gfx.accent() if gfx.BRAND["on"] else self.th["accent"]
        self.stroke = max(2, round(5 * u))
        asc, desc = self.font.getmetrics()
        self.lh = round((asc + desc) * 1.08)
        total_h = self.lh * len(lines)
        self.y0 = h / 2 - total_h / 2
        self.x_left = w * 0.12
        self.words: list[tuple[str, float, float, bool]] = []
        space = gfx.text_width(" ", self.font)
        for k, ln in enumerate(lines):
            lw = gfx.text_width(ln, self.font)
            x = self.x_left if left else w / 2 - lw / 2
            for wd in ln.split():
                self.words.append((wd, x, self.y0 + k * self.lh, wd.lower().strip(PUNCT) in marks))
                x += gfx.text_width(wd, self.font) + space
        dur = spec.frames / self.fps
        self.step = min(0.22, max(0.07, dur * 0.45 / max(1, len(self.words))))
        self.text_w = max((gfx.text_width(ln, self.font) for ln in lines), default=0)
        self.bar_y = self.y0 + total_h + 18 * u
        self.total_h = total_h
        self.full = self._render_all() if self.mode == "zoom" else None

    def _render_all(self) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for wd, x, y, hot in self.words:
            d.text((x, y), gfx.safe_text(wd), font=self.font, fill=(*self.hi, 255) if hot else (255, 255, 255, 255),
                   stroke_width=self.stroke, stroke_fill=(0, 0, 0, 255))
        return img

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        text = spec.body or spec.keyword or ""
        mode = cls.variant(spec)
        if mode == "type":
            return [(0.05, f"typing:{max(0.2, len(text) / cls.CPS):.3f}", 0.55)]
        if mode == "zoom":
            return [(0.0, "impact_soft", 0.5), (0.05, "whoosh", 0.35)]
        n = max(1, len(text.split()))
        dur = spec.frames / spec.fps
        step = min(0.22, max(0.07, dur * 0.45 / n))
        name = "swipe" if mode == "slide" else "tick"
        vol = 0.14 if mode == "slide" else 0.18
        return [(0.02, "impact_soft", 0.45)] + [(0.08 + k * step, name, vol) for k in range(min(n, 14))]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        w, h = self.w, self.h
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        getattr(self, f"_draw_{self.mode}")(canvas, t)
        return canvas

    def _word(self, d: ImageDraw.ImageDraw, wd: str, x: float, y: float, hot: bool, a: int) -> None:
        fill = (*self.hi, a) if hot else (255, 255, 255, a)
        d.text((x, y), gfx.safe_text(wd), font=self.font, fill=fill, stroke_width=self.stroke,
               stroke_fill=(0, 0, 0, a))

    def _draw_pop(self, canvas: Image.Image, t: float) -> None:
        u, w = self.u, self.w
        d = ImageDraw.Draw(canvas)
        for k, (wd, x, y, hot) in enumerate(self.words):
            q = gfx.progress(t, 0.08 + k * self.step, 0.18)
            if q > 0:
                dy = (1 - gfx.ease_out_back(q, 2.0)) * 26 * u
                self._word(d, wd, x, y + dy, hot, round(255 * gfx.ease_out_cubic(q)))
        qb = gfx.ease_out_cubic(gfx.progress(t, 0.08 + len(self.words) * self.step, 0.35))
        if qb > 0 and self.text_w:
            half = self.text_w / 4 * qb
            d.rectangle([w / 2 - half, self.bar_y, w / 2 + half, self.bar_y + max(3, 7 * u)], fill=(*self.hi, 255))

    def _draw_type(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        d = ImageDraw.Draw(canvas)
        shown = int(max(0.0, t - 0.05) * self.CPS)
        count, cx, cy = 0, self.x_left, self.y0
        for wd, x, y, hot in self.words:
            if count >= shown:
                break
            part = wd[: max(0, shown - count)]
            self._word(d, part, x, y, hot, 255)
            count += len(wd) + 1
            cx, cy = x + gfx.text_width(part, self.font) + 6 * u, y
        if int(t * 2.4) % 2 == 0 or count < len(self.text):  # cursor piscando depois de terminar
            asc, _ = self.font.getmetrics()
            d.rectangle([cx, cy + asc * 0.12, cx + max(4, 12 * u), cy + asc * 1.02], fill=(*self.hi, 255))

    def _draw_zoom(self, canvas: Image.Image, t: float) -> None:
        u, w, h = self.u, self.w, self.h
        dur = self.s.frames / self.fps
        q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.45))
        zoom = 0.94 + 0.06 * q + 0.05 * min(1.0, t / max(dur, 0.1))
        layer = gfx.scaled(self.full, zoom)
        gfx.paste_center(canvas, layer, w / 2, h / 2, alpha=q)
        d = ImageDraw.Draw(canvas)
        half = self.text_w / 2 * gfx.ease_out_cubic(gfx.progress(t, 0.2, 0.5))
        for y in (self.y0 - 28 * u, self.y0 + self.total_h + 20 * u):
            d.rectangle([w / 2 - half, y, w / 2 + half, y + max(2, 4 * u)], fill=(*self.hi, 255))

    def _draw_slide(self, canvas: Image.Image, t: float) -> None:
        u = self.u
        d = ImageDraw.Draw(canvas)
        qbar = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.3))
        bar_x = self.x_left - 40 * u
        d.rectangle([bar_x, self.y0 + (1 - qbar) * self.total_h / 2, bar_x + max(4, 12 * u),
                     self.y0 + self.total_h - (1 - qbar) * self.total_h / 2], fill=(*self.hi, 255))
        for k, (wd, x, y, hot) in enumerate(self.words):
            q = gfx.progress(t, 0.12 + k * self.step, 0.22)
            if q > 0:
                dx = (1 - gfx.ease_out_cubic(q)) * -60 * u
                self._word(d, wd, x + dx, y, hot, round(255 * gfx.ease_out_cubic(q)))


PUNCT = ".,!?…:;«»\"'"


BRAND_TEMPLATES: dict[str, type[Template]] = {"showcase": Showcase, "gallery": Gallery, "sketch": Sketch,
                                               "dossier": Dossier, "statement": Statement}


def brand_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = BRAND_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
