# Pipeline de vídeos bíblicos

Pipeline local e remoto para produzir Shorts e vídeos longos com roteiro bíblico verificável,
narração, imagens, música, legendas, render final e metadados do YouTube.

## Contrato editorial atual

- Jesus é o eu lírico de toda locução autoral, em Shorts e vídeos longos.
- A voz usa primeira pessoa e linguagem humana, direta e fluida.
- Citações bíblicas literais permanecem identificadas por aspas, referência, tradução e fonte.
- Dramatização autoral nunca é apresentada como uma fala bíblica documentada.
- Frases metalinguísticas e construções baseadas em listas de ausências são bloqueadas.
- Curiosidade, tensão e retenção precisam ser honestas e entregar o payoff prometido.

## Contrato visual atual

Todo projeto usa exclusivamente `biblical-midcentury-gouache-v1`:

- guache opaco e têmpera;
- acabamento fosco e impressão editorial limpa;
- formas simples, rostos serenos e luz natural difusa;
- paleta suavizada e continuidade visual entre cenas;
- um momento, um ambiente e uma ação por imagem;
- sem colagem, painéis ou texto legível dentro da arte.

Shorts e vídeos longos usam `narration_block_visual_v1`. A locução é dividida em blocos contíguos;
cada bloco recebe uma cena concreta e distinta, classificada como `literal`, `contextual` ou
`symbolic`. Os blocos precisam reconstruir integralmente a locução e preservar a ordem das seções.

A primeira cena usa `scroll_stop_concrete_v1`: sujeito ou objeto dominante, ação já em curso e
tensão, escolha ou contraste coerente com a abertura.

O pipeline não produz nem envia imagem de capa personalizada. O especialista de crescimento gera
somente título, descrição, tags e comentário fixado.

## Fluxo

```text
briefing e fontes
→ roteiro e validação incorporada de fontes
→ blocos visuais
→ imagens em guache
→ narração e música
→ legendas
→ render final
→ validação
→ metadados
→ upload privado, somente quando solicitado
```

A pasta do projeto é a fonte de verdade. O estado compartilhado fica em `project.json`; workers
escrevem apenas em staging e o coordenador principal importa cada lote uma única vez.

## Requisitos

- Python 3.12
- `uv`
- FFmpeg e FFprobe no `PATH`
- ImageMagick disponível quando necessário
- ElevenLabs configurada para voz real
- credenciais OAuth do YouTube somente para upload ou publicação solicitados

Instalação:

```powershell
uv sync
uv run pipeline-videos doctor
```

## Estrutura

```text
projects/
├── shorts/
└── videos-longos/

config/
├── bible/
├── palettes/
└── styles/

src/pipeline_videos/
├── project.py
├── shorts_script.py
├── long_form_script.py
├── scenes.py
├── images.py
├── audio.py
├── captions.py
├── render.py
├── validation.py
├── growth.py
├── metadata.py
├── publication.py
└── remote_jobs.py
```

Dentro de cada projeto:

```text
project.json
briefing/
sources/
script/
visual/
images/
audio/
captions/
render/
youtube/
audit/
```

## Criar projetos

Novo vídeo longo:

```powershell
uv run pipeline-videos new historia-de-andre `
  --title "A história de André" `
  --duration 420
```

Novo Short:

```powershell
uv run pipeline-videos new-short `
  --duration 50
```

Curiosidade bíblica de alto interesse:

```powershell
uv run pipeline-videos new-short `
  --profile high-stakes `
  --duration 50
```

O catálogo high-stakes é de ciclo único. Quando todos os temas estiverem usados ou reservados, o
pipeline bloqueia uma nova criação até que outra rodada curada seja adicionada.

Vídeo dedicado a personagem:

```powershell
uv run pipeline-videos new-character-video andre `
  --duration 420
```

## Duração

Uma duração comum é preferência, não obrigação exata. A narração permanece em velocidade natural
`1.0`; o pipeline não usa silêncio, padding, `atempo` ou time-stretch para preencher poucos
segundos.

