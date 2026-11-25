"""Carregamento de configuração (config.yaml + .env)."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml

try:  # python-dotenv é opcional em tempo de teste
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


@dataclass
class OutputConfig:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    # auto = chip de vídeo se houver (Intel QuickSync, NVIDIA NVENC, AMD AMF), senão o processador
    encoder: str = "auto"
    # "horizontal" (1920x1080, YouTube) ou "vertical" (1080x1920: Shorts, Reels, TikTok). O vertical liga o
    # perfil de vídeo curto (``vertical:`` abaixo): legenda animada, corte das pausas, ritmo rápido
    format: str = "horizontal"


@dataclass
class NarrationConfig:
    """Pausas da narração (broll_bot/silence.py): vídeo curto não pode ter buraco sem fala."""

    cut_silence: bool = False  # liga sozinho no formato vertical
    max_pause: float = 0.35  # pausa maior que isso é encurtada...
    keep_pause: float = 0.12  # ...para isso (um respiro, senão vira metralhadora)
    threshold_db: float = -42.0  # abaixo disso é silêncio (o limiar sobe sozinho se a gravação tiver ruído)


@dataclass
class CaptionsConfig:
    """Legenda animada queimada no vídeo (broll_bot/captions.py), no tempo de cada palavra."""

    enabled: bool = False  # liga sozinho no formato vertical
    # palavra | frase | caixa | karaoke | limpa | impacto | neon | maquina  (prévias: studio/web/legendas/)
    style: str = "frase"
    color: str = ""  # destaque (#RRGGBB); vazio = a cor de destaque do canal
    position: float = 0.0  # altura do centro da legenda (fração da tela); 0 = automático pelo formato
    size: float = 1.0  # escala do tamanho da letra
    uppercase: str = "auto"  # auto (o estilo decide) | sim | nao


@dataclass
class VerticalConfig:
    """Perfil do vídeo curto em pé (``output.format: vertical``)."""

    width: int = 1080
    height: int = 1920
    cut_silence: bool = True
    captions: bool = True
    caption_style: str = "frase"
    # nada sai do vídeo em pé: os overlays são desenhados acima da faixa dos botões do app e o avatar da HeyGen
    # (gerado em 16:9) é recortado no meio para a tela em pé; False tira só do vertical
    overlays: bool = True
    avatar: bool = True
    # planos curtos e mais quebras de padrão: troca de imagem a cada 1–2,5 s, mais efeitos de corte
    min_clip_seconds: float = 0.9
    max_clip_seconds: float = 2.5
    transition_min: float = 0.12
    transition_max: float = 0.28
    hard_cut_probability_in_scene: float = 0.75
    hard_cut_probability_scene_change: float = 0.55
    effect_probability: float = 0.5
    rhythm_gap: float = 6.0  # passou isso só de b-roll: entra uma frase/zoom/template
    progress_bar: bool = True  # barrinha de progresso no topo (segura até o fim)
    # motion de retenção (broll_bot/shortsfx): gancho, ênfase nas palavras fortes, desenhos, congelamento, preto e
    # branco, cartas no corte, contador, tremida e acabamento de cor; tudo no tempo da fala
    fx: bool = True
    fx_density: float = 1.0  # 0,5 = metade dos efeitos; 1,5 = mais
    fx_grade: bool = True  # correção de cor, grão e vinheta leves
    fx_hook: bool = True  # título do gancho nos 2–3 primeiros segundos
    ending: str = "loop"  # loop (o fim emenda no começo) | cta ("segue para mais") | both | off

@dataclass
class PathsConfig:
    work_dir: str = ".work"
    cache_dir: str = ".cache"
    local_library: str = "library/mixkit"
    overlays: str = "library/overlays"  # opcional: clipes de queima de filme/light leak (fundo preto)
    sfx: str = "library/sfx"  # opcional: efeitos sonoros próprios (substituem os gerados)
    music: str = "library/music"  # faixas de música de fundo (subpastas por clima)


@dataclass
class TranscriptionConfig:
    enabled: bool = True
    model: str = "small"
    device: str = "auto"
    compute_type: str = "default"
    language: str | None = "auto"  # "auto" = o idioma do roteiro (ou pt, fr, pl, en, es)


@dataclass
class AnalysisConfig:
    provider: str = "anthropic"  # "anthropic" | "openai" | "heuristic"
    model: str = "claude-opus-5-5"
    # usado com provider "openai". O "mini" custa uma fração do modelo cheio e dá conta do plano;
    # o raciocínio baixo evita gastar milhares de tokens "pensando"
    openai_model: str = "gpt-5-mini"
    openai_effort: str = "medium"  # minimal | low | medium | high
    # modelos de raciocínio gastam tokens "pensando" antes do JSON: o limite precisa ser folgado
    openai_max_tokens: int = 40000
    # se a IA escolhida falhar: False = para com erro claro (padrão); True = segue com a heurística
    fallback_to_heuristic: bool = False
    effort: str = "medium"
    max_tokens: int = 16000
    target_scene_seconds: float = 12.0
    min_scene_seconds: float = 3.0
    max_scene_seconds: float = 25.0
    script_language: str = "auto"  # "auto" = detectado no roteiro (pt, fr, pl, en, es)
    niche: str = "automotivo (carros, reviews, comparativos, preços)"
    # carro/produto principal do vídeo ("toyota corolla"); vazio = detectar no roteiro
    subject: str = ""
    # preenchido sozinho em vídeo de ranking com vários carros: o trecho de cada um (vai para o plano)
    sections_hint: str = ""
    # cena mais longa que isso é dividida nas frases (a IA às vezes junta 40 s numa cena)
    split_scene_seconds: float = 16.0
    auto_templates: bool = True  # acha na fala pistas claras de template (anos, 0-100, FIPE, notas...)


@dataclass
class DirectorConfig:
    """Diretor de IA (broll_bot/director.py): uma IA mais forte lê o roteiro inteiro antes de tudo e decide
    seções do ranking, listas numeradas, cards de dado e o nicho; as regras do bot ficam de reserva."""

    enabled: bool = True
    provider: str = "auto"  # auto = a mesma IA do plano de cenas | openai | anthropic
    openai_model: str = "gpt-5"
    openai_effort: str = "medium"  # minimal | low | medium | high
    model: str = "claude-sonnet-5-5"  # quando a IA é o Claude
    effort: str = "medium"
    max_tokens: int = 40000
    # fase 2: o nicho do vídeo e o que conta como imagem do assunto vêm do diretor (troca de nicho sem ajuste)
    niche: bool = True


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0


@dataclass
class SearchConfig:
    # acervos de licença livre primeiro (mais variedade e foto do assunto exato); YouTube CC só como reserva
    provider_order: list[str] = field(default_factory=lambda: ["openverse", "wikimedia", "archive", "mixkit_local",
                                                               "pixabay", "pexels", "youtube_cc", "youtube_any"])
    # bancos que ficam de fora neste vídeo (o estilo Documentário tira o youtube_any: filmagem da guerra no YouTube
    # costuma ter reivindicação de quem revende material de domínio público)
    skip_providers: list[str] = field(default_factory=list)
    # "compare": cada consulta vai para todos os bancos e o melhor resultado vence;
    # "fallback": para no primeiro banco que já tem mídia boa suficiente (gasta menos API)
    strategy: str = "compare"
    max_searches_per_scene: int = 6  # + 1 por shot acima de 3 (cena longa precisa de mais variedade)
    max_uses_per_clip: int = 3  # um clipe aparece no máximo 3 vezes no vídeo (com cortes diferentes)
    min_reuse_gap_seconds: float = 45.0  # repetição só depois disso desde a última vez que apareceu
    max_marked_uses: int = 3  # arquivo seu marcado (say) aparece no máximo isso no vídeo antes da sobra da cena vizinha
    local_counts_toward_budget: bool = False
    per_page: int = 15
    min_score: float = 0.45
    # Relevância visual mínima (CLIP, 0..1) para um clipe contar como "bom". O score
    # combinado soma qualidade/duração/orientação e deixava passar clipes bonitos
    # mas fora do assunto (ex.: esportivo vermelho numa cena sobre motor).
    min_semantic: float = 0.55
    reserve_per_scene: int = 1
    # vídeo do Pexels/Pixabay mais longo que isso: baixa só os primeiros N segundos, sem som (0 = inteiro). O shot
    # usa no máximo ~16 s; os vídeos inteiros de 60–90 s em 1080p eram 7,6 GB num vídeo de 15 min
    video_head_seconds: float = 24.0
    image_ratio: float = 0.2
    min_width: int = 1280
    orientation: str = "landscape"
    cache_ttl_hours: float = 24.0
    max_rank_candidates: int = 16
    similarity_threshold: float = 0.93
    # mesma foto vinda de outra fonte (ou recortada): quase idêntica no CLIP = não entra de novo
    duplicate_threshold: float = 0.96
    # busca que cita o modelo exato só vai a estes bancos se a fila estiver livre (sem esperar o limite)
    opportunistic_model_providers: list[str] = field(default_factory=lambda: ["pexels"])
    # bancos de reserva: só entram numa busca quando os outros não trouxeram mídia boa que baste (ou quando a busca
    # já está no cache, que é de graça). O Pexels tem 200 buscas/hora: assim elas vão para as cenas que precisam
    reserve_providers: list[str] = field(default_factory=lambda: ["pexels", "youtube_cc", "youtube_any"])
    # depois de esperar isto (minutos, somando o vídeo todo) na fila de um banco com limite por hora (Pexels
    # 200/h), ele só é consultado quando a fila está livre: a busca de um vídeo de 20 min não trava 1 hora
    max_queue_wait_minutes: float = 8.0
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


@dataclass
class WikimediaConfig:
    """Wikimedia Commons: fotos de modelos exatos (Corolla E100, E170...). Sem chave:
    basta WIKIMEDIA_CONTACT no .env (a Wikimedia exige identificar quem consulta)."""

    allow_share_alike: bool = False  # CC BY-SA obriga a licenciar o vídeo nos mesmos termos
    min_width: int = 1200  # fotos menores ficam de fora


@dataclass
class NamedImagesConfig:
    """Fotos das coisas com nome próprio que o diretor listou (a bateria Optima, a loja Canadian Tire, a pessoa):
    o bot baixa antes da busca e grava em library/mixkit/_auto com o "say" pronto (broll_bot/named_images.py).
    Busca de imagens da web: SERPER_API_KEY no .env (Google Imagens); sem ela, só a Wikimedia."""

    enabled: bool = True
    per_name: int = 2  # fotos por nome
    # nome citado a toda hora (o presidente num vídeo de política) ganha mais fotos, para não repetir a mesma:
    # +1 a cada ``mentions_per_extra`` citações, até ``per_name_max``
    per_name_max: int = 6
    mentions_per_extra: int = 6
    web: bool = True  # foto da web (Google Imagens) sem licença verificada; false = só Wikimedia
    min_side: int = 400  # foto menor que isso fica de fora


@dataclass
class ArchiveConfig:
    """Arquivo histórico (Internet Archive): cinejornais e filmes do governo americano em domínio público
    (``providers/archive_org.py``). Sem chave. Desligado por padrão: o estilo Documentário liga no vídeo, e um canal
    de história pode ligar sempre com ``archive: {enabled: true}`` no perfil."""

    enabled: bool = False
    clip_seconds: float = 8.0  # trecho baixado de cada filme
    items_per_search: int = 3  # filmes olhados por busca
    segments_per_item: int = 5  # trechos de cada filme oferecidos ao ranking (um por quadro guardado)
    collections: list[str] = field(default_factory=lambda: ["universal_newsreels", "FedFlix", "usgovfilms",
                                                            "prelinger"])


@dataclass
class OpenverseConfig:
    """Openverse: fotos Creative Commons e domínio público (Flickr, museus, acervos). Funciona sem chave;
    OPENVERSE_CLIENT_ID/OPENVERSE_CLIENT_SECRET no .env aumentam o limite."""

    allow_share_alike: bool = False  # CC BY-SA obriga a licenciar o vídeo nos mesmos termos
    # a foto do Flickr que o Openverse entrega é a "grande" (1024 px): com 1200 quase tudo ficava de fora
    min_width: int = 1000


@dataclass
class YouTubeCCConfig:
    """Vídeos Creative Commons do YouTube (YOUTUBE_API_KEY no .env + yt-dlp instalado)."""

    clip_seconds: float = 25.0  # trecho baixado do meio de cada vídeo
    min_subscribers: int = 1000  # canal pequeno reenviando TV/filme como "CC" é o maior risco
    max_searches_per_video: int = 30  # cada busca gasta 100 das 10.000 unidades diárias da chave grátis
    max_height: int = 1080
    # o YouTube às vezes barra o download como robô ("Sign in to confirm you're not a bot"): com os cookies de um
    # navegador logado passa. "firefox" (no Windows o Chrome/Edge trancam os cookies) ou um cookies.txt exportado
    cookies_from_browser: str = ""
    cookies_file: str = ""


@dataclass
class YouTubeAnyConfig:
    """Qualquer vídeo do YouTube (não só CC), como reserva depois do YouTube CC. Usa a mesma chave, o mesmo
    navegador logado e a mesma cota diária. Ligar/desligar: ``providers.youtube_any.enabled``."""

    clip_seconds: float = 8.0  # trecho curto do meio do vídeo: o shot usa 3–5 s dele
    min_subscribers: int = 1000
    max_searches_per_video: int = 20  # divide a cota diária (10.000) com o YouTube CC
    max_height: int = 1080
    # canais que nunca entram (nome como aparece no YouTube): concorrente que fez o mesmo vídeo, por exemplo
    exclude_channels: list[str] = field(default_factory=list)


@dataclass
class ProviderConfig:
    enabled: bool = True
    requests_per_minute: float = 60.0
    burst: int = 5
    max_wait_seconds: float = 20.0
    max_retries: int = 3
    timeout_seconds: float = 20.0
    locale: str = "en-US"
    min_interval_seconds: float = 0.0  # espaço mínimo entre dois pedidos (o Pexels bloqueia rajada com 403)
    per_hour: int = 0  # cota por hora (Pexels 200): rajada até o que sobrou da hora; no lugar de requests_per_minute


@dataclass
class RankingWeights:
    semantic: float = 0.65
    quality: float = 0.12
    duration: float = 0.1
    orientation: float = 0.08
    text_penalty: float = 0.25
    negative_penalty: float = 0.3


@dataclass
class RankingConfig:
    use_clip: bool = True
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    device: str = "auto"
    weights: RankingWeights = field(default_factory=RankingWeights)
    # a sua biblioteca (imagens escolhidas a dedo) ganha um empurrão sobre os bancos, mas só
    # quando já é relevante: o bônus não conta para o piso de relevância visual
    local_bonus: float = 0.06
    # empurrão por fonte (empate técnico vai para quem tem a foto do assunto exato e repete menos); o do Openverse
    # compensa a foto de 1024 px do Flickr, que perdia ~0,056 na nota de resolução para a de 1920 do Pexels
    # archive: filme de época (só liga no Documentário) ganha da floresta genérica do Pexels no empate
    provider_bonus: dict[str, float] = field(default_factory=lambda: {"openverse": 0.06, "wikimedia": 0.03,
                                                                      "archive": 0.06})
    # checagens visuais (broll_bot/visual_checks.py): quanto a frase ruim precisa ganhar (cosseno CLIP)
    visual_checks: bool = True
    off_topic_margin: float = 0.02   # prédio/paisagem/retrato x "foto de carro"
    crash_margin: float = 0.025      # carro batido x carro inteiro
    reference_margin: float = 0.03   # parece mais outro carro da lista do que o da seção


@dataclass
class TimelineConfig:
    min_clip_seconds: float = 2.2
    max_clip_seconds: float = 7.0
    snap_to_words: bool = True
    snap_tolerance: float = 0.6
    hard_cut_probability_in_scene: float = 0.35
    hard_cut_probability_scene_change: float = 0.1
    transition_min: float = 0.2
    transition_max: float = 0.6
    transitions: list[str] = field(
        default_factory=lambda: [
            "fade", "dissolve", "fadeblack", "smoothleft", "smoothright", "smoothup",
            "slideleft", "slideright", "wipeleft", "wiperight", "circleopen",
            "hblur", "coverleft", "revealright", "zoomin", "fadewhite",
        ]
    )
    # Efeitos de corte seco: whip (chicote com desfoque), punch (zoom rápido),
    # glitch (RGB + ruído), leak (vazamento de luz), wash (lavagem de cor verde-água
    # -> azul), burn (queima de filme) e blackout (tela preta com barrinhas + glitch).
    effects: list[str] = field(default_factory=lambda: ["whip", "punch", "glitch", "leak", "wash", "burn", "blackout"])
    effect_probability: float = 0.35  # chance de um corte seco ganhar um efeito
    # peso de cada efeito no sorteio: wash e burn são as "assinaturas" (aparecem mais)
    effect_weights: dict[str, float] = field(default_factory=lambda: {
        "whip": 1.0, "punch": 0.9, "glitch": 0.5, "leak": 0.5, "wash": 1.7, "burn": 1.7, "blackout": 0.5,
    })
    video_layouts: dict[str, float] = field(
        default_factory=lambda: {"full": 0.35, "zoom_crop": 0.3, "push": 0.2, "inset": 0.15}
    )
    image_layouts: dict[str, float] = field(default_factory=lambda: {"kenburns": 0.75, "inset": 0.25})
    pace: str = "normal"  # "normal" | "fast" (cortes rápidos, estilo documentário)
    # filme antigo (grão, risco, luz piscando, sépia): sempre no vídeo destes bancos (o estilo Documentário põe o
    # arquivo histórico) e, numa fração das cenas, no b-roll de vídeo comum também
    old_film_providers: list[str] = field(default_factory=list)
    old_film_chance: float = 0.0


@dataclass
class TemplatesConfig:
    """Motion graphics sugeridos pelo plano de cenas (campo "style")."""

    enabled: bool = True
    styles: list[str] = field(default_factory=lambda: [
        "comparison", "versus", "specs", "stat", "gauge", "blueprint", "checklist", "ranking", "chapter",
        "mosaic", "hud", "card", "vhs", "headline", "triptych", "counter", "chart", "quote", "scrapbook",
        "timeline", "speedometer", "datasheet", "price", "proscons", "score", "magnifier", "beforeafter", "news",
        "comment", "poll", "showcase", "gallery", "sketch", "dossier", "statement",
        "bulletin", "report", "videowall",  # telejornal: só entram pela troca do estilo Broadcast (swap)
        "paperfloat", "paperpair", "papertimeline", "papercompare", "paperstatement", "paperrank",  # papel: Dossiê
        "newsrank", "newsfloat", "newstimeline", "newscompare",  # telejornal (troca do Broadcast)
        # edição magnética: dados da fala e quebras de padrão, com a cara do estilo (templates_magnet.py)
        "percentbar", "peoplegrid", "receipt", "downtime", "warning", "textmask", "photostack", "splitpanels",
        "annotate", "wordpop",
        # reportagem: fonte, de X para Y, garantia, passo a passo, não confirmado, desmontagem (templates_story.py)
        "source", "fromto", "coverage", "steps", "unverified", "exploded",
        # qualquer nicho (templates_plus.py): esteira na prancheta, abertura, perfil, ano, processo, mito x fato,
        # verbete e "neste vídeo"
        "filmstrip", "opener", "profile", "yearflash", "flow", "mythfact", "definition", "agenda",
        "article",  # matéria de site citada na fala (templates_article.py)
        "docphrase", "docbars",  # estilo Documentário: só entram pela troca do estilo (o esquema "diagram" o estilo libera)
    ])
    # templates desligados (a lista "styles" do config.yaml só acrescenta; tirar um de lá não o desliga)
    styles_off: list[str] = field(default_factory=list)
    # cara dos templates de edição magnética: blueprint | paper | tv | data | kinetic | cinema | mix (sorteia)
    skin: str = ""
    min_gap_seconds: float = 0.0  # b-roll mínimo entre dois templates (0 = podem vir colados)
    max_share: float = 1.0  # fração máxima do vídeo em templates (1.0 = sem limite)
    min_seconds: float = 2.5
    max_seconds: float = 16.0  # templates podem passar dos 7 s dos b-rolls (a IA decide)
    # duração padrão quando o plano não informa template_seconds
    default_seconds: dict[str, float] = field(default_factory=lambda: {
        "comparison": 10.0, "versus": 10.5, "specs": 9.0, "checklist": 9.0, "blueprint": 8.0, "gauge": 6.0,
        "stat": 5.0, "ranking": 5.5, "chapter": 3.2, "mosaic": 7.0, "hud": 6.5, "card": 6.0, "vhs": 6.0,
        "headline": 2.2, "triptych": 6.5, "counter": 5.0, "chart": 7.0, "quote": 4.5, "scrapbook": 6.0,
        "timeline": 10.0, "speedometer": 5.0, "datasheet": 9.0, "price": 5.0, "proscons": 8.0, "score": 8.0,
        "magnifier": 4.0, "beforeafter": 5.5, "news": 5.5, "comment": 6.0, "poll": 5.5,
        "showcase": 4.5, "gallery": 5.0, "sketch": 5.0, "dossier": 5.0, "statement": 2.6,
        "bulletin": 3.0, "report": 4.5, "videowall": 5.0,
        "paperfloat": 4.5, "paperpair": 5.0, "papertimeline": 10.0, "papercompare": 10.0, "paperstatement": 3.0,
        "paperrank": 5.5, "newsrank": 5.5, "newsfloat": 4.5, "newstimeline": 10.0, "newscompare": 10.0,
        "percentbar": 4.5, "peoplegrid": 5.0, "receipt": 5.0, "downtime": 5.0, "warning": 4.5, "textmask": 3.5,
        "photostack": 5.0, "splitpanels": 4.0, "annotate": 5.0, "wordpop": 3.5,
        "source": 5.0, "fromto": 4.5, "coverage": 6.0, "steps": 5.0, "unverified": 4.5, "exploded": 5.0,
        "filmstrip": 7.0, "opener": 4.0, "profile": 5.0, "yearflash": 3.8, "flow": 7.0, "mythfact": 6.5,
        "definition": 6.0, "agenda": 6.0, "article": 6.0, "docphrase": 3.0, "docbars": 7.0, "diagram": 9.0,
        "map": 7.0, "flagversus": 6.0,
    })
    # mínimo por estilo (os demais usam min_seconds): o título de destaque é curto
    style_min_seconds: dict[str, float] = field(default_factory=lambda: {"headline": 1.2, "chapter": 2.0, "ranking": 2.0,
                                                                                   "statement": 1.2, "showcase": 2.5,
                                                                                   "gallery": 3.5, "sketch": 3.5,
                                                                                   "dossier": 3.0, "bulletin": 1.5,
                                                                                   "report": 2.5, "videowall": 3.5,
                                                                                   "paperfloat": 2.5, "paperpair": 3.5,
                                                                                   "paperstatement": 1.5,
                                                                                   "paperrank": 2.0, "newsrank": 2.0,
                                                                                   "newsfloat": 2.5, "percentbar": 3.5,
                                                                                   "peoplegrid": 3.5, "receipt": 3.5,
                                                                                   "downtime": 3.5, "warning": 3.0,
                                                                                   "textmask": 2.5, "photostack": 3.5,
                                                                                   "splitpanels": 3.0, "annotate": 4.0,
                                                                                   "wordpop": 2.5, "source": 3.5,
                                                                                   "fromto": 3.5, "coverage": 4.5,
                                                                                   "steps": 3.5, "unverified": 3.0,
                                                                                   "exploded": 3.5, "filmstrip": 4.5,
                                                                                   "opener": 3.0, "profile": 3.5,
                                                                                   "yearflash": 2.8, "flow": 4.0,
                                                                                   "mythfact": 4.5, "definition": 4.0,
                                                                                   "agenda": 4.0, "article": 4.0,
                                                                                   "docphrase": 1.5, "docbars": 4.0,
                                                                                   "diagram": 5.0, "map": 4.0,
                                                                                   "flagversus": 4.0})
    item_search_budget: int = 1  # buscas de API por item do comparativo/versus
    opener: bool = True  # cartão de abertura com o título do vídeo (precisa do diretor de IA)
    captions: dict[str, bool] = field(
        default_factory=lambda: {"comparison": True, "mosaic": True, "hud": True, "card": True, "vhs": False}
    )
    # padrão visual do canal em TODOS os templates (fundo azul-escuro, blueprint, texto amarelo com
    # contorno preto); "" volta aos temas variados de antes
    brand_theme: str = "channel"
    # ritmo: passou esse tempo só de b-roll, a cena seguinte ganha tela preta com a frase, frase de
    # destaque, a foto do carro no blueprint (showcase, galeria ou desenho), uma setinha ou a palavra (0 = desliga)
    rhythm_gap: float = 11.0
    statement_every: float = 40.0  # intervalo mínimo entre telas pretas (o real é sorteado até 2,2x)
    statement_chance: float = 0.5  # chance de uma frase de impacto virar tela preta
    phrase_chance: float = 0.45  # chance de uma frase de impacto virar frase de destaque sobre o b-roll
    # troca de família (estilo do vídeo no app): {"statement": "bulletin", "showcase": "report"...} depois do
    # ritmo; ``swap_chance`` < 1 troca só parte (estilo Mix). Vale também para ``overlays.swap``.
    swap: dict[str, Any] = field(default_factory=dict)  # destino fixo ou lista (sorteia um)
    swap_chance: float = 1.0
    comparison_themes: list[str] = field(default_factory=lambda: ["paper", "blueprint"])
    hud_themes: list[str] = field(default_factory=lambda: ["redgrid", "cyangrid"])
    dark_themes: list[str] = field(default_factory=lambda: ["carbon", "blueprint"])  # specs, gauge, checklist...


@dataclass
class OverlaysConfig:
    """Overlays sobre o b-roll sugeridos pelo plano de cenas (campo "overlay")."""

    enabled: bool = True
    kinds: list[str] = field(default_factory=lambda: ["label", "pair", "callout", "tag", "badge", "keyword", "bullets",
                                                      "panel", "bignum", "subscribe", "fuel",
                                                      "phrase", "file", "impact", "lowerthird", "ticker", "crawl",
                                                      "years", "paperyears", "papertag"])
    swap: dict[str, Any] = field(default_factory=dict)  # {"keyword": "lowerthird", "phrase": "ticker"...}
    # letreiro do rodapé rolando (estilo Broadcast): blocos contínuos de ~crawl_seconds começando perto destas
    # posições do vídeo (0.04 = logo no começo, 0.5 = no meio), só em trecho sem template; [] = desligado
    crawl_at: list[float] = field(default_factory=list)
    crawl_seconds: float = 24.0
    crawl_every: float = 0.0  # antigo (um letreiro curto a cada N s); ignorado
    min_gap_seconds: float = 3.0  # intervalo mínimo entre dois overlays
    min_shot_seconds: float = 1.6  # shot curto demais não recebe overlay


@dataclass
class SfxConfig:
    """Efeitos sonoros nas transições, templates e overlays (ver broll_bot/sfx.py)."""

    enabled: bool = True
    volume: float = 0.55  # 0–1: volume dos efeitos em relação à narração
    # transições: mais baixas que os templates e só em parte delas (sorteio com a seed)
    transition_volume: float = 0.45  # multiplica o volume dos sons de transição
    transition_probability: float = 0.35  # chance de uma transição/efeito de corte ter som
    transition_min_gap: float = 4.0  # segundos mínimos entre dois sons de transição


@dataclass
class MusicConfig:
    """Música de fundo que segue o clima do roteiro (ver broll_bot/music.py)."""

    enabled: bool = True
    track: str = ""  # uma faixa fixa para o vídeo todo (senão escolhe por clima em library/music)
    volume: float = 0.12  # nível da música enquanto você fala (relativo à voz; 0.12 ≈ -18 dB)
    pause_volume: float = 0.3  # nível nas pausas e antes da fala começar
    duck_hold: float = 0.6  # segundos de pausa antes da música subir (não "bombeia" entre palavras)
    min_segment_seconds: float = 35.0  # a música só troca de clima depois disso
    max_segment_seconds: float = 120.0  # trecho mais longo que isso troca de faixa (mesmo clima)
    crossfade_seconds: float = 2.5
    fade_in_seconds: float = 1.0
    fade_out_seconds: float = 3.0
    title_breath: bool = True  # a música respira (cai por um instante) antes de título/capítulo


@dataclass
class ReviewConfig:
    """Revisão visual dos candidatos pelo Claude (ver broll_bot/review.py)."""

    mode: str = "off"  # "off" | "api"; o modo chat é pelas flags da CLI
    # "auto" = a mesma IA do plano de cenas (analysis.provider); ou "anthropic" | "openai"
    provider: str = "auto"
    model: str = "claude-opus-5-5"
    openai_model: str = "gpt-5-mini"  # precisa enxergar imagens
    effort: str = "low"
    max_tokens: int = 8000
    candidates_per_scene: int = 8  # miniaturas por grade
    scenes_per_request: int = 10
    rounds: int = 2  # rejeitou demais -> busca mais e revisa os novos


@dataclass
class RenderConfig:
    chunk_size: int = 12
    workers: int = 0  # shots renderizados em paralelo (0 = automático, metade dos núcleos, até 6)
    intermediate_crf: int = 15
    intermediate_preset: str = "veryfast"
    keep_intermediates: bool = False
    max_media_cache_gb: float = 8.0  # cache de imagens/vídeos baixados (.cache/media): acima disso sai o mais antigo


@dataclass
class CostsConfig:
    """Preços das IAs (US$ por 1 milhão de tokens: [entrada, saída]) por cima da tabela de usage.py e da HeyGen
    (US$ por segundo de vídeo, por chamada na criação) por cima da tabela de avatar.py."""

    prices: dict[str, list[float]] = field(default_factory=dict)
    heygen: dict[str, float] = field(default_factory=dict)
    images: dict[str, float] = field(default_factory=dict)  # US$ por imagem, ex.: {"gpt-image-1/medium": 0.063}


@dataclass
class AvatarConfig:
    """Apresentador da HeyGen falando com a SUA narração (lábios sincronizados), na abertura, na chamada para se
    inscrever e no final: em tela cheia (a imagem do personagem do canal animada, com o cenário dela) ou num canto.
    Cada canal tem o seu (``avatar:`` em ``canais/<canal>.yaml``). Liga sozinho quando o canal tem a imagem do
    personagem (``photo``) ou um ``avatar_id`` e o ``.env`` tem a ``HEYGEN_API_KEY``."""

    enabled: bool = True  # chave do vídeo (app: Ajustes); sem imagem/ID no canal não faz nada
    avatar_id: str = ""  # ID do avatar (pronto da HeyGen ou o seu) ou da foto falante
    kind: str = "avatar"  # "avatar" | "photo" (foto falante: talking photo)
    style: str = "normal"  # enquadramento da HeyGen: normal | closeUp | circle
    moments: list[str] = field(default_factory=lambda: ["intro", "cta", "outro", "checkin"])
    # imagem do personagem (ex.: o mecânico já dentro da oficina): a HeyGen anima essa imagem com o lip-sync da
    # narração. Caminho a partir da pasta do bot (o app guarda em canais/avatar/). Com ela, o avatar_id é opcional.
    photo: str = ""
    # "full" = tela cheia, do jeito que a imagem é (o cenário é o da imagem); "corner" = num canto, recortado do verde
    layout: str = "full"
    # motor da foto falante: "avatar_iv" (mexe cabeça, corpo e mãos; gasta mais crédito) ou "standard"
    # (mexe boca e cabeça). Se a HeyGen recusar o Avatar IV, o bot refaz no padrão e avisa no report.
    engine: str = "avatar_iv"
    motion: str = "expressive"  # no motor padrão: "expressive" (mais expressão) ou "stable"
    position: str = "right"  # canto de baixo: right | left
    size: float = 0.5  # altura do avatar na tela (fração da altura do vídeo)
    crop: float = 0.5  # largura do recorte central do clipe da HeyGen (o avatar fica no meio)
    intro_seconds: list[float] = field(default_factory=lambda: [6.0, 12.0])  # mínimo e máximo
    cta_seconds: float = 10.0
    # aparições extras no começo do vídeo (retenção): uma a cada ``checkin_every`` segundos até ``checkin_until``
    # (0 = desliga), cada uma com uma frase inteira entre o mínimo e o máximo de ``checkin_seconds``
    checkin_every: float = 60.0
    checkin_until: float = 60.0  # só uma extra, perto de 1:00 (180 = uma por minuto até 3:00)
    checkin_seconds: list[float] = field(default_factory=lambda: [4.0, 9.0])
    outro_seconds: list[float] = field(default_factory=lambda: [4.0, 10.0])
    max_seconds: float = 75.0  # teto por vídeo (créditos da HeyGen); as aparições extras são cortadas primeiro
    wait_minutes: float = 20.0  # espera mínima pela HeyGen (desde o início); enquanto o vídeo monta, segue esperando
    # narração que já tem avatar gerado (versão 2, 3...): usa os mesmos clipes e NUNCA paga a HeyGen de novo, mesmo
    # que os trechos mudem uns segundos. true = gera tudo de novo (paga outra vez)
    regenerate: bool = False
    key_color: str = "#00FF00"  # fundo que a HeyGen pinta e o bot recorta
    similarity: float = 0.17  # recorte do verde (chromakey)
    blend: float = 0.06


@dataclass
class CharacterConfig:
    """Fotos do personagem do canal em cena (broll_bot/character.py), geradas pela OpenAI a partir da imagem do
    avatar do canal: logo depois de cada aparição do avatar e em momentos fortes do roteiro."""

    enabled: bool = True  # chave do vídeo (app: Ajustes); sem imagem do personagem no canal não faz nada
    after_avatar: bool = True  # uma imagem logo depois de cada aparição do avatar (menos a do fim)
    strong_max: int = 4  # momentos fortes por vídeo ("o motor quebra", "a conta passa de £2.000"); 0 = desliga
    min_gap: float = 75.0  # distância mínima entre duas imagens do personagem (s)
    model: str = "gpt-image-1"
    quality: str = "medium"  # low | medium | high (custo: ver costs.images)
    size: str = "1536x1024"
    wait_minutes: float = 10.0  # passou disso, o trecho fica com o b-roll que já tinha
    # biblioteca do canal (canais/personagem/<canal>/): reaproveita imagens já geradas nos próximos vídeos
    reuse: bool = True
    reuse_match: float = 0.34  # quanto da fala precisa bater com a da imagem guardada para reaproveitar (0-1)
    library_max: int = 24  # com tantas imagens guardadas, para de gerar e só reaproveita (as menos usadas)


@dataclass
class ChannelConfig:
    """Perfil do canal (canais/<canal>.yaml): nome, idioma, moeda, paleta e o que o canal não usa."""

    name: str = ""
    slug: str = ""  # nome do arquivo (bernard_chevalier)
    language: str = ""  # "" = detecta no roteiro
    currency: str = ""  # "£", "€", "$", "R$", "zł" ("" = a moeda falada/do idioma)
    palette: dict[str, Any] = field(default_factory=dict)
    no_templates: list[str] = field(default_factory=list)
    no_overlays: list[str] = field(default_factory=list)
    videos_dir: str = ""  # pasta onde ficam os vídeos do canal


@dataclass
class AppConfig:
    seed: int = 42
    output: OutputConfig = field(default_factory=OutputConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    director: DirectorConfig = field(default_factory=DirectorConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {
            "pexels": ProviderConfig(requests_per_minute=3.0, burst=40, min_interval_seconds=1.0,
                                     per_hour=200),  # 200/h oficial
            "pixabay": ProviderConfig(requests_per_minute=90.0, burst=10, locale="en"),  # 100/min oficial
            "mixkit_local": ProviderConfig(),
            "wikimedia": ProviderConfig(requests_per_minute=30.0, burst=3),
            # desligado (03/11): sem chave ele dava 429/401 ou não respondia; no vídeo da Operação Greif foram 41
            # esperas de 20 s e 0 imagens usadas. O código fica: com a chave no .env, ligue em providers.openverse
            "openverse": ProviderConfig(enabled=False, requests_per_minute=20.0, burst=4, max_retries=2),
            "youtube_cc": ProviderConfig(requests_per_minute=30.0, burst=3, max_retries=2, timeout_seconds=60.0),
            # desligado por padrão: vídeos com direitos reservados, a decisão de usar é do dono do canal
            "youtube_any": ProviderConfig(enabled=False, requests_per_minute=30.0, burst=3, max_retries=2,
                                          timeout_seconds=60.0),
        }
    )
    ranking: RankingConfig = field(default_factory=RankingConfig)
    timeline: TimelineConfig = field(default_factory=TimelineConfig)
    templates: TemplatesConfig = field(default_factory=TemplatesConfig)
    overlays: OverlaysConfig = field(default_factory=OverlaysConfig)
    sfx: SfxConfig = field(default_factory=SfxConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    wikimedia: WikimediaConfig = field(default_factory=WikimediaConfig)
    named_images: NamedImagesConfig = field(default_factory=NamedImagesConfig)
    narration: NarrationConfig = field(default_factory=NarrationConfig)
    captions: CaptionsConfig = field(default_factory=CaptionsConfig)
    vertical: VerticalConfig = field(default_factory=VerticalConfig)
    openverse: OpenverseConfig = field(default_factory=OpenverseConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    youtube_cc: YouTubeCCConfig = field(default_factory=YouTubeCCConfig)
    youtube_any: YouTubeAnyConfig = field(default_factory=YouTubeAnyConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    avatar: AvatarConfig = field(default_factory=AvatarConfig)
    character: CharacterConfig = field(default_factory=CharacterConfig)
    costs: CostsConfig = field(default_factory=CostsConfig)

    # Segredos (somente via ambiente).
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    wikimedia_contact: str | None = None  # e-mail/site para o User-Agent exigido pela Wikimedia
    heygen_api_key: str | None = None  # avatar (broll_bot/avatar.py)
    openverse_client_id: str | None = None
    openverse_client_secret: str | None = None
    youtube_api_key: str | None = None  # vídeos Creative Commons do YouTube
    serper_api_key: str | None = None  # Google Imagens (fotos dos nomes do roteiro: broll_bot/named_images.py)


T = TypeVar("T")


_MERGED_DICTS = ("default_seconds", "style_min_seconds")
# listas que o config.yaml completa: os templates novos do código entram mesmo com um config.yaml antigo (antes a lista
# do YAML substituía a do código e os templates novos nunca saíam); para desligar um, use templates.styles_off
_MERGED_LISTS = ("styles", "provider_order")  # bancos novos também (para desligar: providers.<nome>.enabled)


def _build(cls: type[T], data: dict[str, Any] | None, base: T | None = None, merge_lists: bool = False) -> T:
    """Mescla recursivamente um dicionário YAML sobre os defaults da dataclass."""
    obj = base if base is not None else cls()
    if not data:
        return obj
    for f in dataclasses.fields(obj):  # type: ignore[arg-type]
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(obj, f.name)
        if dataclasses.is_dataclass(current) and isinstance(value, dict):
            setattr(obj, f.name, _build(type(current), value, current, merge_lists))
        elif f.name in _MERGED_DICTS and isinstance(current, dict) and isinstance(value, dict):
            # tempos por estilo: o YAML completa a tabela do código (antes a substituía e os templates novos,
            # que não estão no config.yaml do usuário, perdiam o tempo próprio)
            setattr(obj, f.name, {**current, **value})
        elif merge_lists and f.name in _MERGED_LISTS and isinstance(current, list) and isinstance(value, list):
            setattr(obj, f.name, list(value) + [x for x in current if x not in value])
        else:
            setattr(obj, f.name, value)
    return obj


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None, env_file: str | Path | None = ".env") -> AppConfig:
    if load_dotenv is not None and env_file and Path(env_file).exists():
        load_dotenv(env_file)

    raw: dict[str, Any] = {}
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        # ajustes pessoais em config.local.yaml (fora do git): o git pull nunca trava por causa deles
        local = Path(path).with_name("config.local.yaml")
        if local.exists():
            with open(local, encoding="utf-8") as fh:
                raw = _deep_merge(raw, yaml.safe_load(fh) or {})

    providers_raw = raw.pop("providers", None) or {}
    cfg = _build(AppConfig, raw, merge_lists=True)  # canal e ajustes do vídeo (channels.py) ainda podem restringir
    for name, pdata in providers_raw.items():
        base = cfg.providers.get(name, ProviderConfig())
        cfg.providers[name] = _build(ProviderConfig, pdata, base)

    off = set(cfg.templates.styles_off)
    cfg.templates.styles = [x for x in cfg.templates.styles if x not in off]
    apply_pace(cfg)
    apply_format(cfg)
    cfg.pexels_api_key = os.getenv("PEXELS_API_KEY") or None
    cfg.pixabay_api_key = os.getenv("PIXABAY_API_KEY") or None
    cfg.wikimedia_contact = (os.getenv("WIKIMEDIA_CONTACT") or "").strip() or None
    cfg.heygen_api_key = (os.getenv("HEYGEN_API_KEY") or "").strip() or None
    cfg.openverse_client_id = (os.getenv("OPENVERSE_CLIENT_ID") or "").strip() or None
    cfg.openverse_client_secret = (os.getenv("OPENVERSE_CLIENT_SECRET") or "").strip() or None
    cfg.youtube_api_key = (os.getenv("YOUTUBE_API_KEY") or "").strip() or None
    cfg.serper_api_key = (os.getenv("SERPER_API_KEY") or "").strip() or None
    # navegador logado no YouTube (anti-robô): no .env, pelo app, para não mexer no config.yaml
    if (os.getenv("YOUTUBE_COOKIES_FROM_BROWSER") or "").strip():
        cfg.youtube_cc.cookies_from_browser = os.getenv("YOUTUBE_COOKIES_FROM_BROWSER", "").strip().lower()
    if (os.getenv("YOUTUBE_COOKIES_FILE") or "").strip():
        cfg.youtube_cc.cookies_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    _validate(cfg)
    return cfg


FAST_PACE = dict(min_clip_seconds=1.3, max_clip_seconds=4.2, transition_min=0.16, transition_max=0.4,
                 hard_cut_probability_in_scene=0.5, hard_cut_probability_scene_change=0.2, effect_probability=0.45)


def review_provider(cfg: AppConfig) -> str:
    """IA da revisão visual: a configurada ou, em "auto", a mesma do plano de cenas."""
    if cfg.review.provider != "auto":
        return cfg.review.provider
    return "openai" if cfg.analysis.provider == "openai" else "anthropic"


def is_vertical(cfg: AppConfig) -> bool:
    return (cfg.output.format or "").lower() in ("vertical", "shorts", "9:16")


def apply_format(cfg: AppConfig) -> None:
    """Formato vertical: 1080x1920, busca de mídia em pé, legenda, corte de pausas e ritmo de vídeo curto.
    Pode rodar mais de uma vez (config, canal, ajustes do vídeo): o resultado é o mesmo."""
    if not is_vertical(cfg):
        return
    v = cfg.vertical
    cfg.output.width, cfg.output.height = v.width, v.height
    cfg.search.orientation = "portrait"
    cfg.search.min_width = min(cfg.search.min_width, v.width)  # vídeo em pé de 1080 de largura é o normal
    cfg.narration.cut_silence = cfg.narration.cut_silence or v.cut_silence
    if v.captions:
        cfg.captions.enabled = True
        if not getattr(cfg.captions, "_style_set", False) and cfg.captions.style == CaptionsConfig().style:
            cfg.captions.style = v.caption_style
    if not v.overlays:
        cfg.overlays.kinds = [k for k in cfg.overlays.kinds if k in ("subscribe",)]
    if not v.avatar:
        cfg.avatar.enabled = False
    tl = cfg.timeline
    for key in ("min_clip_seconds", "max_clip_seconds", "transition_min", "transition_max",
                "hard_cut_probability_in_scene", "hard_cut_probability_scene_change", "effect_probability"):
        setattr(tl, key, getattr(v, key))
    # imagem de 16:9 numa tela em pé: passeio lateral (pan) e recorte; o inset deixa faixa desfocada demais
    tl.video_layouts = {"full": 0.3, "zoom_crop": 0.35, "push": 0.3, "inset": 0.05}
    tl.image_layouts = {"kenburns": 0.95, "inset": 0.05}
    # cortes de vídeo curto: zoom de soco (punch) e chicote (whip) na frente; as assinaturas longas ficam raras
    tl.effect_weights = {**tl.effect_weights, "punch": 2.2, "whip": 1.6, "glitch": 0.8, "wash": 0.6, "burn": 0.5,
                         "leak": 0.5, "blackout": 0.3}
    cfg.templates.rhythm_gap = min(cfg.templates.rhythm_gap or v.rhythm_gap, v.rhythm_gap)


def apply_pace(cfg: AppConfig) -> None:
    """Ritmo rápido: planos curtos (1,3–4,2 s), mais cortes secos e mais efeitos de corte."""
    if cfg.timeline.pace == "fast":
        for key, value in FAST_PACE.items():
            setattr(cfg.timeline, key, value)


def _validate(cfg: AppConfig) -> None:
    tl = cfg.timeline
    if tl.max_clip_seconds > 7.0:
        raise ValueError("timeline.max_clip_seconds não pode passar de 7 segundos")
    if tl.min_clip_seconds * 2 > tl.max_clip_seconds - tl.transition_max:
        raise ValueError("min_clip_seconds muito alto para max_clip_seconds/transition_max")
    if not 0 <= tl.transition_min <= tl.transition_max:
        raise ValueError("transition_min/transition_max inválidos")
    if not 0 < cfg.templates.min_seconds <= cfg.templates.max_seconds:
        raise ValueError("templates.min_seconds/max_seconds inválidos")
    if cfg.analysis.provider not in {"anthropic", "openai", "heuristic"}:
        raise ValueError('analysis.provider precisa ser "anthropic", "openai" ou "heuristic"')
    if cfg.review.provider not in {"auto", "anthropic", "openai"}:
        raise ValueError('review.provider precisa ser "auto", "anthropic" ou "openai"')
    if cfg.search.strategy not in {"compare", "fallback"}:
        raise ValueError('search.strategy precisa ser "compare" ou "fallback"')
    if cfg.review.mode not in {"off", "api"}:
        raise ValueError('review.mode precisa ser "off" ou "api"')
    if cfg.output.width % 2 or cfg.output.height % 2:
        raise ValueError("resolução de saída precisa ter dimensões pares")
    if cfg.avatar.kind not in {"avatar", "photo"}:
        raise ValueError('avatar.kind precisa ser "avatar" ou "photo"')
    if cfg.avatar.layout not in {"full", "corner"}:
        raise ValueError('avatar.layout precisa ser "full" ou "corner"')
    if cfg.avatar.position not in {"right", "left"}:
        raise ValueError('avatar.position precisa ser "right" ou "left"')
    m = cfg.music
    if not (0 <= m.volume <= 1 and 0 <= m.pause_volume <= 1.5):
        raise ValueError("music.volume/pause_volume fora da faixa (0–1)")
    if m.min_segment_seconds <= 0 or m.max_segment_seconds < m.min_segment_seconds:
        raise ValueError("music.min_segment_seconds/max_segment_seconds inválidos")
