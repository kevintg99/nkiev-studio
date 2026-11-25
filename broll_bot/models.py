"""Estruturas de dados compartilhadas entre as etapas do pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MediaType(str, Enum):
    VIDEO = "video"
    IMAGE = "image"


@dataclass(slots=True)
class Word:
    """Palavra transcrita com timestamps (usada só para sincronizar)."""

    text: str
    start: float
    end: float


@dataclass(slots=True)
class Sentence:
    index: int
    text: str
    paragraph: int
    start: float = 0.0
    end: float = 0.0


@dataclass(slots=True)
class SceneAnalysis:
    visual_summary: str
    queries: list[str]
    fallback_query: str
    negative_terms: list[str] = field(default_factory=list)
    tone: str = "neutral"
    # Idioma das consultas: "en" (padrão, via LLM) ou o idioma do roteiro (modo heurístico).
    query_language: str = "en"
    # Template de motion graphics sugerido para a cena (ver broll_bot/motion).
    style: str = "normal"
    # Palavra-chave curta, no idioma do roteiro, exibida em alguns templates.
    caption: str = ""
    # Itens comparados (template "comparison"): [{"label": ..., "query": ...}]
    items: list[dict[str, str]] = field(default_factory=list)
    # Dados dos templates (ver broll_bot/scenes.py::_SceneOut)
    template_seconds: float = 0.0
    subtitle: str = ""
    stats: list[dict[str, Any]] = field(default_factory=list)
    number: dict[str, Any] = field(default_factory=dict)
    points: list[dict[str, Any]] = field(default_factory=list)
    rank: int = 0
    # quote/news: trechos da fala destacados em marca-texto amarelo
    highlights: list[str] = field(default_factory=list)
    # news/comment: texto mais longo lido na fala (manchete, comentário de inscrito)
    body: str = ""
    # diagram: elementos do esquema desenhado (chão, caixas, bonecos, setas...; templates_docu.py)
    elements: list[dict[str, Any]] = field(default_factory=list)
    # Overlay sobre o b-roll (ver broll_bot/motion/overlays.py): kind, title, detail...
    overlay: dict[str, Any] = field(default_factory=dict)
    # Carro da seção num vídeo de ranking ("Dacia Logan MCV"); "" = vale o assunto do vídeo
    subject: str = ""
    # pedaço de uma cena longa dividida pelo bot: as buscas são as da cena inteira e precisam
    # ser refeitas para o que ESTE pedaço fala (ver query_lang.requery_parts)
    requery: bool = False
    # o que a imagem precisa mostrar, decidido pela fala: "car" (fala de carro: a imagem tem que ser carro),
    # "other" (fala de outra coisa — banco, feira, pessoas, bateria: mostra ISSO) ou "" (o bot decide pela fala)
    shows: str = ""


@dataclass(slots=True)
class Scene:
    index: int
    text: str
    start: float
    end: float
    analysis: SceneAnalysis

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Candidate:
    """Um resultado de busca (ainda não baixado, exceto na biblioteca local)."""

    provider: str
    media_id: str
    media_type: MediaType
    width: int
    height: int
    duration: float | None
    thumbnail_url: str | None
    download_url: str | None
    page_url: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    author: str = ""
    query: str = ""
    local_path: str | None = None
    thumbnail_path: str | None = None
    # Preenchidos pelo ranking.
    score: float = 0.0
    semantic: float = 0.0
    text_penalty: float = 0.0
    review: str = ""  # "approved" | "rejected" pelo revisor visual (por cena)
    # motivo de veto das checagens visuais (não é carro, carro batido, parece outro carro da lista)
    veto: str = ""
    embedding: Any = field(default=None, repr=False, compare=False)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.media_type.value}:{self.media_id}"

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["media_type"] = self.media_type.value
        data.pop("embedding", None)
        for transient in ("score", "semantic", "text_penalty", "review", "veto", "thumbnail_path"):
            data.pop(transient, None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        payload = dict(data)
        payload["media_type"] = MediaType(payload["media_type"])
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})


@dataclass(slots=True)
class Transition:
    kind: str  # nome do xfade ou "cut"
    duration: float  # segundos (0 para corte seco)

    @property
    def is_cut(self) -> bool:
        return self.kind == "cut" or self.duration <= 0


@dataclass
class Shot:
    """Um b-roll na linha do tempo.

    ``cut_in``/``cut_out`` são os pontos de troca (meio da transição) na
    narração. ``start_frame``/``end_frame`` incluem a sobreposição das
    transições e já estão quantizados em frames, o que evita deriva de sync.
    """

    index: int
    scene_index: int
    cut_in: float
    cut_out: float
    clip_duration: float
    transition_in: Transition
    start_frame: int = 0
    end_frame: int = 0
    prefer_media: MediaType = MediaType.VIDEO
    candidate: Candidate | None = None
    media_path: str | None = None
    source_offset: float = 0.0
    speed: float = 1.0
    layout: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str | None = None
    # Template de motion graphics (None = b-roll normal) e seus dados já resolvidos.
    template: str | None = None
    template_data: dict[str, Any] = field(default_factory=dict)
    # Efeitos de corte (whip, punch, glitch, leak) aplicados na entrada/saída do shot.
    effect_in: str | None = None
    effect_out: str | None = None
    # Overlay desenhado por cima do b-roll (rótulo, callout, selo...) já com o início resolvido.
    overlay: dict[str, Any] = field(default_factory=dict)
    # Apresentador da HeyGen no canto (ver broll_bot/avatar.py): clipes que cruzam este shot
    avatar: list[dict[str, Any]] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame
