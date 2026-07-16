# Contrato compacto do agente remoto

Este contrato substitui a leitura repetida de `AGENTS.md`, `README.md` e dos documentos completos
dentro de cada execução do Codex na VPS. O executor serializa somente a fase atual em
`shared/jobs/contexts/<job>/run-NNN-<fase>/phase-request.json`.

## Divisão de responsabilidades

O Python é o único escritor do estado compartilhado. Ele executa `status`, `run --dry-run`,
autopilot, imports, ElevenLabs, música, legendas, render, validação, upload privado e eventual
agendamento. O Codex nunca executa o job inteiro.

Cada `codex exec` é efêmero e atende exatamente uma destas fases:

1. seleção de fontes verificadas, somente quando o briefing ainda não está completo;
2. roteiro de Short ou roteiro/revisão de vídeo longo;
3. uma única imagem de cena;
4. um único par título+thumbnail.

O JSON da fase informa entradas permitidas, saída obrigatória, modelo, raciocínio e política de
retry. Imagens recebem apenas o próprio item e, quando existe, a imagem anterior de continuidade.
Roteiro e crescimento recebem somente suas filas e contextos compactos.

## Isolamento de contexto

As execuções usam `--ephemeral`, `--ignore-user-config` e `--ignore-rules`, com diretório de trabalho
fora do release Git. Apps, plugins, browser/computer use, goals, memories, multi-agent e busca web
ficam desativados. Apenas shell, arquivos, hooks e `image_generation` permanecem disponíveis. A
autenticação existente do Codex continua sendo usada; isso não cria API paga nem exige novo OAuth.

O modelo e o raciocínio seguem a fila. Por padrão, todas as fases usam `gpt-5.6-terra`; variáveis de
ambiente específicas continuam podendo sobrescrever o modelo de uma fase. No `remote_review`,
roteiro de Short, imagens, crescimento e seleção editorial usam raciocínio baixo. Roteiro longo usa
raciocínio alto; uma revisão longa isolada usa raciocínio médio.

Antes de importar um roteiro de Short, o executor valida o staging. Confusões conhecidas entre
`hook_type` e `hook_techniques`, desalinhamentos entre a espinha narrativa e suas seções e bases
bíblicas omitidas são normalizados deterministicamente, sem nova chamada ao Codex. Se o pacote
continuar inválido, ocorre no máximo uma fase curta `shorts_script_repair`, que lê somente a fila e
o pacote inválido, preserva o conteúdo válido e corrige apenas o contrato. Uma segunda falha encerra
o job para evitar consumo repetitivo.

## Hooks

O executor habilita hooks inline e revisados para `PostToolUse`, `PreCompact` e `Stop`. O script
`scripts/remote_codex_hook.py` grava somente evento, modelo, fase, ferramenta e tamanhos de entrada e
saída. Ele não grava conteúdo, não lê transcript e não escreve em `stdout`, portanto não injeta
tokens adicionais no contexto.

Hooks são telemetria, não o mecanismo principal de economia. O ganho vem das sessões curtas e da
remoção das etapas determinísticas do agente. Como `suppressOutput` ainda não reduz a saída entregue
ao modelo, o hook não tenta filtrar respostas de ferramentas.

## Retomada e medição

Cada fase concluída é persistida em `codex_runs` no JSON do job, com modelo, raciocínio e os campos
de uso retornados por `codex exec --json`: input, input em cache, output e reasoning output. O status
expõe também input novo e total novo+output.

Stagings válidos são reutilizados. Em imagem, cada item mantém no máximo três tentativas: prompt
original, repetição idêntica após `moderation_blocked` e uma alternativa restrita que preserva
identidade e continuidade. Imports continuam acontecendo uma única vez pelo executor.
