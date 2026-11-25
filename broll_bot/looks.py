"""Estilos de vídeo e ajustes escolhidos no app (nKiev Studio), vídeo a vídeo.

Um estilo não troca as cores (elas são do canal, em ``canais/``): ele diz quais templates e overlays
entram e muda o ritmo/efeitos. O que o canal proíbe continua proibido: o estilo só tira, nunca devolve.

``job_overrides(...)`` monta o dicionário que o app grava em ``ajustes.yaml`` e passa ao bot com
``--ajustes``; ``apply_overrides`` (em ``channels.py``) aplica por cima do canal.
"""

from __future__ import annotations

from typing import Any

# templates "de enfeite" de cada família (os de conteúdo — preço, ficha técnica, comparativo... — ficam
# em todos os estilos, porque é a fala que pede)
_PAPER = ["dossier", "scrapbook"]
_TV = ["news", "comment", "poll", "headline"]
_BLUEPRINT = ["showcase", "gallery", "sketch"]
# família telejornal: o que o ritmo do bot põe vira a versão de TV (templates_news.py e as tarjas)
NEWS_SWAP: dict[str, str | list[str]] = {
    "statement": "bulletin", "showcase": ["report", "newsfloat"], "gallery": "videowall", "sketch": "newsfloat",
    "dossier": "report", "card": ["report", "newsfloat"], "mosaic": "videowall", "ranking": "newsrank",
    "timeline": "newstimeline", "comparison": "newscompare", "versus": "newscompare"}
NEWS_OVERLAY_SWAP = {"keyword": "lowerthird", "file": "lowerthird", "phrase": "ticker", "impact": "ticker",
                     "years": "lowerthird"}
# família papel (templates_paper.py): polaroides, alfinetes, linha do tempo e comparativo no papel
PAPER_SWAP = {"statement": "paperstatement", "showcase": "paperfloat", "card": "paperfloat", "sketch": "paperfloat",
              "gallery": "paperpair", "mosaic": "paperpair", "timeline": "papertimeline", "comparison": "papercompare",
              "versus": "papercompare", "ranking": "paperrank"}
PAPER_OVERLAY_SWAP = {"keyword": "papertag", "years": "paperyears"}
# família documentário (templates_docu.py): papel rasgado sobre carvão, máquina de escrever e anotação à mão
DOCU_SWAP = {"statement": "docphrase", "paperstatement": "docphrase", "headline": "docphrase", "chart": "docbars"}


def _both(a: dict[str, Any], b: dict[str, Any]) -> dict[str, list[str]]:
    """Mix: cada recurso pode virar a versão de TV ou a de papel."""
    def opts(v: Any) -> list[str]:
        return list(v) if isinstance(v, list) else [v] if v else []

    return {k: opts(a.get(k)) + opts(b.get(k)) for k in sorted(set(a) | set(b))}


