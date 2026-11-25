"""Canais no app: lista com cores, criação de canal novo e presets de ajustes por canal.

Os perfis continuam em ``canais/<arquivo>.yaml`` (os mesmos da linha de comando). Os presets ficam ao
lado, em ``canais/<arquivo>.presets.json``, para o app nunca reescrever o YAML que você editou à mão.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from broll_bot.channels import CHANNELS_DIR, _slug, channel_files
from broll_bot.motion.gfx import DEFAULT_PALETTE

LANGUAGES = {"en": "Inglês", "fr": "Francês", "pt": "Português", "pl": "Polonês", "es": "Espanhol"}
CURRENCIES = ["£", "€", "$", "C$", "R$", "zł"]
NICHES = {"automotivo": "Automotivo", "variado": "Variado / Top 10"}

PALETTES: dict[str, dict[str, Any]] = {
    "grafite": {"nome": "Grafite + roxo", "paleta": {
        "fundo": [23, 21, 31], "grade": [62, 54, 94], "blueprint": [10, 18, 48], "grade_blueprint": [70, 80, 190],
        "texto": [240, 240, 245], "destaque": [139, 92, 246], "contorno": [0, 0, 0], "creme": [236, 232, 224],
        "veu": [20, 18, 28]}},
    "marinho": {"nome": "Marinho + amarelo", "paleta": {}},  # a identidade original (Bernard Chevalier)
    "cinza": {"nome": "Cinza + vermelho", "paleta": {
        "fundo": [24, 25, 28], "grade": [58, 62, 70], "blueprint": [8, 20, 52], "grade_blueprint": [40, 82, 185],
        "texto": [240, 240, 240], "destaque": [214, 36, 36], "contorno": [0, 0, 0], "creme": [236, 232, 224],
        "veu": [18, 18, 20]}},
    "preto": {"nome": "Preto + verde", "paleta": {
        "fundo": [11, 11, 11], "grade": [50, 56, 52], "blueprint": [8, 20, 52], "grade_blueprint": [40, 82, 185],
        "texto": [240, 240, 240], "destaque": [34, 197, 94], "contorno": [0, 0, 0], "creme": [236, 232, 224],
        "veu": [12, 12, 12]}},
}

# nicho variado: o bot não procura "cara de carro" nas imagens nem põe card de consumo
VARIADO = {"analysis": {"niche": "variado (listas top 10, curiosidades, lugares, pessoas, objetos)"},
           "ranking": {"visual_checks": False}}


def _hex(rgb: Any) -> str:
    r, g, b = (list(rgb) + [0, 0, 0])[:3]
    return f"#{int(r):02X}{int(g):02X}{int(b):02X}"


def _read(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def describe(path: Path) -> dict[str, Any]:
    raw = _read(path)
    info = raw.get("canal") or {}
    pal = {**DEFAULT_PALETTE, **{k: v for k, v in (info.get("paleta") or {}).items() if k in DEFAULT_PALETTE}}
    name = str(info.get("nome") or path.stem)
    lang = str(info.get("idioma") or "")
    niche = str(info.get("nicho") or "automotivo")
    blocked = [*(info.get("sem_templates") or []), *(info.get("sem_overlays") or [])]
    return {
        "file": path.stem, "name": name, "language": lang, "language_name": LANGUAGES.get(lang, "Automático"),
        "currency": str(info.get("moeda") or ""), "niche": niche, "niche_name": NICHES.get(niche, niche),
        "videos_dir": str(info.get("pasta_videos") or f"videos/{name}"),
        "colors": [_hex(pal["fundo"]), _hex(pal["grade_blueprint"]), _hex(pal["destaque"]), _hex(pal["creme"])],
        "accent": _hex(pal["destaque"]), "bg": _hex(pal["fundo"]),
        "initials": "".join(w[0] for w in name.split() if w[0].isalpha())[:2].upper() or name[:2].upper(),
        "no_templates": list(info.get("sem_templates") or []), "no_overlays": list(info.get("sem_overlays") or []),
        "blocked": blocked, "presets": load_presets(path.parent, path.stem),
        "palette": palette_id(info.get("paleta") or {}),
        "avatar": avatar_info(raw.get("avatar") or {}),
        "paper": "dossier" not in (info.get("sem_templates") or []) and "file" not in (info.get("sem_overlays") or []),
    }


def avatar_info(av: dict[str, Any]) -> dict[str, Any]:
    """O avatar da HeyGen do canal (seção ``avatar:`` do YAML)."""
    return {"id": str(av.get("avatar_id") or ""), "kind": str(av.get("kind") or "avatar"),
            "layout": str(av.get("layout") or "full"), "photo": str(av.get("photo") or ""),
            "engine": str(av.get("engine") or "avatar_iv"),
            "position": str(av.get("position") or "right"), "name": str(av.get("name") or ""),
            "preview": str(av.get("preview") or "")}


def palette_id(paleta: dict[str, Any]) -> str:
    """Qual das paletas do app o canal usa ("personalizada" se as cores foram mudadas à mão)."""
    if not paleta:
        return "marinho"
    for key, p in PALETTES.items():
        if p["paleta"] and all(list(paleta.get(k) or []) == list(v) for k, v in p["paleta"].items()):
            return key
    return "personalizada"


def list_all(base_dir: Path) -> list[dict[str, Any]]:
    return [describe(p) for p in channel_files(base_dir)]


def create(base_dir: Path, name: str, language: str, currency: str, niche: str = "automotivo",
           palette: str = "grafite", no_templates: list[str] | None = None,
           no_overlays: list[str] | None = None) -> dict[str, Any]:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Dê um nome ao canal.")
    if language not in LANGUAGES:
        raise ValueError("Escolha o idioma do canal.")
    stem = _slug(name)
    folder = base_dir / CHANNELS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    channels = list_all(base_dir)
    if any(_slug(c["name"]) == stem for c in channels):
        raise ValueError(f"Já existe um canal chamado {name}.")
    # o arquivo pode ter o nome antigo de outro canal (renomeado no app): o novo ganha outro arquivo
    files = {c["file"] for c in channels} | {f.stem for f in folder.glob("*.yaml")}
    base, k = stem, 2
    while stem in files:
        stem, k = f"{base}_{k}", k + 1
    no_overlays = list(no_overlays or [])
    if niche == "variado" and "fuel" not in no_overlays:
        no_overlays.append("fuel")
    canal = {"nome": name, "idioma": language, "moeda": currency, "nicho": niche,
             "pasta_videos": f"videos/{name}", "sem_templates": list(no_templates or []),
             "sem_overlays": no_overlays, "paleta": PALETTES.get(palette, PALETTES["grafite"])["paleta"]}
    doc: dict[str, Any] = {"canal": canal}
    if niche == "variado":
        doc.update(VARIADO)
    text = (f"# Canal {name} — criado no nKiev Studio. Pode editar à mão (veja canais/LEIA-ME.md).\n"
            + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=None))
    path = folder / f"{stem}.yaml"
    path.write_text(text, encoding="utf-8")
    videos = base_dir / canal["pasta_videos"]
    videos.mkdir(parents=True, exist_ok=True)
    return describe(path)


def update(base_dir: Path, stem: str, name: str, language: str, currency: str, niche: str = "automotivo",
           palette: str = "personalizada", paper: bool | None = None,
           avatar: dict[str, Any] | None = None) -> dict[str, Any]:
    """Edita um canal que já existe (nome, idioma, moeda, assunto, cores, visual de papel). O arquivo continua
    o mesmo (os vídeos feitos e os presets apontam para ele); o resto do YAML (seções de ajuste) fica como
    está. Se a pasta dos vídeos era a padrão ("videos/<nome>"), ela acompanha o nome novo.
    Devolve o canal e ``moved`` = (pasta antiga, pasta nova) quando a pasta foi renomeada."""
    path = base_dir / CHANNELS_DIR / f"{stem}.yaml"
    if not path.exists():
        raise ValueError("Canal não encontrado.")
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Dê um nome ao canal.")
    if language not in LANGUAGES:
        raise ValueError("Escolha o idioma do canal.")
    others = [c for c in list_all(base_dir) if c["file"] != stem]
    if any(_slug(c["name"]) == _slug(name) for c in others):
        raise ValueError(f"Já existe um canal chamado {name}.")
    doc = _read(path)
    canal = dict(doc.get("canal") or {})
    old_name = str(canal.get("nome") or stem)
    canal.update({"nome": name, "idioma": language, "moeda": currency, "nicho": niche})
    if palette in PALETTES:
        canal["paleta"] = PALETTES[palette]["paleta"]
    no_t = [x for x in (canal.get("sem_templates") or [])]
    no_o = [x for x in (canal.get("sem_overlays") or [])]
    if paper is not None:  # visual de papel: dossiê e a ficha com fita
        no_t = [x for x in no_t if x != "dossier"] + ([] if paper else ["dossier"])
        no_o = [x for x in no_o if x != "file"] + ([] if paper else ["file"])
    no_o = [x for x in no_o if x != "fuel"] + (["fuel"] if niche == "variado" else [])
    canal["sem_templates"], canal["sem_overlays"] = no_t, no_o
    moved = None
    old_dir = str(canal.get("pasta_videos") or f"videos/{old_name}")
    if name != old_name and old_dir == f"videos/{old_name}":
        new_dir = f"videos/{name}"
        src, dst = base_dir / old_dir, base_dir / new_dir
        if src.is_dir() and not dst.exists():
            src.rename(dst)
            moved = (old_dir, new_dir)
        canal["pasta_videos"] = new_dir
    doc["canal"] = canal
    if avatar is not None:  # avatar da HeyGen: sem imagem do personagem e sem ID = canal sem avatar
        av = {k: v for k, v in (doc.get("avatar") or {}).items() if k != "background"}
        aid = str(avatar.get("id") or "").strip()
        photo = str(avatar.get("photo") or "").strip().replace("\\", "/")
        # o ID que o bot guardou ao enviar a imagem é daquela imagem: trocou a imagem, ele sai junto
        if aid and aid == av.get("avatar_id") and av.get("id_photo") and av.get("id_photo") != photo:
            aid = ""
        if not aid or aid != av.get("avatar_id"):
            av.pop("id_photo", None)
        if aid or photo:
            av.update({"avatar_id": aid, "kind": "photo" if avatar.get("kind") == "photo" else "avatar",
                       "layout": "corner" if avatar.get("layout") == "corner" else "full", "photo": photo,
                       "engine": "standard" if avatar.get("engine") == "standard" else "avatar_iv",
                       "position": "left" if avatar.get("position") == "left" else "right",
                       "name": str(avatar.get("name") or ""), "preview": str(avatar.get("preview") or "")})
            doc["avatar"] = av
        else:
            doc.pop("avatar", None)
    if niche == "variado":
        doc.update(VARIADO)
    else:  # voltou a ser automotivo: tira o que o "variado" tinha posto
        if (doc.get("analysis") or {}).get("niche") == VARIADO["analysis"]["niche"]:
            doc["analysis"] = {k: v for k, v in doc["analysis"].items() if k != "niche"}
            if not doc["analysis"]:
                del doc["analysis"]
        if (doc.get("ranking") or {}).get("visual_checks") is False:
            doc["ranking"] = {k: v for k, v in doc["ranking"].items() if k != "visual_checks"}
            if not doc["ranking"]:
                del doc["ranking"]
    text = (f"# Canal {name} — editado no nKiev Studio. Pode editar à mão (veja canais/LEIA-ME.md).\n"
            + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=None))
    path.write_text(text, encoding="utf-8")
    (base_dir / canal["pasta_videos"]).mkdir(parents=True, exist_ok=True)
    return {**describe(path), "moved": moved}


def _presets_path(folder: Path, stem: str) -> Path:
    return folder / f"{stem}.presets.json"


def load_presets(folder: Path, stem: str) -> dict[str, Any]:
    try:
        data = json.loads(_presets_path(folder, stem).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_preset(base_dir: Path, stem: str, name: str, settings: dict[str, Any]) -> dict[str, Any]:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Dê um nome ao preset.")
    folder = base_dir / CHANNELS_DIR
    if not (folder / f"{stem}.yaml").exists():
        raise ValueError("Canal não encontrado.")
    presets = load_presets(folder, stem)
    presets[name] = settings
    _presets_path(folder, stem).write_text(json.dumps(presets, ensure_ascii=False, indent=1), encoding="utf-8")
    return presets
