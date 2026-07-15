# Pipeline de Vídeos Bíblicos

Aplicação local, headless e orientada por arquivos para planejar, produzir, revisar e retomar
vídeos bíblicos. O chat ajuda na criação; a pasta de cada vídeo é a fonte de verdade.

O modo padrão é `draft_one_shot`: o Codex produz uma prévia completa e usa até três subagentes em
paralelo. Quando há pacote do YouTube, duas vagas ficam com imagens e uma com o especialista de
crescimento. O modo `guided` continua disponível
quando forem desejadas aprovações por etapa.

O planejamento one-shot aparece em `run --dry-run` como cinco ondas. Depois do roteiro e das
cenas, dois trabalhadores de imagem e o especialista do YouTube ocupam as três vagas externas,
enquanto o agente principal produz áudio, música e legendas. O principal continua sendo o único
escritor do estado e realiza as importações serialmente antes do ponto de junção do render.

O MVP já prova o fluxo vertical sem serviços pagos: briefing → roteirista especialista de Shorts →
revisão bíblica → cenas → imagens fixture → áudio simulado → música local → alinhamento por palavra
→ ASS/SRT/VTT → especialista YouTube → thumbnail integrada → MP4 final e prévia. O caminho de
produção usa a geração de imagens embutida do Codex por uma fila de importação e ElevenLabs por um
adaptador protegido por confirmação explícita.

Projetos reais novos usam o perfil `token_optimized_v1`. A prévia permanece em resolução completa,
mas usa CRF 16, preset `fast` e supersampling 2; o render final aprovado preserva a qualidade V4 com
CRF 12, preset `slow` e supersampling 3. Trabalhadores recebem arquivos compactos em `images/jobs/`
sem herdar o histórico, e o gerador cria três capas finais com texto e símbolos incorporados. O
modo aditivo `remote_review` usa um único agente e uma única combinação título+thumbnail.

O padrão editorial de projetos novos mantém toda a locução em português, exige citações bíblicas
literais verificadas distribuídas na narrativa (de 0,8 a 1,2 por minuto), centraliza as legendas e
isola entre aspas a resposta pedida nos comentários, seguida por um segundo de silêncio. Títulos
seguem `Passagem principal | Tema principal`; descrições longas recebem uma timeline bíblica iniciada
por `00:00 - Abertura`. Nas capas, a palavra de impacto e o elemento narrativo usam vermelho, com
seta, X, sublinhado ou pincel apontando para algo real da cena.

Depois de roteiro e revisão, o caminho recomendado é:

```powershell
uv run pipeline-videos autopilot <projeto> --allow-external
# inicie os workers declarados em logs/autopilot-state.json
uv run pipeline-videos autopilot <projeto> --allow-external --wait-external
```

O `autopilot` executa áudio, música e legendas enquanto os workers produzem staging; depois aguarda,
importa cada lote uma única vez, compõe capas, renderiza e valida. Projetos existentes permanecem no
perfil legado até uma migração explícita. O contrato completo está em
[Produção otimizada](docs/optimized-production.md).

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
- `projects/videos-longos/14-07-2026_salmo-23/render/preview.mp4`: prévia 640×360;
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

Há também duas versões rápidas e aditivas; o fluxo longo atual não é substituído:

```powershell
# Gera roteiro e prompts; depois aguarda as imagens anexadas pelo usuário
uv run pipeline-videos new-short-light-manual

# O próprio Codex gera as imagens e conclui a prévia
uv run pipeline-videos new-short-light-automatica

# O Codex entrega o roteiro e aguarda um único arquivo de voz anexado pelo usuário
uv run pipeline-videos new-short-audio-manual
```

Ambas escolhem um versículo ainda não usado, mantêm a revisão bíblica e produzem o roteiro Light no
agente principal com raciocínio baixo. O modo manual cria `images/manual-prompts.md` com a quantidade
de cenas necessária, dividida em partes de até 10 imagens separadas. Cada item repete o padrão 3D
completo para que o ChatGPT Web preserve estilo, paleta, personagens e continuidade. Depois de
anexar os arquivos na ordem indicada, a importação é feita de uma vez:

