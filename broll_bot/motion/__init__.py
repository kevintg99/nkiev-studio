"""Motion graphics: templates desenhados quadro a quadro (Pillow/NumPy) e codificados pelo FFmpeg."""

from .templates import TEMPLATES, MediaRef, TemplateSpec, render_template
from .templates_auto import AUTO_TEMPLATES
from .templates_doc import DOC_TEMPLATES
from .templates_pro import PRO_TEMPLATES
from .templates_brand import BRAND_TEMPLATES
from .templates_news import NEWS_TEMPLATES
from .templates_paper import PAPER_TEMPLATES
from .templates_magnet import MAGNET_TEMPLATES
from .templates_story import STORY_TEMPLATES
from .templates_plus import PLUS_TEMPLATES
from .templates_article import ARTICLE_TEMPLATES
from .templates_docu import DOCU_TEMPLATES
from .templates_map import MAP_TEMPLATES

TEMPLATES.update(AUTO_TEMPLATES)
TEMPLATES.update(DOC_TEMPLATES)
TEMPLATES.update(PRO_TEMPLATES)
TEMPLATES.update(BRAND_TEMPLATES)
TEMPLATES.update(NEWS_TEMPLATES)
TEMPLATES.update(PAPER_TEMPLATES)
TEMPLATES.update(MAGNET_TEMPLATES)
TEMPLATES.update(STORY_TEMPLATES)
TEMPLATES.update(PLUS_TEMPLATES)
TEMPLATES.update(ARTICLE_TEMPLATES)
TEMPLATES.update(DOCU_TEMPLATES)
TEMPLATES.update(MAP_TEMPLATES)

__all__ = ["TEMPLATES", "MediaRef", "TemplateSpec", "render_template"]
