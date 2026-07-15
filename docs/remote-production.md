# Produção remota econômica

## Contrato

`remote_review` é um modo aditivo para produção na VPS. Ele preserva os contratos bíblicos, visuais,
de áudio, render e validação, mas usa um único agente Codex e exatamente um par título+thumbnail.
Os demais modos continuam com suas quantidades e especialistas atuais.

Cada pedido declara:

- `video_format`: `short` ou `long`;
- `topic`: tema opcional; vazio usa o catálogo local e evita repetir passagens;
- `duration_seconds`: opcional, com padrão de 50 s para Short e 540 s para longo;
- `start_policy`: `immediate` ou `overnight`;
- `publication_mode`: `private_review` ou `scheduled`;
- `publish_at`: obrigatório somente em `scheduled`, sempre com fuso;
- `external_services_authorized`: precisa ser `true` para autorizar Imagegen, ElevenLabs e upload
  privado naquele job.

`overnight` permite iniciar jobs somente entre 01:00 e 05:00 em `America/Sao_Paulo`. Um job iniciado
nesse intervalo pode terminar depois das 05:00. A fila executa apenas um job por vez.

## Estados

```text
queued -> running -> completed
                  -> waiting_youtube -> completed
                  -> failed -> queued (retry)
queued/waiting_youtube -> cancelled
```

`waiting_youtube` significa que o vídeo já está privado e o executor aguarda o processamento remoto
antes de gravar `publishAt`. Se o job for cancelado nesse estado, o vídeo continua privado.

## Thumbnail única

A fila de crescimento declara `title_variants=1` e `thumbnail_variants=1`. O bitmap final nasce no
Imagegen com texto exato, uma palavra vermelha e seta, X, sublinhado ou pincel apontando para um alvo
real. O importador não aplica texto ou símbolos localmente. Título e frase da capa se complementam.

Em Short, a thumbnail ocupa exatamente o primeiro frame e ainda precisa ser selecionada manualmente
como capa no aplicativo do YouTube. Em vídeo longo, o executor usa `thumbnails.set` após o upload.

## Segurança de publicação

`render-review` usa os ativos ativos em qualidade final, mas não altera aprovações humanas. O bypass
de upload só é aceito quando todas as condições abaixo são verdadeiras:

1. modo `remote_review`;
2. política `allow_automated_private_review_upload=true` no projeto;
3. render produzido por `ffmpeg-review-candidate`;
4. validação técnica concluída e válida;
5. visibilidade inicial exatamente `private`.

Agendamento automático exige ainda `allow_automated_scheduled_release=true` e `publish_at` explícito
no pedido. O Codex produz o projeto, mas não chama a API do YouTube; o executor determinístico faz
upload e agendamento.

## Segredos

Na VPS, `.env`, token OAuth, biblioteca de músicas, jobs e projetos ficam em `shared/`, fora dos
releases Git. Use permissões `600` para `.env` e token. Nunca envie esses arquivos ao GitHub nem os
inclua nos logs. O token OAuth renovável já autorizado pode ser copiado para
`~/.config/pipeline-videos/youtube/token.json`; não é necessário repetir o consentimento enquanto o
refresh token continuar válido.

## Operação

O timer systemd chama `pipeline-videos remote work-once` a cada minuto. Os comandos de leitura e
controle são idempotentes:

```bash
/home/deploy/pipeline-videos/bin/video-job list
/home/deploy/pipeline-videos/bin/video-job status JOB_ID
/home/deploy/pipeline-videos/bin/video-job cancel JOB_ID
/home/deploy/pipeline-videos/bin/video-job retry JOB_ID
```

O serviço usa prioridade reduzida de CPU e I/O e executa como `deploy`. Logs ficam em
`shared/jobs/logs/` com acesso restrito e também podem ser consultados por:

```bash
journalctl -u pipeline-videos-worker.service -n 100 --no-pager
systemctl status pipeline-videos-worker.timer --no-pager
```
