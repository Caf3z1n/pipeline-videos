# Instruções persistentes para o pipeline de vídeos

## Antes de agir

1. Leia este arquivo e o `README.md` da raiz.
2. Para vídeo existente, leia primeiro `<projeto>/project.json`.
3. Leia depois briefing, fontes, decisões, roteiro, revisão bíblica e somente manifestos existentes.
4. Execute `uv run pipeline-videos status <id-ou-pasta>` antes de regenerar qualquer artefato.
5. A pasta do vídeo é a fonte de verdade; o chat é auxiliar.
6. Para criar, retomar ou renderizar vídeo, leia `docs/optimized-production.md`.
7. Para roteiro de Short, leia também `docs/shorts-script-standard.md`.
8. Para roteiro de vídeo longo, leia também `docs/long-form-script-standard.md`.

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
- ElevenLabs exige pedido do usuário e `--allow-external`.
- Nunca exiba, registre ou versione chaves e tokens.
- Não substitua a geração embutida por API paga sem aprovação explícita.
- Não sobrescreva imagem aprovada; gere nova versão e peça aprovação.

## Precisão bíblica

- Não invente versículo, referência, citação ou detalhe histórico.
- Na locução, verbalize: “Livro, capítulo X, versículo Y”; formato compacto fica nos metadados.
- Short cita literalmente ao menos um versículo verificado, curto e claro.
- Apresente a referência fora das aspas: `Em Livro, capítulo X, versículo Y, lemos: “texto.”`
- Registre tradução, fonte, acesso e direitos de citação literal.
- Classifique afirmações como `biblical_text`, `verified_context`, `interpretation` ou
  `creative_elaboration`.
- Preserve incertezas; finding `blocking` aberto impede aprovação.
- Nunca invente nem registre aprovação humana em nome do usuário. Para upload privado solicitado
  explicitamente, use o candidato automatizado validado em vez de preencher aprovações.
- A locução permanece integralmente em português: não narre `open loop`, `payoff`, `storytelling`,
  `call to action` ou outro jargão editorial em inglês.
- Não diga “o texto fala/diz/registra”. Prefira “a Bíblia diz” ou “Em Livro, capítulo X,
  versículo Y, lemos:”.
- Em vídeo longo, use citações literais verificadas distribuídas pela narrativa: alvo de 0,8 a 1,2
  por minuto; dez minutos exigem de oito a doze citações. No padrão novo, a primeira entra até 75
  segundos, o intervalo máximo é de 105 segundos e a parte final não fica mais de 120 segundos sem
  âncora bíblica; não agrupe versículos apenas para cumprir quantidade.

## Produção otimizada

- O agente principal é o único escritor de estado compartilhado.
- Workers escrevem somente no staging declarado e recebem contexto compacto com
  `fork_turns=none`.
- Use no máximo dois trabalhadores de imagem simultaneamente. Em Shorts novos, inicie o
  especialista do YouTube somente depois de importar a imagem ativa da abertura. Vídeos longos usam
  somente o tema central no prompt e não dependem dessa referência.
- Imagens usam uma chamada `imagegen` por tentativa. Em `moderation_blocked`, repita o mesmo prompt
  uma vez; se bloquear novamente, tente uma única alternativa de cena preservando identidade, idade
  aparente, rosto, cabelo, figurino, proporções, silhueta, estilo, paleta, ambiente e sentido.
- Não faça inspeção semântica cena a cena no one-shot; entregue a prévia completa ao usuário.
- Imagens ativas podem entrar no rascunho, mas permanecem não aprovadas.
- Imports de imagem e crescimento acontecem uma única vez no join.

## Criação e modos de Shorts

- Pedido simples de Short usa `new-short`, seguido de `status`, `run --dry-run` e one-shot; não peça
  novamente versículo, CTA, estilo ou fluxo já padronizados.
- O seletor automático usa o catálogo local verificado, combina as pastas com o registro Git
  compartilhado, reserva por compare-and-swap antes de criar o projeto e favorece variedade de
  livro, tema, personagem e testamento. Falha do registro bloqueia a seleção; não use fallback local.
- O briefing automático não equivale a aprovação humana.
- No modo completo, prepare `script/shorts-queue.json` e crie exatamente um
  `shorts_script_specialist`, com raciocínio alto e staging exclusivo; o agente principal valida e
  importa uma única vez com `script shorts-import`.
- `new-short-audio-manual` mantém o fluxo completo, mas usa `audio.provider=local_manual`: mostre
  integralmente `script/narration.txt`, aguarde um único arquivo de voz do usuário e importe com
  `audio import-manual`. O alinhamento usa WhisperX isolado em CPU, sem ElevenLabs e sem
  `--allow-external`; a primeira execução pode baixar o pacote e o modelo português, depois usa o
  cache local.
