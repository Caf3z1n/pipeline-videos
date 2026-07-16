# Contrato do agente remoto persistente

O executor remoto usa o mesmo fluxo de produção dos projetos locais. A diferença operacional é
que cada job mantém uma única sessão do Codex durante toda a produção, com `gpt-5.6-sol`,
`model_reasoning_effort=medium` e multi-agent desativado.

## Divisão de responsabilidades

O Python continua sendo o único escritor do estado compartilhado. Ele executa locks, `status`,
`run --dry-run`, autopilot, imports, ElevenLabs, música, legendas, render, validação, upload privado
e eventual agendamento.

O agente principal permanece na mesma sessão e executa os checkpoints criativos na ordem:

1. seleção de fontes verificadas, quando o briefing ainda não estiver completo;
2. roteiro e eventual correção contratual;
3. imagens de cena, sequencialmente;
4. um par título+thumbnail integrado.

Cada checkpoint recebe um JSON compacto em
`shared/jobs/contexts/<job>/main-agent/checkpoints/`. A primeira chamada usa `codex exec`; as
seguintes usam `codex exec resume <session_id>`. O `session_id` fica persistido no job, portanto uma
retomada do worker continua a conversa existente em vez de abrir outra sessão.

## Restrições

- modelo fixo: `gpt-5.6-sol`;
- raciocínio fixo: `medium`;
- nenhum subagente ou worker Codex;
- apps, browser, memories, goals e busca web desativados;
- somente os arquivos declarados em `allowed_inputs` podem ser lidos em cada checkpoint;
- o agente nunca lê segredos, publica, agenda, aprova ou altera faturamento;
- imagens são geradas uma por vez e copiadas para o `staging_path` da fila;
- filas antigas, modos Light e fallbacks de thumbnail local não são aceitos.

## Validação e retomada

Antes do import do roteiro de Short, o executor roda o preflight agregado. Normalizações
determinísticas são aplicadas sem nova chamada. Se ainda houver erros, a mesma sessão recebe no
máximo um checkpoint curto de correção; uma segunda falha encerra o job.

Stagings válidos são reutilizados. Para imagens, a política continua limitada ao prompt original,
uma repetição idêntica após `moderation_blocked` e uma alternativa visual restrita. A recuperação de
PNG procura somente arquivos novos da sessão persistente e nunca substitui staging válido.

## Telemetria

Cada checkpoint concluído é registrado em `codex_runs` com modelo, raciocínio e uso retornado pelo
JSONL. Hooks de `PostToolUse`, `PreCompact` e `Stop` registram apenas eventos e tamanhos, sem conteúdo
de prompt, resposta ou segredo e sem escrever em `stdout`.
