# Padrão de roteiro para Shorts bíblicos

## Identidade editorial

Todo Short começa com uma tensão humana reconhecível e chega rapidamente à Bíblia. O objetivo não é
esconder o texto bíblico, mas mostrar nos primeiros segundos por que ele importa para quem está
assistindo.

O padrão principal é `human_hook_to_bible`:

1. **Gancho humano** — dor, pergunta, contraste ou situação cotidiana em até 2,5 segundos;
2. **Citação bíblica** — referência falada e ao menos um versículo literal verificado;
3. **Contexto ou conflito** — personagem, situação ou limite necessário para não ler o texto de
   forma rasa;
4. **Verdade central** — uma interpretação clara que resolve o gancho;
5. **Resposta prática** — reflexão, ação ou decisão específica para hoje;
6. **Encerramento** — uma palavra contextual para comentar e o CTA completo do canal.

`verse_first` continua aceito para projetos antigos, mas não é o padrão de novos Shorts.

## Citações bíblicas

Todo Short cita literalmente pelo menos um versículo bíblico verificado. Dê preferência a passagens
conhecidas, curtas e fáceis de entender. Uma segunda citação só entra quando acrescentar valor real,
caber no ritmo e possuir fonte própria verificada.

Toda referência narrada usa as palavras `capítulo` e `versículo`. Diga **“1 Pedro, capítulo 5,
versículo 7”**, nunca apenas “1 Pedro 5:7”. O formato compacto continua nos metadados e campos
técnicos.

As aspas delimitam somente o texto copiado da fonte:

> Em Josué, capítulo 1, versículo 9, lemos: “Seja forte e corajoso…”

Não narre “Até aqui, o texto bíblico”, “agora vem a reflexão” nem outro rótulo de transição. A
separação é feita pelas aspas, pelo itálico das palavras literais, pelas seções estruturadas e pela
classificação das afirmações. Depois da citação, use uma transição natural.

## Gancho humano e atenção ética

O gancho precisa ser o prefixo exato da primeira seção, ter no máximo 12 palavras e mirar 2,5
segundos. Ele deve abrir uma pergunta que a verdade central realmente resolve.

Técnicas permitidas, registradas em `hook_techniques`:

- `self_relevance`: faz o espectador se reconhecer na situação;
- `emotional_mirroring`: nomeia uma emoção comum sem dramatização artificial;
- `curiosity_gap` ou `open_loop`: campo técnico que abre uma pergunta específica com resposta real;
- `contrast`: aproxima duas situações que parecem incompatíveis;
- `specificity`: usa um detalhe concreto em vez de uma abstração;
- `pattern_interrupt`: quebra uma expectativa sem choque gratuito.

Use de uma a três técnicas por gancho. Elas servem à mensagem; não substituem conteúdo. São
proibidos medo artificial, culpa, urgência inventada, promessa espiritual, informação escondida de
propósito e qualquer pressão para provar fé por meio de comentário ou compartilhamento.

Exemplos de estruturas, não de bordões:

- pergunta de identificação: “Você sabe o que fazer, mas o medo ainda trava você?”;
- contraste: “Davi tinha uma promessa, mas estava escondido numa caverna.”;
- inversão: “O lugar que parecia o fim virou o começo da mudança.”

Varie a construção entre vídeos e mantenha a promessa fiel ao conteúdo.

## Voz pastoral e linguagem cotidiana

A voz deve lembrar um bom pastor ou padre conversando com uma pessoa: acolhedora, firme, simples e
fácil de acompanhar. Não use caricatura religiosa, tom de palestra, jargão denominacional ou
linguagem terapêutica automática.

Prefira:

- frases curtas;
- palavras frequentes no cotidiano;
- verbos concretos;
- perguntas que uma pessoa realmente faria;
- imagens mentais simples;
- transições naturais como “repara nisso”, “só que”, “então” e “talvez você pense”, apenas quando
  combinarem com o contexto.

Evite construções que soam escritas por IA ou que poderiam caber em qualquer tema, como “essa
passagem nos convida”, “para a vida real”, “acolha a sua dor”, “nomeie o seu medo”, “a coragem nasce
de” e “escolha um próximo passo responsável”.

Leia a locução em voz alta. Reescreva qualquer frase que soe formal, publicitária, excessivamente
lapidada ou difícil de dizer numa conversa.

## Ritmo e duração

Em um Short de 50 a 60 segundos, use como referência:

- 0–3s: gancho humano;
- 3–15s: entrada no contexto e citação bíblica;
- 15–28s: conflito ou contexto;
- 28–40s: verdade central e resolução;
- 40–50s: reflexão ou resposta prática;
- 50–60s: comentário e CTA.

