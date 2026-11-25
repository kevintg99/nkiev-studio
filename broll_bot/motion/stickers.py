"""Adesivos do vídeo curto: ícones chapados com borda branca grossa e sombra (o "sticker" dos Shorts/TikTok).

Tudo desenhado com o Pillow (sem emoji de fonte nem imagem de terceiros, nada de licença a cuidar). O desenho é
feito em 3x e reduzido no fim, para a borda sair lisa.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from . import gfx

KINDS = ("money", "speed", "danger", "record", "time", "fire", "question", "exclaim", "up", "down", "check", "cross")
SS = 3  # supersampling

GREEN = (46, 204, 113)
RED = (235, 59, 59)
GOLD = (255, 196, 30)
ORANGE = (255, 128, 20)
BLUE = (52, 152, 255)
PURPLE = (150, 90, 255)
INK = (24, 24, 32)
WHITE = (255, 255, 255)


def _glyph(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str, size: int, fill: tuple) -> None:
    fnt = gfx.font("heavy", size)
    x0, y0, x1, y1 = fnt.getbbox(text)
    d.text((cx - (x0 + x1) / 2, cy - (y0 + y1) / 2), text, font=fnt, fill=fill)


def _star(cx: float, cy: float, r: float, inner: float = 0.45, n: int = 5) -> list[tuple[float, float]]:
    pts = []
    for k in range(n * 2):
        a = -math.pi / 2 + k * math.pi / n
        rr = r if k % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _draw(kind: str, s: int, symbol: str) -> Image.Image:
    """O ícone sem a borda branca, num quadrado de lado ``s`` (já em supersampling)."""
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = s / 2, s * 0.46
    lw = max(2, round(s * 0.06))
    if kind in ("money", "question", "exclaim", "check", "cross", "time"):
        fill = {"money": GREEN, "question": PURPLE, "exclaim": RED, "check": GREEN, "cross": RED, "time": WHITE}[kind]
        d.ellipse([c - r, c - r, c + r, c + r], fill=fill)
        if kind == "money":
            d.ellipse([c - r * 0.8, c - r * 0.8, c + r * 0.8, c + r * 0.8], outline=(30, 150, 80), width=lw // 2)
            _glyph(d, c, c, symbol or "$", round(s * (0.62 if len(symbol or "$") == 1 else 0.42)), WHITE)
        elif kind == "question":
            _glyph(d, c, c, "?", round(s * 0.66), WHITE)
        elif kind == "exclaim":
            _glyph(d, c, c, "!", round(s * 0.66), WHITE)
        elif kind == "check":
            d.line([(c - r * 0.48, c + r * 0.02), (c - r * 0.12, c + r * 0.38), (c + r * 0.52, c - r * 0.36)],
                   fill=WHITE, width=round(lw * 1.6), joint="curve")
        elif kind == "cross":
            k = r * 0.42
            d.line([(c - k, c - k), (c + k, c + k)], fill=WHITE, width=round(lw * 1.6))
            d.line([(c - k, c + k), (c + k, c - k)], fill=WHITE, width=round(lw * 1.6))
        else:  # relógio
            d.ellipse([c - r, c - r, c + r, c + r], outline=INK, width=lw)
            for k in range(12):
                a = k * math.pi / 6
                r0 = r * (0.72 if k % 3 == 0 else 0.8)
                d.line([(c + r0 * math.cos(a), c + r0 * math.sin(a)), (c + r * 0.88 * math.cos(a),
                        c + r * 0.88 * math.sin(a))], fill=INK, width=max(1, lw // 2))
            d.line([(c, c), (c, c - r * 0.62)], fill=INK, width=lw)
            d.line([(c, c), (c + r * 0.42, c + r * 0.12)], fill=RED, width=lw)
            d.ellipse([c - lw, c - lw, c + lw, c + lw], fill=INK)
    elif kind == "speed":  # velocímetro: arco colorido, marcas e ponteiro no vermelho
        d.ellipse([c - r, c - r, c + r, c + r], fill=INK)
        box = [c - r * 0.8, c - r * 0.8, c + r * 0.8, c + r * 0.8]
        d.arc(box, 150, 250, fill=GREEN, width=round(lw * 1.3))
        d.arc(box, 250, 330, fill=GOLD, width=round(lw * 1.3))
        d.arc(box, 330, 390, fill=RED, width=round(lw * 1.3))
        a = math.radians(-25)
        d.line([(c, c + r * 0.05), (c + r * 0.72 * math.cos(a), c + r * 0.72 * math.sin(a))], fill=WHITE,
               width=round(lw * 1.1))
        d.ellipse([c - lw * 1.3, c - lw * 1.3, c + lw * 1.3, c + lw * 1.3], fill=WHITE)
    elif kind == "danger":
        h = r * 1.6
        pts = [(c, c - h * 0.5), (c + r * 0.95, c + h * 0.45), (c - r * 0.95, c + h * 0.45)]
        d.polygon(pts, fill=GOLD)
        d.line(pts + [pts[0]], fill=INK, width=lw, joint="curve")
        _glyph(d, c, c + h * 0.08, "!", round(s * 0.5), INK)
    elif kind == "record":
        d.polygon(_star(c, c * 1.04, r * 1.02), fill=GOLD)
        d.polygon(_star(c, c * 1.04, r * 0.62), fill=(255, 224, 110))
    elif kind == "fire":
        outer = [(c, c - r), (c + r * 0.55, c - r * 0.15), (c + r * 0.72, c + r * 0.35), (c + r * 0.5, c + r * 0.85),
                 (c, c + r), (c - r * 0.5, c + r * 0.85), (c - r * 0.72, c + r * 0.35), (c - r * 0.35, c - r * 0.2),
                 (c - r * 0.1, c + r * 0.05)]
        d.polygon(outer, fill=ORANGE)
        inner = [(c + r * 0.05, c - r * 0.2), (c + r * 0.4, c + r * 0.3), (c + r * 0.3, c + r * 0.8), (c, c + r * 0.9),
                 (c - r * 0.3, c + r * 0.8), (c - r * 0.38, c + r * 0.4), (c - r * 0.12, c + r * 0.25)]
        d.polygon(inner, fill=GOLD)
    elif kind in ("up", "down"):
        fill = GREEN if kind == "up" else RED
        d.rounded_rectangle([c - r, c - r, c + r, c + r], radius=round(r * 0.3), fill=fill)
        sgn = -1 if kind == "up" else 1
        tip = (c, c + sgn * r * 0.62)
        d.polygon([tip, (c - r * 0.5, c + sgn * r * 0.02), (c + r * 0.5, c + sgn * r * 0.02)], fill=WHITE)
        d.rectangle([c - r * 0.2, c + sgn * r * 0.05 if sgn < 0 else c - r * 0.6,
                     c + r * 0.2, c + r * 0.6 if sgn < 0 else c - sgn * r * 0.02], fill=WHITE)
    return img


@lru_cache(maxsize=64)
def sticker(kind: str, size: int, symbol: str = "") -> Image.Image:
    """Ícone ``kind`` com borda branca e sombra, ``size`` px de lado (mais a margem da borda e da sombra)."""
    kind = kind if kind in KINDS else "exclaim"
    s = size * SS
    border = round(s * 0.07)
    pad = border * 3
    icon = _draw(kind, s, symbol)
    canvas = Image.new("RGBA", (s + 2 * pad, s + 2 * pad), (0, 0, 0, 0))
    canvas.alpha_composite(icon, (pad, pad))
    alpha = canvas.getchannel("A").point(lambda a: 255 if a > 40 else 0)
    outline = alpha.filter(ImageFilter.MaxFilter(border * 2 + 1)).filter(ImageFilter.GaussianBlur(SS * 0.6))
    white = Image.new("RGBA", canvas.size, (*WHITE, 0))
    white.putalpha(outline)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(outline.point(lambda a: a * 90 // 255).filter(ImageFilter.GaussianBlur(border)))
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (0, round(border * 0.9)))
    out.alpha_composite(white)
    out.alpha_composite(canvas)
    w = max(1, round(out.width / SS))
    return out.resize((w, w), Image.LANCZOS)


def sheet(size: int = 160) -> Image.Image:
    """Todos os adesivos lado a lado (conferência visual)."""
    tiles = [sticker(k, size) for k in KINDS]
    w = sum(t.width for t in tiles)
    out = Image.new("RGBA", (w, tiles[0].height), (60, 60, 70, 255))
    x = 0
    for t in tiles:
        out.alpha_composite(t, (x, 0))
        x += t.width
    return out
