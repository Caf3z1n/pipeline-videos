# Loop de desempenho para Shorts

Este documento transforma recomendações de criação em um ciclo observável, sem prometer
viralização e sem condensar sinais diferentes em uma nota artificial.

## Fontes analisadas

O desenho foi informado por três vídeos enviados para análise:

- [Hooks que interrompem o scroll](https://www.youtube.com/watch?v=Y3pAmxXNOU4): interrupção de
  padrão, situação específica, pergunta, comparação, consequência e início no meio da ação;
- [Retenção por composição](https://www.youtube.com/watch?v=IPW-MF8Kp0I): hook, progressão,
  payoff, ritmo e edição subordinada à narrativa;
- [Estrutura HPC](https://www.youtube.com/watch?v=gEQ0BLyVJhY): promessa inicial, avanço
  verificável e clímax que cumpre a promessa.

Esses materiais são hipóteses editoriais, não evidência causal. O pipeline rejeita as partes
enganosas: erro deliberado para provocar correção, comentário feito por conta alternativa, medo
inventado, promessa não cumprida e retenção vazia obtida apenas atrasando informação.

A calibração segue também a documentação oficial sobre
[apelo, engajamento e satisfação](https://support.google.com/youtube/answer/16559650?hl=pt-BR),
[Analytics de Shorts](https://support.google.com/youtube/answer/12942217?hl=pt-BR) e
[engajamento falso](https://support.google.com/youtube/answer/3399767?hl=pt-BR).

## Três estágios, três diagnósticos

O YouTube separa desempenho em apelo, engajamento e satisfação. O pipeline faz o mesmo:

| Estágio | Sinais principais | Pergunta operacional |
|---|---|---|
| Apelo | `stayed_to_watch_percent`, `swiped_away_percent` | A pessoa decidiu continuar? |
| Engajamento | duração e percentual médio assistido, retenção por marco | A promessa foi sustentada? |
| Satisfação | likes, comentários, shares e inscritos por engaged view | A entrega valeu a atenção? |

`engaged_views / views` é preservado como uma métrica derivada, mas nunca é apresentado como
substituto de `Stayed to watch`. A API pública do YouTube Analytics não expõe diretamente
`Stayed to watch`/`Swiped away`; os valores exatos devem vir do Studio ou de uma fonte combinada.
Da mesma forma, `views` não confirma a amostra de apelo quando `shown_in_feed` estiver ausente.

## Importação

Cada observação é imutável. Uma nova janela ou leitura recebe outro `snapshot_id`:

```powershell
uv run pipeline-videos analytics metrics-import `
  21-07-2026_joao-15-1-2-identity `
  .\snapshot-joao-15-72h.json

uv run pipeline-videos analytics diagnose 21-07-2026_joao-15-1-2-identity
```

Exemplo mínimo:

```json
{
  "snapshot_id": "joao-15-1-2-72h",
  "video_id": "YOUTUBE_VIDEO_ID",
  "published_at": "2026-07-18T09:00:00-03:00",
  "window_start": "2026-07-18T09:00:00-03:00",
  "window_end": "2026-07-21T09:00:00-03:00",
  "observed_at": "2026-07-21T09:00:00-03:00",
  "source": "youtube_studio",
  "processing_status": "final",
  "view_counting_standard": "shorts_starts_2025",
  "shown_in_feed": 1000,
  "views": 886,
  "engaged_views": 280,
  "stayed_to_watch_percent": 28.9,
  "swiped_away_percent": 71.1,
  "average_view_duration_seconds": 32.0,
  "average_percentage_viewed": 85.1,
  "video_duration_seconds": 38.0,
  "likes": 119,
  "comments": 12,
  "shares": 8,
  "subscribers_gained": 33,
  "duration_profile": "adaptive_compact_v1",
  "hook_contract": "cinematic_interruption_v1",
  "visual_standard_id": "biblical-midcentury-gouache-v1",
  "hook_mechanism": "concrete_human_tension",
  "opening_visual_standard": "scroll_stop_concrete_v1",
  "first_frame_pattern": "close_action_in_progress"
}
```

`opening_visual_standard` identifica o contrato de produção congelado; `first_frame_pattern`
descreve o que foi observado no bitmap e não deve receber o nome do contrato. Uploads novos gravam
automaticamente duração, hook e padrões visuais em `youtube/publication.json`; o import preenche
esses campos somente a partir desse fingerprint imutável e rejeita divergências. A duração real é
capturada por `ffprobe` antes do upload; o fallback do manifesto existe apenas nos fluxos de teste
que desativam explicitamente a validação. Para uploads
legados sem fingerprint, informe-os explicitamente no snapshot.

Não registre duas leituras do mesmo `video_id` no mesmo `observed_at`. Una Studio e API em um único
snapshot com `source=combined`; uma nova janela usa outro horário e outro `snapshot_id`.

O import cria ou atualiza deterministicamente:

- `analytics/shorts-snapshots.json` — observações imutáveis;
- `analytics/shorts-diagnostics.json` — diagnóstico e notificações estáveis;
- eventos somente quando entram snapshots novos ou muda o conjunto de alertas ativos.

## Política inicial

`appeal_engagement_satisfaction_v1` usa como linha de base inicial o snapshot do canal entre
23/06/2026 e 20/07/2026 (`Continuaram assistindo = 30,5%`). Isso é uma calibração local versionada,
não um número universal do YouTube.

- menos de 200 exposições no feed: amostra insuficiente para apelo;
- menos de 200 `engaged_views`: amostra insuficiente para progressão/percentual assistido;
- menos de 22% em `Stayed to watch`: apelo crítico;
- 22% a menos de 30,5%: abaixo da linha de base;
- 30,5% a menos de 35%: monitoramento;
- 35% a menos de 38%: saudável;
- 38% ou mais: faixa candidata a referência;
- menos de 75% assistido: engajamento fraco;
- 90% ou mais: engajamento forte;
- 100% ou mais: sinal forte de replay, não prova isolada de satisfação.

Combinações acionáveis:

- baixo apelo + engajamento preservado: corrigir hook e primeiro quadro;
- baixo apelo + engajamento fraco: corrigir abertura e progressão;
- apelo saudável + engajamento fraco: promessa boa, desenvolvimento fraco;
- 38% ou mais de apelo + 100% ou mais assistido: candidato a referência, ainda sujeito a coorte.

O resultado é `provisional` enquanto a métrica processa ou enquanto `window_end` ainda estiver nas
primeiras 48 horas após a publicação; exportar uma janela inicial dias depois não a torna madura.
Quando exports chegam fora de ordem, `window_end` define qual evidência é mais recente;
`observed_at` serve apenas como desempate. Um export tardio de uma janela antiga não resolve nem
reativa alertas mais novos.
Alertas são resolvidos por estágio: uma leitura da API sem `Stayed to watch` não apaga um alerta de
apelo do Studio. Só evidência confirmada e compatível por coorte de duração, contrato de hook,
estilo visual e contrato de abertura pode entrar como advisory de uma nova fila de planejamento.

## Contrato pré-produção

Projetos novos salvam `images.opening_visual_standard=scroll_stop_concrete_v1`. No primeiro bloco
visual, o planejador recebe estas exigências:

- sujeito ou objeto dominante;
- ação observável já em curso;
- tipo de mudança física e estados visíveis inicial/final distintos;
- tensão, escolha ou contraste coerente com o hook;
- emoção legível em menos de um segundo;
- plano médio, close ou objeto específico dominante;
- imagem e fala abrem a mesma pergunta que a fala de Jesus resolve.

No perfil `unrestricted_scene_v1`, isso não recria área segura artificial para legenda, bordas ou
arte de capa. O enquadramento permanece livre; apenas deixa de cair no plano geral contemplativo
genérico que enfraquecia o primeiro quadro pintado.

Na seleção compacta, o pipeline também calcula um prior reproduzível — clareza, concretude e
visualizabilidade — a partir do próprio hook curado. A pontuação é apenas um desempate suave:
aberturas genéricas sem situação, ação ou tensão perdem prioridade, e o hook realmente persistido
em um Short compacto recente recebe penalidade quando se repete. Métricas confirmadas entram como
advisory no roteiro padrão e no planejamento visual compacto; por segurança, uma amostra esparsa
não reescreve automaticamente o hook bíblico já selado. O prior nunca bloqueia uma citação, altera
a fala de Jesus ou se apresenta como prova de desempenho.

Os advisories são congelados no instante em que a fila é criada. Novas métricas não invalidam um
planner em andamento; passam a valer somente na próxima fila deliberadamente materializada. O
fallback visual usa o tema, o conflito, a pergunta central, o hook e a passagem verificada para
selecionar um motivo concreto, em vez de repetir uma única cena genérica para todo o catálogo.

## Experimentos recomendados

Altere um eixo por coorte e mantenha passagem, duração, voz, música e perfil visual tão estáveis
quanto possível:

1. plano geral contextual versus close com ação dominante;
2. frase emocional abstrata versus situação humana concreta;
3. cena que dramatiza o conflito verbal versus acolhimento genérico;
4. progressão com informação nova por beat versus repetição da mesma tese;
5. payoff coerente logo após o hook versus resposta atrasada artificialmente.

Não promova um padrão por causa de um único outlier. Compare Shorts do mesmo perfil, registre a
variável alterada e preserve precisão bíblica, segurança e satisfação como guards.
