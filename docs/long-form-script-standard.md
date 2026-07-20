# Padrão de roteiro para vídeos longos

## Escopo

Vídeos longos novos usam `creative_retention_long_v2`. O modo criativo define a experiência:
storytelling factual, retrato de personagem, explicação, curiosidade, contexto histórico,
reflexão filosófica, investigação temática, comparação, dilema, exploração literária,
reconstrução cronológica, aplicação devocional ou outra direção do catálogo.

O padrão `retention_story_long_v1` continua válido para projetos existentes. O contrato salvo no
`project.json` é a autoridade e não existe migração automática.

Consulte também [creative-direction.md](creative-direction.md).

## Estrutura adaptativa

A duração continua definida pelo projeto. O roteiro ocupa de 85% a 112% da duração-alvo,
considerando 145 palavras por minuto.

- **Abertura:** `opening_line` é o prefixo literal da locução. Sem saudação, apresentação do
  canal ou “no vídeo de hoje”.
- **Hook adaptativo:** `hook_policy` pode ser `required`, `optional` ou `none`. Com hook, o primeiro
  beat termina em até 20 segundos. Sem hook, ele usa `orientation` e entrega valor em até 40
  segundos.
- **Promessa:** `content_promise` fica clara nos dois primeiros movimentos. Pergunta central é
  opcional.
- **Desenvolvimento:** cada seção acrescenta fato, evidência, contexto, ação, consequência,
  comparação, argumento, objeção, revelação ou mudança de entendimento.
- **Reengajamentos:** introduzem valor novo antes que a progressão fique previsível. O intervalo
  máximo é de 120 segundos e a quantidade mínima cresce com a duração.
- **Resolução:** pode ser distribuída, tardia ou reservada ao quarto final. Entrega o valor
  prometido como desfecho factual, síntese, posição, incerteza preservada ou aplicação.
- **CTA:** pede exatamente uma ação — comentar, inscrever-se ou compartilhar — em até 24 palavras
  ou dez segundos, depois da conclusão.

Aplicação prática só é obrigatória em `devotional_application` e `pastoral_encouragement`.
Callback é sempre opcional no v2. Os três arquétipos antigos não são preenchidos no novo
contrato; a estrutura vem do `creative_mode`.

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

Use exatamente:

`Em Livro, capítulo X, versículo Y, lemos: “texto literal verificado.”`

A referência fica fora das aspas. Cada citação registra `biblical_basis`, classifica a afirmação
como `biblical_text` e aponta para a fonte verificada correspondente em `scripture_anchors`.

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

Em cada seção, `retention_goal` copia `viewer_value` e `visual_direction` copia
`visual_progression`. O contrato derivado `creative-contract-v2` é compartilhado por cenas,
crescimento e validação. O perfil visual é `long_creative_v2`.

## Postura e precisão

- `factual` não usa `interpretation` nem `creative_elaboration` na locução;
- `exploratory` identifica opinião e inferência como `interpretation`;
- contexto histórico exige fonte histórica verificada;
- comparação e investigação temática exigem ao menos duas fontes bíblicas verificadas;
- incerteza textual ou histórica é preservada, não preenchida por imaginação.

## Título, thumbnail e direção visual

Vídeos novos usam `Enfoque concreto | Passagem` e geram um título e uma thumbnail. O enfoque
pode ser fato, personagem, curiosidade, pergunta, comparação, dilema, tese ou promessa prática.
Não force tensão motivacional em um vídeo factual ou contemplativo.

A thumbnail usa `biblical_cinematic_landscape_v1`: horizontal 16:9, baseada no tema central, sem
imagem de referência, texto ou marcação editorial. A direção visual muda ação, escala, ângulo,
evidência ou consequência nos reengajamentos e representa um único momento por imagem.

## Critérios de aceitação

Um roteiro novo é aceito quando:

- usa modo, postura e política de hook resolvidos no briefing;
- ocupa a janela de duração configurada;
- entrega valor cedo, com hook ou sem hook;
- cada seção acrescenta informação e direção visual próprias;
- reengajamentos estão distribuídos conforme a duração;
- citações verificadas cumprem quantidade e cadência;
- a resolução é coerente com o modo e não força aplicação;
- o CTA executa uma única ação;
- não existe finding bíblico `blocking` aberto.
