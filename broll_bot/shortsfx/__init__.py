"""Motion de retenção do vídeo curto em pé (só no ``output.format: vertical``).

``plan.py`` lê a fala (palavras com tempo) e a linha do tempo dos shots e decide onde entra cada efeito:

* gancho: título da abertura batendo palavra por palavra, flash, zoom de entrada e carimbo ("PROIBIDO", "$45.000");
* aviso "fica até o final" quando o vídeo é lista/ranking;
* ênfase na palavra forte (número, "nunca", "o mais caro"): zoom de soco, a palavra da legenda muda de cor e cresce,
  um "pop" e, às vezes, um adesivo (dinheiro, velocidade, perigo, recorde...);
* desenhos por cima da foto: círculo e seta "à mão" no detalhe, lupa;
* interrupções de padrão: congelamento com contorno branco, parada em preto e branco, cartas empilhando no corte;
* contador do número dito (0 → 300 km/h), tremida nos impactos, desfoque de movimento nos zooms;
* final em loop (o último quadro se emenda no primeiro) ou aviso de "segue para mais".

``render.py`` aplica tudo quadro a quadro no vídeo montado (antes da legenda, que fica parada por cima).
"""

from .plan import FxEvent, FxPlan, plan_fx
from .render import render_fx

__all__ = ["FxEvent", "FxPlan", "plan_fx", "render_fx"]
