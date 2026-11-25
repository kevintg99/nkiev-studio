# Comece aqui: nKiev Studio + broll_bot

Automação que monta vídeos para o YouTube sozinha. Você entrega o **roteiro** (.txt) e a **narração** (.mp3) e
ela devolve o vídeo pronto: b-roll sincronizado com a fala, motion graphics, cards de dado, mapas, legendas
(nos Shorts), efeitos sonoros, música e, se quiser, um avatar apresentador da HeyGen.

- **broll_bot** (`broll_bot/`, entrada `main.py`): o motor que faz o vídeo.
- **nKiev Studio** (`studio/`): o app de janela, com fila de vídeos, canais, estilos e chaves. É o jeito fácil
  de usar.

> Usa IA? Este repositório tem um `CLAUDE.md` (e uma cópia em `AGENTS.md`) escrito para a sua IA. Abra a pasta
> no Claude Code, Cursor, Codex ou outro assistente e peça: "leia o CLAUDE.md e o COMECE_AQUI.md e me ajude a
> instalar e fazer o primeiro vídeo".

---

## 1. O que instalar (Windows)

1. **Python 3.11 ou mais novo**: <https://www.python.org/downloads/>. Na instalação, marque **"Add python.exe
   to PATH"**.
2. **FFmpeg**: abra o PowerShell e rode `winget install ffmpeg`. Feche e abra o PowerShell de novo.
3. **Git**: <https://git-scm.com> (para baixar e atualizar).

Confira no PowerShell:

```bash
python --version
ffmpeg -version
git --version
```

## 2. Baixar e instalar

```bash
cd $HOME\Desktop
git clone https://github.com/kevintg99/nkiev-studio.git nkiev-studio
cd nkiev-studio
python -m venv .venv
.venv\Scripts\activate
```

Depois dê dois cliques em **`instalar_app.bat`**. Ele:

- instala as bibliotecas (`requirements.txt`);
- com placa NVIDIA, instala a versão com GPU (a transcrição fica bem mais rápida);
- instala o Deno (o yt-dlp precisa dele para os vídeos Creative Commons do YouTube);
- cria o `.env` a partir do `.env.example`;
- cria o atalho **nKiev Studio** na área de trabalho.

Para conferir se está tudo certo: `python -m pytest -q` (tem que terminar com "passed" e nenhum "failed").

## 3. As chaves das APIs

Coloque as chaves dentro do app (**Configurações**) ou direto no arquivo `.env`. **Nunca** mande o `.env` para
o GitHub nem cole as chaves em chat: ele já está no `.gitignore`.

| Chave | Para quê | Precisa? | Onde pegar |
|---|---|---|---|
| `OPENAI_API_KEY` | Diretor de IA (gpt-5), plano de cenas (gpt-5-mini) e revisão das imagens | **Sim** (ou a da Anthropic) | <https://platform.openai.com/api-keys> |
| `ANTHROPIC_API_KEY` | O mesmo, com o Claude no lugar do ChatGPT | Alternativa à OpenAI | <https://console.anthropic.com> |
| `PEXELS_API_KEY` | Vídeos e fotos grátis | **Sim** | <https://www.pexels.com/api/> |
| `PIXABAY_API_KEY` | Vídeos e fotos grátis | **Sim** | <https://pixabay.com/api/docs/> |
| `YOUTUBE_API_KEY` | Vídeos Creative Commons do YouTube | Recomendada | Google Cloud Console, "YouTube Data API v3" |
| `WIKIMEDIA_CONTACT` | A Wikimedia pede um contato (e-mail ou site) para identificar quem consulta | Recomendada | Seu e-mail |
| `SERPER_API_KEY` | Fotos dos nomes do roteiro (produto, pessoa, loja) pelo Google Imagens | Opcional | <https://serper.dev> (2.500 buscas grátis) |
| `HEYGEN_API_KEY` | Avatar apresentador | Opcional | <https://app.heygen.com> (crédito de API é separado do plano do site) |

Custo: um vídeo longo (15–20 min) gasta uns **US$ 0,30** de IA. Pexels, Pixabay, Wikimedia e YouTube são grátis.

No app, escolha a IA em **Configurações** (OpenAI ou Claude). Na linha de comando use `--ai openai` ou
`--ai anthropic`. O padrão do `config.yaml` é o Claude.

## 4. Fazer o primeiro vídeo pelo app

1. Abra o **nKiev Studio** pelo atalho.
2. Em **Canais**, edite o canal de exemplo (`Meu Canal`, em português, ou `My Channel`, em inglês) ou crie o
   seu: nome, idioma, moeda, assunto e cores.
