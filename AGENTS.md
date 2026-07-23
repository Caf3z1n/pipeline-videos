# Instruções persistentes para o pipeline de vídeos

## Antes de agir

1. Leia este arquivo e o `README.md` da raiz.
2. Para vídeo existente, leia primeiro `<projeto>/project.json`.
3. Leia depois briefing, fontes, decisões, roteiro, revisão bíblica e somente manifestos existentes.
4. Execute `uv run pipeline-videos status <id-ou-pasta>` antes de regenerar qualquer artefato.
5. A pasta do vídeo é a fonte de verdade; o chat é auxiliar.
6. Para criar, retomar ou renderizar vídeo, leia `docs/optimized-production.md`.
7. Para roteiro novo, leia também `docs/creative-direction.md`.
8. Para roteiro de Short, leia também `docs/shorts-script-standard.md`.
9. Para roteiro de vídeo longo, leia também `docs/long-form-script-standard.md`.

## Organização

- Horizontais: `projects/videos-longos/`; Shorts: `projects/shorts/`.
- Pastas usam `DD-MM-AAAA_tema-do-video`; aliases sem data continuam válidos.
- Projetos novos usam `token_optimized_v1`; não migre projetos existentes implicitamente.
- O fluxo padrão usa `autopilot`; `run --dry-run` continua sendo diagnóstico sem mutação.

## Segurança e custo

- Não publique, envie ou agende sem pedido explícito do usuário. Quando o pedido incluir upload
  privado, use `render-review` e `youtube upload --automated-review`; esse caminho não exige nem
  registra aprovação humana. Publicação pública ou agendada continua sendo uma ação separada, com
  data/hora explícita e os guards do CLI. Nunca compre, habilite pay-as-you-go ou altere faturamento.
- Pedro autorizou de forma persistente o uso da ElevenLabs nas produções que pedir neste pipeline;
  trate o próprio pedido de produzir/criar/retomar um vídeo como autorização para usar
  `--allow-external`, sem pedir nova confirmação a cada vídeo. Não use ElevenLabs quando ele pedir
  áudio manual, mock, produção sem serviço externo ou revogar essa autorização.
- Nunca exiba, registre ou versione chaves e tokens.
- Não substitua a geração embutida por API paga sem aprovação explícita.
- Não sobrescreva imagem aprovada; gere nova versão e peça aprovação.

## Duração e naturalidade da voz

- Uma duração mencionada normalmente, como “um Short de 50 segundos”, é uma meta aproximada, não
  uma exigência exata. Crie com `preferred_target_v1`; aceite a duração natural resultante e não
  abra correção, nova síntese ou novo render por diferença normal de alguns segundos.
- Use `exact_target_v1`/`--exact-duration` somente quando o usuário disser explicitamente “exatos”,
  “cravados”, “fixos” ou equivalente. Uma faixa mínima/máxima explícita usa `adaptive_range_v1`.
- Nunca reduza ou aumente a velocidade da voz, aplique `atempo`, time-stretch, silêncio, padding ou
  alongamento de imagem para aproximar uma meta comum. A voz permanece em velocidade natural `1.0`.
  Qualquer override de velocidade exige um pedido explícito separado do usuário e
  `speech_rate_policy=explicit_user_override_v1`; a duração, sozinha, nunca autoriza esse override.
- Em `preferred_target_v1`, desvios da janela preferencial são no máximo avisos e não alimentam
  loops de correção. Fidelidade bíblica, integridade da mídia e ativos ausentes continuam sendo
  bloqueios reais.

## Precisão bíblica

- Não invente versículo, referência, citação ou detalhe histórico.
- Na locução, verbalize: “Livro, capítulo X, versículo Y”; formato compacto fica nos metadados.
- Short cita literalmente ao menos um versículo verificado, curto e claro.
- Apresente a referência fora das aspas. Fora do perfil compacto, use
  `Em Livro, capítulo X, versículo Y, lemos: “texto.”`. Em `adaptive_compact_v1`, cite primeiro a
  fala direta de Jesus entre aspas e encerre com `Livro, capítulo X, versículo Y.`.
- Registre tradução, fonte, acesso e direitos de citação literal.
- Classifique afirmações como `biblical_text`, `verified_context`, `interpretation` ou
  `creative_elaboration`.
- Em postura factual, não use `interpretation` nem `creative_elaboration` na locução. Em postura
  exploratória, deixe opinião e inferência explicitamente distinguíveis da Bíblia.
- Preserve incertezas; finding `blocking` aberto impede aprovação.
- Nunca invente nem registre aprovação humana em nome do usuário. Para upload privado solicitado
  explicitamente, use o candidato automatizado validado em vez de preencher aprovações.
