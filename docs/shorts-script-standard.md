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
antes de preparar a fila; o Python sela a locução e os validadores leem esses valores em vez de
comprimir o contrato de 50 segundos depois da escrita. O modelo recebe apenas os blocos de narração
para descrever conceitos visuais. Esse perfil aceita somente `devotional_application` e
`pastoral_encouragement` e deve parecer um momento emocional retirado de um filme épico sobre Jesus,
não uma explicação bíblica. Para 15 segundos, o contrato é:

- **0–3,5s — interrupção emocional:** hook curado de 10 a 12 palavras, ligado à passagem e copiado
  exatamente do catálogo;
- **3,5–12,3s — fala direta de Jesus:** citação literal entre aspas, sem introdução, “lemos” ou
  comentário intermediário, com até 28 palavras;
- **12,3–15s — referência falada:** livro, capítulo e versículo fora das aspas;
- **41 a 46 palavras, até seis cenas e nenhum CTA.** Use menos cenas quando a fala não oferecer
  fronteiras linguísticas naturais suficientes.

O perfil compacto usa exatamente três seções e três beats: hook, evidência bíblica e referência. A
fonte precisa declarar `spoken_by=jesus`; versículos narrativos ou falas de outros personagens são
bloqueados. A fonte também registra força do hook, valor da mensagem e potencial de replay; os três
precisam superar os pisos persistidos. Hook, fonte e revisão bíblica são verificados
deterministicamente contra o catálogo; não há uma segunda chamada de modelo para revisão. Cenas por
bloco, legenda e validação final permanecem, e não há thumbnail separada no perfil padrão. O áudio
nunca recebe silêncio artificial para alcançar o alvo.

Quando o pedido especifica uma faixa, como 15 a 20 segundos, o manifesto usa
`adaptive_range_v1`. A seleção considera a união de pacotes compatíveis com os dois limites e a voz
real define `effective_duration_seconds`. Qualquer duração íntegra dentro da faixa passa; relação com
o alvo preferencial, pausas e silêncio terminal são avisos para a revisão humana, não bloqueios.

Entre 16 e 20 segundos, a faixa total cresce com 170 palavras por minuto e o limite da citação sobe
gradualmente de 32 para até 47 palavras. A conta do pacote usa a referência falada exata: uma faixa
de versículos acrescenta `versículos X a Y`, não a estimativa de uma referência simples. O segmento
`compact_jesus` mantém 120 combinações curadas e pelo menos 20 opções elegíveis em cada duração de
15 a 20 segundos. Trechos de oração de Jesus ao Pai, narrações logísticas, acusações isoladas e
fragmentos de parábola sem valor autônomo não entram nesse segmento.

O contrato permite explicitamente uma única citação literal contínua que cubra dois ou mais
versículos consecutivos quando a sequência preservar melhor o sentido, completar uma fala ou gerar
continuidade benéfica à mensagem. A faixa precisa existir como um único excerpt verificado na fonte,
ser citada integralmente e terminar com a referência exata por extenso. Ela continua sujeita ao
limite de palavras e à janela de duração do projeto; ampliar a passagem apenas para ocupar tempo é
proibido.

## Contrato `creative_direction`

`strategy.creative_direction` registra:

- `creative_mode`, `interpretive_stance` e `hook_family`, copiados do briefing;
- no perfil padrão, seções de abertura, citação, desenvolvimento, reengajamento, resolução e CTA;
- no perfil compacto, exatamente interrupção emocional, citação direta de Jesus e referência;
- `content_promise` e, quando útil, `organizing_question`;
- `resolution` e `viewer_takeaway`;
- `practical_application` e `callback` somente quando usados;
- uma única ação de `engagement` no perfil padrão e nenhuma no perfil compacto;
- todas as fontes utilizadas.

`content_promise`, `open_loop`, `payoff`, `practical_value` e `cta` permanecem como aliases de
compatibilidade e precisam copiar os campos equivalentes. O novo contrato não preenche
`retention_story` nem `narrative_spine`.

Aplicação prática é obrigatória apenas em `devotional_application` e `pastoral_encouragement`.
Nos demais modos, a resolução pode ser inteiramente factual, explicativa, analítica ou filosófica.
Callback também é opcional.

## Hook obrigatório

O `hook` é o prefixo literal da primeira seção e termina até a meta de dois segundos. Shorts padrão
salvam `hook_contract=user_six_v1`: a fila seleciona exatamente uma das seis famílias abaixo e a
primeira fala, com no máximo 12 palavras, precisa corresponder literalmente à estrutura dela:

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

O perfil compacto salva `hook_contract=cinematic_interruption_v1` e
`hook_family=emotional_interruption`. A primeira fala tem de 10 a 12 palavras, não é pergunta, soa
como Jesus interrompendo diretamente um pensamento ou desânimo do espectador e copia exatamente o
hook forte curado para a passagem.

