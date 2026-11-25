"""Orquestra o fluxo completo: áudio + roteiro -> video_final.mp4 + report.json."""

from __future__ import annotations

import json
import os
import random
from collections import Counter
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .assembler import Assembler
from .avatar import attach as attach_avatar
from .avatar import free_scenes, start_avatar
from .character import place as place_character
from .character import start_character
from .cache import SQLiteCache
from .coherence import enforce_coherence
from . import director
from .director import apply_niche
from .auto_templates import auto_templates
from .cues import set_currency
from .cues import set_language as set_cue_language
from .lang import resolve as resolve_language
from . import progress, usage
from .motion.gfx import apply_palette, set_brand, set_number_locale
from .query_lang import ensure_english, requery_parts
from .rhythm import add_fact_overlays, add_file_cards, add_opener, add_rhythm, add_year_overlays, apply_swaps
from .visual_checks import build_references
from .plan_fix import (add_cta_overlays, drop_closing_rank_cards, fix_meta_queries, focus_queries, isolate_rank_headers,
                       split_long_scenes, stretch_item_templates, stretch_title_templates)
from .subject import (anchor_scenes, assign_scene_subjects, detect_subject, first_generation_era, section_eras,
                      section_generations, section_names, sections_prompt, sentence_subjects)
from .config import AppConfig, apply_format, is_vertical
from .http import user_agent
from .log import get_logger
from .disk import DiskFullError, clean_old_work, drop_run_leftovers, ensure_space, prune_media
from .media import FFmpegError, audio_duration, probe, require_ffmpeg, supported_xfade_transitions
from .models import Candidate, MediaType, Scene, Shot
from .motion.planner import (TemplatePlanner, estimated_words, extend_short_templates, overlay_fallbacks, plan_overlays,
                             select_template_scenes, template_length)
from .providers import ArchiveOrgProvider, LocalLibraryProvider, build_providers
from .providers.archive_org import script_years
from .ranking import Ranker, ThumbnailFetcher, load_embedder
from .review import VisualReviewer
from .scenes import (AnalysisError, HeuristicSceneAnalyzer, ManualSceneAnalyzer, build_analyzer, manual_prompt,
                     merge_short_scenes)
from .search import BrollSearcher, SceneSearchResult
from .selection import Selector
from .sfx import build_soundtrack
from .timeline import plan_timeline
from .transcription import align, parse_script, transcribe

log = get_logger("pipeline")


@dataclass
class RunOptions:
    audio: Path
    script: Path
    output: Path
    base_dir: Path
    reindex_library: bool = False
    report_path: Path | None = None
    export_prompt: Path | None = None  # só gera o prompt para o chat do Claude e para
    scenes_file: Path | None = None  # plano de cenas pronto (resposta do chat do Claude)
    export_review: Path | None = None  # só gera as grades para a revisão visual no chat e para
    review_file: Path | None = None  # resposta da revisão visual feita no chat