3. Em **Produzir**: escolha o formato (horizontal 16:9 ou Shorts 9:16), o canal, o roteiro (.txt) e a narração
   (.mp3), e o estilo.
4. Acompanhe na fila. O vídeo sai em `videos/<nome do canal>/` e, ao lado dele, um `report.json`.

**Regra de ouro:** a narração tem que falar o mesmo texto do roteiro. Separe os parágrafos com uma linha em
branco.

## 5. Pela linha de comando

```bash
.venv\Scripts\activate
python main.py --audio narracao.mp3 --script roteiro.txt --canal "Meu Canal" --ai openai
```

- `python main.py --canais` lista os canais.
- `python main.py --help` mostra todas as opções.
- Exemplos de roteiro em `exemplos/`.

## 6. Deixar o vídeo bom: a biblioteca local

Os bancos grátis (Pexels e Pixabay) têm muita coisa genérica, mas quase nunca a coisa **específica** do roteiro:
um aparelho antigo, uma pessoa histórica, um produto, um lugar. Para isso existe a biblioteca local.

- Pasta: `library/mixkit/` (lê as subpastas). Exemplo: `library/mixkit/water-meter/`.
- Nome do arquivo e da pasta em inglês, com hífens, descrevendo o que aparece:
  `water-meter-star-wheel-brass-closeup.jpg`.
- Ao lado de cada arquivo, um `.json` com o mesmo nome:

```json
{
  "tags": ["water meter", "star wheel", "dial"],
  "description": "brass water meter with the black star wheel",
  "page_url": "https://commons.wikimedia.org/wiki/File:...",
  "credit": "Autor (CC BY-SA 4.0), via Wikimedia Commons",
  "requires": ["hidrômetro", "hidrômetros"],
  "requires_min": 3,
  "say": ["estrelinha", "vazamento", "ponteiros"]
}
```

- `requires`: o arquivo só entra em vídeo cujo roteiro cite essas palavras. `requires_min: 3` exige 3 menções,
  para uma citação de passagem não puxar a foto.
- `say` e `say_not`: o arquivo só entra na **cena** cuja fala diz uma dessas palavras, e nunca na que diz uma do
  `say_not`. O arquivo marcado com `say` passa na frente de tudo naquela cena.
- **Licença:** em canal monetizado, só use mídia de licença segura: Wikimedia Commons (CC0, domínio público,
  CC BY, CC BY-SA), NASA, material de imprensa oficial, vídeos Creative Commons. Vídeo comum do YouTube e foto
  de notícia, não.

**O fluxo que funciona:** antes de produzir, peça para a sua IA ler o roteiro, listar as coisas específicas que
o banco grátis não vai ter, pesquisar na Wikimedia Commons e no YouTube (filtro Creative Commons), baixar para
`library/mixkit/<assunto>/` com o `.json` de cada arquivo e só então produzir. Depois, analisar o `report.json`.

## 7. Melhorar o vídeo com o report.json

Ao lado de cada vídeo sai um `report.json` com tudo o que aconteceu:

- `scenes[*].shots[*]`: fonte, busca, nota e plano B de cada trecho (`fallback`);
- `sources_funnel`: quanto cada banco achou e quanto entrou;
- `timings`: tempo de cada etapa;
- `named_images`: fotos dos nomes do roteiro.

Mande o `report.json` para a sua IA e diga o que ficou estranho. Ela acha a causa, corrige o bot (com teste) ou
completa a biblioteca local, e você produz de novo. A segunda versão sai bem mais rápida, porque o plano e as
buscas ficam no cache (`.cache/`).

## 8. Atualizar

`atualizar.bat` faz `git pull` e reinstala as dependências.

## 9. Onde está cada coisa

| Caminho | O que é |
|---|---|
| `config.yaml` | Todas as opções. Seus ajustes vão em `config.local.yaml`, que tem prioridade e fica fora do git |
| `canais/*.yaml` | Um arquivo por canal (veja `canais/LEIA-ME.md`) |
| `library/mixkit/` | Sua mídia (biblioteca local) |
| `library/music/<humor>/` | Músicas de fundo, por humor |
| `projetos/` | Roteiro, áudio, ajustes e log de cada vídeo feito pelo app |
| `videos/<canal>/` | Os vídeos prontos |
| `README.md` | O manual completo |
| `FONTES.md` | Os bancos de imagem e as licenças |
| `docs/historico.md` | Tudo o que já foi corrigido no bot e por quê |
