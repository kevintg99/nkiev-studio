# Passo a passo — broll-bot (Windows / PowerShell)

Pasta do projeto: Desktop\nkiev-studio, dentro da sua pasta de usuário
(C:\Users\<seu usuário>, que muda de um computador para outro; no PowerShell é $HOME)

## A) Toda vez que abrir o terminal

    cd $HOME\Desktop\nkiev-studio
    .venv\Scripts\activate

Confira se apareceu (.venv) no começo da linha.

## B) Atualizar o código (quando eu avisar que tem versão nova)

    git pull
    pip install -r requirements.txt
    python -m pytest -q

O pytest tem que terminar com "passed" e nenhum "failed".

## C) Fazer um vídeo (modo chat do Claude — sem crédito de API)

1. Salve o roteiro como roteiro.txt na pasta do projeto
   (parágrafos separados por uma linha em branco).

2. Grave/gere a narração desse roteiro e salve como narracao.mp3 na mesma pasta.
   O que for falado tem que ser igual ao texto do roteiro.

3. Gere o prompt:

       python main.py --audio narracao.mp3 --script roteiro.txt --export-prompt

4. Abra prompt_cenas.txt, copie TUDO e cole no chat do Claude.
   Salve a resposta (o JSON) como cenas.json na pasta
   (Bloco de Notas → Salvar como → Tipo: Todos os arquivos → nome: cenas.json).

5. (Recomendado) Revisão das imagens pelo Claude — evita Mercedes, esportivo,
   plantas etc. em cena errada:

   5a. Gere as grades de miniaturas (não renderiza o vídeo, é rápido):

       python main.py --audio narracao.mp3 --script roteiro.txt --scenes cenas.json --export-review

   5b. Abra um chat NOVO do Claude, anexe TODAS as imagens da pasta revisao
       (cena_00.jpg, cena_01.jpg, ...), cole na mesma mensagem TODO o conteúdo
       de revisao\prompt_revisao.txt e envie.

   5c. Salve a resposta (o JSON) como revisao\resposta.json
       (Bloco de Notas → Salvar como → Tipo: Todos os arquivos).

   Pode pular o passo 5 inteiro; aí o vídeo sai só com o filtro automático (CLIP).

6. Gere o vídeo:

   Com revisão (fez o passo 5):

       python main.py --audio narracao.mp3 --script roteiro.txt --scenes cenas.json --review-file

   Sem revisão:

       python main.py --audio narracao.mp3 --script roteiro.txt --scenes cenas.json

7. Resultado:
   - video_final.mp4 → o vídeo
   - report.json     → o que foi escolhido em cada cena (fonte, template, duração, créditos)

## D) Quando a API da Anthropic estiver liberada (tudo automático)

    python main.py --audio narracao.mp3 --script roteiro.txt --review

(pula os passos 3, 4 e 5 — o Claude faz o plano de cenas e revisa as imagens sozinho)

## E) Opções úteis

    --seed 7                 outra variação do mesmo vídeo
    --out meu_video.mp4      outro nome de saída
    --no-clip                sem o ranking visual (mais leve)
    --keep-temp              guarda os pedaços intermediários
    --reindex-library        reindexa a pasta library\mixkit
    --pace fast              ritmo de documentário (cortes de 1,3 a 4,2 s)
    --no-sfx                 sem efeitos sonoros

Opcional — sons e efeitos próprios (o bot já gera os dele, isto só substitui):
    library\sfx       ponha arquivos começando pelo nome do efeito: whoosh_1.wav,
                      typing.mp3, pop.wav, ding.wav, impact.wav, glitch.wav...
    library\overlays  vídeos de "film burn" / "light leak" com fundo preto
                      (Mixkit tem grátis) para a transição de queima de filme

Cancelar uma renderização: Ctrl + C no terminal.

## F) Problemas comuns

| Mensagem | O que fazer |
|---|---|
| No module named 'yaml' (ou outro) | ative o ambiente: .venv\Scripts\activate |
| dataclass() got an unexpected keyword argument 'slots' | use python -m pytest (não "pytest" sozinho) |
| binários não encontrados no PATH: ffmpeg | reinstale o FFmpeg e reabra o terminal |
| fonte indisponível ... source=pexels/pixabay | chave faltando no .env (não no .env.example) |
| plano de cenas não bate com o roteiro atual | o roteiro mudou: gere o prompt de novo (passo 3) |
| cenas.json não é um plano de cenas válido | cole a resposta inteira do chat, só o JSON |

## G) Arquivos que ficam só na sua máquina (não vão para o GitHub)

.env (chaves), library\mixkit (seus clipes), narracao.mp3, roteiro.txt, cenas.json,
video_final.mp4, report.json, .cache e .work.
