"""Progresso da produção para o app (nKiev Studio): uma linha JSON por novidade num arquivo.

O pipeline chama ``step("render", done=12, total=300)``; sem ``set_file`` não faz nada (linha de comando).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

# etapas na ordem em que aparecem no app
STAGES = ["transcricao", "cenas", "busca", "templates", "render", "montagem"]

_path: Path | None = None
_lock = threading.Lock()


def set_file(path: Path | str | None) -> None:
    global _path
    _path = Path(path) if path else None
    if _path is not None:
        _path.parent.mkdir(parents=True, exist_ok=True)


def step(stage: str, done: int | None = None, total: int | None = None, **info: Any) -> None:
    if _path is None:
        return
    row: dict[str, Any] = {"t": round(time.time(), 2), "stage": stage}
    if total:
        row.update(done=done or 0, total=total)
    row.update(info)
    with _lock, open(_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read(path: Path | str) -> list[dict[str, Any]]:
    """Linhas já escritas (a última pode estar pela metade enquanto o bot escreve)."""
    rows = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
