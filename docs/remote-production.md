# Produção remota

## Contrato

A VPS executa o mesmo pipeline atual usado localmente. Não existe modo remoto alternativo nem
fallback para o executor antigo. Cada job usa um coordenador persistente em `gpt-5.6-sol` e os
mesmos contratos do fluxo local; o compacto cria somente os especialistas ainda necessários e
mantém roteiro/revisão no Python.

Cada pedido declara:

- `video_format`: `short` ou `long`;
- `topic`: tema opcional; vazio usa o catálogo local e evita repetir passagens;
- `creative_mode`: modo editorial explícito ou `auto` para variar conforme passagem e histórico;
- `hook_policy`: `auto`, `required`, `optional` ou `none`; Shorts não aceitam `none`;
- `hook_family`: família concreta ou `auto`; Shorts padrão aceitam somente as seis famílias de
  `hook_contract=user_six_v1`; Shorts compactos aceitam somente `emotional_interruption`;
- `duration_seconds`: opcional, mínimo de 15 s e padrão de 50 s para Short; mínimo de 30 s e padrão
  de 540 s para longo;
- `voice_variant`: `jesus_1` (padrão) ou `jesus_2`;
- `start_policy`: `immediate` ou `overnight`;
- `publication_mode`: `private_review` ou `scheduled`;
- `publish_at`: obrigatório somente em `scheduled`, sempre com fuso;
- `external_services_authorized`: autoriza Imagegen, ElevenLabs e upload privado naquele job.

`overnight` inicia jobs somente entre 01:00 e 05:00 em `America/Sao_Paulo`. A fila executa um job
por vez. Temas específicos também usam o catálogo bíblico verificado; o Python reconstrói briefing
e fontes a partir dos IDs escolhidos.

## Sessão persistente e especialistas

O primeiro checkpoint inicia `codex exec`. O executor captura o identificador da sessão raiz e o
salva em `codex_session_id`. Os checkpoints retomam essa sessão com `codex exec resume`. O
No compacto, o coordenador cria um planejador visual em raciocínio médio, até dois workers de imagem
em baixo e um especialista de crescimento em médio, todos com `fork_turns=none`. Hook, citação,
referência, seções e revisão bíblica vêm deterministicamente do catálogo verificado. Fora do
compacto, roteirista e revisor independente continuam em raciocínio alto quando a fila os exigir.

O remoto usa o mesmo perfil adaptativo local. Um Short de 15 segundos usa até dois workers de
imagem, aceita somente `devotional_application` ou `pastoral_encouragement`, seleciona apenas fala
direta de Jesus cujo trecho literal caiba no limite de 28 palavras e sela os limites persistidos de
41 a 46 palavras e cinco a seis cenas. O hook curado ocupa de 0 a 3,5 segundos, a
citação de 3,5 a 12,3 e a referência de 12,3 a 15 segundos; não existe CTA. A seleção remota recebe
somente combinações que passam os pisos de força do hook, valor da mensagem, replay e duração. O
áudio nunca é estendido com silêncio. Cobertura do alvo preferencial, pausa e silêncio terminal são
avisos em `completion_first_v1`; integridade e duração solicitada continuam bloqueantes. A música é selecionada exclusivamente
entre `Aloboi .mp3` e `Leaning.mp3` na biblioteca compacta.

O modelo não varia: coordenador e especialistas usam Sol. O esforço varia somente pelo contrato da
função. Áudio, música, legendas, imports, render, validação e YouTube não são entregues aos agentes.

## Imagens e thumbnail

Cada cena segue a política retomável: prompt original, repetição idêntica após
`moderation_blocked` e, somente após o segundo bloqueio, uma alternativa que preserve personagem,
idade aparente, rosto, cabelo, figurino, proporções, estilo, paleta, ambiente e sentido narrativo.

A fila de crescimento declara exatamente um título. No perfil pintado de Shorts, o modelo devolve
somente um rascunho compacto de metadados, não chama Imagegen e o YouTube escolhe um frame do vídeo.
No perfil 3D legado, o bitmap nasce no Imagegen editando a abertura. Em vídeos longos, nasce como
`generated_theme_art`: horizontal 16:9, sem imagem de referência, texto ou marcações e baseada no
tema central. Não há compositor nem overlay local.

