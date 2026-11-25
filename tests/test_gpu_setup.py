from studio import gpu


def test_cpu_torch_on_nvidia_pc_is_swapped_for_cuda_build():
    """PC com RTX e torch "+cpu": o CLIP rodava no processador. Troca pela mesma versão com CUDA."""
    assert gpu.torch_plan("2.15.0+cpu", "0.29.2+cpu", nvidia=True) == ["torch==2.15.0+cu132",
                                                                       "torchvision==0.29.2+cu132"]
    assert gpu.torch_plan("2.15.0", "0.29.2", nvidia=True) == ["torch==2.15.0+cu132", "torchvision==0.29.2+cu132"]


def test_nothing_changes_without_nvidia_or_when_cuda_is_already_there():
    assert gpu.torch_plan("2.15.0+cpu", "0.29.2+cpu", nvidia=False) == []  # notebook sem placa
    assert gpu.torch_plan("2.15.0+cu132", "0.29.2+cu132", nvidia=True) == []
    assert gpu.torch_plan("", "", nvidia=True) == []


def test_whisper_libs_only_when_missing():
    assert gpu.whisper_plan(True, {"nvidia-cublas-cu12": "", "nvidia-cudnn-cu12": ""}) == gpu.WHISPER_LIBS
    assert gpu.whisper_plan(True, {"nvidia-cublas-cu12": "12.9", "nvidia-cudnn-cu12": "9.1"}) == []
    assert gpu.whisper_plan(False, {}) == []


def test_blocked_by_windows_is_not_retried():
    """O Controle Inteligente de Aplicativos do Windows bloqueou o torch com CUDA: depois de voltar para o da
    CPU, as próximas atualizações não baixam os 3 GB de novo."""
    assert gpu.torch_plan("2.15.0+cpu", "0.29.2+cpu", nvidia=True, blocked=True) == []


def test_blocked_torch_is_rolled_back_and_remembered(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "BLOCKED", tmp_path / "torch_cuda_bloqueado.txt")
    monkeypatch.setattr(gpu, "has_nvidia", lambda: True)
    monkeypatch.setattr(gpu, "_version", lambda pkg: {"torch": "2.15.0", "torchvision": "0.29.2"}.get(pkg, "9"))
    monkeypatch.setattr(gpu, "torch_opens", lambda: False)
    monkeypatch.setattr(gpu.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    gpu.main()
    assert any("torch==2.15.0+cu132" in c for c in calls)
    assert any("torch==2.15.0" in c and "torchvision==0.29.2" in c for c in calls)  # voltou para o da CPU
    assert gpu.BLOCKED.exists()
