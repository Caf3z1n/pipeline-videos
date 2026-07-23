# Direção criativa de roteiros

## Objetivo

Projetos novos separam duas decisões que antes estavam acopladas:

- **direção editorial:** qual experiência o vídeo entrega;
- **retenção:** como essa experiência progride sem ficar repetitiva.

Retenção não significa transformar todo assunto em mensagem motivacional. Uma resolução pode
ser um desfecho factual, uma explicação, uma descoberta, uma síntese analítica, uma posição
filosófica, uma incerteza preservada ou uma aplicação. Somente os modos devocionais exigem
conselho prático.

Shorts novos usam `creative_retention_v3`; longos novos usam `creative_retention_long_v2`.

O perfil opt-in `high_stakes_biblical_question_v1` continua usando `creative_retention_v3`, mas
inverte a autoridade editorial: seleciona primeiro uma dúvida bíblica conhecida e depois sela as
passagens capazes de respondê-la. `required_answer_points`, `answer_status` e
`uncertainty_boundaries` impedem que curiosidade vire especulação.

Nos Shorts, a direção criativa também entrega `strategy.visual_scenes`: blocos literais da locução
com uma cena concreta por bloco e relação `literal`, `contextual` ou `symbolic`. A descrição deve
variar ação, ambiente, personagem ou objeto quando isso beneficiar a continuidade, sem transformar
simbolismo em afirmação bíblica factual. O estilo pictórico é acrescentado depois pelo pipeline.
Projetos existentes preservam o padrão salvo no próprio `project.json` e não são migrados
implicitamente.

## Modos criativos

O briefing resolve `creative_mode` antes da escrita. O seletor automático considera a passagem,
evita os modos usados recentemente e registra a escolha no índice compartilhado.

| Modo | Entrega principal | Postura | Hook longo padrão |
| --- | --- | --- | --- |
| `biblical_storytelling` | conta uma história humana por cenas, escolhas e consequências | factual | opcional |
| `character_portrait` | acompanha escolhas, limites e mudanças de um personagem | analítica | opcional |
| `passage_explainer` | explica progressão, relações e limites de uma passagem | expositiva | opcional |
| `biblical_curiosity` | revela um detalhe verificável que muda a leitura | expositiva | obrigatório |
| `historical_context` | reconstrói contexto somente com fonte histórica verificada | factual | opcional |
| `philosophical_reflection` | desenvolve argumento autoral distinguido da Bíblia | exploratória | opcional |
| `thematic_investigation` | rastreia um tema por passagens diferentes | analítica | opcional |
| `comparison_contrast` | compara personagens, decisões ou passagens | analítica | opcional |
| `ethical_dilemma` | examina alternativas, custos e tensão moral | exploratória | obrigatório |
| `misconception_correction` | corrige uma leitura comum pela evidência | analítica | obrigatório |
| `literary_exploration` | observa imagem, repetição, contraste, estrutura ou voz | expositiva | nenhum |
| `question_driven_essay` | investiga uma pergunta sem pressupor resposta prática | exploratória | opcional |
| `chronological_reconstruction` | organiza eventos e deslocamentos em sequência | factual | nenhum |
| `cause_and_consequence` | acompanha efeitos sucessivos de uma escolha | analítica | opcional |
| `devotional_application` | conecta a passagem a uma resposta prática delimitada | devocional | obrigatório |
| `pastoral_encouragement` | acolhe e encoraja sem transformar dor em fórmula | devocional | opcional |

Os modos não são templates de texto. Cada um fornece uma estrutura sugerida, mas o roteirista
pode mudar a ordem, combinar movimentos compatíveis e omitir pergunta, aplicação ou callback
quando esses elementos não servirem ao tema.

## Voz autoral global

Todo modo criativo e todo formato usa `human_authorial_voice_v1`. A locução entrega diretamente o
fato, a ação, a dúvida ou a explicação e nunca comenta o próprio método de leitura. Fórmulas como
“o relato registra”, “o relato não explica”, “a sequência é compacta”, “o retrato começa”, “a
passagem paralela acrescenta”, “comparar é observar” e “não podemos afirmar” são bloqueadas antes
do import. Também são recusadas listas de ausência e negações usadas apenas como andaime para
valorizar a frase seguinte. Isso inclui a fórmula `não A nem B, mas C`: a locução deve declarar
`C` diretamente. Citações bíblicas literais ficam protegidas, assim como hooks negativos legítimos
previstos pelo contrato, por exemplo `Nunca faça [X].`.

## Postura e evidência

`interpretive_stance` declara o tipo de voz autoral:

- `factual`: usa `biblical_text` e `verified_context`; não inventa diálogo, motivação, emoção,
  cronologia ou detalhe histórico;
- `expository`: explica relações do texto e classifica inferências como `interpretation`;
- `analytical`: compara evidências e sustenta conclusões proporcionais;
- `exploratory`: permite opinião e reflexão autoral, sempre distinguíveis da afirmação bíblica;
- `devotional`: oferece aplicação ou encorajamento sem prometer resultado espiritual.

`historical_context` exige fonte histórica verificada. `thematic_investigation` e
`comparison_contrast` exigem ao menos duas fontes bíblicas verificadas. Um finding `blocking`
continua impedindo aprovação.