## Citação e precisão bíblica

Todo Short cita literalmente pelo menos um versículo verificado. A janela é de 8 a 20 segundos no
perfil padrão e vem de `shorts_script.scripture_citation_*` no perfil compacto. No perfil padrão,
use:

> Em Salmos, capítulo 56, versículo 3, lemos: “Quando eu tiver medo, colocarei a minha confiança em
> ti.”

A referência fica fora das aspas; somente o excerpt literal fica dentro. O texto precisa coincidir
com a fonte registrada. Não narre rótulos como “até aqui, o texto bíblico” ou “agora vem a
reflexão”.

No perfil compacto, não use essa introdução. Depois do hook, a citação direta de Jesus começa
imediatamente e a referência vem sozinha no final:

> Olhe para mim. “Eu sou a luz do mundo; quem me segue não andará em trevas, mas terá a luz da
> vida.” João, capítulo 8, versículo 12.

As aspas de abertura e fechamento fazem parte da locução e das legendas centrais; a referência fica
fora delas.

Classifique afirmações como `biblical_text`, `verified_context`, `interpretation` ou
`creative_elaboration`. Em postura factual, a locução não usa interpretação nem elaboração
criativa. Em postura exploratória, ao menos uma afirmação autoral fica explicitamente classificada
como interpretação.

## CTA de uma única ação no perfil padrão

`creative_direction.engagement.mode` aceita `comment`, `subscribe` ou `share`. O texto falado tem
no máximo 12 palavras e quatro segundos; combinação de ações é rejeitada.

`comment_prompt` só existe quando o modo é `comment`. Palavra-chave é opcional e nunca pode ser
condição para bênção, cura, proteção, milagre ou prova de fé. O CTA ocupa sozinho a última seção
`closing` e não é seguido por resumo ou despedida.

Em `adaptive_compact_v1`, `engagement` e CTA ficam ausentes. A última seção contém apenas a
referência falada da citação anterior.

## Direção visual e embalagem

Cada Short novo preenche `strategy.visual_scenes`. Cada item copia literalmente um bloco contíguo
da narração, aponta para sua `section_id`, descreve uma cena concreta e distinta e classifica sua
relação como `literal`, `contextual` ou `symbolic`. A concatenação dos blocos reconstitui cada seção
integralmente; por isso o mesmo item passa a orientar imagem e áudio sem serializar as duas etapas.
Não peça colagem, storyboard, tela dividida ou várias ações na mesma imagem.

No perfil compacto, divida a fala somente em ponto, dois-pontos, ponto e vírgula, pergunta,
exclamação ou vírgula que preserve unidades semanticamente completas. `target_scene_max` não obriga
o preenchimento: quando faltarem fronteiras seguras, mantenha menos blocos. O preflight rejeita um
bloco intermediário que termine sem pontuação natural. A narração é sintetizada inteira e os cortes de
cena são mapeados depois pelos timestamps do alinhamento. Hook, citação e referência pertencem ao
mesmo áudio contínuo, sem reiniciar a interpretação vocal entre as três seções.

O perfil padrão aplica `biblical-midcentury-gouache-v1` depois do planejamento de conteúdo, sem
reservar área para legendas ou bordas. Ele não gera thumbnail nem capa no frame zero; o YouTube
seleciona um frame do vídeo. `cinematic-family-3d-animation` continua disponível explicitamente e
preserva a edição da abertura, o texto integrado e o frame zero. O título usa
`Enfoque concreto | Passagem` sem obrigar o enfoque a ser motivacional.

Em `adaptive_compact_v1`, o item que aponta para
`strategy.cinematic_devotional.reference_section_id` continua obrigatório para preservar a divisão
do áudio, porém usa a imagem fixa de Bíblia aberta do perfil. Essa cena recebe
`image_source_mode=fixed_profile_asset_v1`, é materializada diretamente no manifesto e não entra na
fila de geração. Citação, referência e timings continuam específicos de cada Short; somente o
bitmap desse bloco final é reutilizado.

## Critérios de aceitação

O roteiro está pronto quando:

- usa o modo e a postura resolvidos no briefing;
- no perfil padrão, começa exatamente com uma das seis estruturas aceitas; no compacto, começa com
  uma interrupção emocional válida;
- no perfil padrão, possui desenvolvimento real e ao menos um reengajamento; no compacto, preserva
  o arco emocional nas três seções;
- cita um versículo verificado dentro da janela persistida pelo perfil de duração;
- entrega uma resolução coerente com o modo, com ou sem aplicação;
- no perfil padrão, pede uma única ação de CTA; no compacto, não contém CTA e termina na referência;
- respeita o máximo de cenas, usa somente fronteiras linguísticas seguras e termina dentro da
  duração persistida pelo perfil;
- não possui divergência literal, de atribuição ou de fonte na revisão bíblica determinística; fora
  do compacto, não possui finding `blocking` aberto na revisão independente.
