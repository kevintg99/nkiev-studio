"""Prévias dos estilos na tela "A cara do seu vídeo": o template típico de cada estilo, renderizado de verdade
na pele dele (imagem parada + animação curta que toca quando o mouse passa no cartão).

    python -m studio.previas                 # usa uma foto da sua biblioteca (library/), se houver
    python -m studio.previas --foto carro.jpg

Sem foto, desenha um carro de demonstração. Os arquivos ficam em ``studio/web/previas/`` (<estilo>.jpg e
<estilo>.webp) e o app mostra no lugar do desenho genérico.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent / "web" / "previas"
W, H, FPS, SECONDS = 640, 360, 12, 3.0

# estilo -> (template, pele, dados do template)
DEMOS: dict[str, tuple[str, str, dict]] = {
    "minimal": ("showcase", "blueprint", dict(keyword="Honda Civic", subtitle="1.5 turbo · 173 cv")),
    "dossie": ("paperfloat", "paper", dict(keyword="Honda Civic", subtitle="o motor que não quebra")),
    "documentario": ("docphrase", "paper", dict(body="Dezenas de milhões")),
    "broadcast": ("report", "tv", dict(keyword="Honda Civic", subtitle="o sedã mais confiável do ano")),
    "dados": ("percentbar", "data", dict(keyword="dos donos recomendam", number={"value": 87})),
    "kinetic": ("wordpop", "kinetic", dict(captions=[(0.1, 0.9, "Ninguém"), (0.9, 1.7, "esperava"),
                                                       (1.7, 3.0, "isso")])),
    "cinema": ("textmask", "cinema", dict(keyword="CIVIC", subtitle="a lenda continua")),
}
MIX_ORDER = ("broadcast", "dossie", "dados", "kinetic")


def demo_car(path: Path, w: int = 1280, h: int = 720) -> Path:
    """Ilustração de um sedã vermelho na estrada ao entardecer (quando não há foto de verdade)."""
    s = 2  # desenha em dobro e reduz: bordas suaves
    W2, H2 = w * s, h * s
    img = Image.new("RGB", (W2, H2))
    d = ImageDraw.Draw(img)
    for y in range(H2):  # céu: azul-escuro em cima, laranja no horizonte
        k = y / (H2 * 0.62)
        if k <= 1:
            c = tuple(round(a + (b - a) * k ** 1.6) for a, b in zip((14, 22, 52), (238, 128, 72)))
        else:
            c = (32, 30, 38)
        d.line([(0, y), (W2, y)], fill=c)
    glow = Image.new("RGB", (W2, H2), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W2 * 0.55, H2 * 0.38, W2 * 0.85, H2 * 0.78], fill=(255, 190, 110))
    img = Image.blend(img, Image.composite(glow, img, glow.convert("L").filter(ImageFilter.GaussianBlur(120 * s))), 0.5)
    d = ImageDraw.Draw(img)
    hy = H2 * 0.62
    for k in range(9):  # prédios ao longe
        x0 = W2 * (k / 9) + (k * 37 % 50) * s
        top = hy - (60 + (k * 53) % 140) * s
        d.rectangle([x0, top, x0 + 110 * s, hy], fill=(40, 34, 52))
    d.rectangle([0, hy, W2, H2], fill=(36, 34, 40))  # estrada
    for x in range(0, W2, 260 * s):
        d.rectangle([x, H2 * 0.9, x + 140 * s, H2 * 0.9 + 10 * s], fill=(220, 210, 180))
    sh = Image.new("L", (W2, H2), 0)  # sombra do carro
    ImageDraw.Draw(sh).ellipse([W2 * 0.16, H2 * 0.79, W2 * 0.86, H2 * 0.86], fill=200)
    img.paste((10, 10, 12), mask=sh.filter(ImageFilter.GaussianBlur(18 * s)))
    d = ImageDraw.Draw(img)
    X = lambda v: v * W2  # noqa: E731
    Y = lambda v: v * H2  # noqa: E731
    body = [(X(.17), Y(.70)), (X(.17), Y(.62)), (X(.21), Y(.585)), (X(.33), Y(.565)), (X(.42), Y(.47)),
            (X(.52), Y(.445)), (X(.64), Y(.45)), (X(.72), Y(.53)), (X(.81), Y(.555)), (X(.86), Y(.60)),
            (X(.865), Y(.70))]
    d.polygon(body, fill=(184, 18, 32))
    d.polygon([(X(.18), Y(.60)), (X(.33), Y(.575)), (X(.72), Y(.545)), (X(.85), Y(.585)), (X(.85), Y(.6)),
               (X(.18), Y(.615))], fill=(226, 60, 70))  # brilho na linha de cintura
    d.polygon([(X(.355), Y(.565)), (X(.43), Y(.48)), (X(.52), Y(.465)), (X(.53), Y(.565))], fill=(24, 30, 44))
    d.polygon([(X(.545), Y(.565)), (X(.545), Y(.465)), (X(.63), Y(.47)), (X(.70), Y(.555))], fill=(24, 30, 44))
    d.polygon([(X(.44), Y(.49)), (X(.47), Y(.48)), (X(.40), Y(.56)), (X(.375), Y(.56))], fill=(80, 96, 128))
    d.rectangle([X(.82), Y(.585), X(.86), Y(.61)], fill=(255, 244, 214))  # farol
    d.rectangle([X(.17), Y(.6), X(.19), Y(.625)], fill=(255, 40, 40))  # lanterna
    d.line([(X(.535), Y(.57)), (X(.535), Y(.69))], fill=(120, 10, 20), width=3 * s)
    for cx in (X(.31), X(.735)):  # rodas
        r = Y(.085)
        d.ellipse([cx - r * 1.12, Y(.70) - r * 1.12, cx + r * 1.12, Y(.70) + r * 1.12], fill=(26, 10, 14))
        d.ellipse([cx - r, Y(.70) - r, cx + r, Y(.70) + r], fill=(18, 18, 20))
        d.ellipse([cx - r * .62, Y(.70) - r * .62, cx + r * .62, Y(.70) + r * .62], fill=(190, 192, 200))
        for k in range(5):
            import math

            a = k * 2 * math.pi / 5
            d.line([(cx, Y(.70)), (cx + math.cos(a) * r * .6, Y(.70) + math.sin(a) * r * .6)], fill=(90, 92, 100),
                   width=6 * s)
        d.ellipse([cx - r * .15, Y(.70) - r * .15, cx + r * .15, Y(.70) + r * .15], fill=(60, 60, 66))
    img = img.resize((w, h), Image.LANCZOS)
    img.save(path, quality=92)
    return path


def library_photo() -> Path | None:
    """Uma foto de carro da sua biblioteca (library/), se houver."""
    for p in sorted((BASE_DIR / "library").rglob("*")):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and "overlays" not in p.parts and \
                "sfx" not in p.parts and p.stat().st_size > 60_000:
            return p
    return None


def _frames(kind: str, skin: str, data: dict, photo: Path, seconds: float = SECONDS) -> list[Image.Image]:
    from broll_bot.motion import TEMPLATES, MediaRef, TemplateSpec

    frames = round(seconds * FPS)
    spec = TemplateSpec(kind=kind, theme="channel", w=W, h=H, fps=FPS, frames=frames, seed=7,
                        media=[MediaRef(str(photo))], lang="pt", skin=skin, **data)
    tpl = TEMPLATES[kind](spec)
    try:
        return [tpl.frame(i).convert("RGB") for i in range(frames)]
    finally:
        tpl.close()


def _save(name: str, frames: list[Image.Image], still: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames[min(still, len(frames) - 1)].save(OUT_DIR / f"{name}.jpg", quality=84)
    frames[0].save(OUT_DIR / f"{name}.webp", save_all=True, append_images=frames[1:], duration=round(1000 / FPS),
                   loop=0, quality=62, method=4)


def generate(photo: Path | None = None) -> list[Path]:
    from broll_bot.motion.gfx import apply_palette

    apply_palette(None)  # cores padrão: no vídeo valem as do canal
    with tempfile.TemporaryDirectory() as tmp:
        src = photo or library_photo() or demo_car(Path(tmp) / "carro.jpg")
        made: dict[str, list[Image.Image]] = {}
        for style, (kind, skin, data) in DEMOS.items():
            made[style] = _frames(kind, skin, data, Path(src))
            _save(style, made[style], round(len(made[style]) * 0.75))
        # Mix: um pedaço de cada estilo em sequência
        mix = [f for st in MIX_ORDER for f in made[st][round(FPS * 0.9):round(FPS * 2.1)]]
        _save("mix", mix, round(FPS * 0.9))
    return sorted(OUT_DIR.glob("*"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="gera as prévias dos estilos do nKiev Studio")
    p.add_argument("--foto", type=Path, default=None, help="foto de carro para as prévias")
    args = p.parse_args(argv)
    for f in generate(args.foto):
        print(f.relative_to(BASE_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