LOOKS: dict[str, dict[str, Any]] = {
    "minimal": {
        "nome": "Minimal Blueprint",
        "descricao": "Prancheta azul, foto do carro flutuando, cotas e legendas limpas.",
        "remover": {"templates": [*_PAPER, "vhs", "news", "comment", "poll"], "overlays": ["file"]},
    },
    "dossie": {
        "nome": "Dossiê",
        "descricao": "Papel: polaroides com fita e alfinete, ranking com carimbo, linha do tempo e comparativo "
                     "no papel, frases datilografadas com círculo vermelho, fichas e etiquetas.",
        "remover": {"templates": ["vhs", "news", "hud"]},
        # escolheu Dossiê: o papel entra mesmo num canal que normalmente não usa (a escolha do vídeo vence)
        "liberar": {"templates": ["dossier", "scrapbook", *sorted(set(PAPER_SWAP.values()))],
                    "overlays": ["file", *sorted(set(PAPER_OVERLAY_SWAP.values()))]},
        "templates": {"swap": dict(PAPER_SWAP)},
        "overlays": {"swap": dict(PAPER_OVERLAY_SWAP)},
    },
    "documentario": {
        "nome": "Documentário",
        "descricao": "História e guerra explicadas: papel rasgado sobre fundo escuro, frases datilografadas, "
                     "gráfico de barras no papel, esquemas desenhados à mão (como uma mina funciona, como "
                     "atravessaram o campo), mapas com setas e país x país com as bandeiras.",
        "remover": {"templates": ["vhs", "news", "hud", "comment", "poll", "bulletin"]},
        "liberar": {"templates": ["diagram", "map", "flagversus", *sorted(set(DOCU_SWAP.values()))]},
        "templates": {"swap": dict(DOCU_SWAP)},
        # filme de época do Internet Archive; sem o youtube_any (reivindicação de direitos na filmagem da guerra)
        "archive": {"enabled": True},
        # filme antigo: todo filme de época e 1 em cada 4 cenas de b-roll de vídeo comum
        "timeline": {"old_film_providers": ["archive"], "old_film_chance": 0.25},
        "search": {"skip_providers": ["youtube_any"]},
    },
    "broadcast": {
        "nome": "Broadcast",
        "descricao": "Telejornal: estúdio com telão, foto flutuando, parede de monitores, ranking com o número no "
                     "telão, linha do tempo e comparativo de TV, tarjas e barra de manchete. \"Breaking news\", "
                     "\"ao vivo\" e \"reportagem\" só quando a fala é notícia; no resto, \"ponto-chave\" e "
                     "\"análise\".",
        "remover": {"templates": [*_PAPER, "sketch"]},
        "templates": {"statement_chance": 0.6, "phrase_chance": 0.55, "swap": dict(NEWS_SWAP)},
        "overlays": {"swap": dict(NEWS_OVERLAY_SWAP), "crawl_at": [0.04, 0.5], "crawl_seconds": 24.0},
    },
    "dados": {
        "nome": "Dados",
        "descricao": "Gráficos, números grandes, fichas técnicas e comparativos; cada porcentagem, conta ou "
                     "tempo parado da fala vira gráfico animado.",
        "remover": {"templates": [*_PAPER, "vhs", "news", "comment", "poll", "sketch", "quote"],
                    "overlays": ["file", "phrase"]},
        "templates": {"phrase_chance": 0.2},
    },
    "kinetic": {
        "nome": "Kinetic",
        "descricao": "Muito texto em movimento: frases de destaque, palavras grandes e cortes rápidos.",
        "remover": {"templates": [*_PAPER], "overlays": ["file"]},
        "templates": {"phrase_chance": 0.75, "statement_chance": 0.65, "statement_every": 28.0, "rhythm_gap": 8.0},
        "timeline": {"pace": "fast"},
    },
    "cinema": {
        "nome": "Cinema",
        "descricao": "Mais calmo: cortes mais longos, poucos efeitos e menos texto na tela.",
        "remover": {"templates": [*_PAPER, *_TV, "vhs", "hud", "sketch"], "overlays": ["file", "badge", "subscribe"]},
        "templates": {"phrase_chance": 0.25, "statement_every": 60.0, "rhythm_gap": 16.0},
        "timeline": {"effect_probability": 0.15, "effects": ["leak", "burn", "wash"], "min_clip_seconds": 3.0},
    },
    "mix": {
        "nome": "Mix",
        "descricao": "Tudo liberado: o bot alterna entre os estilos ao longo do vídeo (inclusive o telejornal).",
        "templates": {"swap": _both(NEWS_SWAP, PAPER_SWAP), "swap_chance": 0.4},
        "overlays": {"swap": _both(NEWS_OVERLAY_SWAP, PAPER_OVERLAY_SWAP)},
    },
}

# cara dos templates da edição magnética (percentbar, receipt, textmask...) em cada estilo
SKIN = {"minimal": "blueprint", "dossie": "paper", "documentario": "paper", "broadcast": "tv", "dados": "data", "kinetic": "kinetic",
        "cinema": "cinema", "mix": "mix"}