- A locução permanece integralmente em português: não narre `open loop`, `payoff`, `storytelling`,
  `call to action` ou outro jargão editorial em inglês.
- Toda locução autoral, em qualquer formato, perfil ou duração, usa
  `human_authorial_voice_v1`: narre diretamente o fato, a ação, a dúvida ou a explicação. São
  proibidas frases metalinguísticas como “o relato registra”, “o relato não explica”, “a sequência
  é compacta”, “o retrato começa”, “a passagem paralela acrescenta”, “comparar é observar”, “não
  podemos afirmar” e equivalentes. Também não valorize uma ação dizendo antes o que alguém não fez
  nem defina cenas por listas de ausência como “afirmação, sem A, B ou C”. Também bloqueie a
  fórmula autoral `não A nem B, mas C` e declare `C` diretamente. O filtro vale somente para voz
  autoral: preserve citações bíblicas literais e hooks negativos legítimos como `Nunca faça [X].`.
- Não diga “o texto fala/diz/registra”. Fora do perfil compacto, prefira “a Bíblia diz” ou “Em
  Livro, capítulo X, versículo Y, lemos:”. No compacto, não use introdução antes da citação.
- Em Shorts e vídeos longos, a voz autoral usa `jesus_first_person_v1`: Jesus é o eu lírico e fala
  em primeira pessoa. Separe claramente a dramatização autoral das citações bíblicas literais,
  mantendo aspas, fonte e referência exatas; nunca apresente elaboração criativa como fala bíblica
  documentada.
- Em vídeo longo, use citações literais verificadas distribuídas pela narrativa: alvo de 0,8 a 1,2
  por minuto; dez minutos exigem de oito a doze citações. No padrão novo, a primeira entra até 75
  segundos, o intervalo máximo é de 105 segundos e a parte final não fica mais de 120 segundos sem
  âncora bíblica; não agrupe versículos apenas para cumprir quantidade.

## Produção otimizada

- O agente principal é o único escritor de estado compartilhado.
- Cada produção local materializa `audit/coordinator-job.json` e deve ser conduzida por uma sessão
  isolada `local_video_coordinator`, em raciocínio médio e `fork_turns=none`; o chat solicitante
  apenas acompanha o resultado e não relê todo o histórico dentro do coordenador.
- Workers escrevem somente no staging declarado e recebem contexto compacto com
  `fork_turns=none`.
- Use no máximo dois trabalhadores de imagem simultaneamente. O especialista do YouTube produz
  somente metadados e não depende de imagens.
- Imagens usam uma chamada `imagegen` por tentativa. Em `moderation_blocked`, repita o mesmo prompt
  uma vez; se bloquear novamente, tente uma única alternativa de cena preservando identidade, idade
  aparente, rosto, cabelo, figurino, proporções, silhueta, estilo, paleta, ambiente e sentido.
- Não faça inspeção semântica cena a cena no one-shot; entregue o candidato final completo ao usuário.
- Imagens ativas podem entrar no candidato final, mas permanecem não aprovadas.
- Imports de imagem e crescimento acontecem uma única vez no join.
- Orçamentos de tokens são telemetria, nunca motivo para abandonar a produção. Ao ultrapassar um
  orçamento, use o fallback determinístico da etapa e continue até o candidato final.
- Em `completion_first_v1`, só são bloqueantes: integridade/duração da mídia, fidelidade e fonte
  bíblica, ativos ausentes ou corrompidos, segurança, privacidade, publicação e custo externo não
  autorizado. SEO, força editorial, contagem ideal de palavras, pausas curtas, cobertura de
  legendas e estética de cena viram avisos e não iniciam loops de correção.

## Criação e modos de Shorts

- Ideação editorial não é produção. Todo pedido para sugerir temas, discutir roteiros, preparar
  roteiros em massa ou aprová-los começa com uma sincronização remota única pelo controlador
  `request-video-production` (`-Action ApprovedSync`) e depois
  `uv run pipeline-videos approved-scripts workflow-status`. Antes de qualquer mutação, faça
  exatamente uma pergunta curta confirmando a etapa desejada. Se o usuário já nomear a operação e
  disser para executá-la agora, essa frase vale como confirmação; não repita a pergunta.
- A sincronização de produção usa `approved_script_id + content_hash` e só permite a progressão
  `approved -> reserved -> produced`. Divergência de hash bloqueia o lote antes de qualquer
  mutação; estado remoto atrasado nunca rebaixa o local. `script_counts.approved` representa
  aprovação editorial histórica, enquanto `production_counts.approved` representa somente os
  pacotes ainda disponíveis para produção.
- Depois da confirmação, execute somente a etapa escolhida e encerre com uma resposta direta. Não
  crie projeto, coordenador, subagente, revisão, SEO, plano visual, mídia ou produção que não
  pertença explicitamente à etapa confirmada. Não explique o processo quando bastar informar o
  resultado.