- Voz real usa ElevenLabs e exige `--allow-external`; `mock` é apenas para demonstrações e testes.
- Voz anexada usa somente áudio limpo, sem música ou efeitos, e deve reproduzir exatamente a
  locução; divergência ou palavra sem timestamp bloqueia a importação em vez de simular tempos.

## Contratos dos especialistas

- O roteirista de Shorts novos usa `retention_story_v2`: cold open concreto, progressão,
  citação literal entre 8 e 20 segundos, reengajamento, payoff, aplicação, callback e exatamente
  uma ação de CTA em até quatro segundos, conforme `docs/shorts-script-standard.md`.
- O roteirista de vídeos longos novos usa `retention_story_long_v1`: conflito e promessa em até 20
  segundos, pergunta central, progressão por movimentos, reengajamentos proporcionais à duração,
  citações literais periódicas, payoff no quarto final, aplicação, callback e exatamente uma ação
  de CTA em até dez segundos, conforme `docs/long-form-script-standard.md`.
- Crie exatamente um `youtube_growth_specialist` quando `youtube/growth-queue.json` estiver
  pendente, localmente ou na VPS, com raciocínio médio. Nenhum especialista publica nem promete
  CTR/alcance.
- Em projetos novos de qualquer formato, o especialista gera exatamente uma combinação
  título+thumbnail com `imagegen`. Em Shorts, ele edita diretamente a imagem ativa da abertura,
  declarada na fila como referência obrigatória. Texto, palavra vermelha e
  seta/X/sublinhado/pincel devem nascer incorporados ao bitmap, com grafia exata e integração à
  luz, profundidade e composição. Não existe compositor local nem fallback de pacote legado.
- Em vídeos longos novos, gere a thumbnail com `biblical_cinematic_landscape_v1`: horizontal 16:9,
  baseada exclusivamente no tema central do briefing, sem imagem de referência e sem texto, letras,
  logotipos, marcas-d'água, seta, X, sublinhado, pincel ou outra marcação editorial. O personagem
  ocupa 30% a 40% do quadro e o lado oposto preserva área limpa.
- Thumbnails de Shorts usam conflito visual simples, emoção legível, assunto dominante, espaço
  negativo e de duas a cinco palavras; setas e marcações exigem alvo narrativo real.
- A tipografia padrão das thumbnails de Shorts é `integrated_extruded_3d_v1`: letras extrudadas com volume
  espesso visível, bordas chanfradas, textura material, highlights, sombras de contato/oclusão e
  perspectiva coerentes com a cena. A palavra dominante usa escala muito maior; palavra vermelha e
  seta/X/sublinhado/pincel preservam o mesmo tratamento 3D. Nunca use texto plano, adesivo ou overlay.
- Shorts e vídeos longos novos seguem `Tensão concreta | Passagem`. A descrição abre com resumo
  curto e, em vídeos longos,
  traz timeline das citações iniciada exatamente por `00:00 - Abertura`.
- O especialista cria exatamente um par título+thumbnail. O título carrega referência, tensão e
  busca. Em Shorts, a frase da capa acrescenta decisão, contraste ou consequência. No padrão
  `tension_pipe_passage_v2`, a capa de Short não repete a tensão situada antes de `|`; projetos
  legados com passagem primeiro continuam sem repetir a frase temática depois de `|`. Em longos, o
  conceito visual complementa o título sem texto no bitmap.
- Thumbnails novas de Shorts usam conflito/personagem reconhecível, destaque vermelho e ao menos uma
  seta, X, sublinhado ou pincel com alvo real; evite frases abstratas quando o confronto for mais forte.
- Planeje espaço negativo real e tente não cobrir rosto, corpo, ação principal ou o alvo narrativo.
  Depois da primeira saída válida, aceite o bitmap sem repetir por qualidade, deriva visual ou texto
  imperfeito; tentativas adicionais são permitidas somente quando `moderation_blocked` não gerar
  arquivo.
- Subagentes nunca importam, aprovam, publicam nem alteram `project.json`, filas ou manifestos.

## Filas e retomada

- Em `images/codex-queue.json`, processe somente itens pendentes ou sem staging válido. Faça a
  primeira chamada com o prompt integral. Em `moderation_blocked`, repita exatamente o mesmo prompt
  uma vez; somente após o segundo bloqueio use uma alternativa da mesma cena, mudando ação, pose,
  câmera, enquadramento ou objetos não essenciais, sem trocar nem envelhecer o personagem.
- Copie cada ativo ao `staging_path`; não o deixe apenas em `$CODEX_HOME`. O agente principal faz
  `images import-batch` uma vez.
- Não regenere item importado/aprovado. Uma versão ativa pode entrar na prévia, mas segue sem
  aprovação humana.
- O `worker_id` é afinidade inicial; ao liberar, o trabalhador pode assumir o próximo job pendente
  do plano sem compartilhar staging.
- Consulte `run --dry-run` para diagnóstico e use `autopilot` para execução retomável. Lane local e
  escrita de estado continuam serializadas; dentro da lane de imagens, workers podem gerar em
  paralelo com staging separado.