## Estados e publicação

```text
queued -> running -> completed
       <- recuperaÃ§Ã£o automÃ¡tica com backoff
                  -> waiting_youtube -> completed
                     recuperaÃ§Ã£o automÃ¡tica com backoff
failed -> queued (retry de jobs legados)
queued/waiting_youtube -> cancelled
```

Falhas de execução não encerram novos jobs. Erros corrigíveis acionam no máximo um checkpoint de
reparo agregado. No compacto, uma segunda falha materializa o fallback determinístico de roteiro
visual ou metadados e continua a produção. Somente falhas técnicas realmente bloqueantes voltam para
`queued` (ou permanecem em `waiting_youtube`) com backoff, preservando projeto, sessão e artefatos.

No crescimento, o checkpoint de reparo é limitado a uma execução por tentativa. O validador exige
o padrão 3D como estilo positivo e ignora termos fotográficos presentes dentro de seções negativas
como `Avoid:`; fotografia ou live action declarados como estilo positivo continuam bloqueados.

`render-review` usa os ativos ativos em qualidade final sem registrar aprovação humana. Upload
automatizado exige a política de revisão privada, render candidato válido, validação técnica e
visibilidade inicial `private`. Agendamento exige também `publication_mode=scheduled` e
`publish_at` explícito. O executor Python, não o Codex, chama a API do YouTube.

## Segurança e operação

`.env`, OAuth, músicas, jobs e projetos ficam em `shared/`, fora dos releases Git. A VPS mantém
`ELEVENLABS_VOICE_ID`, os presets `ELEVENLABS_VOICE_JESUS_1_ID` e
`ELEVENLABS_VOICE_JESUS_2_ID`, além de `ELEVENLABS_MODEL_ID`, nesse arquivo persistente. Nunca envie
esses arquivos ao GitHub ou aos logs. O registro compartilhado de conteúdo permanece em
`shared/content-registry.git` e guarda somente metadados editoriais sem roteiro, mídia ou segredo.

Antes de criar um projeto automático, o worker carrega o catálogo base e o banco curado de expansão
BPM. A capacidade é calculada por formato e somente passagens principais do mesmo formato consomem
o ciclo. Referências complementares de vídeos longos não reduzem o estoque de Shorts. Se todas as
passagens já foram usadas, o próximo ciclo é aberto automaticamente; uma passagem ainda reservada
por outro job nunca é reutilizada.

O timer systemd chama `pipeline-videos remote work-once` a cada minuto:

```bash
/home/deploy/pipeline-videos/bin/video-job list
/home/deploy/pipeline-videos/bin/video-job status JOB_ID
/home/deploy/pipeline-videos/bin/video-job cancel JOB_ID
/home/deploy/pipeline-videos/bin/video-job retry JOB_ID
```

## Agenda diária de Shorts

Uma agenda pode criar um ou dois Shorts por dia. Cada slot vira um job idempotente e uma sessão raiz
independente. O render é enviado inicialmente como privado; a API só registra a publicação pública
para o `publish_at` calculado no fuso configurado.

Exemplo com dois Shorts produzidos na madrugada e publicados no dia seguinte:

```bash
uv run pipeline-videos remote schedule-create \
  --name madrugada \
  --production-time 01:00 \
  --production-time 03:00 \
  --publication-time 12:00 \
  --publication-time 19:00 \
  --publish-days-after 1 \
  --timezone America/Sao_Paulo \
  --authorize-external \
  --authorize-public-schedule

uv run pipeline-videos remote schedule-list
uv run pipeline-videos remote schedule-disable SCHEDULE_ID
uv run pipeline-videos remote schedule-enable SCHEDULE_ID
```

O timer materializa automaticamente os slots do dia antes de procurar o próximo job. Reinícios e
chamadas repetidas não duplicam vídeos, pois o ID deriva da agenda, data local e índice do slot. A
fila continua executando um job por vez; se a primeira produção ultrapassar o segundo horário, o
segundo job começa em seguida sem compartilhar sessão.

Logs ficam em `shared/jobs/logs/`. O status inclui `current_phase`, `codex_session_id`,
`codex_run_count` e `codex_usage`. O contrato técnico está em
[Contrato do agente remoto com paridade local](remote-agent-contract.md).