- O fluxo editorial em massa é obrigatoriamente:
  `temas sugeridos -> temas aprovados e salvos -> lote de roteiros -> revisão dos textos pelo
  usuário -> aprovação em massa por hash -> produção solicitada separadamente`. Nunca pule da
  sugestão de tema para criação de projeto ou produção.
- Em roteiro pré-aprovado, duração é apenas estimativa editorial. Depois da aprovação por hash,
  texto, pontuação, CTA, citações e ordem são sagrados e imutáveis: nunca corte, amplie, resuma,
  complete, reescreva ou bloqueie o pacote para alcançar 50 segundos ou qualquer outra meta. Use
  `duration_policy=approved_script_locked_v1`; o áudio em velocidade natural define a duração
  efetiva, sem mínimo ou máximo de palavras. Diferença entre estimativa e duração real não gera
  warning bloqueante, nova síntese nem reparo. Limitação técnica de mídia ou da plataforma deve ser
  relatada sem alterar o roteiro aprovado.
- Temas usam `approved-scripts topic-suggest`, `topic-list` e `topic-approve`. A preparação de
  roteiros usa `draft-batch-prepare`, que não inicia agente automaticamente e não cria projeto.
  Quando o usuário confirmar a escrita do lote, use no máximo um roteirista para todo o lote e
  nenhum `biblical_reviewer`. O próprio lote recebe fontes, `required_answer_points` e
  `uncertainty_boundaries`; o roteirista faz a conferência de fonte na mesma saída e o importador
  aplica `inline_source_guard_v1`. Depois, importe e mostre os textos com
  `draft-batch-import` e `draft-batch-review`; a aprovação de todos usa um único hash com
  `draft-batch-approve`.
- `draft-batch-approve` também materializa, de forma determinística e idempotente, um
  `ApprovedShortScript` por roteiro no pool consumido por `approved-scripts release`. Essa ponte
  preserva narração, CTA, SEO e hash, usa `jesus_1` e `leaning` como configuração padrão e cria o
  plano visual por blocos sem modelo. Não deixe roteiro apenas no estado editorial `approved`.
  Falha de preflight precisa ocorrer antes de mover tema ou roteiro, evitando aprovação parcial.
- Para produzir um aprovado no servidor, aceite o título exato em
  `approved-scripts release --title "<título>" --authorize-external`. A liberação reserva o pacote,
  cria somente um job `approved_fast_v1`, usa upload privado e não reabre roteiro, revisão bíblica,
  SEO ou planejamento visual. Repetição do pedido deve reutilizar o estado persistido ou informar
  que o pacote já está reservado/produzido; nunca crie duplicata silenciosa.
- Pelo controlador remoto, a liberação usa um único bundle content-addressed transferido por SCP.
  O servidor valida todos os IDs, hashes e snapshots antes da primeira mutação, grava uma intenção
  idempotente e retoma os mesmos IDs de job após interrupção. Pacote já existente é referenciado;
  pacote ausente é importado. Não envie snapshots em Base64 pela linha de comando.
- Em `approved_fast_v1`, não execute preflight, pontuação, conferência bíblica, revisão criativa ou
  validação de SEO depois da aprovação humana. Confira somente o hash imutável do snapshot. A
  validação final é estritamente técnica: integridade dos arquivos, correspondência literal entre
  roteiro, cenas e áudio, imagens, legendas, música, mixagem e vídeo final. Se surgir qualquer
  espera externa diferente de imagens, falhe sem chamar modelo e preserve o mesmo job para retomada.
- A revisão bíblica e qualquer correção da bancada acontecem antes de mostrar a versão final ao
  usuário. Depois que o usuário disser que aprovou os textos exibidos, não crie agente, não pesquise
  fontes e não reabra revisão: apenas confira o hash e mude o estado para `approved`. Citação
  literal deve ser copiada da fonte verificada, preservando a distinção entre fala e narração.
  Tema com `uncertainty_boundaries` precisa expressar a inferência naturalmente, como “pode ser
  compreendido” ou “você pode entender assim”, sem transformar interpretação em certeza universal.
- Todo lote usa `quality_reference=published_high_stakes_last_three_v1`,
  `narrator_identity=jesus_first_person_v1` e `hook_contract=high_stakes_opening_v1`. O hook deve
  ser o prefixo literal da narração, abrir tensão ou curiosidade imediatamente, permanecer honesto
  e receber payoff completo. A narração inteira mantém Jesus como eu lírico; abrir em terceira
  pessoa com “Jesus...” é bloqueante. Hook, clareza e payoff precisam obter nota mínima 8/10 antes
  da aprovação em massa.
