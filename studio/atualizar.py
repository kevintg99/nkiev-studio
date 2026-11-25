"""Antes do ``git pull`` (atualizar.bat): se o config.yaml foi mexido à mão, guarda uma cópia, leva o que é
pessoal (navegador logado no YouTube) para o .env e volta o arquivo ao original, para o pull não travar.

    python -m studio.atualizar
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=BASE, capture_output=True, text=True)


def migrate(base: Path = BASE) -> list[str]:
    notes: list[str] = []
    cfg = base / "config.yaml"
    if _git("diff", "--quiet", "--", "config.yaml").returncode == 0:
        return notes
    backup = base / "config.yaml.antigo"
    shutil.copyfile(cfg, backup)
    notes.append("Seu config.yaml antigo ficou em config.yaml.antigo.")
    text = backup.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^\s*cookies_from_browser:\s*['\"]?([A-Za-z]+)", text, re.M)
    if m:
        env = base / ".env"
        current = env.read_text(encoding="utf-8") if env.exists() else ""
        if not re.search(r"^YOUTUBE_COOKIES_FROM_BROWSER=\w", current, re.M):
            with open(env, "a", encoding="utf-8") as fh:
                fh.write(("" if current.endswith("\n") or not current else "\n")
                         + f"YOUTUBE_COOKIES_FROM_BROWSER={m.group(1).lower()}\n")
            notes.append(f"O navegador do YouTube ({m.group(1).lower()}) foi para o .env (app: Configurações).")
    _git("checkout", "--", "config.yaml")
    notes.append("Ajustes pessoais agora vão no config.local.yaml (o git pull não mexe nele).")
    return notes


if __name__ == "__main__":
    for line in migrate():
        print("  " + line)
