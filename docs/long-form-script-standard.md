# Padrão de roteiro para vídeos longos

## Escopo

Vídeos longos novos usam `retention_story_long_v1`. O formato preserva a sensação de acompanhar a
própria narrativa bíblica: versículos literais verificados aparecem periodicamente, com livro,
capítulo e versículo verbalizados, enquanto a explicação conecta cada citação ao movimento da
história.

Projetos anteriores continuam válidos com o padrão armazenado no próprio `project.json`. Não existe
migração automática.

## Promessa e estrutura

A duração continua sendo definida pelo projeto. O roteiro deve ocupar de 85% a 112% da duração-alvo,
considerando 145 palavras por minuto, e usar a menor quantidade de texto capaz de entregar a
promessa sem lacunas.

- **0–20s — conflito e promessa:** comece dentro de uma decisão, risco, perda, confronto ou
  descoberta. Sem saudação, apresentação do canal ou “no vídeo de hoje”. A primeira fala precisa
  corresponder ao título e à imagem de abertura.
- **Abertura — pergunta central:** verbalize claramente o que o espectador compreenderá ao
  permanecer. A pergunta não pode ser genérica nem depender de suspense enganoso.
- **Progressão por movimentos:** cada seção acrescenta fato, ação, consequência, reversão,
  evidência bíblica ou mudança de entendimento. Repetição e contemplação sem função são removidas.
- **Reengajamentos:** introduza nova informação, consequência, contraste, mudança de escala visual
  ou pergunta mais específica antes que a progressão fique previsível. O intervalo máximo é de 120
  segundos; a quantidade mínima cresce com a duração.
- **Quarto final — payoff e aplicação:** resolva a pergunta central somente depois de sustentar a
  progressão. Converta a descoberta em uma resposta prática específica.
- **Callback e CTA:** retome a abertura com significado adicional e peça exatamente uma ação:
  comentar, inscrever-se ou compartilhar. O CTA ocupa no máximo 24 palavras ou dez segundos e é o
  último movimento da locução.

Os arquétipos editoriais são os mesmos dos Shorts, ampliados em atos:

- `conflict_first`: acompanha uma decisão sob pressão e suas consequências;
- `counterintuitive_reveal`: parte de uma leitura comum e revela gradualmente o que a passagem
  realmente sustenta;
- `stair_step_progression`: aumenta risco, custo, oposição ou descoberta a cada movimento.

## Cadência bíblica

O alvo é de 0,8 a 1,2 citações literais por minuto. Um vídeo de dez minutos usa de oito a doze
citações verificadas.

- a primeira citação entra até aproximadamente 75 segundos;
- citações consecutivas ficam separadas por pelo menos 25 segundos;
- a narrativa não passa mais de 105 segundos sem uma nova citação;
- a última parte do vídeo não fica mais de 120 segundos sem âncora bíblica;
- cada citação cumpre uma função: evidência, mudança de ato, consequência, contraste, revelação ou
  confirmação do payoff;
- nunca agrupe os versículos apenas para cumprir quantidade.

Use exatamente:

`Em Livro, capítulo X, versículo Y, lemos: “texto literal verificado.”`

A referência fica fora das aspas. Somente o excerpt literal é citado. A seção registra a referência
em `biblical_basis`, classifica a afirmação como `biblical_text` e aponta para uma fonte bíblica
verificada em `scripture_anchors`.

## Contrato estruturado

`ScriptDocument.long_form_strategy` registra:

- arquétipo, público, necessidade, promessa e pergunta central;
- gancho e conflito de abertura;
- um beat para cada seção, em ordem;
- valor novo, informação nova e progressão visual de cada beat;
- timestamps planejados dos reengajamentos;
- uma âncora para cada citação literal;
- payoff, aplicação, callback e exatamente uma ação de CTA.

Em cada seção:

- `retention_goal` copia o `viewer_value` do beat correspondente;
- `visual_direction` copia `visual_progression`;
- a locução acrescenta algo que não estava disponível no movimento anterior.

O contrato criativo derivado é compartilhado por cenas, crescimento e validação. A abertura recebe
planos de aproximadamente seis segundos, reengajamentos sete segundos, progressão regular dez
segundos, contemplação funcional até quatorze segundos e payoff aproximadamente nove segundos.

## Título, thumbnail e direção visual

Vídeos longos novos seguem `Tensão ou promessa concreta | Passagem`. Título, thumbnail, primeira
fala e cena de abertura entregam a mesma promessa. A thumbnail continua usando a imagem ativa da
abertura, o padrão visual `cinematic-family-3d-v1` e a tipografia integrada
`integrated_extruded_3d_v1`.

A direção visual deve:

- começar com conflito legível e um assunto dominante;
- mudar escala, ângulo, ação ou consequência nos reengajamentos;
- preservar continuidade de personagem, figurino, ambiente e luz;
- representar um único momento por imagem;
- evitar imagens contemplativas genéricas quando a locução descreve ação concreta.

## Critérios de aceitação

Um roteiro novo só é aceito quando:

- começa no conflito e declara a promessa e a pergunta central;
- ocupa a janela de duração configurada;
- cada seção possui valor, informação e direção visual próprios;
- reengajamentos estão distribuídos conforme a duração;
- citações literais verificadas cumprem quantidade e cadência;
- o payoff resolve a pergunta inicial;
- a aplicação é específica;
- o callback acrescenta significado;
- o encerramento pede uma única ação e termina imediatamente.