- Antes de mostrar no chat qualquer hook novo ou reescrito da bancada, valide todos de uma vez com
  `approved-scripts hook-check --hook ...`. Se algum falhar, corrija somente o hook e repita a
  validação; nunca apresente ao usuário um hook reprovado nem reescreva o roteiro inteiro quando o
  erro estiver apenas na abertura.

- Pedido simples de Short usa `new-short`, seguido de `status`, `run --dry-run` e one-shot; não peça
  novamente versículo, CTA, estilo ou fluxo já padronizados.
- Shorts aceitam duração mínima de 15 segundos. De 15 a 44 segundos, respeite o perfil persistido
  `adaptive_compact_v1`, restrito a `devotional_application` e `pastoral_encouragement`. Ele encena
  um momento emocional de um filme épico sobre Jesus: a primeira frase interrompe o pensamento do
  espectador, a fala direta de Jesus começa imediatamente depois e a referência bíblica encerra o
  vídeo. Em 15 segundos, usa 41 a 46 palavras, cinco a seis cenas, hook curado de 10 a 12 palavras
  entre 0 e 3,5 segundos, citação de 3,5 a 12,3 segundos, trecho literal de até 28 palavras,
  referência de 12,3 a 15 segundos, nenhum CTA e até dois workers de imagens. Não use “lemos”, introdução
  expositiva nem versículo narrativo; a fonte precisa estar marcada como fala direta de Jesus. O
  conjunto hook+versículo+referência precisa preencher a duração com narração: nunca insira silêncio
  para alcançar o alvo. Em projeto com faixa adaptativa, aceite qualquer duração real dentro dos
  limites persistidos; cobertura abaixo do alvo preferencial, pausa interna e silêncio terminal são
  avisos quando a mídia final continua íntegra e dentro dessa faixa.
- Uma citação literal de Short pode abranger dois ou mais versículos consecutivos quando a faixa
  for necessária ou benéfica para preservar sentido e continuidade, como em João 14:1-2. Trate a
  faixa como uma única citação verificada, preserve o excerpt integral e a referência exata e
  respeite os limites persistidos de palavras e duração; nunca amplie a faixa apenas para preencher
  tempo.
- Shorts padrão novos usam `hook_contract=user_six_v1` e começam exatamente com uma destas seis estruturas:
  `Se você quer [X], faça isso.`, `O que acontece se você parar de [X]?`,
  `Se você ainda faz [X], está perdendo [Y].`, `Ninguém fala sobre [X], mas...`,
  `Quer aprender [X]? Comece...` ou `Nunca faça [X].`. O perfil compacto usa
  `hook_contract=cinematic_interruption_v1` e `hook_family=emotional_interruption`; outras famílias
  ficam restritas a projetos legados e vídeos longos. O preflight deve bloquear qualquer
  divergência antes da produção. No perfil compacto, use exatamente o hook curado da passagem; a
  seleção exige pisos persistidos de força do hook, valor da mensagem e potencial de replay.
- Quando o pedido exigir curiosidade bíblica de alto interesse, use explicitamente
  `new-short --profile high-stakes` e o perfil `high_stakes_biblical_question_v1`, restrito a 45–60
  segundos. Ele seleciona a dúvida antes da passagem, copia um hook factual curado de
  `high_stakes_opening_v1`, exige todos os `required_answer_points`, preserva `answer_status` e
  `uncertainty_boundaries` e usa validação incorporada de fontes. A citação literal continua
  obrigatória, mas usa `adaptive_payoff_v1` entre 0 e 44 segundos conforme sua função de tensão,
  prova ou recompensa; não imponha a janela padrão de 8–20 segundos.
  Esse catálogo é de ciclo único: quando todos os temas tiverem sido usados ou reservados, informe
  explicitamente a exaustão e não crie outro projeto até que uma nova rodada curada seja incluída e
  validada. Nunca reinicie a contagem automaticamente.
- A seleção compacta usa também um prior suave e reproduzível de clareza, concretude e
  visualizabilidade, penalizando abertura genérica sem situação, ação ou tensão e repetição exata
  entre Shorts recentes. O prior nunca reescreve hook ou citação, não bloqueia precisão bíblica e
  não se apresenta como desempenho observado. Métricas confirmadas orientam o roteiro padrão e o
  plano visual compacto; não reescrevem automaticamente o hook bíblico já selado.
- O seletor automático usa o catálogo local verificado, combina as pastas com o registro Git
  compartilhado, reserva por compare-and-swap antes de criar o projeto e favorece variedade de
  livro, tema, personagem, testamento, modo criativo e família de hook. Falha do registro bloqueia a
  seleção; não use fallback local.