Use duração exata somente quando ela for realmente necessária:

```powershell
uv run pipeline-videos new-short --duration 50 --exact-duration
```

## Produção

Antes de produzir:

```powershell
uv run pipeline-videos status <id-ou-pasta>
uv run pipeline-videos run <id-ou-pasta> --dry-run
```

Execução retomável:

```powershell
uv run pipeline-videos autopilot <id-ou-pasta> --wait-external --allow-external
```

O pedido explícito de criar, produzir ou retomar um vídeo autoriza a síntese real da ElevenLabs.
Isso não autoriza upload, publicação, agendamento, compra ou mudança de faturamento.

## Roteiro

Shorts fora do perfil compacto usam um especialista de roteiro e validação incorporada de fontes,
sem agente revisor separado. O perfil compacto sela deterministicamente hook, citação e referência;
o modelo planeja somente as cenas.

Vídeos longos usam progressão por movimentos, reengajamentos proporcionais à duração e citações
literais distribuídas. O primeiro versículo entra até 75 segundos, o intervalo máximo é de 105
segundos e a parte final não passa de 120 segundos sem âncora bíblica.

Em todos os formatos:

- `narrator_identity=jesus_first_person_v1`;
- a maior parte do desenvolvimento avança por fatos, ações e acontecimentos;
- explicações editoriais não aparecem dentro da locução;
- cada cena cobre um bloco literal da narração.

## Imagens

O plano de imagens fica em `images/codex-queue.json`.

- processe somente itens pendentes;
- use no máximo dois workers simultâneos;
- faça uma chamada de geração por tentativa;
- em `moderation_blocked`, repita o prompt uma vez;
- após um segundo bloqueio, tente uma alternativa que preserve identidade, figurino, proporções,
  estilo, paleta, ambiente e sentido;
- copie cada resultado para o staging declarado;
- importe o lote uma única vez.

## Áudio e música

A voz padrão é `jesus_1`; `jesus_2` continua disponível por pedido explícito. O modelo padrão é
`eleven_multilingual_v2`.

Projetos novos usam `voice_music_loudness_v1`:

- narração: `-18 LUFS`;
- música: `-31 LUFS`;
- diferença: `13 LU`;
- limites de true peak antes do `amix`.

## Render

Existe um único candidato final:

- H.264;
- CRF 12;
- preset `slow`;
- supersampling 3;
- movimento `smooth_pendulum`;
- transições `cinematic_mix` de 0,38 s;
- blur uniforme sigma 8.

Legendas usam Montserrat ExtraBold, caixa alta, largura 0,68, até três palavras por linha e palavra
ativa em card vermelho.

## Validação

```powershell
uv run pipeline-videos validate <id-ou-pasta>
```

São bloqueantes:

- mídia ausente, corrompida ou fora da política de duração;
- fonte ou fidelidade bíblica inválida;
- ativo obrigatório ausente;
- risco de segurança ou privacidade;
- publicação ou custo externo não autorizado;
- mix de áudio fora do contrato.

## YouTube

O especialista gera um único título no padrão `Enfoque concreto | Passagem`, uma descrição única,
oito a quinze tags relevantes e um comentário fixado específico. SEO é tratado como hipótese, nunca
como promessa de alcance.

O primeiro envio aceita somente `private` ou `unlisted`. O upload privado automatizado usa
`render-review` e não registra aprovação humana.

```powershell
uv run pipeline-videos youtube upload <id-ou-pasta> `
  --allow-external `
  --confirm-upload <id-do-projeto> `
  --automated-review
