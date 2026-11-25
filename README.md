# broll-bot: narração + roteiro → vídeo pronto (veja o COMECE_AQUI.md para instalar)

Automação em Python que recebe um **áudio de narração (.mp3)** e o **roteiro (.txt/.md)** e devolve um
`video_final.mp4` **sem legendas**, com b-rolls (vídeos e imagens) que combinam com o que está sendo
dito em cada momento. Os clipes são cortados na duração certa e as transições são feitas com FFmpeg.

```bash
python main.py --audio narracao.mp3 --script roteiro.txt --out video_final.mp4
```

Ou pelo app **nKiev Studio** (janela com canal, roteiro, áudio, estilo e fila de produção): veja
[nKiev Studio (o app)](#nkiev-studio-o-app).

---

## Plano de arquitetura (resumo)

```
narracao.mp3 ─┐
              ├─► 1. transcrição (faster-whisper, só timestamps) ─► 2. alinhamento com o roteiro
roteiro.md ───┘                                                       │
                                                                      ▼
            3. cenas semânticas (Claude: resumo visual, consultas EN, termos a evitar, tom)
                                                                      │
                                                                      ▼
            4. linha do tempo: cortes nas pausas da fala, clipes ≤ 7s com ms "quebrados",
               transições variadas, quantização em frames sem deriva
                                                                      │
                                                                      ▼
            5. busca por cena: Mixkit local → Pexels → Pixabay (cache SQLite, rate limit,
               retry/backoff, circuit breaker, máx. 3 chamadas de API por cena)
                                                                      │
                                                                      ▼
            6. ranking: CLIP compara o resumo visual da cena com a thumbnail real
               + resolução, duração, orientação, penalidade p/ texto/marca d'água
                                                                      │
                                                                      ▼
            7. seleção: baixa só os vencedores (+1 reserva/cena), valida com ffprobe,
               deduplica por hash, evita clipes parecidos em sequência
                                                                      │
                                                                      ▼
            8. FFmpeg: enquadramentos variados (full, zoom/crop, push, inset com fundo
               desfocado, Ken Burns) → xfade/concat em blocos → mux da narração
                                                                      │
                                                                      ▼
                                   video_final.mp4 + report.json + log JSONL
```

**Decisões principais**

| Decisão | Por quê |
|---|---|
| Uma única chamada ao Claude para o roteiro inteiro, com saída estruturada (Pydantic) e cache | Agrupa por *ideia* (não por frase), gera consultas curtas em inglês e custa uma requisição por roteiro. Reexecuções usam o cache. |
| CLIP sobre a **thumbnail** antes de baixar | Mede se a imagem realmente mostra o que a cena descreve. Baixa-se só o vencedor, então thumbnails pequenas economizam banda e requisições. |
| Orçamento de buscas por cena + parada antecipada | Para de buscar assim que há candidatos bons suficientes. Cache hit e biblioteca local não gastam orçamento. |
| Cortes em frames a partir de posições acumuladas | As durações têm milissegundos quebrados, mas a sincronia com o áudio não deriva (erro final < 1 frame + padding do AAC). |
| Tudo com plano B | LLM falhou → heurística. Whisper ausente → estimativa proporcional. Fonte falhou → próxima fonte. Download corrompido → próximo candidato. Render falhou → reserva. Sem nada → fundo gerado. |
| Seed única, com seed derivada por shot | O mesmo `--seed` gera o mesmo vídeo, inclusive com renderização em paralelo. |

---

## Instalação

Requisitos: **Python 3.11+** e **FFmpeg 5+** (com `ffprobe`) no PATH.

```bash
# Ubuntu/Debian: sudo apt install ffmpeg   ·   macOS: brew install ffmpeg   ·   Windows: winget install ffmpeg
cd nkiev-studio
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # CPU: pip install torch --index-url https://download.pytorch.org/whl/cpu
cp .env.example .env                     # preencha PEXELS_API_KEY, PIXABAY_API_KEY (e ANTHROPIC_API_KEY)
```

- Chave do Pexels: <https://www.pexels.com/api/> · Chave do Pixabay: <https://pixabay.com/api/docs/>
- A análise de cenas usa o Claude (`claude-opus-5-5`). Sem credencial da Anthropic, o pipeline usa a
  heurística local automaticamente (funciona, mas as consultas ficam no idioma do roteiro e a relevância cai).
- `faster-whisper`, `open_clip_torch` e `torch` são **opcionais** mas recomendados. Sem eles, o bot usa
  estimativa de tempo proporcional e ranking por metadados.

## Uso

```bash
python main.py --audio narracao.mp3 --script roteiro.md --out video_final.mp4
```

| Flag | Efeito |
|---|---|
| `--config config.yaml` | arquivo de configuração (padrão: `config.yaml`) |
| `--seed 123` | outra variação do vídeo, reproduzível |
| `--no-llm` | análise heurística, sem Claude |
| `--no-clip` | ranking só por metadados (mais leve, sem PyTorch) |
| `--no-transcribe` | não transcreve; estima o tempo de cada frase |
| `--reindex-library` | força reindexar a biblioteca local |
| `--keep-temp` | mantém os shots intermediários em `.work/<execução>/render` |
| `--report caminho.json` | onde salvar o relatório (padrão: ao lado do vídeo) |
| `--export-prompt` | só gera `prompt_cenas.txt` para colar no chat do Claude, e para |
| `--scenes cenas.json` | usa o plano de cenas feito no chat do Claude, sem usar a API |
| `--ai openai` | usa a API da OpenAI (ChatGPT) no plano de cenas e na revisão, no lugar do Claude |
| `--pace fast` | ritmo documentário: planos de 1,3–4,2 s, mais cortes secos e efeitos |
| `--no-sfx` | sem efeitos sonoros |
| `--no-music` | sem música de fundo |
| `--lang fr` | força o idioma do roteiro (`auto`, `pt`, `fr`, `pl`, `en`); o padrão detecta sozinho |
| `--music faixa.mp3` | uma faixa só para o vídeo todo (senão escolhe por clima) |
| `--review` / `--export-review` / `--review-file` | revisão visual dos b-rolls pelo Claude (veja abaixo) |
| `--ajustes ajustes.yaml` | estilo e ajustes deste vídeo por cima do canal (o app grava este arquivo) |
| `--progresso andamento.jsonl` | grava o andamento, uma linha JSON por etapa (o app acompanha por ele) |

## nKiev Studio (o app)

Uma janela para produzir sem linha de comando: escolhe o canal, cola o roteiro, escolhe o áudio, o
estilo e os ajustes, e o vídeo vai para uma fila que produz um por vez.

**Instalar (Windows, uma vez):** dois cliques em `instalar_app.bat`. Ele instala o que falta
(`requirements.txt`, inclusive o `pywebview` da janela), cria o `.env` se não existir e põe o atalho
**nKiev Studio** na área de trabalho. Depois é só abrir pelo atalho (ou `nKiev Studio.bat`).
Em outro sistema: `python -m studio` (janela) ou `python -m studio --navegador` (mesma tela no navegador).

| Tela | O que faz |
|---|---|
| **Produzir** | 4 etapas. **1 · Canal e conteúdo:** canal (o idioma do roteiro sugere o canal sozinho), preset do canal, nome do vídeo, roteiro (colar ou abrir `.txt`, com contagem de palavras e minutos) e áudio (avisa se roteiro e áudio não batem). **2 · Estilo:** Minimal Blueprint, Dossiê, Documentário, Broadcast, Dados, Kinetic, Cinema ou Mix. **3 · Ajustes:** ritmo dos recursos, ritmo de corte, telas pretas, frases de destaque, música e volume, efeitos sonoros, revisão das imagens pela IA. **4 · Revisar:** resumo e tempo estimado. |
| **Concluídos** | Os vídeos prontos saem da Fila e ficam numa lista (vídeo, canal, data, duração e custo). Clicando num vídeo abre a página dele: canal, estilo, duração e data no card da direita, abrir o vídeo/mostrar na pasta, **Produzir novamente** (volta para a tela Produzir com o roteiro, o áudio, o título e o canal já preenchidos; você escolhe de novo o estilo e os ajustes e sai um vídeo novo, sem apagar o anterior) e as abas Custos (o custo do vídeo) (por etapa, tokens, por minuto de vídeo, buscas nos bancos), Arquivos, Diagnóstico e Tags e descrição. Os totais (vídeos, minutos, gasto do mês e total, por canal) ficam em **Configurações → Gastos com IA**. Estimativa pelos tokens e a tabela de preços (`config.yaml` → `costs.prices`); o valor oficial está no painel da OpenAI/Anthropic. |
| **Fila** | Um vídeo por vez, num processo separado (o app continua leve). Abas: **Acompanhamento** (6 etapas, a barra com a porcentagem concluída, tempo de produção, algumas imagens escolhidas, cancelar; sem previsão de término, que errava muito), **Diagnóstico** (referências, imagens repetidas, trechos sem imagem boa, tempo por etapa), **Arquivos** (abrir o vídeo, copiar os créditos, relatório e pasta do projeto) e **Tags e descrição** (capítulos tirados do vídeo + descrição, tags e títulos escritos pela IA no idioma do canal). Deu erro: mostra o motivo e tem **Tentar de novo** (mesmos ajustes) e **Mudar estilo e ajustes**. |
| **Canais** | Os canais com as cores e o que não usam; **Novo canal** com nome, idioma, moeda, assunto (Automotivo ou Variado/Top 10), cores e visual de papel (ligado/desligado). **Editar** abre o mesmo formulário preenchido: trocar o nome muda também a pasta padrão dos vídeos e o nome nos vídeos já feitos (o arquivo do canal e os presets continuam os mesmos; não dá para renomear enquanto um vídeo do canal está sendo produzido). |
| **Configurações** | Chaves das APIs (salvas só no `.env` deste computador, com botão **Testar**), a IA usada (Claude ou ChatGPT), o **diretor de IA** (liga/desliga), a **aparência do app** (Grafite, Ônix, Ardósia, Sálvia ou Areia, que é o tema claro; só a janela, os vídeos seguem o canal) e o que este computador usa para codificar e transcrever. |
| **Biblioteca** | Pastas de imagens por item criadas a partir do roteiro: **na próxima atualização**. |

- **Estilo × canal:** as cores são sempre do canal; o estilo diz quais templates/overlays entram e muda
  ritmo e efeitos (`broll_bot/looks.py`). O estilo só tira recursos, nunca devolve o que o canal proibiu
  (Jhonny Walt + Dossiê = sem o papel do dossiê, o resto igual).
- **Presets:** "Salvar como preset do canal" guarda estilo + ajustes em `canais/<canal>.presets.json`
  (o YAML do canal, que você edita à mão, não é reescrito).
- **Projetos:** cada vídeo ganha uma pasta em `projetos/` com o roteiro, `ajustes.yaml`, o andamento,
  o `log.txt`, o `report.json` e a descrição gerada. O vídeo sai em `videos/<canal>/`.
- **Nicho Variado:** o canal novo desliga as checagens de "cara de carro" nas imagens e o card de consumo.
- **Fechar:** o app pergunta antes; fechar com um vídeo em produção para o vídeo (ele fica como "Deu
  erro" e tem **Produzir de novo**). No modo navegador, o app fecha sozinho quando a aba fecha e a fila acaba.

### Usando a API da OpenAI (ChatGPT) no lugar do Claude

Coloque `OPENAI_API_KEY=` no `.env` e rode com `--ai openai` (ou `analysis.provider: openai` no
`config.yaml`). O plano de cenas e a revisão visual (`--review`) passam a usar o ChatGPT, com o mesmo prompt
do modo chat e resposta em JSON:

```bash
python main.py --audio narracao.mp3 --script roteiro.txt --ai openai --review
```

O modelo fica em `analysis.openai_model` e `review.openai_model` (padrão `gpt-5-mini`, bem mais barato que
o `gpt-5`), e o raciocínio em `analysis.openai_effort` (padrão `low`). Raciocínio mais alto gasta mais
tokens de saída, a parte cara. O terminal mostra os tokens de cada chamada, e rodar de novo o mesmo roteiro
não cobra outra vez (plano e revisão ficam em cache). A revisão precisa de um modelo que enxergue imagens.

### Sem créditos na API: usando o chat do Claude

Dá para ter a mesma qualidade de cenas usando o chat do Claude (claude.ai), sem gastar crédito de API.
A ordem é **roteiro → narração → prompt → chat → vídeo** (o prompt usa o tempo real de cada frase na narração):

```bash
# 1. gera o prompt a partir da narração e do roteiro
python main.py --audio narracao.mp3 --script roteiro.txt --export-prompt
# 2. abra prompt_cenas.txt, copie TUDO, cole no chat do Claude
#    e salve a resposta (o JSON) como cenas.json nesta pasta
# 3. gera o vídeo com esse plano
python main.py --audio narracao.mp3 --script roteiro.txt --scenes cenas.json
```

O arquivo pode ter a resposta com as crases de markdown (```json … ```), que o bot aceita. Se o roteiro mudar,
gere o prompt de novo, porque o plano usa o número de cada frase.

#### Revisão visual pelo chat (opcional, recomendada)

O CLIP não reconhece marca/modelo: para ele um Mercedes prata é um ótimo "sedan prata". Na revisão visual,
o Claude **olha as miniaturas** de cada cena junto com o trecho narrado e reprova o que não combina
(outra marca, esportivo no lugar de sedan, plantas, texto na tela...). Os reprovados saem da disputa e o bot
busca mais nas fontes quando precisar.

```bash
# 1. faz as buscas e salva uma grade numerada por cena em revisao/ (não renderiza)
python main.py --audio narracao.mp3 --script roteiro.txt --scenes cenas.json --export-review
# 2. no chat do Claude, anexe todas as imagens revisao/cena_XX.jpg, cole o conteúdo de
#    revisao/prompt_revisao.txt e salve a resposta (o JSON) como revisao/resposta.json
# 3. gera o vídeo aplicando a revisão
python main.py --audio narracao.mp3 --script roteiro.txt --scenes cenas.json --review-file
```

Os vereditos ficam no cache (`.cache/`): nas próximas execuções com o mesmo roteiro eles continuam valendo.
Com a API liberada, basta `--review` (ou `review.mode: api` no `config.yaml`): uma requisição com as
grades de até 10 cenas, com cache por cena e clipe.

Saídas:

- `video_final.mp4`: H.264 + AAC, `yuv420p`, `+faststart`, 1920x1080@30 (configurável), com exatamente a duração da narração.
- `report.json`: para cada cena, o texto narrado, o resumo visual, as consultas, as tentativas de busca
  (fonte, cache, erro) e, para cada shot, fonte, ID, URL, score, duração aplicada, transição, enquadramento,
  ponto de entrada e fallback usado. Inclui também a lista de **créditos** (autor e URL) para atribuição.
- `.work/<execução>/pipeline.jsonl`: log estruturado de todas as etapas.

### Wikimedia Commons (fotos do modelo exato)

O Commons tem fotos organizadas por modelo e geração (Corolla E100, E170...), justamente o que falta nos
bancos de stock. Não tem chave: basta `WIKIMEDIA_CONTACT=seu-email` no `.env` (a Wikimedia pede que quem
consulta se identifique). Entram só fotos grandes com licença domínio público, CC0 ou CC BY. As CC BY-SA
ficam de fora por padrão (`wikimedia.allow_share_alike`). Autor, licença e link de cada foto usada saem nos
créditos do `report.json`; a maioria exige esse crédito na descrição do vídeo.

### Arquivo histórico (Internet Archive: filmes de época em domínio público)

No estilo **Documentário** o bot busca também filmes de época no Internet Archive: os cinejornais da Universal
(1929–1967) e os filmes do governo americano (Exército, Signal Corps, Arquivo Nacional), todos em domínio público.
Não tem chave. A busca acha o filme; o Internet Archive guarda um quadro a cada 30–60 s e cada quadro vira um
trecho de 8 s que o ranking e a revisão comparam com a fala; baixa só esse trecho, sem som. O filme de época
(640 px) entra sempre com o **filme antigo** (grão, risco, luz piscando, sépia), e 1 em cada 4 cenas de b-roll
comum também ganha o efeito, para o clima ficar igual. No Documentário o `youtube_any` fica de fora (filmagem da
guerra no YouTube costuma ter reivindicação de quem revende material de domínio público). Para um canal de história
usar o arquivo em qualquer estilo: `archive: {enabled: true}` no perfil do canal. Ajustes em `archive:` e
`timeline.old_film_*` no `config.yaml`.

### Openverse (fotos Creative Commons de Flickr, museus e acervos)

**Desligado por padrão** (`providers.openverse.enabled: false`): sem chave ele não respondia ou bloqueava, e
travava a busca sem dar imagem nenhuma. Para voltar, cadastre a chave (abaixo) e ligue no `config.local.yaml`.

O Openverse junta centenas de milhões de fotos CC e de domínio público, com autor e licença em cada uma: é
onde aparecem pessoas reais, lugares e fatos históricos que os bancos de stock não têm. Vem **primeiro** na
ordem de busca, junto com o Wikimedia (`search.provider_order`), e os dois ganham um pequeno bônus no ranking
(`ranking.provider_bonus`) para o empate ir para a foto do assunto exato. Funciona sem chave; com
`OPENVERSE_CLIENT_ID`/`OPENVERSE_CLIENT_SECRET` no `.env` o limite sobe (cadastro grátis:
`python -m broll_bot.providers.openverse --email voce@exemplo.com` e confirme o e-mail). Entram CC0, domínio
público e CC BY (CC BY-SA só com `openverse.allow_share_alike`); o crédito vai para a descrição.

### YouTube Creative Commons (vídeos CC BY)

Com `YOUTUBE_API_KEY` no `.env` (chave grátis da YouTube Data API v3 no Google Cloud), o `yt-dlp` e o Deno instalados (o `instalar_app.bat` instala os dois; desde 2025.11 o yt-dlp precisa do Deno para o YouTube),
o bot busca vídeos com licença Creative Commons. Cada vídeo vira um trecho de 25 s do meio dele (o mesmo
quadro que o YouTube usa de miniatura automática, que é o que o CLIP e a revisão conferem); só esse trecho
é baixado. Entra como **reserva** (`search.reserve_providers`): só quando os outros bancos não trouxeram
mídia boa. Travas contra "CC falso" (reenvio de TV/filme): canal com pelo menos 1.000 inscritos
(`youtube_cc.min_subscribers`) e fora títulos de filme, episódio, clipe, notícia, ao vivo e entrevista. A
chave grátis tem 10.000 unidades/dia e cada busca gasta 100: no máximo 30 buscas por vídeo
(`youtube_cc.max_searches_per_video`). Baixar do YouTube vai contra os termos de uso do site, mesmo com a
licença CC permitindo o reuso: para desligar, `providers.youtube_cc.enabled: false`.

Se o relatório disser que o YouTube pediu login ("Sign in to confirm you're not a bot"), o YouTube barrou os
downloads como robô. Faça login no YouTube no **Firefox** (de preferência com uma conta Google secundária) e
ponha `youtube_cc.cookies_from_browser: firefox` no `config.yaml`; o yt-dlp usa esse login. No Windows o
Chrome e o Edge trancam os cookies; outra saída é exportar um `cookies.txt` (extensão "Get cookies.txt
LOCALLY") e pôr o caminho em `youtube_cc.cookies_file`. Quando o YouTube barra, o bot para de tentar o
YouTube no resto do vídeo (não perde tempo nem cota).

### Como nomear os arquivos da biblioteca local

O nome do arquivo e o nome das pastas viram as tags de busca. O bot também "olha" cada arquivo (CLIP) e,
com `--review`, a IA vê as miniaturas dos seus arquivos junto com as dos bancos. Seus arquivos ganham um
pequeno bônus (`ranking.local_bonus`) quando já combinam com a cena.

- Em inglês, com hífen: `marca-modelo-ano/geração-cor-o que aparece-ação`.
- Anos e códigos de geração contam (`2020`, `e100`, `xei`); números soltos (ids) são ignorados.
- Organize por modelo em pastas; o nome da pasta entra nas tags de tudo que está dentro:

```
library/mixkit/
  toyota-corolla-2020/
    white-driving-city-front.mp4
    led-headlight-closeup-night.mp4
    interior-dashboard-multimedia.mp4
    engine-bay-2-0-flex.mp4
  toyota-corolla-e100-1993/
    sedan-parked-street-side.jpg
  honda-civic-2020/
    black-driving-highway.mp4
```

### Canais (um perfil para cada canal)

Cada canal tem um arquivo em `canais/` com o que é só dele: nome, idioma, moeda padrão, paleta de
cores, templates/overlays que ele não usa e a pasta dos vídeos. O `config.yaml` continua sendo a base.

| Canal | Idioma | Moeda | Identidade | Não usa |
|---|---|---|---|---|
| **Bernard Chevalier** (`canais/bernard_chevalier.yaml`) | francês | € | azul-marinho, blueprint azul, texto e destaques amarelos | — |
| **Jhonny Walt** (`canais/jhonny_walt.yaml`) | inglês (UK) | £ | cinza-escuro com detalhes vermelhos; a prancheta (foto do carro, galeria, desenho) azul-escura | dossiê e ficha de papel |

- **Sem escolher nada:** o bot detecta o idioma do roteiro e usa o canal daquele idioma.
- **Escolhendo:** `python main.py --audio narracao.mp3 --script roteiro.txt --canal "Jhonny Walt"`.
- **Listar:** `python main.py --canais`.
- **Vídeos:** saem em `videos/<canal>/` com data e hora no nome (o report vai junto); `--out` continua valendo.
- **Novo canal:** pelo app (tela Canais) ou copiando um arquivo de `canais/` e trocando o bloco `canal:` (ver `canais/LEIA-ME.md`).

### Avatar apresentador (HeyGen)

Cada canal pode ter o seu personagem (ex.: o mecânico dentro da oficina). A HeyGen **anima a imagem** e faz o
**lip-sync com a sua narração**, e o clipe entra **em tela cheia**, do jeito que a imagem é, em três momentos:
**abertura** (primeiras frases, 6–12 s), **chamada para se inscrever** (a primeira frase com "subscribe",
"inscreva-se", "abonnez-vous"...; o botão de inscrição continua por cima), **final** (últimas frases) e, para
segurar a retenção, **uma aparição extra perto de 1:00** (`checkin_every`/`checkin_until`; `checkin_until: 180` faz
uma por minuto até 3:00): uma frase inteira de 4–9 s, de preferência sem números (ali entra o gráfico) e nunca no meio de uma lista.
Onde o apresentador aparece em tela cheia o bot não monta template (ninguém veria).

1. Cole a chave em **Configurações → HeyGen** (vai só para o `.env`).
2. Em **Canais → Editar → Imagem do personagem**, escolha a imagem pronta (o personagem já com o cenário, rosto de
   frente e bem visível). Ela é copiada para `canais/avatar/` e sobe para a HeyGen uma vez só.
3. Nos **Ajustes** de cada vídeo aparece "Avatar apresentador (HeyGen)" para ligar/desligar só naquele vídeo.

**ID da HeyGen (para nunca enviar a imagem de novo):** cada envio da imagem cria um personagem novo na sua conta
HeyGen e custa US$ 1,32. Crie o personagem **uma vez** em app.heygen.com (Avatars → Photo Avatar, com a imagem do
canal) e, em **Canais → Editar → ID do avatar na HeyGen → Escolher da minha conta**, escolha ele (o marcado "Foto
falante"). Com o ID no canal o bot usa ele direto e **nunca envia a imagem**; a imagem do canal continua sendo a
referência das fotos do personagem. Se o bot precisar enviar (canal sem ID), ele guarda o ID que a HeyGen devolveu
no próprio canal (`avatar.avatar_id`) e não envia mais; trocar a imagem no app tira esse ID junto. Se o ID sumir da
conta, o vídeo sai sem avatar e o report diz para conferir o ID (o bot não envia a imagem por conta própria).

Movimento: **Avatar IV** (padrão; mexe cabeça, corpo e mãos, gasta mais crédito) ou **Padrão** (boca e cabeça). Se a
HeyGen recusar o Avatar IV (plano), o bot refaz no padrão e o motivo aparece no report (`avatar.segments[].note`).

**Custo:** entra no gasto do vídeo (report `usage`, app → Concluídos → Custos) pela tabela da API da HeyGen: foto falante com Avatar IV US$ 0,0385/s, no padrão (Avatar III) US$ 0,0165/s; avatar pronto/de vídeo com Avatar IV
US$ 0,0805/s; envio da imagem do personagem US$ 1,32 (uma vez por imagem). Clipe que veio do cache não conta. O teste da chave em Configurações mostra o saldo da carteira, e o report traz `avatar.wallet_usd` depois do vídeo. Se a HeyGen mudar a tabela: `costs.heygen` no `config.yaml`.

Também dá para pôr o avatar **no canto** (Na tela → No canto: a HeyGen gera sobre verde e o bot recorta) ou usar um
avatar pronto da sua conta HeyGen em vez de uma imagem. Os clipes ficam em `.cache/avatar`: refazer o mesmo vídeo
**não gasta crédito de novo**. Enquanto o vídeo ainda busca e renderiza, o bot continua esperando a HeyGen; só
desiste se, na hora de montar, já passou de `avatar.wait_minutes` (20 min desde o início) e a HeyGen ainda não
terminou (mais 1 min de folga). Aí o vídeo sai sem o avatar e o `report.json` diz o motivo (`avatar.segments`).
**Versão 2, 3... da mesma narração nunca paga a HeyGen de novo:** usa os clipes já gerados (mesmo que os trechos
mudem uns segundos) e momento novo fica sem avatar. Para gerar de novo de propósito: `avatar.regenerate: true`. Teto de `avatar.max_seconds` (40 s) por vídeo.

### Imagens do personagem em cena

Com a imagem do personagem no canal (a mesma do avatar) e a chave da OpenAI, o bot gera **fotos do mesmo
personagem fazendo o que a fala diz** (segurando a peça, apontando para o motor, olhando a conta) e põe no lugar
do b-roll, com o zoom suave das fotos:

- **logo depois de cada aparição do avatar** (menos a do final);
- **em até 4 momentos fortes** do roteiro ("destroys its own bearings", "le moteur est mort", "o motor quebra"),
  com pelo menos 75 s entre uma e outra e nunca colado no avatar.

Cada canal usa o seu personagem como referência (`gpt-image-1`, qualidade média, 1536x1024, ~US$ 0,06 por
imagem; ajuste em `character:` e `costs.images` no `config.yaml`). As imagens ficam em `.cache/personagem`: refazer
o vídeo não gasta de novo. Liga/desliga por vídeo nos Ajustes ("Imagens do personagem em cena"). O report mostra
cada momento em `character` e o gasto entra em Custos.

**Biblioteca do canal:** toda imagem gerada fica guardada em `canais/personagem/<canal>/` (com a fala que ela
ilustra, em `imagens.json`). Nos próximos vídeos do canal, um momento parecido com uma imagem já feita reaproveita
a imagem (status `reused`, sem custo); quando a biblioteca chega a `library_max` (24) imagens, o bot para de gerar
e só reaproveita, as menos usadas primeiro. Cada canal tem a sua; trocar a imagem do personagem começa outra. Apague
da pasta a imagem que não ficou boa e ela não volta. Desliga com `character.reuse: false`.

### Espaço em disco

No app, em **Concluídos → abrir o vídeo → Excluir e liberar espaço** (e no vídeo com erro/cancelado, **Excluir**):
some da lista e apaga a pasta do projeto, as imagens e vídeos que o bot baixou para aquele vídeo (`.cache/media`,
`.cache/thumbs`) e a pasta de trabalho dele (`.work`). Ficam: o vídeo pronto (`videos/`), o seu áudio, os clipes do
avatar e as imagens do personagem (que custaram dinheiro). O app mostra quanto liberou.

Sozinho, o bot já apaga os trechos de render e a narração mixada ao terminar cada vídeo, e mantém o cache de
mídias abaixo de `render.max_media_cache_gb` (8 GB; os usados há mais tempo saem primeiro).

### Idiomas: português (Brasil), francês, polonês e inglês

O bot detecta sozinho o idioma do roteiro (`analysis.script_language: auto`) e ajusta tudo a ele:

- **Transcrição:** o Whisper usa o mesmo idioma (`transcription.language: auto`).
- **Números e moeda na tela:** pt `R$ 129.900` / `9,2 s`; fr `32 900 €`; pl `119 900 zł`; en `$12,000` /
  `£9,000` / `9.5 s` (em inglês a vírgula é separador de milhar: "12,000" = doze mil). Os números por
  extenso também são entendidos na checagem de coerência ("cent", "mille", "sto", "tysiąc", "twelve",
  "fifteen grand"...).
- **Detector de templates:** as pistas existem nos quatro idiomas (`broll_bot/cues.py`), por exemplo:
  - "Number 8: the Toyota Corolla" / "between 9,000 and 13,000 dollars" / "8 to 12 grand";
  - "gets 32 mpg in the city" / "35 to 40 miles per gallon" (card de consumo em mpg);
  - "de 0 à 100 km/h en 9,2 secondes" / "od 0 do 100 km/h w 9,2 sekundy";
  - "points forts / points faibles" / "zalety / wady";
  - "Un abonné a écrit :" / "Jeden z widzów napisał:";
  - "laquelle choisiriez-vous" / "którą byście wybrali".

  Em polonês, os nomes declinados voltam à forma do dicionário ("Corollę" → "Corolla").
- **Rótulos dos gráficos:** PRÓS/POINTS FORTS/ZALETY, NOTA FINAL/NOTE FINALE/OCENA KOŃCOWA,
  INSCREVER-SE/S'ABONNER/SUBSKRYBUJ, BARATO/BON MARCHÉ/TANIO…
- **Glossário do nicho:** "génération"/"generacja" é a geração do carro, "boîte"/"skrzynia" é o câmbio.
- **Botão de inscrição:** entra nas chamadas em qualquer um dos três ("abonnez-vous", "zasubskrybuj",
  "łapka w górę").

Para forçar um idioma: `--lang fr` (ou `pt`, `pl`, `en`), ou `script_language: fr` no `config.yaml`. Roteiros
de exemplo que acionam todos os templates: `exemplos/roteiro_teste_templates.txt` (pt), `_fr.txt` e
`_pl.txt`. As buscas de imagem continuam em inglês (a IA traduz), então os bancos funcionam igual.

### Atualizar o bot

Rode o **`atualizar.bat`** (em vez do `git pull` direto): se o `config.yaml` foi mexido à mão, ele guarda uma
cópia em `config.yaml.antigo`, leva o navegador do YouTube para o `.env` e atualiza sem travar. Ajustes
pessoais vão no **`config.local.yaml`** (mesmo formato do `config.yaml`, só com o que você muda; o git não
mexe nele) ou no app, em Configurações.

**PC com placa NVIDIA:** o `atualizar.bat` (e o `instalar_app.bat`) troca o torch pela versão com CUDA e instala
as bibliotecas da NVIDIA que a transcrição usa (`python -m studio.gpu`; uns 3 GB na primeira vez). Sem isso o
CLIP roda no processador e a transcrição na placa falha. Se a placa falhar mesmo assim, a transcrição roda na
CPU (mais lenta, mas sincroniza pela fala). Sem placa NVIDIA, nada muda.

### Pasta nova para uma sessão do Claude Code no seu computador

Rode o **`preparar_sessao.bat`**. Ele faz quatro coisas:
- baixa o bot do GitHub em `Documentos\nKiev Bot` (já ligado ao git);
- copia seus arquivos pessoais: `.env`, `config.local.yaml`, `canais`, `projetos`, `library` e `.cache`;
- instala as dependências;
- aponta o atalho do nKiev Studio para a pasta nova.

A pasta antiga fica como está, de backup, e os vídeos prontos continuam nela. No Claude Code, abra a pasta
nova: ele lê o `CLAUDE.md` sozinho (contexto do projeto e as regras) e as tarefas pendentes ficam em `docs/`.

### Vídeo curto em pé (Shorts, Reels, TikTok)

No app, logo no começo do vídeo novo (**Que tipo de vídeo?**), escolha **Vídeo vertical** (ou `output.format: vertical` no `config.yaml`,
no canal ou no `--ajustes`). O perfil do vídeo curto (`vertical:` no `config.yaml`) liga junto:

- **1080x1920** e busca de mídia em pé (Pexels `portrait`; foto deitada também serve: ela passeia de lado
  para encher a tela, nunca fica com faixa).
- **Cortar pausas da narração** (`narration.cut_silence`): os silêncios maiores que 0,35 s viram um respiro
  de 0,12 s, antes da transcrição; o resto do bot já roda no áudio cortado. Também dá para ligar no vídeo
  deitado (chave "Cortar pausas da narração" no app).
- **Ritmo de vídeo curto**: plano de 0,9 a 2,5 s, mais cortes secos, zoom de soco (punch) e chicote (whip)
  como efeitos principais, quebra de padrão a cada ~6 s e uma barrinha de progresso no topo.
- **Legenda animada** queimada no vídeo, no tempo de cada palavra (`captions.style`), com prévia no app:
  `palavra` (uma por vez, com pop), `frase` (2–3 palavras, a falada acende), `caixa` (tarja colorida atrás
  da palavra, estilo Hormozi), `karaoke` (a linha enche de cor), `limpa` (documentário), `impacto` (letra de
  quadrinho, cor alternada), `neon` e `maquina` (máquina de escrever). A cor de destaque é a do canal
  (`captions.color` troca). Enquanto um template está na tela, a legenda desce para não cobrir o texto dele.
  Prévias: `python -m broll_bot.captions` (gera `studio/web/legendas/`).
- **Templates em pé** (`broll_bot/motion/templates_vertical.py`): os que já funcionam em pé ficam como estão
  (letra na escala da largura); as famílias que mais aparecem ganharam versão nativa com o conteúdo na metade
  de cima — número gigante contando, "#3"/"1/5" com a foto, duas fotos empilhadas com "VS", lista com ✓/✗,
  pilha de fotos trocando e citação com foto redonda; o resto entra como cartão sobre a imagem desfocada.
- Nenhum recurso sai do vídeo em pé: os overlays (rodapé, tarja, letreiro...) são desenhados acima da faixa
  de baixo, onde o TikTok/Shorts põe a descrição e os botões, com a letra na escala da largura; o avatar da
  HeyGen continua (gerado em 16:9 e recortado no meio, onde o personagem está; no canto ele fica menor e acima
  dos botões). O vídeo deitado continua exatamente como antes.
- **Motion de retenção** (`broll_bot/shortsfx/`, `vertical.fx`): uma passada final, quadro a quadro, no tempo
  das palavras, por baixo da legenda (no mesmo encode dela):
  - **gancho** nos 2–3 primeiros segundos: a frase de abertura batendo palavra por palavra, flash, zoom de
    entrada, carimbo vermelho ("BANNED", "$45,000") e um baque grave. O diretor de IA escreve o gancho e o
    carimbo (`hook`/`stamp`, só com palavras das primeiras frases); sem ele, vale a 1ª frase curta;
  - aviso **"fica até o final"** quando o vídeo é ranking ("o nº 1 é absurdo") ou lista;
  - **ênfase** nas palavras fortes (número, "nunca", "o mais caro", "proibido"...): a palavra da legenda muda
    de cor e bate maior, zoom de soco e um "pop"; às vezes um **adesivo** desenhado (dinheiro, velocidade,
    perigo, recorde, relógio, fogo, seta subindo/descendo, ✓/✗);
  - **desenhos** por cima da foto: círculo e seta "à mão" no detalhe (o ponto com mais detalhe da imagem) e
    **lupa** que aumenta esse ponto;
  - **interrupções de padrão**: congelamento com contorno branco e "?" no fim da pergunta, parada em **preto e
    branco** com disco arranhado no "Mas..." do começo da frase, **cartas empilhando** em alguns cortes;
  - **contador** do número dito (0 → 250 km/h, $45.000, 40%), tremida nos impactos, desfoque de movimento nos
    zooms, e um zoom lento sempre que passariam ~3 s sem nada novo na tela;
  - **acabamento**: cor um pouco mais viva, grão e vinheta leves (`vertical.fx_grade`);
  - **final em loop** (`vertical.ending: loop`): o último quadro se emenda no primeiro e o Short recomeça sem
    corte; `cta` mostra "SEGUE PARA MAIS", `both` faz os dois, `off` nenhum.

  Em cima de template não entra desenho (só a ênfase da legenda), e efeito grande nunca cai junto de outro.
  `vertical.fx_density` (padrão 1) controla a quantidade; `vertical.fx: false` desliga tudo. O report traz o
  que entrou em `captions.fx`.

### Diretor de IA (o roteiro entendido antes de tudo)

Antes do plano de cenas, uma IA mais forte (`director.openai_model`, padrão `gpt-5`; ou o Claude em
`director.model`) lê o roteiro **inteiro** e devolve o plano de edição do vídeo:

* **nicho** do vídeo (carros, finanças, culinária...), assunto principal e moeda;
* **seções do ranking**: o cabeçalho ("Number eight."), o nome como vai na tela ("Chevrolet Blazer",
  "Hyundai IONIQ 5") e até onde vai (recapitulação e pedido de like não são mais do carro);
* **listas numeradas** ("One. The sales numbers." / "Round three. Money and time.");
* **cards de dado** com os campos prontos (barra de %, nota da oficina, fonte, preço, de X para Y, gráfico...),
  com os números já escritos como na tela ("C$4,000", "2026s");
* o **vocabulário visual** do nicho: o que é imagem do assunto e o que é fora dele (fase 2, `director.niche`);
* os **nomes que precisam de foto** (até 15): produto, loja, pessoa, lugar. Para cada um vem a busca de
  imagem em inglês, as palavras da fala que marcam a cena (`say`) e os nomes parecidos onde a foto não entra
  (`say_not`). Veja "Fotos dos nomes do roteiro" abaixo.

Nada entra sem conferência (`broll_bot/director.py::validate`): número que não foi dito, nome que não está
no roteiro, card em cima de cabeçalho ou colado em outro card saem, e o motivo vai para o `report.json`
(`director.dropped`). Com o plano válido, quem decide ranking, listas e cards é o diretor; a IA das cenas
continua escolhendo as buscas e os templates decorativos. Se a chamada falhar (sem chave, API fora, resposta
vazia), o vídeo sai com as regras de sempre. Custo: uma chamada por roteiro (centavos de dólar), e roteiro
repetido reaproveita o plano do cache.

**Troca de nicho:** com `director.niche: true`, um roteiro de culinária num canal de carros deixa de exigir
carro nas imagens: o veto "não mostra carro" vira "fora do nicho do vídeo", com as frases que o diretor deu
para o assunto. Num vídeo de carro, essas frases somam ao que conta como imagem do nicho ("winter tire tread",
"salt truck"), o que derruba os vetos errados.

### Assunto do vídeo e glossário do nicho

O bot acha no roteiro o carro principal (a marca + modelo mais citados, ex.: "toyota corolla"; dá para fixar em
`analysis.subject`) e usa isso em tudo: o prompt pede que as buscas das cenas sobre ele citem o modelo, toda
busca sem carro nenhum ("young generation") ganha o nome do carro, e a revisão visual reprova carros de outra
marca mesmo quando a frase não repete o nome. No nicho automotivo, palavras ambíguas têm sentido fixo:
"geração" é a geração do carro (não pessoas), "câmbio" é a caixa de marcha (não dinheiro), "banco" é o assento,
"bateria" é a do carro. Nas cenas em que aparecem, o bot também põe o sentido errado na lista do que evitar.

#### Vídeo de ranking / lista com vários carros

Quando o roteiro tem cabeçalhos como `Numéro 8 : la Dacia Logan MCV.` / `Número 3: o Honda Fit.` /
`Numer 2: Skoda Octavia.` (com 2 ou mais carros), não existe um assunto único: **cada seção tem o seu carro**,
do cabeçalho até o próximo (ou até a despedida, "avant de vous laisser", "antes de terminar", "na koniec").

* O plano de cenas recebe o trecho de cada carro, e as buscas de cada cena citam o carro da seção dela.
* Os outros carros da lista entram nos termos a evitar (menos os citados na própria cena, numa comparação).
* A revisão visual sabe o carro de cada cena e só reprova outro modelo quando a fala não o cita.
* O cabeçalho vira uma cena própria com o cartão `ranking` (#8 gigante + foto + nome + detalhe após a vírgula);
  no estilo Broadcast ele vira `newsrank` (número no telejornal) e no Dossiê `paperrank` (carimbo "Nº 8").
* Roteiro feito para voz de IA também vale (`broll_bot/spoken.py`): "Number ten." / "And number one." sozinho,
  com o nome na frase seguinte ("The Range Rover Evoque, the current shape, ..."), pulando frases sem nome
  ("The most expensive car on this list."); siglas e números soletrados viram o nome escrito ("Mercedes G L E"
  -> Mercedes GLE, "Audi Q two" -> Audi Q2, "B M W X three" -> BMW X3, "Jaecoo Seven" -> Jaecoo 7) e anos por
  extenso viram ano ("since twenty nineteen" -> 2019). Menção no meio da frase ("and number one is the car
  from the start") ou pergunta ("remember number eight?") não abre seção.
* Cena longa que a IA juntou (Lexus + Audi + Qashqai em 100 s) é dividida e **cada pedaço busca primeiro o
  carro que ele fala**; busca de um carro que o pedaço não cita (e que não é o da seção) sai.
* Plano B da biblioteca local nunca usa a pasta de outro carro: só a do carro da cena/vídeo ou pastas sem
  marca (oficina, estrada...).
* Faixa de preço falada ("entre 3 000 et 6 000 euros", "entre 50 e 70 mil reais", "od 20 do 35 tys. zł")
  vira a etiqueta `price` com os dois valores e o nome do carro embaixo.

Escreva o cabeçalho sozinho, numa linha (parágrafo) própria, com marca e modelo — é ele que define as seções.
Para fotos do modelo exato, ponha na biblioteca local arquivos com o nome do carro (`dacia-logan-mcv-1.jpg`,
`honda-jazz-2010.jpg`...) e deixe o Wikimedia Commons ligado.

### Coerência do plano de cenas

Depois que a IA monta o plano, o bot confere cada template e overlay contra o que é falado naquela cena:
números precisam estar na fala (por extenso também vale), itens do versus/comparativo precisam ser citados,
títulos, tópicos e trechos em marca-texto precisam ter palavras da fala, e o VHS pede uma referência ao
passado. O que não bate vira b-roll normal (ou perde só a linha errada), e a lista de ajustes sai no
`report.json` (`coherence_fixes`).

Ajustes estruturais feitos em seguida (`broll_bot/plan_fix.py`):

- **Cena longa demais** (acima de `analysis.split_scene_seconds`, padrão 16 s) é dividida nas frases;
  cada parte começa por uma busca diferente, para o vídeo não ficar alternando os mesmos dois clipes.
- **Buscas que descrevem o gráfico** ("title card", "number two", "icon") são trocadas pelas da cena
  seguinte — o título apresenta o que vem depois, então a filmagem é a dela.
- **Título/capítulo em cena curta** (até 6,5 s) ocupa a cena inteira, sem b-roll solto depois.
- Na revisão por IA, candidato que a IA não citou conta como reprovado; e um mesmo clipe se repete no
  máximo `search.max_uses_per_clip` vezes (padrão 3).

### Fotos dos nomes do roteiro (automático)

Banco grátis não tem foto da "MotoMaster Eliminator" nem da loja Canadian Tire. Antes da busca, o bot baixa
as fotos de cada nome que o diretor listou (`broll_bot/named_images.py`):

1. Se a biblioteca já tem um `.json` com o mesmo `say`, não baixa nada.
2. Senão, busca no Google Imagens pela API do Serper (`SERPER_API_KEY` no `.env`; cadastro em serper.dev com
   2.500 buscas grátis, e cada vídeo usa umas 15). Sem a chave, busca na Wikimedia.
3. Só entra resultado cujo título cita o nome (a bateria do concorrente fica de fora). A foto vira 16:9 com o
   produto inteiro no meio e a mesma foto desfocada atrás.
4. Grava em `library/mixkit/_auto/<nome>.jpg` com o `.json` pronto (`requires`, `say`, `say_not`). A foto só
   entra em vídeo que cita o nome, e só na cena que diz o nome.
5. Quem a fala cita a toda hora (o presidente num vídeo de política) ganha mais fotos, para a mesma cara não
   repetir: +1 a cada 6 citações, até 6 fotos (`named_images.mentions_per_extra` e `per_name_max`). Só esses
   nomes completam fotos que faltam; quem já tem uma foto na biblioteca (até a posta à mão) fica com ela.
   Político tem retrato oficial na Wikimedia (domínio público), então funciona mesmo sem a chave do Serper.

A foto da web vem **sem licença verificada** (o crédito diz isso no `report.json`). Para usar só a Wikimedia:
`named_images.web: false`. Para desligar tudo: `named_images.enabled: false`. O resumo fica em
`report.json` → `named_images`. Pode apagar ou trocar qualquer foto da `_auto`: o bot só baixa de novo um nome
que não tem foto.

### Biblioteca local (Mixkit)

O Mixkit não tem API pública e não permite scraping. Baixe os clipes manualmente em
`library/mixkit/` (os nomes originais, como `mixkit-woman-typing-on-laptop-4321-hd-ready.mp4`, já viram tags).
Opcionalmente, crie um `.json` com o mesmo nome do clipe para melhorar a busca:

```json
{"tags": ["office", "laptop", "typing"], "description": "woman typing on a laptop", "page_url": "https://mixkit.co/..."}
```

O índice (duração, resolução, thumbnail, tags) é incremental: só arquivos novos ou alterados são reprocessados.
A biblioteca local é a primeira fonte da fila por ser gratuita e ilimitada (não gasta orçamento de API).

### Estrutura

```
nkiev-studio/
├── main.py                  # CLI
├── config.yaml              # parâmetros (resolução, fontes, limites, durações, seed…)
├── broll_bot/
│   ├── cli.py               # argumentos e execução
│   ├── pipeline.py          # orquestração + report.json
│   ├── transcription.py     # parse do roteiro, faster-whisper, alinhamento
│   ├── scenes.py            # cenas semânticas (Claude ou heurística)
│   ├── timeline.py          # cortes, durações ≤ 7s com ms quebrados, transições
│   ├── search.py            # busca por cena: orçamento, cache, fallback, circuit breaker
│   ├── ranking.py           # CLIP + critérios secundários, similaridade entre clipes
│   ├── selection.py         # download dos vencedores, reserva, deduplicação, planos B
│   ├── framing.py           # enquadramentos e Ken Burns (filtergraphs)
│   ├── assembler.py         # FFmpeg: shots, xfade/concat em blocos, mux do áudio
│   ├── cache.py             # cache SQLite com TTL
│   ├── ratelimit.py         # token bucket, retry/backoff, circuit breaker
│   ├── http.py              # cliente HTTP com leitura de X-RateLimit-*
│   ├── media.py             # ffprobe/ffmpeg
│   ├── looks.py             # estilos do app (Minimal, Dossiê, Broadcast...) e ajustes por vídeo
│   ├── progress.py          # andamento para o app (uma linha JSON por etapa)
│   └── providers/           # BaseProvider, Pexels, Pixabay, biblioteca local
├── studio/                  # app nKiev Studio: janela (pywebview), fila, canais, chaves, relatório
│   └── web/index.html       # a tela
└── tests/                   # cache, fallback, http, timeline, alinhamento, cenas, providers
```

---

## Como o bot garante relevância

1. **Cena por ideia.** O Claude lê o roteiro com o tempo de cada frase e decide onde o assunto muda.
2. **Consulta pensada para acervos de stock.** Consultas de 2–4 palavras, nomeando sujeito e ação
   (`"woman typing laptop"`), da mais específica para a mais genérica, além de uma consulta reserva.
3. **Ranking visual.** O CLIP compara o *resumo visual* da cena (ex.: "close-up de mãos contando moedas
   numa mesa de madeira") e as consultas com a thumbnail real de cada candidato. Metadados (tags, título)
   entram só como desempate.
4. **Penalidades.** Imagens com texto, logo ou marca d'água, e candidatos mais parecidos com os
   *termos a evitar* do que com a cena, perdem pontos. Resolução baixa e orientação vertical também.
   **Checagens visuais** (`ranking.visual_checks`): num canal de carro, imagem que não mostra carro (prédio,
   paisagem, retrato, documento) é barrada, a não ser que a cena peça isso; carro batido ou queimado é
   barrado, a não ser que a fala seja sobre acidente; no ranking com vários carros, cada carro tem fotos de
   referência (as da pasta dele e, se faltar, as do Wikimedia cujo título cita o modelo) e a foto que parece
   mais **outro carro da lista** é barrada. A mesma foto vinda de outra fonte não entra duas vezes no vídeo.
   O `report.json` mostra quantas imagens cada checagem barrou (`vetoes`).
5. **Todos os bancos, o melhor vence** (`search.strategy: compare`). Cada consulta vai para todos os bancos
   ligados (biblioteca local, Pexels, Pixabay, Wikimedia) e o ranking visual escolhe as melhores imagens
   entre todos. Com `fallback`, a busca para no primeiro banco que já tem mídia boa (menos chamadas de API).
6. **Score e relevância mínimos.** Um clipe só conta como bom com `min_score` **e** relevância visual
   (`min_semantic`, só o CLIP) suficientes. Qualidade, duração e orientação não compensam um clipe fora do
   assunto. Se ninguém atinge, a busca segue para a próxima consulta/fonte, dentro do orçamento da cena
   (a biblioteca local não encerra a busca com clipes pouco relevantes).
7. **Termos a evitar por frase.** Em vídeo sobre um carro específico, o plano lista marcas e categorias que
   contradizem a narração ("sports car", "mercedes", "suv"...). Um termo pega só quando todas as palavras
   dele aparecem nas tags ("sports car" pega "red sports car", mas não qualquer "car").
8. **Revisão visual pelo Claude** (opcional): veja acima. Aprovado pelo Claude vale mais que o CLIP; reprovado
   nunca entra.
9. **Planos B sem mídia fora do assunto.** Sem candidato bom, o bot primeiro usa a sobra relevante da cena
   vizinha, depois **repete um clipe que combina** (outro trecho e enquadramento). Mídia fraca da própria
   cena só entra quando ainda não há nada baixado e nunca muito abaixo do mínimo.
10. **Variedade.** O mesmo clipe só se repete como plano B, com outro enquadramento e ponto de entrada,
   e clipes visualmente quase iguais (cosseno CLIP ≥ 0.93) não entram em sequência.

## Como o bot evita rate limit e bloqueio

- **Cache SQLite** de todas as buscas (TTL de 24h por padrão, como a Pixabay exige), da transcrição, do
  plano de cenas e dos embeddings. Rodar de novo o mesmo roteiro custa quase zero requisições.
- **Orçamento por cena** (`max_searches_per_scene: 6`, +1 por shot acima de 3): cache hit e biblioteca local não contam.
- **Parada antecipada:** assim que há `shots + 1 reserva` candidatos bons, a busca da cena termina.
- **Token bucket por fonte** configurado abaixo dos limites oficiais (Pexels 200/h, Pixabay 100/min).
  O Pexels usa a cota da hora (`providers.pexels.per_hour: 200`): gasta de uma vez o que sobrou da última hora,
  contando as rodadas anteriores em `.cache/ratelimit_pexels.json` (o cabeçalho dele só informa a cota do mês).
  Os headers `X-RateLimit-Remaining`/`Reset` pausam a fonte quando a cota zera.
- **Retry** com backoff exponencial e *jitter* para 429/5xx. Em 429 com reset longo, o bot desiste na hora
  e passa para a próxima fonte, sem ficar esperando.
- **Circuit breaker:** 3 falhas seguidas desativam a fonte por 5 minutos. Chave inválida, cota esgotada ou
  falta de conexão desativam na hora.
- **Downloads mínimos:** só o vencedor de cada shot e uma reserva por cena. Os arquivos ficam em
  `.cache/media` e são reaproveitados entre execuções.

## Velocidade: chip de vídeo e tempo por etapa

- **Codificador:** `output.encoder: auto` (ou `--encoder`) testa o chip de vídeo do computador e usa o primeiro
  que funcionar: NVIDIA NVENC, Intel QuickSync (quase todo notebook Intel) ou AMD AMF. É o mesmo recurso que
  deixa o CapCut rápido. Sem chip (ou driver), fica o libx264 no processador. Se o chip falhar num trecho,
  aquele trecho é refeito no processador. Depois de 3 falhas, o resto do vídeo vai direto no processador.
  Para forçar o processador: `--encoder cpu`.
- **Montagem final sem recodificar:** os blocos de render terminam, sempre que dá, num corte seco (sem transição
  com o shot seguinte). Aí eles já saem na qualidade final e o passo final só cola os arquivos e põe o áudio, sem
  decodificar e codificar o vídeo inteiro de novo.
- **Tempo por etapa:** o fim do log, o resumo e o `report.json` (`timings`) mostram quanto levou a transcrição,
  o plano da IA, a busca/revisão, o render dos trechos e a montagem final.
- **Buscas e downloads em paralelo:** antes da passada cena a cena, a 1ª consulta de cada cena vai aos bancos
  principais ao mesmo tempo (Wikimedia e Openverse uma chamada por vez; Pixabay até 4), por no máximo 2 min. Antes
  da distribuição, os arquivos que cada cena provavelmente vai usar são baixados em paralelo (YouTube no
  máximo 2 por vez). Na v1 do vídeo das baterias os downloads sozinhos levaram 23 min, um por vez.
- **Só o começo dos vídeos longos:** vídeo do Pexels ou do Pixabay com mais de 30 s vem só nos primeiros 24 s,
  sem som (`search.video_head_seconds`; 0 = inteiro). O shot usa no máximo ~16 s e o b-roll entra mudo. No vídeo
  dos rios os vídeos inteiros eram 7,6 GB e 10 min de download. Se o trecho falhar, baixa o arquivo inteiro.
- **Biblioteca local rápida:** o `requires` de cada arquivo é conferido uma vez por vídeo. Antes, cada busca na
  biblioteca procurava as palavras no roteiro inteiro (0,77 s por busca, 62% do tempo da busca).
- **Revisão em paralelo:** a revisão visual manda 4 lotes de cenas ao mesmo tempo para a IA (um de cada vez,
  a 1ª rodada levava 5 min). Lote que falha deixa só aquelas cenas sem revisão.
- **Revisão sem repetir busca:** depois de cada rodada da revisão visual, só as cenas que ganharam veredito
  novo são buscadas de novo. Antes, as 164 cenas do vídeo das baterias eram refeitas a cada rodada.
- **Cache:** na segunda rodada com a mesma narração, a transcrição, o plano, as buscas, a revisão e os
  downloads vêm de `.cache`. Não apague essa pasta entre uma rodada e outra.

## Regras de montagem

- Cada b-roll (vídeo ou imagem) dura **no máximo 7s, já contando a sobreposição da transição**.
  As durações sempre têm milissegundos quebrados (ex.: 3.487s, 5.132s), nunca se repetem e variam entre
  `min_clip_seconds` e 7s.
- Cenas longas são cobertas por vários b-rolls. Os cortes internos procuram as **pausas da fala**.
- Os vídeos entram em pontos diferentes do clipe. Clipes curtos ganham câmera lenta leve (até 1.6x)
  em vez de loop.
- Enquadramentos: `full`, `zoom_crop` (zoom 1.08–1.28 com reenquadramento), `push` (zoom lento com
  easing), `inset` (mídia menor sobre fundo desfocado da própria mídia; sempre usado para mídia vertical)
  e `kenburns` para imagens (zoom in/out, pans e diagonais com intensidade variada).
- Transições `xfade` de 0.2 a 0.6s, sem repetir o tipo em sequência, mais cortes secos (35% dentro da cena,
  10% na troca de cena). Os tipos disponíveis são conferidos no FFmpeg instalado.
- Nenhum texto ou legenda é desenhado no vídeo.

## Motion graphics (templates) e efeitos

São 30 templates animados, desenhados quadro a quadro (Pillow/NumPy) e codificados pelo FFmpeg.
O Claude escolhe **qual** usar e **por quanto tempo** (`template_seconds`), de acordo com o roteiro.
O nicho do canal fica em `analysis.niche` no `config.yaml` (padrão: automotivo).

| Estilo | Quando o Claude usa | O que aparece | Dados no plano |
|---|---|---|---|
| `comparison` | comparação/lista de 2–3 coisas ou gerações | 2–3 cards inclinados, linha do tempo que se desenha, etiquetas com a fala e rótulo em cada card. Fundo claro quadriculado e blueprint escuro, alternados | `items` |
| `versus` | carro A × carro B | tela dividida na diagonal, selo VS, nomes e linhas de comparação com o vencedor destacado | `items` (2), `stats` com `value`/`value_b` |
| `specs` | ficha técnica | foto + linhas com número contando e barra animada | `stats` (2–5) |
| `stat` | um número marcante (preço, FIPE, 0–100) | número gigante contando sobre o vídeo desfocado | `number` |
| `gauge` | velocidade/rotação/potência | velocímetro com ponteiro varrendo até o valor e leitura digital | `number` com `max` |
| `blueprint` | design, dimensões, engenharia | a foto vira traço técnico sendo "plotado", cotas e revelação da foto | `caption`, `stats` (cotas) |
| `checklist` | prós e contras, o que olhar antes de comprar | ✓/✗ entrando no momento em que cada ponto é falado | `points` |
| `ranking` | posição num top | "#3" gigante com foto e nome | `rank`, `caption` |
| `chapter` | mudança de assunto ("agora, o motor") | título de capítulo animado e numerado | `caption`, `subtitle` |
| `mosaic` | muitos exemplos, uma linha inteira | parede 3×3 com destaque e palavra-chave | — |
| `hud` | um detalhe específico | revelação, linha de scan e marcação HUD | `caption` |
| `card` | apresentar um carro/produto | card neon com glitch e legenda | `caption` |
| `vhs` | memória, gerações antigas, experiência pessoal | PLAY/SP/timecode, granulado, tracking | — |
| `headline` | destaque forte: um nome, um modelo, um dado, a virada do roteiro | **sem imagem**: fundo azul-marinho com grade, título digitado letra a letra (som de teclado) e subtítulo. 1,5–3 s | `caption`, `subtitle` |
| `triptych` | 3 valores lado a lado ("47 PSI · 42 PSI · 37 PSI", "2014 · 2018 · 2024") | 3 vídeos em colunas entrando alternados, valor grande em amarelo em cada um | `items` (label = valor) |
| `counter` | porcentagem/proporção ("4 em cada 5") | frase no topo, barra vertical enchendo e número contando, sobre a mídia em duotone azul | `number` (`suffix: "%"`), `caption` |
| `chart` | 2–5 números comparados (frenagem, preço por ano) | gráfico de barras animado sobre azul-marinho, valores contando | `stats`, `caption` |
| `quote` | frase de impacto ("Nenhum carro chega aos 500 mil km sem reparos. Nenhum.") | imagem escurecida com grade fina; começo da frase em branco palavra por palavra e as palavras fortes em marca-texto amarelo com letra cursiva, uma de cada vez | `caption`, `highlights`, `subtitle` |
| `scrapbook` | exemplos, o carro "na vida real" | 1–3 fotos com borda picotada entrando uma a uma num papel quadriculado envelhecido (som de câmera) | `items` (opcional) |
| `timeline` | "o Corolla evoluiu…", as gerações ao longo dos anos | linha do tempo com os anos; a foto de cada geração sobe quando o ano é falado e a câmera anda pela linha (até 6) | `items` (label = ano, query = geração, `note` opcional), `caption` |
| `speedometer` | 0–100 km/h ou velocidade máxima | velocímetro grande; no 0–100 o ponteiro sobe enquanto o cronômetro conta até o tempo falado, com linhas de velocidade | `number` (`suffix: "s"` no 0–100) |
| `datasheet` | ficha técnica lida em voz alta | foto + linhas que entram quando cada dado é falado, número contando, pontilhado e unidade em amarelo | `stats` (2–6), `caption`, `subtitle` |
| `price` | preço / tabela FIPE | etiqueta amarela caindo e balançando, valor contando e medidor **barato ↔ caro** | `number` (`level` 0–1 se a fala julga o preço) |
| `proscons` | prós **e** contras | tela dividida verde/vermelha, cada item entrando na coluna certa quando é falado | `points` (com os dois lados) |
| `score` | veredito com notas | barras por quesito enchendo e anel da nota final contando | `stats` (quesito, nota), `number` (nota final) |
| `magnifier` | um detalhe (farol, roda, emblema) | lupa com zoom no ponto de destaque da imagem, linha e nome | `caption`, `subtitle` |
| `beforeafter` | facelift, geração antiga × nova | cortina deslizante com alça entre as duas fotos, rótulos nos cantos | `items` (2) |
| `news` | notícia, recall, lançamento | recorte de jornal com a manchete e a foto; o marca-texto passa nos trechos fortes | `body`, `subtitle` (fonte), `highlights` |
| `comment` | leitura de um comentário de inscrito | card de comentário digitado, likes e coração do canal | `body`, `subtitle` (@usuário) |
| `poll` | "e você, qual escolheria?" | pergunta digitada, opções A/B/C com foto e "comente aqui" pulsando | `caption`, `items` (2–4) |
| `showcase` | o carro da cena, sem dado específico | a foto do carro flutuando sobre o blueprint azul-escuro, cotas de desenho técnico se desenhando em volta e o nome em amarelo. Sorteia a composição: foto no centro com o nome embaixo, ou foto à esquerda com o nome ao lado e barra amarela | `caption` (nome do carro) |
| `gallery` | o carro em geral, visual, versões | **três fotos** do carro flutuando em camadas sobre o blueprint (a da frente mais rápida, efeito de profundidade), entrando uma a uma; o nome embaixo. As fotos extras vêm da cena e das vizinhas **do mesmo carro** | `caption` |
| `sketch` | design, concepção, "a Toyota desenhou…" | **uma mão com lápis desenha o carro** no blueprint (os traços saem da própria foto) com som de lápis riscando; depois a foto aparece por cima do desenho, com as cotas. A mão é a foto de `assets/sketch_hand.png` (PNG com fundo transparente, lápis apontando para a esquerda: a ponta é achada sozinha). Para trocar, é só substituir o arquivo | `caption` |
| `dossier` | o carro ou um assunto, com uma frase de destaque | **dossiê em papel** (visual próprio, fora do azul do canal): papel bege quadriculado, título datilografado e espaçado em cima, a foto com borda tracejada vermelha e branca e alças nos cantos (como seleção num editor) e uma tira de papel com a frase. Fonte de máquina de escrever: Special Elite | `caption` (título), `subtitle` (frase da tira, copiada da fala) |
| `statement` | frase curta de impacto ("Et c'est une erreur.") | **tela preta** com a frase; a palavra forte em amarelo. Cada uma sorteia uma de 4 animações: palavra por palavra saltando, máquina de escrever com cursor, zoom lento entre filetes ou deslizando com barra lateral | `body`, `highlights` |

**Padrão do canal.** Com `templates.brand_theme: channel` (o padrão), todos os templates usam o mesmo visual:
fundo azul-escuro, grade de blueprint azul-escura e textos amarelos com contorno preto. `brand_theme: ""` volta
aos temas variados.

**Ritmo automático.** Quando passam `templates.rhythm_gap` segundos (11 por padrão) só de b-roll, a cena
seguinte ganha um recurso de ritmo, sempre com texto da própria fala:

- a **tela preta** com uma frase curta de impacto (o intervalo entre telas pretas é sorteado entre
  `statement_every` e ~2x esse valor, e cada frase só vira tela preta com chance `statement_chance`);
- a **frase de destaque** sobre o b-roll, com marca-texto amarelo, ou a **palavra de impacto** grande sobre um
  véu azul translúcido (chance `phrase_chance`);
- a foto do carro da seção, alternando **showcase**, **galeria**, **mão desenhando** e **dossiê em papel**
  (nunca o mesmo duas vezes seguidas; fala de design/concepção puxa o desenho);
- a peça citada ("turbo", "moteur") ou a palavra-chave numa legenda minimalista.

`rhythm_gap: 0` desliga. No roteiro de 11 carros (~22 min) isso dá umas 30 fotos no blueprint, 10 telas
pretas, 6 frases de destaque e 6 cards de consumo, além dos templates de dados que a fala pede.

**Família telejornal (estilo Broadcast do app).** Com o estilo **Broadcast**, depois do ritmo o bot troca cada
recurso pela versão de TV (`templates.swap` e `overlays.swap`; `broll_bot/looks.py`), sempre com o texto da fala:

| No lugar de | Entra | O que aparece |
|---|---|---|
| tela preta (`statement`) | `bulletin` | **plantão**: faixas vermelhas cruzando, tarja "BREAKING NEWS" na diagonal, a frase numa faixa branca palavra por palavra, selo "● AO VIVO" com relógio e a faixa de notícias rolando embaixo; a imagem da cena desfocada atrás |
| foto no blueprint, dossiê, card (`showcase`, `dossier`, `card`) | `report` | **reportagem**: estúdio no tom do canal (feixes de luz, pontos desfocados), o telão com a imagem do carro entrando pela direita, rótulo "REPORTAGEM", o nome grande e a tarja com um trecho da fala |
| foto no blueprint, desenho, card (`showcase`, `sketch`, `card`) | `newsfloat` | **foto flutuando**: a foto com borda branca balançando de leve sobre ela mesma desfocada, etiqueta "EM FOCO", o nome grande do outro lado e a tarja (sorteia com o `report`) |
| cartão de ranking (`ranking`) | `newsrank` | **posição do ranking**: faixas vermelhas cruzando, "#10" gigante num bloco escuro que entra batendo, rótulo "RANKING", o carro no telão com "AO VIVO" e a tarja "#10 · nome — detalhe" |
| linha do tempo (`timeline`) | `newstimeline` | **linha do tempo de TV**: trilho de luz no tom do canal, um monitor por ano subindo quando o ano é falado, o ano numa etiqueta vermelha e a câmera andando se houver mais de 3 |
| comparativo, versus (`comparison`, `versus`) | `newscompare` | **tela dividida**: 2–3 monitores com o nome de cada carro numa tarja, o selo "VS" pulsando entre eles e o título na barra de cima |
| galeria, mosaico (`gallery`, `mosaic`) | `videowall` | **parede de monitores**: 2–3 telas com imagens do mesmo carro ligando uma a uma (efeito de TV de tubo) e a tarja com o nome |
| legenda e ficha do carro (`keyword`, `file`) | `lowerthird` | **tarja de nome**: bloco vermelho com o nome/palavra e a faixa branca com um trecho da fala, abrindo da esquerda |
| frase de destaque e palavra de impacto (`phrase`, `impact`) | `ticker` | **barra de manchete**: rótulo "ÚLTIMAS" e a frase entrando, com a faixa fina rolando embaixo |
| (novo, 2 blocos) | `crawl` | **letreiro do rodapé**: um pouco acima da borda, o carro no bloco vermelho e frases curtas da própria fala passando da direita para a esquerda (preço, consumo, peças têm prioridade; números já escritos, "76.4%"). Entra em **2 blocos contínuos de ~24 s**, um logo no começo e outro perto do meio (`overlays.crawl_at: [0.04, 0.5]`, `crawl_seconds`), só em trecho sem template; atravessa os cortes sem reiniciar e, enquanto passa, não entra template nem outra tarja |

**Família papel (estilo Dossiê do app).** Com o estilo **Dossiê**, a troca é pela versão em papel (bege, tinta
marrom e vermelho; fonte de máquina de escrever), sempre com o texto da fala:

| No lugar de | Entra | O que aparece |
|---|---|---|
| tela preta (`statement`) | `paperstatement` | a frase datilografada letra por letra e um círculo de caneta vermelha em volta da palavra forte |
| foto no blueprint, desenho, card | `paperfloat` | polaroide com fita adesiva flutuando no centro sobre a própria imagem desfocada e lavada de bege; o nome datilografado na foto e a frase numa tira de papel |
| galeria, mosaico | `paperpair` | duas polaroides do mesmo carro presas com alfinete e ligadas por uma linha vermelha (quadro de investigação) |
| linha do tempo (`timeline`) | `papertimeline` | traço de caneta no papel quadriculado, anos datilografados (o da vez em vermelho) e as fotos presas com alfinete quando cada ano é falado |
| comparativo, versus | `papercompare` | 2–3 polaroides com o nome datilografado e o carimbo vermelho "VS" |
| cartão de ranking (`ranking`) | `paperrank` | o carimbo vermelho "Nº 3" batendo no papel e a polaroide do carro presa com fita, com o nome datilografado |
| legenda (`keyword`) | `papertag` | a palavra datilografada numa etiqueta de papel com fita |
| ano/modelo (`years`) | `paperyears` | ficha de papel com os anos grandes e o carro |

**Família documentário (estilo Documentário do app).** Para canais de história e guerra explicada: papel branco
rasgado sobre fundo carvão, máquina de escrever e anotações à mão em vermelho (`templates_docu.py`; fonte manuscrita
Architects Daughter, OFL):

| No lugar de | Entra | O que aparece |
|---|---|---|
| tela preta, título de destaque | `docphrase` | a frase datilografada num cartão rasgado com clipe; a palavra forte em vermelho e uma linha de caneta |
| gráfico de barras (`chart`) | `docbars` | título datilografado, rótulo de cada linha, a barra vermelha crescendo e o valor; a nota vira rodapé |
| (novo, do diretor) | `diagram` | **esquema desenhado**: quando a fala explica como algo funciona (a mina que pula, a sonda com baioneta), o diretor de IA desenha chão, caixas, bonecos, setas, explosão em raios, cota de altura e anotações à mão, cada peça entrando quando a fala diz a palavra dela. No máximo 3 por vídeo; anotação fora da fala sai |

| (novo, do diretor) | `map` | **mapa da região**: mar azul-ardósia, terra com relevo e fronteiras; os países citados acendem em vermelho, cada lugar ganha o ponto e o nome, as rotas (ataque, travessia) são setas curvas se desenhando e a frase da fala entra numa tarja inclinada, no canto que não tampa nenhum nome. A câmera aproxima do destino. Cada peça entra quando a fala diz o nome |
| (novo, do diretor) | `flagversus` | **país x país**: as duas bandeiras tremulando entram dos lados, o nome de cada país como a fala diz ("Niemcy"), o "VS" e as linhas da comparação com os números ditos |

O esquema, o mapa e o país x país só são pedidos ao diretor com o estilo Documentário (os outros canais continuam com o
mesmo pedido). No esquema, cada anotação vai sozinha para o lugar livre mais perto de onde a IA pôs (nunca em cima
do boneco, da explosão ou de outro texto). No mapa e no país x país só entram lugares e países que a fala cita, em
qualquer língua e declinados ("Polskę" vale pela Polônia); o lugar que não está na base usa a posição da IA.

Dados do mapa em `assets/geo/` (Natural Earth, domínio público): `countries.json.gz` (258 países da escala 1:10m,
contorno em ~1 km, nomes em várias línguas) e `places.csv.gz` (7.342 cidades). As bandeiras vêm do flagcdn.com
(domínio público) e ficam em `.cache/flags`.

Canal que não usa o dossiê (Jhonny Walt) também não usa nenhuma peça de papel. No **Mix**, uns 40% dos recursos
viram telejornal ou papel (sorteado).

**Ano/modelo (todos os estilos).** Quando a fala cita os anos do carro ("2014 to 2019", "de 2012 à 2016",
"the 2008 model", "built in 2012"), entra o card de ano/modelo com o nome do carro (`years`; no Broadcast vira a
tarja de nome com os anos, no Dossiê a ficha de papel). Ano solto ("em 2014 houve um recall") não conta.
A linha do tempo e o comparativo continuam entrando em todos os estilos quando a fala pede.

O vermelho do "BREAKING" é fixo (de telejornal); fundo, filetes e destaques seguem a paleta do canal. Rótulos no
idioma do roteiro ("URGENTE", "DERNIÈRE MINUTE", "BREAKING NEWS"...). No estilo **Mix**, cerca de 1/3 dos
recursos vira telejornal. Os templates de dados (preço, ficha técnica, comparativo...) não mudam.

- **Duração:** o Claude define `template_seconds` (ex.: comparativos com 2+ itens ≥ 10 s, fichas 8–12 s,
  capítulos 2,5–4 s). Os templates podem passar dos 7 s dos b-rolls; o teto fica em `templates.max_seconds`.
  Sem o campo, vale `templates.default_seconds`.
- **Dados nunca inventados:** templates com números só usam os que estão na narração (anos precisam ser
  exatamente os falados); textos (manchete, comentário, lupa) precisam ter as palavras da fala. Sem dados, o
  template vira um `card` (ou `checklist`, no caso de prós e contras de um lado só).
- **Idioma:** os rótulos fixos (PRÓS/CONTRAS, NOTA FINAL, INSCREVER-SE, BARATO/CARO…) saem no idioma do
  roteiro (`analysis.script_language`: `pt`, `en`, `fr`, `pl`, `es`), em `broll_bot/motion/i18n.py`. As
  fontes incluídas têm todos os acentos do polonês e do francês.
- **Detector de templates** (`analysis.auto_templates`, ligado por padrão): além da IA, o bot lê o roteiro
  atrás de pistas inequívocas e aplica o template nas cenas que a IA deixou como b-roll normal, com os dados
  copiados da fala: 3+ anos em sequência → `timeline`; "zero a cem em 9,2 segundos" / "velocidade máxima" →
  `speedometer`; cavalos, kgfm, km/l, câmbio → `datasheet`; "tabela FIPE… 130 mil reais… preço justo" →
  `price`; "pontos fortes/fracos" → `proscons`; "notas: conforto 9…" → `score`; "um inscrito comentou:" →
  `comment`; "qual você escolheria: A, B ou C?" → `poll`; "repare nos faróis" → `magnifier`; "antes e depois…
  2019… 2020" → `beforeafter`; "a Toyota lançou/anunciou/recall" → `news`; "Agora, o motor." → `chapter`;
  "Modelo: frase de efeito." na abertura → `headline`. Se uma cena tem duas pistas em frases diferentes
  (ficha técnica + 0–100), ela é dividida; se a IA picotou uma pista em várias cenas (um ano por cena, prós
  numa cena e contras na outra, o preço separado do "é um preço justo"), as cenas são juntadas de novo. A
  escolha da IA vale, a não ser que seja decorativa (título, card, VHS…) ou uma versão mais pobre do mesmo
  gráfico (um `stat` para o 0–100 vira `speedometer`; um `checklist` com prós e contras vira `proscons`).
  Na chamada para o canal, o título da IA dá lugar ao botão de inscrição sobre um take do carro. As pistas
  estão em português.
  Roteiro de exemplo que aciona todos: `exemplos/roteiro_teste_templates.txt`.
- **Sem limite de templates** (padrão): todo template que a fala pedir entra. Se dois se sobrepõem, o de
  antes encurta para o seguinte caber; se nem assim couber, fica o mais informativo e o outro vira overlay.
  Para voltar a limitar: `templates.max_share` (ex.: 0.5 = no máximo metade do vídeo) e
  `templates.min_gap_seconds` (b-roll mínimo entre dois templates).
- **Legendas só nos templates**, sincronizadas com o Whisper. No comparativo, checklist, prós e contras, ficha
  técnica, notas, linha do tempo e enquete, cada item entra quando é mencionado na fala.
- **Efeitos de corte** (`timeline.effects`): `whip`, `punch`, `glitch`, `leak`, `wash` (lavagem de cor
  verde-água → azul), `burn` (queima de filme) e `blackout` (tela preta com barrinhas + tranco RGB), além do
  `fadewhite` (flash) entre as transições. `timeline.effect_weights` define quais aparecem mais (por padrão,
  `wash` e `burn`, a "assinatura" do estilo documentário). A queima de filme é gerada pelo bot; se você puser
  vídeos de *film burn*/*light leak* com fundo preto em `library/overlays` (o Mixkit tem grátis), ela usa os seus.
- **Movimento suave:** o Ken Burns das fotos é calculado com fração de pixel (no Pillow) e o zoom dos
  vídeos usa o filtro `perspective` do FFmpeg, em vez do `zoompan`, que andava aos saltos de 1 px.
- **Desempenho:** camadas estáticas são desenhadas uma vez, o fundo quadriculado deslizante e a faixa de luz
  são pré-calculados, números e ponteiros ficam em cache, fundos
  desfocados são processados em baixa resolução, o VHS é desenhado em meia resolução e ampliado pelo FFmpeg,
  e os shots são renderizados em paralelo (`render.workers: 0` = automático).
- Fontes: Inter, Bebas Neue, Oswald, Montserrat e Lobster (licença OFL) em `assets/fonts/`.

**Edição magnética (todos os estilos).** Dez templates de retenção com a cara do estilo do vídeo
(`broll_bot/motion/templates_magnet.py`; `templates.skin`: `blueprint` no Minimal, `paper` no Dossiê, `tv` no
Broadcast, `data` no Dados, `kinetic`, `cinema`; no Mix cada um sorteia uma, e o papel só entra se o canal usa).
Cinco transformam os números da fala em gráfico — o bot acha sozinho (`broll_bot/magnet_detect.py`), sempre com o
número falado, no máximo um a cada 12 s e entrando na frase em que o número é dito. As pistas valem em inglês,
francês e português (moeda antes ou depois do número, milhar com espaço: "entre 1 500 et 3 000 euros"), e o
gráfico de dado substitui um template decorativo que a IA tenha posto na cena (card, título, mosaico…) ou um
número mais pobre (stat, contador, etiqueta de preço para uma conta de oficina):

| Template | Quando entra | O que aparece |
|---|---|---|
| `percentbar` | "39% of owners reported a fault", "Reliability rating, 76.4%", "23 pour cent des voitures" | o número contando, a barra enchendo e o que ele é |
| `peoplegrid` | "three in ten", "one owner in five", "un propriétaire sur trois", "um em cada três" | bonequinhos; os da conta acendem um a um |
| `receipt` | conserto com valor ("cost the owner more than £1,500", "La facture : entre 1 500 et 3 000 euros") | a nota da oficina imprimindo com as peças citadas, o TOTAL (a faixa, se a fala deu uma: "1 500–3 000 €") e o carimbo "PAID" |
| `downtime` | tempo parado ("off the road for more than a week", "fixed in a day or less") | calendário com os dias riscados em vermelho (ou o dia circulado em verde) |
| `warning` | o defeito principal ("the biggest area was non-engine electrics", "Le défaut : une surconsommation d'huile", "O problema é a correia dentada") | a luz de alerta do painel piscando e acendendo, com o defeito e o número |

Os outros cinco são quebras de padrão que o ritmo (`rhythm.py`) alterna com as fotos no blueprint, também em vídeo
sem carro de seção: `textmask` (a palavra gigante — o modelo — com o vídeo dentro das letras), `photostack` (fotos
caindo uma sobre a outra), `splitpanels` (a tela em 3 painéis entrando de cima e de baixo), `annotate` (a mão
desenha um círculo e uma seta na foto e escreve a anotação) e `wordpop` (as palavras fortes da frase pulando
grandes no tempo da fala). No roteiro de 19 min do Jhonny Walt isso dá uns 67 templates (um a cada ~17 s).

**Reportagem (todos os estilos).** Seis templates para vídeo investigativo/explicativo
(`broll_bot/motion/templates_story.py`, com as mesmas peles da edição magnética: recorte de jornal e carimbo no
Dossiê, estúdio de TV no Broadcast, grade nos Dados...). O bot acha sozinho na fala (`broll_bot/story_detect.py`,
inglês, francês e português), sempre com o texto falado:

| Template | Quando entra | O que aparece |
|---|---|---|
| `source` | quem disse: "analysts at UBS, the Swiss investment bank", "a Reuters investigation in 2023", "figures from the Association of British Insurers", "according to…", "selon…", "segundo…" | "FONTE", monograma com as iniciais, o nome, o que ela é + ano e o trecho da fala entre aspas aparecendo palavra por palavra |
| `fromto` | "it has gone from five showrooms to well over 140", "est passé de 3 à 12", "passou de 5 para 40" | o número contando de um ao outro, o antigo riscado, as duas barras e o "×28" |
| `coverage` | garantia por item: "six years of general cover, up to 93,750 miles", "the battery is covered for eight years" | uma barra por item numa régua de anos, com o escudo e a quilometragem |
| `steps` | dicas numeradas: a frase é só "One.", "Two."... (1, 2, 3 em sequência) e a seguinte é a dica | o número gigante, "PASSO 1 DE 5", a barra de progresso e a caixinha ganhando o visto |
| `unverified` | "I have not been able to confirm…", "we do not know yet", "an educated guess" | o que não se sabe e o carimbo "NÃO CONFIRMADO" / "SEM DADOS AINDA" / "ESTIMATIVA" batendo |
| `exploded` | desmontagem: "taken apart down to the last bolt", "took a BYD Seal apart", "démontée" | a foto do carro se separando em pedaços, com as peças citadas puxadas por linhas |

A fonte aparece no máximo a cada 30 s, o "não confirmado" a cada 45 s e a desmontagem a cada 90 s; o passo a
passo segue a sequência do roteiro.

**Broadcast com cara de notícia só quando é notícia.** "BREAKING NEWS", o selo "AO VIVO" com relógio,
"REPORTAGEM" e "ÚLTIMAS" só entram quando a fala é notícia (anúncio, recall, lançamento, investigação,
"segundo…", números oficiais, pesquisa publicada — `cues.is_news_talk`). No resto do vídeo os mesmos templates
viram o estúdio de análise: "PONTO-CHAVE" e "ANÁLISE" na cor do canal, sem "ao vivo" nos telões.

**Prévia dos estilos no app.** Na escolha do estilo cada cartão mostra o template típico daquele estilo
renderizado de verdade (`studio/web/previas/`); ao passar o mouse (e no estilo selecionado) a prévia anima. Para
refazer as prévias com uma foto sua: `python -m studio.previas --foto carro.jpg` (sem `--foto`, usa a primeira foto
da `library/` ou um carro desenhado). Em Configurações → Aparência do app dá para ligar/desligar o som suave ao
passar o mouse.

### Templates para qualquer nicho (história, ciência, agro, finanças...)

`broll_bot/motion/templates_plus.py`. Os de conteúdo são marcados pelo diretor de IA (com os números e nomes
conferidos no roteiro); a esteira entra sozinha pelo ritmo, em qualquer vídeo; a abertura usa o título que o
diretor tira do gancho.

| template | quando | o que aparece |
|---|---|---|
| `filmstrip` | ritmo (b-roll longo, qualquer nicho) | prancheta azul-escura com traços brancos; os vídeos da cena passam da direita para a esquerda, um de cada vez no centro ("FIG. 1", régua, cantoneiras), e a frase falada é datilografada embaixo |
| `opener` | começo do vídeo (depois do avatar, se houver) | fundo desfocado, cantoneiras, selo pequeno, linha que se abre do centro e o título em serifa |
| `profile` | a fala apresenta uma pessoa | foto (busca pelo nome, Wikimedia) num círculo com anel se desenhando, nome, função e o feito; sem foto, as iniciais |
| `yearflash` | "Em 1909, ..." | o ano rolando até a data, gigante, com o ano vazado ao fundo e a frase embaixo |
| `flow` | processo em etapas | caixas entrando no tempo da fala, setas se desenhando, a última no destaque do canal |
| `mythfact` | a fala desmente uma crença | o mito aparece e é riscado; o fato entra com o visto |
| `definition` | a fala explica um termo | verbete em papel: a palavra em serifa e o significado digitado |
| `article` | a fala cita uma matéria, comunicado ou post ("CBC reported...") | página de matéria branca: etiqueta FONTE com veículo e data, manchete, a fala daquele trecho com o trecho citado no marca-texto e a câmera aproximando |
| `agenda` | o gancho promete o que vem depois | "Neste vídeo": os assuntos numerados (loop aberto) |

A serifa é a Playfair Display (licença OFL, em `assets/fonts/`). `templates.opener: false` desliga a abertura.

### Overlays sobre o b-roll

Pequenos gráficos desenhados **por cima** do vídeo, que continua aparecendo (estilo documentário). O Claude
sugere um por cena no campo `overlay` do plano, só com fatos da narração, e ele entra quando o texto é falado.

| Tipo | O que aparece | Exemplo |
|---|---|---|
| `label` | caixa escura translúcida com borda fina | "51 PSI" / "máximo" |
| `pair` | duas caixas ligadas por linha tracejada | "51 PSI máx" ↔ "40 PSI frio" |
| `callout` | etiqueta com linha curva apontando um detalhe (achado por saliência visual) e arco fino | "banda de rodagem" |
| `badge` | selo no canto com ícone de velocímetro | "50,3 km/h" |
| `keyword` | legenda minimalista no canto de baixo: um filete amarelo se desenha e a palavra sobe de trás dele (amarelo com contorno preto). Só entra sobre a imagem escolhida para a cena, nunca sobre um plano B | "turbo", "pneu murcho gasta mais" |
| `bullets` | a mesma lista minimalista do `panel`, do lado direito | 2–4 dicas |
| `bignum` | número gigante contando, com frase embaixo | "90%" / "dos motoristas erram" |
| `tag` | título grande + detalhe, com linha guia até um marcador amarelo no ponto de destaque | "CORREIA DENTADA" / "troca a cada 60 mil km" |
| `panel` | lista minimalista: véu azul-escuro esmaecendo para o centro, filete amarelo, título em creme com traço amarelo e os itens numerados ("01", "02") entrando um a um | "À vérifier" + 2–4 itens |
| `fuel` | **consumo**: card no canto com bomba de combustível, o número contando em amarelo e a unidade; "EN VILLE"/"NA CIDADE" se a fala disser onde | "8–9 L/100 km", "12 km/l" |
| `file` | **ficha de papel** com fita adesiva por cima do b-roll: "DOSSIER" pequeno e espaçado, o nome do carro datilografado e um traço vermelho. Entra sozinha no começo de cada seção do ranking | "Dacia Duster" |
| `impact` | **palavra de impacto**: véu azul-escuro translúcido (a imagem continua aparecendo), filete amarelo vertical no meio, a palavra grande em creme com as letras levemente desalinhadas e a frase datilografada embaixo. Minimalista; entra sozinho em parte das frases de impacto | "ERREUR" / "Et c'est une erreur." |
| `phrase` | **frase de destaque**: a frase falada sobe palavra por palavra no terço de baixo e um marca-texto amarelo passa atrás da palavra forte | "Et c'est là que tout se joue." |
| `subscribe` | CTA: o cursor clica no like (fica azul), em **Inscrever-se** (vira "Inscrito") e no sininho, que balança — com som de clique e sino | "se inscreva e ativa o sininho" |

O `fuel` entra sozinho em toda cena que fala de consumo ("5 litres aux cent", "8 à 9 litres aux cent",
"faz 12 km/l", "6 litrów na sto"), no shot em que o número é falado — nunca em "un deux-litres" (cilindrada)
nem em "consommation d'huile".

O `subscribe` entra sozinho em toda cena com chamada para o canal ("se inscreva", "deixa o like",
"sininho"; também "subscribe", "abonnez-vous", "subskrybuj"…), mesmo que a IA não tenha pedido.

Limites em `overlays:` no `config.yaml` (intervalo mínimo entre overlays, duração mínima do shot).

### Efeitos sonoros

Cada animação tem som sincronizado: teclado na digitação (títulos, palavras-chave, tópicos), whoosh nas
transições deslizantes e nos cards, "pop" quando uma caixa aparece, ticks no número contando e "ding" no
fim, impacto no ranking/capítulo, glitch no glitch, estalos na queima de filme e fita no VHS. Os sons são
**gerados pelo próprio bot** (sem arquivos de terceiros) e mixados por baixo da narração, com volume
relativo à voz (`sfx.volume`). Nas transições o som é mais baixo (`sfx.transition_volume`), só aparece em
parte delas (`sfx.transition_probability`, sorteado com a seed) e nunca em duas seguidas
(`sfx.transition_min_gap`). Para usar os seus, ponha arquivos em `library/sfx` começando pelo nome do
efeito: `typing`, `click`, `whoosh`, `whoosh_soft`, `pop`, `tick`, `ding`, `impact`, `impact_soft`,
`glitch`, `riser`, `burn`, `tape`, `swipe`, `snap`, `pencil` (ex.: `whoosh_1.wav`, `whoosh_2.wav`, `typing.mp3`). Vários arquivos do
mesmo efeito são usados em rodízio.

### Música de fundo

A música acompanha o clima do roteiro. A IA marca o clima de cada cena (`tone`) e o bot o traduz para um
de 7 climas: energético, épico, tenso, calmo, tecnológico, emocional e divertido. Um aviso ou problema
fica tenso, ficha técnica fica tecnológico, desempenho fica energético e história fica emocional.

- **Trechos:** a música só troca quando o clima muda de verdade e o trecho já durou
  `music.min_segment_seconds` (35 s). Uma frase isolada não troca a faixa. Se houver um título ou
  capítulo logo adiante, a troca acontece nele, junto com o assunto. Um trecho com mais de
  `max_segment_seconds` (120 s) no mesmo clima troca de faixa para não cansar. As trocas usam
  crossfade.
- **Volume:** toda faixa é nivelada. Enquanto você fala, a música fica em `music.volume` (≈ -18 dB
  abaixo da voz). Nas pausas ela sobe para `pause_volume`, mas só depois de `duck_hold` segundos, para
  não "bombear" entre as palavras. Antes de cada título/capítulo a música respira: cai por um instante
  e volta com o título.
- **Faixas:** ficam em `library/music`, em subpastas por clima: `energetico`, `epico`, `tensao`,
  `calmo`, `tecnologico`, `emocional` e `divertido`. O clima também pode vir do nome do arquivo
  (`epic-drive.mp3`). Uma faixa sem clima serve para qualquer um. Sem clima certo, o bot usa o mais
  parecido e evita repetir a mesma faixa em seguida. Faixa curta é repetida com crossfade.
- 2 ou 3 faixas por pasta já bastam. Onde achar música livre para o YouTube:
  - **YouTube Audio Library** (Estúdio do YouTube → Biblioteca de áudio): a mais segura para
    monetizar; filtre por "Atribuição não obrigatória".
  - **Mixkit Music** e **Pixabay Music**: grátis. Algumas faixas da Pixabay estão no Content ID; se
    aparecer uma reivindicação, conteste com a licença da faixa.
  - **Uppbeat**: grátis com crédito na descrição.
  - **Epidemic Sound / Artlist**: pagos, com licença para o canal (sem reivindicações).
- Sem faixas na pasta, o vídeo sai sem música, com um aviso no final. `--music faixa.mp3` usa uma faixa
  só para o vídeo todo (com o mesmo ducking), e `--no-music` desliga a música. O `report.json` lista
  os trechos (`music`: início, fim, clima e faixa).

## Adicionando uma nova fonte

1. Crie `broll_bot/providers/minha_fonte.py` com uma classe `BaseProvider` que implemente `search()` e
   `download()`. Levante `ProviderError`, `RateLimitedError` ou `AuthError` em falhas; use `HttpClient`
   para ganhar rate limit e retry.
2. Registre a fonte em `providers/__init__.py::build_providers` e inclua o nome em `search.provider_order`.

Veja **[FONTES.md](FONTES.md)** para a análise de outras fontes (gratuitas e pagas) e os cuidados de licença.

## Testes

```bash
pytest -q
```

Os testes cobrem cache (TTL, persistência, arquivo corrompido), rate limit/backoff/circuit breaker, cliente
HTTP (5xx, 429, 401, sem conexão, headers), fallback entre fontes e orçamento de buscas, planejamento de
durações (≤ 7s, ms quebrados, sem repetição, cobertura total, snap nas pausas, reprodutibilidade),
alinhamento roteiro↔fala, plano de cenas e parsing das respostas do Pexels e do Pixabay. Nenhum teste usa rede.

## Limitações conhecidas

- O CLIP avalia a thumbnail, não o clipe inteiro. Um vídeo que muda muito de cena pode ter trechos menos
  aderentes (o ponto de entrada é sorteado).
- No modo heurístico (sem LLM), as consultas saem no idioma do roteiro. O Pexels e o Pixabay aceitam
  português, mas o acervo responde melhor em inglês.
- A primeira execução com CLIP baixa os pesos do modelo (~600 MB para ViT-B-32).
