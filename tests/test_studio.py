"""nKiev Studio: estilos/ajustes por vídeo, fila em processo separado, canais, chaves e relatório."""

import json
import sys
import time
from pathlib import Path

import yaml

from broll_bot import progress
from broll_bot.channels import apply_channel, apply_overrides
from broll_bot.config import AppConfig, apply_pace
from broll_bot.looks import LOOKS, job_overrides
from studio import canais, chaves, relatorio
from studio.api import Api
from studio.jobs import DONE, FAILED, QUEUED, JobQueue, job_progress, last_error

ROOT = Path(__file__).resolve().parent.parent
FIXT = Path(__file__).resolve().parent / "fixtures"  # canais de exemplo fixos (os de canais/ mudam pelo app)


def _write(path: Path, data) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_style_removes_and_the_chosen_style_wins(tmp_path):
    cfg = AppConfig()
    apply_channel(cfg, FIXT / "canais" / "jhonny_walt.yaml")  # sem dossiê nem ficha
    over = job_overrides("dossie", rhythm="intenso", pace="rapido", statements=False, music=False, music_volume=0.2)
    apply_overrides(cfg, _write(tmp_path / "ajustes.yaml", over))
    apply_pace(cfg)
    assert "dossier" in cfg.templates.styles and "file" in cfg.overlays.kinds  # escolheu Dossiê: o papel entra
    assert "statement" not in cfg.templates.styles and cfg.templates.statement_chance == 0
    assert "vhs" not in cfg.templates.styles  # o estilo também tira
    assert cfg.templates.rhythm_gap == round(11 * 0.7, 1)
    assert cfg.timeline.pace == "fast" and cfg.timeline.max_clip_seconds == 4.2
    assert cfg.music.enabled is False and cfg.music.volume == 0.2
    assert cfg.channel.name == "Jhonny Walt"  # o perfil do canal continua


def test_every_look_builds_and_mix_keeps_everything(tmp_path):
    for name in LOOKS:
        over = job_overrides(name)
        assert set(over) <= {"templates", "timeline", "overlays", "music", "sfx", "review", "remover", "liberar",
                             "archive", "search"}
    # o Documentário liga o arquivo histórico e o filme antigo e tira o youtube_any, sem mexer no resto da busca
    doc = AppConfig()
    apply_overrides(doc, _write(tmp_path / "d.yaml", job_overrides("documentario")))
    assert doc.archive.enabled and doc.search.skip_providers == ["youtube_any"]
    assert doc.timeline.old_film_providers == ["archive"] and doc.search.provider_order == AppConfig().search.provider_order
    base = AppConfig()
    cfg = AppConfig()
    apply_overrides(cfg, _write(tmp_path / "a.yaml", job_overrides("mix")))
    assert cfg.templates.styles == base.templates.styles and cfg.overlays.kinds == base.overlays.kinds
    assert cfg.templates.rhythm_gap == base.templates.rhythm_gap
    # um pouco de TV e um pouco de papel
    assert cfg.templates.swap["statement"] == ["bulletin", "paperstatement"] and cfg.templates.swap_chance == 0.4


def test_overrides_cannot_touch_channel_or_keys(tmp_path):
    cfg = AppConfig()
    apply_overrides(cfg, _write(tmp_path / "a.yaml", {"canal": {"nome": "X"}, "providers": {"pexels": {"enabled": False}},
                                                      "pexels_api_key": None}))
    assert cfg.channel.name == "" and cfg.providers["pexels"].enabled


