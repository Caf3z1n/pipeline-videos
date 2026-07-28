# Padrão de roteiro para vídeos longos

## Linguagem do perfil cutout

`biblical_cutout_explainer_7m_v1` acrescenta
`conversational_concrete_ptbr_v1` ao contrato geral:

- use `biblical_explainer_third_person_v1`: narrador explicativo em terceira pessoa, sem escrever
  como se Jesus estivesse narrando;
- use português falado, direto e fluido;
- identifique sujeito, ação e consequência concreta;
- prefira verbos visuais que possam orientar a edição;
- rejeite abstrações passivas como “a ordem foi deslocada” ou “a consequência foi contradita”;
- não use “isso muda tudo” sem explicar imediatamente o que mudou;
- mantenha o hook honesto, específico e visualizável nos primeiros trinta segundos.

Nesse perfil, o roteiro entrega uma decisão editorial completa em uma única saída: locução,
blueprint dos três tipos de bloco, cues semânticos, sequências fullscreen, título, descrição e
conceito de thumbnail. Depois do áudio e do alinhamento, um compilador local converte essas
decisões em tempos e caixas. Não existe uma segunda interpretação de modelo, editor visual
separado ou loop de reparo por bloco.

O roteiro e `script/narration.txt` nunca contêm tags de síntese. O áudio cria
`audio/tts-prompt.json` com `eleven_v3_strategic_tags_v1`, preservando todas as palavras e
acrescentando apenas direções auditivas contextuais.

## Escopo

Vídeos longos novos usam `creative_retention_long_v2`. O modo criativo define a experiência:
storytelling factual, retrato de personagem, explicação, curiosidade, contexto histórico,
reflexão filosófica, investigação temática, comparação, dilema, exploração literária,
reconstrução cronológica, aplicação devocional ou outra direção do catálogo.

O padrão `retention_story_long_v1` continua válido para projetos existentes. O contrato salvo no
`project.json` é a autoridade e não existe migração automática.

Consulte também [creative-direction.md](creative-direction.md).
O perfil cutout aplica diretamente [magnetic-long-form-engine.md](magnetic-long-form-engine.md);
esse material orienta a única saída do roteiro e não cria uma etapa posterior de revisão.

## Estrutura adaptativa

A duração continua definida pelo projeto. O roteiro ocupa de 85% a 112% da duração-alvo,
considerando 145 palavras por minuto.

- **Abertura:** `opening_line` é o prefixo literal da locução. Sem saudação, apresentação do
  canal ou “no vídeo de hoje”.
- **Hook adaptativo:** `hook_policy` pode ser `required`, `optional` ou `none`. Com hook, o primeiro
  beat termina em até 20 segundos. Sem hook, ele usa `orientation` e entrega valor em até 40
  segundos.
- **Pergunta magnética:** quando houver hook, abra uma lacuna de informação concreta: uma
  contradição aparente, consequência visível, decisão humana ou detalhe bíblico que o espectador
  consiga formular em uma frase. Prometa somente a resposta que o roteiro realmente entregará.
- **Promessa:** `content_promise` fica clara nos dois primeiros movimentos. Pergunta central é
  opcional.
- **Desenvolvimento:** cada seção acrescenta fato, evidência, contexto, ação, consequência,
  comparação, argumento, objeção, revelação ou mudança de entendimento.
- **Progressão causal:** sempre que possível, organize os movimentos como acontecimento,
  pergunta, evidência, implicação e resposta. Cada movimento precisa alterar o que o espectador
  entende, e não apenas repetir o tema com outras palavras.
- **Reengajamentos:** introduzem valor novo antes que a progressão fique previsível. O intervalo
  máximo é de 120 segundos e a quantidade mínima cresce com a duração.
- **Resolução:** pode ser distribuída, tardia ou reservada ao quarto final. Entrega o valor
  prometido como desfecho factual, síntese, posição, incerteza preservada ou aplicação.
