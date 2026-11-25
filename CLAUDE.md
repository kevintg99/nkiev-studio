# Instruções para a IA (Claude Code, Cursor, Codex ou outra)

Você está ajudando a usar e melhorar o **broll_bot + nKiev Studio**, uma automação que monta vídeos para o
YouTube a partir de um roteiro (.txt) e uma narração (.mp3). Leia o `COMECE_AQUI.md` antes de tudo, e o
`README.md` e o `docs/historico.md` antes de mudar algo grande.

## Como trabalhar com o usuário
- Responda na língua do usuário, direto e sem enrolação.
- Chave de API só no `.env` (fora do git). Nunca coloque chave em commit, código, log ou chat.
- Não apague nada do usuário: `.env`, `config.local.yaml`, `canais/`, `projetos/`, `videos/`, `library/`,
  `.cache/`.
- Toda correção no código vem com teste de regressão em `tests/`. Antes de commitar, `python -m pytest -q`
  tem que passar inteiro.
- Os testes usam canais de exemplo fixos em `tests/fixtures/canais/`. Nunca faça teste depender dos canais
  reais em `canais/`, que mudam pelo app.
- Ajuste pessoal vai em `config.local.yaml` (tem prioridade sobre o `config.yaml`).

## O que é o projeto
- **broll_bot** (`broll_bot/`, entrada `main.py`):
  - transcreve a narração (faster-whisper) e divide em cenas;
  - um diretor de IA (OpenAI gpt-5 ou Claude) lê o roteiro e decide ranking, listas, cards, mapas e os nomes que
    precisam de foto;
  - o plano de cenas (gpt-5-mini ou Claude) cria as buscas em inglês;
  - busca b-roll nos bancos (ordem em `search.provider_order` no `config.yaml`): Wikimedia, biblioteca local,
    Pixabay, Pexels, YouTube Creative Commons e, como última reserva, `youtube_any` (trecho de 8 s);
  - o CLIP ranqueia as miniaturas e uma IA revisa;
  - entram templates de motion graphics, overlays, transições, efeitos sonoros, música e o avatar da HeyGen;
  - no fim sai um `report.json` ao lado do vídeo.
- **nKiev Studio** (`studio/`, abre pelo `nKiev Studio.bat`): o app, com fila, canais, estilos e chaves.
- **Formatos:** horizontal 16:9 (vídeo longo, o principal) e vertical 9:16 (Shorts, perfil em `vertical:` no
  `config.yaml`, com legenda animada e motion de retenção em `broll_bot/shortsfx/`).

## Biblioteca local (o que mais melhora o vídeo)
- Pasta `library/mixkit/` (lê subpastas). O bot casa a mídia com as buscas pelo nome do arquivo e da pasta (em
  inglês, com hífens). A pasta vira etiqueta de todos os arquivos dela: nada de pasta genérica.
- `.json` ao lado de cada arquivo: `tags`, `description`, `page_url`, `credit` (vai para os créditos do
  report), `requires` + `requires_min` (o roteiro precisa citar as palavras; pasta de assunto usa 3), `say` e
  `say_not` (só entra na cena cuja fala diz a palavra). O arquivo com `say` vem antes de tudo naquela cena, passa
  mesmo pequeno, conta como bom e repete no máximo `search.max_marked_uses` vezes.
- `library/mixkit/_auto/` recebe sozinha as fotos dos nomes que o diretor listou (Google Imagens com
  `SERPER_API_KEY`, ou Wikimedia sem a chave). Pasta com `_` na frente não vira etiqueta.
- **Licença:** só mídia segura para canal monetizado (Wikimedia CC0/PD/CC BY/CC BY-SA, NASA, imprensa oficial,
  Creative Commons). Vídeo comum do YouTube e foto de notícia, não.

## O fluxo que dá vídeo bom já na primeira versão
1. Leia o roteiro e liste o que é **específico** e o banco grátis não vai ter (aparelho, pessoa, produto, lugar,
   evento, empresa).
2. Pesquise com licença segura: Wikimedia Commons (API `commons.wikimedia.org/w/api.php`, com User-Agent com
   contato) e YouTube Data API com `videoLicense=creativeCommon`. Confira as imagens antes de baixar.
3. Baixe para `library/mixkit/<assunto-em-ingles>/`, com o `.json` de cada arquivo (`credit`, `requires`,
   `requires_min: 3`, `say` com as palavras da fala em que ele deve entrar). Vídeo: .mp4 de até ~30 s; recorte
   o trecho sem legenda nem logo.
4. Produza (app ou `python main.py --audio ... --script ... --canal "..." --ai openai`).
5. Abra o `report.json` e confira cena por cena (`scenes[*].shots[*]`: `source`, `media_id`, `query`,
   `fallback`). Corrija o bot quando for bug (com teste) ou complete a biblioteca, e produza de novo.

## Quando o usuário mandar um report.json
Analise o que deu errado (cenas, buscas, fontes, revisão, templates, tempo de cada etapa), corrija a causa no
bot quando for bug, com teste, e explique o que estava errado e o que mudou.
