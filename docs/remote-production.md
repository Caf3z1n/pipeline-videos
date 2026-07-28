# Produção remota

## Modelo operacional

Cada job remoto possui:

- uma pasta própria;
- uma sessão persistente do coordenador;
- checkpoints retomáveis;
- um projeto independente;
- estado serializado e idempotente.

O executor remoto cria o mesmo contrato do fluxo local. A pasta do projeto continua sendo a fonte
de verdade.

## Modelos e agentes

- coordenador: `gpt-5.6-sol`, raciocínio médio;
- especialista de roteiro: `gpt-5.6-sol`, raciocínio alto;
- validação de fontes incorporada, sem agente revisor;
- planejador visual compacto: `gpt-5.6-sol`, raciocínio médio;
- especialista de metadados: `gpt-5.6-sol`, raciocínio médio;
- workers de imagem: `gpt-5.6-sol`, raciocínio baixo.

Use no máximo dois workers de imagem simultaneamente. Todos partem com `fork_turns=none` e contexto
compacto.

## Responsabilidades

O coordenador:

- cria e retoma especialistas;
- serializa escrita de estado;
- importa roteiro, revisão, imagens e metadados;
- conduz o pipeline até o candidato final.

Workers:

- escrevem somente no staging declarado;
- não alteram `project.json`;
- não importam;
- não aprovam;
- não publicam.

Áudio, música, legendas, imports, render, validação, upload e agendamento são determinísticos e não
devem ser delegados ao modelo.

Ao concluir os ativos criativos, o modelo sai do fluxo. O executor remoto continua até o upload,
grava `events/<job-id>.json` a cada transição e materializa `terminal/<job-id>.json` somente em
`completed`, `failed` ou `cancelled`. `remote wait` aguarda esse arquivo no próprio servidor, sem
polling por um agente.

## Roteiro e cenas

Toda locução autoral usa `jesus_first_person_v1`. Jesus fala em primeira pessoa, e citações literais
permanecem claramente separadas e verificadas.

Shorts e vídeos longos usam o mesmo padrão visual `biblical-midcentury-gouache-v1` e o mesmo
planejamento `narration_block_visual_v1`. Cada bloco de narração recebe uma cena concreta e distinta.

O especialista de metadados não depende de imagens e produz somente um título, descrição, tags e
comentário fixado.

## Imagens

Cada item pendente da fila vira um job isolado. Em `moderation_blocked`:

1. repita exatamente o mesmo prompt;
2. após novo bloqueio, use uma alternativa equivalente;
3. preserve identidade, idade aparente, rosto, cabelo, figurino, proporções, silhueta, estilo,
   paleta, ambiente e sentido.

O coordenador importa o lote uma única vez.

Na VPS, cada cena usa um checkpoint com exatamente um worker e um output esperado. Essa isolação
permite ao executor identificar sem ambiguidade o único PNG novo criado durante a fase, mesmo que o
worker rode em outra sessão e o hook do coordenador não receba o retorno do `imagegen`.

O hook `PostToolUse` continua como caminho rápido: recebe o `savedPath`, confere o hash do prompt
contra o mapa imutável da fase, valida que o arquivo é um PNG `exec-*.png` dentro de
`.codex/generated_images` e o copia atomicamente. Se não houver recibo, o executor compara o
snapshot anterior e posterior de todas as sessões de imagem. A recuperação cross-session só ocorre
quando existe exatamente um PNG novo e um destino; zero ou mais de um candidato bloqueiam a fase
sem associação por ordem, nome ou suposição.

Uma fase de imagens é considerada concluída quando todos os outputs declarados são PNGs válidos e a
identidade da sessão persistente foi confirmada, mesmo que o processo do agente encerre com erro
depois de entregar os artefatos.

## Retomada

`autopilot --wait-external` mantém a mesma execução até que uma integração externa produza o
artefato esperado. Polling de estado usa `status --json`; não abra novas sessões apenas para
consultar progresso.

