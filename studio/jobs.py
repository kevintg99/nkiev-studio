"""Fila de produção do nKiev Studio: um vídeo por vez, cada um num processo do bot (``main.py``).

Rodar o bot num processo separado deixa o app leve (o PyTorch e o FFmpeg ficam no processo do vídeo),
libera a memória entre um vídeo e outro (8 GB de RAM) e permite cancelar no meio.
Cada vídeo tem uma pasta em ``projetos/`` com roteiro, ajustes, andamento, log e relatório.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from broll_bot import progress
from broll_bot.text import normalize

PROJECTS_DIR = "projetos"
QUEUE_FILE = "fila.json"

# peso de cada etapa no percentual geral (transcrição e busca são as mais longas no notebook)
# peso de cada etapa no percentual: tempos medidos num vídeo de 19 min no notebook (busca e revisão das
# imagens ~47%, render ~26%, montagem ~15%, templates ~9%)
WEIGHTS = {"transcricao": 4, "cenas": 3, "busca": 45, "templates": 9, "render": 26, "montagem": 13}
# segundos de produção por segundo de narração (para o andamento das etapas que não contam itens)
SECONDS_PER_AUDIO_SECOND = 4.0
STAGE_NAMES = {"transcricao": "Transcrição", "cenas": "Plano de cenas", "busca": "Busca e revisão",
               "templates": "Templates", "render": "Render", "montagem": "Montagem"}

QUEUED, RUNNING, DONE, FAILED, CANCELED = "fila", "produzindo", "pronto", "erro", "cancelado"


# o que o vídeo baixou e deixou no disco (fica em comum entre os vídeos, por isso a janela de tempo do vídeo)
DOWNLOAD_DIRS = (".cache/media", ".cache/thumbs")
WORK_DIR = ".work"


def job_files(base: Path, job: "Job") -> list[Path]:
    """Imagens e vídeos baixados durante a produção deste vídeo (bancos de imagem e miniaturas) e a pasta de
    trabalho da execução. O avatar e as imagens do personagem (pagos) e o vídeo pronto não entram."""
    if not job.started:
        return []
    t0, t1 = job.started - 60, (job.finished or time.time()) + 60
    out: list[Path] = []
    for rel in DOWNLOAD_DIRS:
        folder = Path(base) / rel
        if not folder.is_dir():
            continue
        for p in folder.rglob("*"):
            try:
                if p.is_file() and t0 <= p.stat().st_mtime <= t1:
                    out.append(p)
            except OSError:
                pass
    work = Path(base) / WORK_DIR
    if work.is_dir():
        for run in work.iterdir():  # "20261006-142701": a execução começou junto com o vídeo
            try:
                began = time.mktime(time.strptime(run.name[:15], "%Y%m%d-%H%M%S"))
            except ValueError:
                continue
            if run.is_dir() and t0 - 60 <= began <= t1:
                out.append(run)
    return out


def free_job_files(base: Path, job: "Job") -> int:
    """Apaga o que ``job_files`` achou; devolve os bytes liberados."""
    freed = 0
    for p in job_files(base, job):
        try:
            if p.is_dir():
                freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p, ignore_errors=True)
            else:
                freed += p.stat().st_size
                p.unlink()
        except OSError:
            pass
    return freed


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(text or "")).strip("-")
    return slug[:limit].rstrip("-") or "video"


@dataclass
class Job:
    id: str
    title: str
    channel: str  # nome do canal ("Jhonny Walt")
    channel_file: str  # arquivo em canais/ ("jhonny_walt"); vazio = config.yaml puro
    style: str
    settings: dict[str, Any]
    folder: str  # pasta do projeto (relativa à base)
    script: str  # roteiro salvo na pasta do projeto
    audio: str  # áudio escolhido pelo usuário (fica onde está)
    output: str  # vídeo final
    status: str = QUEUED
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    error: str = ""
    audio_seconds: float | None = None
    ai: str = ""  # "" = a do config.yaml; "anthropic" | "openai"
    # gasto do vídeo pronto (do report.json): usd, steps {etapa: {...}}, banks {banco: requisições}, video_seconds
    usage: dict[str, Any] = field(default_factory=dict)


class JobQueue:
    def __init__(self, base_dir: Path, python: str | None = None,
                 on_change: Callable[[], None] | None = None) -> None:
        self.base = Path(base_dir)
        self.dir = self.base / PROJECTS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.python = python or sys.executable
        self.on_change = on_change
        self.jobs: list[Job] = []
        self.lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.current: str | None = None
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self._load()

    # --- persistência ------------------------------------------------------
    def _load(self) -> None:
        path = self.dir / QUEUE_FILE
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = []
        names = {f.name for f in Job.__dataclass_fields__.values()}
        self.jobs = [Job(**{k: v for k, v in r.items() if k in names}) for r in rows if isinstance(r, dict)]
        for job in self.jobs:  # o app foi fechado no meio de um vídeo
            if job.status == RUNNING:
                job.status, job.error = FAILED, "Interrompido: o app foi fechado durante a produção."
            if job.status == DONE and not job.usage:  # pronto antes da contagem de gasto: lê o que der do relatório
                job.usage = usage_from_report(self.base / job.folder / "report.json")
        self._save()

    def _save(self) -> None:
        with self.lock:  # a tela e a fila salvam ao mesmo tempo: um de cada vez
            data = json.dumps([asdict(j) for j in self.jobs], ensure_ascii=False, indent=1)
            tmp = self.dir / (QUEUE_FILE + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.dir / QUEUE_FILE)

    def _changed(self) -> None:
        self._save()
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - aviso para a tela nunca derruba a fila
                pass

    # --- fila --------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return next((j for j in self.jobs if j.id == job_id), None)

    def add(self, *, title: str, channel: str, channel_file: str, style: str, settings: dict[str, Any],
            overrides: dict[str, Any], script_text: str, audio: str, videos_dir: str,
            audio_seconds: float | None = None, ai: str = "") -> Job:
        stamp = time.strftime("%Y-%m-%d_%H%M")
        slug = slugify(title)
        folder = self.dir / f"{stamp}_{slug}"
        n = 2
        while folder.exists():
            folder = self.dir / f"{stamp}_{slug}-{n}"
            n += 1
        folder.mkdir(parents=True)
        script = folder / f"{slug}.txt"
        script.write_text(script_text.strip() + "\n", encoding="utf-8")
        (folder / "ajustes.yaml").write_text(
            "# estilo e ajustes deste vídeo (gravado pelo nKiev Studio)\n"
            + yaml.safe_dump(overrides, allow_unicode=True, sort_keys=False), encoding="utf-8")
        out_dir = self.base / videos_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"{stamp}_{slug}.mp4"
        job = Job(id=uuid.uuid4().hex[:10], title=title.strip() or slug, channel=channel, channel_file=channel_file,
                  style=style, settings=settings, folder=self._rel(folder), script=self._rel(script), audio=audio,
                  output=self._rel(output), audio_seconds=audio_seconds, ai=ai)
        with self.lock:
            self.jobs.insert(0, job)
        self._changed()
        self.wake.set()
        return job

    def retry(self, job_id: str) -> bool:
        with self.lock:
            job = self.get(job_id)
            if job is None or job.status in (QUEUED, RUNNING):
                return False
            job.status, job.error, job.started, job.finished = QUEUED, "", None, None
            for name in ("progresso.jsonl", "report.json"):
                (self.base / job.folder / name).unlink(missing_ok=True)
        self._changed()
        self.wake.set()
        return True

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            job = self.get(job_id)
            if job is None or job.status not in (QUEUED, RUNNING):
                return False
            was_running = job.status == RUNNING and self.current == job.id
            job.status, job.finished = CANCELED, time.time()
            proc = self.proc if was_running else None
        if proc is not None:
            _kill_tree(proc)
        self._changed()
        return True

    def remove(self, job_id: str, delete_project: bool = False) -> bool:
        """Tira da lista. ``delete_project`` apaga também a pasta do projeto e o que a produção deixou no
        disco (``delete``); o vídeo final, se existir, nunca é apagado daqui."""
        if delete_project:
            return self.delete(job_id) is not None
        with self.lock:
            job = self.get(job_id)
            if job is None or job.status == RUNNING:
                return False
            self.jobs.remove(job)
        self._changed()
        return True

    def delete(self, job_id: str) -> int | None:
        """Tira da lista e apaga a pasta do projeto (roteiro, ajustes, log, relatório) e o que a produção
        baixou e deixou no disco (``free_job_files``). Devolve os bytes liberados (None: não achou/produzindo)."""
        with self.lock:
            job = self.get(job_id)
            if job is None or job.status == RUNNING:
                return None
            self.jobs.remove(job)
            busy = self.current is not None
        freed = 0
        folder = (self.base / job.folder).resolve()
        if self.dir.resolve() in folder.parents:  # só dentro de projetos/
            freed += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            shutil.rmtree(folder, ignore_errors=True)
        if not busy:  # outro vídeo produzindo agora pode estar usando o cache
            freed += free_job_files(self.base, job)
        self._changed()
        return freed

    def shutdown(self) -> None:
        """O app vai fechar: para o vídeo em produção (senão o bot seguiria sozinho, sem ninguém olhando)."""
        with self.lock:
            proc, job = self.proc, self.get(self.current or "")
            if job is not None:
                job.status, job.finished = FAILED, time.time()
                job.error = "Interrompido: o app foi fechado durante a produção."
        if proc is not None:
            _kill_tree(proc)
        self._save()

    # --- execução ----------------------------------------------------------
    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="fila", daemon=True)
            self.thread.start()

    def _next(self) -> Job | None:
        with self.lock:
            waiting = [j for j in self.jobs if j.status == QUEUED]
            return min(waiting, key=lambda j: j.created) if waiting else None  # o mais antigo primeiro

    def _loop(self) -> None:
        while True:
            job = self._next()
            if job is None:
                self.wake.wait(2.0)
                self.wake.clear()
                continue
            self.run(job)

    def command(self, job: Job) -> list[str]:
        folder = self.base / job.folder
        cmd = [self.python, str(self.base / "main.py"), "--audio", job.audio, "--script", str(self.base / job.script),
               "--out", str(self.base / job.output), "--report", str(folder / "report.json"),
               "--ajustes", str(folder / "ajustes.yaml"), "--progresso", str(folder / "progresso.jsonl")]
        if job.channel_file:
            cmd += ["--canal", job.channel_file]
        if job.ai:
            cmd += ["--ai", job.ai]
        return cmd

    def run(self, job: Job) -> None:
        folder = self.base / job.folder
        (folder / "progresso.jsonl").unlink(missing_ok=True)
        with self.lock:
            if job.status != QUEUED:
                return
            job.status, job.started, job.error = RUNNING, time.time(), ""
            self.current = job.id
        self._changed()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        kwargs: dict[str, Any] = {}
        if os.name == "nt":  # sem janela preta do terminal e com grupo próprio (para cancelar tudo junto)
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            with open(folder / "log.txt", "w", encoding="utf-8") as log:
                proc = subprocess.Popen(self.command(job), cwd=self.base, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=env, **kwargs)
                with self.lock:
                    self.proc = proc
                code = proc.wait()
        except OSError as exc:
            code, job.error = -1, f"Não consegui iniciar o bot: {exc}"
        with self.lock:
            self.proc, self.current = None, None
            job.finished = time.time()
            if job.status in (CANCELED, FAILED):  # cancelado na tela ou app fechando
                pass
            elif code == 0 and (folder / "report.json").exists():
                job.status = DONE
                job.usage = usage_from_report(folder / "report.json")
            else:
                job.status = FAILED
                job.error = job.error or last_error(folder / "log.txt")
        self._changed()

    # --- andamento ---------------------------------------------------------
    def snapshot(self, job: Job) -> dict[str, Any]:
        return {**asdict(job), "progress": job_progress(self.base / job.folder / "progresso.jsonl", job)}

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.base))
        except ValueError:
            return str(path)


def job_progress(path: Path, job: Job | None = None) -> dict[str, Any]:
    """Etapas com estado (feita/agora/a seguir), contagens e percentual geral."""
    rows = progress.read(path)
    order = list(WEIGHTS)
    first: dict[str, float] = {}
    last: dict[str, dict[str, Any]] = {}
    thumbs: list[str] = []
    for r in rows:
        st = r.get("stage")
        first.setdefault(st, r.get("t", 0))
        last[st] = r
        if r.get("thumbs"):
            thumbs = r["thumbs"]
    seen = [s for s in order if s in first]
    finished = "pronto" in first
    current = None if finished or not seen else seen[-1]
    stages, pct = [], 0.0
    for i, st in enumerate(order):
        if st in first and (finished or st != current):
            state = "done"
            nxt = next((first[o] for o in order[i + 1:] + ["pronto"] if o in first), None)
            took = (nxt - first[st]) if nxt else None
            frac = 1.0
        elif st == current:
            state, took = "now", None
            r = last[st]
            if r.get("total"):
                frac = r["done"] / r["total"]
            elif job and job.audio_seconds and job.status == RUNNING:
                # etapa sem contagem (templates, montagem): anda com o tempo, até 95% do esperado
                expected = job.audio_seconds * SECONDS_PER_AUDIO_SECOND * WEIGHTS[st] / 100
                frac = min(0.95, max(0.0, time.time() - first[st]) / max(1.0, expected))
            else:
                frac = 0.3
        else:
            state, took, frac = "next", None, 0.0
        pct += WEIGHTS[st] * frac
        r = last.get(st, {})
        stages.append({"id": st, "name": STAGE_NAMES[st], "state": state, "seconds": took,
                       "done": r.get("done"), "total": r.get("total")})
    pct = 100.0 if finished else round(min(99.0, pct), 1)
    start = job.started if job and job.started else (rows[0]["t"] if rows else None)
    elapsed = (time.time() - start) if start and job and job.status == "produzindo" else (
        (job.finished - job.started) if job and job.started and job.finished else None)
    # sem previsão de término: com a busca e a revisão variando tanto de vídeo para vídeo, ela só ia subindo
    return {"stages": stages, "percent": pct, "elapsed": elapsed, "thumbs": thumbs,
            "current": STAGE_NAMES.get(current or "", "")}


def usage_from_report(path: Path) -> dict[str, Any]:
    """Gasto do vídeo para a tela Concluídos (vídeos de antes da contagem não têm ``usage``)."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, Any] = {"video_seconds": report.get("video_duration"),
                           "banks": (report.get("totals") or {}).get("api_requests") or {}}
    if isinstance(report.get("usage"), dict):
        u = report["usage"]
        out.update(usd=u.get("usd", 0.0), steps=u.get("steps", {}), calls=u.get("calls", 0),
                   unpriced=u.get("unpriced", []), services_usd=u.get("services_usd", 0.0))
    return out


def estimate_seconds(audio_seconds: float) -> float:
    """Tempo médio medido no notebook (i5 + QuickSync): ~4 min de produção por minuto de narração."""
    return audio_seconds * SECONDS_PER_AUDIO_SECOND


def last_error(log_path: Path) -> str:
    """A mensagem de erro mais útil do log do bot (o "✖ ..." da linha de comando ou o último ERROR)."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return "O bot parou sem deixar log."
    for i, line in enumerate(lines):
        if line.startswith("✖"):
            return " ".join(x.strip() for x in lines[i:i + 3])[:500]
    errors = [ln for ln in lines if " ERROR " in ln]
    if errors:
        return re.sub(r"^\S+ ERROR\s+\[[^\]]*\]\s*", "", errors[-1])[:500]
    tail = [ln for ln in lines if ln.strip()][-1:]
    return (tail[0][:500] if tail else "O bot parou sem dizer o motivo.")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Para o bot e o FFmpeg que ele abriu."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass
