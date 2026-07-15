# Biblioteca local de músicas

Coloque as faixas de fundo diretamente nesta pasta. Formatos aceitos: `.mp3`, `.wav`, `.m4a`,
`.aac`, `.flac` e `.ogg`.

O pipeline sorteia uma faixa quando executa a etapa `music`, copia o arquivo escolhido para
`audio/music/` dentro do projeto e registra a escolha em `audio/music/manifest.json`. A mesma faixa
é reutilizada nas execuções seguintes. Use `music select <projeto> --force` para sortear novamente.

Use somente músicas para as quais você possui os direitos ou a licença necessária para publicação.
