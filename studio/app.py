"""Abre o nKiev Studio: janela própria (pywebview) ou, com ``--navegador``, no navegador padrão.

    python -m studio              # janela do app
    python -m studio --navegador  # mesma tela no navegador (se a janela não abrir neste PC)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .api import Api

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"
TITLE = "nKiev Studio"


def _tk_dialog(kind: str, file_types: tuple[str, ...]) -> str | None:
    """Janela de escolher arquivo no modo navegador (o navegador não entrega o caminho do arquivo)."""
    import tkinter
    from tkinter import filedialog

    pairs = []
    for ft in file_types:  # "Áudio (*.mp3;*.wav)" -> ("Áudio", "*.mp3 *.wav")
        label, _, pattern = ft.partition("(")
        pairs.append((label.strip(), pattern.rstrip(")").replace(";", " ")))
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askopenfilename(filetypes=pairs, parent=root)
    finally:
        root.destroy()
    return path or None


def window_size(screen: tuple[int, int] | None) -> tuple[int, int, tuple[int, int]]:
    """(largura, altura, tamanho mínimo) da janela, cabendo na tela: em notebook (768 de altura, com a barra de
    tarefas do Windows) a janela de 900 px ficava com os botões de baixo escondidos atrás da barra."""
    w, h = 1440, 900
    if screen and screen[0] > 0 and screen[1] > 0:
        w = min(w, screen[0] - 40)
        h = min(h, screen[1] - 90)  # barra de tarefas + barra de título
    w, h = max(w, 900), max(h, 560)
    return w, h, (min(1000, w), min(600, h))


def _screen() -> tuple[int, int] | None:
    try:
        import webview

        s = webview.screens[0]
        return int(s.width), int(s.height)
    except Exception:  # noqa: BLE001 - pywebview antigo: tamanho padrão
        return None


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # sem poluir o terminal
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.server.last_seen = time.time()  # type: ignore[attr-defined]
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                # a paleta escolhida já vai na página (sem piscar a roxa antes de carregar)
                tema = json.dumps(str(api._prefs().get("tema") or "grafite"))
                html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
                html = html.replace("<head>", f"<head><script>window.__TEMA={tema};</script>", 1)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path.startswith("/arquivo?"):
                self._file()
            elif self.path.startswith("/previa/"):
                self._preview()
            elif self.path.startswith("/personagem/"):
                self._character()
            else:
                self._send(404, b"", "text/plain")

        def _file(self) -> None:
            """Miniaturas do acompanhamento (só imagens do cache do bot)."""
            from urllib.parse import parse_qs, urlparse

            raw = parse_qs(urlparse(self.path).query).get("p", [""])[0]
            path = Path(raw).resolve()
            cache = (BASE_DIR / ".cache").resolve()
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp") or cache not in path.parents:
                self._send(403, b"", "text/plain")
                return
            ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            self._send(200, path.read_bytes(), ctype)

        def _preview(self) -> None:
            """Prévia de cada estilo na escolha do estilo (studio/web/previas, feitas por studio.previas)."""
            name = self.path.removeprefix("/previa/").split("?")[0]
            path = WEB_DIR / "previas" / name
            if not re.fullmatch(r"[a-z]+\.(?:jpg|webp)", name) or not path.is_file():
                self._send(404, b"", "text/plain")
                return
            self._send(200, path.read_bytes(), "image/webp" if name.endswith(".webp") else "image/jpeg")

        def _character(self) -> None:
            """Imagem do personagem (avatar) escolhida para o canal (canais/avatar)."""
            from urllib.parse import unquote

            name = unquote(self.path.removeprefix("/personagem/").split("?")[0])
            path = BASE_DIR / "canais" / "avatar" / name
            if not re.fullmatch(r"[\w.-]+\.(?:jpg|jpeg|png|webp)", name) or not path.is_file():
                self._send(404, b"", "text/plain")
                return
            self._send(200, path.read_bytes(), "image/png" if name.endswith(".png") else "image/jpeg")

        def do_POST(self) -> None:  # noqa: N802
            name = self.path.removeprefix("/api/")
            method = getattr(api, name, None)
            if name.startswith("_") or not callable(method):
                self._send(404, b"{}", "application/json")
                return
            length = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(length) or b"[]")
            try:
                result = method(*args)
            except Exception as exc:  # noqa: BLE001
                result = {"erro": f"{type(exc).__name__}: {exc}"}
            self._send(200, json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def serve(api: Api, port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    server.last_seen = time.time()  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="nKiev Studio")
    p.add_argument("--navegador", action="store_true", help="abre no navegador em vez da janela do app")
    p.add_argument("--porta", type=int, default=0, help="porta do modo navegador (padrão: qualquer livre)")
    args = p.parse_args(argv)

    api = Api(BASE_DIR, dialogs=_tk_dialog)  # uma fila só, seja na janela ou no navegador
    server = serve(api, args.porta)
    url = f"http://127.0.0.1:{server.server_port}/"
    if not args.navegador and _window(api, url):
        return 0
    print(f"nKiev Studio aberto em {url}  (fechou a aba e a fila acabou: o app fecha sozinho)")
    webbrowser.open(url)
    try:
        while not _idle(api, server):
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    return 0


def _idle(api: Api, server: ThreadingHTTPServer, grace: float = 90.0) -> bool:
    """Modo navegador: a aba fechou (a página pergunta da fila a cada 2,5 s) e não há vídeo na fila."""
    if time.time() - server.last_seen < grace:  # type: ignore[attr-defined]
        return False
    with api._queue.lock:
        return not any(j.status in ("fila", "produzindo") for j in api._queue.jobs)


def _window(api: Api, url: str) -> bool:
    """Janela própria (pywebview + WebView2 do Windows). False = não deu; o app abre no navegador."""
    try:
        import webview  # pywebview
    except ImportError:
        print("pywebview não instalado (pip install pywebview); abrindo no navegador.", file=sys.stderr)
        return False
    holder: dict[str, Any] = {}

    def dialogs(kind: str, file_types: tuple[str, ...]) -> str | None:
        kind_open = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        picked = holder["window"].create_file_dialog(kind_open, file_types=file_types)
        return picked[0] if picked else None

    tk_dialogs = api._dialogs
    api._dialogs = dialogs
    try:
        # a janela abre a página do nosso servidor local (o mesmo do modo navegador): a tela conversa com o
        # Python sempre pelo mesmo caminho, sem depender da ponte do pywebview estar pronta
        width, height, min_size = window_size(_screen())
        holder["window"] = webview.create_window(TITLE, url, width=width,
                                                 height=height, min_size=min_size, background_color="#0F0D17",
                                                 text_select=True,
                                                 confirm_close=True)
        webview.start(localization={"global.quitConfirmation":
                                    "Fechar o nKiev Studio? Se um vídeo estiver em produção, ele para."})
        api._queue.shutdown()
        return True
    except Exception as exc:  # noqa: BLE001 - sem WebView2/pythonnet: segue no navegador
        print(f"a janela do app não abriu ({exc}); abrindo no navegador.", file=sys.stderr)
        api._dialogs = tk_dialogs
        return False
