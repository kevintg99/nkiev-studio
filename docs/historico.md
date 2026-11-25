# Histórico do bot (o que foi feito e por quê)

Este é um resumo da conversa longa na nuvem (set–out/2026) em que o bot foi construído. O detalhe de cada
mudança está no `git log` (155+ commits, todos em português) e no `README.md`.

## Como a conversa funcionava
- Eu mando o `report.json` de cada vídeo (e às vezes um print) dizendo o que ficou estranho.
- O Claude acha a causa no report (cenas, buscas, fontes, revisão), corrige o bot com teste de regressão, dá
  push no `main` e explica em português.
- Eu atualizo com o `atualizar.bat` e rodo de novo.

## Linha do tempo resumida
1. **Base (28–29/09):** b-roll sincronizado com a narração; biblioteca local; modo chat do Claude (sem API);
   motion graphics e transições; revisão visual por IA; OpenAI como alternativa; Wikimedia; música por clima;
   zoom/pan suave; verificador de coerência.
2. **Idiomas e rankings (30/09–01/10):** pt, fr, pl e en; um carro por seção no ranking; chip de vídeo
   (QuickSync/NVENC/AMF); checagens visuais (carro batido, outro carro, duplicada); paleta do canal; dossiê de
   papel; perfis de canal.
3. **nKiev Studio (01–03/10):** app com fila, canais, estilos, chaves, custos e Concluídos; estilos Broadcast,
   Dossiê, Dados e Magnético (templates de retenção com a cara de cada estilo).
4. **Avatar HeyGen (03–10/10):** personagem do canal com lip-sync na abertura, na chamada de inscrição e no
   final; Avatar IV; custo e saldo no report.
5. **Ajustes por vídeo (04–14/10):** muitos vídeos reais (diesel, pneus, ferrugem, recalls, roubados, SUVs):
   números falados pela voz de IA viram algarismos na tela, moedas (£, C$, €), listas e rounds.
6. **Diretor de IA (14–16/10):** o gpt-5 lê o roteiro inteiro e decide ranking, listas, cards e o nicho; 8
   templates para qualquer nicho (abertura, perfil, ano, processo, mito x fato, verbete, agenda, esteira).
7. **Fontes livres (16–18/10):** Openverse (chave grátis no `.env`) e YouTube Creative Commons (cookies do
   Firefox + Deno); funil por fonte no report.
8. **Vídeo curto vertical (19–20/10):** 9:16 escolhido no começo; corte das pausas; 8 estilos de legenda
   animada com tamanho da letra; templates em pé; motion de retenção (`broll_bot/shortsfx/`).
9. **Busca mais esperta (20–21/10):** cenas do mesmo assunto vão aos bancos de reserva; perfil só com foto
   que traz o nome; buscas de coisa única pelo nome + visual genérico; plano de cenas refeito quando as
   regras mudam.
10. **Sessão local (21–28/10):** biblioteca local com `credit` e `requires`; clipe curto sem loop; fonte
    `youtube_any`; canais Dale Kowalchuk e Jornada Temporal; `CLAUDE.md`, `preparar_sessao.bat` e testes com
    canais de exemplo fixos (`tests/fixtures/canais/`).
11. **PC novo com RTX e vídeo polonês (28–29/10):** avatar espera a HeyGen enquanto o vídeo monta; transcrição
    na placa NVIDIA (ou na CPU, nunca mais por estimativa) e `studio/gpu.py` no atualizar; fonte que cai de
    novo (Openverse com timeout, YouTube com 429) fica fora o dobro do tempo a cada recaída; números por
    extenso em polonês nos cards; lista numerada que a IA não marcou ("Rzecz pierwsza… piąta") achada no
    roteiro.
