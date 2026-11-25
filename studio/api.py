"""O que a tela do nKiev Studio pode pedir ao Python (``window.pywebview.api.<método>``).

Tudo devolve dicionários/listas simples (JSON). Erros esperados viram ``{"erro": "mensagem"}``.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from broll_bot.channels import channel_for_language
from broll_bot.config import load_config
from broll_bot.lang import detect_language
from broll_bot import usage
from broll_bot.looks import LOOKS, job_overrides

from . import canais, chaves, relatorio, seo
from .jobs import DONE, RUNNING, JobQueue, estimate_seconds

PREFS_FILE = "studio.json"
TEMAS = ("grafite", "onix", "ardosia", "salvia", "areia")  # paletas da janela (index.html)
SCRIPT_TYPES = ("Roteiro (*.txt;*.md)", "Todos os arquivos (*.*)")
AUDIO_TYPES = ("Áudio (*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac)", "Todos os arquivos (*.*)")
PHOTO_TYPES = ("Imagem (*.jpg;*.jpeg;*.png;*.webp)",)
LANG_LABELS = {"en": "Inglês", "fr": "Francês", "pt": "Português", "pl": "Polonês", "es": "Espanhol"}

# dialogs(kind, file_types) -> caminho escolhido ou None (a janela do pywebview ou o tkinter)
Dialogs = Callable[[str, tuple[str, ...]], "str | None"]


def _guard(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, OSError) as exc:
            return {"erro": str(exc)}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class Api:
    def __init__(self, base_dir: Path, dialogs: Dialogs | None = None, start_queue: bool = True) -> None:
        self._base = Path(base_dir)
        self._dialogs = dialogs
        self._queue = JobQueue(self._base)
        self._machine: dict[str, Any] | None = None
        self._seo_lock = threading.Lock()
        self._thumbs_url = "/arquivo?p="  # miniaturas do acompanhamento (o app troca pelo endereço local)
        if start_queue:
            self._queue.start()

    # --- utilidades internas (o pywebview só expõe o que não começa com "_") ---
    def _config(self):
        return load_config(self._base / "config.yaml", self._base / ".env")

    def _prefs(self) -> dict[str, Any]:
        try:
            return json.loads((self._base / "projetos" / PREFS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _abs(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self._base / p

    # --- início ---------------------------------------------------------------
    def inicio(self) -> dict[str, Any]:
        return {"canais": canais.list_all(self._base), "estilos": self.estilos(), "legendas": self.legendas(),
                "fila": self.fila(),
                "prefs": self._prefs(), "thumbs_url": self._thumbs_url, "idiomas": canais.LANGUAGES, "moedas": canais.CURRENCIES,
                "nichos": canais.NICHES, "paletas": {k: v["nome"] for k, v in canais.PALETTES.items()},
                "chaves_ok": all(k["filled"] for k in chaves.summary(self._base / ".env")
                                 if k["env"] in ("PEXELS_API_KEY", "PIXABAY_API_KEY"))}

    def legendas(self) -> list[dict[str, Any]]:
        """Estilos de legenda com as prévias (studio/web/legendas/<estilo>.webp/.jpg)."""
        from broll_bot.captions import NAMES, STYLES

        web = Path(__file__).resolve().parent / "web" / "legendas"
        return [{"id": k, "name": NAMES.get(k, k), "preview": f"legendas/{k}.webp" if (web / f"{k}.webp").exists() else "",
                 "still": f"legendas/{k}.jpg" if (web / f"{k}.jpg").exists() else ""} for k in STYLES]

    def estilos(self) -> list[dict[str, Any]]:
        return [{"id": k, "name": v["nome"], "desc": v["descricao"],
                 "removes": (v.get("remover") or {}).get("templates", [])} for k, v in LOOKS.items()]

    # --- roteiro e áudio --------------------------------------------------------
    @_guard
    def escolher_roteiro(self) -> dict[str, Any] | None:
        path = self._dialogs("open", SCRIPT_TYPES) if self._dialogs else None
        if not path:
            return None
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        return {"path": path, "name": Path(path).name, "text": text, **self.analisar_roteiro(text)}

    def analisar_roteiro(self, text: str) -> dict[str, Any]:
        words = len((text or "").split())
        lang = detect_language(text) if words >= 8 else ""
        hit = channel_for_language(self._base, lang) if lang else None
        return {"words": words, "chars": len(text or ""), "minutes": max(1, round(words / 150)) if words else 0,
                "lang": lang, "lang_name": LANG_LABELS.get(lang, ""), "channel": hit.stem if hit else ""}

    @_guard
    def escolher_audio(self) -> dict[str, Any] | None:
        path = self._dialogs("open", AUDIO_TYPES) if self._dialogs else None
        if not path:
            return None
        return self.info_audio(path)

    @_guard
    def info_audio(self, path: str) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            raise ValueError("Arquivo de áudio não encontrado.")
        seconds = None
        try:
            from broll_bot.media import audio_duration

            seconds = round(audio_duration(p), 1)
        except Exception:  # noqa: BLE001 - sem ffprobe ainda dá para seguir
            pass
        return {"path": str(p), "name": p.name, "seconds": seconds,
                "clock": relatorio.clock(seconds) if seconds else "", "mb": round(p.stat().st_size / 1e6, 1)}

    def estimativa(self, audio_seconds: float | None) -> dict[str, Any]:
        if not audio_seconds:
            return {"seconds": None}
        return {"seconds": estimate_seconds(audio_seconds)}

    # --- produzir ----------------------------------------------------------------
    @_guard
    def produzir(self, pedido: dict[str, Any]) -> dict[str, Any]:
        """pedido: title, channel (arquivo), style, script (texto), audio (caminho), settings{...}."""
        script = (pedido.get("script") or "").strip()
        if len(script.split()) < 8:
            raise ValueError("Cole o roteiro (ou escolha o arquivo) antes de produzir.")
        audio = pedido.get("audio") or ""
        if not audio or not Path(audio).exists():
            raise ValueError("Escolha o áudio da narração.")
        chans = {c["file"]: c for c in canais.list_all(self._base)}
        ch = chans.get(pedido.get("channel") or "")
        if ch is None:
            raise ValueError("Escolha o canal.")
        s = pedido.get("settings") or {}
        style = pedido.get("style") or "mix"
        overrides = job_overrides(style, rhythm=s.get("rhythm", "normal"), pace=s.get("pace", "normal"),
                                  statements=s.get("statements", True), phrases=s.get("phrases", True),
                                  music=s.get("music", True), sfx=s.get("sfx", True), review=s.get("review", False),
                                  avatar=s.get("avatar", True), character=s.get("character", True),
                                  music_volume=(s["vol"] / 100) if s.get("vol") is not None else None,
                                  video_format=s.get("format", "horizontal"), captions=s.get("captions", "auto"),
                                  caption_size=s.get("cap_size", "media"),
                                  cut_silence=("on" if s.get("cut_silence") else "off") if "cut_silence" in s else "auto")
        if self._prefs().get("diretor") is False:  # diretor de IA desligado em Configurações: só as regras
            overrides["director"] = {"enabled": False}
        info = self.info_audio(audio)
        job = self._queue.add(title=pedido.get("title") or Path(audio).stem, channel=ch["name"],
                              channel_file=ch["file"], style=style, settings={**s, "style": style},
                              overrides=overrides, script_text=script, audio=str(Path(audio).resolve()),
                              videos_dir=ch["videos_dir"], audio_seconds=info.get("seconds"),
                              ai=self._prefs().get("ia", ""))
        return self._queue.snapshot(job)

    # --- fila --------------------------------------------------------------------
    def fila(self) -> list[dict[str, Any]]:
        with self._queue.lock:
            jobs = list(self._queue.jobs)
        return [self._queue.snapshot(j) for j in jobs]

    def custos(self) -> dict[str, Any]:
        """Totais da tela Concluídos: vídeos prontos, minutos de vídeo e gasto estimado de IA."""
        import time as _time

        with self._queue.lock:
            done = [j for j in self._queue.jobs if j.status == DONE]
        month = _time.strftime("%Y-%m")
        counted = [j for j in done if "usd" in (j.usage or {})]
        by_channel: dict[str, float] = {}
        for j in counted:
            by_channel[j.channel] = round(by_channel.get(j.channel, 0.0) + j.usage["usd"], 4)
        return {
            "videos": len(done),
            "minutes": round(sum((j.usage or {}).get("video_seconds") or j.audio_seconds or 0 for j in done) / 60, 1),
            "usd": round(sum(j.usage["usd"] for j in counted), 4),
            "usd_month": round(sum(j.usage["usd"] for j in counted
                                   if _time.strftime("%Y-%m", _time.localtime(j.finished or j.created)) == month), 4),
            "counted": len(counted), "by_channel": by_channel,
        }

    def cancelar(self, job_id: str) -> bool:
        return self._queue.cancel(job_id)

    def repetir(self, job_id: str) -> bool:
        return self._queue.retry(job_id)

    @_guard
    def reabrir(self, job_id: str) -> dict[str, Any]:
        """Produzir novamente: o roteiro, o áudio, o título e o canal de um vídeo já feito voltam para a tela
        Produzir; o estilo e os ajustes vêm preenchidos, mas dá para trocar antes de mandar. É um vídeo novo
        (pasta e arquivo próprios): o anterior continua em Concluídos."""
        job = self._queue.get(job_id)
        if job is None:
            raise ValueError("Vídeo não encontrado na lista.")
        try:
            script = (self._base / job.script).read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            raise ValueError("O roteiro deste vídeo não está mais na pasta do projeto.") from None
        settings = {k: v for k, v in (job.settings or {}).items() if k != "style"}
        out: dict[str, Any] = {"title": job.title, "channel": job.channel_file, "style": job.style,
                               "settings": settings, "script": script, "scriptInfo": self.analisar_roteiro(script),
                               "audio": None, "aviso": ""}
        if Path(job.audio).exists():
            out["audio"] = self.info_audio(job.audio)
        else:
            out["aviso"] = f"O áudio {Path(job.audio).name} não está mais em {Path(job.audio).parent}. Escolha de novo."
        if job.channel_file not in {c["file"] for c in canais.list_all(self._base)}:
            out["channel"] = ""
            out["aviso"] = (out["aviso"] + " " if out["aviso"] else "") + "O canal deste vídeo não existe mais."
        return out

    def remover(self, job_id: str) -> bool:
        return self._queue.remove(job_id)

    def excluir(self, job_id: str) -> dict[str, Any]:
        """Some da lista e apaga a pasta do projeto e as imagens/vídeos que a produção baixou (o vídeo, se saiu,
        continua na pasta do canal)."""
        freed = self._queue.delete(job_id)
        if freed is None:
            return {"ok": False, "erro": "Não deu para excluir (o vídeo está sendo produzido?)."}
        mb = freed / 1024 ** 2
        return {"ok": True, "liberado": f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"}

    def _report(self, job) -> dict[str, Any] | None:
        try:
            return json.loads((self._base / job.folder / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @_guard
    def detalhes(self, job_id: str) -> dict[str, Any]:
        job = self._queue.get(job_id)
        if job is None:
            raise ValueError("Vídeo não encontrado na fila.")
        out: dict[str, Any] = {"job": self._queue.snapshot(job)}
        report = self._report(job)
        if report is None:
            return out
        caps = relatorio.chapters(report)
        out.update(
            diag=relatorio.diagnose(report, self._config().search.min_reuse_gap_seconds),
            credits=relatorio.credits_text(report),
            chapters=[{"t": t, "name": n} for t, n in caps],
            video={"path": str(self._abs(report.get("output") or job.output)),
                   "duration": relatorio.clock(report.get("video_duration") or 0),
                   "name": Path(report.get("output") or job.output).name},
            report_path=str(self._base / job.folder / "report.json"),
            timings=report.get("timings") or {},
            seo=self._load_seo(job),
        )
        return out

    def _load_seo(self, job) -> dict[str, Any] | None:
        try:
            return json.loads((self._base / job.folder / "seo.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @_guard
    def gerar_seo(self, job_id: str) -> dict[str, Any]:
        job = self._queue.get(job_id)
        if job is None or job.status != DONE:
            raise ValueError("O vídeo ainda não terminou.")
        report = self._report(job) or {}
        caps = relatorio.chapters(report)
        script = (self._base / job.script).read_text(encoding="utf-8", errors="ignore")
        cfg = self._config()
        lang = report.get("language") or "en"
        with self._seo_lock:
            usage.reset()
            try:
                data = seo.generate(cfg, job.title, script, caps, lang, provider=self._prefs().get("ia", ""))
            except Exception as exc:  # noqa: BLE001 - mostra o motivo na tela
                raise ValueError(f"A IA não respondeu: {str(exc)[:300]}") from exc
        data["full"] = seo.full_description(data, caps)
        spent = usage.summary(cfg.costs.prices)
        data["usage"] = spent
        with self._queue.lock:  # a descrição entra no gasto do vídeo
            u = dict(job.usage or {})
            if "usd" in u:
                u["usd"] = round(u["usd"] + spent["usd"], 4)
                steps = dict(u.get("steps") or {})
                steps.update(spent["steps"])
                u["steps"] = steps
            job.usage = u
        self._queue._changed()
        (self._base / job.folder / "seo.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
        return data

    # --- canais ------------------------------------------------------------------
    def canais(self) -> list[dict[str, Any]]:
        return canais.list_all(self._base)

    @_guard
    def criar_canal(self, d: dict[str, Any]) -> dict[str, Any]:
        paper = d.get("paper", True) is not False
        return canais.create(self._base, d.get("name", ""), d.get("language", ""), d.get("currency", ""),
                             d.get("niche", "automotivo"), d.get("palette", "grafite"),
                             no_templates=[] if paper else ["dossier"], no_overlays=[] if paper else ["file"])

    @_guard
    def editar_canal(self, file: str, d: dict[str, Any]) -> dict[str, Any]:
        """Salva as mudanças de um canal; os vídeos dele na lista (Fila e Concluídos) passam a mostrar o nome
        novo e, se a pasta dos vídeos mudou de nome, o caminho novo."""
        current = next((c for c in canais.list_all(self._base) if c["file"] == file), None)
        renaming = current is not None and " ".join(str(d.get("name", "")).split()) != current["name"]
        with self._queue.lock:
            busy = any(j.channel_file == file and j.status == RUNNING for j in self._queue.jobs)
        if renaming and busy:
            raise ValueError("Um vídeo deste canal está sendo produzido agora. Renomeie quando ele terminar.")
        ch = canais.update(self._base, file, d.get("name", ""), d.get("language", ""), d.get("currency", ""),
                           d.get("niche", "automotivo"), d.get("palette", "personalizada"), d.get("paper"),
                           avatar=d.get("avatar"))
        moved = ch.pop("moved", None)
        with self._queue.lock:
            for job in self._queue.jobs:
                if job.channel_file != file:
                    continue
                job.channel = ch["name"]
                if moved and job.output.replace("\\", "/").startswith(moved[0] + "/"):
                    job.output = moved[1] + job.output.replace("\\", "/")[len(moved[0]):]
        self._queue._changed()
        return ch

    @_guard
    def salvar_preset(self, channel: str, name: str, settings: dict[str, Any]) -> dict[str, Any]:
        return canais.save_preset(self._base, channel, name, settings)

    @_guard
    def escolher_imagem_avatar(self, file: str) -> dict[str, Any] | None:
        """Imagem do personagem do canal (ex.: o mecânico já dentro da oficina), que a HeyGen anima. Vai para
        canais/avatar/, para o canal não depender de onde o arquivo estava."""
        import shutil

        path = self._dialogs("open", PHOTO_TYPES) if self._dialogs else None
        if not path:
            return None
        src = Path(path)
        folder = self._base / "canais" / "avatar"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{Path(file).stem or 'canal'}{src.suffix.lower()}"
        for old in folder.glob(f"{dest.stem}.*"):
            if old != dest:
                old.unlink(missing_ok=True)
        shutil.copyfile(src, dest)
        return {"photo": dest.relative_to(self._base).as_posix(), "name": src.name}

    @_guard
    def avatares(self) -> list[dict[str, str]]:
        """Avatares e fotos falantes da sua conta HeyGen (para escolher o de cada canal)."""
        from broll_bot.avatar import HeyGen, HeyGenError

        key = chaves.read_env(self._base / ".env").get("HEYGEN_API_KEY", "")
        if not key:
            raise ValueError("Cole a chave da HeyGen em Configurações primeiro.")
        try:
            return HeyGen(key, timeout=30).avatars()
        except HeyGenError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - sem internet etc.
            raise ValueError(f"Não consegui falar com a HeyGen ({type(exc).__name__}).") from exc

    # --- configurações -----------------------------------------------------------
    def chaves(self) -> list[dict[str, Any]]:
        return chaves.summary(self._base / ".env")

    @_guard
    def salvar_chave(self, env: str, value: str) -> list[dict[str, Any]]:
        chaves.write_env(self._base / ".env", env, value)
        return self.chaves()

    def testar_chave(self, env: str) -> dict[str, Any]:
        values = chaves.read_env(self._base / ".env")
        ok, msg = chaves.test_key(env, values.get(env, ""), env=values)
        return {"ok": ok, "msg": msg}

    @_guard
    def salvar_preferencia(self, key: str, value: Any) -> dict[str, Any]:
        if key not in ("ia", "tema", "som", "fundo", "diretor"):
            raise ValueError("preferência desconhecida")
        if key == "tema" and value not in TEMAS:
            raise ValueError("paleta desconhecida")
        if key in ("som", "fundo", "diretor"):  # som ao passar o mouse (o "fundo" antigo ainda é aceito, mas não aparece mais)
            value = bool(value)
        prefs = {**self._prefs(), key: value}
        path = self._base / "projetos" / PREFS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")
        return prefs

    def maquina(self) -> dict[str, Any]:
        if self._machine is None:
            cfg = self._config()
            try:
                from broll_bot.encoder import NAMES, choose

                enc = NAMES.get(choose(cfg.output.encoder), "Processador")
            except Exception:  # noqa: BLE001 - sem FFmpeg
                enc = "FFmpeg não encontrado"
            self._machine = {
                "encoder": enc,
                "transcription": f"{'Placa de vídeo' if cfg.transcription.device == 'cuda' else 'Processador'}"
                                 f" · modelo {cfg.transcription.model}",
                "videos": str(self._base / "videos"),
                "system": f"{platform.system()} {platform.release()} · {os.cpu_count()} núcleos",
                "ia_config": cfg.analysis.provider,
            }
        return self._machine

    # --- arquivos ----------------------------------------------------------------
    @_guard
    def abrir(self, path: str) -> bool:
        target = self._abs(path)
        if not target.exists():
            raise ValueError("Arquivo não encontrado (foi movido ou apagado?).")
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])
        return True

    @_guard
    def mostrar_na_pasta(self, path: str) -> bool:
        target = self._abs(path)
        if sys.platform.startswith("win") and target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)])
            return True
        return self.abrir(str(target.parent if target.is_file() or not target.exists() else target))

    def copiar(self, text: str) -> bool:
        """Área de transferência pelo Python (a da página nem sempre é liberada dentro do app)."""
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                return True
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
            return True
        except Exception:  # noqa: BLE001
            return False