- **CTA:** pede exatamente uma ação — comentar, inscrever-se ou compartilhar — em até 24 palavras
  ou dez segundos, depois da conclusão.

Aplicação prática só é obrigatória em `devotional_application` e `pastoral_encouragement`.
Callback é sempre opcional no v2. Os três arquétipos antigos não são preenchidos no novo
contrato; a estrutura vem do `creative_mode`.

## Voz autoral em todos os modos

Todo roteiro longo usa `human_authorial_voice_v1`, inclusive curiosidades, explicações, reflexões,
investigações e formatos legados quando forem reprocessados. A locução não pode comentar o método
de leitura com frases como “o relato registra”, “a sequência é compacta” ou “não podemos afirmar”,
nem construir valor por listas do que não aconteceu ou pela fórmula `não A nem B, mas C`. O
validador exige que `C` seja declarado diretamente e examina apenas a voz autoral: citações
bíblicas literais permanecem intactas.

## Storytelling bíblico de personagem

`biblical_character_storytelling_v1` é o padrão dos novos pedidos de história de personagem. Ele
usa `creative_mode=biblical_storytelling` e postura factual, mas factualidade não significa narrar
uma auditoria do texto. A voz acompanha a pessoa por uma sequência compreensível: mundo inicial,
encontro ou virada, escolha, consequência, caminho posterior documentado e último lugar bíblico
conhecido.

- a maior parte das seções de desenvolvimento usa `biblical_narrative`;
- beats de `analysis` e `comparison` são inválidos nesse perfil;
- passagens paralelas só entram quando acrescentam uma etapa ou consequência, não para comparar
  redações do mesmo momento;
- citações são incorporadas à história e seguidas imediatamente por ação ou consequência;
- detalhes sem fonte são omitidos silenciosamente; a revisão registra a cautela fora da locução;
- além do filtro autoral global, títulos de seção metalinguísticos e beats de análise/comparação
  também são bloqueados neste perfil narrativo.

`character_portrait` continua disponível por seleção explícita quando o objetivo for análise de
traços, limites ou contrastes, e não uma biografia bíblica breve.

## Hooks longos

Quando o tema pede hook, a família escolhida precisa corresponder à fala real. As formas
prioritárias incluem:

1. `Se você quer [X], faça isso.`
2. `O que acontece se você parar de [X]?`
3. `Se você ainda faz [X], está perdendo [Y].`
4. `Ninguém fala sobre [X], mas...`
5. `Quer aprender [X]? Comece...`
6. `Nunca faça [X].`

Também são válidos começar em cena, abrir por um fato verificável, uma pergunta específica,
uma contradição, a consequência antes da causa ou uma citação curta. Em narrativa factual,
reconstrução cronológica, exploração literária ou abertura contemplativa, `hook_family=none`
pode ser a escolha correta.

## Cadência bíblica preservada

O alvo continua sendo de 0,8 a 1,2 citação literal por minuto. Um vídeo de dez minutos usa de oito
a doze citações verificadas.

- a primeira citação entra até 75 segundos;
- citações consecutivas ficam separadas por pelo menos 25 segundos;
- a narrativa não passa mais de 105 segundos sem nova citação;
- a parte final não fica mais de 120 segundos sem âncora bíblica;
- cada citação cumpre função narrativa ou argumentativa real;
- versículos não são agrupados apenas para cumprir quantidade.

A referência fica fora das aspas. Ela pode aparecer antes da citação, como `Em Livro, capítulo X,
versículo Y: “texto literal verificado.”`, ou depois, como `“texto literal verificado.” Livro,
capítulo X, versículo Y.`. Use a ordem que tornar a fala mais natural e variar a cadência. Não
repita automaticamente “lemos”.

Cada citação registra `biblical_basis`, classifica a afirmação como `biblical_text` e aponta para a
fonte verificada correspondente em `scripture_anchors`.

