# Canais

Cada arquivo `.yaml` desta pasta é um canal: nome, idioma, moeda, paleta de cores, o que o canal não
usa e a pasta onde os vídeos dele são salvos. O `config.yaml` da raiz continua sendo a base.

- **Escolha automática:** sem `--canal`, o bot detecta o idioma do roteiro e usa o canal daquele
  idioma (português → Meu Canal, inglês → My Channel). Com dois canais no mesmo idioma, marque
  um com `padrao: true` dentro do bloco `canal:` ou passe `--canal`.
- **Escolha manual:** `python main.py --audio narracao.mp3 --script roteiro.txt --canal "Meu Canal"`.
- **Ver os canais:** `python main.py --canais`.
- **Novo canal:** pelo app nKiev Studio (tela Canais) ou copiando um arquivo e mudando o nome do arquivo e o bloco `canal:`.
- **Presets do app:** ficam em `<canal>.presets.json` ao lado do YAML (o app não reescreve o YAML).
- **Assunto:** `nicho: variado` no bloco `canal:` (o app põe junto `ranking: {visual_checks: false}`, que desliga as checagens de "cara de carro").
- **Vídeos:** saem em `videos/<nome do canal>/` com data e hora no nome (`--out` continua valendo).
- **Avatar (HeyGen):** seção `avatar:` com a imagem do personagem do canal (o app preenche em Canais → Editar →
  "Imagem do personagem"). A HeyGen anima a imagem com o lip-sync da sua narração e ela entra em tela cheia na
  abertura, na chamada para se inscrever e no final. Precisa da `HEYGEN_API_KEY` (Configurações). Exemplo:

  ```yaml
  avatar:
    photo: canais/avatar/meu_canal.jpg   # o personagem já com o cenário (ex.: mecânico na oficina)
    layout: full                           # full (tela cheia) | corner (no canto, recortado do verde)
  ```
