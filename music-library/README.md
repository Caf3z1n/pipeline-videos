# Biblioteca local de músicas

Coloque as faixas de fundo diretamente nesta pasta. Formatos aceitos: `.mp3`, `.wav`, `.m4a`,
`.aac`, `.flac` e `.ogg`.

O pipeline sorteia uma faixa quando executa a etapa `music`, copia o arquivo escolhido para
`audio/music/` dentro do projeto e registra a escolha em `audio/music/manifest.json`. A mesma faixa
é reutilizada nas execuções seguintes. Use `music select <projeto> --force` para sortear novamente.

O perfil `adaptive_compact_v1` não usa esta seleção geral. Ele sorteia exclusivamente entre
`adaptive-compact-v1/Aloboi .mp3` e `adaptive-compact-v1/Leaning.mp3`; o manifesto registra
`library_profile=adaptive_compact_devotional_v1` e o import valida o hash da faixa contra essa
biblioteca isolada.

Use somente músicas para as quais você possui os direitos ou a licença necessária para publicação.