- Pedido dedicado a personagem usa `new-character-video` e o segmento opt-in `narrative_story`.
  Selecione pelo nome ou alias, use `biblical_storytelling` com o perfil
  `biblical_character_storytelling_v1` por padrão e importe a âncora e todas as passagens
  relacionadas do pacote. `character_portrait` permanece disponível somente por seleção explícita
  quando o usuário pedir análise do personagem. No storytelling, a locução conta a história em
  progressão humana e cronológica; cautelas, fontes e lacunas ficam na revisão e não viram frases
  metalinguísticas como “o relato registra”, “a sequência mostra” ou “não podemos afirmar”.
  Fragmentos narrativos nunca entram na rotação automática comum nem no perfil compacto de 15 a 44
  segundos; nesse caso, exija Short de ao menos 45 segundos ou vídeo longo.
- O catálogo combinado contém 476 candidatos: 76 legados e o banco reproduzível
  `curated-verse-bank-v2.json`, com 120 falas diretas de Jesus para compactos, 180 versos gerais e
  100 âncoras de vídeo longo. Reconstrua o banco somente pelo script a partir dos artefatos oficiais
  VPL+USFM da BPM; não digite citações manualmente no JSON. Respeite `recommended_formats`, use
  `passage` para o trecho literal e `context_passage` para a janela de estudo. A cobertura compacta
  deve manter ao menos vinte combinações elegíveis para cada duração de 15 a 20 segundos.
- O briefing automático não equivale a aprovação humana.
- Em `adaptive_compact_v1`, o pipeline sela deterministicamente hook, citação literal, referência,
  seções, beats e revisão bíblica a partir do catálogo verificado. Crie apenas um planejador visual
  em raciocínio médio para devolver descrições de cenas. A revisão determinística substitui o
  `biblical_reviewer`; não crie roteirista nem revisor de modelo nesse perfil.
- Fora do perfil compacto, prepare `script/shorts-queue.json` e crie exatamente um
  `shorts_script_specialist` em raciocínio alto. Não crie `biblical_reviewer`; aplique a validação
  incorporada de fontes e importe uma única vez após o preflight final.
- `new-short-audio-manual` mantém o fluxo completo, mas usa `audio.provider=local_manual`: mostre
  integralmente `script/narration.txt`, aguarde um único arquivo de voz do usuário e importe com
  `audio import-manual`. O alinhamento usa WhisperX isolado em CPU, sem ElevenLabs e sem
  `--allow-external`; a primeira execução pode baixar o pacote e o modelo português, depois usa o
  cache local.
- Voz real usa ElevenLabs e exige `--allow-external`; `mock` é apenas para demonstrações e testes.
- Voz anexada usa somente áudio limpo, sem música ou efeitos, e deve reproduzir exatamente a
  locução; divergência ou palavra sem timestamp bloqueia a importação em vez de simular tempos.

## Contratos dos especialistas

- O contrato de Shorts novos usa `creative_retention_v3`: modo e postura explícitos e hook inicial
  obrigatório. No perfil padrão, usa citação literal entre 8 e 20 segundos, desenvolvimento com
  informação nova, reengajamento, resolução coerente com o modo e exatamente uma ação de CTA em até
  quatro segundos. No perfil compacto, o Python produz exatamente três seções — interrupção
  emocional, fala direta de Jesus entre aspas e referência falada — sem CTA, comentário, despedida
  ou resumo; o modelo não pode reescrever essa locução.
  Aplicação e callback são opcionais fora do contrato cinematográfico compacto.
- Shorts novos usam `narration_block_visual_v1`: `strategy.visual_scenes` divide literalmente a
  narração em blocos contíguos e atribui a cada bloco uma descrição concreta e distinta de cena,
  classificada como `literal`, `contextual` ou `symbolic`. Os blocos reconstroem integralmente cada
  seção e se tornam a autoridade compartilhada por cena, imagem e segmento de áudio. Não limite a
  sequência a Jesus conversando; varie ação, ambiente, objeto e simbolismo quando a narração permitir.
- No compacto, `target_scene_max` é um teto. Divida a citação somente depois de ponto, dois-pontos,
  ponto e vírgula, interrogação, exclamação ou vírgula semanticamente segura; nunca encerre bloco em
  palavra pendente. Se não houver fronteiras naturais suficientes, use menos cenas.
- O compacto usa `audio.synthesis_segmentation=continuous_narration_v1`: sintetize hook, citação e
  referência juntos em uma única chamada e derive os WAVs das cenas pelos timestamps de palavras.
  A concatenação dos recortes precisa reconstruir a fonte PCM sem perder ou duplicar amostras;
  divergência literal ou acústica bloqueia antes do render. `continuous_section_v1` permanece apenas
  para reproduzir projetos existentes.