O orçamento de palavras é validado pela velocidade configurada da voz, não apenas por um intervalo
fixo. Não acelere a leitura para salvar um roteiro longo. Se o versículo ocupar mais tempo, encurte a
explicação sem cortar o sentido original.

## Precisão e revisão bíblica

Texto bíblico, contexto, interpretação e elaboração criativa continuam separados em
`claim_classification`. Uma estrutura envolvente não autoriza transformar uma observação em promessa
universal.

Não diga que Deus causou uma dor para preparar alguém, que todo sofrimento anuncia uma promoção ou
que o mesmo desfecho de um personagem será repetido na vida do espectador, salvo quando a fonte
realmente sustentar essa afirmação. Preserve limites, contexto e incertezas na revisão.

Modos de desenvolvimento:

- `contextual_reflection`: explica o contexto e conduz a uma reflexão;
- `biblical_story_bridge`: usa outra cena ou personagem bíblico com fonte complementar;
- `biblical_image_bridge`: usa uma imagem já presente na Bíblia;
- `cross_reference_bridge`: relaciona outra citação verificada;
- `promise_boundary`: explica o que o texto afirma e o que ele não promete.

História, imagem e correlação externas exigem outra fonte bíblica verificada em
`bridge_source_ids`.

## Direção visual

Cada `visual_direction` descreve uma única imagem:

- um momento;
- um ambiente contínuo;
- uma ação principal;
- um assunto dominante;
- espaço de baixo contraste integrado ao mesmo fundo para as legendas.

Não peça vinhetas, painéis, colagem, storyboard, díptico, tríptico, tela dividida, cenas empilhadas,
antes e depois ou uma sequência de ações na mesma imagem. A área da legenda nunca pode virar faixa
branca, retângulo vazio ou divisor horizontal.

Quando uma seção tiver várias ideias, escolha o beat visual ligado ao trecho narrado ou divida a
locução em cenas separadas.

## Comentário e CTA

Escolha uma palavra ou frase curta que nasça da resolução. Quando for uma palavra, ela funciona como
uma palavra-síntese: expressiva, específica, com pelo menos seis letras e ligada diretamente à
verdade central, ao payoff ou à aplicação. `REFÚGIO`, `PERSISTÊNCIA`, `CONFIANÇA`, `FIDELIDADE`,
`ESPERANÇA`, `PERSEVERANÇA`, `RECOMEÇO`, `DESCANSO` e `CORAGEM` são referências de densidade
semântica, não uma lista para escolha aleatória.

Não use isoladamente respostas vagas como `BEM`, `SIM`, `AMÉM`, `EU`, `AQUI`, `PERTO`, `FÉ`, `PAZ`,
`FORÇA` ou `FIEL`. O `fit_reason` precisa citar a palavra escolhida e explicar sua ligação concreta
com a resolução do vídeo; dizer apenas que ela é simples não basta. Não faça uma pergunta aberta e
depois force uma resposta diferente. O comentário nunca é condição para bênção, cura, proteção,
milagre ou prova de fé.

Modos aceitos:

- `keyword_reaffirmation`;
- `next_step_commitment`;
- `sentence_completion`;
- `message_declaration`;
- `community_encouragement`.

O roteiro termina exatamente com o convite para comentar e depois:

> Se essa mensagem falou com você, curta o vídeo, se inscreva no canal e compartilhe com alguém que
> precisa ouvir isso.

No convite, a resposta esperada aparece entre aspas: `escreva “CORAGEM” nos comentários`. O áudio
insere um segundo de silêncio após a palavra ou frase, e a legenda a mantém sozinha durante a pausa.
Termos técnicos em inglês podem existir nos campos estruturados, mas nunca entram na locução.

## Contrato das fontes

Antes de preparar o roteiro, toda citação literal precisa registrar:

- `kind: biblical_text` e `verified: true`;
- `passage`, `translation` e `excerpt` com o texto exato;
- `author_or_publisher`, `accessed_at` e `rights_note`;
- `source_id` presente no roteiro e na estratégia.

O roteirista não reconstrói versículos de memória. O texto citado precisa coincidir com o `excerpt`
da fonte.

## Critérios de qualidade

Um roteiro está pronto quando:

- o gancho interessa antes de explicar;
- a Bíblia entra imediatamente depois e contém ao menos uma citação verificada;
- a citação é curta e clara sempre que a fonte permitir;
- o contexto impede distorção;
- o payoff resolve a pergunta inicial;
- a aplicação é concreta;
- a linguagem soa pastoral, cotidiana e natural;
- o comentário usa uma palavra-síntese expressiva, contextual e justificada pela resolução;
- o CTA pede curtir, inscrição e compartilhamento sem pressão;
- cada direção visual representa somente uma cena;
- a duração estimada cabe no alvo;
- a revisão bíblica não possui finding `blocking` aberto.
