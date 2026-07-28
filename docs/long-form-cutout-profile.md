# Perfil biblical_cutout_explainer_7m_v1

Perfil opt-in para vídeos bíblicos horizontais, com voz Matheus
(`36rVQA1AOIPwpA3Hg1tC`), ElevenLabs v3, assets e cenas 3D em massinha e edição
explicativa. A meta padrão é 420 segundos, mas a voz permanece natural em `1.0` e
define a duração efetiva.

A voz autoral usa `biblical_explainer_third_person_v1`: um narrador bíblico
explicativo, humano e concreto. Citações literais continuam claramente separadas
da explicação.

## Fluxo direto

```text
uma pesquisa e um roteiro estruturado
→ uma síntese com alinhamento por palavra
→ compilador local do plano visual e dos metadados
→ inventário semântico deduplicado
→ até dois workers para os assets ausentes
→ thumbnail local
→ um render final
→ checagem técnica da mídia
```

O roteiro decide, em uma única saída, a locução, os blocos visuais, os cues dos
assets, as sequências fullscreen, o título, a descrição e o conceito da
thumbnail. Depois do áudio, o compilador local apenas converte esse blueprint em
tempos e caixas. Não existe editor visual de modelo, especialista separado de
crescimento nem ciclo de reparo criativo.

O compilador executa somente invariantes baratas: o hash do roteiro e do áudio,
a continuidade dos tempos e a existência dos arquivos usados. Ele não tenta
julgar novamente a qualidade editorial já decidida no roteiro.

Criação manual:

```powershell
uv run pipeline-videos new primeira-mentira `
  --title "A primeira mentira no Éden" `
  --profile cutout-explainer
```

Criação automática com um tema verificado ainda não usado:

```powershell
uv run pipeline-videos new-long --duration 420
```

## Blueprint obrigatório do roteiro

Cada `strategy.visual_scenes` cobre literalmente um trecho contíguo da narração
e escolhe `presentation`, cues e ênfases antes do áudio.

- `assets_with_full_text_v1`: um asset grande ou dois a três assets menores,
  todos ancorados em palavras literais do trecho.
- `center_statement_v1`: referência bíblica centralizada ou, raramente, uma
  frase curta que realmente mereça pausa.
- `fullscreen_image_sequence_v1`: duas a quatro imagens distintas de uma ação,
  sem texto, recorte, background ou overlay.

Como direção de ritmo, asset + texto ocupa aproximadamente metade a dois terços
dos blocos, fullscreen aproximadamente um quarto e frase central fica quase
sempre restrita às referências. São proporções de composição, não contadores que
forçam cortes sem sentido.

Um asset só é reutilizável quando o blueprint declara `shareable=true`.
Descrições iguais sem essa intenção geram assets independentes. Sequências
fullscreen descrevem estágios diferentes da ação, não variações quase idênticas
do mesmo enquadramento.

## Regras de composição

### Asset e texto

- Texto e imagem ficam lado a lado, nunca um sobre o outro.
- O lado alterna entre os blocos.
- Um asset usa a maior parte da zona visual; dois ou três recebem slots
  proporcionais e distintos.
- O renderer mede a caixa disponível e ajusta a fonte por busca binária.
- Até duas palavras relevantes recebem escala maior e cor de ênfase.
- Se a fala não couber, o texto troca de página no mesmo lado.

### Referência e citação

- O nome exato da passagem, como `MATEUS 24:29`, ocupa sozinho o centro.
- Esse cartão nunca usa asset.
- O corpo da citação muda para asset + texto.
- O texto das páginas não contém aspas.
- O renderer abre uma única aspa na primeira página e fecha uma única aspa
  somente depois da última palavra.
- A locução pode apresentar a referência antes ou depois da citação. Não existe
  fórmula obrigatória com “lemos”.

### Sequência fullscreen

- Usa de duas a quatro imagens de ação em tela cheia.
- Cada imagem permanece aproximadamente de três a cinco segundos quando o ritmo
  da fala permitir.
- Bons exemplos: estrelas atravessando o céu e se aproximando da terra; uma
  figueira sendo sacudida pelo vento; mãos costurando uma vestimenta; uma mão
  alcançando um fruto.
- Não usa o background padrão nem qualquer texto sobre a imagem.

## Abertura magnética

Os primeiros segundos começam por ação visual, tensão concreta ou consequência.
O perfil não abre com uma sucessão de cartões de texto. A primeira referência
bíblica entra quando já responde ou aprofunda a pergunta, e não por obrigação de
formato.

Nos primeiros trinta segundos:

- pelo menos seis blocos;
- pelo menos dois tipos de bloco;
- nenhum asset repetido;
- a promessa do título e da thumbnail aparece imediatamente;
- cada mudança visual acompanha uma palavra, ação ou virada real da narração.

Depois da abertura, a edição desacelera sem ficar vazia. Um bloco muda quando a
ideia muda, não para cumprir uma contagem artificial.

## Linguagem

Use `conversational_concrete_ptbr_v1`: português falado, sujeito identificável,
verbo claro e consequência concreta.

Evitar:

> A ordem foi deslocada; a consequência foi contradita.

Preferir:

> A serpente mudou o sentido da ordem de Deus e negou a consequência que Ele
> havia anunciado.

O hook abre uma lacuna de informação específica, a progressão entrega pistas ou
evidências novas e o payoff responde exatamente à promessa. Curiosidade não
autoriza esconder a resposta indefinidamente nem fazer promessa falsa.

## Thumbnail e metadados

O mesmo roteiro produz uma combinação única de título, descrição e thumbnail.

- A arte usa fundo bíblico quente e texturizado.
- O texto em Knewave ocupa a área esquerda, em duas ou três linhas.
- Uma linha importante recebe vermelho queimado.
- Personagens e objetos 3D em massinha ocupam a área direita.
- A imagem gerada não contém texto; o compositor local aplica a tipografia.
- Título e thumbnail se completam sem repetir a mesma frase.
- O título usa termos cristãos relevantes, como Bíblia, Jesus, Deus ou Espírito
  Santo, somente quando forem verdadeiros para o tema.

O perfil grava `youtube/thumbnail.png`, `youtube/thumbnail.jpg`, título,
descrição, tags e comentário. Nenhum desses arquivos é enviado sem pedido
explícito.

## Efeitos sonoros

- `whoosh`: primeiro take e início de citação bíblica.
- `click`: entrada pontual de personagem ou objeto importante, com intervalo
  amplo.
- Nenhum outro efeito entra automaticamente.

## Artefatos

- `script/script.json`: locução, blueprint visual e pacote do YouTube.
- `audio/manifest.json`: alinhamento único por palavra.
- `visual/edit-plan.json`: saída determinística do compilador.
- `visual/asset-inventory.json`: inventário semântico.
- `visual/asset-queue.json`: somente assets ausentes.
- `youtube/thumbnail-plan.json`: composição local da capa.
- `youtube/thumbnail.png` e `youtube/thumbnail.jpg`: arquivos finais da capa.
- `audit/edit-plan-preflight.json`: invariantes técnicas mínimas, sem revisão
  criativa repetida.