- No perfil `biblical-midcentury-gouache-v1`, o bloco final identificado por
  `cinematic_devotional.reference_section_id` reutiliza sempre a imagem fixa de Bíblia aberta
  instalada em `visual/reference-images/`. Mantenha o bloco no plano e no áudio, mas não crie job de
  `imagegen` para ele; somente os demais blocos entram na fila dos trabalhadores.
- O roteirista de vídeos longos novos usa `creative_retention_long_v2`: modo e postura explícitos,
  hook adaptativo, progressão por movimentos, reengajamentos proporcionais à duração, citações
  literais periódicas, resolução coerente com o modo e exatamente uma ação de CTA em até dez
  segundos. Pergunta, aplicação e callback são opcionais.
- Retenção não implica mensagem motivacional. A resolução pode ser desfecho factual, explicação,
  descoberta, síntese, posição filosófica, incerteza preservada ou aplicação.
- Em `biblical_character_storytelling_v1`, além do filtro autoral global, a maior parte do
  desenvolvimento avança por ações e acontecimentos; beats de análise/comparação e títulos
  metalinguísticos continuam bloqueados.
- Crie exatamente um `youtube_growth_specialist` quando `youtube/growth-queue.json` estiver
  pendente, localmente ou na VPS, com raciocínio médio. Nenhum especialista publica nem promete
  CTR/alcance.
- O especialista gera exatamente um título e os metadados. Não chama `imagegen`, não produz imagem
  de capa e não altera o primeiro frame.
- Shorts e vídeos longos novos seguem `Enfoque concreto | Passagem`. A passagem aparece exatamente
  uma vez e somente depois da barra; o enfoque nunca repete a referência bíblica. O enfoque se
  adapta ao modo criativo sem forçar tensão motivacional. A descrição abre com resumo
  curto e, em vídeos longos,
  traz timeline das citações iniciada exatamente por `00:00 - Abertura`.
- O especialista cria exatamente um título em Shorts e vídeos longos. O título carrega referência,
  enfoque e busca.
- Projetos novos usam `search_first_unique_v2` para SEO. A descrição é única, abre com a consulta
  principal, passagem e entrega concreta, usa ao menos dois parágrafos e não repete CTA genérico
  como “Compartilhe esta mensagem”. Também não se refere ao próprio formato com expressões como
  “este Short”, “neste vídeo”, “este conteúdo” ou “o vídeo mostra”; começa diretamente pelo assunto.
  Em vídeos longos, preserve também a timeline bíblica.
- `metadata.topics` contém de oito a quinze tags distintas e diretamente relevantes, incluindo
  consulta principal, passagem, livro e variações reais de busca. Tags são metadado secundário:
  não use `#` nelas, não cole listas na descrição e não invente volume de busca. Hashtags na
  descrição são opcionais, no máximo três e nunca `#viral`/`#fyp`. A lane pré-aprovada
  `approved_fast_v1` é a exceção persistida: usa exatamente três hashtags específicas do vídeo
  mais `#biblia` e `#jesus` na descrição, todas congeladas junto com título e descrição no hash do
  roteiro. `metadata.topics` permanece separado e é gerado automaticamente com dez a doze tags
  distintas, relevantes e sem `#`; não precisa ser exibido na aprovação editorial.
- O comentário fixado formula uma pergunta específica ao tema ou passagem. O pacote registra
  `description_rationale`, `tag_strategy` e `discovery_hypothesis`; nunca afirma que SEO garante
  viralização. Para Shorts, trate escolha de assistir, duração média, percentual assistido e
  satisfação como sinais principais de distribuição.
- Métricas de Shorts usam `appeal_engagement_satisfaction_v1`: `Stayed to watch`/`Swiped away`
  medem apelo no feed, duração/percentual assistido medem engajamento e reações/compartilhamentos/
  inscritos apoiam satisfação. Nunca substitua a métrica do Studio por `engaged_views/views`.
  Snapshots são imutáveis; apelo exige ao menos 200 exposições e progressão exige ao menos 200
  `engaged_views`. Resultados em processamento ou cuja janela termina antes de 48 horas permanecem
  provisórios. Alertas só são resolvidos por evidência confirmada do mesmo estágio. Só advisories
  compatíveis por coorte de duração, hook, estilo e contrato de abertura entram em novas filas; um
  único Short nunca prova causalidade.
- Nunca implemente erro deliberado para provocar correção, comentário por conta alternativa,
  engajamento falso, medo inventado ou promessa vazia. Curiosidade precisa ser honesta e o payoff
  deve cumprir o mesmo loop aberto pelo hook.
- Subagentes nunca importam, aprovam, publicam nem alteram `project.json`, filas ou manifestos.

## Filas e retomada

