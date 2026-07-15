#!/usr/bin/env bash
set -euo pipefail

original="${SSH_ORIGINAL_COMMAND:-}"
pattern="^/home/deploy/pipeline-videos/bin/deploy-from-github[[:space:]]+'?([0-9a-f]{40})'?$"
if [[ "${original}" =~ ${pattern} ]]; then
  exec /home/deploy/pipeline-videos/bin/deploy-from-github "${BASH_REMATCH[1]}"
fi

echo "Comando não autorizado para a chave de deploy." >&2
exit 126
