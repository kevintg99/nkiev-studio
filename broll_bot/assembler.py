"""Montagem com FFmpeg: normaliza cada shot, aplica transições (xfade) e mixa a narração."""

from __future__ import annotations

import subprocess
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .config import AppConfig
from .encoder import NAMES, choose, encode_args
from .framing import BURN_SECONDS, build_filter, kenburns_frames
from .log import get_logger
from .media import FFmpegError, run
from .models import MediaType, Shot
from .motion import TemplateSpec, render_template
from .motion.overlays import apply_overlay

log = get_logger("assembler")


@dataclass(slots=True)
class SeqItem:
    path: Path
    start_frame: int
    end_frame: int
    transition: str  # tipo do xfade de entrada ou "cut"

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


class Assembler:
    def __init__(self, cfg: AppConfig, work_dir: Path) -> None:
        self.cfg = cfg
        self.out = cfg.output
        self.work_dir = work_dir
        self._leak_lock = threading.Lock()
        self._burn_lock = threading.Lock()
        self.overlay_dir: Path | None = None  # clipes reais de queima de filme / light leak (opcional)
        work_dir.mkdir(parents=True, exist_ok=True)
        # chip de vídeo (QuickSync/NVENC/AMF) quando existe: várias vezes mais rápido que o libx264
        self.encoder = choose(getattr(self.out, "encoder", "auto"))
        self._local = threading.local()  # "este shot refaz no processador" (por thread)
        self._hw_lock = threading.Lock()
        self._hw_failures = 0
        self.encoded_with: dict[str, str] = {}  # arquivo -> codificador usado (para colar blocos sem recodificar)
        self.last_assembly = ""  # "copy" (blocos colados) ou "encode" (passada final recodificando)

    # ------------------------------------------------------------- shots
    def _current_encoder(self) -> str:
        return "cpu" if getattr(self._local, "force_cpu", False) else self.encoder

    def _encode_args(self, final: bool) -> list[str]:
        r = self.cfg.render
        crf, preset = (self.out.crf, self.out.preset) if final else (r.intermediate_crf, r.intermediate_preset)
        return encode_args(self._current_encoder(), crf, preset, self.out.fps)

    def _with_fallback(self, fn, *args, **kwargs):
        """Se o chip de vídeo falhar num shot, refaz aquele shot no processador. Depois de 3 falhas
        que o processador resolveu, o resto do vídeo vai direto no processador."""
        from .disk import DiskFullError

        try:
            return fn(*args, **kwargs)
        except DiskFullError:
            raise  # sem espaço o processador também não resolve
        except FFmpegError as exc:
            if self.encoder == "cpu" or getattr(self._local, "force_cpu", False):
                raise
            first_error = exc
        self._local.force_cpu = True
        try:
            out = fn(*args, **kwargs)
        finally:
            self._local.force_cpu = False
        with self._hw_lock:
            self._hw_failures += 1
            give_up = self._hw_failures >= 3 and self.encoder != "cpu"
            if give_up:
                self.encoder = "cpu"
        log.warning("chip de vídeo falhou; refeito no processador",
                    extra={"stage": "render", "encoder": NAMES.get(self.encoder, self.encoder),
                           "error": str(first_error)[-200:]})
        if give_up:
            log.warning("chip de vídeo falhou 3 vezes; o resto vai no processador", extra={"stage": "render"})
        return out

    def _avatar_under_overlay(self, shot: Shot) -> bool:
        """Avatar em tela cheia num shot com overlay (botão de inscrição...): o avatar entra no b-roll e o overlay
        vai por cima dele (ver _render_shot)."""
        return bool(shot.avatar) and self.cfg.avatar.layout == "full" and bool(shot.overlay) \
            and not (shot.template and shot.template_data) and shot.media_path is not None \
            and shot.candidate is not None

    def render_shot(self, shot: Shot, dest: Path) -> Path:
        if not shot.avatar or self._avatar_under_overlay(shot):
            return self._with_fallback(self._render_shot, shot, dest)
        base = self._with_fallback(self._render_shot, shot, dest.with_name(dest.stem + "_pre.mp4"))
        try:
            self._with_fallback(self._add_avatar, shot, base, dest)
        except (FFmpegError, OSError) as exc:
            from .disk import DiskFullError

            if isinstance(exc, DiskFullError):
                raise
            log.warning("avatar falhou neste shot; segue sem ele", extra={"stage": "render", "shot": shot.index,
                                                                          "error": str(exc)[-300:]})
            shot.avatar = []
            base.replace(dest)
            return dest
        base.unlink(missing_ok=True)
        return dest

    def _avatar_into(self, shot: Shot, base: Path) -> Path:
        with_avatar = base.with_name(base.stem + "_av.mp4")
        try:
            self._add_avatar(shot, base, with_avatar)
        except FFmpegError as exc:
            from .disk import DiskFullError

            if isinstance(exc, DiskFullError) or self._current_encoder() != "cpu":
                raise  # no chip de vídeo: o shot inteiro é refeito no processador (_with_fallback)
            log.warning("avatar falhou neste shot; segue sem ele", extra={"stage": "render", "shot": shot.index,
                                                                          "error": str(exc)[-300:]})
            shot.avatar = []
            return base
        base.unlink(missing_ok=True)
        return with_avatar

    def _add_avatar(self, shot: Shot, base: Path, dest: Path) -> Path:
        from .avatar import composite

        return composite(base, shot.avatar, dest, self.cfg.avatar, self.out.width, self.out.height, self.out.fps,
                         shot.frames, self._encode_args(final=False))

    def _render_shot(self, shot: Shot, dest: Path) -> Path:
        if shot.template and shot.template_data:
            return self.render_template_shot(shot, dest)
        if shot.media_path is None or shot.candidate is None:
            return self.render_generated(shot, dest)
        if not shot.overlay:
            return self._render_broll(shot, dest)
        base = self._render_broll(shot, dest.with_name(dest.stem + "_base.mp4"))
        if self._avatar_under_overlay(shot):
            base = self._avatar_into(shot, base)
        try:
            apply_overlay(base, shot.overlay, dest, self.out.width, self.out.height, self.out.fps, shot.frames,
                          self._encode_args(final=False))
        except (FFmpegError, OSError, KeyError, ValueError) as exc:
            log.warning("overlay falhou; shot segue sem ele", extra={"stage": "render", "shot": shot.index,
                                                                      "error": str(exc)[-300:]})
            shot.overlay = {}
            base.replace(dest)
            return dest
        base.unlink(missing_ok=True)
        return dest

    def _render_broll(self, shot: Shot, dest: Path) -> Path:
        assert shot.media_path is not None and shot.candidate is not None
        w, h, fps = self.out.width, self.out.height, self.out.fps
        is_image = shot.candidate.media_type is MediaType.IMAGE
        # Ken Burns de foto: o movimento é calculado em Python (subpixel, sem tremido) e
        # entra pronto pelo stdin; o FFmpeg só aplica os efeitos de corte
        kenburns = is_image and shot.layout.get("name") == "kenburns"
        layout = {"name": "full"} if kenburns else shot.layout
        # clipe mais curto que o plano: nunca em loop (o trecho voltando ao começo "vai e volta" na tela);
        # o último quadro segura e o movimento do enquadramento continua por cima dele
        hold = 0.0
        if not is_image:
            src_dur = shot.candidate.duration or 0.0
            shown = (src_dur - shot.source_offset) * max(shot.speed, 1e-3)
            if src_dur and shown < shot.frames / fps - 0.05:
                hold = shot.frames / fps - shown
        filt = build_filter(layout, w, h, fps, shot.frames, speed=1.0 if kenburns else shot.speed,
                            src_aspect=shot.candidate.aspect, effect_in=shot.effect_in, effect_out=shot.effect_out,
                            hold=hold, grade=self.grade_for(shot))
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if kenburns:
            cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
        elif is_image:
            cmd += ["-nostdin", "-loop", "1", "-framerate", str(fps), "-i", shot.media_path]
        else:
            cmd += ["-nostdin", "-ss", f"{shot.source_offset:.3f}", "-i", shot.media_path]
        if shot.effect_in == "leak":
            cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(self.leak_image())]
        elif shot.effect_in == "burn":
            cmd += ["-i", str(self.burn_clip(shot.index))]
        cmd += ["-filter_complex", filt, "-map", "[v]", "-frames:v", str(shot.frames), "-an",
                *self._encode_args(final=False), str(dest)]
        if kenburns:
            _pipe_frames(cmd, kenburns_frames(shot.media_path, shot.layout, w, h, shot.frames))
        else:
            run(cmd, timeout=600, stage="render_shot")
        return dest

    def grade_for(self, shot: Shot) -> str | None:
        """Filme antigo (estilo Documentário): sempre no filme de época (``timeline.old_film_providers``) e, em
        alguns trechos, no b-roll de vídeo comum também (a cena inteira, ``old_film_chance``), para a floresta com
        neve do Pexels entrar no clima de 1944 junto com o cinejornal. Foto não: a foto colorida da Wikimedia de
        hoje não vira "filme"."""
        tl, cand = self.cfg.timeline, shot.candidate
        if cand is None:
            return None
        if cand.provider in tl.old_film_providers:
            return "oldfilm"
        if tl.old_film_chance > 0 and cand.media_type is MediaType.VIDEO and cand.provider not in ("youtube_cc",
                                                                                                      "youtube_any"):
            # sorteio pela cena (todos os planos dela juntos), estável entre uma renderização e outra
            roll = (shot.scene_index * 2654435761 % 2 ** 32) / 2 ** 32
            if roll < tl.old_film_chance:
                return "oldfilm"
        return None

    def render_template_shot(self, shot: Shot, dest: Path) -> Path:
        spec = TemplateSpec.from_dict({**shot.template_data, "kind": shot.template, "w": self.out.width,
                                       "h": self.out.height, "fps": self.out.fps, "frames": shot.frames})
        return render_template(spec, dest, self._encode_args(final=False))

    def leak_image(self) -> Path:
        """Vazamento de luz (laranja/rosa) gerado uma vez e reaproveitado."""
        path = self.work_dir / "light_leak.png"
        with self._leak_lock:  # shots são renderizados em paralelo
            if not path.exists():
                self._make_leak(path)
        return path

    def _make_leak(self, path: Path) -> None:
        w, h = self.out.width, self.out.height
        y, x = np.ogrid[0:h, 0:w]
        glow = np.zeros((h, w, 4), np.float32)
        for cx, cy, r, color in ((0.85, 0.2, 0.55, (255, 150, 60)), (0.1, 0.9, 0.45, (255, 80, 140))):
            d = np.sqrt(((x - cx * w) / w) ** 2 + ((y - cy * h) / h) ** 2)
            a = np.clip(1 - d / r, 0, 1) ** 1.8
            glow[..., :3] += np.array(color, np.float32) * a[..., None]
            glow[..., 3] = np.maximum(glow[..., 3], a * 235)
        Image.fromarray(glow.clip(0, 255).astype(np.uint8), "RGBA").save(path)

    BURN_VARIANTS = 3

    def burn_clip(self, seed: int) -> Path:
        """Clipe de queima de filme (fundo preto, entra em modo *screen*).

        Usa os vídeos que o usuário puser em ``library/overlays`` (ex.: "film burn"
        grátis do Mixkit); sem eles, gera variações procedurais uma vez por execução."""
        if self.overlay_dir is not None and self.overlay_dir.is_dir():
            clips = sorted(p for p in self.overlay_dir.iterdir()
                           if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"})
            if clips:
                return clips[seed % len(clips)]
        k = seed % self.BURN_VARIANTS
        path = self.work_dir / f"film_burn_{k}.mp4"
        with self._burn_lock:
            if not path.exists():
                self._make_burn(path, k)
        return path

    def _make_burn(self, path: Path, seed: int) -> None:
        from .motion.gfx import FrameWriter  # import tardio: evita ciclo na importação

        fps = self.out.fps
        w, h = 960, 540
        frames = int(BURN_SECONDS * fps) + 3
        rng = np.random.default_rng(1000 + seed)

        def noise(gh: int, gw: int) -> np.ndarray:
            small = Image.fromarray((rng.random((gh, gw)) * 255).astype(np.uint8))
            return np.asarray(small.resize((w, h), Image.BICUBIC), np.float32) / 255

        # manchas grandes + detalhe médio (borda irregular) + granulado fino (faíscas)
        coarse = 0.7 * noise(6, 10) + 0.3 * noise(18, 32)
        fine = rng.random((h, w)).astype(np.float32)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        xn, yn = xx / w, yy / h
        tilt = rng.uniform(-0.3, 0.3)
        stops = np.array([0.0, 0.3, 0.55, 0.8, 1.0], np.float32)
        colors = np.array([(0, 0, 0), (140, 22, 0), (250, 92, 14), (255, 168, 48), (255, 214, 120)], np.float32)
        writer = FrameWriter(path, w, h, fps, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                                               "-pix_fmt", "yuv420p"])
        try:
            for i in range(frames):
                p = i / max(1, frames - 1)
                sweep = -0.35 + 1.5 * p  # a mancha atravessa a tela da esquerda para a direita
                env = np.sin(np.pi * min(1.0, p * 1.15)) ** 0.6
                band = 1 - np.abs(xn + tilt * (yn - 0.5) - sweep) / 0.5
                field = band * 1.25 + (coarse - 0.5) * 1.3
                inten = np.clip((field - 0.25) / 0.45, 0, 1)
                inten = inten * inten * (3 - 2 * inten) * env  # smoothstep: borda de "queimado"
                rgb = np.stack([np.interp(inten, stops, colors[:, c]) for c in range(3)], axis=-1)
                rgb *= (0.88 + 0.24 * fine)[..., None]  # granulado de película
                sparks = (fine > 0.996) & (inten > 0.08) & (inten < 0.7)
                rgb[sparks] = (255, 236, 170)
                img = Image.fromarray(rgb.clip(0, 255).astype(np.uint8), "RGB")
                writer.write(img)
                fine = np.roll(fine, 7, axis=1)
        except BaseException:
            writer.abort()
            raise
        writer.close()

    def render_generated(self, shot: Shot, dest: Path) -> Path:
        return self._with_fallback(self._render_generated, shot, dest)

    def _render_generated(self, shot: Shot, dest: Path) -> Path:
        """Último recurso: fundo em degradê animado e discreto (nunca derruba o vídeo)."""
        w, h, fps = self.out.width, self.out.height, self.out.fps
        dur = shot.frames / fps + 0.5
        sources = [
            f"gradients=s={w}x{h}:c0=0x0f1720:c1=0x1f2f3f:c2=0x101820:speed=0.004:r={fps}:d={dur:.3f}",
            f"color=c=0x141a22:s={w}x{h}:r={fps}:d={dur:.3f}",
        ]
        last_error: Exception | None = None
        for src in sources:
            try:
                run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "lavfi", "-i", src,
                     "-vf", "format=yuv420p", "-frames:v", str(shot.frames), "-an",
                     *self._encode_args(final=False), str(dest)], timeout=300, stage="render_shot")
                return dest
            except FFmpegError as exc:
                last_error = exc
        raise FFmpegError(f"não foi possível gerar fundo substituto: {last_error}")

    # ---------------------------------------------------------- sequence
    def render_sequence(self, items: list[SeqItem], dest: Path, audio: Path | None = None,
                        total_seconds: float | None = None, final_quality: bool = False) -> Path:
        return self._with_fallback(self._render_sequence, items, dest, audio, total_seconds, final_quality)

    def _render_sequence(self, items: list[SeqItem], dest: Path, audio: Path | None = None,
                         total_seconds: float | None = None, final_quality: bool = False) -> Path:
        """Encadeia os itens com xfade (ou concat nos cortes secos).

        ``start_frame``/``end_frame`` são posições absolutas na linha do tempo,
        então offset e duração de cada transição saem direto delas.
        """
        if not items:
            raise ValueError("sequência vazia")
        fps = self.out.fps
        final = audio is not None
        base = items[0].start_frame
        inputs: list[str] = []
        graph: list[str] = []
        for i, item in enumerate(items):
            inputs += ["-i", str(item.path)]
            # mesma base de tempo em tudo: xfade exige que as duas entradas coincidam
            graph.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={fps},setsar=1,format=yuv420p,"
                         f"trim=end_frame={item.frames},setpts=PTS-STARTPTS,settb=AVTB[s{i}]")
        acc = "s0"
        acc_end = items[0].end_frame - base
        for i, item in enumerate(items[1:], start=1):
            start_rel = item.start_frame - base
            overlap = acc_end - start_rel
            label = f"x{i}"
            if overlap > 0:
                kind = item.transition if item.transition != "cut" else "fade"
                graph.append(f"[{acc}][s{i}]xfade=transition={kind}:duration={overlap / fps:.6f}"
                             f":offset={start_rel / fps:.6f}[{label}]")
            else:
                graph.append(f"[{acc}][s{i}]concat=n=2:v=1:a=0[{label}]")
            acc, acc_end = label, item.end_frame - base

        cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", *inputs]
        if final:
            graph.append(f"[{acc}]tpad=stop_mode=clone:stop_duration=2,format=yuv420p[vout]")
            cmd += ["-i", str(audio), "-filter_complex", ";".join(graph),
                    "-map", "[vout]", "-map", f"{len(items)}:a:0",
                    *self._encode_args(final=True),
                    "-c:a", "aac", "-b:a", self.out.audio_bitrate,
                    "-t", f"{total_seconds:.3f}", "-movflags", "+faststart", str(dest)]
        else:
            cmd += ["-filter_complex", ";".join(graph), "-map", f"[{acc}]", "-an",
                    "-frames:v", str(acc_end), *self._encode_args(final=final_quality), str(dest)]
        run(cmd, timeout=3600, stage="render_sequence")
        self.encoded_with[str(dest)] = self._current_encoder()
        return dest

    def assemble(self, shots: list[Shot], clip_paths: list[Path], audio: Path, dest: Path, audio_seconds: float) -> Path:
        """Renderiza em blocos (grafos menores) e junta tudo com o áudio.

        Os blocos terminam, sempre que dá, num corte seco (sem transição com o shot seguinte). Aí os
        blocos já saem na qualidade final e o passo final só cola os arquivos e põe o áudio, sem
        decodificar e recodificar os 20+ minutos de vídeo de novo."""
        items = [SeqItem(p, s.start_frame, s.end_frame, s.transition_in.kind) for s, p in zip(shots, clip_paths)]
        size = max(2, self.cfg.render.chunk_size)
        if len(items) <= size:
            self.last_assembly = "encode"
            return self.render_sequence(items, dest, audio=audio, total_seconds=audio_seconds)
        blocks, at_cuts = split_blocks(items, size)
        chunks: list[SeqItem] = []
        for n, block in enumerate(blocks):
            path = self.work_dir / f"chunk_{n:03d}.mp4"
            log.info("renderizando bloco", extra={"stage": "render", "chunk": n, "shots": len(block)})
            self.render_sequence(block, path, final_quality=at_cuts)
            chunks.append(SeqItem(path, block[0].start_frame, block[-1].end_frame, block[0].transition))
        same_encoder = len({self.encoded_with.get(str(c.path)) for c in chunks}) == 1
        covers_audio = (chunks[-1].end_frame - chunks[0].start_frame) / self.out.fps >= audio_seconds - 1 / self.out.fps
        if at_cuts and same_encoder and covers_audio:
            try:
                log.info("colando blocos + narração (sem recodificar o vídeo)",
                         extra={"stage": "render", "chunks": len(chunks)})
                self._concat_copy(chunks, audio, dest, audio_seconds)
                self.last_assembly = "copy"
                return dest
            except FFmpegError as exc:
                log.warning("colagem direta falhou; recodificando o vídeo final",
                            extra={"stage": "render", "error": str(exc)[-300:]})
        log.info("juntando blocos + narração", extra={"stage": "render", "chunks": len(chunks)})
        self.last_assembly = "encode"
        return self.render_sequence(chunks, dest, audio=audio, total_seconds=audio_seconds)

    def _concat_copy(self, chunks: list[SeqItem], audio: Path, dest: Path, audio_seconds: float) -> None:
        listing = self.work_dir / "chunks.txt"
        # barra normal: no arquivo do concat a barra invertida do Windows é caractere de escape
        listing.write_text("".join(f"file '{c.path.resolve().as_posix()}'\n" for c in chunks), encoding="utf-8")
        run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", self.out.audio_bitrate, "-t", f"{audio_seconds:.3f}",
             "-movflags", "+faststart", str(dest)], timeout=3600, stage="render_sequence")