## Política de hook

Shorts padrão novos usam `hook_contract=user_six_v1`: a primeira fala é específica, honesta, tem no
máximo 12 palavras, termina até dois segundos e corresponde exatamente a uma das seis estruturas
aceitas. Shorts em `adaptive_compact_v1` usam `cinematic_interruption_v1`: a primeira fala tem de
10 a 12 palavras, começa como uma interrupção direta — por exemplo, “Olhe para mim.” — e continua
com uma tensão específica ligada ao versículo. O hook é curado no catálogo e não pode ser
reescrito pelo roteirista.
O perfil de grandes perguntas usa `hook_contract=high_stakes_opening_v1`. Seu hook também é curado,
mas pode ser fato surpreendente, contradição, reversão de crença, consequência, cena em andamento,
citação ou pergunta. A regra é criar uma pergunta na mente do espectador, não necessariamente
pronunciar uma pergunta.
Vídeos longos resolvem `hook_policy` como `required`, `optional` ou `none` conforme o modo e o tema.
Quando não há hook, `opening_line` ainda precisa orientar a cena e entregar valor nos primeiros 40
segundos; saudação genérica continua proibida.

As seis famílias exclusivas dos Shorts novos são:

1. `Se você quer [X], faça isso.`
2. `O que acontece se você parar de [X]?`
3. `Se você ainda faz [X], está perdendo [Y].`
4. `Ninguém fala sobre [X], mas...`
5. `Quer aprender [X]? Comece...`
6. `Nunca faça [X].`

Contratos legados e vídeos longos também podem usar abertura em cena, fato surpreendente
verificável, pergunta específica, contradição, consequência primeiro e citação primeiro. A família
escolhida precisa corresponder à fala real. Advertência, perda e urgência só podem ser usadas quando
o roteiro realmente as sustenta; o hook não pode fabricar risco ou promessa.

## Retenção que permanece obrigatória

Independentemente do modo:

- cada movimento acrescenta informação, evidência, contexto, argumento, consequência, contraste
  ou mudança real de entendimento;
- reengajamentos mudam a pergunta, escala, evidência ou direção visual;
- a resolução entrega o valor prometido sem precisar virar conselho;
- fora do perfil compacto, o CTA pede exatamente uma ação e vem depois da conclusão;
- a direção visual representa um momento, ambiente e ação por imagem.

Shorts mantêm ao menos uma citação literal verificada. O perfil padrão usa 8 a 20 segundos. O perfil
de grandes perguntas usa `adaptive_payoff_v1` entre 0 e 44 segundos e posiciona a citação como
tensão, prova ou recompensa conforme o roteiro. O perfil compacto aceita somente fala direta de
Jesus, iniciada imediatamente após o hook, e termina com a
referência fora das aspas; em 15 segundos, o hook vai de 0 a 3,5, a citação de 3,5 a 12,3 segundos e
a referência de 12,3 a 15 segundos. Vídeos longos
mantêm o alvo de 0,8 a 1,2 citação por minuto, primeira âncora até 75 segundos, intervalo máximo
de 105 segundos e no máximo 120 segundos entre a última citação e o fim.

O catálogo combinado contém 557 candidatos. O segmento `compact_jesus` acrescenta 120 falas
diretas curadas, com hook específico, valor da mensagem e potencial de replay persistidos; há ao
menos 20 combinações elegíveis para cada duração de 15 a 20 segundos. Os segmentos `general` e
`long_form_anchor` acrescentam, respectivamente, 180 versos de uso amplo e 100 passagens para
desenvolvimento longo. O seletor respeita `recommended_formats`, portanto uma âncora exclusiva de
vídeo longo nunca é usada para preencher um Short.

O segmento `narrative_story` acrescenta 81 histórias, 88 personagens ou grupos indexados e 325
excertos verificados. Ele só é elegível quando há um personagem solicitado explicitamente. Cada
pacote inclui uma âncora, o intervalo completo do relato e passagens relacionadas classificadas por
função narrativa. Um pedido desse tipo usa `biblical_storytelling` e o perfil
`biblical_character_storytelling_v1` por padrão; `character_portrait` é opt-in para pedidos de
análise. O storytelling percorre a linha temporal disponível, mantém a maior parte do
desenvolvimento em `biblical_narrative` e não aceita beats de `analysis` ou `comparison`. A revisão
preserva limites e lacunas silenciosamente. Além do filtro autoral global, esse perfil exige que a
maior parte do desenvolvimento avance por acontecimentos e rejeita beats de análise ou comparação.

O perfil nunca invade o compacto de 15 a 44 segundos. Fragmentos narrativos, portanto, ficam
disponíveis para histórias como a mulher cananeia, Judas, Pedro ou José, mas permanecem fora dos
devocionais compactos.

## Embalagem editorial

Projetos novos usam `creative_pipe_passage_v3`: `Enfoque concreto | Passagem`. O enfoque antes da
barra pode ser fato, personagem, curiosidade, pergunta, comparação, dilema, tese interpretativa ou
promessa prática. Título e abertura visual continuam complementares, mas não precisam simular tensão
motivacional quando o modo escolhido é factual, explicativo ou contemplativo.
