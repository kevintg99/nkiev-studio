"""Chaves das APIs no ``.env`` (só neste computador; cada pessoa usa as suas) e o botão "Testar"."""

from __future__ import annotations

import re
from pathlib import Path

import requests

KEYS = [
    {"env": "OPENAI_API_KEY", "name": "OpenAI (ChatGPT)", "use": "Plano de cenas, revisão e descrição"},
    {"env": "ANTHROPIC_API_KEY", "name": "Anthropic (Claude)", "use": "Plano de cenas, revisão e descrição"},
    {"env": "PEXELS_API_KEY", "name": "Pexels", "use": "Vídeos e fotos"},
    {"env": "PIXABAY_API_KEY", "name": "Pixabay", "use": "Vídeos e fotos"},
    {"env": "HEYGEN_API_KEY", "name": "HeyGen", "use": "Avatar apresentador (abertura, inscrição e final)"},
    {"env": "WIKIMEDIA_CONTACT", "name": "Wikimedia (seu e-mail)", "use": "Fotos dos modelos exatos", "plain": True},
    {"env": "YOUTUBE_API_KEY", "name": "YouTube (Creative Commons)", "use": "Vídeos CC do YouTube (reserva)"},
    {"env": "YOUTUBE_COOKIES_FROM_BROWSER", "name": "YouTube — navegador logado", "plain": True,
     "use": "firefox: o YouTube não barra o download como robô"},
    {"env": "SERPER_API_KEY", "name": "Serper (Google Imagens)",
     "use": "Fotos dos produtos, lojas e pessoas do roteiro (serper.dev, 2.500 grátis)"},
    {"env": "OPENVERSE_CLIENT_ID", "name": "Openverse (opcional)", "use": "Fotos CC: limite maior", "plain": True},
    {"env": "OPENVERSE_CLIENT_SECRET", "name": "Openverse — segredo", "use": "Fotos CC: limite maior"},
]
NAMES = {k["env"] for k in KEYS}
_LINE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#"):
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return values


def write_env(path: Path, name: str, value: str) -> None:
    """Troca (ou acrescenta) uma linha, sem mexer nas outras nem nos comentários."""
    if name not in NAMES:
        raise ValueError(f"chave desconhecida: {name}")
    value = value.strip().replace("\n", "")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m and m.group(1) == name and not line.lstrip().startswith("#"):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask(value: str) -> str:
    if not value:
        return ""
    return value if len(value) <= 8 else value[:4] + "•" * 10 + value[-4:]


def summary(path: Path) -> list[dict[str, str]]:
    env = read_env(path)
    out = []
    for k in KEYS:
        value = env.get(k["env"], "")
        out.append({**k, "plain": bool(k.get("plain")), "filled": bool(value),
                    "shown": value if k.get("plain") else mask(value)})
    return out


def test_key(name: str, value: str, timeout: float = 15.0, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """(funcionou?, mensagem curta). Faz a menor chamada possível em cada serviço."""
    value = (value or "").strip()
    if not value:
        return False, "Falta preencher"
    try:
        if name == "WIKIMEDIA_CONTACT":
            ok = "@" in value or value.startswith("http")
            return ok, "Ok" if ok else "Use um e-mail ou o link do canal"
        if name == "PEXELS_API_KEY":
            r = requests.get("https://api.pexels.com/v1/search", params={"query": "car", "per_page": 1},
                             headers={"Authorization": value}, timeout=timeout)
        elif name == "PIXABAY_API_KEY":
            r = requests.get("https://pixabay.com/api/", params={"key": value, "q": "car", "per_page": 3}, timeout=timeout)
        elif name == "YOUTUBE_API_KEY":
            r = requests.get("https://www.googleapis.com/youtube/v3/search",
                             params={"part": "id", "q": "nature", "type": "video", "maxResults": 1,
                                     "videoLicense": "creativeCommon", "key": value}, timeout=timeout)
            if r.status_code == 400:
                return False, "Chave recusada"
            if r.status_code == 403:
                try:
                    reason = str(r.json()["error"]["errors"][0].get("reason", ""))
                except (ValueError, KeyError, IndexError, TypeError):
                    reason = ""
                if reason in ("quotaExceeded", "dailyLimitExceeded"):
                    return True, "Conectada (cota do dia acabou; volta amanhã)"
                return False, "Ative a YouTube Data API v3 no Google Cloud" if "accessNotConfigured" in reason \
                    else "Chave recusada"
        elif name == "YOUTUBE_COOKIES_FROM_BROWSER":
            ok = value.lower() in ("firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium", "safari")
            if not ok:
                return False, "Use firefox (ou chrome, edge, brave)"
            if value.lower() != "firefox":
                return True, "Salvo (no Windows o Chrome/Edge trancam os cookies: prefira o Firefox)"
            return True, "Salvo: o bot usa o login do Firefox"
        elif name in ("OPENVERSE_CLIENT_ID", "OPENVERSE_CLIENT_SECRET"):
            env = env or {}
            cid = value if name == "OPENVERSE_CLIENT_ID" else env.get("OPENVERSE_CLIENT_ID", "")
            secret = value if name == "OPENVERSE_CLIENT_SECRET" else env.get("OPENVERSE_CLIENT_SECRET", "")
            if not (cid and secret):
                return True, "Salvo (falta o outro campo para testar)"
            r = requests.post("https://api.openverse.org/v1/auth_tokens/token/", timeout=timeout,
                              data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret})
            if r.status_code in (400, 401):
                return False, "Recusada (confirmou o e-mail do Openverse?)"
        elif name == "OPENAI_API_KEY":
            r = requests.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {value}"},
                             timeout=timeout)
        elif name == "ANTHROPIC_API_KEY":
            r = requests.get("https://api.anthropic.com/v1/models",
                             headers={"x-api-key": value, "anthropic-version": "2023-06-01"}, timeout=timeout)
        elif name == "SERPER_API_KEY":  # gasta 1 das buscas da conta
            r = requests.post("https://google.serper.dev/images", json={"q": "car battery", "num": 1},
                              headers={"X-API-KEY": value}, timeout=timeout)
        elif name == "HEYGEN_API_KEY":  # mostra o saldo da carteira pré-paga da API junto
            r = requests.get("https://api.heygen.com/v3/users/me", headers={"X-Api-Key": value}, timeout=timeout)
            if r.status_code == 200:
                from broll_bot.avatar import wallet_usd

                try:
                    body = r.json()
                except ValueError:
                    body = {}
                bal = wallet_usd((body.get("data") if isinstance(body, dict) else None) or body)
                return True, f"Conectada · saldo US$ {bal:.2f}".replace(".", ",") if bal is not None else "Conectada"
            if r.status_code == 404:  # sem a rota v3: testa só a chave
                r = requests.get("https://api.heygen.com/v2/avatars", headers={"X-Api-Key": value}, timeout=timeout)
        else:
            return False, "Chave desconhecida"
    except requests.RequestException as exc:
        return False, f"Sem conexão ({type(exc).__name__})"
    if r.status_code == 200:
        return True, "Conectada"
    if r.status_code in (401, 403):
        return False, "Chave recusada"
    if r.status_code == 429:
        return True, "Conectada (limite de uso atingido agora)"
    return False, f"Erro {r.status_code}"