```powershell
uv run pipeline-videos images import-manual <projeto> imagem-01.png imagem-02.png imagem-03.png
uv run pipeline-videos run <projeto>
```

No modo automático, `images/codex-queue.json` distribui uma imagem por job para até dois trabalhadores
quando o especialista de crescimento está ativo. Os modos Light eliminam somente o subagente de
roteiro: mantêm um `youtube_growth_specialist` com `reasoning_effort=low`, usam render local mais
rápido, narração ElevenLabs e usam a primeira thumbnail integrada no frame zero. A chamada de voz
exige `--allow-external`, inclusive no modo Light.
O render final V4 permanece inalterado; somente a prévia de projetos novos usa o perfil rápido.

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

Na VPS, `remote_review` mantém a qualidade final e reduz o uso do Codex: o agente principal produz
os pacotes compactos, processa as imagens sequencialmente e gera exatamente um título e uma
thumbnail integrada. Em Shorts, essa arte ocupa o primeiro frame; em vídeos longos, ela é enviada
como thumbnail personalizada.

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
```

Instalação, estados da fila, segurança dos segredos e serviço systemd estão em
[Produção remota](docs/remote-production.md).

Esse comando consulta o catálogo bíblico local em domínio público, varre todas as referências já
usadas ou reservadas nos projetos, escolhe um versículo-base inédito com variedade de livro e tema,
e preenche automaticamente `briefing.json` e `sources.json`. A varredura inclui passagem principal,
referências complementares, fontes e bases bíblicas do roteiro. A reserva fica registrada em
`projects/verse-usage.json` e é protegida contra duas criações simultâneas.

O projeto já nasce com o padrão `human_hook_to_bible`: gancho humano curto, entrada imediata na
Bíblia, citação literal verificada, contexto ou conflito bíblico, verdade central, aplicação e
encerramento. A voz é pastoral, simples e cotidiana. O seletor favorece versículos conhecidos,
curtos e claros sem abrir mão de ineditismo, variedade e contexto. Há cinco opções seguras de
convite para comentar, CTA final com curtir, inscrição e compartilhamento, revisão bíblica
obrigatória e o padrão visual de animação `cinematic-family-3d-v1`. O briefing é concluído pelo seletor
automático, mas sua aprovação humana continua pendente. Para escolher a duração, use
`new-short --duration 60`.

O comando manual continua disponível quando você quiser definir explicitamente o tema:

```powershell
uv run pipeline-videos new davi-e-golias --title "Davi e Golias em 1 minuto" --duration 60 --format short
```

Projetos novos são organizados automaticamente em `projects/videos-longos/` ou `projects/shorts/`.
O nome físico recebe a data local e o tema: `DD-MM-AAAA_tema-do-video`. O argumento curto continua
funcionando como alias, portanto `status salmo-121` encontra a pasta datada correspondente.

O segundo comando imprime uma mensagem pronta para colar na conversa com o Codex. Ela pede que o
agente leia o estado persistido e conduza a conversa sobre tema, passagem, versículos, mensagem,
público, duração, tom e limites de interpretação sem gerar ativos prematuramente.

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
no staging; o processo principal valida referências, contagem de palavras, gancho, beats, payoff,
CTA e revisão bíblica antes de promover a nova versão:

```powershell
uv run pipeline-videos script shorts-prepare davi-e-golias
# O subagente escreve apenas em script/staging/shorts-script-specialist/package.json
uv run pipeline-videos script shorts-import davi-e-golias
```

O roteiro anterior é arquivado em `script/versions/`. O mapa de retenção fica em
`script/retention-strategy.json`. Vídeos longos e fluxos manuais continuam aceitando importação
direta.

Novos Shorts seguem o padrão editorial `human_hook_to_bible`: começam com uma dor, pergunta ou
contraste reconhecível em até 2,5 segundos; entram imediatamente no contexto bíblico e citam ao menos
um versículo literal verificado, preferencialmente conhecido, curto e claro; depois entregam
contexto ou conflito, verdade central, resposta prática e encerramento comunitário. O roteirista
escolhe entre cinco formatos de convite para comentar de acordo com a mensagem e sempre termina com
o CTA fixo de curtir, se inscrever e compartilhar. A ponte pode usar contexto, outra história
bíblica, imagem presente na Bíblia,
correlação com outro versículo ou explicação dos limites de uma promessa. O contrato completo e um
exemplo baseado em Josué 1:9 estão em
[docs/shorts-script-standard.md](docs/shorts-script-standard.md).

Nas locuções, referências são sempre verbalizadas com `capítulo` e `versículo`: “Josué, capítulo
1, versículo 9”. O formato compacto `Josué 1:9` permanece apenas nos campos técnicos e metadados.
O roteiro não fala “Até aqui, o texto bíblico” nem outro rótulo de transição. As aspas, o itálico,
as seções estruturadas e a classificação das afirmações separam a citação do comentário. As legendas
não exibem rótulos de contexto: somente as palavras copiadas literalmente da fonte bíblica verificada
aparecem em itálico; referência, paráfrase e reflexão permanecem sem itálico. A apresentação do
narrador fica fora das aspas; somente o versículo literal é delimitado: `Em Josué, capítulo 1,
versículo 9, lemos: “texto do versículo.”`

O roteiro pode ser um JSON estruturado ou texto com parágrafos:

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
uv run pipeline-videos build-scenes salmo-121 --target-seconds 8
```

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

