# Instruções persistentes para o pipeline de vídeos

## Antes de agir

1. Leia este arquivo e o `README.md` da raiz.
2. Para vídeo existente, leia primeiro `<projeto>/project.json`.
3. Leia depois briefing, fontes, decisões, roteiro, revisão bíblica e somente manifestos existentes.
4. Execute `uv run pipeline-videos status <id-ou-pasta>` antes de regenerar qualquer artefato.
5. A pasta do vídeo é a fonte de verdade; o chat é auxiliar.
6. Para criar, retomar ou renderizar vídeo, leia `docs/optimized-production.md`.
7. Para roteiro de Short, leia também `docs/shorts-script-standard.md`.

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
  por minuto; dez minutos exigem de oito a doze citações.

## Produção otimizada

- O agente principal é o único escritor de estado compartilhado.
- Workers escrevem somente no staging declarado e recebem contexto compacto com
  `fork_turns=none`.
- Use no máximo dois trabalhadores de imagem e um especialista do YouTube simultaneamente.
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
- `new-short-light-manual` e `new-short-light-automatica` são modos aditivos e não alteram
  `new-short`/`draft_one_shot`. Neles, o agente principal faz o roteiro com raciocínio baixo e cria
  exatamente um especialista de crescimento com raciocínio baixo.
- `new-short-audio-manual` mantém o fluxo completo, mas usa `audio.provider=local_manual`: mostre
  integralmente `script/narration.txt`, aguarde um único arquivo de voz do usuário e importe com
  `audio import-manual`. O alinhamento usa WhisperX isolado em CPU, sem ElevenLabs e sem
  `--allow-external`; a primeira execução pode baixar o pacote e o modelo português, depois usa o
  cache local.
- Light manual prepara prompts em partes de até dez imagens e para até o usuário anexá-las; importe
  tudo na ordem com `images import-manual`. Light automático usa até dois image workers e importa o
  lote uma única vez.
- Voz real usa ElevenLabs e exige `--allow-external`; `mock` é apenas para demonstrações e testes.
- Voz anexada usa somente áudio limpo, sem música ou efeitos, e deve reproduzir exatamente a
  locução; divergência ou palavra sem timestamp bloqueia a importação em vez de simular tempos.

## Contratos dos especialistas

- O roteirista de Shorts produz gancho humano imediato, curiosidade honesta, contexto rápido,
  citação bíblica, resolução, aplicação, palavra específica para comentar e CTA completo, conforme
  `docs/shorts-script-standard.md`.
- Crie exatamente um `youtube_growth_specialist` quando `youtube/growth-queue.json` estiver
  pendente: raciocínio médio no completo e baixo no Light. Ele gera títulos, descrição, estratégia
  e designs, nunca publica nem promete CTR/alcance.
- Em projetos novos de qualquer formato, inclusive Shorts, o especialista gera três thumbnails
  finais com `imagegen`, sob a mesma política limitada de retry por candidata. A exceção aditiva é
  `remote_review`, que gera
  exatamente uma combinação título+thumbnail para economizar uso. Texto, palavra vermelha e
  seta/X/sublinhado/pincel devem nascer incorporados ao bitmap, com grafia exata e integração à
  luz, profundidade e composição. O compositor local é somente fallback para projetos legados.
- Thumbnails usam conflito visual simples, emoção legível, assunto dominante, espaço negativo e de
  duas a cinco palavras; setas e marcações exigem alvo narrativo real.
- Títulos novos seguem `Passagem principal | Tema principal`. A descrição abre com resumo curto e,
  em vídeos longos, traz timeline das citações iniciada exatamente por `00:00 - Abertura`.
- O especialista cria a quantidade de pares título+thumbnail declarada na fila: três no fluxo
  padrão e exatamente um em `remote_review`. O título carrega referência, tema e busca; a frase da
  capa acrescenta tensão, decisão, contraste ou consequência. A capa nunca repete a frase temática
  situada depois de `|` no título, embora possa compartilhar nome próprio ou uma palavra-chave
  necessária.
- Thumbnails novas usam conflito/personagem reconhecível, destaque vermelho e ao menos uma seta, X,
  sublinhado ou pincel com alvo real; evite frases abstratas quando o confronto for mais forte.
- Nunca aceite texto ou símbolo flutuando sobre o personagem. Planeje espaço negativo real e não
  cubra rosto, corpo, ação principal ou o alvo narrativo.
- Subagentes nunca importam, aprovam, publicam nem alteram `project.json`, filas ou manifestos.

## Filas e retomada

- Em `images/codex-queue.json`, processe somente itens pendentes ou sem staging válido. Faça a
  primeira chamada com o prompt integral. Em `moderation_blocked`, repita exatamente o mesmo prompt
  uma vez; somente após o segundo bloqueio use uma alternativa da mesma cena, mudando ação, pose,
  câmera, enquadramento ou objetos não essenciais, sem trocar nem envelhecer o personagem.
- Copie cada ativo ao `staging_path`; não o deixe apenas em `$CODEX_HOME`. O agente principal faz
  `images import-batch` uma vez. Em fluxo manual, use o comando de importação declarado.
- Não regenere item importado/aprovado. Uma versão ativa pode entrar na prévia, mas segue sem
  aprovação humana.
- O `worker_id` é afinidade inicial; ao liberar, o trabalhador pode assumir o próximo job pendente
  do plano sem compartilhar staging.
- Consulte `run --dry-run` para diagnóstico e use `autopilot` para execução retomável. Lane local e
  escrita de estado continuam serializadas mesmo quando geração e crescimento rodam em paralelo.

## Produção remota econômica

- O executor remoto divide o trabalho em `codex exec --ephemeral` isolados: seleção editorial,
  roteiro, uma imagem por execução e crescimento. Cada fase lê somente seu JSON compacto e os
  `allowed_inputs`; nunca peça que uma fase releia README, AGENTS, histórico ou o projeto inteiro.
- Áudio, música, legendas, imports, render, validação, upload e agendamento são determinísticos e
  não devem ser entregues ao Codex. Hooks remotos apenas medem eventos/tamanhos e não escrevem em
  `stdout`.
- `remote_review` é aditivo e processa roteiro, imagens e crescimento no agente principal, sem
  criar subagentes. Respeite as filas e os mesmos staging paths, mas faça as chamadas de `imagegen`
  sequencialmente e importe cada lote uma única vez.
- Gere exatamente um título e uma thumbnail integrada. Em Shorts, essa mesma arte ocupa exatamente
  o primeiro frame; em vídeos longos, ela é enviada pela API como thumbnail personalizada.
- A thumbnail integrada deve usar exatamente o mesmo `visual_standard`, estilo 3D, materiais,
  desenho de personagem, paleta e iluminação das cenas. Em `remote_review`, use obrigatoriamente a
  imagem produzida para a cena de abertura como referência visual e interrompa a etapa se ela ainda
  não existir; nunca improvise fotografia, fotorrealismo ou live action.
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
- Legendas: Montserrat ExtraBold, caixa alta, escala 88%, largura 0,68, até três palavras por linha
  e card ativo vermelho `#E5484D` com raio 24, sempre centralizadas.
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
