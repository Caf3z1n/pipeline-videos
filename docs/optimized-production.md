# Produção otimizada

## Perfil atual

Todos os projetos novos usam `draft_one_shot` e `token_optimized_v1`. A prévia permanece em
resolução completa com CRF 16, preset `fast` e supersampling 2. O render final aprovado usa CRF 12,
preset `slow` e supersampling 3.

Não existem perfis legados, modos Light, produção guiada ou fallback de imagens manuais. Áudio
manual continua disponível porque muda somente a origem da locução e preserva o pipeline atual.

## Comando principal

Depois de briefing, fontes, roteiro e revisão válidos:

```powershell
uv run pipeline-videos autopilot <projeto> --allow-external
uv run pipeline-videos autopilot <projeto> --allow-external --wait-external
```

`run --dry-run` mostra o plano sem mutação. O autopilot prepara filas, executa áudio, legendas e
música, aguarda os ativos criativos, importa cada lote uma vez, renderiza e valida. Hashes e staging
válido tornam a retomada idempotente.

## Produção local

No cenário local, o roteiro de Short usa exatamente um `shorts_script_specialist` com raciocínio
alto. Imagens podem usar até dois workers com `fork_turns=none`; cada worker recebe somente seu job
compacto e escreve no staging declarado. O especialista de crescimento usa raciocínio médio,
recebe a imagem ativa da abertura e gera exatamente um par título+thumbnail integrado.

O agente principal é o único escritor de `project.json`, filas, manifestos, decisões e eventos. Ele
valida e importa roteiro, lote de imagens e crescimento de forma serializada.

## Produção remota

Na VPS, o fluxo, os contratos e a orquestração criativa são os mesmos:

- uma sessão persistente do coordenador por job;
- coordenador e especialistas em `gpt-5.6-sol`;
- coordenador em `medium`, roteirista em `high`, imagens em `low` e crescimento em `medium`;
- exatamente um roteirista, até dois workers de imagem e exatamente um especialista de crescimento;
- imagens processadas em até dois lotes paralelos com staging separado;
- exatamente um título e uma thumbnail.

O executor inicia a sessão uma vez e usa `codex exec resume` nos checkpoints seguintes. Áudio,
música, legendas, imports, render, validação e publicação continuam determinísticos no Python. Veja
[Produção remota](remote-production.md).

## Roteiro de Shorts

Prepare `script/shorts-queue.json`. O escritor autorizado produz
`script/staging/shorts-script-specialist/package.json` e executa o comando `preflight.command`
declarado na fila. O preflight é somente leitura e agrega todos os erros detectáveis. Normalizações
seguras são aplicadas deterministicamente; uma falha restante permite uma única correção antes de
encerrar.

O contrato editorial completo está em [Padrão de roteiro de Shorts](shorts-script-standard.md).

## Roteiro de vídeo longo

Projetos longos novos usam `retention_story_long_v1`. O `ScriptDocument` inclui
`long_form_strategy`, com um beat por seção, âncoras para todas as citações, reengajamentos
proporcionais à duração, payoff no quarto final, aplicação, callback e uma única ação de CTA.

O fluxo remoto produz e valida esse contrato antes de importar roteiro e revisão. No fluxo local, o
agente principal segue [Padrão de roteiro para vídeos longos](long-form-script-standard.md) e importa
o JSON estruturado com `import-script`. O validador rejeita duração insuficiente, abertura genérica,
trechos sem valor novo, citações agrupadas, intervalos bíblicos excessivos, reengajamentos escassos
e encerramento com mais de uma ação.

## Imagens

`images/codex-queue.json` contém uma imagem por item. A primeira tentativa usa o prompt integral.
Em `moderation_blocked`, repita o mesmo prompt uma vez; somente após o segundo bloqueio use uma
alternativa de ação ou enquadramento, preservando identidade, idade aparente, rosto, cabelo,
figurino, proporções, estilo, paleta, ambiente e sentido narrativo.

Cada ativo é salvo no `staging_path`; `images import-batch` importa o lote uma vez. Nunca regenere
item já importado ou aprovado.

## Crescimento e thumbnail

A fila exige exatamente um par complementar. O título concentra passagem, tema e busca; a capa
acrescenta tensão, decisão, contraste ou consequência. A thumbnail edita a imagem ativa da abertura
e nasce no Imagegen com texto, palavra vermelha e seta, X, sublinhado ou pincel incorporados.

O importador aceita somente `generated_integrated_art`. Não existe compositor local, overlay de
texto, reutilização de outra cena ou fallback para pacote antigo. Uma saída válida é aceita; retries
adicionais existem apenas para `moderation_blocked` sem bitmap.

O guard visual exige os marcadores positivos de `cinematic-family-3d-v1` e animação 3D, mas
desconsidera marcadores fotográficos declarados dentro de seções negativas como `Avoid:`. Se um
pacote pedir fotografia como estilo positivo ou falhar em outra regra determinística de crescimento,
o remoto permite um único checkpoint de correção por tentativa antes de voltar ao backoff.

## Áudio manual

Quando `audio.provider=local_manual`, o pipeline prepara `audio/manual-queue.json`, apresenta
`script/narration.txt` integralmente e aguarda um arquivo de voz limpo. `audio import-manual`
normaliza o áudio e usa WhisperX em CPU. Não usa ElevenLabs, `--allow-external` ou alinhamento
proporcional silencioso.

## Concorrência e segurança

- um único escritor por projeto;
- imports e montagem final serializados;
- projetos diferentes podem rodar em paralelo;
- subagentes locais e remotos escrevem somente em staging;
- nunca publicar, agendar, aprovar ou alterar faturamento sem autorização persistida.
