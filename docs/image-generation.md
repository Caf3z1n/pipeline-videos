# Geração de imagens no Codex

## Limite real do ambiente

A geração embutida disponível na conversa não expõe um endpoint local para o processo Python. Ela
funciona sem `OPENAI_API_KEY`, mas somente o agente Codex pode fazer a chamada. O CLI, portanto, não
promete automação que não existe e não troca silenciosamente para uma API cobrada.

## Handoff retomável

`images prepare --provider codex` produz `images/codex-queue.json`. Cada item contém:

- scene ID e image ID;
- prompt modular final;
- hash do prompt;
- versão reservada;
- destino esperado;
- status e regra de não sobrescrever aprovado.

A fila também registra `visual_standard`. O padrão obrigatório atual é
`cinematic-family-3d-v1`. Os trabalhadores devem preservar uma animação 3D cinematográfica familiar
premium, no espírito do apelo emocional de longas Pixar/Disney, sem copiar personagens existentes:
personagens carismáticos e expressivos, formas arredondadas, materiais estilizados, profundidade em
camadas, cores ricas e iluminação de longa-metragem. Cena épica não autoriza converter o resultado
em fotorrealismo, live action, vetor flat, anime, low-poly, boneco de plástico ou render 3D genérico.

Cada item representa uma única composição contínua: um momento, um ambiente e uma ação principal.
Não use colagem, storyboard, díptico, tríptico, tela dividida, cenas empilhadas ou divisórias. A
área central das legendas deve continuar sendo parte do mesmo fundo, com poucos detalhes e baixo
contraste; nunca uma faixa branca ou um retângulo vazio. A importação rejeita deterministicamente
grandes faixas claras com bordas abruptas que indiquem separação entre painéis.

O agente gera uma imagem por item, move/copia o resultado para o workspace e executa `images import`.
Uma falha deixa o item pendente. Reexecutar a fila ignora itens já importados.

No modo `final_candidate_one_shot`, a fila inclui `batch_id`, `job_id`, `worker_id` e `staging_path`. Quando o
especialista de crescimento está ativo, até dois trabalhadores geram imagens e a terceira vaga fica
reservada para ele. `worker_id` define a afinidade inicial; o planejador read-only entrega o próximo
job ordenado ao primeiro trabalhador livre. Cada job possui staging exclusivo. O agente principal
permanece como escritor único de `project.json`, manifestos e filas e executa `images import-batch`
uma vez, após o join. Essa separação evita perda de atualizações por operações read-modify-write
concorrentes.

Nos projetos novos com `token_optimized_v1`, cada trabalhador lê somente o arquivo compacto
`images/jobs/image-worker-N.json`, inicia sem herdar turnos da conversa (`fork_turns=none`) e usa
raciocínio baixo para a chamada de imagem. A fila completa permanece como contrato do orquestrador,
mas deixa de ser reenviada integralmente a todos os trabalhadores. Isso reduz tokens de entrada sem
reduzir o prompt visual de cada cena.

O comando `pipeline-videos autopilot <projeto>` prepara a fila e continua a lane local. Se as imagens
ainda não estiverem no staging, ele registra um checkpoint retomável; após os workers terminarem,
uma nova execução importa o lote uma única vez e prossegue automaticamente.

`active_version` é a seleção usada no candidato final. `approved_version` continua vazio até a
decisão do usuário, portanto produção técnica não é confundida com aprovação editorial.

## Continuidade

`visual/continuity.json` guarda fichas de personagens, roupas, proporções, ambientes, invariantes e
imagens-âncora. O prompt de cada cena repete apenas as regras relevantes. Imagens aprovadas podem ser
anexadas como referência em gerações posteriores quando a ferramenta suportar isso.

## Perfil e paleta

O perfil `cinematic-family-3d-animation` implementa o padrão versionado
`cinematic-family-3d-v1` e continua independente da paleta. O perfil antigo
`minimalist-cinematic-flat-vector` permanece disponível para projetos que o selecionem
explicitamente com a trava global desativada. O projeto inclui paletas de noite azul/dourada,
amanhecer quente, deserto terroso e crepúsculo rosado. Trocar uma paleta marca somente as etapas
visuais dependentes como stale.

Projetos novos mantêm `config.images.enforce_visual_standard=true`. Antes de construir cenas, o
pipeline compara `visual/style-profile.json` com o perfil canônico. Se encontrar um override ou o
padrão flat legado, arquiva a versão em `visual/versions/`, migra o identificador do projeto e
restaura o padrão 3D. Uma
troca explícita por outro `set-style` desativa a trava para respeitar a escolha deliberada.
Filas antigas não podem ser preparadas com prompts anteriores ao padrão: primeiro reconstrua as
cenas para que cada prompt carregue o identificador e as restrições visuais atuais.
