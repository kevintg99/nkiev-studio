"""Perfis de canal: cada canal tem um arquivo em ``canais/`` com o que é só dele.

Exemplo (``canais/jhonny_walt.yaml``)::

    canal:
      nome: Jhonny Walt
      idioma: en                 # o bot usa este perfil sozinho quando o roteiro é em inglês
      moeda: "£"
      pasta_videos: videos/Jhonny Walt
      sem_templates: [dossier]   # o que este canal nunca usa
      sem_overlays: [file]
      paleta: {fundo: [22, 23, 26], destaque: [214, 40, 40], ...}
    templates:                   # e qualquer seção do config.yaml que este canal muda
      rhythm_gap: 11

O ``config.yaml`` continua sendo a base (chaves das APIs, buscas, render...); o perfil só muda o
que ele define. Uso: ``--canal "Jhonny Walt"`` ou, sem ``--canal``, o canal do idioma do roteiro.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import AppConfig, ProviderConfig, _build
from .log import get_logger
from .text import normalize

log = get_logger("channels")

CHANNELS_DIR = "canais"
# templates da família papel (estilo Dossiê do app): somem junto quando o canal não usa o dossiê
PAPER_FAMILY = ("paperfloat", "paperpair", "papertimeline", "papercompare", "paperstatement", "paperrank")
# chaves do bloco "canal" (em português, para editar à mão) -> campos do ChannelConfig
_KEYS = {"nome": "name", "idioma": "language", "moeda": "currency", "paleta": "palette",
         "sem_templates": "no_templates", "sem_overlays": "no_overlays", "pasta_videos": "videos_dir"}


def _slug(text: str) -> str:
    return "_".join(normalize(text).replace("-", " ").split())


def channel_files(base_dir: Path) -> list[Path]:
    folder = base_dir / CHANNELS_DIR
    return sorted(p for p in folder.glob("*.yaml") if not p.name.startswith("_")) if folder.is_dir() else []


def _read(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def list_channels(base_dir: Path) -> list[tuple[str, str, str]]:
    """(arquivo, nome, idioma) de cada canal — para o terminal e, no futuro, para os botões."""
    out = []
    for p in channel_files(base_dir):
        info = _read(p).get("canal") or {}
        out.append((p.stem, str(info.get("nome") or p.stem), str(info.get("idioma") or "")))
    return out


def find_channel(base_dir: Path, name: str) -> Path | None:
    """Pelo nome do arquivo ("jhonny_walt") ou pelo nome do canal ("Jhonny Walt"), sem ligar para
    maiúsculas/acentos."""
    want = _slug(name)
    for path, (stem, title, _) in zip(channel_files(base_dir), list_channels(base_dir)):
        if want in (_slug(stem), _slug(title)):
            return path
    return None


def channel_for_language(base_dir: Path, lang: str) -> Path | None:
    """O canal do idioma do roteiro. Com mais de um canal no mesmo idioma, o marcado com
    ``padrao: true``; sem marcação, nenhum (o bot avisa e usa o config.yaml puro)."""
    lang = (lang or "").split("-")[0].lower()
    hits = [(p, _read(p).get("canal") or {}) for p in channel_files(base_dir)]
    hits = [(p, c) for p, c in hits if str(c.get("idioma") or "").split("-")[0].lower() == lang]
    if len(hits) == 1:
        return hits[0][0]
    default = [p for p, c in hits if c.get("padrao")]
    if len(default) == 1:
        return default[0]
    if hits:
        log.warning("mais de um canal neste idioma: use --canal (ou marque um com 'padrao: true')",
                    extra={"stage": "input", "idioma": lang, "canais": [p.stem for p, _ in hits]})
    return None


def apply_channel(cfg: AppConfig, path: Path) -> AppConfig:
    """Aplica o perfil por cima do config.yaml: bloco "canal" + as seções que ele redefinir."""
    raw = _read(path)
    info = raw.pop("canal", None) or {}
    providers_raw = raw.pop("providers", None) or {}
    _build(AppConfig, raw, cfg)
    for name, pdata in providers_raw.items():
        cfg.providers[name] = _build(ProviderConfig, pdata, cfg.providers.get(name, ProviderConfig()))
    ch = cfg.channel
    for key, attr in _KEYS.items():
        if key in info:
            setattr(ch, attr, info[key])
    ch.name = ch.name or path.stem
    ch.slug = path.stem
    if ch.language:
        cfg.analysis.script_language = ch.language
        cfg.transcription.language = ch.language
    no_t, no_o = set(ch.no_templates), set(ch.no_overlays)
    if "dossier" in no_t:  # canal sem o visual de papel: nenhuma peça da família papel
        no_t |= set(PAPER_FAMILY)
        no_o |= {"papertag", "paperyears"}
    if "file" in no_o:
        no_o |= {"papertag", "paperyears"}
    cfg.templates.styles = [s for s in cfg.templates.styles if s not in no_t]
    cfg.overlays.kinds = [k for k in cfg.overlays.kinds if k not in no_o]
    from .config import apply_format

    apply_format(cfg)  # canal de Shorts (output.format: vertical no canal)
    log.info("canal", extra={"stage": "input", "canal": ch.name, "idioma": ch.language or "auto",
                             "moeda": ch.currency or "da fala", "sem": [*ch.no_templates, *ch.no_overlays]})
    return cfg


def apply_overrides(cfg: AppConfig, path: Path) -> AppConfig:
    """Ajustes de um vídeo (``--ajustes``, gravado pelo app): seções do config por cima do canal;
    ``liberar: {templates, overlays}`` devolve o que o canal não usa quando o estilo escolhido para o vídeo
    é justamente esse (Dossiê num canal "sem papel": quem escolhe o estilo é você, vídeo a vídeo) e
    ``remover: {templates, overlays}`` tira estilos."""
    raw = _read(path)
    remove = raw.pop("remover", None) or {}
    free = raw.pop("liberar", None) or {}
    for key in ("canal", "providers"):  # chaves e perfil do canal não mudam por vídeo
        raw.pop(key, None)
    _build(AppConfig, raw, cfg)
    cfg.templates.styles += [s for s in free.get("templates") or [] if s not in cfg.templates.styles]
    cfg.overlays.kinds += [k for k in free.get("overlays") or [] if k not in cfg.overlays.kinds]
    cfg.templates.styles = [s for s in cfg.templates.styles if s not in set(remove.get("templates") or [])]
    cfg.overlays.kinds = [k for k in cfg.overlays.kinds if k not in set(remove.get("overlays") or [])]
    from .config import apply_format

    apply_format(cfg)  # o vídeo pode ter vindo como vertical (Shorts) do app
    log.info("ajustes do vídeo", extra={"stage": "input", "arquivo": path.name,
                                        "sem": [*(remove.get("templates") or []), *(remove.get("overlays") or [])],
                                        "liberado": [*(free.get("templates") or []), *(free.get("overlays") or [])]})
    return cfg
