# Pipeline de Vídeos Bíblicos

Aplicação local, headless e orientada por arquivos para planejar, produzir, revisar e retomar
vídeos bíblicos. O chat ajuda na criação; a pasta de cada vídeo é a fonte de verdade.

O modo único é `final_candidate_one_shot`: o Codex produz diretamente um candidato final V4.
Localmente e na VPS, cada vídeo usa um coordenador persistente e subagentes de roteiro, imagens e crescimento, todos em
`gpt-5.6-sol` e com o raciocínio próprio da função. Shorts editam a imagem ativa da abertura;
vídeos longos geram uma thumbnail horizontal nova, baseada somente no tema central.

O planejamento one-shot aparece em `run --dry-run` como cinco ondas. Depois do roteiro e das
cenas, os trabalhadores de imagem rodam enquanto o agente principal produz áudio, música e
legendas. Após o import das imagens, o especialista do YouTube recebe a abertura nos Shorts e o
tema central nos vídeos longos.
O principal continua sendo o único escritor do estado e realiza as importações serialmente antes do
ponto de junção do render.

O MVP já prova o fluxo vertical sem serviços pagos: briefing → roteirista especialista de Shorts →
revisão bíblica → cenas → imagens fixture → áudio simulado → música local → alinhamento por palavra
→ ASS/SRT/VTT → especialista YouTube → thumbnail final → MP4 final. O caminho de
produção usa a geração de imagens embutida do Codex por uma fila de importação e ElevenLabs por um
adaptador protegido pelo guard técnico `--allow-external`; Pedro já autorizou esse uso por padrão nas
produções que pedir neste pipeline.

Projetos reais novos usam o perfil `token_optimized_v1`. O único render usa a qualidade V4 com CRF
12, preset `slow` e supersampling 3. Trabalhadores recebem arquivos compactos em `images/jobs/`
sem herdar o histórico. O gerador cria uma única thumbnail: Shorts recebem texto e símbolos
incorporados; vídeos longos recebem uma imagem cinematográfica limpa, sem texto. Título e thumbnail
formam uma única combinação complementar em todos os modos novos.

O padrão editorial mantém toda a locução em português e exige citações bíblicas literais
verificadas. Projetos novos escolhem entre 16 modos criativos e usam
`Enfoque concreto | Passagem`, sem converter automaticamente a conclusão em aplicação
motivacional. Shorts mantêm hook inicial obrigatório; longos usam hook adaptativo. Ambos encerram
com CTA de uma única ação. Longos recebem também uma timeline bíblica iniciada por
`00:00 - Abertura`. Nas capas de
Shorts, a palavra de impacto e o elemento narrativo usam vermelho, com seta, X, sublinhado ou pincel
apontando para algo real da cena. As capas longas preservam uma área limpa no lado oposto ao
personagem.

Depois de roteiro e revisão, o caminho recomendado é:

```powershell
uv run pipeline-videos autopilot <projeto> --allow-external
# inicie os workers declarados em logs/autopilot-state.json
uv run pipeline-videos autopilot <projeto> --allow-external --wait-external
```

O `autopilot` executa áudio, música e legendas enquanto os workers produzem staging; depois aguarda,
importa cada lote uma única vez, renderiza e valida. O contrato completo está em
[Produção otimizada](docs/optimized-production.md).

Se uma cena ou thumbnail retornar `moderation_blocked`, o executor repete exatamente o mesmo prompt
uma vez. Um segundo bloqueio libera apenas uma alternativa de ação ou enquadramento que preserve a
identidade visual completa do personagem e do vídeo. Stagings válidos são reutilizados na retomada.

## Marco validado

O comando abaixo gera uma demonstração de 52,5 segundos baseada em Salmo 23:1-4, sem citação
literal longa e sem chamada paga:

```powershell
uv sync --all-groups
uv run pipeline-videos doctor
uv run pipeline-videos demo
```

Artefatos principais:

- `projects/videos-longos/14-07-2026_salmo-23/render/final.mp4`: H.264, 1280×720, 30 fps;
- `projects/videos-longos/14-07-2026_salmo-23/captions/`: legendas ASS, SRT e VTT;
- `projects/videos-longos/14-07-2026_salmo-23/youtube/`: metadados e thumbnail fixture;
- `projects/videos-longos/14-07-2026_salmo-23/validation/`: contratos e mídia.