## Produção remota com paridade local

- O executor remoto inicia uma sessão persistente do coordenador por job e a retoma nos checkpoints.
  Cada Short tem sua própria sessão raiz. O coordenador cria exatamente os mesmos especialistas do
  fluxo local, sempre com `fork_turns=none` e contexto compacto.
- Áudio, música, legendas, imports, render, validação, upload e agendamento são determinísticos e
  não devem ser entregues ao Codex. Hooks remotos apenas medem eventos/tamanhos e não escrevem em
  `stdout`.
- Coordenador e especialistas usam obrigatoriamente `gpt-5.6-sol`. O coordenador usa raciocínio
  médio; `shorts_script_specialist`, alto; cada `image_worker`, baixo; e
  `youtube_growth_specialist`, médio. Use até dois workers de imagem em paralelo e importe o lote
  uma única vez.
- Agendas diárias materializam um ou dois jobs independentes por data e slot. Cada ocorrência deve
  ter `not_before`, upload inicial privado, `publication_mode=scheduled` e `publish_at` explícito
  com fuso. A materialização é idempotente e nunca reutiliza a sessão raiz de outro Short.
- Gere exatamente um título e uma thumbnail. Em Shorts, edite a imagem ativa da abertura com
  `imagegen`; essa mesma arte ocupa exatamente o primeiro frame. Em vídeos longos, gere a composição
  horizontal somente a partir do prompt temático e envie-a pela API como thumbnail personalizada.
- A thumbnail de Short deve usar exatamente o mesmo `visual_standard`, estilo 3D, materiais,
  desenho de personagem, paleta e iluminação das cenas. No remoto, use obrigatoriamente a
  imagem produzida para a cena de abertura como referência visual e interrompa a etapa se ela ainda
  não existir; nunca improvise fotografia, fotorrealismo ou live action.
- Local e remoto usam `integrated_extruded_3d_v1` para o texto da capa de Shorts, com o mesmo volume, bevel,
  material, luz, perspectiva, sombra e hierarquia por escala definidos na fila de crescimento.
- `render-review` funciona em produções locais e remotas, usa os ativos ativos em qualidade final e
  nunca registra aprovação humana. Um candidato assim pode ser enviado automaticamente somente como
  `private`, quando a política do projeto permitir e a validação técnica estiver concluída.
- Agendamento automático só é permitido quando o job original trouxer `publication_mode=scheduled`
  e `publish_at` explícito com fuso. Um job `private_review` termina após o upload privado.
- No modo remoto, o executor remoto faz upload/agendamento. No fluxo local, quando o usuário pedir
  explicitamente, o Codex pode usar os comandos guardados do pipeline depois da validação. Nunca leia
  ou imprima `.env`, token OAuth ou outros segredos.

## Padrão visual e render

- Imagens: `cinematic-family-3d-v1`, animação 3D familiar cinematográfica premium, expressiva e
  reverente; nunca fotorrealismo, live action, flat, anime, low-poly ou boneco de plástico.
- Uma imagem mostra um momento, um ambiente e uma ação; sem colagem ou painéis.
- Prévia nova: resolução completa, H.264, CRF 16, preset `fast`, supersampling 2.
- Final aprovado V4: resolução completa, H.264, CRF 12, preset `slow`, supersampling 3.
- Movimento: `smooth_pendulum`, zoom 1,02×–1,105×, pan 0,32.
- Transições: `cinematic_mix`, 0,38 s.
- Blur de fundo padrão: sigma 8 uniforme em todas as cenas; em Shorts, somente o primeiro frame
  integrado da thumbnail permanece totalmente nítido.
- Legendas: Montserrat ExtraBold, caixa alta, escala 88%, largura 0,68, até três palavras por linha
  e card ativo vermelho `#E5484D` com raio 24, sempre centralizadas.
- As palavras da legenda entram progressivamente com animação curta de posição, escala e opacidade,
  preservando fonte, layout e card ativo.
- A voz ElevenLabs padrão é `wKukuyYdIlElkaj9zEQG`; overrides explícitos do projeto continuam
  válidos.
- A resposta pedida nos comentários fica entre aspas, sozinha na legenda, e permanece visível
  durante um segundo de silêncio antes da continuação.
- Em Shorts, a resposta pedida nos comentários deve ser uma palavra-síntese expressiva e contextual,
  com ao menos seis letras quando for uma única palavra. Prefira densidade semântica como REFÚGIO,
  PERSISTÊNCIA, CONFIANÇA, FIDELIDADE, ESPERANÇA, PERSEVERANÇA, RECOMEÇO, DESCANSO ou CORAGEM,
  derivada da resolução; nunca use isoladamente BEM, SIM, AMÉM, EU, AQUI, PERTO, FÉ, PAZ, FORÇA
  ou FIEL.
- Música é sorteada da biblioteca local, persistida, inicia em -12 dB e preserva a narração.

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
