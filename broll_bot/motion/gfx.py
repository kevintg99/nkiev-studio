"""Primitivas gráficas dos templates (Pillow + NumPy): fontes, easing, fundos,
cards com sombra, chips de texto e efeitos de cor."""

from __future__ import annotations

import math
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..media import FFmpegError

FONTS_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
FONT_FILES = {"black": "InterDisplay-ExtraBold.ttf", "bold": "Inter-Bold.ttf", "semibold": "Inter-SemiBold.ttf",
              # condensadas (estilo documentário): Bebas Neue e Oswald (variável)
              "condensed": "BebasNeue-Regular.ttf", "oswald": "Oswald-Variable.ttf",
              "oswald_light": "Oswald-Variable.ttf", "oswald_bold": "Oswald-Variable.ttf",
              # marca-texto (script em itálico) e texto limpo (estilo "Gotham")
              "script": "Lobster-Regular.ttf", "montserrat": "Montserrat-Variable.ttf",
              "montserrat_bold": "Montserrat-Variable.ttf",
              # máquina de escrever (dossiê/ficha em papel)
              "typewriter": "SpecialElite-Regular.ttf",
              # serifa de documentário (abertura, verbete, perfil)
              "serif": "PlayfairDisplay-Bold.ttf", "serif_italic": "PlayfairDisplay-Italic.ttf",
              # vídeo curto em pé (templates_vertical.py): a letra pesada das legendas
              "heavy": "Montserrat-Black.ttf", "heavy_x": "Montserrat-ExtraBold.ttf",
              # anotação à mão (estilo Documentário, templates_docu.py)
              "hand": "ArchitectsDaughter-Regular.ttf"}
VARIATIONS = {"oswald": b"Medium", "oswald_light": b"Light", "oswald_bold": b"Bold",
              "montserrat": b"SemiBold", "montserrat_bold": b"Bold"}
SYSTEM_FALLBACKS = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

RGB = tuple[int, int, int]


@lru_cache(maxsize=128)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (FONTS_DIR / FONT_FILES.get(weight, FONT_FILES["bold"]), *map(Path, SYSTEM_FALLBACKS)):
        if candidate.exists():
            fnt = ImageFont.truetype(str(candidate), max(8, size))
            if weight in VARIATIONS and candidate.name == FONT_FILES[weight]:
                try:
                    fnt.set_variation_by_name(VARIATIONS[weight])
                except (OSError, ValueError):  # Pillow sem suporte a fontes variáveis
                    pass
            return fnt
    return ImageFont.load_default(size)  # type: ignore[return-value]


# ------------------------------------------------------------------ easing
def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def progress(t: float, start: float, duration: float) -> float:
    return clamp01((t - start) / duration) if duration > 0 else float(t >= start)


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - clamp01(x)) ** 3


def ease_in_out(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp01(x))


def ease_out_back(x: float, s: float = 1.6) -> float:
    x = clamp01(x) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


