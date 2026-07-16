# Produção remota

## Contrato

A VPS executa o mesmo pipeline atual usado localmente. Não existe modo remoto alternativo nem
fallback para o executor antigo. Cada job usa um agente principal persistente em `gpt-5.6-sol`,
raciocínio médio e sem subagentes; as etapas determinísticas continuam no Python.

Cada pedido declara:

- `video_format`: `short` ou `long`;
- `topic`: tema opcional; vazio usa o catálogo local e evita repetir passagens;
- `duration_seconds`: opcional, com padrão de 50 s para Short e 540 s para longo;
- `start_policy`: `immediate` ou `overnight`;
- `publication_mode`: `private_review` ou `scheduled`;
- `publish_at`: obrigatório somente em `scheduled`, sempre com fuso;
- `external_services_authorized`: autoriza Imagegen, ElevenLabs e upload privado naquele job.

`overnight` inicia jobs somente entre 01:00 e 05:00 em `America/Sao_Paulo`. A fila executa um job
por vez. Temas específicos também usam o catálogo bíblico verificado; o Python reconstrói briefing
e fontes a partir dos IDs escolhidos.

## Sessão persistente

O primeiro checkpoint inicia `codex exec`. O executor captura o identificador da sessão e o salva
em `codex_session_id`. Seleção editorial, roteiro, imagens e crescimento retomam essa sessão com
`codex exec resume`, preservando o contexto útil do job sem criar subagentes.

O modelo e o esforço não podem variar por fase ou variável de ambiente: todos os checkpoints usam
Sol e `medium`. Áudio, música, legendas, imports, render, validação e YouTube não são entregues ao
agente.

## Imagens e thumbnail

Cada cena segue a política retomável: prompt original, repetição idêntica após
`moderation_blocked` e, somente após o segundo bloqueio, uma alternativa que preserve personagem,
idade aparente, rosto, cabelo, figurino, proporções, estilo, paleta, ambiente e sentido narrativo.

A fila de crescimento declara exatamente um título e uma thumbnail. O bitmap nasce no Imagegen com
texto e elemento narrativo incorporados, editando a imagem ativa da abertura. O importador aceita
somente `generated_integrated_art`; não há compositor, overlay ou reutilização silenciosa de cenas
como fallback.

## Estados e publicação

```text
queued -> running -> completed
                  -> waiting_youtube -> completed
                  -> failed -> queued (retry)
queued/waiting_youtube -> cancelled
```

`render-review` usa os ativos ativos em qualidade final sem registrar aprovação humana. Upload
automatizado exige a política de revisão privada, render candidato válido, validação técnica e
visibilidade inicial `private`. Agendamento exige também `publication_mode=scheduled` e
`publish_at` explícito. O executor Python, não o Codex, chama a API do YouTube.

## Segurança e operação

`.env`, OAuth, músicas, jobs e projetos ficam em `shared/`, fora dos releases Git. Nunca envie esses
arquivos ao GitHub ou aos logs. O registro compartilhado de conteúdo permanece em
`shared/content-registry.git` e guarda somente metadados editoriais sem roteiro, mídia ou segredo.

O timer systemd chama `pipeline-videos remote work-once` a cada minuto:

```bash
/home/deploy/pipeline-videos/bin/video-job list
/home/deploy/pipeline-videos/bin/video-job status JOB_ID
/home/deploy/pipeline-videos/bin/video-job cancel JOB_ID
/home/deploy/pipeline-videos/bin/video-job retry JOB_ID
```

Logs ficam em `shared/jobs/logs/`. O status inclui `current_phase`, `codex_session_id`,
`codex_run_count` e `codex_usage`. O contrato técnico está em
[Contrato do agente remoto persistente](remote-agent-contract.md).