def split_blocks(items: list[SeqItem], size: int) -> tuple[list[list[SeqItem]], bool]:
    """Blocos de ~``size`` shots que terminam num corte seco. Devolve os blocos e se TODAS as
    divisões caíram em corte seco (senão o passo final precisa das transições entre blocos)."""
    blocks: list[list[SeqItem]] = [[]]
    at_cuts = True
    hard_cap = size * 4  # grafo grande demais pesa na memória: divide mesmo sem corte seco
    for item in items:
        cur = blocks[-1]
        seamless = bool(cur) and item.start_frame >= cur[-1].end_frame  # sem sobreposição = corte seco
        if cur and ((len(cur) >= size and seamless) or len(cur) >= hard_cap):
            at_cuts = at_cuts and seamless
            blocks.append([])
        blocks[-1].append(item)
    return blocks, at_cuts


def _pipe_frames(cmd: list[str], frames: Iterable[bytes], timeout: float = 600) -> None:
    """Roda o FFmpeg lendo quadros brutos do stdin."""
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        assert proc.stdin is not None
        try:
            for data in frames:
                proc.stdin.write(data)
        except BrokenPipeError:
            pass  # o FFmpeg já tem os quadros que pediu (-frames:v) ou falhou: o código diz
        except OSError as exc:
            if exc.errno not in (22, 32):  # no Windows o cano fechado vem como "Invalid argument" (22)
                proc.kill()
                proc.wait()
                raise
            # o FFmpeg morreu no meio (ex.: disco cheio): o código de saída e o stderr dizem o motivo
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        rc = proc.wait(timeout=timeout)
        if rc != 0:
            err.seek(0)
            tail = "\n".join(err.read().decode(errors="ignore").strip().splitlines()[-12:])
            from .media import _raise_disk_full

            _raise_disk_full(tail)
            raise FFmpegError(f"ffmpeg falhou (código {rc}):\n{tail}")