- Em `images/codex-queue.json`, processe somente itens pendentes ou sem staging válido. Faça a
  primeira chamada com o prompt integral. Em `moderation_blocked`, repita exatamente o mesmo prompt
  uma vez; somente após o segundo bloqueio use uma alternativa da mesma cena, mudando ação, pose,
  câmera, enquadramento ou objetos não essenciais, sem trocar nem envelhecer o personagem.
- Copie cada ativo ao `staging_path`; não o deixe apenas em `$CODEX_HOME`. O agente principal faz
  `images import-batch` uma vez.
- Quando o job compacto declarar `common_prompt_prefix`, reconstrua cada prompt exatamente como
  `common_prompt_prefix + prompt_delta`; não duplique o prefixo nos jobs nem altere o texto.
- Roteiro visual compacto e metadados aceitam no máximo uma correção agregada do modelo por etapa.
  Se o preflight continuar inválido, execute o `fallback_command` declarado na fila e prossiga; não
  abra ciclos adicionais de revisão.
- Não regenere item importado/aprovado. Uma versão ativa pode entrar na prévia, mas segue sem
  aprovação humana.
- O `worker_id` é afinidade inicial; ao liberar, o trabalhador pode assumir o próximo job pendente
  do plano sem compartilhar staging.
- Consulte `run --dry-run` para diagnóstico e use `autopilot --wait-external` para execução e espera
  retomáveis no mesmo processo. Não abra rodadas do coordenador só para polling; quando uma
  integração precisar consultar estado, use `status --json`. Lane local e escrita de estado
  continuam serializadas; dentro da lane de imagens, workers podem gerar em paralelo com staging
  separado.
- Ao concluir, mantenha `audit/production-metrics.json` e a seção automática de `audit/report.md`
  atualizados com `audit tokens`; a atualização deve ser idempotente e preservar análise anterior.

## Produção remota com paridade local

- O executor remoto inicia uma sessão persistente do coordenador por job e a retoma nos checkpoints.
  Cada Short tem sua própria sessão raiz. O coordenador cria exatamente os mesmos especialistas do
  fluxo local, sempre com `fork_turns=none` e contexto compacto.
- Áudio, música, legendas, imports, render, validação, upload e agendamento são determinísticos e
  não devem ser entregues ao Codex. Hooks remotos apenas medem eventos/tamanhos e não escrevem em
  `stdout`.
- Depois que os ativos de imagem forem materializados, o coordenador de modelo encerra sua
  participação. O executor remoto conclui render, validação e upload sem polling de agente, grava
  um evento terminal persistido e só solicita atenção humana quando o job termina em `failed`.
- O upload remoto é idempotente por projeto e hash. Uma tentativa cujo resultado no YouTube fique
  indeterminado nunca é repetida automaticamente, evitando duplicata silenciosa.
- Coordenador e especialistas usam obrigatoriamente `gpt-5.6-sol`. O coordenador usa raciocínio
  médio; no compacto, o planejador visual e `youtube_growth_specialist` usam médio, sem roteirista
  nem revisor bíblico de modelo; cada `image_worker` usa baixo. Fora do compacto,
  `shorts_script_specialist` usa alto. Não crie `biblical_reviewer`. Use até dois workers de imagem em
  paralelo e importe o lote uma única vez.
- Agendas diárias materializam um ou dois jobs independentes por data e slot. Cada ocorrência deve
  ter `not_before`, upload inicial privado, `publication_mode=scheduled` e `publish_at` explícito
  com fuso. A materialização é idempotente e nunca reutiliza a sessão raiz de outro Short.
- Gere exatamente um título e os metadados em Shorts e vídeos longos. Não chame `imagegen` nessa
  etapa, não altere o primeiro frame e não envie imagem de capa personalizada.
- `render-review` funciona em produções locais e remotas, usa os ativos ativos em qualidade final e
  nunca registra aprovação humana. Um candidato assim pode ser enviado automaticamente somente como
  `private`, quando a política do projeto permitir e a validação técnica estiver concluída.
- Agendamento automático só é permitido quando o job original trouxer `publication_mode=scheduled`
  e `publish_at` explícito com fuso. Um job `private_review` termina após o upload privado.
- No modo remoto, o executor remoto faz upload/agendamento. No fluxo local, quando o usuário pedir
  explicitamente, o Codex pode usar os comandos guardados do pipeline depois da validação. Nunca leia
  ou imprima `.env`, token OAuth ou outros segredos.

## Padrão visual e render

- Shorts e vídeos longos usam exclusivamente `biblical-midcentury-gouache-v1`: ilustração editorial cristã de meados do
  século em guache opaco e têmpera, acabamento fosco, impressão offset limpa, formas simples, rostos
  serenos, luz natural difusa e paleta suavizada.
