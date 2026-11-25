"""Enquadramentos variados (o que evita cara de template).

Cada shot recebe um layout sorteado com seed própria, então a mesma seed
sempre gera o mesmo vídeo, independentemente da ordem de renderização.

Layouts de vídeo:  full · zoom_crop · push (zoom lento) · inset (fundo desfocado)
Layouts de imagem: kenburns (zoom/pan com direção e velocidade variadas) · inset
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from PIL import Image

from .models import MediaType

KENBURNS_MOVES = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "diagonal")


def _weighted(options: dict[str, float], rng: random.Random, avoid: str | None) -> str:
    items = [(k, w) for k, w in options.items() if w > 0 and k != avoid] or list(options.items())
    total = sum(w for _, w in items)
    pick = rng.uniform(0, total)
    for key, weight in items:
        pick -= weight
        if pick <= 0:
            return key
    return items[-1][0]


def choose_layout(media_type: MediaType, source_aspect: float, video_layouts: dict[str, float],
                  image_layouts: dict[str, float], rng: random.Random, previous: str | None,
                  out_aspect: float = 16 / 9) -> dict[str, Any]:
    """Sorteia o layout e seus parâmetros. Mídia com a orientação contrária à do vídeo (em pé num vídeo deitado)
    vai de inset; num vídeo em pé, a foto deitada passeia de lado (pan) para encher a tela."""
    vertical_out = out_aspect < 1.0
    mismatch = (source_aspect < 1.0) != vertical_out
    if vertical_out and media_type is MediaType.IMAGE and source_aspect > 1.2:
        name = "kenburns"
    elif mismatch and not vertical_out:
        name = "inset"
    elif media_type is MediaType.IMAGE:
        name = _weighted(image_layouts, rng, previous)
    else:
        name = _weighted(video_layouts, rng, previous)

    r3 = lambda a, b: round(rng.uniform(a, b), 3)  # noqa: E731
    layout: dict[str, Any] = {"name": name}
    if name == "full":
        pass
    elif name == "zoom_crop":
        layout.update(zoom=r3(1.08, 1.28), fx=r3(0.2, 0.8), fy=r3(0.25, 0.75))
    elif name == "push":
        z0 = r3(1.0, 1.06)
        layout.update(z0=z0, z1=round(z0 + rng.uniform(0.06, 0.16), 3), fx=r3(0.35, 0.65), fy=r3(0.35, 0.65))
    elif name == "kenburns":
        # foto deitada num vídeo em pé: o recorte em pé passeia pela foto inteira (de um lado ao outro)
        moves = ("pan_left", "pan_right", "pan_left", "pan_right", "zoom_in") if vertical_out and source_aspect > 1.2 \
            else KENBURNS_MOVES
        move = rng.choice(moves)
        strength = rng.uniform(0.08, 0.22)
        z_hi = round(1.0 + strength + 0.05, 3)
        if move == "zoom_in":
            layout.update(z0=1.0, z1=z_hi, fx0=0.5, fx1=r3(0.4, 0.6), fy0=0.5, fy1=r3(0.4, 0.6))
        elif move == "zoom_out":
            layout.update(z0=z_hi, z1=1.0, fx0=r3(0.35, 0.65), fx1=0.5, fy0=r3(0.35, 0.65), fy1=0.5)
        elif move == "pan_left":
            layout.update(z0=z_hi, z1=z_hi, fx0=r3(0.75, 1.0), fx1=r3(0.0, 0.25), fy0=0.5, fy1=0.5)
        elif move == "pan_right":
            layout.update(z0=z_hi, z1=z_hi, fx0=r3(0.0, 0.25), fx1=r3(0.75, 1.0), fy0=0.5, fy1=0.5)
        elif move == "pan_up":
            layout.update(z0=z_hi, z1=z_hi, fx0=0.5, fx1=0.5, fy0=r3(0.75, 1.0), fy1=r3(0.0, 0.25))
        else:
            layout.update(z0=1.02, z1=z_hi, fx0=r3(0.0, 0.3), fx1=r3(0.7, 1.0), fy0=r3(0.0, 0.3), fy1=r3(0.7, 1.0))
        layout["move"] = move
        if vertical_out and move in ("pan_left", "pan_right"):  # plano curto: meia travessia, sem correria
            a, b = r3(0.62, 0.78), r3(0.22, 0.38)
            layout.update(fx0=a if move == "pan_left" else b, fx1=b if move == "pan_left" else a)
    elif name == "inset":
        layout.update(scale=r3(0.72, 0.9), fx=r3(0.42, 0.58), fy=r3(0.44, 0.56), blur=rng.randint(18, 32),
                      dim=r3(-0.12, -0.04), drift=r3(0.0, 0.08))
    return layout


# Lavagem de cor (duotone): a imagem vira verde-água e depois azul forte por um instante.
WASH_TEAL = (0.30, 0.98, 0.93)
WASH_BLUE = (0.16, 0.30, 1.22)
LUMA = (0.299, 0.587, 0.114)

# efeitos que precisam de uma 2ª entrada no comando do FFmpeg
EXTRA_INPUT_EFFECTS = {"leak", "burn"}
BURN_SECONDS = 0.62  # duração da queima de filme no início do shot


def _duotone(color: tuple[float, float, float], enable: str) -> str:
    rows = [f"{ch}r={c * LUMA[0]:.4f}:{ch}g={c * LUMA[1]:.4f}:{ch}b={c * LUMA[2]:.4f}" for ch, c in zip("rgb", color)]
    return f"colorchannelmixer={':'.join(rows)}:enable='{enable}'"


def effect_chain(w: int, h: int, fps: int, frames: int, effect_in: str | None, effect_out: str | None) -> str:
    """Efeitos de corte aplicados sobre o shot já em WxH (sem 'leak'/'burn', que usam 2ª entrada)."""
    parts: list[str] = []
    total = frames / fps
    whip_in = 0.2 if effect_in == "whip" else 0.0
    whip_out = 0.2 if effect_out == "whip" else 0.0
    if whip_in or whip_out:
        # chicote: a imagem desliza rápido na horizontal com desfoque de movimento
        d_in = f"if(lt(t,{whip_in}),pow(1-t/{whip_in},2),0)" if whip_in else "0"
        t0 = max(0.0, total - whip_out)
        d_out = f"if(gt(t,{t0:.3f}),pow((t-{t0:.3f})/{whip_out},2),0)" if whip_out else "0"
        parts.append(f"scale={_even(w * 1.14)}:{_even(h * 1.14)},"
                     f"crop={w}:{h}:x='(iw-ow)/2*(1+{d_in}-{d_out})':y='(ih-oh)/2'")
        cond = "+".join(c for c in (f"lt(t,{whip_in})" if whip_in else "", f"gt(t,{t0:.3f})" if whip_out else "") if c)
        parts.append(f"avgblur=sizeX={max(8, round(w / 40))}:sizeY=1:enable='{cond}'")
    if effect_in == "punch":
        pn = max(2, round(0.32 * fps))
        parts.append(f"zoompan=z='if(lt(on,{pn}),1.28-0.28*(1-pow(1-on/{pn},3)),1)'"
                     f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}")
    if effect_in == "glitch":
        g = max(4, round(14 * w / 1920))
        parts.append(f"rgbashift=rh=-{g}:bh={g}:enable='lt(t,0.22)',noise=alls=40:allf=t+u:enable='lt(t,0.22)'")
    if effect_in == "wash":
        # verde-água lavado (0–0.1 s) -> azul forte (0.1–0.27 s) -> imagem normal
        parts.append(_duotone(WASH_TEAL, "lt(t,0.1)"))
        parts.append("eq=brightness=0.10:contrast=0.78:enable='lt(t,0.1)'")
        parts.append(_duotone(WASH_BLUE, "between(t,0.1,0.27)"))
    if effect_in == "blackout":
        # tela preta com barrinhas de "dados" na lateral e depois um tranco RGB
        parts.append("drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='lt(t,0.1)'")
        bx, bw = round(w * 0.957), max(4, round(w * 0.012))
        for k, (yy, color) in enumerate(((0.25, "0xF2B33D"), (0.27, "0xF2B33D"), (0.40, "0xEDEDED"),
                                          (0.42, "0xEDEDED"), (0.44, "0xEDEDED"), (0.46, "0xEDEDED"))):
            parts.append(f"drawbox=x={bx}:y={round(h * yy)}:w={bw}:h={max(2, round(h * 0.008))}"
                         f":color={color}:t=fill:enable='lt(t,0.1)'")
        g = max(4, round(18 * w / 1920))
        parts.append(f"rgbashift=rh={g}:bh=-{g}:enable='between(t,0.1,0.2)'")
    return ("," + ",".join(parts)) if parts else ""


def old_film(w: int, h: int) -> str:
    """Filme antigo (estilo Documentário): preto e branco puxado para o sépia, luz variando devagar, grão fino, um
    risco vertical de vez em quando, poeira e vinheta; a borda preta do filme de época sai num corte fixo. Tudo no
    FFmpeg, sem entrada extra (dá para somar com o leak/burn).

    Nada mexe o quadro: a 1ª versão balançava a imagem (até 8 px de um quadro para o outro, imitando o projetor) e
    piscava a luz 5 vezes por segundo, e no vídeo da Operação Greif a tela inteira tremia ("horrível de assistir")."""
    return (",crop=iw*0.97:ih*0.97,"
            f"scale={w}:{h}:flags=bicubic,hue=s=0,"
            "colorchannelmixer=rr=.85:rg=.25:rb=.05:gr=.75:gg=.3:gb=.05:br=.6:bg=.25:bb=.1,"
            f"eq=contrast=1.06:brightness='{OLD_FILM_FLICKER}*sin(t*5.3)+0.006*sin(t*1.7)':eval=frame,"
            f"noise=alls={OLD_FILM_GRAIN}:allf=t,"
            f"drawbox=x='iw*(0.15+0.7*abs(sin(floor(t*2)*12.9898)))':y=0:w={max(1, round(2 * w / 1920))}:h=ih:"
            "color=white@0.18:t=fill:enable='lt(mod(t*2,1),0.25)',"
            f"drawbox=x='iw*abs(sin(floor(t*4)*78.233))':y='ih*abs(sin(floor(t*4)*39.34))':"
            f"w={max(2, round(4 * w / 1920))}:h={max(2, round(3 * w / 1920))}:color=black@0.5:t=fill,"
            "vignette=angle=PI/4.5")


OLD_FILM_FLICKER = 0.012  # amplitude da luz variando (era 0,035 a 5 Hz: piscava)
OLD_FILM_GRAIN = 9  # grão (16 no filme de 640 px ampliado para 1080p fervia)


def build_filter(layout: dict[str, Any], w: int, h: int, fps: int, frames: int, speed: float = 1.0,
                 src_aspect: float = 16 / 9, effect_in: str | None = None, effect_out: str | None = None,
                 hold: float = 0.0, grade: str | None = None) -> str:
    """Filtergraph de ``[0:v]`` até ``[v]`` para um shot já normalizado em WxH@fps.

    Com ``effect_in`` em ``EXTRA_INPUT_EFFECTS`` o comando precisa de uma 2ª entrada:
    a imagem do vazamento de luz (``leak``) ou o clipe de queima de filme (``burn``,
    fundo preto, misturado em modo *screen* nos primeiros instantes do shot).
    ``hold`` (s): o clipe acaba antes do shot; o último quadro segura antes do enquadramento,
    então o zoom/push continua andando por cima dele (em vez de repetir o clipe)."""
    head = f"[0:v]setpts=(PTS-STARTPTS)*{speed:.4f},fps={fps},setsar=1"
    if hold > 0:
        head += f",tpad=stop_mode=clone:stop_duration={hold + 0.5:.3f}"
    fx = (old_film(w, h) if grade == "oldfilm" else "") + effect_chain(w, h, fps, frames, effect_in, effect_out)
    end = "setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=4[v]"
    if effect_in == "leak":
        tail = (f"setsar=1{fx}[pre];[1:v]format=rgba,fade=t=out:st=0.12:d=0.5:alpha=1[lk];"
                f"[pre][lk]overlay=0:0:shortest=1,{end}")
    elif effect_in == "burn":
        d = BURN_SECONDS
        tail = (f"setsar=1{fx},format=gbrp[pre];"
                f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
                f"trim=duration={d},setpts=PTS-STARTPTS,fade=t=out:st={d - 0.22:.2f}:d=0.22,format=gbrp,"
                f"tpad=stop_mode=add:stop_duration=600:color=black[bn];"
                f"[pre][bn]blend=all_mode=screen:shortest=1,{end}")
    else:
        tail = f"setsar=1{fx},{end}"
    n = max(1, frames - 1)
    name = layout.get("name", "full")

    def cover(scale: float = 1.0) -> str:
        sw, sh = _even(w * scale), _even(h * scale)
        return f"scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos"

    if name == "zoom_crop":
        z = layout["zoom"]
        return f"{head},{cover(z)},crop={w}:{h}:(iw-{w})*{layout['fx']}:(ih-{h})*{layout['fy']},{tail}"

    if name in ("push", "kenburns"):
        z0, z1 = layout["z0"], layout["z1"]
        fx0, fx1 = layout.get("fx0", layout.get("fx", 0.5)), layout.get("fx1", layout.get("fx", 0.5))
        fy0, fy1 = layout.get("fy0", layout.get("fy", 0.5)), layout.get("fy1", layout.get("fy", 0.5))
        # foto parada mostra cada detalhe: interpolação cúbica; em vídeo a linear basta
        # (vídeo: sem pré-ampliação, que dobraria o custo e quase não aparece em movimento)
        move = smooth_zoom(w, h, n, z0, z1, fx0, fx1, fy0, fy1, cubic=name == "kenburns",
                           supersample=name == "kenburns")
        return f"{head},{move},{tail}"

    if name == "inset":
        box_w, box_h = w * layout["scale"], h * layout["scale"]
        if src_aspect >= box_w / box_h:
            fw, fh = _even(box_w), _even(box_w / src_aspect)
        else:
            fw, fh = _even(box_h * src_aspect), _even(box_h)
        drift = layout.get("drift", 0.0)
        # fundo: a própria mídia ampliada, desfocada e escurecida; frente: mídia inteira
        # com um zoom bem leve (drift) para não ficar estática
        fg = (smooth_zoom(fw, fh, n, 1.0, 1.0 + drift, 0.5, 0.5, 0.5, 0.5) if drift > 0.001
              else f"scale={fw}:{fh}:force_original_aspect_ratio=increase:flags=lanczos,crop={fw}:{fh}")
        return (
            f"{head},split=2[bgsrc][fgsrc];"
            f"[bgsrc]{cover(1.0)},crop={w}:{h},gblur=sigma={layout['blur']},eq=brightness={layout['dim']}[bg];"
            f"[fgsrc]{fg}[fg];"
            f"[bg][fg]overlay=x=(W-w)*{layout['fx']}:y=(H-h)*{layout['fy']}:shortest=1,{tail}"
        )

    # full
    return f"{head},{cover(1.0)},crop={w}:{h},{tail}"


def smooth_zoom(w: int, h: int, n: int, z0: float, z1: float, fx0: float, fx1: float, fy0: float, fy1: float,
                cubic: bool = False, supersample: bool = True) -> str:
    """Zoom/pan com precisão subpixel, sem o "tremido" do zoompan.

    O zoompan arredonda a janela para pixels inteiros, então um movimento lento anda
    aos saltos (0, 0,25, -0,1 px...). Aqui a janela é levada ao quadro inteiro pelo
    filtro ``perspective`` (1/256 de pixel de precisão), sobre a imagem pré-ampliada
    no zoom máximo (1:1 no ponto mais fechado) e reduzida no fim com lanczos.
    Movimento com aceleração e freada suaves (cosseno), como uma câmera de verdade."""
    pre = min(1.4, max(1.0, z0, z1)) if supersample else 1.0
    pw, ph = _even(w * pre), _even(h * pre)
    p = f"(0.5-0.5*cos(PI*min(in/{max(1, n)},1)))"
    z = f"({z0}+({z1 - z0:.4f})*{p})"
    ww, wh = f"(W/{z})", f"(H/{z})"
    x = f"((W-{ww})*({fx0}+({fx1 - fx0:.4f})*{p}))"
    y = f"((H-{wh})*({fy0}+({fy1 - fy0:.4f})*{p}))"
    persp = (f"perspective=x0='{x}':y0='{y}':x1='{x}+{ww}':y1='{y}':x2='{x}':y2='{y}+{wh}'"
             f":x3='{x}+{ww}':y3='{y}+{wh}':interpolation={'cubic' if cubic else 'linear'}:eval=frame")
    return (f"scale={pw}:{ph}:force_original_aspect_ratio=increase:flags=lanczos,crop={pw}:{ph},{persp},"
            f"scale={w}:{h}:flags=lanczos")


def kenburns_frames(path: str | Path, layout: dict[str, Any], w: int, h: int, frames: int) -> Iterator[bytes]:
    """Quadros RGB do Ken Burns de uma foto, com posição e zoom em fração de pixel.

    Mesmo movimento do ``smooth_zoom``, só que calculado no Pillow (3x mais rápido que o
    filtro ``perspective`` do FFmpeg): a foto é ampliada uma vez no zoom máximo e cada
    quadro é uma janela com coordenadas fracionárias reduzida para WxH."""
    z0, z1 = layout["z0"], layout["z1"]
    fx0, fx1 = layout.get("fx0", layout.get("fx", 0.5)), layout.get("fx1", layout.get("fx", 0.5))
    fy0, fy1 = layout.get("fy0", layout.get("fy", 0.5)), layout.get("fy1", layout.get("fy", 0.5))
    pre = min(1.4, max(1.0, z0, z1))
    pw, ph = _even(w * pre), _even(h * pre)
    with Image.open(path) as img:
        img = img.convert("RGB")
        scale = max(pw / img.width, ph / img.height)
        nw, nh = max(pw, round(img.width * scale)), max(ph, round(img.height * scale))
        left, top = (nw - pw) / 2, (nh - ph) / 2
        base = img.resize((pw, ph), Image.LANCZOS, box=(left / scale, top / scale,
                                                        (left + pw) / scale, (top + ph) / scale))
    n = max(1, frames - 1)
    for i in range(frames):
        p = 0.5 - 0.5 * math.cos(math.pi * min(i / n, 1.0))
        z = z0 + (z1 - z0) * p
        cw, ch = pw / z, ph / z
        x = (pw - cw) * (fx0 + (fx1 - fx0) * p)
        y = (ph - ch) * (fy0 + (fy1 - fy0) * p)
        yield base.resize((w, h), Image.BICUBIC, box=(x, y, x + cw, y + ch)).tobytes()


def _even(value: float) -> int:
    v = int(round(value))
    return v if v % 2 == 0 else v + 1
