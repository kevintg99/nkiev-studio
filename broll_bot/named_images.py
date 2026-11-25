"""Fotos das coisas com nome próprio que o roteiro cita (a bateria Optima YellowTop, a loja Canadian Tire, a
pessoa), baixadas ANTES da busca.

No vídeo das 9 baterias foram 4 versões: na v1 nenhum banco grátis tinha foto de "MotoMaster Eliminator" e as
fotos tiveram que ser procuradas e baixadas à mão, com ``say``/``say_not`` escritos à mão para cada uma não cair na
cena de outra bateria. Agora o diretor de IA lista os nomes (``DirectorPlan.named``) e este módulo faz o mesmo
sozinho: Google Imagens pela API do Serper (``SERPER_API_KEY``) ou, sem a chave, a Wikimedia. As fotos vão para
``library/mixkit/_auto/`` com o ``.json`` pronto (``requires``/``say``/``say_not``), e a biblioteca local cuida
do resto (a foto só entra na cena que diz o nome; ver ``local_library.fits_scene``).
"""

from __future__ import annotations

import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import requests
from PIL import Image, ImageFilter

from .log import get_logger

log = get_logger("named_images")

AUTO_DIR = "_auto"  # pasta com "_" na frente não vira etiqueta dos arquivos (local_library.build_index)
SERPER_URL = "https://google.serper.dev/images"
W, H = 1920, 1080
MAX_BYTES = 15 * 1024 * 1024
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

# (url da imagem, página, crédito, título) de uma busca
Hit = tuple[str, str, str, str]
FallbackFn = Callable[[str], list[Hit]]
_FALLBACK_LOCK = threading.Lock()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "foto"


def _lower(words: list[str]) -> set[str]:
    return {w.lower() for w in words}


def _cites(words: list[str], text: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.I) for w in words if w)


def library_has(root: Path, item: Any) -> bool:
    """A biblioteca já tem foto deste nome: um ``.json`` cujo ``say`` cita o nome (a "Kirkland" já vale para
    "Kirkland Signature") e cujo ``say_not`` não o exclui (a "Kirkland" não vale para a "Kirkland Signature AGM")."""
    return bool(library_photos(root, item))


def wanted(item: Any, cfg: Any) -> int:
    """Fotos que este nome precisa: ``per_name`` e mais uma a cada ``mentions_per_extra`` citações na fala."""
    extra = int(getattr(item, "mentions", 0) or 0) // max(1, int(getattr(cfg, "mentions_per_extra", 6) or 6))
    return max(cfg.per_name, min(int(getattr(cfg, "per_name_max", cfg.per_name)), cfg.per_name + extra))


def library_photos(root: Path, item: Any) -> list[dict]:
    """Os ``.json`` da biblioteca que valem para este nome (ver ``library_has``)."""
    names = " | ".join([item.name, *item.say])
    out: list[dict] = []
    if not root.is_dir():
        return out
    for side in root.rglob("*.json"):
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        say = [str(w) for w in meta.get("say", [])]
        if say and _cites(say, names) and not _cites([str(w) for w in meta.get("say_not", [])], names):
            out.append(meta)
    return out


def serper_search(query: str, key: str, num: int = 10) -> list[Hit]:
    resp = requests.post(SERPER_URL, json={"q": query, "num": num},
                         headers={"X-API-KEY": key, "Content-Type": "application/json"}, timeout=20)
    resp.raise_for_status()
    out = []
    for img in resp.json().get("images", []):
        url = img.get("imageUrl") or ""
        page = img.get("link") or ""
        if url.startswith("http"):
            domain = img.get("domain") or urlparse(page or url).netloc
            out.append((url, page, f"{domain} (foto da web, licença não verificada)", img.get("title") or ""))
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


# palavras da busca que só dizem o tipo de foto: o título da Wikimedia não as traz ("2021 Toyota Highlander SUV"
# recusava "2021_Toyota_Highlander_2.5_Hybrid.jpg")
GENERIC = {"logo", "photo", "picture", "image", "portrait", "exterior", "interior", "front", "rear", "side", "view",
           "suv", "sedan", "pickup", "truck", "minivan", "official", "building", "headquarters", "office", "sign"}


def _mentions(title: str, name: str, query: str = "", strict: bool = False) -> bool:
    """O resultado traz o nome inteiro (Google Imagens às vezes devolve a bateria do concorrente). ``strict`` (na
    Wikimedia): também o que a coisa é. "Kirkland Signature" achava leite e garrafa d'água da Kirkland."""
    t = _words(title)
    # o nome sem o parêntese ("OMVIC (Ontario Motor Vehicle Industry Council)") ou a busca inteira ("NHTSA logo")
    name_w = _words(re.sub(r"\(.*?\)", " ", name)) or _words(name)
    asked = _words(query) - GENERIC
    if asked and asked <= t:
        return True
    if not name_w or not name_w <= t:
        return False
    extra = asked - name_w
    return not strict or not extra or bool(extra & t)


def commons_title(cand: Any) -> str:
    """O nome do arquivo na Wikimedia ("File:2021_Toyota_Highlander_2.5_Hybrid.jpg"), não a descrição: a do
    "Sky_Tamil_News_Logo.png" diz "global Tamil media" e passava por foto da Global News."""
    page = unquote(getattr(cand, "page_url", "") or "")
    if "File:" in page:
        return page.rsplit("File:", 1)[1].rsplit(".", 1)[0].replace("_", " ")
    return getattr(cand, "title", "") or getattr(cand, "description", "") or ""