```

Publicação pública ou agendada é uma ação separada e exige pedido explícito.

## Produção remota

Cada job remoto tem uma sessão persistente do coordenador e checkpoints retomáveis. Áudio, música,
legendas, imports, render, validação e upload são executados deterministicamente. O coordenador e
especialistas usam `gpt-5.6-sol`; até dois workers de imagem podem atuar em paralelo.

Quando as imagens ficam prontas, o modelo sai do fluxo. O servidor renderiza em um único encode
H.264, valida e envia o candidato privado ao YouTube. Para aguardar sem polling de modelo:

```powershell
uv run pipeline-videos remote wait <job-id> --timeout 21600
```

O job grava um evento terminal em `completed`, `failed` ou `cancelled`. Upload interrompido em
estado indeterminado é bloqueado para impedir duplicata silenciosa.

### Roteiros pré-aprovados

Antes de existir um projeto de vídeo, temas e roteiros passam pela bancada editorial. O agente
sincroniza uma vez os estados de produção da VPS pelo controlador `request-video-production`,
consulta a etapa atual e faz uma única pergunta curta; nenhuma ação é iniciada até a etapa ser
confirmada:

```powershell
powershell -ExecutionPolicy Bypass -File `
  "$env:USERPROFILE\.codex\skills\request-video-production\scripts\video-job.ps1" `
  -Action ApprovedSync
uv run pipeline-videos approved-scripts workflow-status
```

O status separa `script_counts.approved` (aprovação editorial histórica) de
`production_counts.approved` (roteiros ainda disponíveis). A sincronização confere
`approved_script_id + content_hash`, nunca rebaixa `reserved` ou `produced` e não altera o conteúdo
congelado.

O fluxo é sequencial e cada comando executa somente sua própria etapa:

```powershell
# 1. Salvar sugestões, sem criar roteiro
uv run pipeline-videos approved-scripts topic-suggest `
  --title "Por que Jesus não chamou os anjos?" `
  --question "Se eu podia chamar anjos, por que aceitei a prisão?"

# 2. Aprovar vários temas, ainda sem escrever roteiros
uv run pipeline-videos approved-scripts topic-approve `
  --topic-id <tema-1> `
  --topic-id <tema-2>

# 3. Preparar uma única fila econômica para os temas aprovados
uv run pipeline-videos approved-scripts draft-batch-prepare --count 3

# 4. Depois da escrita e do preflight de fontes na mesma saída, mostrar os textos
uv run pipeline-videos approved-scripts draft-batch-review <lote>

# 5. Aprovar todos os textos exibidos com um único hash
uv run pipeline-videos approved-scripts draft-batch-approve <lote> `
  --confirm-hash <hash> `
  --approved-by Pedro
```

`draft-batch-prepare` não inicia agente, não cria projeto e não produz mídia. Quando a escrita for
confirmada, o contrato permite no máximo um roteirista e nenhum revisor bíblico separado. Fontes,
pontos obrigatórios e limites de incerteza entram na fila; o mesmo roteirista faz a conferência de
fonte e o importador aplica `inline_source_guard_v1`. Cada roteiro precisa começar com um hook
high-stakes forte, manter Jesus como eu lírico, fechar o payoff prometido e atingir nota mínima 8
em hook, clareza e payoff. A referência de qualidade é
`published_high_stakes_last_three_v1`.

Quando o usuário aprova os textos já exibidos, a transição é
`hash_only_no_model_v1`: nenhuma pesquisa, revisão ou chamada de modelo é aberta depois da
aprovação. O comando confere o hash, congela o lote e materializa automaticamente um pacote no pool
de produção para cada roteiro. Essa ponte é idempotente e acontece antes de qualquer mídia.

Um roteiro pode então ser liberado pelo título exato, sem procurar IDs manualmente:

```powershell
uv run pipeline-videos approved-scripts release `
  --title "Por que usei barro para curar o cego?" `
  --authorize-external
