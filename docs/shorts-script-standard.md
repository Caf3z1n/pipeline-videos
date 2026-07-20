# Padrão de roteiro para Shorts bíblicos

## Identidade editorial

Shorts novos usam `creative_retention_v3`. O formato mantém retenção alta sem obrigar todos os
roteiros a terminar em uma mensagem motivacional. O modo criativo pode entregar narrativa factual,
curiosidade, explicação, comparação, reflexão, correção de leitura, aplicação devocional ou outra
direção registrada em [creative-direction.md](creative-direction.md).

`retention_story_v2`, `human_hook_to_bible` e `verse_first` continuam válidos para projetos antigos.
O pipeline usa o contrato salvo no `project.json` e não faz migração implícita.

## Estrutura e tempo

O perfil padrão `standard_45_55_v1`, de 45 a 55 segundos, é flexível dentro destes marcos:

- **0–2s — hook obrigatório:** fala específica, honesta e coerente com a promessa;
- **2–8s — orientação ou risco:** deixe claro personagem, fato, pergunta ou objeto investigado;
- **8–20s — evidência bíblica:** cite literalmente ao menos um versículo verificado;
- **20–36s — desenvolvimento:** acrescente fatos, contexto, análise, comparação ou revelação;
- **40% a 60% — reengajamento:** mude a evidência, a pergunta ou a progressão visual;
- **32–48s — resolução:** entregue desfecho, descoberta, síntese, posição ou aplicação;
- **últimos 4s — CTA:** peça exatamente uma ação e encerre.

O roteiro usa de 105 a 130 palavras por padrão e produz de 8 a 10 cenas no perfil
`short_creative_v3`. Cada `retention_beat` declara pergunta ou expectativa do espectador, valor,
informação nova, mudança visual, função e tempo. Reformular a mesma tese não conta como
progressão.

Pedidos entre 15 e 44 segundos usam `adaptive_compact_v1`. O pipeline deriva e persiste os limites
antes de preparar a fila; o roteirista e os validadores leem esses valores em vez de comprimir o
contrato de 50 segundos depois da escrita. Para 15 segundos, o contrato é:

- **0–2s — hook obrigatório;**
- **2–3s — orientação;**
- **3–9s — citação bíblica literal, com até 18 palavras;**
- **9–12s — desenvolvimento, reengajamento e resolução;**
- **12–15s — CTA de uma única ação;**
- **30 a 38 palavras e cinco a seis cenas.**

O perfil compacto mantém informação nova e resolução, mas exige somente uma seção de progressão.
Ele não remove hook, fonte verificada, revisão bíblica, thumbnail, legenda ou validação final.

## Contrato `creative_direction`

`strategy.creative_direction` registra:

- `creative_mode`, `interpretive_stance` e `hook_family`, copiados do briefing;
- seções de abertura, citação, desenvolvimento, reengajamento, resolução e CTA;
- `content_promise` e, quando útil, `organizing_question`;
- `resolution` e `viewer_takeaway`;
- `practical_application` e `callback` somente quando usados;
- uma única ação de `engagement`;
- todas as fontes utilizadas.

`content_promise`, `open_loop`, `payoff`, `practical_value` e `cta` permanecem como aliases de
compatibilidade e precisam copiar os campos equivalentes. O novo contrato não preenche
`retention_story` nem `narrative_spine`.

Aplicação prática é obrigatória apenas em `devotional_application` e `pastoral_encouragement`.
Nos demais modos, a resolução pode ser inteiramente factual, explicativa, analítica ou filosófica.
Callback também é opcional.

## Hook obrigatório

O `hook` é o prefixo literal da primeira seção, tem no máximo 12 palavras e termina até a meta
de dois segundos. Shorts novos salvam `hook_contract=user_six_v1`: a fila seleciona exatamente
uma das seis famílias abaixo e a primeira fala precisa corresponder literalmente à estrutura dela:

1. `Se você quer [X], faça isso.`
2. `O que acontece se você parar de [X]?`
3. `Se você ainda faz [X], está perdendo [Y].`
4. `Ninguém fala sobre [X], mas...`
5. `Quer aprender [X]? Comece...`
6. `Nunca faça [X].`

Abertura em cena, fato surpreendente, pergunta específica, contradição, consequência primeiro e
outras famílias permanecem disponíveis apenas para contratos legados e vídeos longos. No contrato
novo, uma frase apenas parecida ou classificada com outro nome não passa no preflight. Não use as
seis formas para inventar perda, risco, proibição ou promessa. Registre de uma a três técnicas
éticas em `hook_techniques`.

## Citação e precisão bíblica

Todo Short cita literalmente pelo menos um versículo verificado. A janela é de 8 a 20 segundos no
perfil padrão e vem de `shorts_script.scripture_citation_*` no perfil compacto. Use:

> Em Salmos, capítulo 56, versículo 3, lemos: “Quando eu tiver medo, colocarei a minha confiança em
> ti.”

A referência fica fora das aspas; somente o excerpt literal fica dentro. O texto precisa coincidir
com a fonte registrada. Não narre rótulos como “até aqui, o texto bíblico” ou “agora vem a
reflexão”.

Classifique afirmações como `biblical_text`, `verified_context`, `interpretation` ou
`creative_elaboration`. Em postura factual, a locução não usa interpretação nem elaboração
criativa. Em postura exploratória, ao menos uma afirmação autoral fica explicitamente classificada
como interpretação.

## CTA de uma única ação

`creative_direction.engagement.mode` aceita `comment`, `subscribe` ou `share`. O texto falado tem
no máximo 12 palavras e quatro segundos; combinação de ações é rejeitada.

`comment_prompt` só existe quando o modo é `comment`. Palavra-chave é opcional e nunca pode ser
condição para bênção, cura, proteção, milagre ou prova de fé. O CTA ocupa sozinho a última seção
`closing` e não é seguido por resumo ou despedida.

## Direção visual e embalagem

Cada `visual_direction` descreve um momento, um ambiente contínuo e uma ação principal. A
progressão precisa ter mudanças concretas no desenvolvimento, no reengajamento e na resolução.
Não peça colagem, storyboard, tela dividida ou várias ações na mesma imagem.

A thumbnail continua editando a imagem ativa da abertura, preserva `cinematic-family-3d-v1`,
incorpora texto e marcação ao bitmap e ocupa o frame zero. O título usa
`Enfoque concreto | Passagem`; título, primeira fala, cena e thumbnail entregam a mesma promessa,
sem obrigar o enfoque a ser motivacional.

## Critérios de aceitação

O roteiro está pronto quando:

- usa o modo e a postura resolvidos no briefing;
- começa exatamente com uma das seis estruturas aceitas e cumpre a promessa;
- possui desenvolvimento real e ao menos um reengajamento;
- cita um versículo verificado dentro da janela persistida pelo perfil de duração;
- entrega uma resolução coerente com o modo, com ou sem aplicação;
- pede uma única ação de CTA;
- produz a quantidade de cenas e termina dentro da duração persistidas pelo perfil;
- não possui finding bíblico `blocking` aberto na revisão independente.
