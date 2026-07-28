---
product: "Pipeline de vídeos bíblicos"
surface: "biblical_cutout_explainer_7m_v1"
updated: "2026-07-28"
---

## Overview

Sistema visual educacional bíblico em 16:9. Massinha 3D fosca, fundo de pergaminho quente e
tipografia expressiva criam uma aparência acessível, colecionável e reconhecível. A composição
privilegia leitura imediata, relação literal com a narração e ausência de sobreposição.

## Colors

- `parchment-light`: `#F4DFAA`, base clara para blocos explicativos.
- `parchment-deep`: `#D8AD68`, textura e profundidade do fundo.
- `ink`: `#312424`, texto principal.
- `accent`: `#A13D30`, ênfase, palavra-chave e linha forte da thumbnail.
- `clay-green`: `#7F9C38`, serpentes, plantas e elementos naturais.
- `cream`: `#FFF1D1`, brilho e contraste suave.

## Typography

- Display: Knewave, caixa alta, peso visual forte e espaçamento compacto.
- Texto de bloco: Knewave com ajuste automático ao espaço.
- Palavras de ênfase: até 1,35 vezes o tamanho base e cor `accent`.
- Thumbnail: duas ou três linhas, com uma linha em `accent`.
- Referência bíblica: centralizada, sem asset e sem cartão adicional.

## Spacing

- Margem segura mínima: 5,5% do quadro.
- Asset + texto: duas zonas laterais independentes com respiro central.
- Um asset: zona visual dominante.
- Dois assets: colunas equilibradas dentro da metade visual.
- Três assets: dois acima e um abaixo, sem cruzar a zona do texto.
- Fullscreen: quadro inteiro, sem overlay.

## Components

- `AssetTextBlock`: texto paginado e um a três assets ao lado, alternando orientação.
- `BibleReferenceCard`: referência curta centralizada, sem asset.
- `BibleQuoteBlock`: citação completa em asset + texto, com aspas controladas pelo renderer.
- `CenterStatement`: frase curta e rara, centralizada.
- `FullscreenActionSequence`: duas a quatro ações distintas em tela cheia.
- `YouTubeThumbnail`: texto à esquerda e composição bíblica 3D à direita.

## Guidelines

- Começar o vídeo com ação, personagem, objeto ou consequência visual.
- Usar somente assets com cue literal na fala.
- Não sobrepor texto, personagem, objeto ou sombra de um elemento a outro.
- Não reutilizar asset no hook.
- Dar de três a cinco segundos para cada imagem fullscreen quando a fala permitir.
- Evitar ícones genéricos de pergunta, luz, seta ou emoção quando a narrativa oferece uma cena
  bíblica concreta.
- Fazer título, thumbnail e primeiro trecho responderem à mesma promessa.

