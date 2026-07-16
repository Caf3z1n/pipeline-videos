# Publicação no YouTube

## O que a integração faz

O módulo `publication.py` consome artefatos finais aprovados ou candidatos privados tecnicamente
validados e usa a YouTube Data API v3
para:

- enviar `render/final.mp4` com título, descrição, capítulos, tags e classificação;
- definir a thumbnail ativa em vídeos longos;
- inserir ou atualizar a legenda SRT em português;
- consultar o processamento remoto;
- publicar imediatamente ou agendar a publicação;
- retomar uma falha sem criar outro vídeo quando o render não mudou.

O pipeline normal nunca chama essa etapa. Upload e publicação exigem comandos separados, o guard
`--allow-external` e confirmações literais.

A exceção controlada também vale para produção local: depois de `render-review` e validação técnica,
um pedido explícito do usuário pode enviar o candidato somente como privado com
`--automated-review`. Isso não exige nem registra aprovação humana. Agendamento continua separado e
só é aceito com data/hora explícita e os guards do pipeline; consulte também
[Produção remota](remote-production.md).

## 1. Preparar o Google Cloud

1. Crie ou selecione um projeto no [Google Cloud Console](https://console.cloud.google.com/).
2. Ative a [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com).
3. Configure a tela de consentimento OAuth. Enquanto o app estiver em teste, inclua sua conta
   Google na lista de usuários de teste.
4. Crie um cliente OAuth do tipo **Desktop app**.
5. Baixe o JSON e salve-o fora deste repositório, por exemplo em
   `C:\Users\SEU_USUARIO\.config\pipeline-videos\youtube\client_secrets.json`.

O YouTube não oferece autenticação de canal por service account. A integração usa OAuth de usuário
e o redirect loopback local recomendado para aplicativos desktop. Consulte a
[documentação de OAuth do YouTube](https://developers.google.com/youtube/v3/guides/authentication)
e o [fluxo para apps desktop](https://developers.google.com/identity/protocols/oauth2/native-app).

## 2. Autorizar o canal

```powershell
uv sync --all-groups
uv run pipeline-videos youtube auth `
  --client-secrets "C:\caminho-seguro\client_secrets.json" `
  --allow-external
```

O navegador abre a escolha de conta/canal. O token renovável é salvo, por padrão, em:

```text
~/.config/pipeline-videos/youtube/token.json
```

Esse arquivo contém credenciais e não deve ser versionado nem copiado para a pasta de um vídeo. O
caminho pode ser alterado com `YOUTUBE_TOKEN_FILE` ou `--token-file`.

Os escopos solicitados são `youtube.upload` e `youtube.force-ssl`. O segundo é necessário para
gerenciar a faixa de legenda. Se os escopos forem alterados, refaça a autorização.

## 3. Revisar sem acessar a API

No fluxo aprovado, aprove imagens, metadados e render final. Para um pedido explícito de upload
privado sem aprovação humana, gere antes o candidato validado:

```powershell
uv run pipeline-videos render-review salmo-121
uv run pipeline-videos youtube preflight salmo-121 --privacy private --automated-review
```

No fluxo aprovado tradicional, execute:

```powershell
uv run pipeline-videos youtube preflight salmo-121
```

O preflight bloqueia:

- projeto demo, imagens ou thumbnail fixture;
- render final, thumbnail ou SRT ausentes;
- validação final incompleta;
- metadados ou render final sem aprovação humana, exceto no candidato privado produzido por
  `render-review` com `--automated-review` e política habilitada;
- título, descrição ou tags acima dos limites;
- primeiro upload com visibilidade diferente de `private` ou `unlisted`.

Em vídeos longos, a thumbnail aprovada é enviada pela API. Em Shorts, o preflight informa que a
escolha do quadro de capa é manual: o YouTube não permite enviar uma thumbnail personalizada para
Shorts da mesma forma que em vídeos longos. A arte da capa continua incorporada ao frame zero para
que ele possa ser selecionado no aplicativo móvel do YouTube.

Por padrão, o pacote é privado, não notifica inscritos, envia a legenda, declara que não é conteúdo
infantil e marca presença de mídia sintética. Revise essas classificações para cada vídeo:

```powershell
uv run pipeline-videos youtube preflight salmo-121 `
  --privacy unlisted `
  --not-made-for-kids `
  --synthetic-media `
  --captions `
  --no-notify-subscribers
```

## 4. Fazer o upload inicial

Use o `project_id` exibido no preflight como confirmação literal:

```powershell
uv run pipeline-videos youtube upload salmo-121 `
  --confirm-upload "14-07-2026_salmo-121" `
  --privacy private `
  --automated-review `
  --allow-external
```

O resultado fica em `youtube/publication.json`, sem tokens ou secrets. Uma nova execução com o mesmo
render reutiliza o `video_id` e apenas retoma/sincroniza legenda, metadados ou, em vídeos longos,
thumbnail pendente.
Se o MP4 mudou, o comando bloqueia outro upload. `--force-new-upload` é necessário para criar
deliberadamente um segundo vídeo; o registro anterior é arquivado em `youtube/publications/`.

Consulte o registro local ou o estado remoto:

```powershell
uv run pipeline-videos youtube status salmo-121
uv run pipeline-videos youtube status salmo-121 --remote
```

## 5. Publicar ou agendar

Somente depois de revisar o vídeo no YouTube, use o `video_id` como segunda confirmação.

Publicação imediata:

```powershell
uv run pipeline-videos youtube publish salmo-121 `
  --confirm-publication "VIDEO_ID" `
  --allow-external
```

Agendamento, sempre com data ISO 8601 e fuso:

```powershell
uv run pipeline-videos youtube publish salmo-121 `
  --confirm-publication "VIDEO_ID" `
  --publish-at "2026-07-20T19:00:00-03:00" `
  --allow-external
```

O comando verifica se o processamento remoto terminou. Para agendar, a API mantém o vídeo privado e
define `status.publishAt`, conforme a
[referência de vídeos](https://developers.google.com/youtube/v3/docs/videos/update).

## Limitações importantes

- Projetos de API não verificados criados após 28/07/2020 têm uploads restringidos a privado até
  passarem pela auditoria do YouTube. Veja [`videos.insert`](https://developers.google.com/youtube/v3/docs/videos/insert).
- Em vídeos longos, a thumbnail precisa ter até 2 MB. O pipeline cria um JPEG derivado quando
  necessário, sem sobrescrever a thumbnail aprovada. Veja
  [`thumbnails.set`](https://developers.google.com/youtube/v3/docs/thumbnails/set).
- Em Shorts, o pipeline não chama `thumbnails.set`, pois isso não define o quadro exibido nas
  superfícies de Shorts. Depois do upload, selecione o frame zero no aplicativo móvel do YouTube;
  essa escolha não está disponível no Studio. Veja
  [Adicionar thumbnails](https://support.google.com/youtube/answer/72431?hl=pt-BR) e
  [Criar Shorts](https://support.google.com/youtube/answer/10343433?hl=pt-BR).
- A faixa SRT usa `captions.insert`/`captions.update`; o recurso consome quota da API. Veja
  [`captions.insert`](https://developers.google.com/youtube/v3/docs/captions/insert).
- O comentário fixado e o teste A/B nativo do Studio não são automatizados neste marco.
- Nenhum comando habilita faturamento, compra quota, publica por conta própria ou altera o projeto
  do Google Cloud.
