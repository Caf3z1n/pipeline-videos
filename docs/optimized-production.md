# Produção otimizada

## Perfil atual

Todos os projetos novos usam `final_candidate_one_shot` e `token_optimized_v1`. O pipeline produz
diretamente `render/final.mp4` com CRF 12, preset `slow` e supersampling 3. Não existe render de
prévia rápida nem uma segunda transcodificação para `preview.mp4`.

Não existem perfis legados, modos Light, produção guiada ou fallback de imagens manuais. Áudio
manual continua disponível porque muda somente a origem da locução e preserva o pipeline atual.

## Comando principal

Depois de briefing, fontes, roteiro e revisão válidos:

```powershell
uv run pipeline-videos autopilot <projeto> --allow-external
uv run pipeline-videos autopilot <projeto> --allow-external --wait-external
# Quando o pedido já inclui upload privado, conclua o fluxo na mesma execução guardada:
uv run pipeline-videos autopilot <projeto> --allow-external --wait-external `
  --upload-private --confirm-upload <id-exato-do-projeto> --json
```

`run --dry-run` mostra o plano sem mutação. O autopilot prepara filas, executa áudio, legendas e
música, aguarda os ativos criativos, importa cada lote uma vez, renderiza e valida. Hashes e staging
válido tornam a retomada idempotente. `--wait-external` monitora os stagings no próprio processo;
não abra novas rodadas do coordenador apenas para consultar arquivos. `status --json` entrega o
estado compacto quando uma integração realmente precisar consultar o projeto.

`--upload-private` exige `--allow-external`, validação final aprovada e confirmação com o ID exato.
Ele usa o caminho de revisão automatizada privada e nunca registra aprovação humana.

## Produção local

Cada produção materializa `audit/coordinator-job.json`. Uma sessão isolada
`local_video_coordinator`, em raciocínio médio e `fork_turns=none`, conduz o fluxo lendo apenas esse
job e as filas declaradas. O chat solicitante não precisa carregar o histórico inteiro da produção.

Em `adaptive_compact_v1`, hook, citação, referência, seções, beats e revisão bíblica são montados
deterministicamente a partir do catálogo verificado. Um planejador em raciocínio médio devolve
somente conceitos visuais por bloco; não há `shorts_script_specialist` nem `biblical_reviewer` de
modelo. Imagens usam até dois workers com `fork_turns=none`. Cada worker recebe somente seu job
compacto e escreve no staging declarado. O especialista de crescimento gera apenas um rascunho
pequeno de título e metadados, sem imagegen nem thumbnail. Fora do compacto, os especialistas
completos continuam disponíveis.

No Short compacto pintado, o bloco de referência falada usa o ativo fixo do perfil. O agente
principal o materializa no manifesto antes de montar `images/codex-queue.json`; ele aparece em
`fixed_profile_assets`, não em `items`, reduzindo a fila de geração em uma imagem sem alterar a
quantidade de cenas ou segmentos de áudio.

O agente principal é o único escritor de `project.json`, filas, manifestos, decisões e eventos. Ele
valida e importa roteiro, lote de imagens e crescimento de forma serializada.

## Produção remota

Na VPS, o fluxo, os contratos e a orquestração criativa são os mesmos:

- uma sessão persistente do coordenador por job;
- coordenador e especialistas em `gpt-5.6-sol`;
- coordenador em `medium`; no compacto, planejador visual e crescimento em `medium`, imagens em
  `low` e nenhuma sessão de roteirista ou revisor bíblico;
- fora do compacto, roteirista e revisor bíblico em `high`; até dois workers de imagem em qualquer
  caso e exatamente um especialista de crescimento;
- imagens processadas em até dois lotes paralelos com staging separado;
- exatamente um título; nenhuma thumbnail no perfil padrão de Shorts, e uma thumbnail nos longos
  ou no perfil 3D legado.

O executor inicia a sessão uma vez e usa `codex exec resume` nos checkpoints seguintes. Áudio,
música, legendas, imports, render, validação e publicação continuam determinísticos no Python. Veja
[Produção remota](remote-production.md).

## Roteiro de Shorts

Prepare `script/shorts-queue.json`. No compacto, o arquivo de staging contém apenas
`CompactVisualPlan`: `job_id`, `input_hash` e um conceito para cada bloco. O pipeline reconstrói o
pacote completo sem modelo, verifica literalmente hook, fala de Jesus, referência e fonte e grava a
revisão `deterministic-verified-compact-v1`. O preflight agrega os erros; o planejador faz no máximo
uma correção e, se ela falhar, executa `preflight.fallback_command`. A produção continua com cenas
visuais seguras em vez de abrir novas rodadas. Fora do compacto, permanece o pacote completo com
revisão independente quando configurada.

No perfil padrão de Shorts, o mesmo pacote traz `strategy.visual_scenes`. Cada item contém um
trecho literal e contíguo da locução, `section_id`, uma descrição concreta de cena e a relação
`literal`, `contextual` ou `symbolic`. O construtor usa esses itens diretamente para criar as cenas;
o áudio preserva `scene_id` e `narration_block_id`, permitindo gerar imagens e voz em paralelo sem
perder o vínculo entre o que é narrado e o que aparece.

No compacto, a quantidade configurada de cenas é um limite máximo. A citação é separada primeiro
em frases e, quando necessário, em vírgulas que preservem duas orações legíveis. Se não houver
fronteiras naturais suficientes, o plano usa menos cenas; divisão uniforme por quantidade de
palavras e blocos que terminem em termos pendentes são proibidos pelo preflight.

Projetos novos usam `creative_retention_v3`. O seletor resolve modo criativo, postura e família de
hook antes de abrir a fila. No perfil padrão, `hook_contract=user_six_v1` aceita exclusivamente as
seis estruturas documentadas; uma família declarada que não corresponda à primeira fala bloqueia o
preflight antes de imagens, áudio, render ou upload. A citação literal entra entre 8 e 20 segundos.
O perfil compacto usa `cinematic_interruption_v1`, restringe a seleção a fala direta de Jesus e
reduz palavras, cenas e orçamento do coordenador sem reduzir a qualidade final. Em 15 segundos, o
hook curado vai de 0 a 3,5, a citação de 3,5 a 12,3 e a referência de 12,3 a 15 segundos; o limite
do trecho é 28 palavras e são produzidas até seis cenas. Não há CTA, aplicação explicativa nem
callback. A otimização não preenche duração com silêncio: seleção e preflight escolhem um pacote
compatível. Quando o projeto declara `adaptive_range_v1`, a duração real da voz é aceita em qualquer
ponto da faixa solicitada; cobertura do alvo preferencial, pausas internas e silêncio terminal são
registrados como avisos, não como motivo para reiniciar uma produção tecnicamente íntegra.
De 16 a 20 segundos, o limite literal cresce junto com a janela de narração até 47 palavras. O
seletor calcula hook, citação e a referência realmente falada; intervalos como `João 8:31-32`
incluem também `versículos`, `a` e o número final. O banco curado oferece pelo menos 20 pacotes por
duração e bloqueia cedo um pedido sem combinação suficiente, antes de roteiro, TTS ou imagens.

O contrato editorial completo está em [Padrão de roteiro de Shorts](shorts-script-standard.md).

## Roteiro de vídeo longo

Projetos longos novos usam `creative_retention_long_v2`. O `ScriptDocument` inclui
`long_form_strategy`, com modo e postura explícitos, hook adaptativo, um beat por seção, âncoras
para todas as citações, reengajamentos proporcionais à duração, resolução coerente com o modo e
uma única ação de CTA. Aplicação, pergunta central e callback são opcionais quando o tema não os
exige.

O fluxo remoto produz e valida esse contrato antes de importar roteiro e revisão. No fluxo local, o
agente principal segue [Padrão de roteiro para vídeos longos](long-form-script-standard.md) e importa
o JSON estruturado com `import-script`. O validador rejeita duração insuficiente, abertura genérica,
trechos sem valor novo, evidência incompatível com a postura, citações agrupadas, intervalos
bíblicos excessivos, reengajamentos escassos e encerramento com mais de uma ação. O catálogo
completo está em [Direção criativa](creative-direction.md).

## Imagens

`images/codex-queue.json` contém uma imagem por item. A primeira tentativa usa o prompt integral.
Em `moderation_blocked`, repita o mesmo prompt uma vez; somente após o segundo bloqueio use uma
alternativa de ação ou enquadramento, preservando identidade, idade aparente, rosto, cabelo,
figurino, proporções, estilo, paleta, ambiente e sentido narrativo.

Cada ativo é salvo no `staging_path`; `images import-batch` importa o lote uma vez. Os arquivos
compactos de worker armazenam o prefixo comum dos prompts uma única vez e os deltas por cena; o
worker reconstrói `common_prompt_prefix + prompt_delta` sem alterar um byte do prompt original.
Nunca regenere item já importado ou aprovado.

## Crescimento e thumbnail

A fila exige exatamente um título no perfil padrão de Shorts. O título segue
`Enfoque concreto | Passagem`: a
passagem aparece uma única vez, somente no final, e nunca é repetida no enfoque. O enfoque se adapta
ao modo criativo, sem forçar tensão motivacional. A fila declara `youtube_auto_frame`, zero designs,
zero arquivos e `external_image_generation_required=false`. O perfil 3D legado preserva a capa que
edita a abertura e nasce no Imagegen com texto e marcação incorporados.

No compacto, o modelo devolve apenas `CompactGrowthDraft`. O Python completa o pacote, normaliza
`Enfoque concreto | Passagem`, preserva a seleção automática de frame e grava recomendações de SEO
em `youtube/growth-warnings.json`. Tamanho ideal de descrição, densidade de tags e presença de
palavra-chave são consultivos; estrutura válida, limites da plataforma, correspondência do contrato
e privacidade continuam obrigatórios. Depois de uma única correção agregada, o fallback
determinístico finaliza metadados seguros.

Em vídeos longos, a fila usa `generated_theme_art` e o template
`biblical_cinematic_landscape_v1`: geração nova horizontal 16:9, baseada exclusivamente no tema do
briefing, sem imagem de referência e sem texto, letras, logotipos ou marcações. O personagem ocupa
30% a 40% do quadro e o lado oposto preserva espaço negativo limpo. O importador aceita os dois
modos e nunca aplica overlay local. Uma saída válida é aceita; retries adicionais existem apenas
para `moderation_blocked` sem bitmap.

No perfil 3D legado de Shorts, o prompt final reserva os 10% superiores do canvas como faixa segura sem tipografia,
incluindo extrusão, contornos e sombras. Todo o texto começa abaixo desse limite para tolerar o corte
visual da grade do YouTube. Essa margem específica não se aplica às thumbnails de vídeos longos.

No perfil 3D legado, o guard visual exige os marcadores positivos de
`cinematic-family-3d-v1` e animação 3D, mas
desconsidera marcadores fotográficos declarados dentro de seções negativas como `Avoid:`. Se um
pacote pedir fotografia como estilo positivo ou falhar em outra regra determinística de crescimento,
o remoto permite um único checkpoint de correção por tentativa antes de voltar ao backoff.

Metadados novos usam `search_first_unique_v2`: consulta principal nos primeiros 55 caracteres do
título, descrição única com passagem e valor concreto na abertura, dois parágrafos, sem CTA
genérico e sem autorreferência ao formato como “este Short”, “neste vídeo”, “este conteúdo” ou “o
vídeo mostra”; oito a quinze tags relevantes e comentário fixado específico. O contrato exige
justificativa de descrição, estratégia de tags e hipótese de descoberta. Tags continuam
secundárias; o especialista não promete viralização nem inventa volume de busca.

## Cache de voz

Cada trecho de TTS possui hash endereçado pelo conteúdo, voz, modelo e configurações de áudio, sem
depender do número da cena ou da versão do roteiro. O `.wav`, o alinhamento e o sidecar `.cache.json`
são gravados imediatamente após a geração. Renomear ou reordenar cenas reutiliza segmentos iguais;
uma retomada também recupera trechos já pagos antes de uma validação posterior falhar.

`adaptive_compact_v1` usa `continuous_narration_v1`. A ElevenLabs recebe hook, fala bíblica e
referência como uma única narração; os limites visuais são derivados dos timestamps entre palavras.
Os WAVs por cena continuam existindo para render e legendas, mas são fatias contíguas da mesma
fonte. Antes de persistir o áudio, o pipeline confere a sequência literal de tokens e exige que a
concatenação das fatias reproduza todas as amostras PCM da fonte, sem perda ou duplicação.
`continuous_section_v1` permanece compatível somente com projetos anteriores.

## Política de validação

`completion_first_v1` mantém como bloqueantes apenas integridade e faixa de duração da mídia,
fidelidade/fonte bíblica, ativos ausentes ou corrompidos, segurança, privacidade, publicação e custo
externo não autorizado. Heurísticas editoriais — SEO, hook, reengajamento, contagem ideal de
palavras, pausas curtas, cobertura de legendas e estética — viram avisos. O candidato final ainda é
validado, mas esses avisos não alimentam loops de correção.

## Mixagem de áudio

Projetos novos usam `voice_music_loudness_v1`. A narração é normalizada para -18 LUFS e a música
para um alvo derivado 13 LU abaixo (-31 LUFS por padrão), com true peak limitado antes da mistura.
O mesmo contrato vale para Shorts e vídeos longos, inclusive quando a faixa anexada tem masterização
muito mais alta que a voz. Trims em dB continuam possíveis como override explícito; projetos legados
mantêm `legacy_gain_v1`.

## Áudio manual

Quando `audio.provider=local_manual`, o pipeline prepara `audio/manual-queue.json`, apresenta
`script/narration.txt` integralmente e aguarda um arquivo de voz limpo. `audio import-manual`
normaliza o áudio e usa WhisperX em CPU. Não usa ElevenLabs, `--allow-external` ou alinhamento
proporcional silencioso.

## Concorrência e segurança

- um único escritor por projeto;
- imports e montagem final serializados;
- projetos diferentes podem rodar em paralelo;
- subagentes locais e remotos escrevem somente em staging;
- nunca publicar, agendar, aprovar ou alterar faturamento sem autorização persistida.

## Auditoria e orçamento de tokens

O `autopilot` grava a auditoria local ao concluir. Para recalcular sem chamar modelo nem serviço
externo:

```powershell
uv run pipeline-videos audit tokens <projeto>
```

O comando lê os contadores cumulativos das sessões Codex relacionadas ao ID do projeto e atualiza
`audit/production-metrics.json` e uma seção marcada de `audit/report.md`. A seção é substituída de
forma idempotente e não apaga análises escritas anteriormente. O relatório separa coordenação,
roteiro, revisão bíblica, imagens e crescimento; também compara tokens efetivos com os orçamentos
persistidos do coordenador e da produção completa. Esses orçamentos são telemetria: nunca encerram
o fluxo. O relatório também registra tempo decorrido, faixa/duração efetiva e fallbacks acionados.
