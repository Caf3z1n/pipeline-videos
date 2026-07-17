# Padrão de roteiro para Shorts bíblicos

## Identidade editorial

Novos Shorts usam `retention_story_v2`. O objetivo é criar uma experiência bíblica de 45 a 55
segundos que começa dentro de uma ação ou conflito, progride sem trechos neutros, usa a Bíblia como
evidência e encerra assim que entrega o payoff e uma única ação de engajamento.

Os formatos `human_hook_to_bible` e `verse_first` continuam válidos para projetos antigos. Eles não
são migrados implicitamente.

O novo padrão adapta princípios de retenção sem usar clickbait enganoso: título, thumbnail e cold
open prometem a mesma coisa; cada beat acrescenta valor; existe uma mudança real no meio; e a
resolução entrega exatamente o que a abertura criou como pergunta.

## Arquétipos

O seletor reserva um dos três arquétipos no briefing e não repete nenhum dos dois Shorts anteriores:

- `conflict_first`: começa dentro de uma decisão, risco ou conflito já em andamento;
- `counterintuitive_reveal`: mostra um detalhe bíblico que corrige uma expectativa comum;
- `stair_step_progression`: aumenta consequência, dificuldade ou descoberta em etapas.

O roteiro copia esse valor para `retention_story.archetype`. Trocar o arquétipo depois da reserva é
erro de validação.

## Estrutura e tempo

Use como contrato:

- **0–2s — cold open:** ação, detalhe ou conflito concreto; sem saudação ou explicação;
- **2–8s — pergunta central:** aumente a tensão e deixe claro o que está em jogo;
- **8–20s — evidência bíblica:** cite literalmente ao menos um versículo verificado;
- **20–32s — progressão e reengajamento:** acrescente consequência, reversão ou revelação;
- **32–45s — payoff e aplicação:** resolva a pergunta e ofereça uma ação específica;
- **últimos 4s — CTA:** peça exatamente uma ação e encerre no callback.

O último beat termina entre 45 e 55 segundos. O reengajamento começa entre 40% e 60% da duração. O
roteiro usa de 105 a 130 palavras por padrão e deve produzir de 8 a 10 cenas no perfil
`short_retention_v2`.

Cada `retention_beat` preenche:

- `viewer_question` e `viewer_value`;
- `new_information`, diferente dos demais beats;
- `visual_change`, com uma mudança visível e executável;
- tempo crescente, função editorial e, quando aplicável, abertura ou resolução do loop.

Reformular a mesma tese com palavras diferentes não conta como progressão.

## Contrato `retention_story`

O pacote estrutura explicitamente:

- seção de abertura e `opening_promise`;
- `central_question`;
- seção da citação e `scripture_anchor` literal;
- duas ou mais progressões, cada uma ligada à sua seção;
- reengajamento único;
- payoff e resposta prática;
- `engagement` com uma única ação;
- `replay_bridge` ligando abertura e encerramento.

`content_promise`, `open_loop`, `payoff`, `practical_value` e `cta` copiam os campos equivalentes do
novo contrato. O pipeline normaliza essas duplicações antes de validar o staging.

## Citação e precisão bíblica

Todo Short cita literalmente pelo menos um versículo verificado. A referência falada usa
`capítulo` e `versículo`:

> Em Salmos, capítulo 56, versículo 3, lemos: “Quando eu tiver medo, colocarei a minha confiança em
> ti.”

A apresentação e a referência ficam fora das aspas; somente o texto literal fica dentro. O texto
precisa coincidir com o `excerpt` da fonte. Não narre “Até aqui, o texto bíblico”, “agora vem a
reflexão” ou outro rótulo editorial.

Texto bíblico, contexto, interpretação e elaboração criativa continuam separados em
`claim_classification`. O formato de retenção não autoriza inventar cena histórica, promessa,
resultado espiritual ou intenção divina.

## Abertura, progressão e replay

O `hook` é o prefixo exato da primeira seção, tem no máximo 12 palavras e termina até a meta de dois
segundos. Prefira começar com personagem, ação, objeto, consequência ou contraste. Evite perguntas
terapêuticas que caberiam em qualquer vídeo.

Use de uma a três técnicas éticas em `hook_techniques`: `specificity`, `contrast`, `curiosity_gap`,
`open_loop`, `self_relevance`, `emotional_mirroring` ou `pattern_interrupt`.

O `replay_bridge` registra um elemento real da abertura, o callback final e o novo significado que
ele recebe. O callback precisa aparecer na última fala. Ele deve recompensar uma segunda exibição,
não produzir uma frase circular artificial.

## CTA de uma única ação

`retention_story.engagement.mode` aceita:

- `comment`;
- `subscribe`;
- `share`.

O texto falado tem no máximo 12 palavras e quatro segundos. O validador rejeita qualquer combinação
de duas ações. Não há bloco automático de curtir, comentar, inscrever-se e compartilhar.

`comment_prompt` só existe quando o modo é `comment`. Uma palavra-chave continua permitida, mas é
opcional. Quando usada entre aspas, o pipeline pode isolá-la na legenda e aplicar a pausa configurada.
O comentário nunca é condição para bênção, cura, proteção, milagre ou prova de fé.

O CTA ocupa sozinho a última seção `closing`, contém o callback e encerra o vídeo sem resumo,
agradecimento ou despedida posterior.

## Direção visual e thumbnail

Cada `visual_direction` descreve um único momento, ambiente contínuo e ação principal. A progressão
de 8 a 10 cenas precisa conter:

- abertura visualmente forte e coerente com o cold open;
- mudanças concretas de ação ou informação;
- uma mudança visual no reengajamento;
- payoff legível;
- callback final para o elemento da abertura.

Não peça colagem, painéis, storyboard, tela dividida ou várias ações na mesma imagem.

O sistema de thumbnail não muda: o especialista edita diretamente a imagem ativa da abertura,
preserva `cinematic-family-3d-v1`, incorpora texto e marcação ao bitmap e usa o resultado no frame
zero. O título do Short segue `Tensão concreta | Passagem` e a primeira fala, a cena de abertura e a
thumbnail precisam entregar a mesma promessa estruturada em `promise_match`.

## Linguagem e critérios de qualidade

Escreva em português brasileiro falado, pastoral, simples e concreto. Evite frases que soam como
texto de IA, jargão editorial narrado e abstrações sem ação.

O roteiro está pronto quando:

- começa dentro de uma situação específica e cumpre a promessa da embalagem;
- possui duas ou mais progressões e um reengajamento real;
- cita um versículo verificado entre 8 e 20 segundos;
- resolve a pergunta inicial sem distorcer a passagem;
- entrega aplicação específica, CTA único e callback;
- produz de 8 a 10 direções de cena úteis;
- cabe entre 45 e 55 segundos;
- não possui finding bíblico `blocking` aberto.