Falhas recuperáveis preservam o projeto, os arquivos produzidos e a sessão raiz. O retry retoma do
último checkpoint válido. Cada falha recebe uma assinatura estável por fase e causa. O servidor
registra a ação e o resultado em `recovery-learnings.json`; recuperações determinísticas conhecidas
passam a valer para todos os jobs seguintes sem alterar código em produção.

Uma falha idêntica de materialização de imagem ou identidade de sessão é interrompida na segunda
ocorrência; credencial OAuth revogada ou expirada não é repetida. Falhas ainda não classificadas
mantêm o teto global de sete recuperações. Ao atingir qualquer limite, o job termina em `failed` e
emite um evento com `agent_attention_required=true`; ele não permanece em repetição infinita.

O aprendizado automático é deliberadamente limitado a políticas seguras já implementadas:
reutilizar recibos, recuperar o único artefato novo cross-session, preservar arquivos, retomar
checkpoints e evitar repetição idêntica. Uma causa nova não modifica o próprio código do servidor;
ela fica auditada para que uma correção revisada seja testada e implantada antes de virar política
automática.

## Medição de uso do Codex

Jobs materializados por agendas diárias formam um lote pela data local da ocorrência. O executor
consulta `account/rateLimits/read` no `codex app-server` autenticado da VPS:

- imediatamente antes de o primeiro job devido do lote mudar para `running`;
- imediatamente depois de todos os jobs do lote atingirem `completed`, `failed` ou `cancelled`.

A medição não inicia turnos de modelo. Ela persiste nos próprios jobs somente as janelas de limite,
percentuais usado/restante, horário de reset e o delta da janela semanal. Tokens e credenciais de
autenticação nunca são gravados. Falha nessa telemetria fica registrada em
`codex_account_usage.error`, mas não bloqueia produção, render, upload ou agendamento.

O campo `codex_account_usage.weekly_usage_delta_percent` só é calculado quando as duas leituras
pertencem à mesma janela semanal. Se houver reset entre elas, as leituras brutas permanecem
disponíveis e o delta fica vazio.

## Lane econômica de roteiros aprovados

`approved_fast_v1` recebe um snapshot editorial imutável. O pacote contém briefing, fontes,
narração, revisão bíblica sem bloqueios, plano visual por blocos, CTA escolhido, título, descrição,
três hashtags específicas, `#biblia`, `#jesus` e hash. A aprovação é uma ação explícita do usuário
vinculada ao hash; o pipeline nunca a inventa.

As cinco hashtags acima ficam no final da descrição. O campo separado de tags do YouTube recebe
automaticamente de dez a doze termos distintos, relevantes e sem `#`; esse campo técnico não
precisa ser exibido durante a aprovação editorial.

Na finalização remota:

- não há seleção de tema;
- não há roteirista nem nova revisão;
- não há especialista de metadados; o pacote SEO aprovado é reutilizado literalmente;
- até dois workers geram somente as imagens ainda ausentes;
- áudio, legendas, música, import, render, validação e upload continuam determinísticos;
- qualquer alteração no roteiro, fontes, revisão ou plano visual quebra o lock antes do áudio ou
  do upload.

A agenda `approved_pool` reserva cada roteiro atomicamente. Um roteiro não pode alimentar dois
jobs. Quando a fila se esgota, a agenda persiste o motivo e não usa o catálogo automático como
fallback.

## Upload e agendamento

Upload exige pedido explícito. `private_review` usa `render-review`, validação concluída e upload
privado; não registra aprovação humana.

Agendamento automático só é permitido se a requisição original trouxer:

- `publication_mode=scheduled`;
- `publish_at` explícito;
- fuso horário.

O executor nunca compra créditos, habilita cobrança ou altera faturamento.

Antes do primeiro byte enviado, o projeto persiste `youtube/upload-intent.json`. Se a conexão cair
num ponto em que não seja possível confirmar se o YouTube criou o vídeo, o estado fica
`indeterminate` e o executor bloqueia novo envio automático. Quando `publication.json` existe, a
retomada reutiliza o mesmo `video_id`.
