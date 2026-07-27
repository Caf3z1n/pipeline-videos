# YouTube Analytics API

O pipeline possui um caminho API-first para leitura de desempenho. Ele não usa o
navegador para consultar métricas: o acesso é feito pela **YouTube Analytics API
v2**, com OAuth somente leitura e um token separado do token usado para upload.

## Credencial e escopo

No Google Cloud, habilite **YouTube Analytics API** no mesmo projeto em que está o
cliente OAuth Desktop. O consentimento precisa incluir:

```
https://www.googleapis.com/auth/yt-analytics.readonly
https://www.googleapis.com/auth/youtube.readonly
```

O token de upload (`YOUTUBE_TOKEN_FILE`, com `youtube.upload`/`youtube.force-ssl`)
não é convertido automaticamente em token de Analytics. Configure:

```
YOUTUBE_ANALYTICS_CLIENT_SECRETS_FILE=C:\\seguro\\client_secret.json
YOUTUBE_ANALYTICS_TOKEN_FILE=C:\\Users\\Pedro Henrique\\.config\\pipeline-videos\\youtube\\analytics-token.json
```

O arquivo de credencial e o token ficam fora do repositório e nunca devem ser
impressos ou versionados. A autorização é executada uma vez, explicitamente:

```powershell
uv run pipeline-videos analytics auth --allow-external `
  --client-secrets C:\\seguro\\client_secret.json
```

Se a organização exigir verificação do app, o consentimento pode ficar limitado a
usuários de teste. Sem esse escopo ou sem refresh token, o comando `fetch` falha
antes da consulta e informa a necessidade de nova autorização.

## Consulta e cruzamento local

```powershell
uv run pipeline-videos analytics fetch `
  --start-date 2026-07-01 `
  --end-date 2026-07-24 `
  --output audit/youtube-analytics-2026-07-01_2026-07-24.json
```

Para um vídeo:

```powershell
uv run pipeline-videos analytics fetch `
  --start-date 2026-07-01 --end-date 2026-07-24 `
  --video-id VIDEO_ID --output audit/video-metrics.json
```

A consulta padrão executa dois relatórios compatíveis, porque a API não aceita `video,day` como
uma combinação universal:

- `rows`: consolidado por `video`, ordenado por `-views`, com até 200 resultados;
- `daily_rows`: série temporal por `day`, agregada para o canal ou filtrada pelo `--video-id`.

Os dois usam as métricas:

`views`, `estimatedMinutesWatched`, `averageViewDuration`,
`averageViewPercentage`, `likes`, `comments`, `shares`, `subscribersGained`,
`subscribersLost` e `engagedViews`.

Para tentar `uniques` (quando a conta/API oferecer a métrica), acrescente
`--include-unique-viewers`. A consulta `reports.query` atualmente também exige o
escopo `youtube.readonly`; por isso os dois escopos são solicitados juntos no
token separado.

Cada linha é enriquecida com a publicação local correspondente (título, projeto,
formato, duração e prévia da narração), quando o `video_id` estiver em
`projects/*/youtube/publication.json`. Os registros locais privados também são
indexados, mas naturalmente não terão atividade pública na API.

`uniques` depende da disponibilidade da conta, janela e combinação de
dimensões. Ela entra somente na série diária; o consolidado por vídeo continua com
as métricas compatíveis. Se a API rejeitar apenas essa métrica, o cliente repete a consulta sem
ela e grava `unavailable_metrics: ["uniques"]`. A API não expõe a métrica
do Studio **Continuaram assistindo / Stayed to watch** como uma coluna equivalente;
essa informação continua sendo importada separadamente por
`analytics metrics-import` quando disponível em um snapshot do Studio.

O arquivo de saída é um artefato de análise, não altera `project.json`, publicação,
roteiro ou produção. Ele pode ser versionado somente se não contiver dados que o
usuário não queira compartilhar.

## Testes

`tests/test_youtube_analytics.py` usa um serviço falso com a mesma superfície
`reports().query().execute()` da biblioteca Google e cobre conversão de linhas,
fallback de `uniqueViewers` e cruzamento por `video_id`; nenhuma chamada real é
feita durante os testes.
