"""Liga a placa de vídeo NVIDIA no bot (instalar_app.bat e atualizar.bat).

O ``pip install -r requirements.txt`` instala o torch só para CPU: o CLIP (que pontua as miniaturas na busca)
rodava no processador mesmo num PC com RTX. E o faster-whisper achava a placa, mas não as bibliotecas da
NVIDIA (cublas64_12.dll), e a transcrição falhava. Aqui: com placa NVIDIA, troca o torch pela versão com CUDA
e instala as bibliotecas que o whisper procura. Sem placa (notebook), não faz nada.

    python -m studio.gpu
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CUDA_INDEX = "cu132"  # torch com CUDA 13.2: roda nas RTX 50 (Blackwell) e nas anteriores
# o Windows (Controle Inteligente de Aplicativos) pode bloquear as DLLs do torch com CUDA ("Uma política de
# Controle de Aplicativo bloqueou este arquivo"): o torch nem abre. Aí volta para o da CPU e anota aqui, para
# não baixar 3 GB de novo a cada atualização
BLOCKED = BASE / ".cache" / "torch_cuda_bloqueado.txt"
WHISPER_LIBS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10"]  # o ctranslate2 usa CUDA 12 + cuDNN 9


def has_nvidia() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-L"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _version(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return ""


def torch_plan(torch_version: str, vision_version: str, nvidia: bool, blocked: bool = False) -> list[str]:
    """Pacotes do torch com CUDA para instalar (vazio = já está certo, não tem placa ou o Windows bloqueia)."""
    if not nvidia or blocked or not torch_version or "+cu" in torch_version:
        return []
    pkgs = [f"torch=={torch_version.split('+')[0]}+{CUDA_INDEX}"]
    if vision_version:
        pkgs.append(f"torchvision=={vision_version.split('+')[0]}+{CUDA_INDEX}")
    return pkgs


def torch_opens() -> bool:
    """O torch instalado abre? (num processo novo: o que já está carregado aqui é o antigo)"""
    r = subprocess.run([sys.executable, "-c", "import torch; torch.cuda.is_available()"], capture_output=True)
    return r.returncode == 0


def whisper_plan(nvidia: bool, installed: dict[str, str]) -> list[str]:
    if not nvidia:
        return []
    return [spec for spec in WHISPER_LIBS if not installed.get(spec.split(">")[0].split("=")[0])]


def main() -> int:
    nvidia = has_nvidia()
    if not nvidia:
        return 0
    pip = [sys.executable, "-m", "pip", "install", "-q"]
    before = _version("torch"), _version("torchvision")
    pkgs = torch_plan(*before, nvidia, blocked=BLOCKED.exists())
    if pkgs:
        print(" Placa NVIDIA encontrada: instalando o torch com CUDA (uns 3 GB, so na primeira vez)...")
        r = subprocess.run(pip + ["--force-reinstall", "--no-deps", *pkgs,
                                  "--index-url", f"https://download.pytorch.org/whl/{CUDA_INDEX}"])
        if r.returncode != 0:  # versão sem build com CUDA: segue na CPU, nada quebra
            print(" Nao deu para instalar o torch com CUDA; o bot segue usando o processador.")
        elif not torch_opens():
            print(" O Windows bloqueou o torch com CUDA (Controle Inteligente de Aplicativos). Voltando para a")
            print(" versao do processador: o bot funciona igual, so o CLIP fica na CPU.")
            subprocess.run(pip + ["--force-reinstall", "--no-deps",
                                  *[f"{n}=={v.split('+')[0]}" for n, v in zip(("torch", "torchvision"), before) if v]])
            BLOCKED.parent.mkdir(parents=True, exist_ok=True)
            BLOCKED.write_text("O Windows bloqueou as DLLs do torch com CUDA. Apague este arquivo para tentar de novo.\n",
                               encoding="utf-8")
    libs = whisper_plan(nvidia, {name: _version(name) for name in ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")})
    if libs:
        print(" Instalando as bibliotecas da NVIDIA para a transcricao...")
        subprocess.run(pip + libs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
