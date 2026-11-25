"""Aplica o plano de efeitos (``plan.py``) quadro a quadro no vídeo montado.

O vídeo entra decodificado pelo FFmpeg (RGB por stdout), cada quadro passa pelos efeitos ativos no Pillow e sai
para outro FFmpeg (stdin), que acrescenta a correção de cor, o grão, a vinheta e, se pedido, a legenda (``post_vf``)
num encode só. O áudio é copiado do vídeo de entrada (os efeitos sonoros já estão na trilha).

Quadro sem efeito nenhum passa direto (sem Pillow), então o custo fica nos trechos com efeito.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from ..log import get_logger
from ..media import FFmpegError
from ..motion import gfx
from ..motion.stickers import sticker
from .plan import FxEvent, FxPlan

log = get_logger("shortsfx")

GRADE_VF = "eq=contrast=1.06:saturation=1.12:brightness=-0.008,vignette=angle=0.5,noise=alls=4:allf=t"
RED_INK = (232, 36, 44)
CAPTION_TOP = 0.56  # a legenda fica de 0,56 a 0,74 da altura: desenho nenhum desce até ela


def _ease_out(x: float) -> float:
    return gfx.ease_out_cubic(gfx.clamp01(x))


def _ease_io(x: float) -> float:
    return gfx.ease_in_out(gfx.clamp01(x))


# ------------------------------------------------------------------------------------------ câmera
def zoom_at(plan_events: list[FxEvent], t: float) -> float:
    """Zoom da câmera no instante ``t`` (1 = sem zoom): socos, zoom lento e a entrada do gancho somados."""
    z = 0.0
    for e in plan_events:
        if not (e.t0 - 1e-6 <= t < e.t1):
            continue
        p = t - e.t0
        a = float(e.data.get("zoom", 0.1))
        if e.kind == "punch":
            if e.data.get("intro"):  # entrada: começa perto e abre
                z += a * (1 - _ease_out(p / (e.t1 - e.t0)))
            elif p < 0.06:
                z += a * _ease_out(p / 0.06)
            elif p < 0.26:
                z += a
            else:
                z += a * (1 - _ease_io((p - 0.26) / max(0.05, e.t1 - e.t0 - 0.26)))
        elif e.kind == "drift":
            dur = e.t1 - e.t0
            back = min(0.3, dur * 0.2)
            z += a * (_ease_io(p / max(0.1, dur - back)) if p < dur - back else 1 - _ease_io((p - dur + back) / back))
        elif e.kind == "shake":
            z += 0.035 * math.exp(-p / 0.2)
    return 1.0 + z


def shake_at(plan_events: list[FxEvent], t: float, u: float) -> tuple[float, float, float]:
    dx = dy = rot = 0.0
    for e in plan_events:
        if e.kind != "shake" or not (e.t0 - 1e-6 <= t < e.t1):
            continue
        p = t - e.t0
        k = math.exp(-p / 0.11)
        dx += 24 * u * k * math.sin(2 * math.pi * 17 * p + 0.3)
        dy += 18 * u * k * math.sin(2 * math.pi * 13 * p + 1.7)
        rot += 0.8 * k * math.sin(2 * math.pi * 11 * p)
    return dx, dy, rot


def camera(img: Image.Image, z: float, dx: float = 0.0, dy: float = 0.0, rot: float = 0.0,
           cy: float = 0.45) -> Image.Image:
    """Zoom ``z`` em volta de (meio, ``cy``·altura), deslocado (dx, dy); sem borda preta. ``resize`` com ``box``
    fracionário: suave (sem o tremido do zoom em pixel inteiro) e ~4x mais rápido que a transformação afim."""
    if abs(z - 1) < 1e-4 and abs(dx) < 0.05 and abs(dy) < 0.05:
        return img
    w, h = img.size
    z = max(z, 1.0 + 2 * max(abs(dx) / w, abs(dy) / h))  # a tremida nunca mostra a borda
    bw, bh = w / z, h / z
    x0 = min(max(w / 2 - (w / 2 + dx) / z, 0.0), w - bw)
    y0 = min(max(h * cy - (h * cy + dy) / z, 0.0), h - bh)
    return img.resize((w, h), Image.BICUBIC, box=(x0, y0, x0 + bw, y0 + bh))


def salient(img: Image.Image) -> tuple[float, float]:
    """Ponto com mais detalhe (bordas fortes e cor que se destaca: farol, roda, logotipo), em fração da tela, dentro
    da faixa onde o desenho pode ficar (acima da legenda, longe das bordas)."""
    small = img.convert("RGB").resize((108, 192), Image.BILINEAR)
    g = np.asarray(small.convert("L"), np.float32) / 255
    gx, gy = np.abs(np.diff(g, axis=1, prepend=g[:, :1])), np.abs(np.diff(g, axis=0, prepend=g[:1]))
    edges = np.asarray(Image.fromarray(np.clip((gx + gy) * 400, 0, 255).astype(np.uint8))
                       .filter(ImageFilter.GaussianBlur(5)), np.float32) / 255
    hsv = np.asarray(small.convert("HSV"), np.float32) / 255
    sat = hsv[..., 1] * hsv[..., 2]
    pop = np.abs(sat - float(sat.mean()))
    score = edges + 0.6 * pop
    yy, xx = np.mgrid[0:192, 0:108]
    score *= 0.75 + 0.25 * np.exp(-(((xx - 54) / 40) ** 2 + ((yy - 70) / 60) ** 2))
    mask = np.zeros_like(score)
    mask[round(192 * 0.2):round(192 * 0.52), round(108 * 0.18):round(108 * 0.82)] = 1
    y, x = np.unravel_index(int(np.argmax(score * mask)), score.shape)
    return round(float(x) / 108, 3), round(float(y) / 192, 3)


# ------------------------------------------------------------------------------------------ peças
def _text_layer(text: str, fnt, fill: tuple, stroke: int, shadow: int = 0) -> Image.Image:
    text = gfx.safe_text(text)
    x0, y0, x1, y1 = fnt.getbbox(text, stroke_width=stroke)
    pad = stroke + shadow * 2 + 4
    img = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad - x0, pad - y0 + shadow), text, font=fnt, fill=(0, 0, 0, 150),
                                stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(shadow)))
    d.text((pad - x0, pad - y0), text, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    return img


def _fit(weight: str, size: float, text: str, max_w: float):
    fnt = gfx.font(weight, max(10, round(size)))
    width = gfx.text_width(text or " ", fnt)
    if width > max_w:
        fnt = gfx.font(weight, max(10, round(size * max_w / width)))
    return fnt


def _paste(canvas: Image.Image, layer: Image.Image, cx: float, cy: float, scale: float = 1.0,
           alpha: float = 1.0, rot: float = 0.0) -> None:
    if alpha <= 0.01 or scale <= 0.01:
        return
    if abs(rot) > 0.05:
        layer = layer.rotate(rot, resample=Image.BILINEAR, expand=True)
    if abs(scale - 1) > 1e-3:
        layer = layer.resize((max(1, round(layer.width * scale)), max(1, round(layer.height * scale))),
                             Image.BILINEAR)
    gfx.paste_center(canvas, layer, cx, cy, alpha)


def _alpha_shadow(layer: Image.Image, blur: int, dy: int, opacity: int = 120) -> Image.Image:
    """Sombra com o formato da camada (pela transparência), não um retângulo."""
    pad = blur * 2 + abs(dy)
    out = Image.new("RGBA", (layer.width + 2 * pad, layer.height + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    a = Image.new("L", out.size, 0)
    a.paste(layer.getchannel("A").point(lambda v: v * opacity // 255), (pad, pad + dy))
    shadow.putalpha(a.filter(ImageFilter.GaussianBlur(blur)))
    out.alpha_composite(shadow)
    out.alpha_composite(layer, (pad, pad))
    return out


def _rounded_mask(size: tuple[int, int], r: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=max(0, r), fill=255)
    return m


def _stamp(text: str, u: float, seed: int) -> Image.Image:
    """Carimbo de borracha vermelho: texto pesado com moldura dupla e falhas de tinta."""
    fnt = gfx.font("heavy", round(96 * u))
    tw = gfx.text_width(text, fnt)
    asc, desc = fnt.getmetrics()
    pad_x, pad_y = round(36 * u), round(18 * u)
    bw, bh = tw + 2 * pad_x, asc + desc + 2 * pad_y
    img = Image.new("RGBA", (bw + 20, bh + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(3, round(8 * u))
    d.rounded_rectangle([10, 10, 10 + bw, 10 + bh], radius=round(14 * u), outline=(*RED_INK, 255), width=lw)
    d.rounded_rectangle([10 + lw * 2, 10 + lw * 2, 10 + bw - lw * 2, 10 + bh - lw * 2], radius=round(8 * u),
                        outline=(*RED_INK, 255), width=max(2, lw // 3))
    d.text((10 + pad_x, 10 + pad_y), text, font=fnt, fill=(*RED_INK, 255))
    rng = np.random.default_rng(seed)
    holes = (rng.random((img.height // 3 + 1, img.width // 3 + 1)) > 0.86).astype(np.uint8) * 255
    holes_img = Image.fromarray(holes).resize(img.size, Image.NEAREST).filter(ImageFilter.GaussianBlur(1.2))
    alpha = ImageChops.subtract(img.getchannel("A"), holes_img.point(lambda a: a * 0.7))
    img.putalpha(alpha)
    return img


def _pill(lines: list[tuple[str, Any, tuple]], u: float, icon: Image.Image | None = None) -> Image.Image:
    pad_x, pad_y, gap = round(34 * u), round(20 * u), round(6 * u)
    sizes = [(gfx.text_width(t, f), sum(f.getmetrics())) for t, f, _ in lines]
    iw = icon.width + round(14 * u) if icon is not None else 0
    bw = max(s[0] for s in sizes) + 2 * pad_x + iw
    bh = sum(s[1] for s in sizes) + gap * (len(lines) - 1) + 2 * pad_y
    if icon is not None:
        bh = max(bh, icon.height + 2 * pad_y // 2)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=bh // 2 if len(lines) == 1 else round(28 * u),
                        fill=(12, 12, 18, 215), outline=(255, 255, 255, 60), width=max(1, round(2 * u)))
    if icon is not None:
        img.alpha_composite(icon, (pad_x - round(10 * u), (bh - icon.height) // 2))
    y = (bh - (sum(s[1] for s in sizes) + gap * (len(lines) - 1))) // 2
    for (text, fnt, color), (tw, th) in zip(lines, sizes):
        d.text((pad_x + iw + (bw - 2 * pad_x - iw - tw) / 2, y), text, font=fnt, fill=(*color, 255))
        y += th + gap
    return _alpha_shadow(img, round(14 * u), round(8 * u), 120)


# ------------------------------------------------------------------------------------------ efeitos
class _Active:
    """Estado de um efeito enquanto ele está na tela (ponto de destaque, quadro congelado, camadas prontas)."""

    def __init__(self) -> None:
        self.cache: dict[int, dict[str, Any]] = {}

    def get(self, e: FxEvent) -> dict[str, Any]:
        return self.cache.setdefault(id(e), {})

    def drop_before(self, events: list[FxEvent], t: float) -> None:
        alive = {id(e) for e in events if e.t1 >= t - 0.5}
        for k in list(self.cache):
            if k not in alive:
                del self.cache[k]


class FxRenderer:
    def __init__(self, plan: FxPlan, w: int, h: int, fps: int, accent: tuple[int, int, int] = (255, 204, 0),
                 lang: str = "en", seed: int = 0) -> None:
        self.plan, self.w, self.h, self.fps = plan, w, h, fps
        self.u = w / 1080
        self.acc = accent
        self.lang = lang
        self.seed = seed
        self.state = _Active()
        self.events = sorted(plan.events, key=lambda e: e.t0)
        self.first_out: Image.Image | None = None
        self.last_out: Image.Image | None = None
        self.last_raw: bytes | None = None

    def previous(self) -> Image.Image | None:
        """O último quadro que saiu (para o congelamento e o corte em cartas)."""
        if self.last_out is None and self.last_raw is not None:
            self.last_out = Image.frombuffer("RGB", (self.w, self.h), self.last_raw, "raw", "RGB", 0, 1)
        return self.last_out
        gfx.set_number_locale(lang)

    def active(self, t: float) -> list[FxEvent]:
        return [e for e in self.events if e.t0 - 1e-6 <= t < e.t1]

    # ----------------------------------------------------------------------------------- quadro
    def process(self, frame: Image.Image, i: int) -> Image.Image:
        t = i / self.fps
        act = self.active(t)
        if i % 30 == 0:
            self.state.drop_before(self.events, t)
        kinds = {e.kind for e in act}
        out = frame
        frozen = next((e for e in act if e.kind == "freeze"), None)
        stack = next((e for e in act if e.kind == "stack"), None)
        if frozen is not None:
            out = self._freeze(frozen, frame, t)
        elif stack is not None and self.previous() is not None:
            out = self._stack(stack, frame, t)
        else:
            z = zoom_at(act, t)
            dx, dy, rot = shake_at(act, t, self.u)
            if abs(z - 1) > 1e-4 or dx or dy:
                z_prev = zoom_at(act, t - 1 / self.fps)
                if abs(z - z_prev) > 0.012:  # zoom rápido: desfoque de movimento (média de 3 posições)
                    mids = [camera(frame, z_prev + (z - z_prev) * k, dx, dy, rot) for k in (0.33, 0.66)]
                    out = Image.blend(Image.blend(mids[0], mids[1], 0.5), camera(frame, z, dx, dy, rot), 0.4)
                else:
                    out = camera(frame, z, dx, dy, rot)
        if "mono" in kinds:
            out = self._mono(next(e for e in act if e.kind == "mono"), out, t)
        if i == 0:  # o final em loop se emenda nesta imagem (sem o flash e o texto do gancho, que voltam no replay)
            self.first_out = out.copy()
        if kinds & {"circle", "magnify", "sticker", "count", "hook", "teaser", "cta"}:
            out = out.convert("RGBA") if out.mode != "RGBA" else out.copy()
            for e in act:
                fn = getattr(self, f"_{e.kind}", None)
                if fn is not None and e.kind in ("circle", "magnify", "sticker", "count", "hook", "teaser", "cta"):
                    fn(e, out, t)
            out = out.convert("RGB")
        flash = self._flash(act, t)
        if flash > 0.01:
            out = Image.blend(out, Image.new("RGB", out.size, (255, 255, 255)), flash)
        loop = next((e for e in act if e.kind == "loop"), None)
        if loop is not None and self.first_out is not None:
            out = Image.blend(out, self.first_out, _ease_io((t - loop.t0) / (loop.t1 - loop.t0)) * 0.92)
        self.last_out = out
        return out

    def _flash(self, act: list[FxEvent], t: float) -> float:
        best = 0.0
        for e in act:
            p = t - e.t0
            if e.kind == "hook" and p < 0.14:
                best = max(best, 0.75 * (1 - p / 0.14))
            elif e.kind in ("freeze", "mono") and p < 0.1:
                best = max(best, 0.5 * (1 - p / 0.1))
        return best

    # ----------------------------------------------------------------------------------- interrupções
    def _freeze(self, e: FxEvent, frame: Image.Image, t: float) -> Image.Image:
        st = self.state.get(e)
        u = self.u
        if "card" not in st:
            held = (self.previous() or frame).convert("RGB")
            small = held.resize((self.w // 6, self.h // 6), Image.BILINEAR).filter(ImageFilter.GaussianBlur(4))
            st["bg"] = ImageEnhance.Brightness(small.resize((self.w, self.h), Image.BILINEAR)).enhance(0.45)
            border = round(16 * u)
            card = Image.new("RGB", (self.w + 2 * border, self.h + 2 * border), (255, 255, 255))
            card.paste(held, (border, border))
            st["card"] = card.convert("RGBA")
            st["rot"] = -3.0 if (self.seed + int(e.t0 * 10)) % 2 else 2.5
        p = t - e.t0
        q = gfx.ease_out_back(gfx.clamp01(p / 0.22), 1.4)
        scale = 1.0 - 0.17 * q
        out = st["bg"].copy().convert("RGBA")
        _paste(out, st["card"], self.w / 2, self.h * 0.45, scale * self.w / st["card"].width, 1.0, st["rot"] * q)
        if p > 0.12:
            k = gfx.clamp01((p - 0.12) / 0.18)
            icon = sticker(e.data.get("icon") or "question", round(200 * u))
            _paste(out, icon, self.w * 0.8, self.h * 0.16, 0.3 + 0.7 * gfx.ease_out_back(k, 2.2), 1.0,
                   10 * (1 - k))
        return out.convert("RGB")

    def _stack(self, e: FxEvent, frame: Image.Image, t: float) -> Image.Image:
        st = self.state.get(e)
        if "prev" not in st:
            st["prev"] = self.previous().convert("RGB")
            r = round(56 * self.u)
            st["mask"] = _rounded_mask((self.w, self.h), r)
            sh = Image.new("L", (self.w + 120, self.h + 120), 0)
            ImageDraw.Draw(sh).rounded_rectangle([60, 60, self.w + 60, self.h + 60], radius=r, fill=150)
            st["shadow"] = sh.filter(ImageFilter.GaussianBlur(30))
        q = _ease_out((t - e.t0) / (e.t1 - e.t0))
        prev = st["prev"]
        bg_scale = 1 - 0.1 * q
        bg = prev.resize((round(self.w * bg_scale), round(self.h * bg_scale)), Image.BILINEAR)
        bg = ImageEnhance.Brightness(bg).enhance(1 - 0.5 * q)
        out = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        out.paste(bg, ((self.w - bg.width) // 2, round((self.h - bg.height) / 2 - self.h * 0.05 * q)))
        s = 0.9 + 0.1 * q
        cw, ch = round(self.w * s), round(self.h * s)
        card = frame.resize((cw, ch), Image.BILINEAR)
        mask = st["mask"].resize((cw, ch), Image.BILINEAR)
        x = (self.w - cw) // 2
        y = round((self.h - ch) / 2 + (1 - q) * self.h * 0.95)
        shadow = st["shadow"].resize((cw + round(120 * s), ch + round(120 * s)), Image.BILINEAR)
        out.paste((0, 0, 0), (x - round(60 * s), y - round(60 * s) + round(20 * self.u)), shadow)
        out.paste(card, (x, y), mask)
        return out

    def _mono(self, e: FxEvent, img: Image.Image, t: float) -> Image.Image:
        g = img.convert("L")
        g = g.point(lambda v: max(0, min(255, round((v - 128) * 1.35 + 120))))
        out = Image.merge("RGB", (g, g, g))
        p = (t - e.t0) / (e.t1 - e.t0)
        if p > 0.82:  # a cor volta no fim
            out = Image.blend(out, img.convert("RGB"), (p - 0.82) / 0.18)
        return camera(out, 1.05)

    # ----------------------------------------------------------------------------------- desenhos
    def _target(self, e: FxEvent, img: Image.Image) -> tuple[float, float]:
        st = self.state.get(e)
        if "target" not in st:
            st["target"] = salient(img.convert("RGB"))
        return st["target"]

    def _circle(self, e: FxEvent, canvas: Image.Image, t: float) -> None:
        st = self.state.get(e)
        u = self.u
        tx, ty = self._target(e, canvas)
        if "pts" not in st:
            rng = np.random.default_rng(int(e.t0 * 1000) + self.seed)
            rx, ry = self.w * rng.uniform(0.15, 0.19), self.w * rng.uniform(0.11, 0.14)
            a0 = rng.uniform(0, 2 * math.pi)
            n = 90
            wob = rng.uniform(0.94, 1.06, 5)
            pts = []
            for k in range(n + 1):
                a = a0 + k / n * 2 * math.pi * 1.08  # passa um pouco do começo, como à mão
                rr = 1 + 0.04 * math.sin(3 * a + wob[0]) + 0.03 * math.sin(5 * a + wob[1]) + 0.05 * k / n
                pts.append((tx * self.w + rx * rr * math.cos(a), ty * self.h + ry * rr * math.sin(a)))
            st["pts"] = pts
            side = 1 if tx < 0.5 else -1  # seta vem do lado com mais espaço
            start = (tx * self.w + side * self.w * 0.3, ty * self.h - self.w * 0.28)
            end = (tx * self.w + side * rx * 0.8, ty * self.h - ry * 0.85)
            ctrl = ((start[0] + end[0]) / 2 + side * self.w * 0.06, start[1] + (end[1] - start[1]) * 0.15)
            st["arrow"] = [((1 - s) ** 2 * start[0] + 2 * (1 - s) * s * ctrl[0] + s ** 2 * end[0],
                            (1 - s) ** 2 * start[1] + 2 * (1 - s) * s * ctrl[1] + s ** 2 * end[1])
                           for s in np.linspace(0, 1, 30)]
        p = t - e.t0
        dur = e.t1 - e.t0
        alpha = 1 - gfx.clamp01((p - (dur - 0.2)) / 0.2)
        if alpha <= 0:
            return
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        lw = max(4, round(12 * u))
        pts = st["pts"][: max(2, int(len(st["pts"]) * _ease_out(p / 0.4)))]
        d.line(pts, fill=(255, 255, 255, 230), width=lw + round(6 * u), joint="curve")
        d.line(pts, fill=(*RED_INK, 255), width=lw, joint="curve")
        if p > 0.35:
            arr = st["arrow"][: max(2, int(len(st["arrow"]) * _ease_out((p - 0.35) / 0.25)))]
            d.line(arr, fill=(255, 255, 255, 230), width=lw + round(6 * u), joint="curve")
            d.line(arr, fill=(*RED_INK, 255), width=lw, joint="curve")
            if len(arr) == len(st["arrow"]):
                (x0, y0), (x1, y1) = arr[-4], arr[-1]
                ang = math.atan2(y1 - y0, x1 - x0)
                L = 40 * u
                head = [(x1, y1), (x1 - L * math.cos(ang - 0.5), y1 - L * math.sin(ang - 0.5)),
                        (x1 - L * math.cos(ang + 0.5), y1 - L * math.sin(ang + 0.5))]
                d.polygon(head, fill=(*RED_INK, 255), outline=(255, 255, 255, 230))
        if alpha < 1:
            layer.putalpha(layer.getchannel("A").point(lambda a: int(a * alpha)))
        canvas.alpha_composite(layer)

    def _magnify(self, e: FxEvent, canvas: Image.Image, t: float) -> None:
        st = self.state.get(e)
        u = self.u
        tx, ty = self._target(e, canvas)
        R = round(self.w * 0.22)
        if "lens" not in st:
            zoom = 2.1
            cx, cy = tx * self.w, ty * self.h
            half = R / zoom
            box = (round(cx - half), round(cy - half), round(cx + half), round(cy + half))
            crop = canvas.convert("RGB").crop(box).resize((2 * R, 2 * R), Image.BICUBIC)
            ring = round(12 * u)
            size = 2 * R + 2 * ring
            lens = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            m = Image.new("L", (2 * R, 2 * R), 0)
            ImageDraw.Draw(m).ellipse([0, 0, 2 * R - 1, 2 * R - 1], fill=255)
            d = ImageDraw.Draw(lens)
            d.ellipse([0, 0, size - 1, size - 1], fill=(255, 255, 255, 255))
            lens.paste(crop, (ring, ring), m)
            d.ellipse([ring, ring, size - ring - 1, size - ring - 1], outline=(20, 20, 26, 160),
                      width=max(1, round(3 * u)))
            glare = Image.new("RGBA", lens.size, (0, 0, 0, 0))
            ImageDraw.Draw(glare).ellipse([ring + R * 0.25, ring + R * 0.18, ring + R * 0.9, ring + R * 0.55],
                                          fill=(255, 255, 255, 38))
            lens.alpha_composite(glare)
            full = Image.new("RGBA", (size + round(R * 0.9), size + round(R * 0.9)), (0, 0, 0, 0))
            dd = ImageDraw.Draw(full)
            c0 = size / 2
            dd.line([(c0 + R * 0.75, c0 + R * 0.75), (c0 + R * 1.55, c0 + R * 1.55)], fill=(30, 30, 36, 255),
                    width=round(34 * u))
            full.alpha_composite(lens, (0, 0))
            st["lens"] = _alpha_shadow(full, round(18 * u), round(12 * u), 120)
            ly = min(max(cy, R + self.h * 0.07), self.h * CAPTION_TOP - R)
            lx = min(max(cx, R + 30 * u), self.w - R - 30 * u)
            st["at"] = (lx + (st["lens"].width / 2 - c0 - (st["lens"].width - full.width) / 2), ly +
                        (st["lens"].height / 2 - c0 - (st["lens"].height - full.height) / 2))
        p = t - e.t0
        dur = e.t1 - e.t0
        q = gfx.ease_out_back(gfx.clamp01(p / 0.22), 1.8)
        out = gfx.clamp01((dur - p) / 0.16)
        _paste(canvas, st["lens"], st["at"][0], st["at"][1], max(0.05, q * (0.85 + 0.15 * out)), out)

    def _sticker(self, e: FxEvent, canvas: Image.Image, t: float) -> None:
        u = self.u
        icon = sticker(e.data.get("icon") or "exclaim", round(210 * u), e.data.get("symbol") or "")
        p = t - e.t0
        dur = e.t1 - e.t0
        q = gfx.ease_out_back(gfx.clamp01(p / 0.2), 2.4)
        out = gfx.clamp01((dur - p) / 0.15)
        x = self.w * (0.79 if e.data.get("side") == "right" else 0.21)
        wob = 12 * math.exp(-p / 0.25) * math.sin(p * 18) * (1 if e.data.get("side") == "right" else -1)
        y = self.h * 0.27 + math.sin(p * 5) * 6 * u
        _paste(canvas, icon, x, y, max(0.05, q * out), 1.0, wob)

    def _count(self, e: FxEvent, canvas: Image.Image, t: float) -> None:
        u = self.u
        p = t - e.t0
        dur = e.t1 - e.t0
        value = float(e.data.get("value", 0))
        dec = int(e.data.get("decimals", 0))
        q = _ease_out(p / 0.7)
        shown = value * q
        text = gfx.fmt_number(shown if dec else round(shown), dec, e.data.get("prefix", ""), e.data.get("suffix", ""))
        final = gfx.fmt_number(value, dec, e.data.get("prefix", ""), e.data.get("suffix", ""))
        fnt = _fit("heavy", 170 * u, final, self.w * 0.84)
        color = (255, 255, 255, 255) if p < 0.7 else (*self.acc, 255)
        layer = _text_layer(text, fnt, color, max(3, round(9 * u)), round(8 * u))
        pop = gfx.ease_out_back(gfx.clamp01(p / 0.16), 1.8)
        hit = 1 + 0.08 * math.exp(-max(0.0, p - 0.7) / 0.08) if p >= 0.7 else 1.0
        out = gfx.clamp01((dur - p) / 0.2)
        cy = self.h * 0.25
        bar_w = round(self.w * 0.5 * _ease_out((p - 0.1) / 0.6))
        if bar_w > 4:
            bar = Image.new("RGBA", (bar_w, max(6, round(14 * u))), (*self.acc, 255))
            gfx.paste_center(canvas, bar, self.w / 2, cy + layer.height * 0.52, out)
        _paste(canvas, layer, self.w / 2, cy, (0.6 + 0.4 * pop) * hit, out)

    def _hook(self, e: FxEvent, canvas: Image.Image, t: float) -> None:
        st = self.state.get(e)
        u = self.u
        text = (e.data.get("text") or "").upper()
        p = t - e.t0
        dur = e.t1 - e.t0
        if "words" not in st:
            lines, fnt = [], gfx.font("heavy", round(118 * u))
            for size in (118, 104, 92, 82, 72):  # a frase inteira em até 3 linhas (nunca cortada com "...")
                fnt = gfx.font("heavy", round(size * u))
                lines = gfx.wrap(text, fnt, round(self.w * 0.86), 3) if text else []
                if " ".join(lines).split() == text.split():
                    break
            words, y = [], self.h * 0.16
            lh = sum(fnt.getmetrics()) * 0.98
            longest = max(text.split(), key=len) if text.split() else ""
            for line in lines:
                parts = line.split()
                space = gfx.text_width(" ", fnt)
                widths = [gfx.text_width(x, fnt) for x in parts]
                x = (self.w - sum(widths) - space * (len(parts) - 1)) / 2
                for word, ww in zip(parts, widths):
                    color = (*self.acc, 255) if word == longest else (255, 255, 255, 255)
                    words.append((_text_layer(word, fnt, color, max(3, round(9 * u)), round(8 * u)),
                                  x + ww / 2, y + lh / 2))
                    x += ww + space
                y += lh
            st["words"] = words
            st["bottom"] = y
            grad = np.linspace(150, 0, round(self.h * 0.5)).astype(np.uint8)
            veil = Image.new("RGBA", (self.w, len(grad)), (0, 0, 0, 0))
            veil.putalpha(Image.fromarray(np.repeat(grad[:, None], self.w, axis=1)))
            st["veil"] = veil
            if e.data.get("stamp"):
                st["stamp"] = _stamp(str(e.data["stamp"]).upper(), u, self.seed)
        out = gfx.clamp01((dur - p) / 0.22)
        if st["words"]:
            gfx.paste_at(canvas, st["veil"], 0, 0, min(1.0, p / 0.15) * out)
        step = min(0.15, max(0.07, (dur * 0.45) / max(1, len(st["words"]))))
        for k, (layer, cx, cy) in enumerate(st["words"]):
            q = gfx.clamp01((p - 0.06 - k * step) / 0.12)
            if q <= 0:
                continue
            scale = (1.7 - 0.7 * _ease_out(q)) * (1 + 0.06 * (1 - out))
            _paste(canvas, layer, cx, cy, scale, min(1.0, q * 2) * out)
        if "stamp" in st:
            at = float(e.data.get("stamp_at", 0.95))
            q = gfx.clamp01((p - at) / 0.11)
            if q > 0:
                y = (st["bottom"] if st["words"] else self.h * 0.2) + st["stamp"].height * 0.8
                _paste(canvas, st["stamp"], self.w * 0.55, min(y, self.h * 0.5), 2.2 - 1.2 * _ease_out(q),
                       min(1.0, q * 1.5) * out, -11)

    def _teaser(self, e: FxEvent, canvas: Image.Image, t: float) -> None:
        st = self.state.get(e)
        u = self.u
        if "pill" not in st:
            lines = [(e.data.get("title", ""), gfx.font("heavy", round(60 * u)), (255, 255, 255))]
            if e.data.get("text"):
                lines.append((e.data["text"], gfx.font("heavy_x", round(46 * u)), self.acc))
            st["pill"] = _pill(lines, u, sticker("down", round(70 * u)))
        p = t - e.t0
        dur = e.t1 - e.t0
        q = gfx.ease_out_back(gfx.clamp01(p / 0.3), 1.6)
        out = _ease_out((dur - p) / 0.25)
        pill = st["pill"]
        y = -pill.height / 2 + (self.h * 0.1 + pill.height / 2) * min(q, out) if out < 1 else \
            -pill.height / 2 + (self.h * 0.1 + pill.height / 2) * q
        pulse = 1 + 0.025 * math.sin(max(0.0, p - 0.3) * 8)
        _paste(canvas, pill, self.w / 2, y, pulse, 1.0)

    def _cta(self, e: FxEvent, canvas: Image.Image, t: float) -> None:
        st = self.state.get(e)
        u = self.u
        if "pill" not in st:
            st["pill"] = _pill([(e.data.get("text", ""), gfx.font("heavy", round(60 * u)), (255, 255, 255))], u,
                               sticker("down", round(80 * u)))
        p = t - e.t0
        dur = e.t1 - e.t0
        q = gfx.ease_out_back(gfx.clamp01(p / 0.3), 1.8)
        out = gfx.clamp01((dur - p) / 0.2)
        _paste(canvas, st["pill"], self.w / 2, self.h * 0.32 + math.sin(p * 6) * 8 * u, max(0.05, q), out)


# ---------------------------------------------------------------------------------------------- vídeo
def render_fx(src: Path, plan: FxPlan, dest: Path, w: int, h: int, fps: int, encode_args: list[str], *,
              accent: tuple[int, int, int] = (255, 204, 0), lang: str = "en", grade: bool = True,
              post_vf: str = "", seed: int = 0, timeout: float = 7200) -> dict[str, Any]:
    """Grava ``dest`` com os efeitos do ``plan`` aplicados ao ``src`` (áudio copiado)."""
    vf = ",".join(x for x in (GRADE_VF if grade else "", post_vf) if x) or "null"
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{w}x{h}", "-r", str(fps), "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                            "-r", str(fps), "-i", "-", "-i", str(src), "-map", "0:v", "-map", "1:a?", "-vf", vf,
                            *encode_args, "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(dest)],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert dec.stdout is not None and enc.stdin is not None
    fx = FxRenderer(plan, w, h, fps, accent, lang, seed)
    size = w * h * 3
    frames = touched = 0
    try:
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            t = frames / fps
            need = frames == 0 or bool(fx.active(t)) or any(e.kind == "stack" and abs(e.t0 - t) < 0.2
                                                             for e in fx.events)
            if need:
                img = Image.frombuffer("RGB", (w, h), buf, "raw", "RGB", 0, 1)
                out = fx.process(img, frames)
                enc.stdin.write(out.tobytes())
                touched += 1
            else:
                enc.stdin.write(buf)
                fx.last_out, fx.last_raw = None, buf  # o quadro só vira imagem se o congelamento/corte pedir
            frames += 1
        enc.stdin.close()
    except BrokenPipeError as exc:
        dec.kill()
        enc.kill()
        raise FFmpegError(f"ffmpeg encerrou nos efeitos: {enc.stderr.read().decode(errors='ignore')[-400:]}") from exc
    dec.wait(timeout=60)
    rc = enc.wait(timeout=timeout)
    if rc != 0:
        raise FFmpegError(f"ffmpeg falhou nos efeitos: {enc.stderr.read().decode(errors='ignore')[-400:]}")
    if frames == 0:
        raise FFmpegError(f"vídeo sem quadros para os efeitos: {dec.stderr.read().decode(errors='ignore')[-300:]}")
    log.info("efeitos do vídeo curto", extra={"stage": "shortsfx", "quadros": frames, "com_efeito": touched,
                                               **plan.summary()["events"]})
    return {"frames": frames, "touched": touched}