- O perfil pintado usa `unrestricted_scene_v1`: não acrescente regras para preservar rostos diante
  das legendas, esvaziar o centro vertical, afastar elementos das bordas direita/inferior ou evitar
  poses intermediárias. As cenas passam rapidamente e a composição artística pode ocupar o quadro.
- Shorts novos usam `opening_visual_standard=scroll_stop_concrete_v1`. O primeiro bloco mostra um
  sujeito ou objeto dominante, uma ação observável já em curso e tensão, escolha ou contraste
  coerente com o hook, preferindo plano médio, close ou objeto específico a plano geral
  contemplativo. No perfil pintado, isso não reintroduz área segura, centro vazio nem restrições de
  borda; apenas impede abertura genérica e faz imagem e fala abrirem a mesma pergunta. O contrato
  registra `action_kind`, `initial_state` e `resulting_state` concretos; esses campos dirigem o
  primeiro prompt. Em todos os formatos, `strategy.visual_scenes` é a autoridade e divide
  literalmente a narração em blocos contíguos com cenas concretas, distintas e criativas.
- Uma imagem mostra um momento, um ambiente e uma ação; sem colagem ou painéis.
- O único render é o candidato final V4: resolução completa, H.264, CRF 12, preset `slow`,
  supersampling 3. Não gere `draft-preview.mp4` nem `preview.mp4`.
- O candidato final usa `single_pass_filtergraph_v1`: movimento, blur, transições, legendas e
  mixagem entram em um único filtergraph, com exatamente uma codificação H.264 final. Não produza
  segmentos H.264 nem uma base H.264 intermediária.
- Movimento: `smooth_pendulum`, zoom 1,02×–1,105×, pan 0,32.
- Transições: `cinematic_mix`, 0,38 s.
- Blur de fundo padrão: sigma 8 uniforme em todas as cenas.
- Legendas: Montserrat ExtraBold, caixa alta, escala 88%, largura 0,68, até três palavras por linha
  e card ativo vermelho `#E5484D` com raio 24, sempre centralizadas.
- As palavras da legenda entram progressivamente com animação curta de posição, escala e opacidade,
  preservando fonte, layout e card ativo.
- A voz ElevenLabs padrão é o preset `jesus_1` (`qfOmqDJTQP75MDYJvxKc`); `jesus_2`
  (`rtrpEL7BeMd5aTru4HPq`) é a segunda variação. O modelo padrão é `eleven_multilingual_v2`;
  overrides explícitos do projeto continuam válidos.
- A resposta pedida nos comentários fica entre aspas, sozinha na legenda, e permanece visível
  durante um segundo de silêncio antes da continuação.
- Em Shorts, a resposta pedida nos comentários deve ser uma palavra-síntese expressiva e contextual,
  com ao menos seis letras quando for uma única palavra. Prefira densidade semântica como REFÚGIO,
  PERSISTÊNCIA, CONFIANÇA, FIDELIDADE, ESPERANÇA, PERSEVERANÇA, RECOMEÇO, DESCANSO ou CORAGEM,
  derivada da resolução; nunca use isoladamente BEM, SIM, AMÉM, EU, AQUI, PERTO, FÉ, PAZ, FORÇA
  ou FIEL.
- Projetos novos usam `voice_music_loudness_v1`: a narração é normalizada para -18 LUFS e a
  música fica 13 LU abaixo dela (-31 LUFS por padrão), com limites de true peak antes do `amix`.
  `narration_gain_db` e `background_music_gain_db` são apenas trims explícitos; não substituem a
  normalização. Projetos legados preservam o padrão salvo.
- O perfil `adaptive_compact_v1` usa exclusivamente `music-library/adaptive-compact-v1/Aloboi .mp3`
  e `music-library/adaptive-compact-v1/Leaning.mp3`; não misture essa biblioteca com a biblioteca
  geral de outros formatos.
- A validação e o manifesto da música registram `audio_mix_standard`, alvo da voz, alvo da música e
  diferença em LU; divergência bloqueia o candidato final.

## Convenções técnicas

- Python 3.12 e `uv`; schemas Pydantic em `src/pipeline_videos/models.py`.
- JSON atômico, caminhos relativos, subprocessos com lista de argumentos e `shell=False`.
- FFmpeg + libass continuam sendo o renderizador; não introduza UI/Remotion sem necessidade.
- Mudança apenas de legenda reutiliza a base visual.
- Use hashes; `--force` somente por decisão deliberada; preserve versões e eventos.
- Para alterar uma cena, prefira `--scene scene-NNN`; mudanças de configuração deixam dependências
  `stale` ou são invalidadas pelo novo hash.
- Adicione testes para lógica determinística e finalize com Ruff + pytest.

## Interface futura

Uma UI futura deve chamar o núcleo existente e respeitar schemas, aprovações, guards, custos e
estado; não duplique a lógica do pipeline na camada visual.