12. **Vídeo das 9 baterias, 4 versões (31/10):** o avatar nunca é pago de novo na mesma narração; foto de
    produto só na cena que diz o nome (`say`/`say_not`) e, no ranking, só na seção dele; busca do cache volta
    mesmo com a fonte barrada. Para sair bom na v1: o diretor lista os nomes que precisam de foto e o bot baixa
    sozinho para `library/mixkit/_auto` (`named_images.py`, Google Imagens pelo Serper ou Wikimedia); a busca
    na biblioteca local deixou de procurar o `requires` no roteiro inteiro a cada busca (era 62% do tempo da
    busca); depois da revisão, só as cenas com veredito novo são buscadas de novo.
13. **Vídeo da garantia Toyota, 1ª rodada do zero (01/11):** fotos dos nomes com a Wikimedia uma por vez e
    falha que não derruba o vídeo; o nome do arquivo da Wikimedia decide, não a descrição (logo da Sky Tamil News
    entrava como Global News); Pexels gasta o que sobrou da cota da hora em vez de 3 por minuto.
14. **Estilo comentário político (01/11, referência: Gord Wallace, "Now Watch What Canada Did"):** card `article`
    (matéria de site branca com etiqueta FONTE, veículo e data, manchete, o parágrafo com a fala daquele trecho e o
    trecho citado no marca-texto, câmera aproximando); nome citado a toda hora (o presidente) ganha até 6 fotos
    em vez de 2 (`per_name_max`, `mentions_per_extra`).
15. **Vídeo dos 9 pneus de inverno (01/11, 68 min para 23 min de narração):** as buscas das cenas divididas
    levavam 19 min (lotes de 20 um por vez, gpt-5-mini pensando até estourar os 8.000 tokens, uma chamada
    pendurada 10 min). Agora são lotes de 10, 4 ao mesmo tempo, com raciocínio mínimo e no máximo 2 min por
    chamada.
16. **Vídeo dos rios (02/11, 36 min para 15 min de narração):** 10 min só baixando 239 vídeos inteiros (7,6 GB;
    vídeos de 60–90 s em 1080p, a internet dá ~10 MB/s) para usar ~16 s de cada. Agora o vídeo longo do Pexels e do
    Pixabay vem só nos primeiros 24 s, sem som (`search.video_head_seconds`); o trecho do shot é sorteado dentro
    disso.
17. **Estilo Documentário (03/11, referência: canais gringos de guerra explicada):** papel rasgado sobre carvão,
    frase datilografada (`docphrase`), barras no papel (`docbars`) e o esquema desenhado à mão pelo diretor
    (`diagram`: chão, caixas, bonecos, setas, explosão, cota e anotações, peça por peça no tempo da fala). Depois: as
    anotações do esquema vão sozinhas para o lugar livre mais perto; mapa com setas (`map`, Natural Earth em
    `assets/geo/`) e país x país com bandeiras (`flagversus`), só com lugares e países que a fala cita (até declinados
    em polonês).
18. **Canal de história em francês (03/11, roteiro "Opération Greif", 19 min):** francês nos templates novos
    ("l'Allemagne", "États-Unis", "?" sem cair sozinho). Fonte nova **arquivo histórico** (Internet Archive:
    cinejornais e filmes do governo americano em domínio público, trecho de 8 s escolhido pelo quadro) e **filme
    antigo** (grão, risco, sépia) nela e em 1 de cada 4 cenas do Documentário, que também tira o `youtube_any`.
    Rodando só o diretor no roteiro: 8 cards em 19 min (um mapa, sem seta). Agora o Documentário pede o número de
    cards com conta (36–54 em 18 min) e "6–12 mapas" em vídeo de guerra; o mapa enquadra no mínimo uma região
    (~700 km) com o nome dos países vizinhos na língua do vídeo, os nomes dos lugares não se encostam, o mapa aceita
    lugar dito até 6 frases antes, e o esquema tem veículos prontos (tanque, jeep, caminhão, avião, navio, canhão).