```

O comando reserva somente esse pacote e cria um job remoto `approved_fast_v1`, com voz `jesus_1`,
música `leaning` e upload privado. Roteiro, CTA, SEO, fontes e plano visual já estão congelados; não
há roteirista, revisor bíblico, especialista de crescimento nem novo preflight editorial nessa
liberação. O executor confere somente o hash imutável antes da mídia; conteúdo e decisões aprovadas
não são reinterpretados, pontuados ou validados novamente.

A duração informada na bancada é somente uma estimativa. Depois da aprovação por hash, o roteiro
usa `approved_script_locked_v1`: nenhuma contagem de palavras ou diferença em relação a 50 segundos
pode alterar ou bloquear o texto. A voz natural define a duração efetiva do Short, seja ela menor
ou maior que a estimativa. O pipeline preserva integralmente o roteiro e registra a duração real
depois da síntese.

O perfil `approved_fast_v1` separa a decisão editorial da produção. Um projeto de Short já
roteirizado e revisado pode ser congelado com narração, CTA escolhido, fontes, revisão bíblica,
plano visual e hash imutável:

```powershell
uv run pipeline-videos approved-scripts capture <projeto> `
  --voice jesus_1 `
  --music leaning

uv run pipeline-videos approved-scripts cta-select <id> `
  --option compartilhar

uv run pipeline-videos approved-scripts seo-set <id> `
  --title "Por que Jesus manteve Judas? | João 6:70-71" `
  --description "Jesus sabia da futura traição de Judas. Entenda como conhecimento, escolha e responsabilidade aparecem nessa dúvida bíblica." `
  --hashtag "Judas Iscariotes" `
  --hashtag "Livre Arbítrio" `
  --hashtag "Traição de Judas"

uv run pipeline-videos approved-scripts approve <id> `
  --confirm-hash <hash-exibido> `
  --approved-by Pedro
```

Sem `--cta`, a captura apresenta o CTA atual e três alternativas de uma ação. `cta-select` substitui
o texto no roteiro e no bloco visual correspondente e gera um novo hash; também aceita uma opção
nova com `--text` e `--mode`. Em Shorts de 45 segundos ou mais, a captura consolida o plano em até
sete cenas por padrão (`--max-scenes` altera o teto), preservando integralmente a narração e as
fronteiras entre seções.

O SEO nasce no mesmo rascunho: título, descrição e três hashtags específicas são gerados
imediatamente e podem ser substituídos por `seo-set`. O pipeline sempre acrescenta `#biblia` e
`#jesus`, totalizando cinco hashtags. Esses metadados entram no hash aprovado e não são
reescritos durante a produção. Separadamente, o pipeline gera de dez a doze tags internas do
YouTube, sem `#`; elas não precisam aparecer na revisão do roteiro.

Depois da aprovação, a finalização não chama roteirista, revisor bíblico nem especialista de SEO.
Ela gera apenas as imagens pendentes, executa áudio, legendas, música, render, validação e upload
privado. A validação dessa lane é exclusivamente técnica: integridade dos arquivos, correspondência
literal entre roteiro, cenas e áudio, imagens, legendas, mixagem e vídeo final. O texto é conferido
somente pelo lock antes da síntese e novamente antes do upload:

```powershell
uv run pipeline-videos approved-scripts list --status approved
uv run pipeline-videos approved-scripts release --count 2 --authorize-external
```

Uma agenda privada pode consumir um ou dois roteiros por dia:

```powershell
uv run pipeline-videos approved-scripts schedule-create `
  --name "Curiosidades aprovadas" `
  --production-time 02:00 `
  --production-time 04:00 `
  --authorize-external
```

Quando a fila aprovada acaba, a agenda registra `last_depletion_reason`, não escolhe outro tema e
não cria job. Um novo roteiro só entra após nova captura e aprovação explícita pelo hash.

Consulte:

- [docs/optimized-production.md](docs/optimized-production.md)
- [docs/remote-production.md](docs/remote-production.md)
- [docs/creative-direction.md](docs/creative-direction.md)
- [docs/shorts-script-standard.md](docs/shorts-script-standard.md)
- [docs/long-form-script-standard.md](docs/long-form-script-standard.md)
- [docs/image-generation.md](docs/image-generation.md)

## Testes

```powershell
uv run ruff check src tests
uv run pytest -q
```