O segmento `long_form_anchor` acrescenta 100 passagens curadas, equilibradas entre Antigo e Novo
Testamento e marcadas com `recommended_formats=["long"]`. Na fonte persistida, `passage` corresponde
exatamente à referência do trecho literal usado na locução; `context_passage` preserva a janela
maior para estudo e desenvolvimento. Essa separação impede que o validador confunda contexto com a
citação e também evita que âncoras longas entrem na seleção automática de Shorts.

## Contrato estruturado

`ScriptDocument.long_form_strategy` registra:

- modo criativo, postura, política e família de hook;
- público, necessidade, promessa, primeira fala e justificativa da abertura;
- pergunta organizadora somente quando usada;
- um beat para cada seção, em ordem;
- valor, informação e progressão visual novos em cada beat;
- reengajamentos e uma âncora para cada citação literal;
- tempo de resolução, resolução e valor final;
- aplicação e callback opcionais;
- exatamente uma ação de CTA.

No perfil cutout, a mesma estratégia registra também:

- `visual_scenes` como partição literal, contígua e completa da narração;
- `presentation` resolvida em cada bloco, sem deixar a escolha para outro agente;
- de um a três `asset_cues` com frase literal, descrição concreta e reutilização explícita;
- de dois a quatro `fullscreen_shots` com ações ou estágios visualmente distintos;
- até duas `emphasis_terms` relevantes por bloco de texto;
- `youtube_package` com uma combinação única de título, descrição, palavras-chave, comentário
  fixado e conceito visual da thumbnail.

O hook do perfil cutout precisa começar com asset ou ação fullscreen. Não abra com uma sequência de
frases centralizadas. A referência bíblica centralizada é um cartão de orientação; o corpo da
citação usa asset + texto e as aspas ficam sob responsabilidade exclusiva do renderer.

Em cada seção, `retention_goal` copia `viewer_value` e `visual_direction` copia
`visual_progression`. O contrato derivado `creative-contract-v2` é compartilhado por cenas,
crescimento e validação. O perfil visual é `long_creative_v2`.

## Postura e precisão

- `factual` não usa `interpretation` nem `creative_elaboration` na locução;
- `exploratory` identifica opinião e inferência como `interpretation`;
- contexto histórico exige fonte histórica verificada;
- comparação e investigação temática exigem ao menos duas fontes bíblicas verificadas;
- incerteza textual ou histórica é preservada, não preenchida por imaginação.

## Título, seleção de imagem e direção visual

Vídeos novos usam `Enfoque concreto | Passagem` e geram um único título. O enfoque
pode ser fato, personagem, curiosidade, pergunta, comparação, dilema, tese ou promessa prática.
Não force tensão motivacional em um vídeo factual ou contemplativo.

No perfil cutout, roteiro, título e thumbnail formam uma única promessa. O título traz busca e
contexto cristão de forma natural; a thumbnail traz a tensão visual em duas ou três linhas, sem
copiar o título. A base 3D em massinha reserva a esquerda para Knewave e concentra personagens e
objetos bíblicos à direita. O compositor local cria `youtube/thumbnail.png` e
`youtube/thumbnail.jpg`; criação não autoriza upload.

Nos demais perfis longos, a imagem de capa continua fora da produção automática. A direção visual
muda ação, escala, ângulo, evidência ou consequência nos reengajamentos e representa um único
momento por imagem.

## Critérios de aceitação

Um roteiro novo é aceito quando:

- usa modo, postura e política de hook resolvidos no briefing;
- ocupa a janela de duração configurada;
- entrega valor cedo, com hook ou sem hook;
- cada seção acrescenta informação e direção visual próprias;
- reengajamentos estão distribuídos conforme a duração;
- citações verificadas cumprem quantidade e cadência;
- o blueprint cutout cobre a locução e resolve bloco, cue, ação fullscreen e pacote do YouTube em
  uma passagem;
- a resolução é coerente com o modo e não força aplicação;
- o CTA executa uma única ação;
- não existe finding bíblico `blocking` aberto.