19. **1º vídeo do canal Derrière les Lignes (03/11, Operação Greif, 62 min para 14 min de narração):** o filme
    antigo balançava o quadro (até 8 px de um quadro para o outro) e piscava a luz 5 vezes por segundo: a tela tremia.
    Agora o corte é fixo, a luz varia pouco e o grão é mais fino. O cinejornal "Berlin 1961" entrou num vídeo de 1944:
    o arquivo só traz filme dos anos que o roteiro cita (cinejornal até 1 ano fora; filme do governo até 25 anos
    depois, que pode ser retrospectiva). A busca levou 41 min; o Openverse sozinho esperou 41 vezes 20 s sem
    responder (0 imagens usadas): banco que não responde tenta só mais uma vez e pausa (a pausa dobra a cada queda).
20. **Vídeo dos disjuntores (Jornada Temporal, 07/11):** quase não apareceu disjuntor (Pexels e Pixabay quase não
    têm; entraram linha de montagem de moto, painel solar, solda). Pesquisei 47 arquivos de licença segura
    (Wikimedia, Pexels, Pixabay) para `library/mixkit/circuit-breaker/`, `electrical-fuse/`, `electric-arc/`,
    `hugo-stotz-mannheim/`, `thomas-edison/` e outras, cada um com `say` para a cena certa. Bug: a foto do vendedor
    de carvão entrou na cena da mola do disjuntor como último recurso, porque o roteiro dizia "Brasil" uma vez
    (requires da foto). Agora o último recurso só usa arquivo de assunto (com `requires`) em cena que fala dele.
    Na v2 só 24 dos 48 arquivos entraram: quando o shot preferia vídeo, qualquer vídeo aprovado (solda, YouTube,
    fábrica de moto) ganhava da foto certa. Agora o arquivo com `say` que casa com a fala vem antes da preferência
    por vídeo. E o "✔" do resumo final derrubava o processo (código 1) no console do Windows.
    Na v3 (31 usados) faltava a foto da ABB na cena da fábrica: com vídeos (de moto) bastando, a biblioteca nunca
    era procurada por foto. Agora o arquivo com `say` que casa com a fala entra sempre como candidato da cena e
    conta como bom (só o revisor barra).

21. **Vídeo do relógio de luz (Jornada Temporal, 09/11):** pesquisei 45 arquivos (relógio de disco, bobinas, ímã,
    Shallenberger, Westinghouse, Bláthy/Ganz, leituristas, apagão de SP, gato, medidores inteligentes, Enel) e 6
    vídeos Creative Commons do YouTube com o disco girando e o medidor inteligente. O retrato de 303 px do
    Shallenberger era cortado pela largura mínima e o cartão de perfil saiu só com as iniciais: arquivo com `say`
    passa mesmo pequeno. As fotos do vídeo dos disjuntores entraram aqui porque este roteiro diz "disjuntor" duas
    vezes: o `requires` delas agora pede "disjuntores".

22. **Vídeo do hidrômetro (Jornada Temporal, 13/11):** 45 arquivos pesquisados (hidrômetro no cavalete, estrelinha,
    molinete original do Woltman, hidrômetro contando ar, medidor ultrassônico e diagramas do som no cano, Sabesp,
    crise hídrica, gato de água). O roteiro diz "relógio de luz" duas vezes: `requires_min: 3` nas pastas de assunto
    para menção de passagem não puxar fotos de outro vídeo. Fotos marcadas apareceram 4 ou 5 vezes cada:
    `search.max_marked_uses: 3`. O cartão do Woltman usava a foto de um rebocador com o nome dele: o perfil agora
    prefere o arquivo marcado da biblioteca.

## Lições que valem para tudo
- **O horizontal é o principal:** recurso novo do vertical nunca pode mudar o horizontal.
- **Não tirar recursos:** não tire função nenhuma. O formato e o canal ligam o que precisam sozinhos, sem eu
  ter que ligar e desligar.
- **Texto na tela só com o que a narração diz:** número, nome ou título fora da fala é cortado.
- **Banco grátis não tem coisa única** (protótipo de uma empresa, pessoa específica): use a biblioteca local
  ou a Wikimedia, nunca uma imagem qualquer.
- **Custo de IA:** cada vídeo longo gasta ~US$ 0,30 (diretor gpt-5, plano gpt-5-mini, revisão). O avatar da
  HeyGen usa crédito de API, que é separado do plano do site.