def fit_16x9(data: bytes, min_side: int) -> Image.Image | None:
    """Foto de produto (quadrada, fundo branco) em 16:9: a foto inteira no meio, a mesma foto desfocada atrás."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    if min(img.size) < min_side:
        return None
    scale = max(W / img.width, H / img.height)
    back = img.resize((round(img.width * scale), round(img.height * scale)))
    left, top = (back.width - W) // 2, (back.height - H) // 2
    canvas = back.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(40))
    fit = min(W / img.width, H / img.height)
    front = img.resize((max(1, round(img.width * fit)), max(1, round(img.height * fit))))
    canvas.paste(front, ((W - front.width) // 2, (H - front.height) // 2))
    return canvas


def _download(url: str) -> bytes | None:
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20, stream=True)
        if resp.status_code != 200:
            return None
        data = resp.raw.read(MAX_BYTES + 1, decode_content=True)
        return data if len(data) <= MAX_BYTES else None
    except requests.RequestException:
        return None


def fetch_one(item: Any, folder: Path, cfg: Any, key: str | None, fallback: FallbackFn | None,
              want: int | None = None, have: set[str] | None = None) -> list[Path]:
    """Baixa até ``want`` fotos de um nome (padrão ``per_name``) e grava o ``.json`` ao lado de cada uma. ``have``:
    páginas das fotos que a biblioteca já tem (não baixa a mesma de novo)."""
    want = cfg.per_name if want is None else want
    have = have or set()
    hits: list[Hit] = []
    # "name" não pode ir no extra do log (é campo do LogRecord): derrubou o vídeo da garantia Toyota
    if key and cfg.web:
        try:
            hits = [h for h in serper_search(item.query, key) if _mentions(h[3], item.name)]
        except (requests.RequestException, ValueError) as exc:
            log.warning("busca de imagens da web falhou", extra={"stage": "named_images", "nome": item.name,
                                                                 "error": str(exc)[-200:]})
    if not hits and fallback is not None:
        try:
            with _FALLBACK_LOCK:  # a Wikimedia pede uma chamada por vez (6 juntas estouravam o limite)
                found = fallback(item.query)
            hits = [h for h in found if _mentions(h[3], item.name, item.query, strict=True)]
        except Exception as exc:  # noqa: BLE001 - foto que falta não derruba o vídeo
            log.warning("busca na Wikimedia falhou", extra={"stage": "named_images", "nome": item.name,
                                                            "error": str(exc)[-200:]})
    saved: list[Path] = []
    base = slug(item.name)
    for url, page, credit, _title in hits:
        if len(saved) >= want:
            break
        if page and page in have:
            continue
        data = _download(url)
        img = fit_16x9(data, cfg.min_side) if data else None
        if img is None:
            continue
        n = 1
        while (path := folder / f"{base}{'' if n == 1 else f'-{n}'}.jpg").exists():
            n += 1
        folder.mkdir(parents=True, exist_ok=True)
        img.save(path, "JPEG", quality=90)
        meta = {"tags": [item.name.lower()], "description": item.query, "page_url": page, "credit": credit,
                "requires": item.say, "say": item.say, "say_not": item.say_not, "auto": True}
        path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        saved.append(path)
    return saved


def fetch_named(named: list, library_root: Path, cfg: Any, key: str | None,
                fallback: FallbackFn | None = None) -> dict[str, Any]:
    """Baixa as fotos de todos os nomes do diretor que a biblioteca ainda não tem (em paralelo)."""
    if not cfg.enabled or not named:
        return {}
    folder = library_root / AUTO_DIR
    # o que falta de cada nome: quem já tem foto fica de fora (a foto posta à mão vale), menos o nome citado a toda
    # hora, que completa as fotos a mais que ganhou
    have = {id(n): library_photos(library_root, n) for n in named}
    todo = [n for n in named if not have[id(n)] or len(have[id(n)]) < wanted(n, cfg) > cfg.per_name]
    report: dict[str, Any] = {"named": len(named), "already_in_library": len(named) - len(todo), "downloaded": {},
                              "missing": []}
    if not todo:
        return report
    if not key and cfg.web:
        log.warning("sem SERPER_API_KEY no .env: as fotos dos nomes do roteiro vêm só da Wikimedia",
                    extra={"stage": "named_images"})
    def one(item: Any) -> list[Path]:
        try:
            old = have[id(item)]
            return fetch_one(item, folder, cfg, key, fallback, wanted(item, cfg) - len(old),
                             {str(m.get("page_url") or "") for m in old} - {""})
        except Exception as exc:  # noqa: BLE001 - foto que falta não derruba o vídeo
            log.warning("foto do nome falhou", extra={"stage": "named_images", "nome": item.name,
                                                      "error": str(exc)[-200:]})
            return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        saved = list(pool.map(one, todo))
    for item, paths in zip(todo, saved):
        if paths:
            report["downloaded"][item.name] = [p.name for p in paths]
        elif not have[id(item)]:
            report["missing"].append(item.name)
    log.info("fotos dos nomes do roteiro", extra={"stage": "named_images", "baixadas": list(report["downloaded"]),
                                                  "sem_foto": report["missing"],
                                                  "ja_tinha": report["already_in_library"]})
    return report