Para cada item de `images/codex-queue.json`, o Codex deve fazer uma chamada de geração e copiar o PNG
para o `staging_path`. O `worker_id` é a afinidade inicial; depois, o planejador recomenda o próximo
job ordenado ao primeiro trabalhador livre. Os trabalhadores não compartilham um `staging_path` nem
alteram manifestos. Quando o lote terminar, o agente principal faz uma única importação:

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
  --voice-id "SEU_VOICE_ID" `
  --model eleven_flash_v2_5

uv run pipeline-videos audio generate salmo-121 --allow-external
```

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

Projetos reais usam um subagente `youtube_growth_specialist`. A fila pode ser preparada junto das
imagens e processada em paralelo:

```powershell
uv run pipeline-videos metadata growth-prepare salmo-121
# O subagente escreve apenas em youtube/staging/youtube-growth-specialist/
uv run pipeline-videos metadata growth-import salmo-121
```

O especialista entrega três combinações distintas de título e thumbnail no fluxo padrão, uma
descrição única, palavras-chave, intenção de busca, hipótese de CTR e justificativas registradas em
`youtube/seo-strategy.json`. O agente principal valida e importa o pacote; o subagente não altera
`project.json`, aprovações ou ativos em uso.

Em `remote_review`, a fila declara exatamente uma combinação e é processada pelo agente principal,
sem subagente de crescimento. O contrato visual e a complementaridade entre título e capa permanecem
iguais; somente as alternativas de A/B test são eliminadas.

Nos projetos novos, todas as opções de título começam pela passagem principal, uma barra vertical e
o tema reconhecível do vídeo. A descrição abre com resumo curto e, em vídeos longos, recebe uma
timeline das citações no ponto em que entram na narrativa.

As três opções são produzidas como pares editoriais pelo mesmo índice: título principal com a
primeira thumbnail e títulos alternativos com as candidatas seguintes. O título concentra
referência, tema e intenção de busca; a frase curta da capa acrescenta tensão, decisão, contraste ou
consequência. O importador rejeita repetições literais, trechos completos e paráfrases quase iguais
entre o tema depois de `|` e o texto da thumbnail. Nomes próprios e palavras-chave isoladas podem
aparecer nos dois quando forem necessários para clareza.

Nos Shorts, o título principal precisa conter a expressão de busca primária, pelo menos uma opção
de título precisa ancorar a passagem bíblica e a descrição fica curta, sem capítulos. O especialista
recebe um contexto compacto em `youtube/growth-context.json`, em vez de reler todos os manifestos e
prompts completos.