# ------------------------------------------------------------------ temas
THEMES: dict[str, dict] = {
    # fundo claro quadriculado (papel) — como no quadro comparativo
    "paper": dict(bg=(236, 235, 230), grid=(212, 211, 204, 255), text=(20, 20, 22), accent=(240, 92, 60),
                  chip_text=(34, 20, 14), border=(252, 252, 252), glow=None, shadow=110),
    # blueprint escuro com brilho azul embaixo
    "blueprint": dict(bg=(3, 5, 14), grid=(46, 60, 210, 150), text=(246, 248, 255), accent=(214, 243, 74),
                      chip_text=(12, 14, 22), border=(250, 250, 255), glow=(18, 38, 215), shadow=170),
    # grade branca com brilho vermelho (scan HUD)
    "redgrid": dict(bg=(4, 4, 5), grid=(225, 225, 225, 150), text=(246, 246, 246), accent=(206, 242, 74),
                    chip_text=(10, 10, 10), border=(240, 240, 240), glow=(150, 12, 12), shadow=170),
    # grade branca com brilho ciano (scan HUD alternativo)
    "cyangrid": dict(bg=(3, 6, 8), grid=(215, 235, 240, 140), text=(240, 250, 252), accent=(255, 196, 40),
                     chip_text=(10, 10, 10), border=(240, 240, 240), glow=(0, 105, 140), shadow=170),
    # fibra de carbono com brilho vermelho (automotivo)
    "carbon": dict(bg=(14, 15, 18), grid=(0, 0, 0, 0), text=(245, 245, 247), accent=(232, 36, 52),
                   chip_text=(250, 250, 250), border=(236, 236, 240), glow=(70, 8, 14), shadow=190,
                   muted=(150, 154, 162)),
    # papel creme com grade preta (álbum/caderno) — fotos com borda picotada
    "notebook": dict(bg=(236, 232, 220), grid=(34, 34, 34, 215), text=(24, 24, 24), accent=(240, 196, 40),
                     chip_text=(24, 24, 24), border=(250, 250, 248), glow=None, shadow=90, muted=(90, 90, 90)),
    # dossiê: papel bege com grade fina (template "dossier"; tem visual próprio, fora do padrão do canal)
    "dossier": dict(bg=(233, 226, 211), grid=(190, 180, 160, 150), text=(48, 40, 34), accent=(196, 46, 34),
                    chip_text=(40, 34, 30), border=(250, 248, 242), glow=None, shadow=70, muted=(110, 100, 90)),
    # azul-marinho profundo (títulos de destaque, gráficos) — estilo documentário
    "navy": dict(bg=(6, 12, 38), grid=(70, 110, 230, 42), text=(250, 251, 255), accent=(255, 206, 38),
                 chip_text=(8, 12, 30), border=(235, 240, 255), glow=(18, 46, 150), shadow=170,
                 muted=(150, 170, 220)),
    # padrão do canal: azul-escuro com blueprint azul-escuro, textos amarelos com contorno preto
    "channel": dict(bg=(5, 12, 34), grid=(40, 82, 185, 92), text=(255, 204, 0), accent=(255, 204, 0),
                    chip_text=(8, 12, 30), border=(226, 234, 255), glow=(10, 30, 104), shadow=170,
                    muted=(150, 174, 226)),
    # prancheta de engenharia (azul clássico de blueprint)
    "engineering": dict(bg=(16, 62, 148), grid=(215, 232, 255, 60), text=(248, 250, 255), accent=(255, 214, 64),
                        chip_text=(16, 30, 60), border=(235, 242, 255), glow=None, shadow=150, muted=(175, 200, 240)),
}
# prancheta azul-escura com traços brancos (filmstrip): visual próprio, igual em todos os canais
THEMES["blueprint_white"] = dict(bg=(10, 30, 70), grid=(225, 235, 255, 70), text=(246, 248, 255), accent=(255, 206, 38),
                                 chip_text=(10, 30, 70), border=(250, 250, 255), glow=(24, 70, 150), shadow=160,
                                 muted=(170, 190, 225))
THEMES["channel_blueprint"] = dict(THEMES["channel"])  # foto do carro/galeria/desenho (o canal pode mudar)
for _t in THEMES.values():
    _t.setdefault("muted", tuple(int(c * 0.62 + 40) for c in _t["text"]))