As imagens, o áudio e a thumbnail da demonstração são marcados como `FIXTURE`: validam o
pipeline, mas não são ativos publicáveis. Uma segunda execução reutiliza hashes e artefatos; o MP4
não é refeito quando nada mudou.

## Requisitos

- Windows, Linux ou macOS;
- [uv](https://docs.astral.sh/uv/) para Python 3.12 e dependências;
- FFmpeg e FFprobe no `PATH`, com `libx264`, AAC e `libass`;
- opcional: chave e voice ID da ElevenLabs para narração real;
- opcional: cliente OAuth Desktop do Google para upload/publicação no YouTube;
- uma conversa no Codex com geração de imagens disponível para ativos visuais reais.

Neste computador o projeto foi validado com Python 3.12.10, uv 0.11.21 e FFmpeg 8.1.1.

## Começar o primeiro vídeo real

Execute exatamente:

```powershell
uv run pipeline-videos new salmo-121 --title "De onde vem o meu socorro?" --duration 540
uv run pipeline-videos resume-prompt salmo-121
```

Para um Short vertical 9:16, o fluxo recomendado agora não exige tema, título nem passagem:

```powershell
uv run pipeline-videos new-short
```

Para usar uma locução anexada sem ElevenLabs:

```powershell
# O Codex entrega o roteiro e aguarda um único arquivo de voz anexado pelo usuário
uv run pipeline-videos new-short-audio-manual
```

`new-short-audio-manual` mantém o roteiro completo, imagens e especialista de crescimento, mas troca
somente a origem da voz. Quando as cenas estiverem prontas, o pipeline expõe
`script/narration.txt` e pausa a etapa de áudio. Envie esse texto integralmente ao gerador de voz,
devolva um único arquivo sem música ou efeitos e importe-o:

```powershell
uv run pipeline-videos audio prepare-manual <projeto>
uv run pipeline-videos audio import-manual <projeto> .\narracao.mp3
uv run pipeline-videos autopilot <projeto> --wait-external
```

WAV, MP3, M4A, AAC, FLAC, OGG, Opus e WebM são aceitos e normalizados para WAV PCM mono 16-bit/48
kHz. O texto narrado precisa coincidir integralmente com `script/narration.txt`. O alinhamento usa
WhisperX 3.8.6 e o modelo fonético português em processo CPU isolado; não usa chave, API, crédito ou
`--allow-external`. A primeira importação baixa dependências e o modelo para o cache local; depois
eles são reutilizados. Consulte [Áudio manual local](docs/local-manual-audio.md).

### Produção remota sob demanda

Na VPS, o mesmo pipeline local roda com um coordenador persistente por job. O executor inicia
`codex exec` uma vez e usa `codex exec resume`; o coordenador delega roteiro, até dois lotes de
imagens e crescimento aos mesmos especialistas locais. Todos usam `gpt-5.6-sol`, com raciocínio
alto, baixo e médio conforme a função.

Cada checkpoint recebe um JSON compacto. Áudio, música, legendas, imports, render, validação e upload
rodam diretamente no Python. O status soma o uso por checkpoint e registra também o identificador
da sessão persistente.

Pedidos ficam em uma fila persistente e podem começar imediatamente ou somente entre 01:00 e 05:00
no fuso `America/Sao_Paulo`. Todo upload automatizado começa como privado. `private_review` termina
nesse ponto; `scheduled` só agenda quando o pedido original contém `publish_at` explícito com fuso.
O render candidato e o upload privado não registram aprovação humana.

```powershell
# A skill local gera este JSON/Base64 e o envia por SSH; o comando abaixo é a interface da VPS.
uv run pipeline-videos remote enqueue --payload-base64 "<PAYLOAD_BASE64>"
uv run pipeline-videos remote list
uv run pipeline-videos remote status <JOB_ID>
uv run pipeline-videos remote cancel <JOB_ID>
uv run pipeline-videos remote retry <JOB_ID>

# Agenda diária idempotente de um ou dois Shorts, sempre em sessões separadas.
uv run pipeline-videos remote schedule-create --name madrugada `
  --production-time 01:00 --production-time 03:00 `
  --publication-time 12:00 --publication-time 19:00 `
  --publish-days-after 1 --authorize-external --authorize-public-schedule
uv run pipeline-videos remote schedule-list
```

Instalação, estados da fila, segurança dos segredos e serviço systemd estão em
[Produção remota](docs/remote-production.md).

Esse comando consulta o catálogo bíblico local em domínio público, combina o histórico das pastas
e o banco curado de expansão BPM com o registro Git compartilhado, escolhe um versículo-base com
variedade de livro e tema,
e preenche automaticamente `briefing.json` e `sources.json`. A varredura inclui passagem principal,
referências complementares, fontes e bases bíblicas do roteiro. `projects/verse-usage.json` continua
como cache legível; a autoridade de coordenação é `content-registry.json` no repositório Git bare
descrito em [Produção remota](docs/remote-production.md). A reserva é commitada antes da criação.

Passagens principais são controladas por formato: um uso principal em vídeo longo não esgota o
estoque dos Shorts, e citações complementares de longos não bloqueiam nenhum futuro tema principal.
O banco de expansão é carregado automaticamente antes da seleção. Quando todas as passagens
principais de um formato completam um ciclo, o seletor inicia outro pela passagem menos utilizada,
sem reutilizar referências ainda reservadas por jobs ativos. Consulte a capacidade sem reservar:

```powershell
uv run pipeline-videos catalog status --format short
uv run pipeline-videos catalog status --format long
```

O projeto já nasce com `creative_retention_v3`: hook de até dois segundos, citação literal entre 8
e 20 segundos, desenvolvimento, reengajamento, resolução e CTA de uma única ação. O seletor
considera potencial narrativo, passagem e histórico recente para variar modo criativo e família de
hook. O briefing recebe postura interpretativa, resultado para o espectador, progressão visual,
possível revelação e semente de título. O tom segue o modo — factual, expositivo, analítico,
exploratório ou devocional — enquanto a revisão bíblica e o padrão visual
`cinematic-family-3d-v1` permanecem obrigatórios. A aprovação humana do briefing continua pendente.

O comando manual continua disponível quando você quiser definir explicitamente o tema:

```powershell
uv run pipeline-videos new davi-e-golias --title "Davi e Golias em 1 minuto" --duration 60 --format short
```

Projetos novos são organizados automaticamente em `projects/videos-longos/` ou `projects/shorts/`.
O nome físico recebe a data local e o tema: `DD-MM-AAAA_tema-do-video`. O argumento curto continua
funcionando como alias, portanto `status salmo-121` encontra a pasta datada correspondente.

O segundo comando imprime uma mensagem pronta para colar na conversa com o Codex. Ela pede que o
agente leia o estado persistido e conduza a conversa sobre tema, passagem, versículos, modo
criativo, postura, política de hook, público, duração e limites sem gerar ativos prematuramente.

Para retomar dias depois:

```powershell
uv run pipeline-videos status salmo-121
uv run pipeline-videos resume-prompt salmo-121
```

## Fluxo de produção

### 1. Briefing, fontes e decisões

Edite, diretamente ou com o Codex:

- `editorial/briefing.json`;
- `editorial/sources.json`;
- `editorial/decisions.jsonl`.

Registre tradução, fonte e direitos antes de inserir citações literais. Depois:

```powershell
uv run pipeline-videos approve salmo-121 briefing --by "Pedro"
```

### 2. Roteiro e revisão bíblica

Em Shorts reais, use primeiro a etapa especializada. O subagente lê somente a fila e grava um pacote
no staging; o processo principal valida referências, contagem de palavras, gancho, beats, resolução,
modo criativo, resolução, CTA e revisão bíblica antes de promover a nova versão:

```powershell
uv run pipeline-videos script shorts-prepare davi-e-golias
# O subagente escreve apenas em script/staging/shorts-script-specialist/package.json
uv run pipeline-videos script shorts-check-staging davi-e-golias
uv run pipeline-videos script shorts-import davi-e-golias
```

O `shorts-check-staging` é somente leitura e devolve, de uma vez, todos os erros detectáveis do
pacote. O especialista deve corrigir o lote completo antes do único import. O import também
sincroniza deterministicamente os campos duplicados sem reescrever o staging: `open_loop` nos
formatos legados e promessa, pergunta opcional, resolução, valor final e CTA em
`creative_retention_v3`.

O roteiro anterior é arquivado em `script/versions/`. O mapa de retenção fica em
`script/retention-strategy.json`. Vídeos longos continuam aceitando importação direta, mas projetos
novos exigem `ScriptDocument.long_form_strategy` com o contrato de retenção e a cadência bíblica.
Toda importação também deriva `script/creative-contract.json` sem chamada externa: modo, promessa,
primeira fala, resolução, função de cada seção, componentes críticos e perfil de ritmo passam a ser reutilizados por
cenas, crescimento e validação, em vez de serem decididos novamente.

Novos Shorts seguem `creative_retention_v3`: selecionam uma direção editorial, abrem com hook
obrigatório em até dois segundos, inserem a citação verificada entre 8 e 20 segundos e fazem o valor
crescer até um reengajamento entre 40% e 60% da duração. A resolução pode ser factual,
explicativa, analítica, filosófica ou devocional. Aplicação e callback só entram quando servem ao
modo. O encerramento pede somente comentar, inscrever-se ou compartilhar, em até quatro segundos.
Contratos anteriores continuam válidos nos projetos que já os armazenam.
O contrato completo está em
[docs/shorts-script-standard.md](docs/shorts-script-standard.md).

Vídeos longos novos seguem `creative_retention_long_v2`: o hook pode ser obrigatório, opcional ou
dispensado conforme tema e modo. A abertura sempre entrega valor cedo; os movimentos acrescentam
informação nova e os reengajamentos acompanham a duração. O alvo bíblico permanece entre 0,8 e 1,2
citação literal por minuto, com primeira citação até 75 segundos, intervalo de 25 a 105 segundos
entre âncoras e uma citação próxima da parte final. Pergunta, aplicação e callback são opcionais;
a resolução segue o modo e exatamente uma ação de CTA encerra o vídeo.
O contrato completo está em
[docs/long-form-script-standard.md](docs/long-form-script-standard.md).
O catálogo comum está em [docs/creative-direction.md](docs/creative-direction.md).

Nas locuções, referências são sempre verbalizadas com `capítulo` e `versículo`: “Josué, capítulo
1, versículo 9”. O formato compacto `Josué 1:9` permanece apenas nos campos técnicos e metadados.
O roteiro não fala “Até aqui, o texto bíblico” nem outro rótulo de transição. As aspas, o itálico,
as seções estruturadas e a classificação das afirmações separam a citação do comentário. As legendas
não exibem rótulos de contexto: somente as palavras copiadas literalmente da fonte bíblica verificada
aparecem em itálico; referência, paráfrase e reflexão permanecem sem itálico. A apresentação do
narrador fica fora das aspas; somente o versículo literal é delimitado: `Em Josué, capítulo 1,
versículo 9, lemos: “texto do versículo.”`

Projetos longos novos exigem o JSON estruturado com `long_form_strategy`. Texto com parágrafos
continua disponível para projetos legados:

```powershell
uv run pipeline-videos import-script salmo-121 .\meu-roteiro.json
uv run pipeline-videos import-review salmo-121 .\minha-revisao.json
uv run pipeline-videos approve salmo-121 biblical_review --by "Pedro"
uv run pipeline-videos approve salmo-121 script --by "Pedro"
```

A aprovação do roteiro é bloqueada enquanto a revisão bíblica tiver apontamento `blocking` aberto.
Cada afirmação pode ser classificada como texto bíblico, contexto verificado, interpretação ou
elaboração criativa.

### 3. Cenas e prompts modulares

```powershell
uv run pipeline-videos build-scenes salmo-121
# Override compatível para um ritmo uniforme explícito:
uv run pipeline-videos build-scenes salmo-121 --target-seconds 8
```

Sem override, Shorts preservam o corte rápido existente e longos novos usam o perfil
`long_creative_v2`: abertura em planos curtos, reengajamentos em aproximadamente sete segundos,
progressão regular em dez e planos mais longos somente em reflexão contemplativa com função
declarada. Projetos longos anteriores preservam `balanced_long_v1`.

O prompt de cada cena guarda separadamente perfil de estilo, paleta, continuidade, descrição,
enquadramento, emoção e restrições negativas. Trocar a paleta não altera o estilo-base:

```powershell
uv run pipeline-videos set-palette salmo-121 amanhecer-quente
uv run pipeline-videos set-style salmo-121 cinematic-family-3d-animation
```

### 4. Imagens pelo Codex

O processo local não possui um endpoint para invocar a ferramenta embutida da assinatura. Por isso
ele cria uma fila retomável, sem trocar silenciosamente para uma API paga:

```powershell
uv run pipeline-videos images prepare salmo-121 --provider codex
uv run pipeline-videos images queue salmo-121
```

Para cada item de `images/codex-queue.json`, o Codex faz uma chamada por tentativa e copia o PNG
bem-sucedido para o `staging_path`. Em bloqueio de moderação, repete o mesmo prompt uma vez e só então
pode usar uma alternativa visual que preserve integralmente a identidade. O `worker_id` é a afinidade
inicial; depois, o planejador recomenda o próximo job ordenado ao primeiro trabalhador livre. Os
trabalhadores não compartilham um `staging_path` nem alteram manifestos. Quando o lote terminar, o
agente principal faz uma única importação:

```powershell
uv run pipeline-videos images import-batch salmo-121
```

As imagens importadas ficam como versões ativas para o rascunho, mas não são aprovadas. `--force`
cria uma nova candidata sem sobrescrever a versão aprovada. Para um teste local sem geração externa:

```powershell
uv run pipeline-videos images prepare salmo-121 --provider fixture
```

### 5. Narração

Copie `.env.example` para `.env` e defina os valores localmente. Nunca cole chaves no repositório.

```powershell
uv run pipeline-videos audio configure salmo-121 `
  --provider elevenlabs `
  --voice-variant jesus_1 `
  --model eleven_multilingual_v2

uv run pipeline-videos audio generate salmo-121 --allow-external
```

`jesus_1` é o preset padrão. Use `--voice-variant jesus_2` para a segunda variação; `--voice-id`
continua disponível apenas para uma voz personalizada fora desses presets.

`--allow-external` é obrigatório para a ElevenLabs porque a chamada consome créditos. O padrão de
saída é `mp3_44100_128`, convertido localmente para WAV 48 kHz. O áudio é gerado por cena; hashes
evitam repetir chamadas e `--scene scene-003 --force` regenera somente um trecho.

Provedor gratuito de teste:

```powershell
uv run pipeline-videos audio generate salmo-121 --provider mock
```

`mock` é portátil, determinístico e não contém fala humana. Ele permanece disponível somente para
demonstrações e testes; o caminho de produção usa ElevenLabs.

Para uma voz real gerada fora do pipeline e alinhada inteiramente no computador:

```powershell
uv run pipeline-videos audio configure salmo-121 --provider local_manual
uv run pipeline-videos audio prepare-manual salmo-121
uv run pipeline-videos audio import-manual salmo-121 .\narracao.wav
```

Esse caminho não chama a ElevenLabs. O importador converte o arquivo, alinha o roteiro conhecido,
reconstrói os segmentos de cena e mantém o contrato das legendas. Não há fallback proporcional: se
o áudio omitir ou reformular palavras, a importação falha e pede uma nova narração.

### 6. Música de fundo

Adicione suas faixas diretamente em `music-library/` na raiz deste repositório. São aceitos MP3,
WAV, M4A, AAC, FLAC e OGG. Para conferir a pasta e listar as faixas disponíveis:

```powershell
uv run pipeline-videos music library
```

O fluxo completo seleciona automaticamente uma faixa aleatória. Também é possível executar somente
essa etapa:

```powershell
uv run pipeline-videos music select salmo-121
# Sorteia novamente, preservando o arquivo anteriormente copiado:
uv run pipeline-videos music select salmo-121 --force
```

A escolha é copiada para `audio/music/` dentro do projeto e registrada no manifesto da etapa. Ela é
reutilizada nas execuções seguintes, mesmo que a biblioteca mude. O render usa -12 dB, com fades de
entrada e saída de 2 e 3 segundos, preservando a narração como faixa principal.

### 7. Especialista de título, descrição, SEO e thumbnail

Projetos reais usam um subagente `youtube_growth_specialist`. Nos projetos novos, a fila é preparada
depois da importação das imagens. Shorts editam diretamente a imagem ativa da abertura; longos usam
o tema central do briefing no template `biblical_cinematic_landscape_v1`, sem imagem de referência:

```powershell
uv run pipeline-videos metadata growth-prepare salmo-121
# O subagente escreve apenas em youtube/staging/youtube-growth-specialist/
uv run pipeline-videos metadata growth-import salmo-121
```

O especialista entrega exatamente uma combinação de título e thumbnail, uma
descrição única, palavras-chave, intenção de busca, hipótese de CTR e justificativas registradas em
`youtube/seo-strategy.json`. O agente principal valida e importa o pacote; o subagente não altera
`project.json`, aprovações ou ativos em uso.

Na VPS, a mesma combinação é processada pelo `youtube_growth_specialist`, com contexto compacto e
raciocínio médio. O contrato visual e a complementaridade entre título e capa permanecem iguais.

Nos projetos novos, o título usa `Enfoque concreto | Passagem`. O enfoque pode ser fato,
personagem, curiosidade, pergunta, comparação, dilema, tese ou promessa prática. A descrição abre
com resumo curto e, em vídeos longos, recebe uma timeline das citações no ponto em que entram na
narrativa.

O par editorial distribui funções entre os dois elementos. O título concentra referência, enfoque e
intenção de busca. Em Shorts, a frase curta da capa acrescenta conflito, decisão, detalhe,
contraste ou consequência. Em longos, a imagem comunica personagem, gesto, paisagem, objeto ou
conflito sem qualquer texto,
letra, logotipo ou marcação editorial.

Nos Shorts, o título principal precisa conter a expressão de busca primária, pelo menos uma opção
de título precisa ancorar a passagem bíblica e a descrição fica curta, sem capítulos. O especialista
recebe um contexto compacto em `youtube/growth-context.json`, em vez de reler todos os manifestos e
prompts completos. Esse contexto reutiliza o contrato criativo, omite a locução integral e os prompts
montados, e inclui no máximo seis aprendizados observados de `analytics/experiments.json`, priorizando
o mesmo formato. O importador rejeita promessa ou gancho que tenham sido reinterpretados.

Métricas observadas podem ser registradas de forma idempotente sem executar nova geração:

```powershell
uv run pipeline-videos analytics import salmo-121 .\experimento.json
```

Percentuais conhecidos são validados entre 0 e 100; conclusões continuam sendo hipóteses, sem inferir
causalidade a partir de poucos dados.

O objetivo é aumentar relevância, clareza e probabilidade de clique sem clickbait enganoso. Não há
garantia de viralização: o próprio YouTube considera relevância, satisfação, watch time, histórico do
público, concorrência e sazonalidade. Em Shorts novos, o especialista faz uma chamada de imagegen
para editar a imagem ativa da abertura e entrega o bitmap final com texto, destaque vermelho e
elemento narrativo incorporados. Em vídeos longos novos, faz uma geração horizontal 16:9 sem
referência visual, usando integralmente o prompt temático versionado e proibindo texto, letras,
logotipos e marcações. O importador apenas normaliza dimensões e formato, sem aplicar texto com
Pillow. A primeira saída válida é mantida; nova tentativa automática ocorre somente quando
`moderation_blocked` não produz imagem. Projetos legados preservam o contrato armazenado. Em Shorts,
a capa ocupa exatamente o frame zero.

Nos Shorts, a tipografia integrada segue `integrated_extruded_3d_v1`: as letras são modeladas como
objetos 3D da própria cena, com extrusão espessa, bordas chanfradas, textura material, highlights, sombras de
contato/oclusão e perspectiva coerentes com a câmera e a iluminação. A palavra dominante recebe
escala muito maior, enquanto a palavra vermelha e a marcação narrativa mantêm o mesmo volume e
profundidade. O validador rejeita prompts sem esses marcadores para evitar texto plano, adesivo ou
overlay genérico nas próximas thumbnails.

As regras seguem as orientações oficiais de [títulos e thumbnails](https://support.google.com/youtube/answer/12340300),
[descrições](https://support.google.com/youtube/answer/12948449),
[busca](https://support.google.com/youtube/answer/16090438) e
[A/B tests](https://support.google.com/youtube/answer/16391400).

### 8. Legendas, metadados e render

```powershell
uv run pipeline-videos captions build salmo-121
uv run pipeline-videos metadata growth-prepare salmo-121
uv run pipeline-videos metadata growth-import salmo-121
# Gera diretamente o candidato final V4 usando as versões ativas:
uv run pipeline-videos render salmo-121

# Depois da revisão do usuário, registre as aprovações sem renderizar novamente:
uv run pipeline-videos approve salmo-121 images --by "Pedro"
uv run pipeline-videos approve salmo-121 metadata --by "Pedro"
uv run pipeline-videos validate salmo-121
uv run pipeline-videos approve salmo-121 final_render --by "Pedro"
```

O candidato fica em `render/final.mp4`. A renderização usa os ativos ativos, passa pela validação
técnica e não registra aprovação humana automaticamente.

O padrão final aprovado continua sendo o da V4 de Davi e Golias: resolução completa, H.264 com CRF
12, preset `slow` e supersampling 3; movimento pendular suave entre 1,02× e 1,105×; transições
`cinematic_mix`; e legendas Montserrat ExtraBold em caixa alta, com no máximo três palavras por
linha e card vermelho sólido arredondado na palavra ativa. O caminho antigo de prévia rápida foi
removido; projetos existentes têm seus campos antigos ignorados ao serem carregados. Em Shorts, o bloco de legenda fica no
centro da tela; os prompts visuais reservam essa faixa para preservar a leitura. Um marcador menor
Somente citações bíblicas literais usam itálico. A fonte e sua licença OFL são copiadas
automaticamente para cada projeto novo.

As imagens seguem separadamente o padrão `cinematic-family-3d-v1`: animação 3D cinematográfica
familiar premium, no espírito do apelo emocional de longas Pixar/Disney, sem copiar personagens ou
elementos de filmes existentes. Personagens expressivos, formas arredondadas, materiais estilizados,
profundidade em camadas e iluminação de longa-metragem formam a identidade visual. A fila protege
esse padrão contra deriva para fotorrealismo, live action, vetor flat, anime, low-poly, aparência de
boneco de plástico ou render 3D genérico.

A música de fundo é escolhida pela etapa `music`; nenhuma faixa específica é fixa no padrão global.
SRT e VTT continuam usando blocos naturais sem marcação visual. Alterar somente o estilo da legenda
reutiliza a base visual em cache.

### 9. Upload e publicação no YouTube

A integração com a YouTube Data API é opt-in e não faz parte de `run`. O primeiro comando apenas
autoriza o canal e guarda o token fora dos projetos:

```powershell
uv run pipeline-videos youtube auth `
  --client-secrets "C:\caminho-seguro\client_secrets.json" `
  --allow-external
```

Depois de aprovar metadados e render final, revise localmente o pacote e faça o upload inicial como
privado ou não listado:

```powershell
uv run pipeline-videos youtube preflight salmo-121
uv run pipeline-videos youtube upload salmo-121 `
  --confirm-upload "DD-MM-AAAA_salmo-121" `
  --allow-external
```

O upload inclui título, descrição/capítulos, tags e legenda SRT. Vídeos longos usam por padrão a
categoria Educação (`27`); Shorts permanecem em Pessoas e blogs (`22`). Em vídeos longos, o upload
inclui também a thumbnail. Nos Shorts, a capa é incorporada ao frame zero e o quadro precisa ser selecionado
manualmente no aplicativo móvel do YouTube. O estado fica em `youtube/publication.json`; tentativas
repetidas retomam o mesmo `video_id` em vez de duplicar o vídeo. Publicar ou agendar é uma segunda
ação, com confirmação pelo ID remoto:

```powershell
# Publicar agora
uv run pipeline-videos youtube publish salmo-121 `
  --confirm-publication "VIDEO_ID" `
  --allow-external

# Ou agendar com fuso explícito
uv run pipeline-videos youtube publish salmo-121 `
  --confirm-publication "VIDEO_ID" `
  --publish-at "2026-07-20T19:00:00-03:00" `
  --allow-external
```

Configuração do Google Cloud, escopos, guards, classificações e limitações estão em
[Publicação no YouTube](docs/youtube-publishing.md).

O render das cenas usa no máximo dois processos FFmpeg em paralelo, preservando a ordem da timeline
e o cache individual. Bases visuais equivalentes de rascunho/final são compartilhadas por cópia
atômica. Quando a configuração da prévia é idêntica à do final, o arquivo é copiado sem uma segunda
recodificação completa.

Projetos novos usam blur uniforme com sigma 8 em todo o fundo. Nos Shorts, a thumbnail sobreposta
no frame zero permanece totalmente nítida; a partir do primeiro quadro narrativo, o mesmo blur é
aplicado a todas as cenas. As legendas `active_card_v2` entram progressivamente, palavra por palavra,
com animação curta de posição, escala e opacidade, sem alterar tipografia, card ou sincronização.

Nos Shorts novos, o gerador cria uma capa final e ela ocupa o frame zero. Em versículos longos, o
construtor
cria mudanças visuais aproximadamente a cada 5,5 segundos, preferindo pontuação e limites de oração;
isso pode aumentar o número de imagens para dar mais ritmo à abertura. As filas continuam usando
schemas compactados e arquivos de contexto dedicados, e os validadores locais completos permanecem
como autoridade na importação.

Para conferir tudo sem alterar arquivos nem consumir créditos:

```powershell
uv run pipeline-videos run salmo-121 --dry-run
```

## Estrutura de cada vídeo

```text
projects/
├── videos-longos/DD-MM-AAAA_tema-do-video/
└── shorts/DD-MM-AAAA_tema-do-video/

<projeto>/
├── project.json                    # estado, hashes, config e aprovações
├── editorial/
│   ├── briefing.json
│   ├── sources.json
│   └── decisions.jsonl
├── script/
│   ├── script.json
│   ├── creative-contract.json
│   ├── shorts-context.json
│   ├── narration.txt
│   └── versions/
├── reviews/biblical-consistency.json
├── visual/
│   ├── style-profile.json
│   ├── palette.json
│   └── continuity.json
├── scenes/manifest.json
├── images/
│   ├── manifest.json
│   ├── codex-queue.json
│   └── assets/<scene>/vNNN.png
├── audio/
│   ├── manifest.json
│   ├── narration.wav
│   ├── segments/<scene>-vNNN.wav
│   └── music/
│       ├── manifest.json
│       └── <faixa-selecionada>
├── captions/
│   ├── manifest.json
│   ├── captions.ass
│   ├── captions.srt
│   └── captions.vtt
├── render/
│   ├── config.json
│   └── final.mp4
├── youtube/
│   ├── metadata.json
│   ├── description.md
│   ├── growth-queue.json
│   ├── growth-context.json
│   ├── growth-package.json
│   ├── seo-strategy.json
│   ├── staging/youtube-growth-specialist/
│   ├── thumbnail-prompt.json
│   ├── thumbnail-vNNN.png
│   ├── thumbnail.png
│   └── publication.json
├── analytics/experiments.json
├── validation/report.json
└── logs/events.jsonl
```

Todos os caminhos armazenados são relativos à pasta do vídeo. Isso torna cada projeto portátil.

## ElevenLabs: decisões atuais

O adaptador segue o endpoint oficial
[`POST /v1/text-to-speech/{voice_id}/with-timestamps`](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps),
prefere `normalized_alignment`, preserva headers de custo/diagnóstico e repete com backoff apenas
erros transitórios. O padrão atual é `eleven_multilingual_v2` para Shorts e vídeos longos.

Os planos, limites e licenças mudam. Em 13/07/2026, a
[página oficial de preços](https://elevenlabs.io/pricing/api?price.platform=api) informava 10.000
créditos no plano Free e restrições de uso comercial. Confirme os termos atuais antes de publicar
ou monetizar; o pipeline não habilita pay-as-you-go nem faturamento.

Mais detalhes em [docs/elevenlabs.md](docs/elevenlabs.md).

## Confiabilidade e segurança

- cada etapa calcula hash de entrada e valida os artefatos antes de reutilizar;
- `--force` é explícito e recursos aprovados permanecem versionados;
- erros ficam em `project.json` e `logs/events.jsonl`;
- upload e publicação só existem em comandos separados, com `--allow-external` e confirmações
  literais; um pedido explícito de upload privado pode usar `render-review` e
  `--automated-review` sem aprovação humana, sem publicação implícita, compra ou cobrança;
- chaves vêm apenas de `.env`/ambiente e nunca são gravadas nos manifestos;
- a validação compara contrato criativo, roteiro, cenas, imagens, áudio e legendas para detectar
  componentes críticos ausentes, omissão, duplicação ou reordenação;
- a publicação futura começa como privada; o fluxo tradicional exige aprovação humana, enquanto o
  candidato privado automatizado exige pedido explícito, política habilitada e validação técnica.

## Testes e qualidade

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pipeline-videos demo
uv run pipeline-videos validate demo-salmo-23
```

Os testes cobrem alinhamento de caracteres, mock, guard da ElevenLabs, seleção e persistência
da música local, versionamento de imagens, agrupamento e cobertura de legendas, planejamento das
cinco ondas concorrentes, importação de thumbnails integradas, migração de manifestos antigos,
cache paralelo do render e
FFprobe real.

## Arquitetura e próximos marcos

- [Arquitetura e contratos](docs/architecture.md)
- [Integração de imagens no Codex](docs/image-generation.md)
- [ElevenLabs](docs/elevenlabs.md)
- [Roadmap incremental](docs/roadmap.md)

OAuth, upload, thumbnail, legenda e agendamento do YouTube ficam isolados em `publication.py` e não
são chamados pelo pipeline de produção. Métricas e aprendizado por desempenho permanecem no próximo
marco.