O objetivo é aumentar relevância, clareza e probabilidade de clique sem clickbait enganoso. Não há
garantia de viralização: o próprio YouTube considera relevância, satisfação, watch time, histórico do
público, concorrência e sazonalidade. Em projetos novos, o especialista faz uma chamada de imagegen
por candidata e entrega o bitmap final já com texto, palavra de destaque vermelha e elemento
narrativo incorporados. Tipografia e símbolos participam da luz, profundidade, perspectiva e
composição; não podem parecer uma camada solta nem cobrir rostos, corpos ou a ação principal. O
importador apenas normaliza dimensões e formato, sem aplicar texto com Pillow. Projetos legados
continuam compatíveis com reutilização de cenas e composição local. Em vídeos longos, as três
combinações ficam pareadas para A/B test. Em Shorts, a primeira candidata ocupa exatamente o frame
zero e as demais permanecem versionadas como alternativas.

As regras seguem as orientações oficiais de [títulos e thumbnails](https://support.google.com/youtube/answer/12340300),
[descrições](https://support.google.com/youtube/answer/12948449),
[busca](https://support.google.com/youtube/answer/16090438) e
[A/B tests](https://support.google.com/youtube/answer/16391400).

### 8. Legendas, metadados e render

```powershell
uv run pipeline-videos captions build salmo-121
uv run pipeline-videos metadata growth-prepare salmo-121
uv run pipeline-videos metadata growth-import salmo-121
# No modo padrão, gera a prévia completa usando as versões ativas:
uv run pipeline-videos render salmo-121

# Depois da revisão do usuário, aprove imagens/metadados e produza o final:
uv run pipeline-videos approve salmo-121 images --by "Pedro"
uv run pipeline-videos approve salmo-121 metadata --by "Pedro"
uv run pipeline-videos render salmo-121 --final
uv run pipeline-videos validate salmo-121
uv run pipeline-videos approve salmo-121 final_render --by "Pedro"
```

A prévia one-shot fica em `render/draft-preview.mp4` e não altera `render/final.mp4`, a aprovação das
imagens ou a aprovação final.

O padrão final aprovado continua sendo o da V4 de Davi e Golias: resolução completa, H.264 com CRF
12, preset `slow` e supersampling 3; movimento pendular suave entre 1,02× e 1,105×; transições
`cinematic_mix`; e legendas Montserrat ExtraBold em caixa alta, com no máximo três palavras por
linha e card vermelho sólido arredondado na palavra ativa. Somente a prévia dos projetos novos usa
CRF 16, preset `fast` e supersampling 2 para reduzir o tempo de revisão. Projetos legados preservam
as configurações anteriores até uma migração explícita. Em Shorts, o bloco de legenda fica no
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

O upload inclui título, descrição/capítulos, tags e legenda SRT. Em vídeos longos, inclui também a
thumbnail. Nos Shorts, a capa é incorporada ao frame zero e o quadro precisa ser selecionado
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

Nos Shorts do fluxo padrão, o gerador cria três capas finais e a primeira ocupa o frame zero. Em
`remote_review`, cria apenas uma capa e ela ocupa esse mesmo frame. Em versículos longos, o construtor
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
│   ├── preview.mp4
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
erros transitórios. O default `eleven_flash_v2_5` reduz consumo na conta gratuita; troque para
`eleven_multilingual_v2` quando qualidade long-form for prioritária.

Os planos, limites e licenças mudam. Em 13/07/2026, a
[página oficial de preços](https://elevenlabs.io/pricing/api?price.platform=api) informava 10.000
créditos no plano Free e restrições de uso comercial. Confirme os termos atuais antes de publicar
ou monetizar; o pipeline não habilita pay-as-you-go nem faturamento.

Mais detalhes em [docs/elevenlabs.md](docs/elevenlabs.md).

## Confiabilidade e segurança

- cada etapa calcula hash de entrada e valida os artefatos antes de reutilizar;
- `--force` é explícito e recursos aprovados permanecem versionados;
- erros ficam em `project.json` e `logs/events.jsonl`;
- upload e publicação só existem em comandos separados, com aprovações, `--allow-external` e
  confirmações literais; não há publicação implícita, compra ou ativação de cobrança;
- chaves vêm apenas de `.env`/ambiente e nunca são gravadas nos manifestos;
- a validação compara roteiro, cenas, imagens, áudio e legendas para detectar omissão, duplicação ou
  reordenação;
- a publicação futura deve começar como privada ou não listada e exigir aprovação humana.

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
cinco ondas concorrentes, importação de thumbnails integradas, fallback determinístico legado,
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
