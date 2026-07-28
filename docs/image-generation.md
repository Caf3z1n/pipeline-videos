# Geração de imagens

## Padrão único

Shorts e vídeos longos usam exclusivamente `biblical-midcentury-gouache-v1`.

O resultado esperado é uma ilustração editorial cristã de meados do século, criada em guache opaco
e têmpera, com acabamento fosco, impressão offset limpa, formas simples, rostos serenos, luz natural
difusa e paleta suavizada.

Cada imagem representa:

- um momento;
- um ambiente;
- uma ação;
- um foco visual dominante.

Não use colagem, quadrinhos, painéis, texto legível, fotografia, live action, CGI, anime, low-poly
ou aparência plástica.

## Planejamento por blocos

`narration_block_visual_v1` é obrigatório em todos os formatos.

`strategy.visual_scenes` divide literalmente a locução em blocos contíguos. Cada item declara:

- `section_id`;
- trecho exato de `narration`;
- `scene_description` concreta e distinta;
- `visual_relation`: `literal`, `contextual` ou `symbolic`.

A concatenação dos blocos precisa reconstruir integralmente cada seção. A ordem das cenas acompanha
a ordem da locução.

O primeiro bloco usa `scroll_stop_concrete_v1`: plano médio, close ou objeto específico; ação já em
curso; tensão, contraste ou escolha ligada à primeira frase.

## Continuidade

Ao repetir personagens, preserve idade aparente, rosto, cabelo, figurino, proporções, silhueta e
paleta. A continuidade não impede variar câmera, ambiente, objeto, ação ou simbolismo.

## Fila

`images/codex-queue.json` contém somente itens pendentes ou sem staging válido. Quando
`common_prompt_prefix` estiver presente, o prompt final é exatamente a concatenação desse prefixo
com `prompt_delta`.

Workers:

- recebem contexto compacto;
- escrevem somente no staging declarado;
- materializam cada resultado com `images stage-generated`, usando somente o `savedPath` absoluto
  retornado pelo Imagegen;
- nunca tratam `result`/base64, `revisedPrompt` ou `output_hint` como caminho de arquivo;
- não alteram `project.json`;
- não importam nem aprovam ativos;
- usam no máximo duas execuções paralelas.

## Tentativas

Cada tentativa faz uma única chamada de geração.

1. Use o prompt integral.
2. Se houver `moderation_blocked`, repita o mesmo prompt uma vez.
3. Se bloquear novamente, faça uma única alternativa de cena.

A alternativa pode mudar ação, pose, câmera, enquadramento ou objetos não essenciais. Deve preservar
identidade, idade aparente, rosto, cabelo, figurino, proporções, silhueta, estilo, paleta, ambiente e
sentido.

## Importação

Copie cada arquivo para o `staging_path` declarado com o comando congelado no próprio job:

```powershell
uv run pipeline-videos images stage-generated <projeto> `
  --job-id <scene-NNN-vNNN> `
  --source "<savedPath absoluto retornado pelo Imagegen>"
```

O comando aceita somente PNG íntegro dentro de `.codex/generated_images`, deriva o destino da fila
e faz uma cópia atômica. Se ele falhar, preserve e reporte o `savedPath`; não regenere a imagem para
corrigir uma falha de cópia. O coordenador importa o lote uma única vez com hashes e preserva
versões anteriores. Imagens aprovadas nunca são sobrescritas ou regeneradas.

Na produção remota, o hook `PostToolUse` executa esse handoff automaticamente a partir do
`savedPath` retornado pelo próprio `imagegen`. O hash do prompt identifica de forma inequívoca o job
e o staging autorizado, inclusive quando a chamada foi feita em uma sessão isolada de worker. Um
recibo persistido permite repetir somente a cópia caso o hook seja interrompido; a imagem não é
regenerada para reparar materialização.