def _cut_narration(cfg: AppConfig, audio: Path, cache_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Narração sem as pausas longas, guardada no cache (o mesmo áudio não é cortado nem transcrito de novo)."""
    import hashlib

    from .silence import cut_silences

    n = cfg.narration
    st = audio.stat()
    key = hashlib.sha1(f"{audio.resolve()}|{st.st_size}|{st.st_mtime}|{n.max_pause}|{n.keep_pause}|"
                       f"{n.threshold_db}".encode()).hexdigest()[:16]
    dest = cache_dir / "narracao" / f"{audio.stem[:40]}-{key}.wav"
    before = audio_duration(audio)
    if not dest.exists():
        res = cut_silences(audio, dest, n.max_pause, n.keep_pause, n.threshold_db)
        cuts = res.cuts
    else:
        cuts = None
    after = audio_duration(dest)
    info = {"before_s": round(before, 2), "after_s": round(after, 2), "removed_s": round(before - after, 2)}
    if cuts is not None:
        info["cuts"] = cuts
    log.info("narração sem pausas", extra={"stage": "input", **info})
    return dest, info


def _plan_shorts_fx(cfg: AppConfig, shots: list[Shot], words: list, total: float, plan: Any, lang: str):
    """Efeitos de retenção do vídeo curto (broll_bot/shortsfx), no tempo das palavras."""
    from .shortsfx import plan_fx
    from .shortsfx.plan import segments_from_shots

    v = cfg.vertical
    teaser = "rank" if plan is not None and plan.sections else "list" if plan is not None and plan.steps else ""
    kinds = {sh.template or "" for sh in shots}
    if not teaser and any("rank" in k for k in kinds):  # sem o diretor: o ranking achado pelas regras
        teaser = "rank"
    fx = plan_fx(words, segments_from_shots(shots), total, lang,
                 hook=getattr(plan, "hook", "") or "", title=getattr(plan, "title", "") or "",
                 stamp=getattr(plan, "stamp", "") or "", teaser=teaser, currency=getattr(plan, "currency", "") or "",
                 seed=cfg.seed, density=v.fx_density, ending=v.ending, hook_on=v.fx_hook)
    log.info("efeitos do vídeo curto planejados", extra={"stage": "shortsfx", **fx.summary()})
    return fx


def _burn_captions(cfg: AppConfig, assembler: Assembler, video: Path, words: list, total: float,
                   work_dir: Path, busy: list[tuple[float, float]] | None = None, fx: Any = None) -> dict[str, Any]:
    """Queima a legenda animada (e a barrinha de progresso do vídeo curto) no vídeo pronto. Com ``fx`` (vídeo
    curto), os efeitos de retenção entram no mesmo encode, por baixo da legenda."""
    from .captions import build_ass, burn, caption_filter, hex_rgb
    from .motion.gfx import accent

    c = cfg.captions
    acc = hex_rgb(c.color, accent())
    ass = work_dir / "legendas.ass"
    if c.enabled and words:
        ass.write_text(build_ass(words, cfg.output.width, cfg.output.height, c.style, acc, c.position, c.size,
                                 c.uppercase, busy=busy, emphasis=fx.emphasis if fx is not None else None),
                       encoding="utf-8")
    else:
        ass.write_text(build_ass([], cfg.output.width, cfg.output.height, c.style, acc), encoding="utf-8")
    bar = acc if is_vertical(cfg) and cfg.vertical.progress_bar else None
    tmp = video.with_name(video.stem + ".legenda.mp4")
    info: dict[str, Any] = {}
    done = False
    if fx is not None:
        from .shortsfx import render_fx

        t0 = time.monotonic()
        try:
            render_fx(video, fx, tmp, cfg.output.width, cfg.output.height, cfg.output.fps,
                      assembler._encode_args(final=True), accent=acc, lang=cfg.analysis.script_language,
                      grade=cfg.vertical.fx_grade, post_vf=caption_filter(ass, bar, total), seed=cfg.seed)
            done = True
            info["fx"] = {**fx.summary(), "seconds": round(time.monotonic() - t0, 1)}
        except Exception as exc:  # noqa: BLE001 - os efeitos são um extra: sem eles, a legenda entra do jeito normal
            log.warning("efeitos do vídeo curto falharam; vai só a legenda", extra={"stage": "shortsfx",
                                                                                     "error": str(exc)[-400:]})
            info["fx"] = {"error": str(exc)[-300:]}
    if not done:
        burn(video, ass, tmp, assembler._encode_args(final=True), progress_bar=bar, total=total)
    os.replace(tmp, video)
    log.info("legenda no vídeo", extra={"stage": "legendas", "estilo": c.style if c.enabled else "-",
                                        "palavras": len(words), "barra": bool(bar)})
    return {"style": c.style if c.enabled else "", "words": len(words) if c.enabled else 0,
            "progress_bar": bool(bar), **info}


def run_pipeline(cfg: AppConfig, opts: RunOptions, work_dir: Path) -> dict[str, Any]:
    t0 = time.monotonic()
    laps: dict[str, float] = {}  # tempo de cada etapa (vai para o report e para o fim do log)
    last = [t0]

    def lap(name: str) -> None:
        now = time.monotonic()
        laps[name] = round(laps.get(name, 0.0) + now - last[0], 1)
        last[0] = now

    require_ffmpeg()
    apply_format(cfg)  # vídeo em pé (Shorts/Reels/TikTok): 1080x1920, legenda, corte de pausas, ritmo curto
    cache_dir = opts.base_dir / cfg.paths.cache_dir
    media_dir = cache_dir / "media"
    cache = SQLiteCache(cache_dir / "cache.sqlite")
    cache.purge_expired()
    rng = random.Random(cfg.seed)
    usage.reset()  # gasto de IA deste vídeo

    # 1) áudio, roteiro e sincronização -------------------------------------
    silence_info: dict[str, Any] = {}
    if cfg.narration.cut_silence and opts.export_prompt is None:
        # pausas longas fora antes de tudo: transcrição, cenas e avatar já usam a narração cortada
        try:
            opts.audio, silence_info = _cut_narration(cfg, opts.audio, cache_dir)
        except Exception as exc:  # noqa: BLE001 - sem o corte o vídeo sai igual, só com as pausas
            log.warning("corte das pausas falhou; segue a narração original",
                        extra={"stage": "input", "error": str(exc)[-300:]})
    audio_seconds = audio_duration(opts.audio)
    # disco: sobras de render de vídeos que deram erro/cancelados saem, o cache de mídias fica no limite
    # e, se ainda assim não couber, para aqui com a mensagem certa (e não no meio do render)
    clean_old_work(work_dir.parent, keep=work_dir)
    prune_media(media_dir, cfg.render.max_media_cache_gb)
    if opts.export_prompt is None:
        ensure_space(work_dir, audio_seconds, media_dir, cfg.render.max_media_cache_gb)
    script_text = opts.script.read_text(encoding="utf-8")
    sentences = parse_script(script_text)
    if not sentences:
        raise ValueError("roteiro vazio ou sem frases reconhecíveis")
    # idioma do roteiro: transcrição, números/moeda, pistas dos templates e rótulos seguem ele
    configured_lang = (cfg.analysis.script_language or "auto").split("-")[0].lower()
    lang = resolve_language(cfg.analysis.script_language, script_text)
    language_note = ""
    if configured_lang not in ("auto", "", lang):
        # o canal diz um idioma e o roteiro está em outro (canal errado no app): vale o do roteiro
        language_note = f"o canal é em {configured_lang} e o roteiro está em {lang}: o vídeo seguiu o roteiro"
        log.warning("idioma do canal diferente do roteiro", extra={"stage": "input", "canal": configured_lang,
                                                                    "roteiro": lang})
    cfg.analysis.script_language = lang
    if (cfg.transcription.language or "auto") in ("auto", configured_lang):
        cfg.transcription.language = lang
    set_number_locale(lang)
    set_brand(bool(cfg.templates.brand_theme))  # textos amarelos com contorno preto nos gráficos
    apply_palette(cfg.channel.palette if cfg.templates.brand_theme else None)  # cores do canal (canais/)
    set_currency(cfg.channel.currency)  # moeda padrão do canal (£ no UK); a falada sempre vence
    set_cue_language(lang)
    log.info("idioma do roteiro", extra={"stage": "input", "language": lang})
    log.info("entrada carregada", extra={"stage": "input", "audio_s": round(audio_seconds, 3), "sentences": len(sentences)})
    progress.step("transcricao", audio_s=round(audio_seconds, 1), language=lang)
    # diretor de IA: lê o roteiro inteiro e decide ranking, listas, cards e o nicho (as regras ficam de reserva).
    # A chamada não depende da transcrição: as duas correm juntas
    director.reset()
    plan = None
    director_job = None
    if opts.export_prompt is None and opts.scenes_file is None and cfg.director.enabled:
        director_job = ThreadPoolExecutor(max_workers=1)
        director_call = director_job.submit(director.ask, sentences, cfg, cache, lang)
    words = transcribe(opts.audio, cfg.transcription, cache)
    lap("transcrição")
    progress.step("cenas")
    align(sentences, words, audio_seconds)
    if director_job is not None:
        try:
            plan = director.finish(director_call.result(), sentences, lang)
        except Exception as exc:  # noqa: BLE001 - o diretor é um extra: sem ele, valem as regras
            log.warning("diretor de IA falhou; seguem as regras do bot", extra={"stage": "director", "error": str(exc)})
        director_job.shutdown(wait=False)
        director.install(plan)
    if plan is not None:
        if plan.currency and not cfg.channel.currency:
            set_currency(plan.currency)
        if cfg.director.niche and plan.niche:
            apply_niche(cfg, plan)
    lap("diretor de IA")
    # ranking com vários carros ("Numéro 8 : la Dacia Logan MCV"...): cada seção tem o seu carro
    per_sentence = sentence_subjects([s.text for s in sentences])
    sections = section_names(per_sentence)
    if sections:
        cfg.analysis.sections_hint = sections_prompt(sentences, per_sentence)
        log.info("vídeo com vários carros: assunto por seção", extra={"stage": "input", "carros": sections})
    elif not cfg.analysis.subject:
        # assunto único só em vídeo de carro: num vídeo de agro, "Supermagro" nas buscas e na revisão (feitas para
        # marca/modelo de carro) só atrapalha — os bancos não têm foto dele
        cfg.analysis.subject = (plan.subject if plan is not None and plan.subject and plan.automotive
                                else "" if plan is not None and not plan.automotive
                                else detect_subject(" ".join(s.text for s in sentences)))
    if cfg.analysis.subject:
        log.info("assunto do vídeo", extra={"stage": "input", "subject": cfg.analysis.subject})

    if opts.export_prompt is not None:
        opts.export_prompt.write_text(manual_prompt(sentences, cfg.analysis), encoding="utf-8")
        log.info("prompt para o chat do Claude salvo", extra={"stage": "scenes", "file": str(opts.export_prompt),
                                                              "sentences": len(sentences)})
        cache.close()
        return {"exported_prompt": str(opts.export_prompt), "sentences": len(sentences)}

    # apresentador da HeyGen (abertura, inscrição, final): gera em segundo plano enquanto o resto anda
    avatar_job = None
    if opts.export_review is None:
        try:
            avatar_job = start_avatar(cfg, opts.audio, sentences, audio_seconds, cache_dir, work_dir, opts.base_dir)
        except Exception as exc:  # noqa: BLE001 - avatar é um extra
            log.warning("avatar não iniciou", extra={"stage": "avatar", "error": str(exc)[-300:]})
    # fotos do personagem em cena (depois do avatar e nos momentos fortes): também em segundo plano
    character_job = None
    if opts.export_review is None:
        try:
            windows = [(g.start, g.end, g.moment) for g in avatar_job.segments] if avatar_job else []
            character_job = start_character(cfg, sentences, windows, cache_dir, opts.base_dir, per_sentence)
        except Exception as exc:  # noqa: BLE001 - extra
            log.warning("imagens do personagem não iniciaram", extra={"stage": "character", "error": str(exc)[-300:]})

    # 2) cenas semânticas ----------------------------------------------------
    if opts.scenes_file is not None:
        # plano feito no chat do Claude: se estiver inválido, é melhor avisar do que
        # seguir com um vídeo pior sem o usuário perceber
        analysis_mode = f"ManualSceneAnalyzer ({opts.scenes_file.name})"
        scenes = ManualSceneAnalyzer(opts.scenes_file).analyze(sentences, audio_seconds)
    else:
        analyzer = build_analyzer(cfg.analysis, cache)
        analysis_mode = type(analyzer).__name__
        try:
            scenes = analyzer.analyze(sentences, audio_seconds)
        except Exception as exc:  # noqa: BLE001
            # sem o plano da IA o vídeo sai sem templates e com buscas genéricas: melhor parar
            # e dizer o motivo do que entregar um vídeo ruim sem o usuário perceber
            if not cfg.analysis.fallback_to_heuristic:
                raise AnalysisError(str(exc)) from exc
            log.warning("análise com LLM falhou; usando heurística", extra={"stage": "scenes", "error": str(exc)})
            analysis_mode = "HeuristicSceneAnalyzer (fallback)"
            scenes = HeuristicSceneAnalyzer(cfg.analysis).analyze(sentences, audio_seconds)
    scenes = merge_short_scenes(scenes, min_seconds=1.0)
    director_notes = director.strip_ai_styles(scenes) if plan is not None else []
    # buscas em francês/polonês acham pouco nos bancos (e o vídeo repete imagens): traduz para o inglês
    english_fixes = ensure_english(scenes, cfg.analysis, cache)
    assign_scene_subjects(scenes, sentences, per_sentence)
    coherence_fixes = director_notes + english_fixes + enforce_coherence(scenes)  # nada na tela que não esteja na fala
    scenes, split_notes = split_long_scenes(scenes, sentences, cfg.analysis.split_scene_seconds,
                                            cfg.analysis.target_scene_seconds)
    coherence_fixes += split_notes + fix_meta_queries(scenes) + isolate_rank_headers(scenes, sentences)
    assign_scene_subjects(scenes, sentences, per_sentence)  # cada pedaço com o carro da SUA seção
    coherence_fixes += requery_parts(scenes, cfg.analysis, cache)  # cena de 3 min dividida: buscas por pedaço
    coherence_fixes += focus_queries(scenes)  # o pedaço do Audi busca o Audi (não o Lexus da frase anterior)
    if cfg.analysis.auto_templates:
        scenes, auto_notes = auto_templates(scenes, sentences, cfg.analysis.subject,
                                            lang=cfg.analysis.script_language)
        coherence_fixes += auto_notes
    coherence_fixes += drop_closing_rank_cards(scenes, sentences)  # "Number three." que só fecha a seção
    coherence_fixes += enforce_coherence(scenes)  # de novo: cenas divididas/juntadas acima
    stretch_title_templates(scenes)
    stretch_item_templates(scenes, cfg.templates.max_seconds)
    assign_scene_subjects(scenes, sentences, per_sentence)  # cenas divididas/juntadas acima
    coherence_fixes += add_cta_overlays(scenes, cfg.analysis.subject)
    anchor_fixes = anchor_scenes(scenes, cfg.analysis.subject, cfg.analysis.niche)  # buscas sempre com o carro
    kinds = set(cfg.overlays.kinds) if cfg.overlays.enabled else set()
    if "fuel" in kinds:
        coherence_fixes += add_fact_overlays(scenes)  # "5 litres aux cent" -> card de consumo
    # ficha de papel com o nome do carro no começo da seção (o canal pode não usar); no estilo Broadcast
    # ela vira a tarja com o nome (troca abaixo)
    if "file" in kinds or cfg.overlays.swap.get("file") in kinds:
        coherence_fixes += add_file_cards(scenes)
    if "years" in kinds:  # "2014 to 2019", "the 2008 model": card de ano/modelo com o carro
        coherence_fixes += add_year_overlays(scenes)
    if cfg.templates.rhythm_gap > 0:  # nada de minutos seguidos só de b-roll
        scenes, rhythm_notes = add_rhythm(scenes, sentences, cfg.templates.rhythm_gap, cfg.templates.statement_every,
                                          seed=cfg.seed, statement_chance=cfg.templates.statement_chance,
                                          phrase_chance=cfg.templates.phrase_chance,
                                          styles=set(cfg.templates.styles) if cfg.templates.enabled else set(),
                                          kinds=kinds)
        coherence_fixes += rhythm_notes
    if plan is not None and plan.title and cfg.templates.opener:  # cartão de abertura com o título do vídeo
        intro_end = max((g.end for g in avatar_job.segments if g.moment == "intro"), default=0.0) \
            if avatar_job is not None else 0.0
        coherence_fixes += add_opener(scenes, plan.title, plan.kicker, after=intro_end,
                                      styles=set(cfg.templates.styles) if cfg.templates.enabled else set())
    coherence_fixes += apply_swaps(scenes, cfg.templates.swap, cfg.overlays.swap,
                                   set(cfg.templates.styles) if cfg.templates.enabled else set(), kinds,
                                   cfg.templates.swap_chance, cfg.seed)
    lap("plano de cenas (IA)")
    progress.step("busca", 0, len(scenes))

    # 3) linha do tempo ----------------------------------------------------
    supported = supported_xfade_transitions()
    transitions = [t for t in cfg.timeline.transitions if not supported or t in supported]
    if avatar_job is not None and cfg.avatar.layout == "full":  # nada de template escondido atrás do avatar
        coherence_fixes += free_scenes(scenes, avatar_job.segments)
    template_scenes = select_template_scenes(scenes, cfg.templates, audio_seconds)
    coherence_fixes += extend_short_templates(scenes, template_scenes, cfg.templates)  # tempo de leitura
    coherence_fixes += overlay_fallbacks(scenes, template_scenes, cfg.analysis.script_language)
    scene_map = {s.index: s for s in scenes}
    hold = [(g.start, g.end) for g in avatar_job.segments] \
        if avatar_job is not None and cfg.avatar.layout == "full" else None  # o apresentador num shot só
    shots = plan_timeline(scenes, words, audio_seconds, cfg.timeline, cfg.output.fps, rng, transitions,
                          image_ratio=cfg.search.image_ratio, template_scenes=template_scenes,
                          template_min=cfg.templates.min_seconds,
                          template_seconds={i: template_length(scene_map[i], cfg.templates) for i in template_scenes},
                          hold=hold)
    log.info("linha do tempo planejada", extra={
        "stage": "timeline", "scenes": len(scenes), "shots": len(shots),
        "max_clip_s": max(s.clip_duration for s in shots), "min_clip_s": min(s.clip_duration for s in shots),
    })

    # 4) busca + seleção (cena a cena, para evitar repetição) ----------------
    embedder = load_embedder(cfg, cache_dir)
    thumbs = ThumbnailFetcher(cache_dir, user_agent=user_agent(cfg.wikimedia_contact))
    ranker = Ranker(cfg, embedder, thumbs)
    ranker.gens = section_generations([s.text for s in sentences], per_sentence)  # "troisième génération"
    ranker.eras = first_generation_era(ranker.gens, section_eras([s.text for s in sentences], per_sentence))
    providers = build_providers(cfg, opts.base_dir, semantic=ranker.text_image_scores if embedder else None)
    for p in providers.values():
        if isinstance(p, LocalLibraryProvider):
            if opts.reindex_library:
                p.reindex()
            p.context = script_text  # arquivo com "requires" no .json só entra se o roteiro citar o assunto
        if isinstance(p, ArchiveOrgProvider):  # filme de época só da época do roteiro
            p.years = script_years(script_text)
    named_report = _fetch_named_images(cfg, plan, opts.base_dir, providers)  # a foto da Optima antes da busca
    if hasattr(providers.get("openverse"), "auth_headers"):  # miniatura da API do Openverse com a chave
        thumbs.host_headers["api.openverse.org"] = providers["openverse"].auth_headers
    searcher = BrollSearcher(cfg, providers, ranker, cache, rng)
    if sections and embedder is not None:  # fotos de referência de cada carro (sua pasta + Wikimedia + Pixabay)
        local = [c for p in providers.values() if isinstance(p, LocalLibraryProvider) for c in p.usable]
        ranker.refs = build_references(sections, local, _reference_search(cfg, providers, searcher, cache),
                                       thumbs, embedder)
    selector = Selector(cfg, providers, ranker, media_dir, cfg.seed)
    results: dict[int, SceneSearchResult] = {}

    def search(scene: Scene, reviews: dict[str, str] | None = None) -> SceneSearchResult:
        scene_shots = [s for s in shots if s.scene_index == scene.index]
        n_images = sum(s.prefer_media is MediaType.IMAGE for s in scene_shots)
        prev = results.get(scene.index)
        if prev is None:
            return searcher.search_scene(scene, len(scene_shots), n_images, selector.used_keys)
        # nova passada depois da revisão: o que já foi buscado vem do cache e não gasta
        # orçamento; só busca mais se os aprovados não bastarem
        full = cfg.search.max_searches_per_scene + max(0, len(scene_shots) - 3)
        budget = max(2, full - prev.budget_used)
        res = searcher.search_scene(scene, len(scene_shots), n_images, selector.used_keys, budget, reviews)
        res.attempts = prev.attempts + [{**a, "after_review": True} for a in res.attempts]
        res.budget_used += prev.budget_used
        return res

    # primeiro pesquisa todas as cenas, depois distribui: assim o plano B de uma cena
    # não "rouba" um clipe que é o certo para outra
    searcher.prefetch(scenes, {s.scene_index for s in shots if s.prefer_media is MediaType.IMAGE})
    for n, scene in enumerate(scenes, 1):
        results[scene.index] = search(scene)
        progress.step("busca", n, len(scenes))

    # revisão visual (Claude olhando as miniaturas): API, resposta do chat ou cache
    reviewer = VisualReviewer(cfg, cache, thumbs)
    for car in sections:  # o revisor também sabe a geração/os anos de cada carro
        parts = [f"{GEN_NAMES.get(ranker.gens[car], '')} generation".strip()] if car in ranker.gens else []
        if car in ranker.eras:
            parts.append("years {}-{}".format(*ranker.eras[car]) if ranker.eras[car][0] > 1900
                         else f"before {ranker.eras[car][1] + 1}")
        if parts:
            reviewer.car_detail[car] = ", ".join(parts)
    reviews = {s.index: reviewer.cached(s, results[s.index]) for s in scenes}
    if opts.review_file is not None:
        n = reviewer.import_file(opts.review_file, scenes, reviews)
        log.info("revisão do chat aplicada", extra={"stage": "review", "file": str(opts.review_file), "verdicts": n})
    use_api = cfg.review.mode == "api" and opts.export_review is None
    searched_with: dict[int, dict[str, str]] = {}
    for _ in range(max(1, cfg.review.rounds) if use_api else 1):
        if use_api and (pending := reviewer.pending(scenes, results, reviews)):
            reviewer.review_api(pending, reviews)
        research_reviewed(scenes, reviews, searched_with, results, search)
    if opts.export_review is not None:
        pending = reviewer.pending(scenes, results, reviews)
        prompt_path = reviewer.export(pending, opts.export_review) if pending else None
        cache.close()
        return {"exported_review": str(prompt_path) if prompt_path else None, "review_scenes": len(pending),
                "review_dir": str(opts.export_review)}

    lap("busca, download e revisão")
    progress.step("templates")
    selector.prefetch(shots, results)  # os ~470 downloads em paralelo, não um por vez
    planner = TemplatePlanner(cfg, selector, searcher, thumbs)
    if template_scenes:
        planner.reserve_items(shots, scene_map)
    selector.section_of = {s.index: s.analysis.subject for s in scenes if s.analysis.subject}
    selector.scene_text = {s.index: s.text for s in scenes}
    selector.subject = cfg.analysis.subject
    for scene in scenes:
        selector.assign([s for s in shots if s.scene_index == scene.index], results)
    timing_words = words or estimated_words(sentences)
    if template_scenes:
        planner.prepare(shots, scene_map, results, timing_words)
    plan_overlays(shots, scene_map, cfg, timing_words)
    lap("preparo dos templates")
    progress.step("render", 0, len(shots), thumbs=[str(s.media_path) for s in shots if s.media_path
                                                   and Path(s.media_path).suffix.lower() in IMAGE_SUFFIXES][:12])

    if avatar_job is not None:
        progress.step("templates", note="esperando o avatar da HeyGen")
        n = attach_avatar(shots, avatar_job.result(), cfg.output.fps, full=cfg.avatar.layout == "full")
        log.info("avatar nos shots", extra={"stage": "avatar", "shots": n})
        lap("avatar (HeyGen)")
    if character_job is not None:
        n = place_character(shots, character_job.result(), cfg.output.fps, cfg.seed)
        log.info("imagens do personagem nos shots", extra={"stage": "character", "shots": n})
        lap("imagens do personagem")

    fx_plan = None
    if is_vertical(cfg) and cfg.vertical.fx:
        try:
            fx_plan = _plan_shorts_fx(cfg, shots, timing_words, audio_seconds, plan, lang)
        except Exception as exc:  # noqa: BLE001 - sem os efeitos o vídeo curto ainda sai
            log.warning("plano dos efeitos do vídeo curto falhou", extra={"stage": "shortsfx", "error": str(exc)[-300:]})

    # 5) render -------------------------------------------------------------
    assembler = Assembler(cfg, work_dir / "render")
    assembler.overlay_dir = opts.base_dir / cfg.paths.overlays
    clip_paths = _render_shots(assembler, selector, shots, cfg.render.workers)
    lap("render dos trechos")
    progress.step("montagem")
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    audio_in, sfx_events, music_segs = opts.audio, 0, []
    if cfg.sfx.enabled or cfg.music.enabled:
        try:
            audio_in, sfx_events, music_segs = build_soundtrack(
                cfg, shots, scenes, opts.audio, audio_seconds, work_dir, opts.base_dir / cfg.paths.sfx,
                opts.base_dir / cfg.paths.music, extra=fx_plan.cues() if fx_plan is not None else None)
        except Exception as exc:  # noqa: BLE001 - trilha nunca derruba o vídeo
            log.warning("efeitos/música falharam; vídeo sai só com a narração",
                        extra={"stage": "sfx", "error": str(exc)[-300:]})
            audio_in = opts.audio
    lap("música e efeitos")
    assembler.assemble(shots, clip_paths, audio_in, opts.output, audio_seconds)
    lap("montagem final")
    captions_info: dict[str, Any] = {}
    if cfg.captions.enabled or (is_vertical(cfg) and cfg.vertical.progress_bar) or fx_plan is not None:
        progress.step("montagem", note="efeitos e legendas" if fx_plan is not None else "legendas")
        try:
            busy = [(sh.cut_in, sh.cut_out) for sh in shots if sh.template]
            captions_info = _burn_captions(cfg, assembler, opts.output, words or estimated_words(sentences),
                                           audio_seconds, work_dir, busy, fx=fx_plan)
        except Exception as exc:  # noqa: BLE001 - legenda é um extra: o vídeo sem ela ainda serve
            log.warning("legenda não entrou no vídeo", extra={"stage": "legendas", "error": str(exc)[-400:]})
            captions_info = {"error": str(exc)[-300:]}
        lap("legendas")
    log.info("tempo por etapa", extra={"stage": "done", "codificador": assembler.encoder,
                                       **{k: _mmss(v) for k, v in laps.items()}})

    final = probe(opts.output)
    drift = abs(final.duration - audio_seconds)
    log.info("vídeo final pronto", extra={"stage": "done", "output": str(opts.output),
                                          "duration_s": round(final.duration, 3), "drift_s": round(drift, 3)})

    report = build_report(cfg, opts, scenes, shots, results, providers, analysis_mode, audio_seconds,
                          final.duration, time.monotonic() - t0, selector.downloads, reviewer.notes)
    report["totals"]["sfx_events"] = sfx_events
    report["music"] = [{"start": round(m.start, 2), "end": round(m.end, 2), "mood": m.mood,
                        "track": Path(m.track).name} for m in music_segs]
    report["coherence_fixes"] = coherence_fixes
    report["subject"] = cfg.analysis.subject
    if sections:
        report["subjects"] = sections
    report["language"] = cfg.analysis.script_language
    report["channel"] = cfg.channel.name
    if named_report:
        report["named_images"] = named_report  # fotos dos nomes do roteiro baixadas antes da busca
    # sem transcrição o corte segue uma estimativa pelo tamanho das frases (imagem fora do tempo da fala)
    report["sync"] = {"words": len(words), "mode": "transcrição" if words else "estimada"}
    report["timings"] = laps
    report["encoder"] = assembler.encoder
    report["assembly"] = assembler.last_assembly  # "copy" = blocos colados sem recodificar
    vetoes: dict[str, int] = {}
    for res in results.values():
        for c in res.ranked:
            if c.veto:
                vetoes[c.veto] = vetoes.get(c.veto, 0) + 1
    report["vetoes"] = vetoes  # imagens barradas pelas checagens visuais, por motivo
    report["review"] = reviewer.status()  # a revisão pela IA rodou? (e, se não, por quê)
    # bancos ligados que ficaram de fora (sem chave, sem yt-dlp): o app avisa em vez de sumir calado
    report["sources_off"] = {name: p.unavailable_reason() for name, p in providers.items() if not p.available()}
    if language_note:
        report["language_note"] = language_note
    report["format"] = {"format": "vertical" if is_vertical(cfg) else "horizontal",
                        "size": f"{cfg.output.width}x{cfg.output.height}"}
    if silence_info:
        report["narration"] = silence_info
    if captions_info:
        report["captions"] = captions_info
    from .search import source_funnel

    report["sources_funnel"] = source_funnel(results, shots, ranker, selector.fail_counts, selector.fail_reasons)
    for name, why in thumbs.reasons.items():  # por que as miniaturas falharam (sem elas a IA não vê a foto)
        if name in report["sources_funnel"]:
            report["sources_funnel"][name]["thumb_errors"] = why
    report["references"] = {k: len(v) for k, v in ranker.refs.items()}
    report["anchor_fixes"] = anchor_fixes
    if avatar_job is not None:
        report["avatar"] = {"id": cfg.avatar.avatar_id, "image": Path(cfg.avatar.photo).name if cfg.avatar.photo else "",
                            "layout": cfg.avatar.layout, "segments": avatar_job.report(),
                            "seconds": round(sum(g.duration for g in avatar_job.segments if g.status == "ok"), 1),
                            "usd": round(sum(g.usd for g in avatar_job.segments), 4),
                            "wallet_usd": avatar_job.wallet,
                            "shots": sum(bool(s.avatar) for s in shots)}
    if character_job is not None:
        report["character"] = character_job.report()
    # o que o diretor de IA decidiu (e o que a validação tirou); sem ele, valeram as regras do bot
    report["director"] = plan.summary() if plan is not None else {"status": "desligado ou indisponível: valeram as regras"}
    director.reset()
    report["usage"] = usage.summary(cfg.costs.prices)  # gasto estimado de IA (tokens x tabela de preços)
    report_path = opts.report_path or opts.output.with_name("report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    progress.step("pronto", output=str(opts.output), report=str(report_path))
    if not cfg.render.keep_intermediates:
        shutil.rmtree(work_dir / "render", ignore_errors=True)
        drop_run_leftovers(work_dir)  # narração mixada (centenas de MB) e cortes do avatar: o vídeo já saiu
    cache.close()
    return report


def _fetch_named_images(cfg: AppConfig, plan: Any, base_dir: Path, providers: dict) -> dict[str, Any]:
    """Baixa para a biblioteca as fotos dos nomes que o diretor listou e reindexa (``named_images.py``)."""
    from .named_images import commons_title, fetch_named

    library = next((p for p in providers.values() if isinstance(p, LocalLibraryProvider)), None)
    if plan is None or not plan.named or library is None:
        return {}
    wiki = providers.get("wikimedia")

    def fallback(query: str) -> list[tuple[str, str, str, str]]:
        return [(c.download_url, c.page_url or "", c.author or "Wikimedia Commons", commons_title(c))
                for c in wiki.search(query, MediaType.IMAGE, 10) if c.download_url]

    report = fetch_named(plan.named, base_dir / cfg.paths.local_library, cfg.named_images, cfg.serper_api_key,
                         fallback if wiki is not None else None)
    if report.get("downloaded"):
        library.reindex()
    return report


def research_reviewed(scenes: list[Scene], reviews: dict[int, dict[str, str]],
                      searched_with: dict[int, dict[str, str]], results: dict[int, SceneSearchResult],
                      search: Callable[[Scene, dict[str, str]], SceneSearchResult]) -> list[int]:
    """Depois da revisão, busca de novo só as cenas cujos vereditos mudaram desde a última busca. Antes, cada
    rodada refazia todas as cenas revisadas: no vídeo das 9 baterias foram 3 passadas de 164 cenas (34 min de
    busca), mas só 20 a 45 cenas tinham reprovação nova em cada rodada."""
    redone = []
    for scene in scenes:
        verdicts = reviews.get(scene.index)
        if verdicts and verdicts != searched_with.get(scene.index):
            results[scene.index] = search(scene, verdicts)
            searched_with[scene.index] = dict(verdicts)
            redone.append(scene.index)
    return redone


def _mmss(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}min{s:02d}s" if m else f"{s}s"


def _render_shots(assembler: Assembler, selector: Selector, shots: list[Shot], workers: int) -> list[Path]:
    render_dir = assembler.work_dir
    lock = threading.Lock()
    finished = [0]

    def one(shot: Shot) -> Path:
        path = _render_one(shot)
        with lock:
            finished[0] += 1
            progress.step("render", finished[0], len(shots))
        return path

    def _render_one(shot: Shot) -> Path:
        dest = render_dir / f"shot_{shot.index:04d}.mp4"
        try:
            return assembler.render_shot(shot, dest)
        except DiskFullError:
            raise  # disco cheio: parar já, com a mensagem certa (outra imagem não ajuda)
        except FFmpegError as exc:
            log.warning("falha ao renderizar shot; tentando reserva", extra={"stage": "render", "shot": shot.index, "error": str(exc)[-300:]})
        with lock:
            reserve = selector.take_reserve(shot.scene_index)
            if reserve is not None:
                selector.apply(shot, reserve[0], reserve[1], "render_failed_used_reserve")
        if reserve is not None:
            try:
                return assembler.render_shot(shot, dest)
            except FFmpegError:
                pass
        shot.fallback_reason = "render_failed_generated_background"
        shot.candidate, shot.media_path = None, None
        return assembler.render_generated(shot, dest)

    if workers <= 0:
        workers = max(1, min(6, (os.cpu_count() or 2) // 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        paths = list(pool.map(one, shots))
    log.info("shots renderizados", extra={"stage": "render", "count": len(paths)})
    return paths


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
GEN_NAMES = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th"}


def _reference_search(cfg: AppConfig, providers: dict, searcher: BrollSearcher, cache):
    """Busca de fotos de referência: Wikimedia sem filtro de licença (só a miniatura é comparada,
    nunca entra no vídeo) e, junto, o Pixabay (as tags dele costumam trazer o modelo)."""
    wiki, pixabay = providers.get("wikimedia"), providers.get("pixabay")
    ttl = cfg.search.cache_ttl_hours * 3600

    def search(query: str) -> list[Candidate]:
        found: list[Candidate] = []
        if wiki is not None and wiki.available():
            key = f"wikimedia-ref-v2|{query}"
            hit = cache.get("search", key)
            if hit is None:
                try:
                    hit = [c.to_dict() for c in wiki.search_reference(query)]
                    cache.set("search", key, hit, ttl_seconds=ttl)
                except Exception as exc:  # noqa: BLE001 - referência é um extra
                    log.warning("referências do Wikimedia falharam", extra={"stage": "references", "query": query,
                                                                           "error": str(exc)[-200:]})
                    hit = []
            found += [Candidate.from_dict(d) for d in hit]
        if pixabay is not None and pixabay.available():
            try:
                found += searcher._cached_search(pixabay, query, MediaType.IMAGE, "en")[0]
            except Exception as exc:  # noqa: BLE001
                log.warning("referências do Pixabay falharam", extra={"stage": "references", "query": query,
                                                                     "error": str(exc)[-200:]})
        return found

    return search


def build_report(cfg: AppConfig, opts: RunOptions, scenes: list[Scene], shots: list[Shot],
                 results: dict[int, SceneSearchResult], providers: dict, analysis_mode: str,
                 audio_seconds: float, video_seconds: float, elapsed: float, downloads: int,
                 review_notes: dict[int, str] | None = None) -> dict[str, Any]:
    scene_items = []
    for scene in scenes:
        res = results.get(scene.index, SceneSearchResult(scene.index))
        scene_items.append({
            "scene": scene.index,
            "start": round(scene.start, 3),
            "end": round(scene.end, 3),
            "narrated_text": scene.text,
            "visual_summary": scene.analysis.visual_summary,
            "queries": scene.analysis.queries,
            "fallback_query": scene.analysis.fallback_query,
            "tone": scene.analysis.tone,
            "api_calls": res.budget_used,
            "search_attempts": res.attempts,
            "review": {"approved": sum(c.review == "approved" for c in res.ranked),
                       "rejected": sum(c.review == "rejected" for c in res.ranked),
                       "notes": (review_notes or {}).get(scene.index, "")},
            "shots": [_shot_item(s) for s in shots if s.scene_index == scene.index],
        })
    credits = sorted({(s.candidate.provider, s.candidate.author, s.candidate.page_url)
                      for s in shots if s.candidate and s.candidate.page_url})
    return {
        "input": {"audio": str(opts.audio), "script": str(opts.script)},
        "output": str(opts.output),
        "seed": cfg.seed,
        "analysis_mode": analysis_mode,
        "audio_duration": round(audio_seconds, 3),
        "video_duration": round(video_seconds, 3),
        "elapsed_seconds": round(elapsed, 1),
        "totals": {
            "scenes": len(scenes),
            "shots": len(shots),
            "remote_downloads": downloads,
            "api_requests": {name: p.request_count() for name, p in providers.items()},
            "fallbacks": sum(1 for s in shots if s.fallback_reason),
            "reviewed_shots": sum(1 for s in shots if s.candidate and s.candidate.review == "approved"),
            "max_clip_seconds": max(s.clip_duration for s in shots),
            "templates": dict(Counter(s.template for s in shots if s.template)),
            "effects": dict(Counter(s.effect_in for s in shots if s.effect_in)),
            "overlays": dict(Counter(s.overlay["kind"] for s in shots if s.overlay)),
            "sources": dict(Counter(s.candidate.provider for s in shots if s.candidate)),
        },
        "scenes": scene_items,
        "credits": [{"source": p, "author": a, "url": u} for p, a, u in credits],
    }


def _shot_item(s: Shot) -> dict[str, Any]:
    c = s.candidate
    return {
        "shot": s.index,
        "cut_in": s.cut_in,
        "cut_out": s.cut_out,
        "applied_duration": s.clip_duration,
        "transition_in": {"type": s.transition_in.kind, "duration": s.transition_in.duration},
        "source": c.provider if c else None,
        "media_type": c.media_type.value if c else None,
        "media_id": c.media_id if c else None,
        "page_url": c.page_url if c else None,
        "query": c.query if c else None,
        "score": c.score if c else None,
        "semantic": c.semantic if c else None,
        "review": (c.review or None) if c else None,
        "layout": s.layout,
        "template": s.template,
        "template_theme": s.template_data.get("theme") if s.template else None,
        "overlay": s.overlay.get("kind") if s.overlay else None,
        "effect_in": s.effect_in,
        "effect_out": s.effect_out,
        "source_offset": s.source_offset,
        "speed": s.speed,
        "fallback": s.fallback_reason,
    }