for _name, _look in LOOKS.items():
    _look.setdefault("templates", {})["skin"] = SKIN[_name]

# ritmo do app: multiplica o intervalo máximo só de b-roll do estilo (menor = mais coisa na tela)
RHYTHM = {"calmo": 1.45, "normal": 1.0, "intenso": 0.7}
DEFAULT_RHYTHM_GAP = 11.0
CAPTION_SIZES = {"pequena": 0.8, "media": 1.0, "grande": 1.2}  # escala da letra da legenda (captions.size)


def job_overrides(style: str = "mix", rhythm: str = "normal", pace: str = "normal", statements: bool = True,
                  phrases: bool = True, music: bool = True, sfx: bool = True, review: bool = False,
                  music_volume: float | None = None, avatar: bool = True,
                  character: bool = True, video_format: str = "horizontal", captions: str = "auto",
                  cut_silence: str = "auto", caption_size: str = "media") -> dict[str, Any]:
    """Estilo + ajustes do vídeo -> seções do config para o ``--ajustes`` do bot."""
    look = LOOKS.get(style, LOOKS["mix"])
    out: dict[str, Any] = {"templates": dict(look.get("templates", {})), "timeline": dict(look.get("timeline", {})),
                           "overlays": dict(look.get("overlays", {}))}
    for key in ("archive", "search"):  # fontes do estilo (o Documentário liga o arquivo histórico)
        if look.get(key):
            out[key] = dict(look[key])
    remove = {"templates": list(look.get("remover", {}).get("templates", [])),
              "overlays": list(look.get("remover", {}).get("overlays", []))}
    gap = out["templates"].get("rhythm_gap", DEFAULT_RHYTHM_GAP)
    out["templates"]["rhythm_gap"] = round(gap * RHYTHM.get(rhythm, 1.0), 1)
    if pace in ("rapido", "fast"):
        out["timeline"]["pace"] = "fast"
    if not statements:
        out["templates"]["statement_chance"] = 0.0
        remove["templates"].append("statement")
    if not phrases:
        out["templates"]["phrase_chance"] = 0.0
        remove["overlays"] += ["phrase", "impact"]
    out["music"] = {"enabled": bool(music)}
    if music_volume is not None:
        out["music"]["volume"] = round(max(0.0, min(0.5, float(music_volume))), 3)
    out["sfx"] = {"enabled": bool(sfx)}
    out["review"] = {"mode": "api" if review else "off"}
    if not avatar:  # avatar da HeyGen do canal fica de fora só neste vídeo
        out["avatar"] = {"enabled": False}
    if not character:  # fotos do personagem em cena: fora só neste vídeo
        out["character"] = {"enabled": False}
    vertical = video_format in ("vertical", "9:16", "shorts")
    if vertical:  # Shorts/Reels/TikTok: o perfil do vídeo curto (config.apply_format) faz o resto
        out["output"] = {"format": "vertical"}
    # legenda: "auto" = liga no vertical (estilo do perfil); "off" = sem legenda; outro = o estilo escolhido
    if captions == "off":
        out["captions"] = {"enabled": False}
        out["vertical"] = {"captions": False}
    elif captions not in ("", "auto"):
        out["captions"] = {"enabled": True, "style": captions}
        out["vertical"] = {"caption_style": captions}
    if captions != "off" and caption_size in CAPTION_SIZES and caption_size != "media":
        out.setdefault("captions", {})["size"] = CAPTION_SIZES[caption_size]
    if cut_silence in ("on", "off"):  # corte das pausas da narração (no vertical já vem ligado)
        out["narration"] = {"cut_silence": cut_silence == "on"}
        out.setdefault("vertical", {})["cut_silence"] = cut_silence == "on"
    out["remover"] = {k: sorted(set(v)) for k, v in remove.items() if v}
    if look.get("liberar"):
        out["liberar"] = {k: list(v) for k, v in look["liberar"].items()}
    return {k: v for k, v in out.items() if v}