def test_progress_file_and_stages(tmp_path):
    path = tmp_path / "p.jsonl"
    progress.set_file(path)
    try:
        progress.step("transcricao")
        progress.step("cenas")
        progress.step("busca", 5, 10)
    finally:
        progress.set_file(None)
    p = job_progress(path)
    states = {s["id"]: s["state"] for s in p["stages"]}
    assert states["transcricao"] == "done" and states["busca"] == "now" and states["render"] == "next"
    assert 4 + 3 < p["percent"] < 4 + 3 + 45
    progress.step("render")  # sem arquivo: não faz nada
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"t": 1, "stage": "pronto"}\n{"meia linha')
    assert job_progress(path)["percent"] == 100.0


def _fake_bot(tmp_path: Path, body: str) -> Path:
    """Um main.py de mentira na base: grava progresso e relatório como o bot de verdade."""
    (tmp_path / "main.py").write_text(body, encoding="utf-8")
    return tmp_path


FAKE_OK = '''
import json, sys
args = sys.argv[1:]
get = lambda k: args[args.index(k) + 1]
with open(get("--progresso"), "a", encoding="utf-8") as fh:
    for st in ["transcricao", "cenas", "busca", "templates", "render", "montagem", "pronto"]:
        fh.write(json.dumps({"t": 1, "stage": st}) + "\\n")
open(get("--out"), "wb").write(b"video")
json.dump({"output": get("--out"), "scenes": [], "language": "en", "credits": []}, open(get("--report"), "w"))
'''


def _add(q: JobQueue, tmp_path: Path, **kw):
    audio = tmp_path / "voz.mp3"
    audio.write_bytes(b"x")
    return q.add(title=kw.get("title", "21 SUVs Built to Last"), channel="Jhonny Walt", channel_file="jhonny_walt",
                 style="minimal", settings={}, overrides=job_overrides("minimal"), script_text="roteiro " * 20,
                 audio=str(audio), videos_dir="videos/Jhonny Walt", audio_seconds=60)


def test_queue_runs_bot_in_a_separate_process(tmp_path):
    q = JobQueue(_fake_bot(tmp_path, FAKE_OK), python=sys.executable)
    job = _add(q, tmp_path)
    assert job.status == QUEUED and (tmp_path / job.folder / "ajustes.yaml").exists()
    assert job.output.endswith("21-suvs-built-to-last.mp4")
    cmd = q.command(job)
    assert cmd[cmd.index("--canal") + 1] == "jhonny_walt" and "--ajustes" in cmd and "--progresso" in cmd
    q.run(job)
    assert job.status == DONE and (tmp_path / job.output).exists()
    assert q.snapshot(job)["progress"]["percent"] == 100.0
    # a fila sobrevive a fechar o app
    again = JobQueue(tmp_path)
    assert again.get(job.id).status == DONE


def test_failed_job_shows_the_reason_and_can_retry(tmp_path):
    q = JobQueue(_fake_bot(tmp_path, 'print("✖ Nada foi renderizado: a IA não gerou o plano.")\nprint("  Motivo: sem créditos")\nraise SystemExit(1)\n'))
    job = _add(q, tmp_path)
    q.run(job)
    assert job.status == FAILED and "sem créditos" in job.error
    assert q.retry(job.id) and job.status == QUEUED
    assert q.cancel(job.id) and not q.retry("nao-existe")


def test_app_closed_mid_video_is_marked(tmp_path):
    q = JobQueue(tmp_path)
    job = _add(q, tmp_path)
    job.status = "produzindo"
    q._save()
    assert JobQueue(tmp_path).get(job.id).status == FAILED


def test_last_error_without_marker(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("12:00:00 INFO    [x] ok\n12:00:01 ERROR   [cli] falha no pipeline: ffmpeg sumiu\n", encoding="utf-8")
    assert last_error(log) == "falha no pipeline: ffmpeg sumiu"


def test_create_channel_and_presets(tmp_path):
    (tmp_path / "canais").mkdir()
    c = canais.create(tmp_path, "Top 10 Mundo", "pt", "R$", niche="variado", palette="preto")
    raw = yaml.safe_load((tmp_path / "canais" / "top_10_mundo.yaml").read_text(encoding="utf-8"))
    assert raw["canal"]["nome"] == "Top 10 Mundo" and raw["ranking"]["visual_checks"] is False
    assert "fuel" in raw["canal"]["sem_overlays"] and c["accent"] == "#22C55E"
    assert (tmp_path / "videos" / "Top 10 Mundo").is_dir()
    cfg = AppConfig()
    apply_channel(cfg, tmp_path / "canais" / "top_10_mundo.yaml")  # o bot lê o canal criado no app
    assert cfg.channel.language == "pt" and not cfg.ranking.visual_checks and "fuel" not in cfg.overlays.kinds
    try:
        canais.create(tmp_path, "top 10 mundo", "pt", "R$")
        raise AssertionError("nome repetido")
    except ValueError:
        pass
    canais.save_preset(tmp_path, "top_10_mundo", "Ranking padrão", {"style": "kinetic", "rhythm": "intenso"})
    assert canais.describe(tmp_path / "canais" / "top_10_mundo.yaml")["presets"]["Ranking padrão"]["style"] == "kinetic"


def test_edit_channel_renames_and_keeps_the_rest(tmp_path):
    from studio.api import Api

    (tmp_path / "canais").mkdir()
    text = (FIXT / "canais" / "jhonny_walt.yaml").read_text(encoding="utf-8") + "templates:\n  rhythm_gap: 9\n"
    (tmp_path / "canais" / "jhonny_walt.yaml").write_text(text, encoding="utf-8")
    (tmp_path / "videos" / "Jhonny Walt").mkdir(parents=True)
    (tmp_path / "videos" / "Jhonny Walt" / "old.mp4").write_bytes(b"v")
    canais.save_preset(tmp_path, "jhonny_walt", "Padrão", {"style": "broadcast"})
    api = Api(tmp_path, start_queue=False)
    job = _add(api._queue, tmp_path)
    before = canais.describe(tmp_path / "canais" / "jhonny_walt.yaml")
    assert before["palette"] == "cinza" and before["paper"] is False
    ch = api.editar_canal("jhonny_walt", {"name": "Walt Motors UK", "language": "en", "currency": "£",
                                          "niche": "automotivo", "palette": "preto", "paper": True})
    assert ch["name"] == "Walt Motors UK" and ch["file"] == "jhonny_walt" and ch["accent"] == "#22C55E"
    assert ch["videos_dir"] == "videos/Walt Motors UK" and ch["paper"] is True and ch["presets"]["Padrão"]
    assert (tmp_path / "videos" / "Walt Motors UK" / "old.mp4").exists()  # a pasta foi junto
    raw = yaml.safe_load((tmp_path / "canais" / "jhonny_walt.yaml").read_text(encoding="utf-8"))
    assert raw["templates"]["rhythm_gap"] == 9  # o resto do arquivo fica
    j = api._queue.get(job.id)
    assert j.channel == "Walt Motors UK" and j.output.replace("\\", "/").startswith("videos/Walt Motors UK/")
    cfg = AppConfig()
    apply_channel(cfg, tmp_path / "canais" / "jhonny_walt.yaml")
    assert cfg.channel.name == "Walt Motors UK"
    assert "erro" in api.editar_canal("nao_existe", {"name": "X", "language": "en"})
    canais.create(tmp_path, "Outro", "fr", "€")
    assert "erro" in api.editar_canal("jhonny_walt", {"name": "outro", "language": "en"})  # nome repetido
    j.status = "produzindo"  # renomear com um vídeo do canal produzindo: espera terminar
    assert "erro" in api.editar_canal("jhonny_walt", {"name": "Novo Nome", "language": "en"})
    assert "erro" not in api.editar_canal("jhonny_walt", {"name": "Walt Motors UK", "language": "en", "currency": "$"})


def test_bernard_keeps_the_original_colors():
    c = canais.describe(FIXT / "canais" / "bernard_chevalier.yaml")
    assert c["accent"] == "#FFCC00" and c["blocked"] == []


def test_env_keys_are_kept_and_masked(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comentário\nPEXELS_API_KEY=abc\nOUTRA=1\n", encoding="utf-8")
    chaves.write_env(env, "PEXELS_API_KEY", "nova-chave-123456")
    chaves.write_env(env, "WIKIMEDIA_CONTACT", "eu@exemplo.com")
    text = env.read_text(encoding="utf-8")
    assert "# comentário" in text and "OUTRA=1" in text and text.count("PEXELS_API_KEY") == 1
    rows = {r["env"]: r for r in chaves.summary(env)}
    assert rows["PEXELS_API_KEY"]["shown"] == "nova••••••••••3456" and rows["WIKIMEDIA_CONTACT"]["shown"] == "eu@exemplo.com"
    assert chaves.test_key("WIKIMEDIA_CONTACT", "eu@exemplo.com")[0]
    assert chaves.test_key("PEXELS_API_KEY", "") == (False, "Falta preencher")


def shot(i, mid, t):
    return {"shot": i, "media_id": mid, "source": "pexels", "cut_in": t, "fallback": None}


def _report():
    return {"language": "en", "subjects": ["Toyota RAV4", "Honda CR-V"], "references": {"Toyota RAV4": 4},
            "scenes": [
                {"start": 0, "narrated_text": "There are twenty one SUVs that never break down.", "shots": [shot(0, "a", 0)]},
                {"start": 72.4, "narrated_text": "Number 21: the Toyota RAV4, 2006 to 2012.", "shots": [shot(1, "b", 72)]},
                {"start": 101, "narrated_text": "It has a timing chain.", "shots": [shot(2, "a", 101)]},
                {"start": 125.2, "narrated_text": "Number 20: the Honda CR-V, third generation.", "shots": [shot(3, "c", 125)]},
            ],
            "credits": [{"source": "wikimedia", "author": "Mr. Choppers", "url": "https://commons.wikimedia.org/x"}]}


def test_report_tabs():
    r = _report()
    caps = relatorio.chapters(r)
    assert caps == [("00:00", "Intro"), ("01:12", "#21 Toyota RAV4"), ("02:05", "#20 Honda CR-V")]
    diag = relatorio.diagnose(r)
    texts = " | ".join(d["text"] for d in diag)
    assert "1 de 2 itens com fotos de referência" in texts and "3 imagens diferentes em 4" in texts
    assert "Wikimedia Commons:\nMr. Choppers – https://commons.wikimedia.org/x" == relatorio.credits_text(r)


def test_api_end_to_end(tmp_path):
    base = _fake_bot(tmp_path, FAKE_OK)
    (base / "canais").mkdir()
    for name in ("jhonny_walt.yaml", "bernard_chevalier.yaml"):
        (base / "canais" / name).write_text((FIXT / "canais" / name).read_text(encoding="utf-8"), encoding="utf-8")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    picks = iter([str(audio)])
    api = Api(base, dialogs=lambda kind, types: next(picks), start_queue=False)
    start = api.inicio()
    assert [c["name"] for c in start["canais"]] == ["Bernard Chevalier", "Jhonny Walt"] and len(start["estilos"]) == 8
    info = api.analisar_roteiro("Number 21: the Toyota RAV4. It is the most reliable car you can buy in Britain today.")
    assert info["lang"] == "en" and info["channel"] == "jhonny_walt"
    assert api.escolher_audio()["name"] == "narration.mp3"
    assert "erro" in api.produzir({"script": "curto", "audio": str(audio), "channel": "jhonny_walt"})
    assert "erro" in api.produzir({"script": "word " * 50, "audio": str(audio), "channel": "nao_existe"})
    job = api.produzir({"title": "21 SUVs", "script": "word " * 50, "audio": str(audio), "channel": "jhonny_walt",
                        "style": "cinema", "settings": {"vol": 10, "music": True}})
    over = yaml.safe_load((base / job["folder"] / "ajustes.yaml").read_text(encoding="utf-8"))
    assert over["music"]["volume"] == 0.1 and "news" in over["remover"]["templates"]
    api._queue.run(api._queue.get(job["id"]))
    deadline = time.time() + 5
    while api.fila()[0]["status"] != DONE and time.time() < deadline:
        time.sleep(0.05)
    det = api.detalhes(job["id"])
    assert det["video"]["name"].endswith("_21-suvs.mp4") and det["chapters"] == []
    # produzir novamente: roteiro, áudio, título e canal voltam; estilo e ajustes vêm preenchidos
    again = api.reabrir(job["id"])
    assert again["title"] == "21 SUVs" and again["channel"] == "jhonny_walt" and again["style"] == "cinema"
    assert again["script"].split() == ["word"] * 50 and again["audio"]["name"] == "narration.mp3"
    assert again["settings"] == {"vol": 10, "music": True} and again["aviso"] == ""
    second = api.produzir({"title": again["title"], "script": again["script"], "audio": again["audio"]["path"],
                           "channel": again["channel"], "style": "broadcast", "settings": again["settings"]})
    assert second["id"] != job["id"] and second["folder"] != job["folder"] and len(api.fila()) == 2
    audio.unlink()
    assert "não está mais" in api.reabrir(job["id"])["aviso"] and api.reabrir(job["id"])["audio"] is None
    assert "erro" in api.reabrir("nao-existe")
    assert "erro" in api.salvar_preferencia("outra", 1) and api.salvar_preferencia("ia", "openai")["ia"] == "openai"
    assert json.loads((base / "projetos" / "studio.json").read_text())["ia"] == "openai"


def test_closing_the_app_stops_the_running_video(tmp_path):
    import threading

    q = JobQueue(_fake_bot(tmp_path, "import time\ntime.sleep(60)\n"))
    job = _add(q, tmp_path)
    t = threading.Thread(target=q.run, args=(job,))
    t.start()
    deadline = time.time() + 10
    while q.proc is None and time.time() < deadline:
        time.sleep(0.05)
    q.shutdown()
    t.join(15)
    assert not t.is_alive() and job.status == FAILED and "app foi fechado" in job.error


def test_window_loads_the_page_from_the_local_server(tmp_path, monkeypatch):
    """A janela abre a página pelo nosso servidor (antes vinha do servidor do pywebview e /api não existia)."""
    import types
    import urllib.request

    from studio import app

    seen = {}

    class Win:
        def create_file_dialog(self, kind, file_types=()):
            return ["/tmp/voz.mp3"]

    def create_window(title, url, **kw):
        seen["url"] = url
        return Win()

    def start(**kw):
        req = urllib.request.Request(seen["url"] + "api/estilos", data=b"[]", method="POST")
        seen["estilos"] = json.loads(urllib.request.urlopen(req).read())

    fake = types.SimpleNamespace(FileDialog=types.SimpleNamespace(OPEN=10), create_window=create_window, start=start)
    monkeypatch.setitem(sys.modules, "webview", fake)
    api = Api(tmp_path, start_queue=False)
    server = app.serve(api)
    try:
        assert app._window(api, f"http://127.0.0.1:{server.server_port}/")
        assert len(seen["estilos"]) == 8 and api._dialogs("open", ("Áudio (*.mp3)",)) == "/tmp/voz.mp3"
    finally:
        server.shutdown()


def test_windows_subprocesses_open_without_a_terminal_window():
    from broll_bot.winproc import CREATE_NO_WINDOW, hide_console_windows

    class FakePopen:
        def __init__(self, cmd, creationflags=0, **kw):
            self.flags = creationflags

    assert hide_console_windows(FakePopen) and not hide_console_windows(FakePopen)  # só uma vez
    assert FakePopen(["ffmpeg"]).flags == CREATE_NO_WINDOW
    assert FakePopen(["taskkill"], creationflags=0x200).flags == 0x200  # quem escolheu continua mandando


def test_delete_failed_job_removes_project_but_not_outside(tmp_path):
    q = JobQueue(tmp_path)
    job = _add(q, tmp_path)
    folder = tmp_path / job.folder
    assert folder.is_dir()
    job.status = FAILED
    assert q.remove(job.id, delete_project=True)
    assert not folder.exists() and q.get(job.id) is None
    assert (tmp_path / "voz.mp3").exists()  # o áudio do usuário fica
    other = _add(q, tmp_path, title="outro")
    other.status, other.folder = FAILED, str(tmp_path)  # pasta fora de projetos/: nunca apaga
    assert q.remove(other.id, delete_project=True) and tmp_path.exists()


def test_delete_done_job_frees_what_it_downloaded(tmp_path):
    """Excluir um vídeo pronto apaga as imagens/vídeos baixados durante a produção dele e a pasta de trabalho;
    o que outro vídeo baixou, o avatar/personagem (pagos) e o vídeo pronto ficam."""
    import os

    q = JobQueue(tmp_path)
    job = _add(q, tmp_path)
    t = time.time() - 3600
    job.status, job.started, job.finished = DONE, t, t + 1200
    media = tmp_path / ".cache" / "media" / "pexels"
    media.mkdir(parents=True)
    mine, older = media / "a.mp4", media / "b.mp4"
    for f, when in ((mine, t + 300), (older, t - 86400)):
        f.write_bytes(b"x" * 1000)
        os.utime(f, (when, when))
    paid = tmp_path / ".cache" / "avatar" / "c.mp4"
    paid.parent.mkdir(parents=True)
    paid.write_bytes(b"x")
    os.utime(paid, (t + 300, t + 300))
    run = tmp_path / ".work" / time.strftime("%Y%m%d-%H%M%S", time.localtime(t + 5))
    run.mkdir(parents=True)
    (run / "narracao_mix.wav").write_bytes(b"x" * 5000)
    video = tmp_path / "videos" / "pronto.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"v")
    freed = q.delete(job.id)
    assert freed >= 6000 and not mine.exists() and not run.exists()
    assert older.exists() and paid.exists() and video.exists() and q.get(job.id) is None
    assert q.delete("nao-existe") is None


def test_old_runs_leftovers_are_dropped(tmp_path):
    from broll_bot.disk import clean_old_work

    old, cur = tmp_path / "20260101-100000", tmp_path / "20260102-100000"
    for run in (old, cur):
        (run / "render").mkdir(parents=True)
        (run / "avatar").mkdir()
        (run / "narracao_mix.wav").write_bytes(b"x" * 10)
        (run / "pipeline.jsonl").write_text("{}")
    clean_old_work(tmp_path, keep=cur)
    assert sorted(p.name for p in old.iterdir()) == ["pipeline.jsonl"]  # o log fica
    assert (cur / "narracao_mix.wav").exists()


def test_app_palette_is_saved_and_injected(tmp_path):
    import urllib.request

    from studio import app

    api = Api(tmp_path, start_queue=False)
    assert "erro" in api.salvar_preferencia("tema", "rosa</script>")
    assert api.salvar_preferencia("tema", "ardosia")["tema"] == "ardosia"
    server = app.serve(api)
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/").read().decode("utf-8")
    finally:
        server.shutdown()
    assert '<head><script>window.__TEMA="ardosia";</script>' in html


def test_usage_cost_estimate_and_steps():
    from broll_bot import usage

    usage.reset()
    usage.record("openai", "gpt-5-mini", 1_000_000, 100_000, "plano de cenas")
    usage.record("anthropic", "claude-opus-5-5", 10_000, 2_000, "revisão das imagens", cache_read=100_000)
    usage.record("openai", "modelo-desconhecido", 5, 5, "outros")
    s = usage.summary()
    assert s["steps"]["plano de cenas"]["usd"] == round(0.25 + 0.2, 4)
    assert s["steps"]["revisão das imagens"]["usd"] == round((10_000 + 10_000) * 4 / 1e6 + 2_000 * 20 / 1e6, 4)
    assert s["unpriced"] == ["modelo-desconhecido"] and s["calls"] == 3
    assert usage.summary({"modelo-desconhecido": [1000, 1000]})["unpriced"] == []  # preço do config.yaml
    assert usage.price_of("gpt-5-mini-2026-08-07") == (0.25, 2.0)  # versão datada usa o preço da família
    usage.reset()
    assert usage.summary()["usd"] == 0


def test_openai_client_records_usage():
    from types import SimpleNamespace

    from broll_bot import usage
    from broll_bot.llm_openai import OpenAIJSON

    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500),
                           choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"a": 1}'))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: resp)))
    usage.reset()
    assert OpenAIJSON("gpt-5-mini", client=client, step="plano de cenas").complete("s", "p") == {"a": 1}
    assert usage.summary()["steps"]["plano de cenas"]["input"] == 1000
    usage.reset()


def test_done_videos_and_total_costs(tmp_path):
    q_dir = tmp_path / "projetos" / "v1"
    q_dir.mkdir(parents=True)
    (q_dir / "report.json").write_text(json.dumps({"video_duration": 600, "totals": {"api_requests": {"pexels": 9}},
                                                   "usage": {"usd": 0.4, "calls": 3, "steps": {}}}), encoding="utf-8")
    rows = [{"id": "v1", "title": "A", "channel": "Jhonny Walt", "channel_file": "jhonny_walt", "style": "mix",
             "settings": {}, "folder": "projetos/v1", "script": "x", "audio": "y", "output": "z", "status": "pronto"},
            {"id": "v2", "title": "B", "channel": "Jhonny Walt", "channel_file": "jhonny_walt", "style": "mix",
             "settings": {}, "folder": "projetos/v2", "script": "x", "audio": "y", "output": "z", "status": "pronto",
             "audio_seconds": 300}]
    (tmp_path / "projetos" / "fila.json").write_text(json.dumps(rows), encoding="utf-8")
    api = Api(tmp_path, start_queue=False)
    assert api._queue.get("v1").usage["usd"] == 0.4  # lido do relatório ao abrir
    c = api.custos()
    assert c == {"videos": 2, "minutes": 15.0, "usd": 0.4, "usd_month": c["usd_month"], "counted": 1,
                 "by_channel": {"Jhonny Walt": 0.4}}


def test_chosen_style_wins_over_channel_restriction(tmp_path):
    """Jhonny Walt não usa papel, mas se o vídeo é Dossiê, o papel entra (a escolha do vídeo vence)."""
    cfg = AppConfig()
    apply_channel(cfg, FIXT / "canais" / "jhonny_walt.yaml")
    assert "paperfloat" not in cfg.templates.styles and "file" not in cfg.overlays.kinds
    apply_overrides(cfg, _write(tmp_path / "a.yaml", job_overrides("dossie")))
    assert {"paperfloat", "paperrank", "papertimeline", "dossier"} <= set(cfg.templates.styles)
    assert {"file", "papertag", "paperyears"} <= set(cfg.overlays.kinds)
    assert "vhs" not in cfg.templates.styles  # o que o estilo tira continua fora
    cfg2 = AppConfig()
    apply_channel(cfg2, FIXT / "canais" / "jhonny_walt.yaml")
    apply_overrides(cfg2, _write(tmp_path / "b.yaml", job_overrides("broadcast")))
    assert "paperfloat" not in cfg2.templates.styles  # outro estilo: o canal continua sem papel


def test_style_previews_and_ui_preferences(tmp_path):
    """Prévia de cada estilo servida pelo app e as preferências de som/fundo guardadas."""
    import urllib.request

    from studio.api import Api
    from studio.app import WEB_DIR, serve

    for style in ("minimal", "dossie", "broadcast", "dados", "kinetic", "cinema", "mix"):
        assert (WEB_DIR / "previas" / f"{style}.jpg").is_file() and (WEB_DIR / "previas" / f"{style}.webp").is_file()
    api = Api(tmp_path, start_queue=False)
    server = serve(api)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/previa/dossie.webp") as r:
            assert r.headers["Content-Type"] == "image/webp" and r.read(4) == b"RIFF"
        for bad in ("/previa/../app.py", "/previa/x.py", "/previa/nada.jpg"):
            try:
                urllib.request.urlopen(base + bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
    finally:
        server.shutdown()
    assert api.salvar_preferencia("som", 0)["som"] is False
    assert api.salvar_preferencia("fundo", True)["fundo"] is True


def test_window_fits_laptop_screen():
    from studio.app import window_size

    w, h, (mw, mh) = window_size((1366, 768))  # notebook com a barra de tarefas do Windows
    assert h <= 768 - 90 and w <= 1366 and mh <= h and mw <= w
    assert window_size((2560, 1440))[:2] == (1440, 900)
    assert window_size(None)[:2] == (1440, 900)


def test_new_channel_with_the_old_name_of_a_renamed_one(tmp_path):
    """O canal "Jhonny Walt" virou "Graham Mercer" (o arquivo continua jhonny_walt.yaml): criar um "Jhonny Walt"
    novo funciona e ganha outro arquivo; o mesmo nome de canal continua proibido."""
    import pytest

    canais.create(tmp_path, "Jhonny Walt", "en", "£")
    canais.update(tmp_path, "jhonny_walt", "Graham Mercer", "en", "£")
    novo = canais.create(tmp_path, "Jhonny Walt", "en", "£")
    assert novo["file"] == "jhonny_walt_2" and novo["name"] == "Jhonny Walt"
    assert {c["name"] for c in canais.list_all(tmp_path)} == {"Graham Mercer", "Jhonny Walt"}
    with pytest.raises(ValueError):
        canais.create(tmp_path, "Graham Mercer", "en", "£")


def test_heygen_id_saved_by_bot_goes_away_when_the_image_changes(tmp_path):
    """O ID que o bot guardou no canal é da imagem enviada: trocou a imagem no app, o ID sai (senão a HeyGen
    animaria a imagem antiga). O ID digitado/escolhido à mão fica."""
    import yaml

    c = canais.create(tmp_path, "Dale", "en", "C$")
    path = tmp_path / "canais" / f"{c['file']}.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["avatar"] = {"photo": "canais/avatar/a.jpg", "avatar_id": "tp_auto", "kind": "photo",
                     "id_photo": "canais/avatar/a.jpg"}
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    keep = {"id": "tp_auto", "kind": "photo", "photo": "canais/avatar/a.jpg"}
    canais.update(tmp_path, c["file"], "Dale", "en", "C$", avatar=keep)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["avatar"]["avatar_id"] == "tp_auto"
    canais.update(tmp_path, c["file"], "Dale", "en", "C$", avatar={**keep, "photo": "canais/avatar/b.jpg"})
    av = yaml.safe_load(path.read_text(encoding="utf-8"))["avatar"]
    assert av["avatar_id"] == "" and av["photo"] == "canais/avatar/b.jpg"
    canais.update(tmp_path, c["file"], "Dale", "en", "C$", avatar={"id": "tp_manual", "kind": "photo",
                                                                    "photo": "canais/avatar/c.jpg"})
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["avatar"]["avatar_id"] == "tp_manual"


def test_director_can_be_turned_off_per_computer(tmp_path):
    api = Api(tmp_path, start_queue=False)
    assert api.salvar_preferencia("diretor", False)["diretor"] is False
    assert api.salvar_preferencia("diretor", True)["diretor"] is True


def test_diagnosis_says_when_the_image_review_did_not_run():
    from studio.relatorio import diagnose

    off = diagnose({"review": {"mode": "api", "scenes_sent": 12, "verdicts": 0,
                               "errors": ["a API da OpenAI falhou: 400 image too large"]}})
    assert any(d["level"] == "aviso" and "não rodou" in d["text"] and "400" in d["text"] for d in off)
    ok = diagnose({"review": {"mode": "api", "scenes_sent": 12, "verdicts": 80, "errors": []}})
    assert any(d["level"] == "ok" and "80 imagens" in d["text"] for d in ok)
    assert not any("Revisão" in d["text"] or "revisão" in d["text"] for d in diagnose({"review": {"mode": "off"}}))
