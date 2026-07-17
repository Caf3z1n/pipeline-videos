# Contrato do agente remoto com paridade local

O executor remoto usa o mesmo fluxo dos projetos locais. Cada job mantém uma sessão raiz própria do
coordenador em `gpt-5.6-sol`, com `model_reasoning_effort=medium` e multi-agent habilitado.

## Divisão de responsabilidades

O Python é o único escritor do estado compartilhado. Ele executa locks, `status`, `run --dry-run`,
autopilot, imports, ElevenLabs, música, legendas, render, validação, upload privado e agendamento.

O coordenador retoma a mesma sessão raiz e delega os checkpoints criativos:

1. seleção de fontes verificadas, quando necessária, pelo coordenador em Sol/medium;
2. exatamente um `shorts_script_specialist` em Sol/high;
3. até dois `image_worker` em Sol/low, paralelos e com staging separado;
4. exatamente um `youtube_growth_specialist` em Sol/medium após a abertura estar importada.

Cada especialista parte com `fork_turns=none`, recebe somente seu JSON compacto e escreve apenas nos
arquivos de staging declarados. O coordenador aguarda todos; o Python valida e importa uma vez.

## Sessões

Cada job recebe um `codex_session_id` exclusivo. O primeiro checkpoint usa `codex exec`; os
seguintes usam `codex exec resume <session_id>`. Os subagentes são criados dentro dessa sessão raiz,
mas não herdam o histórico. Dois Shorts diários são dois jobs, duas sessões raiz e conjuntos de
especialistas independentes.

## Restrições

- modelo fixo do coordenador e dos especialistas: `gpt-5.6-sol`;
- raciocínio por função: coordenador `medium`, roteiro `high`, imagens `low`, crescimento `medium`;
- no máximo dois workers de imagem simultâneos;
- apps, browser, memories, goals e busca web desativados;
- somente os arquivos declarados em `allowed_inputs` podem ser lidos em cada checkpoint;
- agentes nunca leem segredos, publicam, agendam, aprovam ou alteram faturamento;
- filas antigas, modos Light e fallbacks de thumbnail local não são aceitos em projetos novos.

## Validação e retomada

Antes do import do roteiro de Short, o executor roda o preflight agregado. Normalizações
determinísticas são aplicadas sem nova chamada. Se ainda houver erros, o coordenador cria uma
correção especializada; uma segunda falha encerra o job.

Stagings válidos são reutilizados. Para imagens, a política continua limitada ao prompt original,
uma repetição idêntica após `moderation_blocked` e uma alternativa visual restrita. Nenhum
especialista altera manifestos ou aprova artefatos.

## Telemetria

Cada checkpoint do coordenador é registrado em `codex_runs` com modelo, raciocínio e uso retornado
pelo JSONL. Hooks de `PostToolUse`, `PreCompact` e `Stop` registram apenas eventos e tamanhos, sem
conteúdo de prompt, resposta ou segredo e sem escrever em `stdout`.
