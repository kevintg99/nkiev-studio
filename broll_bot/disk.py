"""Espaço em disco: o render de um vídeo de 20 min passa de alguns GB em trechos temporários.

* ``clean_old_work``: apaga os trechos de render, a narração mixada e os cortes do avatar que execuções
  anteriores deixaram (vídeo que deu erro, foi cancelado ou o app foi fechado no meio). Os logs ficam.
* ``prune_media``: o cache de imagens/vídeos baixados não passa de ``max_gb`` (apaga os usados há mais tempo).
* ``ensure_space``: antes de começar, confere se cabe; se não, limpa o que dá e, se ainda faltar, para com
  uma mensagem clara (em vez de falhar no meio do render com "Invalid argument").
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .log import get_logger
from .media import FFmpegError

log = get_logger("disk")

GB = 1024 ** 3


class DiskFullError(FFmpegError):
    """Acabou o espaço em disco: não adianta tentar de novo nem trocar de encoder."""


def is_disk_full(text: str) -> bool:
    low = (text or "").lower()
    return "no space left on device" in low or "error code: -28" in low or "espaço insuficiente" in low


def free_gb(path: Path) -> float:
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free / GB


def _size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


# o que sobra numa execução e não serve depois do vídeo pronto (o log ``pipeline.jsonl`` fica)
LEFTOVERS = ("render", "avatar", "narracao_mix.wav", "personagem.jpg")


def drop_run_leftovers(run: Path) -> int:
    """Apaga de ``<.work>/<execução>`` os trechos de render, a narração mixada e os cortes do avatar; devolve
    os bytes liberados."""
    freed = 0
    for name in LEFTOVERS:
        p = Path(run) / name
        try:
            if p.is_dir():
                freed += _size(p)
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file():
                freed += p.stat().st_size
                p.unlink()
        except OSError:
            pass
    return freed


def clean_old_work(work_root: Path, keep: Path | None = None) -> float:
    """Apaga as sobras (``LEFTOVERS``) de execuções anteriores; devolve quantos GB liberou."""
    freed = 0
    if not Path(work_root).is_dir():
        return 0.0
    for run in Path(work_root).iterdir():
        if not run.is_dir() or (keep is not None and run.resolve() == Path(keep).resolve()):
            continue
        freed += drop_run_leftovers(run)
    if freed:
        log.info("trechos de render antigos apagados", extra={"stage": "disk", "liberado_gb": round(freed / GB, 2)})
    return freed / GB


def prune_media(media_dir: Path, max_gb: float) -> float:
    """Mantém o cache de mídias baixadas abaixo de ``max_gb``: sai primeiro o que foi usado há mais tempo."""
    media_dir = Path(media_dir)
    if max_gb <= 0 or not media_dir.is_dir():
        return 0.0
    files = []
    for p in media_dir.rglob("*"):
        try:
            if p.is_file():
                st = p.stat()
                files.append((max(st.st_atime, st.st_mtime), st.st_size, p))
        except OSError:
            pass
    total = sum(f[1] for f in files)
    limit = max_gb * GB
    freed = 0
    for _, size, p in sorted(files):
        if total - freed <= limit:
            break
        try:
            p.unlink()
            freed += size
        except OSError:
            pass
    if freed:
        log.info("cache de mídias reduzido", extra={"stage": "disk", "liberado_gb": round(freed / GB, 2)})
    return freed / GB


def needed_gb(audio_seconds: float) -> float:
    """Espaço para produzir: trechos temporários (~0,15 GB por minuto de narração), o vídeo final e folga."""
    return round(1.5 + 0.2 * audio_seconds / 60, 1)


def ensure_space(work_dir: Path, audio_seconds: float, media_dir: Path, max_media_gb: float) -> None:
    need = needed_gb(audio_seconds)
    if free_gb(work_dir) >= need:
        return
    prune_media(media_dir, min(max_media_gb, 2.0))  # faltando espaço, o cache encolhe mais
    free = free_gb(work_dir)
    if free < need:
        raise DiskFullError(
            f"Espaço em disco insuficiente: {free:.1f} GB livres, este vídeo precisa de uns {need:.0f} GB. "
            "Libere espaço no disco (Lixeira, Downloads, vídeos antigos em videos/) e mande de novo.")
