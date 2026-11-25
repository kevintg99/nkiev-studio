"""Apresentador da HeyGen falando com a SUA narração (lábios sincronizados), em tela cheia ou num canto.

Tela cheia (``layout: full``, o padrão): a imagem do personagem do canal (ex.: o mecânico já dentro da oficina) é
animada pela HeyGen como foto falante, com o lip-sync da narração, e o clipe entra na tela inteira do jeito que a
imagem é (o cenário é o da própria imagem). No canto (``layout: corner``): a HeyGen gera sobre verde e o bot recorta.

Fluxo: logo depois da sincronização do roteiro o bot escolhe os momentos (abertura, chamada para se inscrever e
final), corta esses trechos da narração e manda para a HeyGen enquanto o resto do vídeo segue. Antes do render ele
põe os clipes prontos por cima dos shots daqueles trechos (o botão de inscrição continua por cima do avatar).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from . import usage
from .config import AvatarConfig
from .log import get_logger
from .media import FFmpegError, probe, run
from .models import Scene, Sentence, Shot
from .rhythm import is_cta

log = get_logger("avatar")

API = "https://api.heygen.com"
UPLOAD = "https://upload.heygen.com/v1/asset"
MIME = {".mp3": "audio/mpeg", ".wav": "audio/x-wav", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
FADE = 0.3  # entrada/saída suave do avatar (s)
PAD_BEFORE, PAD_AFTER = 0.12, 0.3  # o avatar já está na tela quando a frase começa e não some no meio da última sílaba
MIN_GAP = 2.0  # entre um aparecimento e outro
CHECKIN_WINDOW = 18.0  # a aparição extra procura uma frase até esse tanto antes/depois do minuto marcado
# frase com dado (número, preço, porcentagem): ali quem aparece é o gráfico, não o apresentador
_DATA = re.compile(
    r"\d|[%£€$]|\b(?:per ?cent|percent|pour ?cent|por ?cento|procent\w*|thousand|million|billion|hundred|"
    r"mille|millions?|milliards?|cent(?:s)?|mil|milh\w+|bilh\w+|tysi\w+|milion\w*|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
    r"deux|trois|quatre|cinq|six|sept|huit|neuf|dix|vingt|trente|quarante|cinquante|"
    r"dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|vinte|trinta|quarenta|cinquenta)\b", re.I)


def is_data(text: str) -> bool:
    return bool(_DATA.search(text))


def _checkins(spoken: list[Sentence], cfg: AvatarConfig, taken: list[tuple[float, float]]) -> list[AvatarSegment]:
    """Aparições extras no começo do vídeo: perto de cada minuto marcado, a frase (ou duas seguidas) que cabe
    entre o mínimo e o máximo, de preferência sem números (onde entra gráfico) e sem pedir inscrição."""
    every, until = cfg.checkin_every, cfg.checkin_until
    if every <= 0 or until < every:
        return []
    lo, hi = (list(cfg.checkin_seconds) + [4.0, 9.0])[:2]
    out: list[AvatarSegment] = []
    k = 1
    while k * every <= until + 1e-6:
        target = k * every
        k += 1
        best: tuple[float, float, float] | None = None
        for i, s in enumerate(spoken):
            if abs(s.start - target) > CHECKIN_WINDOW or s.start > until + 10:  # dentro dos primeiros minutos
                continue
            end, text = s.end, s.text
            if end - s.start < lo and i + 1 < len(spoken) and spoken[i + 1].end - s.start <= hi:
                end, text = spoken[i + 1].end, text + " " + spoken[i + 1].text
            dur = end - s.start
            if not lo <= dur <= hi or is_cta(text):
                continue
            last = i + (1 if text != s.text else 0)
            near = spoken[max(0, i - 1):last + 2]
            if any(len(n.text.split()) <= 2 for n in near):  # "Two.", "Number 8": lista/ranking com gráfico próprio
                continue
            if not all(end + MIN_GAP <= a or s.start >= b + MIN_GAP for a, b in taken):
                continue
            score = abs(s.start - target) + (25.0 if is_data(text) else 0.0)
            if best is None or score < best[0]:
                best = (score, s.start, end)
        if best is not None:
            out.append(AvatarSegment("checkin", best[1], best[2]))
            taken.append((best[1], best[2]))
    return out


# Tabela da API da HeyGen (cobrança na carteira pré-paga, em US$; 720p e 1080p custam igual).
# Muda em config.yaml (costs.heygen) se a HeyGen mudar os preços.
HEYGEN_PRICES: dict[str, float] = {
    "photo_avatar_iv": 0.0385,  # foto falante, Avatar IV (US$/s)
    "photo_standard": 0.0165,  # foto falante, Avatar III (US$/s)
    "studio_avatar_iv": 0.0805,  # avatar pronto da HeyGen / avatar de vídeo, Avatar IV
    "studio_standard": 0.0165,  # avatar pronto da HeyGen, Avatar III
    "photo_upload": 1.32,  # criação do avatar de foto (US$ por chamada; uma vez por imagem)
}


def heygen_rate(kind: str, engine: str, prices: dict[str, float] | None = None) -> tuple[str, float]:
    """(nome do preço, US$ por segundo) de um clipe."""
    table = {**HEYGEN_PRICES, **{k: float(v) for k, v in (prices or {}).items()}}
    key = f"{'photo' if kind == 'photo' else 'studio'}_{'avatar_iv' if engine == 'avatar_iv' else 'standard'}"
    return key, table[key]


def video_seconds(path: Path, fallback: float) -> float:
    """Duração do clipe que a HeyGen entregou (é o que ela cobra); sem ffprobe, a do trecho."""
    try:
        return probe(path).duration or fallback
    except Exception:  # noqa: BLE001
        return fallback


def _photo_gone(message: str) -> bool:
    """ "HeyGen 404: avatar look not found, look_id: ..." / "talking photo not found": a foto saiu da conta."""
    low = message.lower()
    return "not found" in low and any(w in low for w in ("look", "photo", "avatar"))


class HeyGenError(RuntimeError):
    pass


@dataclass
class AvatarSegment:
    moment: str  # "intro" | "cta" | "outro"
    start: float
    end: float
    path: str = ""  # clipe pronto
    status: str = "pending"  # pending | ok | cache | failed | timeout
    error: str = ""
    engine: str = ""  # motor que gerou (avatar_iv | standard)
    seconds: float = 0.0  # segundos cobrados pela HeyGen (0 = veio do cache, não custou)
    usd: float = 0.0
    note: str = ""  # ex.: a HeyGen recusou o Avatar IV e o bot refez no padrão

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# ------------------------------------------------------------------ momentos
def pick_segments(sentences: list[Sentence], cfg: AvatarConfig, audio_seconds: float) -> list[AvatarSegment]:
    """Abertura (primeiras frases, entre o mínimo e o máximo), a primeira chamada para se inscrever e o final
    (últimas frases). Sempre em frases inteiras quando dá; nunca sobrepostos; no total, até ``max_seconds``."""
    spoken = [s for s in sentences if s.end > s.start]
    if not spoken:
        return []
    moments = set(cfg.moments)
    segs: list[AvatarSegment] = []
    lo, hi = (list(cfg.intro_seconds) + [6.0, 12.0])[:2]
    if "intro" in moments:
        start, end = spoken[0].start, spoken[0].end
        for s in spoken[1:]:
            # abertura curta demais ("Les garagistes n'en peuvent plus." = 3 s): pega a frase seguinte inteira,
            # mesmo passando um pouco do máximo, em vez de cortar no meio dela
            if end - start >= lo or s.end - start > hi * 1.5:
                break
            end = s.end
        segs.append(AvatarSegment("intro", start, min(end, start + hi * 1.5)))
    if "outro" in moments:
        olo, ohi = (list(cfg.outro_seconds) + [4.0, 10.0])[:2]
        start, end = spoken[-1].start, spoken[-1].end
        for s in reversed(spoken[:-1]):
            if end - start >= olo or end - s.start > ohi:
                break
            start = s.start
        start = max(start, end - ohi)
        if not segs or start >= segs[0].end + MIN_GAP:
            segs.append(AvatarSegment("outro", start, end))
    if "cta" in moments:
        taken = [(g.start, g.end) for g in segs]
        for s in spoken:
            if not is_cta(s.text):
                continue
            start, end = s.start, s.end
            nxt = spoken[spoken.index(s) + 1] if s is not spoken[-1] else None
            if end - start < 4.0 and nxt is not None and nxt.end - start <= cfg.cta_seconds:
                end = nxt.end  # "If this is useful, subscribe now." (3 s) + a frase seguinte
            end = min(end, start + cfg.cta_seconds)
            if all(end + MIN_GAP <= a or start >= b + MIN_GAP for a, b in taken):
                segs.append(AvatarSegment("cta", start, end))
                break
    if "checkin" in moments:
        segs += _checkins(spoken, cfg, [(g.start, g.end) for g in segs])
    order = {"intro": 0, "cta": 1, "outro": 2, "checkin": 3}
    budget = cfg.max_seconds
    out: list[AvatarSegment] = []
    for g in sorted(segs, key=lambda g: (order[g.moment], g.start)):  # o teto corta as extras primeiro
        g.start = max(0.0, g.start - PAD_BEFORE)
        g.end = min(audio_seconds, g.end + PAD_AFTER)
        if g.duration < 1.5 or budget < 1.5:
            continue
        g.end = min(g.end, g.start + budget)
        budget -= g.duration
        out.append(g)
    return sorted(out, key=lambda g: g.start)


# ------------------------------------------------------------------ HeyGen
class HeyGen:
    """Cliente mínimo da API v2 da HeyGen (upload do áudio, geração, status, download, lista de avatares)."""

    def __init__(self, api_key: str, timeout: float = 60.0, session: Any = None) -> None:
        self.key = api_key.strip()
        self.timeout = timeout
        self.http = session or requests.Session()

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"X-Api-Key": self.key, "Accept": "application/json", **extra}

    @staticmethod
    def _json(r: Any) -> dict[str, Any]:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code in (401, 403):
            raise HeyGenError("chave da HeyGen recusada (confira a HEYGEN_API_KEY em Configurações)")
        err = data.get("error") if isinstance(data, dict) else None
        if r.status_code >= 400 or (err and err not in ({}, "")):
            msg = err.get("message") if isinstance(err, dict) else err or data.get("message") or r.text[:200]
            raise HeyGenError(f"HeyGen {r.status_code}: {msg}")
        return data.get("data") or {}

    def upload(self, path: Path) -> str:
        """Sobe um arquivo (áudio, imagem ou vídeo de fundo) e devolve o ID dele na HeyGen."""
        mime = MIME.get(path.suffix.lower(), "application/octet-stream")
        r = self.http.post(UPLOAD, data=path.read_bytes(), headers=self._headers(**{"Content-Type": mime}),
                           timeout=max(self.timeout, 180))
        asset = self._json(r).get("id")
        if not asset:
            raise HeyGenError("a HeyGen não devolveu o ID do arquivo enviado")
        return str(asset)

    upload_audio = upload

    def delete_photo(self, photo_id: str) -> bool:
        """Apaga uma foto falante que o bot mesmo enviou (libera vaga no limite de avatares de foto do plano)."""
        try:
            r = self.http.delete(f"{API}/v2/talking_photo/{photo_id}", headers=self._headers(),
                                 timeout=self.timeout)
            self._json(r)
            self.last_delete_error = ""
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_delete_error = str(exc)[-200:]
            log.warning("avatar: não consegui apagar a foto antiga na HeyGen",
                        extra={"stage": "avatar", "id": photo_id, "error": self.last_delete_error})
            return False

    last_delete_error = ""

    def upload_photo(self, path: Path) -> str:
        """Sobe a imagem do personagem como foto falante e devolve o ``talking_photo_id``."""
        mime = MIME.get(path.suffix.lower(), "image/jpeg")
        r = self.http.post(f"{UPLOAD.rsplit('/', 1)[0]}/talking_photo", data=path.read_bytes(),
                           headers=self._headers(**{"Content-Type": mime}), timeout=max(self.timeout, 180))
        data = self._json(r)
        tp = data.get("talking_photo_id") or data.get("id")
        if not tp:
            raise HeyGenError("a HeyGen não aceitou a imagem do personagem (precisa de um rosto bem visível)")
        return str(tp)

    def generate(self, audio_asset: str, cfg: AvatarConfig, width: int = 1280, height: int = 720,
                 photo_id: str = "", engine: str = "standard") -> str:
        """Tela cheia: a cena é a da própria imagem (sem fundo trocado). No canto: fundo verde para recortar.
        Foto falante com ``engine="avatar_iv"``: o motor Avatar IV (cabeça, corpo e mãos se mexem)."""
        corner = cfg.layout == "corner"
        if photo_id or cfg.kind == "photo":
            character: dict[str, Any] = {"type": "talking_photo", "talking_photo_id": photo_id or cfg.avatar_id}
            if engine == "avatar_iv":
                character["use_avatar_iv_model"] = True
            else:
                character["talking_style"] = cfg.motion or "expressive"
            if corner:
                character["matting"] = True  # tira o cenário da foto para ficar só a pessoa sobre o verde
        else:
            character = {"type": "avatar", "avatar_id": cfg.avatar_id, "avatar_style": cfg.style or "normal"}
        video_input: dict[str, Any] = {"character": character,
                                       "voice": {"type": "audio", "audio_asset_id": audio_asset}}
        if corner:
            video_input["background"] = {"type": "color", "value": cfg.key_color}
        body = {"video_inputs": [video_input], "dimension": {"width": width, "height": height}}
        r = self.http.post(f"{API}/v2/video/generate", json=body, headers=self._headers(), timeout=self.timeout)
        vid = self._json(r).get("video_id")
        if not vid:
            raise HeyGenError("a HeyGen não devolveu o ID do vídeo")
        return str(vid)

    def status(self, video_id: str) -> tuple[str, str, str]:
        """(status, url do vídeo, erro)."""
        r = self.http.get(f"{API}/v1/video_status.get", params={"video_id": video_id}, headers=self._headers(),
                          timeout=self.timeout)
        data = self._json(r)
        err = data.get("error") or ""
        if isinstance(err, dict):
            err = err.get("detail") or err.get("message") or json.dumps(err)
        return str(data.get("status") or ""), str(data.get("video_url") or ""), str(err)

    def wait(self, video_id: str, deadline: float | Callable[[], float], poll: float = 10.0,
             sleep=time.sleep) -> str:
        """Espera o vídeo ficar pronto. ``deadline`` pode ser uma função: o prazo muda enquanto espera (o
        AvatarJob só fecha o prazo quando o vídeo precisa do avatar)."""
        limit = deadline if callable(deadline) else (lambda: deadline)
        while True:
            try:
                status, url, err = self.status(video_id)
            except requests.RequestException:  # queda da conexão no meio da espera: o vídeo segue na HeyGen
                status, url, err = "", "", ""
                if time.monotonic() + poll > limit():
                    raise
                sleep(poll)
                continue
            if status == "completed" and url:
                return url
            if status == "failed":
                if "credit" in err.lower():  # "Insufficient credit ... 'api' credits": carteira da API vazia
                    err += " (acabou o saldo da API da HeyGen, que é separado do plano do site: recarregue e rode de novo)"
                raise HeyGenError(f"a HeyGen não conseguiu gerar o avatar: {err or 'sem detalhes'}")
            if time.monotonic() + poll > limit():
                raise TimeoutError("a HeyGen ainda não terminou")
            sleep(poll)

    def download(self, url: str, dest: Path) -> Path:
        tmp = dest.with_suffix(".part")
        with self.http.get(url, stream=True, timeout=self.timeout) as r:
            if r.status_code >= 400:
                raise HeyGenError(f"download do avatar falhou ({r.status_code})")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        tmp.replace(dest)
        return dest

    def balance(self) -> float | None:
        """Saldo da carteira pré-paga da API (US$), ou None se a HeyGen não disser."""
        r = self.http.get(f"{API}/v3/users/me", headers=self._headers(), timeout=self.timeout)
        try:
            data = self._json(r)
        except HeyGenError:
            return None
        return wallet_usd(data)

    def avatars(self) -> list[dict[str, str]]:
        """Avatares e fotos falantes da conta: [{id, name, kind, preview, gender}]."""
        r = self.http.get(f"{API}/v2/avatars", headers=self._headers(), timeout=self.timeout)
        data = self._json(r)
        out = [{"id": a.get("avatar_id", ""), "name": a.get("avatar_name", ""), "kind": "avatar",
                "preview": a.get("preview_image_url", ""), "gender": a.get("gender", "")}
               for a in data.get("avatars") or [] if a.get("avatar_id")]
        out += [{"id": a.get("talking_photo_id", ""), "name": a.get("talking_photo_name", ""), "kind": "photo",
                 "preview": a.get("preview_image_url", ""), "gender": ""}
                for a in data.get("talking_photos") or [] if a.get("talking_photo_id")]
        return out


def wallet_usd(data: Any) -> float | None:
    """Acha o saldo na resposta de /v3/users/me (``wallet`` pode vir como número ou como objeto)."""
    if isinstance(data, dict) and "wallet" in data:
        data = data["wallet"]
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        return float(data)
    if isinstance(data, dict):
        for key in ("balance", "remaining_balance", "available_balance", "available", "usd", "amount", "credits"):
            if key in data:
                found = wallet_usd(data[key])
                if found is not None:
                    return found
    if isinstance(data, str):
        try:
            return float(data.replace("$", "").strip())
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ geração em segundo plano
def cut_audio(audio: Path, start: float, end: float, dest: Path) -> Path:
    """Trecho da narração em MP3 (o formato que a HeyGen aceita); sem o codificador de MP3, vai em WAV."""
    base = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(audio),
            "-vn", "-ac", "1", "-ar", "44100"]
    try:
        run([*base, "-c:a", "libmp3lame", "-b:a", "160k", str(dest.with_suffix(".mp3"))], timeout=120, stage="avatar")
        return dest.with_suffix(".mp3")
    except FFmpegError:
        run([*base, "-c:a", "pcm_s16le", str(dest.with_suffix(".wav"))], timeout=120, stage="avatar")
        return dest.with_suffix(".wav")


def photo_file(cfg: AvatarConfig, base_dir: Path) -> Path | None:
    """A imagem do personagem do canal (``avatar.photo``), se existir."""
    if not cfg.photo:
        return None
    path = Path(cfg.photo)
    path = path if path.is_absolute() else base_dir / path
    if not path.is_file():
        log.warning("avatar: imagem do personagem não encontrada", extra={"stage": "avatar", "imagem": str(path)})
        return None
    return path


def engine_of(cfg: AvatarConfig, photo: str = "") -> str:
    """Motor usado: o Avatar IV só existe para foto falante (a imagem do personagem)."""
    is_photo = bool(photo) or cfg.kind == "photo"
    return "avatar_iv" if is_photo and cfg.engine == "avatar_iv" else "standard"


def clip_key(cfg: AvatarConfig, audio_bytes: bytes, photo: str = "") -> str:
    h = hashlib.sha1()
    for part in (cfg.avatar_id, cfg.kind, cfg.style, cfg.key_color, cfg.layout, photo, engine_of(cfg, photo), "v3"):
        h.update(part.encode() + b"|")
    h.update(audio_bytes)
    return h.hexdigest()[:20]


RESULT_GRACE = 60.0  # quando o vídeo pede o avatar e a HeyGen ainda não terminou: espera mais isso (s)

LIMIT_MSG = ("a conta da HeyGen chegou no limite de avatares de foto do plano. Apague em app.heygen.com → "
             "Avatars as fotos que não usa mais (testes, imagens trocadas) e rode o vídeo de novo")


def photo_digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def photos_in_use(base_dir: Path) -> set[str]:
    """Impressões digitais das imagens de personagem que os canais usam hoje (as outras podem ser apagadas)."""
    import yaml

    from .channels import channel_files

    used: set[str] = set()
    for f in channel_files(base_dir):
        try:
            photo = ((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("avatar") or {}).get("photo")
            path = Path(photo) if photo else None
            if path is not None and not path.is_absolute():
                path = base_dir / path
            if path is not None and path.is_file():
                used.add(photo_digest(path))
        except Exception:  # noqa: BLE001
            continue
    return used


def _photo_entry(value: Any) -> tuple[str, str]:
    """(id, canal) de uma linha de fotos_heygen.json (antes era só o id)."""
    if isinstance(value, dict):
        return str(value.get("id") or ""), str(value.get("channel") or "")
    return str(value or ""), ""


class AvatarJob:
    """Gera os clipes do avatar em paralelo com o resto do pipeline."""

    def __init__(self, cfg: AvatarConfig, api_key: str, audio: Path, segments: list[AvatarSegment],
                 cache_dir: Path, work_dir: Path, client: HeyGen | None = None, photo: Path | None = None,
                 size: tuple[int, int] = (1920, 1080), prices: dict[str, float] | None = None,
                 channel: str = "", in_use: set[str] | None = None, channel_file: Path | None = None) -> None:
        self.cfg = cfg
        self.channel_file = channel_file  # canais/<canal>.yaml: o ID da imagem enviada fica guardado nele
        self.channel = channel  # canal dono da imagem (a imagem antiga dele sai da HeyGen quando ele troca)
        self.in_use = in_use  # imagens que algum canal usa hoje (None = não sabe: não apaga nada fora do canal)
        self.prices = prices or {}
        self.wallet: float | None = None  # saldo da carteira da HeyGen depois do vídeo
        self.photo = photo  # imagem do personagem (animada pela HeyGen)
        self.size = size if cfg.layout == "full" else (1280, 720)
        self._photo_id = ""  # foto falante na HeyGen (depois do upload)
        self._photo_tag = ""
        self._source = ""  # impressão digital do arquivo da narração (para achar clipes já feitos)
        self._index_lock = threading.Lock()
        self._photo_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}  # um pedido por clipe (trechos de áudio igual esperam)
        self.audio = audio
        self.segments = segments
        self.cache_dir = cache_dir
        self.work_dir = work_dir
        self.client = client or HeyGen(api_key)
        self._thread: threading.Thread | None = None
        self._deadline = 0.0
        self._needed = False  # o vídeo já pediu o avatar (result): daí em diante vale o prazo

    def start(self) -> "AvatarJob":
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._deadline = time.monotonic() + self.cfg.wait_minutes * 60
        self._thread = threading.Thread(target=self._run, name="avatar", daemon=True)
        self._thread.start()
        return self

    def _upload_photo(self) -> None:
        """Sobe a imagem do personagem uma vez (o ID fica guardado: a mesma imagem não sobe de novo).
        Canal com o ID da HeyGen (criado à mão em app.heygen.com ou guardado pelo bot): usa o ID direto e
        nunca envia a imagem (cada envio cria um personagem novo na conta e custa US$ 1,32)."""
        if self.cfg.avatar_id:
            # canal com a imagem do personagem + o ID: o ID é a foto falante dessa imagem, mesmo que a lista da HeyGen
            # mostre como "Avatar" (o personagem do Dale saiu só mexendo a boca: ia no motor padrão, sem Avatar IV)
            if self.cfg.kind == "photo" or self.photo is not None:
                self._photo_id = self.cfg.avatar_id
                self._photo_tag = f"id:{self.cfg.avatar_id}"
            return
        if self.photo is None:
            return
        digest = photo_digest(self.photo)
        known_path = self.cache_dir / "fotos_heygen.json"
        try:
            known = json.loads(known_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        photo_id = _photo_entry(known.get(digest))[0]
        if not photo_id:
            src = self.photo
            if src.suffix.lower() not in (".jpg", ".jpeg", ".png"):  # webp etc.: a HeyGen quer JPG ou PNG
                from PIL import Image

                src = self.work_dir / "personagem.jpg"
                with Image.open(self.photo) as im:
                    im.convert("RGB").save(src, quality=95)
            # a imagem antiga deste canal (trocada) sai da HeyGen antes: o plano tem limite de avatares de foto
            if self._cleanup(known, digest, only_channel=True):
                known_path.write_text(json.dumps(known, indent=1), encoding="utf-8")
            try:
                photo_id = self.client.upload_photo(src)
            except HeyGenError as exc:
                if "limit" not in str(exc).lower():
                    raise
                # no limite: apaga as fotos que o bot enviou e nenhum canal usa mais (testes, imagens trocadas)
                if not self._cleanup(known, digest, only_channel=False):
                    raise HeyGenError(f"{LIMIT_MSG}. {self._limit_detail(known)} ({exc})") from exc
                known_path.write_text(json.dumps(known, indent=1), encoding="utf-8")
                try:
                    photo_id = self.client.upload_photo(src)
                except HeyGenError as exc2:
                    raise HeyGenError(f"{LIMIT_MSG}. {self._limit_detail(known)} ({exc2})") from exc2
            fee = {**HEYGEN_PRICES, **self.prices}.get("photo_upload", 0.0)
            usage.charge("avatar (HeyGen)", fee, "HeyGen", "imagem do personagem enviada (uma vez por imagem)")
            known[digest] = {"id": photo_id, "channel": self.channel}
            known_path.write_text(json.dumps(known, indent=1), encoding="utf-8")
            remember_photo_id(self.channel_file, photo_id, self.photo)
        self._photo_id, self._photo_tag = photo_id, digest

    def _limit_detail(self, known: dict[str, Any]) -> str:
        """O que tem na conta e o que o bot tentou apagar (para saber o que apagar à mão)."""
        parts = []
        try:
            photos = [a for a in self.client.avatars() if a.get("kind") == "photo"]
            names = ", ".join(a.get("name") or a.get("id", "")[:8] for a in photos[:6])
            parts.append(f"Fotos na conta: {len(photos)} ({names})")
        except Exception:  # noqa: BLE001
            pass
        mine = [_photo_entry(v)[0] for v in known.values()]
        parts.append(f"enviadas pelo bot e guardadas: {len(mine)}")
        if self.client.last_delete_error:
            parts.append(f"a HeyGen não deixou apagar: {self.client.last_delete_error}")
        return "; ".join(parts)

    def _cleanup(self, known: dict[str, Any], current: str, only_channel: bool) -> int:
        """Apaga na HeyGen fotos que o bot enviou e que ninguém usa mais: a antiga deste canal ou, no limite do
        plano, todas as que nenhum canal usa hoje. Nunca mexe em avatares que você criou fora do bot."""
        removed = 0
        for dig, value in list(known.items()):
            pid, channel = _photo_entry(value)
            if dig == current or not pid:
                continue
            if only_channel:
                drop = bool(self.channel) and channel == self.channel and \
                    (self.in_use is None or dig not in self.in_use)
            else:
                drop = self.in_use is not None and dig not in self.in_use
            if drop and self.client.delete_photo(pid):
                del known[dig]
                removed += 1
                log.info("avatar: foto antiga apagada na HeyGen", extra={"stage": "avatar", "id": pid})
        return removed

    def _run(self) -> None:
        try:
            self._source = hashlib.sha1(self.audio.read_bytes()).hexdigest()[:16]
        except OSError:
            self._source = ""
        if not self.cfg.regenerate and self._reuse_existing():
            return
        # só aqui: antes, a frase saía em toda rodada (também na versão 2, que não pede nada) e parecia que o bot
        # estava pagando a HeyGen de novo
        log.info("avatar: gerando na HeyGen em segundo plano", extra={
            "stage": "avatar", "avatar": self.photo.name if self.photo else self.cfg.avatar_id,
            "trechos": [f"{g.moment} {g.start:.1f}-{g.end:.1f}s" for g in self.segments]})
        try:
            self._upload_photo()
        except Exception as exc:  # noqa: BLE001 - sem a imagem não há o que animar: o vídeo sai sem avatar
            for g in self.segments:
                g.status, g.error = "failed", f"imagem do personagem: {str(exc)[-700:]}"
            return
        with ThreadPoolExecutor(max_workers=max(1, len(self.segments))) as pool:
            list(pool.map(self._one, self.segments))

    def _expected_tag(self) -> str:
        """A marca do personagem que ``_upload_photo`` vai usar, sem subir nada para a HeyGen."""
        if self.cfg.avatar_id:
            return f"id:{self.cfg.avatar_id}" if (self.cfg.kind == "photo" or self.photo is not None) else ""
        return photo_digest(self.photo) if self.photo is not None else ""

    def _reuse_existing(self) -> bool:
        """Versão 2, 3... da mesma narração: usa os clipes que a HeyGen já gerou (os mesmos momentos, no tempo em
        que foram feitos) e não pede nada novo, nem sobe a imagem. Momento sem clipe fica sem avatar. Antes, a
        transcrição mudava o trecho uns décimos de segundo e o bot pagava os 4 clipes de novo."""
        if not self._source:
            return False
        self._photo_tag = self._expected_tag()
        try:
            entries = json.loads(self._index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        prof = self._profile()
        done = [e for e in entries if isinstance(e, dict) and all(e.get(k) == v for k, v in prof.items())
                and (self.cache_dir / str(e.get("file", ""))).is_file()] if isinstance(entries, list) else []
        if not done:
            return False
        used: set[str] = set()
        for seg in self.segments:
            same = [e for e in done if e.get("moment") == seg.moment and e["file"] not in used]
            if not same:
                seg.status = "skipped"
                seg.error = "esta narração já tem avatar gerado e este momento não tem clipe: não paga a HeyGen de novo"
                continue
            e = min(same, key=lambda e: abs(e["start"] - seg.start))
            used.add(e["file"])
            seg.start, seg.end = e["start"], e["end"]
            seg.path, seg.status = str(self.cache_dir / e["file"]), "cache"
        log.info("avatar: reaproveitando os clipes já gerados desta narração (sem gastar na HeyGen)",
                 extra={"stage": "avatar", "clipes": sorted(used), "sem_clipe": [g.moment for g in self.segments
                                                                                 if g.status == "skipped"]})
        return True

    # índice dos clipes já feitos: mesma narração + mesmo personagem + mesmo motor + mesmo trecho (com folga
    # de alguns décimos, se a sincronização mudar um pouco) = reaproveita, não paga de novo
    def _index_path(self) -> Path:
        return self.cache_dir / "clipes.json"

    def _profile(self) -> dict[str, str]:
        return {"source": self._source, "photo": self._photo_tag, "avatar": self.cfg.avatar_id,
                "engine": engine_of(self.cfg, self._photo_tag), "layout": self.cfg.layout, "style": self.cfg.style,
                "kind": self.cfg.kind}

    def _find_done(self, seg: AvatarSegment) -> dict[str, Any] | None:
        if not self._source:
            return None
        try:
            entries = json.loads(self._index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        prof = self._profile()
        best = None
        for e in entries if isinstance(entries, list) else []:
            if any(e.get(k) != v for k, v in prof.items()):
                continue
            if abs(e["start"] - seg.start) <= 0.4 and abs(e["end"] - seg.end) <= 0.6 and \
                    (self.cache_dir / e["file"]).is_file():
                gap = abs(e["start"] - seg.start) + abs(e["end"] - seg.end)
                if best is None or gap < best[0]:
                    best = (gap, e)
        return best[1] if best else None

    def _remember(self, seg: AvatarSegment, clip: Path) -> None:
        if not self._source:
            return
        with self._index_lock:
            try:
                entries = json.loads(self._index_path().read_text(encoding="utf-8"))
                entries = entries if isinstance(entries, list) else []
            except (OSError, ValueError):
                entries = []
            entries = [e for e in entries if e.get("file") != clip.name]
            entries.append({**self._profile(), "start": round(seg.start, 3), "end": round(seg.end, 3),
                            "moment": seg.moment, "file": clip.name})
            self._index_path().write_text(json.dumps(entries, indent=1), encoding="utf-8")

    def _one(self, seg: AvatarSegment) -> None:
        try:
            done = self._find_done(seg)
            if done is not None:  # este trecho já foi feito antes: usa o clipe (e o trecho exato dele)
                seg.start, seg.end = done["start"], done["end"]
                seg.path, seg.status = str(self.cache_dir / done["file"]), "cache"
                return
            # um arquivo por trecho: as aparições rodam em paralelo (antes, duas "checkin" usavam o mesmo arquivo)
            part = cut_audio(self.audio, seg.start, seg.end,
                             self.work_dir / f"avatar_{seg.moment}_{int(round(seg.start * 1000))}")
            key = clip_key(self.cfg, part.read_bytes(), self._photo_tag)
            # dois trechos com o mesmo áudio dão o mesmo clipe: o segundo espera e reaproveita (no Windows, os dois
            # escrevendo o mesmo arquivo ao mesmo tempo davam "Acesso negado" e o trecho saía sem avatar)
            with self._index_lock:
                lock = self._key_locks.setdefault(key, threading.Lock())
            with lock:
                self._make(seg, part, key)
        except TimeoutError as exc:
            seg.status, seg.error = "timeout", str(exc)
        except Exception as exc:  # noqa: BLE001 - avatar é um extra: o vídeo sai sem ele
            seg.status, seg.error = "failed", str(exc)[-300:]

    def _make(self, seg: AvatarSegment, part: Path, key: str) -> None:
        clip = self.cache_dir / f"{key}.mp4"
        if clip.exists() and clip.stat().st_size > 0:
            seg.path, seg.status = str(clip), "cache"
            self._remember(seg, clip)
            return
        ticket = self.cache_dir / f"{key}.json"  # pedido já feito (rodada anterior): não paga de novo
        video_id = ""
        if ticket.exists():
            try:
                video_id = json.loads(ticket.read_text(encoding="utf-8")).get("video_id", "")
            except (OSError, ValueError):
                video_id = ""
        if video_id:
            try:
                url = self.client.wait(video_id, self._limit)
            except HeyGenError:
                video_id = ""  # o pedido antigo falhou: faz um novo
        if not video_id:
            asset = self.client.upload(part)
            video_id = self._generate(asset, seg)
            ticket.write_text(json.dumps({"video_id": video_id, "moment": seg.moment}), encoding="utf-8")
            url = self.client.wait(video_id, self._limit)
        self.client.download(url, clip)
        ticket.unlink(missing_ok=True)
        seg.path, seg.status = str(clip), "ok"
        self._remember(seg, clip)
        self._charge(seg, clip)

    def _charge(self, seg: AvatarSegment, clip: Path) -> None:
        kind = "photo" if (self._photo_id or self.cfg.kind == "photo") else "avatar"
        key, rate = heygen_rate(kind, seg.engine or "standard", self.prices)
        seg.seconds = round(video_seconds(clip, seg.duration), 2)
        seg.usd = round(seg.seconds * rate, 4)
        name = "Avatar IV" if seg.engine == "avatar_iv" else "Avatar III"
        usage.charge("avatar (HeyGen)", seg.usd, f"HeyGen {name} · {'foto' if kind == 'photo' else 'avatar'}",
                     f"{seg.moment} {seg.seconds:.1f} s", seg.seconds)

    def _generate(self, audio_asset: str, seg: AvatarSegment) -> str:
        """Gera; se a HeyGen não acha mais a foto guardada (apagada à mão em app.heygen.com), sobe de novo e
        tenta outra vez."""
        stale = self._photo_id
        try:
            return self._generate_once(audio_asset, seg)
        except HeyGenError as exc:
            if self.cfg.avatar_id and _photo_gone(str(exc)):  # ID do canal: nunca envia a imagem por conta própria
                raise HeyGenError(f"a HeyGen não acha o avatar {self.cfg.avatar_id} na sua conta (foi apagado?). "
                                  "Confira o ID em Canais → Editar → Avatar da HeyGen") from exc
            if not stale or not _photo_gone(str(exc)) or not self._refresh_photo(stale):
                raise
        return self._generate_once(audio_asset, seg)

    def _refresh_photo(self, stale: str) -> bool:
        """Esquece o ID que a HeyGen não reconhece e sobe a imagem de novo (uma vez só, mesmo com os trechos
        rodando em paralelo: quem chega depois usa o ID novo)."""
        with self._photo_lock:
            if self._photo_id != stale:
                return True
            known_path = self.cache_dir / "fotos_heygen.json"
            try:
                known = json.loads(known_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                known = {}
            known = {k: v for k, v in known.items() if _photo_entry(v)[0] != stale}
            known_path.write_text(json.dumps(known, indent=1), encoding="utf-8")
            log.info("avatar: a HeyGen não acha mais a imagem do personagem; enviando de novo",
                     extra={"stage": "avatar", "id": stale})
            self._upload_photo()
            return bool(self._photo_id) and self._photo_id != stale

    def _generate_once(self, audio_asset: str, seg: AvatarSegment) -> str:
        """Tenta o motor escolhido em 1080p; se a HeyGen recusar, 720p (plano sem 1080p) e, por último, o motor
        padrão (Avatar IV recusado). O que aconteceu vai para o report."""
        w, h = self.size
        engine = engine_of(self.cfg, self._photo_tag)
        tries = [(engine, w, h)]
        if (w, h) != (1280, 720):
            tries.append((engine, 1280, 720))
        if engine == "avatar_iv":
            tries.append(("standard", w, h))
        if self._photo_id and self.cfg.avatar_id and self.cfg.kind == "avatar":
            tries.append(("avatar", w, h))  # o ID não é foto falante (avatar de vídeo): gera como avatar mesmo
        errors: list[str] = []
        for eng, tw, th in tries:
            try:
                if eng == "avatar":
                    vid = self.client.generate(audio_asset, self.cfg, tw, th, "", "standard")
                    eng = "standard"
                else:
                    vid = self.client.generate(audio_asset, self.cfg, tw, th, self._photo_id, eng)
            except HeyGenError as exc:
                if "recusada" in str(exc):  # chave errada: não adianta tentar de outro jeito
                    raise
                errors.append(f"{eng} {tw}x{th}: {exc}")
                continue
            seg.engine = eng
            if errors:
                seg.note = " | ".join(errors)[-400:]
            return vid
        raise HeyGenError(" | ".join(errors)[-400:])

    def _limit(self) -> float:
        """Prazo da HeyGen. Enquanto o vídeo ainda busca e renderiza, esperar não custa nada: só desiste depois
        que o vídeo pede o avatar (antes, desistia 20 min depois do início, com o vídeo ainda 1 h longe do fim,
        e os clipes já pagos ficavam de fora)."""
        return self._deadline if self._needed else float("inf")

    def result(self) -> list[AvatarSegment]:
        """Espera a HeyGen (até ``wait_minutes`` desde o início, e no mínimo ``RESULT_GRACE`` segundos a partir
        de agora) e devolve os trechos com clipe pronto."""
        if self._thread is not None:
            self._deadline = max(self._deadline, time.monotonic() + RESULT_GRACE)
            self._needed = True  # depois do prazo: a thread lê o prazo novo, nunca o antigo
            self._thread.join(max(0.0, self._deadline - time.monotonic()) + 30)
            if self._thread.is_alive():
                for g in self.segments:
                    if g.status == "pending":
                        g.status, g.error = "timeout", "a HeyGen ainda não terminou"
        for g in self.segments:
            if g.status in ("failed", "timeout"):
                log.warning("avatar: trecho sem avatar", extra={"stage": "avatar", "momento": g.moment,
                                                                 "motivo": g.error})
        ready = [g for g in self.segments if g.path and g.status in ("ok", "cache")]
        if any(g.status == "ok" for g in self.segments):
            try:
                self.wallet = self.client.balance()
            except Exception:  # noqa: BLE001 - saldo é só informação
                self.wallet = None
        log.info("avatar pronto", extra={"stage": "avatar", "trechos": len(ready), "de": len(self.segments),
                                         "do_cache": sum(g.status == "cache" for g in ready)})
        return ready

    def report(self) -> list[dict[str, Any]]:
        return [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(g).items() if k != "path"}
                for g in self.segments]


def start_avatar(cfg: Any, audio: Path, sentences: list[Sentence], audio_seconds: float, cache_dir: Path,
                 work_dir: Path, base_dir: Path | None = None) -> AvatarJob | None:
    """Liga o avatar quando o canal tem um (imagem do personagem ou ID) e a chave da HeyGen está no ``.env``."""
    av: AvatarConfig = cfg.avatar
    photo = photo_file(av, base_dir or Path.cwd())
    if not av.enabled or not (photo or av.avatar_id):
        return None
    if not cfg.heygen_api_key:
        log.warning("avatar: o canal tem avatar mas falta a HEYGEN_API_KEY (Configurações)", extra={"stage": "avatar"})
        return None
    segments = pick_segments(sentences, av, audio_seconds)
    if not segments:
        return None
    channel_file = (base_dir or Path.cwd()) / "canais" / f"{cfg.channel.slug}.yaml" if cfg.channel.slug else None
    return AvatarJob(av, cfg.heygen_api_key, audio, segments, cache_dir / "avatar", work_dir / "avatar",
                     # vídeo em pé: a HeyGen gera em 16:9 (a imagem do personagem é deitada) e o bot recorta o
                     # meio, onde o personagem está, para a tela em pé (avatar_filter)
                     photo=photo, size=(max(cfg.output.width, cfg.output.height), min(cfg.output.width, cfg.output.height)),
                     prices=cfg.costs.heygen,
                     channel=cfg.channel.slug, in_use=_in_use(base_dir), channel_file=channel_file).start()


def remember_photo_id(channel_file: Path | None, photo_id: str, photo: Path | None) -> bool:
    """Guarda no canal (``avatar.avatar_id``) o ID da imagem que o bot acabou de enviar: nos próximos vídeos ele
    usa o ID direto, mesmo se o cache for apagado. ``id_photo`` diz de qual imagem ele é (trocou a imagem no app,
    o ID sai junto)."""
    if channel_file is None or not photo_id or not Path(channel_file).is_file():
        return False
    import yaml

    try:
        doc = yaml.safe_load(Path(channel_file).read_text(encoding="utf-8")) or {}
        av = doc.get("avatar") or {}
        if av.get("avatar_id"):
            return False
        av.update({"avatar_id": photo_id, "kind": "photo", "id_photo": str(av.get("photo") or (photo.name if photo else ""))})
        doc["avatar"] = av
        Path(channel_file).write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        log.info("avatar: ID da HeyGen guardado no canal (não envia a imagem de novo)",
                 extra={"stage": "avatar", "id": photo_id, "canal": Path(channel_file).stem})
        return True
    except Exception:  # noqa: BLE001 - guardar é um extra
        return False


def _in_use(base_dir: Path | None) -> set[str] | None:
    try:
        return photos_in_use(base_dir) if base_dir is not None else None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ montagem
def free_scenes(scenes: list[Scene], segments: list[AvatarSegment]) -> list[str]:
    """Tela cheia: a cena que passa mais da metade com o apresentador na tela fica sem template e sem overlay
    (ninguém veria; só o botão de inscrição continua, por cima dele)."""
    notes: list[str] = []
    for scene in scenes:
        covered = sum(max(0.0, min(g.end, scene.end) - max(g.start, scene.start)) for g in segments)
        if scene.duration <= 0 or covered <= scene.duration / 2:
            continue
        a = scene.analysis
        dropped = []
        if a.style not in ("normal", ""):
            dropped.append(a.style)
            a.style, a.template_seconds = "normal", 0.0
        if a.overlay and a.overlay.get("kind") != "subscribe":
            dropped.append(str(a.overlay.get("kind")))
            a.overlay = {}
        if dropped:
            notes.append(f"cena {scene.index}: apresentador em tela cheia; sem {', '.join(dropped)}")
    return notes


def attach(shots: list[Shot], segments: list[AvatarSegment], fps: int, full: bool = False) -> int:
    """Marca, em cada shot que cruza um trecho, qual clipe entra e a partir de que ponto dele.

    Em tela cheia o avatar ocupa shots inteiros: entra no shot que passa mais da metade dentro do trecho e a
    troca para o b-roll é a própria transição entre os shots (sem um shot vizinho com um pedacinho do avatar,
    o que fazia a transição parecer duas vezes)."""
    n = 0
    for shot in shots:
        t0, t1 = shot.start_frame / fps, shot.end_frame / fps
        if full:
            span = max(1e-6, shot.cut_out - shot.cut_in)
            hits = [g for g in segments if min(g.end, shot.cut_out) - max(g.start, shot.cut_in) > span / 2]
        else:
            hits = [g for g in segments if g.start < t1 and g.end > t0]
        shot.avatar = [{"path": g.path, "start": round(g.start, 4), "end": round(g.end, 4), "shot_start": t0,
                        "shot_end": t1, "moment": g.moment} for g in hits]
        n += bool(shot.avatar)
    return n


def _hex(color: str) -> str:
    return "0x" + color.lstrip("#").upper()


def avatar_filter(items: list[dict[str, Any]], cfg: AvatarConfig, w: int, h: int, fps: int) -> str:
    """Grafo do FFmpeg: [0:v] é o shot; [1:v], [2:v]... são os clipes do avatar (já cortados no ponto certo)."""
    full = cfg.layout == "full"
    vertical = h > w
    # tela em pé: o avatar do canto fica menor e acima da faixa de baixo (onde o app põe texto e botões)
    box_h = int(round(h * (min(cfg.size, 0.34) if vertical else cfg.size) / 2) * 2)
    margin = round(w * 0.025)
    x = "0" if full else f"W-w-{margin}" if cfg.position == "right" else str(margin)
    y = "0" if full else (f"H-h-{round(h * 0.13)}" if vertical else "H-h")
    graph, last = [], "0:v"
    for k, it in enumerate(items, start=1):
        t0 = it["shot_start"]
        skip = max(0.0, t0 - it["start"])  # já cortado no -ss da entrada
        delay = max(0.0, it["start"] - t0)  # o avatar entra no meio deste shot
        visible = it["end"] - it["start"] - skip  # quanto do clipe ainda falta, a partir do início deste shot
        chain = [f"[{k}:v]setpts=PTS-STARTPTS", f"fps={fps}", f"trim=end={max(0.05, visible):.3f}"]
        if full:  # o clipe já vem com o fundo: cobre a tela inteira
            chain += [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", "setsar=1"]
        else:
            chain += [f"crop=trunc(iw*{cfg.crop:.3f}/2)*2:ih:(iw-trunc(iw*{cfg.crop:.3f}/2)*2)/2:0",
                      f"chromakey={_hex(cfg.key_color)}:{cfg.similarity:.3f}:{cfg.blend:.3f}"]
            if cfg.key_color.upper() in ("#00FF00", "00FF00"):
                chain += ["format=rgba", "despill=type=green"]  # tira o reflexo verde do cabelo e dos ombros
            chain += [f"scale=-2:{box_h}"]
        if full:
            # cobre o shot inteiro: antes da fala começar e depois que ela termina fica o quadro parado do
            # avatar (a sincronia da boca não muda); a entrada e a saída são as transições dos shots
            dur = it.get("shot_end", t0 + visible) - t0
            chain += [f"tpad=start_mode=clone:start_duration={delay:.3f}:stop_mode=clone:stop_duration={dur + 1:.3f}",
                      f"trim=end={dur + 0.1:.3f}", "format=yuv420p"]
        else:
            chain.append("format=yuva420p")
            if skip < 0.05:
                chain.append(f"fade=t=in:st=0:d={FADE}:alpha=1")
            chain.append(f"fade=t=out:st={max(0.0, visible - FADE):.3f}:d={FADE}:alpha=1")
            if delay > 0:
                chain.append(f"setpts=PTS+{delay:.3f}/TB")
        graph.append(",".join(chain) + f"[a{k}]")
        out = f"o{k}"
        graph.append(f"[{last}][a{k}]overlay=x={x}:y={y}:eof_action=pass:format=auto[{out}]")
        last = out
    graph.append(f"[{last}]format=yuv420p[v]")
    return ";".join(graph)


def composite(base: Path, items: list[dict[str, Any]], dest: Path, cfg: AvatarConfig, w: int, h: int, fps: int,
              frames: int, encode: list[str]) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(base)]
    for it in items:
        skip = max(0.0, it["shot_start"] - it["start"])
        cmd += ["-ss", f"{skip:.3f}", "-i", it["path"]]
    cmd += ["-filter_complex", avatar_filter(items, cfg, w, h, fps), "-map", "[v]", "-frames:v", str(frames), "-an",
            *encode, str(dest)]
    run(cmd, timeout=600, stage="avatar")
    return dest
