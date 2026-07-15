# Produção otimizada e autopilot

Este documento é carregado somente quando a tarefa realmente envolve criar, retomar ou renderizar
um vídeo. O objetivo é manter o contexto dos agentes pequeno sem reduzir os contratos editoriais,
bíblicos ou técnicos.

## Padrão para projetos novos

Projetos reais novos usam `token_optimized_v1`:

- roteiro e revisão bíblica continuam com raciocínio alto;
- trabalhadores de imagem usam contexto compacto, `fork_turns=none` e raciocínio baixo;
- o especialista do YouTube lê apenas `youtube/growth-context.json`;
- a prévia usa 1080p, CRF 16, preset `fast` e supersampling 2;
- o render final aprovado preserva V4, CRF 12, preset `slow` e supersampling 3;
- todos os formatos usam três thumbnails finais com texto e símbolos incorporados pelo imagegen;
  `remote_review` é a exceção aditiva e usa exatamente uma;
- scripts locais executam áudio, música, legendas, imports, render e validação.
- a locução usa somente português, com citações literais verificadas em média uma vez por minuto;
- a resposta pedida nos comentários fica entre aspas, isolada na legenda e seguida por um segundo
  de silêncio;
- títulos usam `Passagem | Tema`, a descrição recebe timeline bíblica e as legendas ficam no centro.

Projetos existentes sem esse perfil continuam em `legacy_v4` e não são migrados implicitamente.

## Comando principal

Depois que briefing, fontes, roteiro e revisão estiverem válidos:

```powershell
uv run pipeline-videos autopilot <projeto> --allow-external
```

O primeiro passe prepara filas e executa imediatamente a lane local. Quando houver saídas externas,
ele grava o resumo pequeno em `logs/autopilot-state.json`.

Inicie os workers declarados e use:

```powershell
uv run pipeline-videos autopilot <projeto> --allow-external --wait-external
```

O processo aguarda staging, importa cada lote uma única vez, compõe capas, renderiza a prévia e
valida o projeto. Uma falha pode ser retomada com o mesmo comando; hashes evitam repetir artefatos
válidos.

## Workers de imagem

Quando `images/codex-queue.json` estiver pendente:

1. Use no máximo dois `image_worker` quando a vaga de crescimento estiver reservada.
2. Crie cada worker sem histórico herdado (`fork_turns=none`).
3. Entregue somente `images/jobs/image-worker-N.json`.
4. O worker não deve ler o chat, `AGENTS.md`, `project.json` ou manifestos completos.
5. Faça uma chamada `imagegen` por job, preservando o prompt integral.
6. Salve exclusivamente no `staging_path` e nunca importe ou aprove ativos.
7. Processe o lote em uma única execução retomável sempre que a ferramenta permitir.

Cada imagem continua sendo uma composição única, sem colagem, storyboard, díptico, tríptico, tela
dividida, cenas empilhadas, divisor ou faixa branca para legenda. O padrão visual continua
`cinematic-family-3d-v1`.

## Especialista do YouTube

Crie exatamente um `youtube_growth_specialist` quando a fila estiver pendente. Use contexto vazio e
entregue somente `youtube/growth-queue.json`; a própria fila aponta para o contexto compacto.

- completo: raciocínio médio;
- Light: raciocínio baixo;
- nunca publica, aprova ou altera estado compartilhado;
- escreve somente em `youtube/staging/youtube-growth-specialist/`;
- em projetos novos de qualquer formato, chama `imagegen` uma vez por candidata e salva a
  thumbnail final já com texto e símbolo incorporados no staging declarado;
- entrega três títulos e três designs pareados para A/B test;
- trata cada índice como um par editorial: título para referência/tema/busca e capa para
  tensão/decisão/contraste, sem repetir a mesma promessa nos dois textos;
- usa conflito concreto, palavra de destaque vermelha e elemento narrativo com alvo real;
- confirma grafia e acentos e rejeita texto flutuante ou sobreposto aos personagens;
- segue somente orientações oficiais e nunca promete alcance ou viralização.

Em `remote_review`, não crie o subagente: o agente principal lê a mesma fila compacta, escreve o
staging declarado e importa uma única vez. A fila exige um título e um design integrados. O contrato
de complementaridade, palavra vermelha e elemento narrativo com alvo real permanece integral.

## Shorts

O padrão editorial completo permanece em `docs/shorts-script-standard.md`. No modo completo, use
exatamente um `shorts_script_specialist`; em modos Light, o agente principal produz o pacote com
raciocínio baixo. Todos os modos mantêm revisão bíblica, validações, ElevenLabs com
`--allow-external` e aprovação humana pendente.

No modo manual, pare para o envio das imagens e importe com `images import-manual`. No automático,
use os jobs compactos e o `autopilot`.

Quando `audio.provider=local_manual`, a lane local prepara `audio/manual-queue.json`, mostra ao
usuário o conteúdo integral de `script/narration.txt` e pausa em `manual_audio`. Após o anexo, o
agente principal executa `audio import-manual` uma vez. O importador normaliza o arquivo, chama o
WhisperX CPU em ambiente `uv` isolado, divide os tempos por cena e conclui a etapa de áudio. Esse
modo não usa ElevenLabs, não recebe `--allow-external` e nunca aceita alinhamento proporcional como
substituto silencioso.

## Escritor único e concorrência

- Subagentes escrevem somente em seus caminhos de staging.
- O agente principal é o único escritor de `project.json`, filas, manifestos, decisões e eventos.
- A lane local é serializada em `audio -> captions -> music`; no áudio manual, ela aguarda o anexo
  antes de prosseguir.
- Imports e montagem final são serializados.
- O render usa no máximo dois segmentos FFmpeg independentes em paralelo.
- Projetos diferentes podem rodar em paralelo; o mesmo projeto mantém um escritor único.

## Limite da automação

O processo local não possui endpoint para chamar a geração de imagens embutida na assinatura do
Codex. Por isso o `autopilot` automatiza todo o trabalho determinístico, mas ainda depende de workers
para `imagegen`. Não trocar por API paga, browser ou pay-as-you-go sem aprovação explícita.

Na VPS, `codex exec` é o único processo agente e pode consumir essas filas diretamente. O serviço
local continua responsável por lock, retomada, validação, upload privado e eventual agendamento;
isso não cria uma API paga de imagens e não autoriza publicação fora do pedido persistido.
