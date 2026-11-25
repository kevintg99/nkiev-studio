"""Linha de comando: python main.py --audio narracao.mp3 --script roteiro.txt --out video_final.mp4"""

from __future__ import annotations

import argparse
import sys
import shutil
import time
from pathlib import Path

from . import progress
from .channels import apply_channel, apply_overrides, channel_for_language, find_channel, list_channels
from .config import apply_pace, load_config
from .lang import detect_language
from .log import get_logger, setup_logging
from .pipeline import RunOptions, run_pipeline
from .disk import DiskFullError
from .scenes import AnalysisError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gera um vídeo com b-rolls sincronizados à narração (sem legendas).")
    p.add_argument("--audio", type=Path, help="narração em .mp3 (ou outro formato de áudio)")
    p.add_argument("--script", type=Path, help="roteiro em .txt ou .md")
    p.add_argument("--out", type=Path,
                   help="arquivo de saída (padrão: na pasta de vídeos do canal; sem canal, video_final.mp4)")
    p.add_argument("--canal", help='perfil do canal em canais/ ("Jhonny Walt" ou jhonny_walt); '
                                   "sem isso, o canal do idioma do roteiro")
    p.add_argument("--canais", action="store_true", help="lista os canais configurados e sai")
    p.add_argument("--ajustes", type=Path, metavar="ajustes.yaml",
                   help="estilo e ajustes deste vídeo por cima do canal (o app nKiev Studio grava este arquivo)")
    p.add_argument("--progresso", type=Path, metavar="ARQUIVO",
                   help="grava o andamento (uma linha JSON por etapa) para o app acompanhar")
    p.add_argument("--config", default=Path("config.yaml"), type=Path, help="arquivo de configuração")
    p.add_argument("--env", default=Path(".env"), type=Path, help="arquivo .env com as chaves das APIs")
    p.add_argument("--seed", type=int, help="sobrescreve a seed (resultado reproduzível)")
    p.add_argument("--no-llm", action="store_true", help="usa a análise heurística em vez do Claude")
    p.add_argument("--no-clip", action="store_true", help="desativa o ranking visual com CLIP")
    p.add_argument("--no-transcribe", action="store_true", help="não transcreve; estima o tempo das frases")
    p.add_argument("--reindex-library", action="store_true", help="reindexa a biblioteca local (Mixkit)")
    p.add_argument("--no-sfx", action="store_true", help="sem efeitos sonoros")
    p.add_argument("--no-music", action="store_true", help="sem música de fundo")
    p.add_argument("--lang", choices=["auto", "pt", "fr", "pl", "en", "es"],
                   help="idioma do roteiro (padrão: auto = detecta sozinho)")
    p.add_argument("--music", type=Path, metavar="FAIXA",
                   help="usa esta faixa como música do vídeo todo (senão escolhe por clima em library/music)")
    p.add_argument("--encoder", choices=["auto", "qsv", "nvenc", "amf", "cpu"],
                   help="codificador do vídeo: auto = chip de vídeo se houver (QuickSync/NVENC/AMF), cpu = processador")
    p.add_argument("--pace", choices=["normal", "fast"], help="ritmo de corte: fast = planos de 1,3–4 s")
    p.add_argument("--keep-temp", action="store_true", help="mantém os arquivos intermediários")
    p.add_argument("--report", type=Path, help="caminho do report.json (padrão: ao lado do vídeo)")
    p.add_argument("--export-prompt", nargs="?", const=Path("prompt_cenas.txt"), type=Path, metavar="ARQUIVO",
                   help="só gera o prompt para colar no chat do Claude (padrão: prompt_cenas.txt) e para")
    p.add_argument("--scenes", type=Path, metavar="cenas.json",
                   help="usa o plano de cenas salvo da resposta do chat do Claude (não usa a API)")
    p.add_argument("--ai", choices=["anthropic", "openai"],
                   help="IA usada pela API no plano de cenas e na revisão (anthropic = Claude, openai = ChatGPT)")
    p.add_argument("--review", action="store_true",
                   help="a IA revisa as miniaturas dos b-rolls pela API (ANTHROPIC_API_KEY ou OPENAI_API_KEY)")
    p.add_argument("--export-review", nargs="?", const=Path("revisao"), type=Path, metavar="PASTA",
                   help="só gera as grades de miniaturas + prompt para revisar no chat do Claude (padrão: revisao/) e para")
    p.add_argument("--review-file", nargs="?", const=Path("revisao/resposta.json"), type=Path, metavar="ARQUIVO",
                   help="aplica a resposta da revisão feita no chat (padrão: revisao/resposta.json)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def _pick_channel(args: argparse.Namespace, base_dir: Path) -> "Path | None | bool":
    """O canal pedido (--canal) ou o do idioma do roteiro. False = pedido e não encontrado."""
    if args.canal:
        path = find_channel(base_dir, args.canal)
        if path is None:
            names = ", ".join(name for _, name, _ in list_channels(base_dir)) or "nenhum"
            print(f"canal não encontrado: {args.canal} (canais: {names})", file=sys.stderr)
            return False
        return path
    lang = args.lang if args.lang and args.lang != "auto" else None
    if lang is None and args.script is not None and args.script.exists():
        lang = detect_language(args.script.read_text(encoding="utf-8", errors="ignore"))
    return channel_for_language(base_dir, lang) if lang else None


def _default_output(cfg, script: Path, base_dir: Path) -> Path:
    """Vídeo na pasta do canal ("videos/Jhonny Walt/2026-10-01_1830_roteiro.mp4"); sem canal, como antes."""
    if not cfg.channel.videos_dir:
        return Path("video_final.mp4")
    folder = base_dir / cfg.channel.videos_dir
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{time.strftime('%Y-%m-%d_%H%M')}_{script.stem}.mp4"


def safe_console(stream=None) -> None:
    """Console do Windows em cp1252: o "✔" do resumo final derrubava o processo (código 1) depois do vídeo
    pronto, quando rodado direto no terminal. Caractere que o console não tem vira "?"."""
    stream = stream or sys.stdout
    try:
        stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    safe_console()
    args = parse_args(argv)
    base_dir = Path.cwd()
    if args.canais:
        rows = list_channels(base_dir)
        print("\n".join(f"{name}  (idioma: {lang or 'auto'}, arquivo: canais/{stem}.yaml)" for stem, name, lang in rows)
              or "nenhum canal em canais/")
        return 0
    if args.audio is None or args.script is None:
        print("informe --audio e --script (ou --canais para ver os canais)", file=sys.stderr)
        return 2
    cfg = load_config(args.config, args.env)
    channel = _pick_channel(args, base_dir)
    if channel is False:
        return 2
    if channel is not None:
        apply_channel(cfg, channel)
    if args.ajustes is not None:
        if not args.ajustes.exists():
            print(f"ajustes não encontrados: {args.ajustes}", file=sys.stderr)
            return 2
        apply_overrides(cfg, args.ajustes)
        apply_pace(cfg)
    if args.out is None:
        args.out = _default_output(cfg, args.script, base_dir)
    if args.seed is not None:
        cfg.seed = args.seed
    if args.no_llm:
        cfg.analysis.provider = "heuristic"
    if args.no_clip:
        cfg.ranking.use_clip = False
    if args.no_transcribe:
        cfg.transcription.enabled = False
    if args.ai:
        cfg.analysis.provider = args.ai
        cfg.review.provider = args.ai
    if args.review:
        cfg.review.mode = "api"
    if args.no_sfx:
        cfg.sfx.enabled = False
    if args.no_music:
        cfg.music.enabled = False
    if args.lang:
        cfg.analysis.script_language = args.lang
        if args.lang != "auto":
            cfg.transcription.language = args.lang
    if args.music:
        cfg.music.track = str(args.music)
    if args.encoder:
        cfg.output.encoder = args.encoder
    if args.pace:
        cfg.timeline.pace = args.pace
        apply_pace(cfg)
    if args.keep_temp:
        cfg.render.keep_intermediates = True

    run_id = time.strftime("%Y%m%d-%H%M%S")
    work_dir = base_dir / cfg.paths.work_dir / run_id
    setup_logging(args.log_level, work_dir / "pipeline.jsonl")
    log = get_logger("cli")

    inputs = [("áudio", args.audio), ("roteiro", args.script)]
    if args.scenes is not None:
        inputs.append(("plano de cenas", args.scenes))
    if args.review_file is not None:
        inputs.append(("resposta da revisão", args.review_file))
    if args.music is not None:
        inputs.append(("música", args.music))
    for label, path in inputs:
        if not path.exists():
            log.error(f"{label} não encontrado: {path}", extra={"stage": "input"})
            return 2

    progress.set_file(args.progresso)
    opts = RunOptions(audio=args.audio, script=args.script, output=args.out, base_dir=base_dir,
                      reindex_library=args.reindex_library, report_path=args.report,
                      export_prompt=args.export_prompt, scenes_file=args.scenes,
                      export_review=args.export_review, review_file=args.review_file)
    try:
        report = run_pipeline(cfg, opts, work_dir)
    except KeyboardInterrupt:
        log.error("interrompido pelo usuário", extra={"stage": "cli"})
        return 130
    except AnalysisError as exc:
        ai = cfg.analysis.provider
        key = "OPENAI_API_KEY" if ai == "openai" else "ANTHROPIC_API_KEY"
        model = cfg.analysis.openai_model if ai == "openai" else cfg.analysis.model
        log.error(f"a IA ({ai}) não gerou o plano de cenas: {exc}", extra={"stage": "cli"})
        print(
            f"\n✖ Nada foi renderizado: a IA ({ai}, modelo {model}) não gerou o plano de cenas.\n"
            f"  Motivo: {str(exc)[:400]}\n"
            f"  Confira: {key} no .env, créditos na conta e o nome do modelo no config.yaml.\n"
            "  Para seguir sem IA (sem templates e com buscas genéricas): --no-llm"
        )
        return 1
    except DiskFullError as exc:
        log.error(str(exc), extra={"stage": "cli"})
        print(f"\n✖ {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - mensagem clara no topo
        log.exception(f"falha no pipeline: {exc}", extra={"stage": "cli"})
        return 1
    finally:
        if not cfg.render.keep_intermediates:  # deu erro ou foi interrompido: os trechos não ficam ocupando disco
            shutil.rmtree(work_dir / "render", ignore_errors=True)
    if "exported_prompt" in report:
        print(
            f"\n✔ Prompt salvo em {report['exported_prompt']} ({report['sentences']} frases).\n"
            "  1. Abra o arquivo, copie TODO o conteúdo e cole no chat do Claude (claude.ai).\n"
            "  2. Salve a resposta (o JSON) como cenas.json nesta pasta.\n"
            f"  3. Rode: python main.py --audio {args.audio} --script {args.script} --scenes cenas.json"
        )
        return 0
    if "exported_review" in report:
        again = f"python main.py --audio {args.audio} --script {args.script}" + (
            f" --scenes {args.scenes}" if args.scenes else "")
        folder = report["review_dir"]
        if report["exported_review"] is None:
            print("\n✔ Nada para revisar: todas as cenas já têm b-rolls aprovados.\n"
                  f"  Rode: {again} --review-file {folder}/resposta.json")
            return 0
        print(
            f"\n✔ Revisão visual pronta em {folder}/ ({report['review_scenes']} cenas).\n"
            f"  1. No chat do Claude (claude.ai), anexe TODAS as imagens cena_XX.jpg da pasta {folder}.\n"
            f"  2. Cole na mesma mensagem todo o conteúdo de {folder}/prompt_revisao.txt e envie.\n"
            f"  3. Salve a resposta (o JSON) como {folder}/resposta.json.\n"
            f"  4. Rode: {again} --review-file {folder}/resposta.json"
        )
        return 0
    totals = report["totals"]
    print(f"\n  Idioma do roteiro: {report.get('language', '?')}")
    print(f"  Plano de cenas: {report['analysis_mode']} · templates: {totals.get('templates') or 'nenhum'} · "
          f"overlays: {totals.get('overlays') or 'nenhum'}\n  Imagens por banco: {totals.get('sources') or {}}")
    if report.get("music"):
        moods = " → ".join(m["mood"] for m in report["music"])
        print(f"  Música: {len(report['music'])} trecho(s) ({moods})")
    elif cfg.music.enabled:
        print(f"  Música: nenhuma faixa em {cfg.paths.music}/ (veja o README: Música de fundo)")
    if report.get("coherence_fixes"):
        print(f"  Coerência: {len(report['coherence_fixes'])} ajuste(s) no plano da IA (detalhes no report.json)")
    if report.get("timings"):
        from .encoder import NAMES

        print(f"  Codificador: {NAMES.get(report.get('encoder', ''), report.get('encoder', '?'))}")
        print("  Tempo por etapa: " + " · ".join(f"{k} {_mmss(v)}" for k, v in report["timings"].items())
              + f" · total {_mmss(report['elapsed_seconds'])}")
    print(
        f"\n✔ {args.out}  ({report['video_duration']:.3f}s, {totals['scenes']} cenas, {totals['shots']} b-rolls, "
        f"{totals['fallbacks']} fallbacks, requisições: {totals['api_requests']})"
    )
    return 0


def _mmss(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}min" if h else f"{m}min{s:02d}s" if m else f"{s}s"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