def background(theme: str, w: int, h: int, cell: int, extra: int = 0) -> Image.Image:
    """Fundo com grade. ``extra`` px a mais em cada eixo permitem 'drift' sem emenda."""
    th = THEMES[theme]
    W, H = w + extra, h + extra
    arr = np.empty((H, W, 3), np.float32)
    arr[:] = th["bg"]
    if th["glow"] is not None:
        y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        strength = np.clip((y - 0.35) / 0.65, 0, 1) ** 1.6
        arr = arr * (1 - strength * 0.9) + np.array(th["glow"], np.float32) * strength * 0.9
    if theme == "carbon":  # trama diagonal sutil de fibra de carbono
        yy, xx = np.mgrid[0:H, 0:W]
        weave = (((xx + yy) // max(3, cell // 12)) % 2) * 2 - 1
        arr += (weave * 3.5)[..., None]
    if theme in ("notebook", "dossier"):  # papel envelhecido: manchas suaves + granulado
        rng = np.random.default_rng(11)
        blot = Image.fromarray((rng.random((max(2, H // 90), max(2, W // 90))) * 255).astype(np.uint8))
        blot = np.asarray(blot.resize((W, H), Image.BICUBIC), np.float32) / 255 - 0.5
        arr += (blot * (14 if theme == "notebook" else 5))[..., None] + (rng.standard_normal((H, W)) * 3.0)[..., None]
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
    if th["grid"][3] > 0:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        lw = max(1, round(h / 540))
        for x in range(0, W, cell):
            d.line([(x, 0), (x, H)], fill=th["grid"], width=lw)
        for y in range(0, H, cell):
            d.line([(0, y), (W, y)], fill=th["grid"], width=lw)
        if theme in ("engineering", "channel", "channel_blueprint", "blueprint_white"):  # subdivisões finas da prancheta
            fine = (*th["grid"][:3], th["grid"][3] // 3)
            for x in range(0, W, cell // 4):
                d.line([(x, 0), (x, H)], fill=fine, width=1)
            for y in range(0, H, cell // 4):
                d.line([(0, y), (W, y)], fill=fine, width=1)
        img = Image.alpha_composite(img, layer)
    if theme in ("notebook", "dossier"):
        img = vignette(img.convert("RGB"), 0.12).convert("RGBA")
    elif theme != "paper":
        img = vignette(img.convert("RGB"), 0.45).convert("RGBA")
    return img.convert("RGB")


DRIFT_PHASES = 4
_drift_cache: dict[int, tuple[Image.Image, dict[int, Image.Image]]] = {}


def drift(bg: Image.Image, t: float, w: int, h: int, cell: int, speed: float = 10.0) -> Image.Image:
    """Recorte do fundo deslizando devagar (periodicidade = 1 célula, sem emenda).

    Posição com 1/4 de pixel: sem isso o fundo anda aos trancos (1 px a cada 3 quadros).
    As 4 versões deslocadas do fundo são calculadas uma vez; cada quadro é só um recorte."""
    off = (t * speed) % cell
    whole, phase = int(off), int((off % 1) * DRIFT_PHASES)
    if whole + 1 + w > bg.width or whole + 1 + h > bg.height:
        phase = 0
    entry = _drift_cache.get(id(bg))
    if entry is None or entry[0] is not bg:
        if len(_drift_cache) > 6:
            _drift_cache.clear()
        entry = _drift_cache[id(bg)] = (bg, {0: bg})
    shifted = entry[1].get(phase)
    if shifted is None:
        f = phase / DRIFT_PHASES
        W, H = bg.width - 1, bg.height - 1  # 1 px a menos: a janela deslocada cabe dentro do fundo
        shifted = entry[1][phase] = bg.resize((W, H), Image.BILINEAR, box=(f, f, f + W, f + H))
    return shifted.crop((whole, whole, whole + w, whole + h))


@lru_cache(maxsize=8)
def _vignette_mask(w: int, h: int, strength: float) -> np.ndarray:
    y, x = np.ogrid[-1:1:complex(0, h), -1:1:complex(0, w)]
    r = np.sqrt(x * x * 0.8 + y * y)
    return (1 - strength * np.clip(r - 0.55, 0, 1) ** 1.5).astype(np.float32)[..., None]


def vignette(img: Image.Image, strength: float = 0.35) -> Image.Image:
    arr = np.asarray(img, np.float32) * _vignette_mask(img.width, img.height, strength)
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


# ------------------------------------------------------------------ imagens
def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Redimensiona cobrindo (w, h) e corta o centro, sem distorcer."""
    scale = max(w / img.width, h / img.height)
    nw, nh = max(w, round(img.width * scale)), max(h, round(img.height * scale))
    img = img.resize((nw, nh), Image.BILINEAR)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def bordered(content: Image.Image, border: int, color: RGB) -> Image.Image:
    card = Image.new("RGBA", (content.width + 2 * border, content.height + 2 * border), (*color, 255))
    card.paste(content.convert("RGB"), (border, border))
    return card


@lru_cache(maxsize=256)
def _shadow_layer(card_size: tuple[int, int], angle: float, blur: int, offset: tuple[int, int], opacity: int) -> Image.Image:
    """Sombra de um retângulo girado. Só depende do tamanho e do ângulo, então é cacheada
    (quando o card para de animar, a sombra deixa de ser recalculada a cada frame)."""
    rect = Image.new("L", card_size, opacity)
    rotated = rect.rotate(angle, resample=Image.BICUBIC, expand=True) if abs(angle) > 0.05 else rect
    pad = blur * 3 + max(abs(offset[0]), abs(offset[1]))
    size = (rotated.width + 2 * pad, rotated.height + 2 * pad)
    mask = Image.new("L", size, 0)
    mask.paste(rotated, (pad + offset[0], pad + offset[1]))
    out = Image.new("RGBA", size, (0, 0, 0, 255))
    out.putalpha(mask.filter(ImageFilter.GaussianBlur(blur)))
    return out


def with_shadow(card: Image.Image, angle: float, blur: int, offset: tuple[int, int], opacity: int) -> Image.Image:
    """Gira o card e adiciona sombra projetada. Devolve RGBA centrado no card."""
    angle = round(angle, 1)
    # bilinear: com ângulos pequenos fica igual ao bicúbico e é ~3x mais rápido
    rotated = card.rotate(angle, resample=Image.BILINEAR, expand=True) if abs(angle) > 0.05 else card
    out = _shadow_layer(card.size, angle, blur, offset, opacity).copy()
    pad = (out.width - rotated.width) // 2
    out.alpha_composite(rotated, (pad, pad))
    return out


def glow(card: Image.Image, color: RGB, blur: int, strength: float = 0.8) -> Image.Image:
    """Brilho em volta do card (neon)."""
    pad = blur * 3
    out = Image.new("RGBA", (card.width + 2 * pad, card.height + 2 * pad), (0, 0, 0, 0))
    halo = Image.new("L", out.size, 0)
    halo.paste(card.getchannel("A").point(lambda a: int(a * strength)), (pad, pad))
    halo = halo.filter(ImageFilter.GaussianBlur(blur))
    tint = Image.new("RGBA", out.size, (*color, 255))
    tint.putalpha(halo)
    out.alpha_composite(tint)
    out.alpha_composite(card, (pad, pad))
    return out


def scaled(img: Image.Image, factor: float) -> Image.Image:
    if abs(factor - 1) < 1e-3:
        return img
    return img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))), Image.BILINEAR)


def fade(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return img
    img = img.copy()
    img.putalpha(img.getchannel("A").point(lambda a: int(a * clamp01(alpha))))
    return img


def paste_center(canvas: Image.Image, layer: Image.Image, cx: float, cy: float, alpha: float = 1.0) -> None:
    if alpha <= 0.001:
        return
    layer = fade(layer, alpha) if layer.mode == "RGBA" else layer.convert("RGBA")
    x, y = round(cx - layer.width / 2), round(cy - layer.height / 2)
    canvas.paste(layer, (x, y), layer)


def paste_at(canvas: Image.Image, layer: Image.Image, x: float, y: float, alpha: float = 1.0) -> None:
    if alpha <= 0.001:
        return
    layer = fade(layer, alpha)
    canvas.paste(layer, (round(x), round(y)), layer)


def chroma_shift(img: Image.Image, px: int) -> Image.Image:
    """Aberração cromática: vermelho para um lado, azul para o outro."""
    if px == 0:
        return img
    arr = np.asarray(img).copy()
    arr[:, :, 0] = np.roll(arr[:, :, 0], px, axis=1)
    arr[:, :, 2] = np.roll(arr[:, :, 2], -px, axis=1)
    return Image.fromarray(arr, img.mode)


def slice_glitch(img: Image.Image, rng: np.random.Generator, bands: int = 4, max_shift: int = 30) -> Image.Image:
    arr = np.asarray(img).copy()
    h = arr.shape[0]
    for _ in range(bands):
        y0 = int(rng.integers(0, max(1, h - 10)))
        y1 = min(h, y0 + int(rng.integers(4, max(6, h // 12))))
        arr[y0:y1] = np.roll(arr[y0:y1], int(rng.integers(-max_shift, max_shift + 1)), axis=1)
    return Image.fromarray(arr, img.mode)


# ------------------------------------------------------------------ texto
# espaços que as fontes condensadas (Bebas, Oswald) não têm: sem o motor raqm (Pillow do
# Windows) viram um quadrado riscado. O espaço inseparável comum existe em todas.
_MISSING_SPACES = str.maketrans({"\u202f": "\u00a0", "\u2009": "\u00a0", "\u2007": "\u00a0"})


def safe_text(text: str) -> str:
    return text.translate(_MISSING_SPACES)


def text_width(text: str, fnt: ImageFont.FreeTypeFont) -> int:
    x0, _, x1, _ = fnt.getbbox(safe_text(text))
    return int(x1 - x0)


def spaced_width(text: str, fnt: ImageFont.FreeTypeFont, tracking: float = 0.0) -> float:
    """Largura do texto com ``tracking`` (fração do tamanho da fonte) entre as letras."""
    text = safe_text(text)
    extra = tracking * getattr(fnt, "size", 40)
    return sum(fnt.getlength(ch) for ch in text) + extra * max(0, len(text) - 1)


def draw_spaced(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fnt: ImageFont.FreeTypeFont,
                fill: tuple, tracking: float = 0.0, chars: int | None = None, bold: int = 0) -> float:
    """Escreve letra por letra com espaçamento (máquina de escrever: ``chars`` = quantas já saíram).
    Devolve o x depois da última letra escrita."""
    text = safe_text(text)
    x, y = xy
    extra = tracking * getattr(fnt, "size", 40)
    for ch in text if chars is None else text[:max(0, chars)]:
        d.text((x, y), ch, font=fnt, fill=fill, stroke_width=bold, stroke_fill=fill if bold else None)
        x += fnt.getlength(ch) + extra
    return x


def _french_glue(words: list[str]) -> list[str]:
    """No francês o "?", "!", ":", ";" e o "»" vêm com espaço antes ("Pourquoi ?"): grudam na palavra de antes, e o
    "«" na de depois, para nunca caírem sozinhos na linha de baixo."""
    out: list[str] = []
    for word in words:
        if out and (word in ("?", "!", ":", ";", "»", "?!", "!?") or out[-1] == "«"):
            out[-1] += " " + word
        else:
            out.append(word)
    return out


def wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: int, max_lines: int = 2) -> list[str]:
    words, lines, cur = _french_glue(text.split()), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if text_width(trial, fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and text_width(lines[-1] + "…", fnt) > max_w:
            lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
        lines[-1] += "…"
    return lines


# ------------------------------------------------------------------ marca do canal
# Com a marca ligada (templates.brand_theme), todo texto sai com contorno preto e o que seria
# branco sai no amarelo do canal. Cinzas/azuis claros (textos secundários) ficam como estão.
BRAND: dict = {"on": False, "text": (255, 204, 0), "stroke": (0, 0, 0), "accent": (255, 204, 0),
               "veil": (6, 14, 40), "cream": (234, 226, 210)}
SIGNAL_YELLOW = (255, 206, 38)  # amarelo dos gráficos sem a marca do canal

# paleta de cada canal (canais/<canal>.yaml, chave "paleta"); a padrão é a do Bernard Chevalier:
# azul-marinho, blueprint azul e texto/destaque amarelos com contorno preto
DEFAULT_PALETTE: dict[str, tuple] = {
    "fundo": (5, 12, 34), "grade": (40, 82, 185), "blueprint": (5, 12, 34), "grade_blueprint": (40, 82, 185),
    "texto": (255, 204, 0), "destaque": (255, 204, 0), "contorno": (0, 0, 0), "creme": (234, 226, 210),
    "veu": (6, 14, 40),
}
_BASE_THEMES: dict[str, dict] = {}


def set_brand(on: bool, text: RGB | None = None) -> None:
    BRAND["on"] = bool(on)
    if text is not None:
        BRAND["text"] = tuple(text)


def accent() -> RGB:
    """Cor de destaque (filetes, marca-texto, números, ponteiros): a do canal com a marca ligada."""
    return BRAND["accent"] if BRAND["on"] else SIGNAL_YELLOW


def apply_palette(palette: dict | None) -> dict:
    """Aplica a paleta do canal: fundo dos templates, grade, blueprint, texto, destaque, véu dos
    overlays. Só o que o canal define muda (sem paleta, tudo fica exatamente como o padrão).
    Devolve a paleta completa em uso."""
    given = {k: tuple(v) for k, v in (palette or {}).items() if k in DEFAULT_PALETTE}
    pal = {**DEFAULT_PALETTE, **given}
    if "veu" not in given and "fundo" in given:
        pal["veu"] = pal["fundo"]  # véu dos overlays no tom do fundo do canal
    for name in ("channel", "navy", "channel_blueprint"):
        _BASE_THEMES.setdefault(name, dict(THEMES[name]))
    glow = lambda c: tuple(min(255, round(x * 2.2)) for x in c)  # noqa: E731 - brilho no pé do fundo
    ch = dict(_BASE_THEMES["channel"])
    nv = dict(_BASE_THEMES["navy"])
    if "fundo" in given:
        ch.update(bg=given["fundo"], glow=glow(given["fundo"]), chip_text=given["fundo"], muted=(176, 178, 184))
        nv.update(bg=given["fundo"], muted=(176, 178, 184))
    if "grade" in given:
        ch["grid"], nv["grid"] = (*given["grade"], 92), (*given["grade"], 42)
    if "texto" in given:
        ch["text"] = given["texto"]
    if "destaque" in given:
        ch["accent"] = nv["accent"] = given["destaque"]
    bp = {**ch}
    bp_bg = given.get("blueprint")
    if bp_bg is not None:
        bp.update(bg=bp_bg, glow=glow(bp_bg))
    elif "fundo" in given:  # sem blueprint própria: a do canal padrão
        bp.update(bg=_BASE_THEMES["channel_blueprint"]["bg"], glow=_BASE_THEMES["channel_blueprint"]["glow"],
                  grid=_BASE_THEMES["channel_blueprint"]["grid"])
    if "grade_blueprint" in given:
        bp["grid"] = (*given["grade_blueprint"], 92)
    THEMES["channel"], THEMES["navy"], THEMES["channel_blueprint"] = ch, nv, bp
    BRAND.update(text=pal["texto"], accent=pal["destaque"], stroke=pal["contorno"], veil=pal["veu"],
                 cream=pal["creme"])
    return pal


def brand_fill(fill: RGB) -> RGB:
    if BRAND["on"] and min(fill[:3]) >= 225:  # branco / quase branco
        return BRAND["text"]
    return fill


def brand_stroke(fnt: ImageFont.FreeTypeFont) -> int:
    return max(2, round(getattr(fnt, "size", 40) / 14)) if BRAND["on"] else 0


def text_block(lines: Iterable[str], fnt: ImageFont.FreeTypeFont, fill: RGB, line_gap: float = 1.12,
               shadow: int = 0, shadow_blur: int = 6) -> Image.Image:
    lines = [safe_text(line) for line in lines]
    fill = brand_fill(fill)
    sw = brand_stroke(fnt)
    asc, desc = fnt.getmetrics()
    lh = int((asc + desc) * line_gap)
    w = max((text_width(l, fnt) for l in lines), default=1) + 4 + 2 * sw
    pad = (shadow_blur * 2 + abs(shadow) if shadow else 2) + sw
    img = Image.new("RGBA", (w + 2 * pad, lh * len(lines) + 2 * pad), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("L", img.size, 0)
        d = ImageDraw.Draw(sh)
        for i, line in enumerate(lines):
            d.text((pad + shadow, pad + shadow + i * lh), line, font=fnt, fill=190)
        sh = sh.filter(ImageFilter.GaussianBlur(shadow_blur))
        black = Image.new("RGBA", img.size, (0, 0, 0, 255))
        black.putalpha(sh)
        img.alpha_composite(black)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((pad, pad + i * lh), line, font=fnt, fill=(*fill, 255),
               stroke_width=sw, stroke_fill=(*BRAND["stroke"], 255) if sw else None)
    return img


def chip(text: str, fnt: ImageFont.FreeTypeFont, bg: RGB, fg: RGB, max_w: int, pad_x: int, pad_y: int) -> Image.Image:
    lines = wrap(text, fnt, max_w - 2 * pad_x, max_lines=2)
    asc, desc = fnt.getmetrics()
    lh = int((asc + desc) * 1.1)
    w = max(text_width(l, fnt) for l in lines) + 2 * pad_x
    img = Image.new("RGBA", (w, lh * len(lines) + 2 * pad_y), (*bg, 255))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((pad_x, pad_y + i * lh), line, font=fnt, fill=(*fg, 255))
    return with_shadow(img, 0, max(2, pad_y // 2), (0, max(2, pad_y // 3)), 70)


def outlined_text(text: str, fnt: ImageFont.FreeTypeFont, fill: tuple, stroke: int, stroke_fill: tuple) -> Image.Image:
    text = safe_text(text)
    if BRAND["on"]:  # número gigante: amarelo com contorno preto
        fill = (*brand_fill(tuple(fill[:3])), *fill[3:])
        stroke, stroke_fill = max(stroke, brand_stroke(fnt)), (*BRAND["stroke"], 255)
    x0, y0, x1, y1 = fnt.getbbox(text, stroke_width=stroke)
    img = Image.new("RGBA", (x1 - x0 + 4, y1 - y0 + 4), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((2 - x0, 2 - y0), text, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return img


# ------------------------------------------------------------------ números
# separador de milhar e de decimal por idioma (o vídeo inteiro usa o idioma do roteiro)
NUMBER_FORMATS = {"pt": (".", ","), "es": (".", ","), "fr": ("\u00a0", ","), "pl": ("\u00a0", ","), "en": (",", ".")}
_number_locale = "pt"


def set_number_locale(lang: str) -> None:
    global _number_locale
    lang = (lang or "pt").split("-")[0].lower()
    _number_locale = lang if lang in NUMBER_FORMATS else "pt"


def fmt_number(value: float, decimals: int = 0, prefix: str = "", suffix: str = "") -> str:
    """No idioma do roteiro: pt 89.900 / 7,2 · fr e pl 89 900 / 7,2 · en 89,900 / 7.2."""
    thousands, decimal = NUMBER_FORMATS[_number_locale]
    txt = f"{value:,.{max(0, decimals)}f}".replace(",", "X").replace(".", decimal).replace("X", thousands)
    return f"{prefix}{txt}{suffix}"


def parse_number(text: str) -> tuple[float, int] | None:
    """'177' -> (177, 0) · '21,4' -> (21.4, 1) · '4.630' -> (4630, 0) · '7.2' -> (7.2, 1)."""
    import re

    # "89 900" / "89 900" (espaço fino, fr/pl) = milhar
    text = re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))", "", text or "")
    m = re.search(r"\d[\d.,]*", text)
    if not m:
        return None
    raw = m.group(0).rstrip(".,")
    if re.fullmatch(r"\d{1,3}(,\d{3})+", raw) and (_number_locale == "en" or raw.count(",") > 1):
        return float(raw.replace(",", "")), 0  # inglês: "12,000"
    if "," in raw:  # vírgula = decimal (pt-BR)
        ints, dec = raw.rsplit(",", 1)
        return float(ints.replace(".", "") + "." + dec), len(dec)
    if raw.count(".") == 1 and len(raw.split(".")[1]) != 3:
        return float(raw), len(raw.split(".")[1])
    return float(raw.replace(".", "")), 0


# ------------------------------------------------------------------ saída
class FrameWriter:
    """Recebe frames RGB (PIL) e codifica direto com o FFmpeg (via stdin)."""

    def __init__(self, dest: Path, w: int, h: int, fps: int, encode_args: list[str],
                 input_size: tuple[int, int] | None = None) -> None:
        """``input_size`` menor que (w, h): o FFmpeg amplia (no processo dele, em paralelo)."""
        self.size = input_size or (w, h)
        self.dest = dest
        vf = "setsar=1" if self.size == (w, h) else f"scale={w}:{h}:flags=bicubic,setsar=1"
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{self.size[0]}x{self.size[1]}",
             "-r", str(fps), "-i", "-", "-an", "-vf", vf, *encode_args, str(dest)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def write(self, img: Image.Image) -> None:
        if img.size != self.size:
            img = img.resize(self.size, Image.BILINEAR)
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write((img if img.mode == "RGBA" else img.convert("RGBA")).tobytes())
        except BrokenPipeError as exc:
            raise FFmpegError(f"ffmpeg encerrou ao receber frames: {self._stderr()}") from exc

    def _stderr(self) -> str:
        return (self.proc.stderr.read().decode(errors="ignore") if self.proc.stderr else "")[-500:]

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        rc = self.proc.wait(timeout=600)
        if rc != 0:
            raise FFmpegError(f"ffmpeg falhou ao codificar template: {self._stderr()}")

    def abort(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        finally:
            self.proc.kill()
            self.proc.wait()
