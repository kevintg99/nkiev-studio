"""Legenda animada no tempo de cada palavra (o que segura quem assiste sem som no Shorts/Reels/TikTok).

Gera um arquivo ASS (libass, dentro do FFmpeg) e queima no vídeo final. Cada estilo é uma família vista no
mercado de vídeo curto:

* ``palavra``  — uma palavra por vez, grande, entrando com um "pop" (escala 60% → 112% → 100%).
* ``frase``    — 2–3 palavras na tela; a palavra falada acende na cor de destaque e cresce um pouco.
* ``caixa``    — caixa alta com uma tarja colorida atrás da palavra falada (estilo Hormozi / Submagic).
* ``karaoke``  — a linha inteira, preenchendo da esquerda para a direita na cor de destaque.
* ``limpa``    — frase curta, letra fina sem contorno, sombra suave e fade (documentário, elegante).
* ``impacto``  — 1–2 palavras enormes em letra de quadrinho, cor alternada, inclinação e quique.
* ``neon``     — palavra falada em branco com brilho da cor de destaque; as outras apagadas.
* ``maquina``  — máquina de escrever: as letras aparecendo no ritmo da fala.

As fontes vêm de ``assets/fonts`` (``fontsdir`` do filtro), então o resultado é igual em qualquer PC.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .log import get_logger
from .models import Word

log = get_logger("captions")

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


@dataclass(frozen=True)
class Style:
    font: str
    size: int  # px numa tela de 1080 de largura (vertical); o horizontal usa ~0,8 disso
    mode: str  # word | chunk | box | karaoke | line | type
    words: int = 3  # máximo de palavras na tela
    chars: int = 18  # máximo de letras por linha
    upper: bool = False
    bold: bool = False
    outline: float = 6.0
    shadow: float = 0.0
    spacing: float = 0.0


STYLES: dict[str, Style] = {
    "palavra": Style("Montserrat Black", 112, "word", words=1, upper=True, outline=7, shadow=3),
    "frase": Style("Montserrat ExtraBold", 106, "chunk", words=3, chars=14, upper=True, outline=8, shadow=3),
    "caixa": Style("Montserrat Black", 110, "box", words=3, chars=13, upper=True, outline=5, shadow=0),
    "karaoke": Style("Inter Display ExtraBold", 92, "karaoke", words=5, chars=20, outline=7, shadow=3),
    "limpa": Style("Inter", 76, "line", words=6, chars=24, bold=True, outline=0, shadow=3),
    "impacto": Style("Bangers", 150, "word", words=2, chars=10, upper=True, outline=8, shadow=4, spacing=2),
    "neon": Style("Poppins ExtraBold", 108, "neon", words=3, chars=13, upper=True, outline=3, shadow=0),
    "maquina": Style("Special Elite", 92, "type", words=5, chars=18, outline=5, shadow=0),
}
NAMES = {"palavra": "Palavra por palavra", "frase": "Frase com destaque", "caixa": "Caixa na palavra",
         "karaoke": "Karaokê", "limpa": "Limpa (documentário)", "impacto": "Impacto", "neon": "Neon",
         "maquina": "Máquina de escrever"}
IMPACT_COLORS = ((255, 255, 255), (255, 214, 0), (80, 255, 120))


def _ass_color(rgb: tuple[int, int, int], alpha: int = 0) -> str:
    r, g, b = rgb
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def hex_rgb(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (value or "").strip())
    if not m:
        return default
    v = m.group(1)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


_PUNCT_END = re.compile(r"[.,;:…]+$")


def clean_word(text: str, upper: bool) -> str:
    """O que vai para a tela: sem chaves/barras do ASS, sem ponto/vírgula no fim (? e ! ficam)."""
    t = re.sub(r"[{}\\]", "", (text or "").strip())
    t = _PUNCT_END.sub("", t) if not re.fullmatch(r"[\d.,]+", t) else t.rstrip(".,")
    return t.upper() if upper else t


def chunks(words: list[Word], max_words: int, max_chars: int, max_gap: float = 0.5) -> list[list[Word]]:
    """Grupos de palavras que aparecem juntas: quebra no fim da frase, numa pausa e no limite de letras."""
    out: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if not w.text.strip():
            continue
        if cur:
            text_len = sum(len(x.text) + 1 for x in cur) + len(w.text)
            gap = w.start - cur[-1].end
            ends = re.search(r"[.!?…]$", cur[-1].text.strip())
            if len(cur) >= max_words or text_len > max_chars or gap > max_gap or ends:
                out.append(cur)
                cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def _is_key(text: str) -> bool:
    """Palavra que pede destaque mesmo sem estar sendo falada (número, valor, palavra longa)."""
    t = text.strip()
    return bool(re.search(r"\d", t)) or len(re.sub(r"\W", "", t)) >= 8


class _Builder:
    def __init__(self, w: int, h: int, style: str, accent: tuple[int, int, int], position: float = 0.0,
                 size: float = 1.0, uppercase: str = "auto", seed: int = 7,
                 busy: list[tuple[float, float]] | None = None, emphasis: list[float] | None = None) -> None:
        self.w, self.h = w, h
        # palavras em destaque (efeitos do vídeo curto, broll_bot/shortsfx): cor de destaque e letra maior
        self.emph = {round(t * 50) for t in emphasis or []}
        self.name = style if style in STYLES else "frase"
        self.st = STYLES[self.name]
        vertical = h > w
        # libass mede a letra pela altura da linha (ascendente + descendente): a maiúscula sai bem menor que o
        # número; 1,45 leva o tamanho de STYLES para a altura de letra que se vê nos Shorts
        scale = (w / 1080 if vertical else h / 1080 * 0.8) * 1.45
        self.px = round(self.st.size * scale * size)
        self.outline = round(self.st.outline * scale, 1)
        self.shadow = round(self.st.shadow * scale, 1)
        self.y = round(h * (position or (0.64 if vertical else 0.83)))
        self.x = w // 2
        # trechos com template na tela: a legenda desce para não cobrir o texto do template
        self.busy = sorted(busy or [])
        self.busy_y = round(h * (0.79 if vertical else 0.9))
        self.upper = self.st.upper if uppercase == "auto" else uppercase in ("sim", "yes", "true", "1")
        self.acc = accent
        self.rng = random.Random(seed)
        self.events: list[str] = []
        # largura útil: a legenda nunca encosta na borda (nem passa por baixo dos botões do app)
        per_char = self.px * (0.56 if self.upper else 0.46) + self.st.spacing  # largura média da letra
        fit = int(w * 0.86 / max(1.0, per_char))
        self.chars = max(6, min(self.st.chars if vertical else round(self.st.chars * 1.6), fit))

    # --------------------------------------------------------------- cabeçalho
    def header(self) -> str:
        st = self.st
        white, black = _ass_color((255, 255, 255)), _ass_color((0, 0, 0))
        shadow = _ass_color((0, 0, 0), 0x60)
        acc = _ass_color(self.acc)
        bold = -1 if st.bold else 0

        def line(name: str, primary: str, secondary: str, outline_c: str, back: str, border: int, outline: float,
                 shadow_px: float) -> str:
            return (f"Style: {name},{st.font},{self.px},{primary},{secondary},{outline_c},{back},{bold},0,0,0,"
                    f"100,100,{st.spacing},0,{border},{outline},{shadow_px},5,40,40,40,1")

        styles = [line("Cap", white, white, black, shadow, 1, self.outline, self.shadow)]
        if st.mode == "karaoke":
            styles = [line("Cap", acc, white, black, shadow, 1, self.outline, self.shadow)]
        elif st.mode == "type":
            styles = [line("Cap", white, _ass_color((255, 255, 255), 0xFF), black, shadow, 1, self.outline,
                           self.shadow)]
        elif st.mode == "box":
            pad = max(8, round(self.px * 0.16))
            styles.append(line("Box", acc, acc, acc, acc, 3, pad, 0))
        elif st.mode == "neon":
            styles = [line("Cap", white, white, acc, shadow, 1, self.outline, 0),
                      line("Glow", acc, acc, acc, acc, 1, max(6, round(self.px * 0.12)), 0)]
        elif st.mode == "line":
            styles = [line("Cap", white, white, black, _ass_color((0, 0, 0), 0x40), 1, 0, self.shadow)]
        return "\n".join([
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {self.w}", f"PlayResY: {self.h}",
            "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding",
            *styles, "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ])

    def add(self, t0: float, t1: float, text: str, style: str = "Cap", layer: int = 1) -> None:
        if t1 - t0 < 0.02 or not text.strip():
            return
        y = self.busy_y if any(a - 0.05 <= t0 < b for a, b in self.busy) else self.y
        self.events.append(f"Dialogue: {layer},{_ts(t0)},{_ts(t1)},{style},,0,0,0,,"
                           f"{{\\an5\\pos({self.x},{y})}}{text}")

    def is_emph(self, w: Word) -> bool:
        k = round(w.start * 50)
        return k in self.emph or k - 1 in self.emph or k + 1 in self.emph

    # --------------------------------------------------------------- texto
    def _lines(self, parts: list[str]) -> list[list[int]]:
        """Índices das palavras por linha: uma linha se couber; senão duas, equilibradas ("QUE ESSA / GARRAFA",
        nunca "DE / PLÁSTICO FOSSE")."""
        idx = list(range(len(parts)))
        if len(parts) <= 1 or len(" ".join(parts)) <= self.chars:
            return [idx]
        best, cut = None, 1
        for k in range(1, len(parts)):
            a, b = len(" ".join(parts[:k])), len(" ".join(parts[k:]))
            score = max(a, b) + (0.5 if b > a else 0)  # empate: a linha de cima maior (pirâmide invertida)
            if best is None or score < best:
                best, cut = score, k
        return [idx[:cut], idx[cut:]]

    def _join(self, parts: list[str], fmt) -> str:
        return "\\N".join(" ".join(fmt(k, parts[k]) for k in line) for line in self._lines(parts))

    # --------------------------------------------------------------- estilos
    def build(self, words: list[Word]) -> str:
        st = self.st
        # até 2 linhas na tela (a quebra é por linha, em ``_lines``)
        groups = chunks(words, st.words, self.chars if st.mode == "word" else self.chars * 2 - 1)
        for gi, group in enumerate(groups):
            nxt = groups[gi + 1][0].start if gi + 1 < len(groups) else group[-1].end + 0.6
            end = min(nxt, group[-1].end + 0.5)  # segura a última palavra até a próxima (sem piscar)
            parts = [clean_word(w.text, self.upper) for w in group]
            if not any(parts):
                continue
            getattr(self, f"_{st.mode}")(group, parts, end, gi)
        return self.header() + "\n" + "\n".join(self.events) + "\n"

    def _times(self, group: list[Word], end: float) -> list[tuple[float, float]]:
        out = []
        for k, w in enumerate(group):
            t1 = group[k + 1].start if k + 1 < len(group) else end
            out.append((w.start, max(t1, w.start + 0.05)))
        return out

    def _word(self, group: list[Word], parts: list[str], end: float, gi: int) -> None:
        impact = self.name == "impacto"
        for k, ((t0, t1), txt) in enumerate(zip(self._times(group, end), parts)):
            if not txt:
                continue
            color = ""
            if impact:
                color = f"\\c{_ass_color(IMPACT_COLORS[(gi + k) % len(IMPACT_COLORS)])}"
                rot = self.rng.uniform(-5, 5)
                pop = (f"\\frz{rot:.1f}\\fscx40\\fscy40\\t(0,110,\\fscx122\\fscy122)"
                       f"\\t(110,200,\\fscx96\\fscy96)\\t(200,260,\\fscx100\\fscy100)")
            else:
                if _is_key(txt):
                    color = f"\\c{_ass_color(self.acc)}"
                pop = "\\fscx60\\fscy60\\t(0,90,\\fscx112\\fscy112)\\t(90,160,\\fscx100\\fscy100)"
            if self.is_emph(group[k]):  # palavra forte: cor de destaque e bate maior
                color = f"\\c{_ass_color(self.acc)}" if not impact else color
                pop = ("\\fscx50\\fscy50\\t(0,90,\\fscx138\\fscy138)\\t(90,180,\\fscx120\\fscy120)"
                       + (f"\\frz{self.rng.uniform(-4, 4):.1f}" if impact else ""))
            self.add(t0, t1, f"{{{pop}{color}}}{txt}")

    def _chunk(self, group: list[Word], parts: list[str], end: float, gi: int) -> None:
        acc = _ass_color(self.acc)
        for k, (t0, t1) in enumerate(self._times(group, end)):
            def fmt(j: int, p: str, k: int = k) -> str:
                em = self.is_emph(group[j])
                if j == k:
                    size = 124 if em else 108
                    return f"{{\\c{acc}\\fscx{size}\\fscy{size}}}{p}{{\\r}}"
                return f"{{\\c{acc}}}{p}{{\\r}}" if em and j < k else p
            enter = "{\\fscx86\\fscy86\\t(0,80,\\fscx100\\fscy100)}" if k == 0 else ""
            self.add(t0, t1, enter + self._join(parts, fmt))

    def _box(self, group: list[Word], parts: list[str], end: float, gi: int) -> None:
        for k, (t0, t1) in enumerate(self._times(group, end)):
            def box_fmt(j: int, p: str, k: int = k) -> str:
                return f"{{\\1a&HFF&\\3a&H{'00' if j == k else 'FF'}&\\4a&HFF&}}{p}"
            pop = "{\\fscx92\\fscy92\\t(0,70,\\fscx100\\fscy100)}"
            self.add(t0, t1, pop + self._join(parts, box_fmt), style="Box", layer=0)
            self.add(t0, t1, pop + self._join(parts, lambda j, p: p), style="Cap", layer=1)

    def _karaoke(self, group: list[Word], parts: list[str], end: float, gi: int) -> None:
        t0 = group[0].start
        pieces = []
        for (a, b), txt in zip(self._times(group, end), parts):
            pieces.append((max(1, round((b - a) * 100)), txt))
        lines = self._lines([p for _, p in pieces])

        def kf(j: int) -> str:
            big = "\\fscx118\\fscy118" if self.is_emph(group[j]) else ""
            return f"{{\\kf{pieces[j][0]}{big}}}{pieces[j][1]}" + ("{\\fscx100\\fscy100}" if big else "")
        text = "\\N".join(" ".join(kf(j) for j in line) for line in lines)
        self.add(t0, end, "{\\fad(60,60)}" + text)

    def _line(self, group: list[Word], parts: list[str], end: float, gi: int) -> None:
        acc = _ass_color(self.acc)
        self.add(group[0].start, end, "{\\fad(140,120)\\blur0.6}" + self._join(
            parts, lambda j, p: f"{{\\c{acc}}}{p}{{\\r}}" if self.is_emph(group[j]) else p))

    def _neon(self, group: list[Word], parts: list[str], end: float, gi: int) -> None:
        for k, (t0, t1) in enumerate(self._times(group, end)):
            def glow_fmt(j: int, p: str, k: int = k) -> str:
                return f"{{\\alpha&H{'00' if j == k else 'FF'}&}}{p}"

            def text_fmt(j: int, p: str, k: int = k) -> str:
                if j == k:
                    return f"{{\\fscx118\\fscy118}}{p}{{\\r}}" if self.is_emph(group[j]) else p
                return f"{{\\1a&H90&\\3a&HC0&}}{p}{{\\r}}"
            self.add(t0, t1, "{\\blur9}" + self._join(parts, glow_fmt), style="Glow", layer=0)
            self.add(t0, t1, self._join(parts, text_fmt), style="Cap", layer=1)

    def _type(self, group: list[Word], parts: list[str], end: float, gi: int) -> None:
        t0 = group[0].start
        tokens = []
        for (a, b), txt in zip(self._times(group, end), parts):
            dur = max(0.05, min(b - a, 0.06 * max(1, len(txt)) + 0.05))
            cs = max(1, round(dur * 100 / max(1, len(txt))))
            color = f"{{\\c{_ass_color(self.acc)}}}" if self.is_emph(group[len(tokens)]) else ""
            tokens.append(color + "".join(f"{{\\ko{cs}}}{ch}" for ch in txt) + ("{\\r}" if color else ""))
        lines = self._lines(parts)
        text = "\\N".join(" ".join(tokens[j] for j in line) for line in lines)
        self.add(t0, end, text)


def build_ass(words: list[Word], w: int, h: int, style: str = "frase",
              accent: tuple[int, int, int] = (255, 204, 0), position: float = 0.0, size: float = 1.0,
              uppercase: str = "auto", busy: list[tuple[float, float]] | None = None,
              emphasis: list[float] | None = None) -> str:
    return _Builder(w, h, style, accent, position, size, uppercase, busy=busy, emphasis=emphasis).build(words)


def _filter_path(path: Path) -> str:
    """Caminho dentro do filtro do FFmpeg (no Windows o "C:" precisa de escape)."""
    p = path.resolve().as_posix()
    return p.replace(":", "\\:").replace("'", "\\'")


def caption_filter(ass: Path, progress_bar: tuple[int, int, int] | None = None, total: float = 0.0) -> str:
    """Filtro do FFmpeg que queima a legenda (e a barrinha de progresso no topo, se pedida)."""
    vf = f"ass='{_filter_path(ass)}':fontsdir='{_filter_path(FONTS_DIR)}'"
    if progress_bar and total > 0:
        r, g, b = progress_bar
        vf += (f",drawbox=x=0:y=0:w='iw*min(1,t/{total:.3f})':h='max(6,ih/240)':"
               f"color=0x{r:02X}{g:02X}{b:02X}@0.95:t=fill")
    return vf


def burn(video: Path, ass: Path, dest: Path, encode_args: list[str], progress_bar: tuple[int, int, int] | None = None,
         total: float = 0.0, timeout: float = 3600) -> Path:
    """Queima a legenda (e a barrinha de progresso no topo, se pedida) no vídeo pronto. O áudio é copiado."""
    from .media import run

    vf = caption_filter(ass, progress_bar, total)
    run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(video), "-vf", vf, *encode_args,
         "-c:a", "copy", "-movflags", "+faststart", str(dest)], timeout=timeout, stage="legendas")
    return dest


# ------------------------------------------------------------------------------------- prévias do app
SAMPLE_PT = ("Ninguém esperava que essa garrafa de plástico fosse mudar a vida de 1 milhão de pessoas. "
             "E o melhor: custa quase nada.")


def sample_words(text: str = SAMPLE_PT, start: float = 0.25, rate: float = 3.1) -> list[Word]:
    out, t = [], start
    for raw in text.split():
        d = max(0.18, min(0.6, 0.09 * len(raw) + 0.08)) / (rate / 3)
        out.append(Word(raw, round(t, 3), round(t + d, 3)))
        t += d + (0.25 if raw.endswith((".", ":")) else 0.02)
    return out


def render_previews(out_dir: Path, background: Path | None = None, w: int = 540, h: int = 960,
                    seconds: float = 6.0, styles: list[str] | None = None) -> list[Path]:
    """Um vídeo curto (WebP animado + JPG parado) de cada estilo, para o app mostrar antes de escolher."""
    out_dir.mkdir(parents=True, exist_ok=True)
    words = sample_words()
    made: list[Path] = []
    bg_in = (["-loop", "1", "-i", str(background)] if background else
             ["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x1d2b44:c1=0x6b3b2a:x0=0:y0=0:x1={w}:y1={h}:d=6:speed=0.01"])
    zoom = (f"scale={w * 1.3:.0f}:{h * 1.3:.0f}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:'(iw-ow)/2+sin(t/2)*20':'(ih-oh)/2',eq=brightness=-0.08") if background else "null"
    for name in styles or list(STYLES):
        ass = out_dir / f"{name}.ass"
        ass.write_text(build_ass(words, w, h, name), encoding="utf-8")
        webp = out_dir / f"{name}.webp"
        vf = f"{zoom},fps=15,ass='{_filter_path(ass)}':fontsdir='{_filter_path(FONTS_DIR)}'"
        subprocess.run(["ffmpeg", "-y", "-v", "error", *bg_in, "-t", f"{seconds}", "-vf", vf, "-c:v", "libwebp_anim",
                        "-loop", "0", "-q:v", "62", "-an", str(webp)], check=True, timeout=300)
        jpg = out_dir / f"{name}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", *bg_in, "-t", f"{seconds}", "-vf", vf, "-ss", "2.2",
                        "-frames:v", "1", "-q:v", "3", str(jpg)], check=True, timeout=120)
        ass.unlink(missing_ok=True)
        made += [webp, jpg]
    return made


if __name__ == "__main__":  # python -m broll_bot.captions [--fundo foto.jpg]
    import argparse

    ap = argparse.ArgumentParser(description="Gera as prévias das legendas para o app")
    ap.add_argument("--fundo", type=Path, default=None)
    ap.add_argument("--saida", type=Path, default=Path(__file__).resolve().parent.parent / "studio" / "web" / "legendas")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("FFmpeg não encontrado")
    for p in render_previews(args.saida, args.fundo):
        print(p)
