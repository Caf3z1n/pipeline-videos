#!/usr/bin/env bash
set -euo pipefail

base="${PIPELINE_VIDEOS_BASE_DIR:-/home/deploy/pipeline-videos}"
export PATH="/home/deploy/.local/bin:${PATH}"
export PIPELINE_VIDEOS_REMOTE_JOBS_DIR="${PIPELINE_VIDEOS_REMOTE_JOBS_DIR:-${base}/shared/jobs}"

if [[ ! -d "${base}/current" ]]; then
  echo "Release ativo ausente em ${base}/current" >&2
  exit 1
fi

if [[ "${1:-}" == "submit" ]]; then
  shift
  set -- enqueue "$@"
fi

cd "${base}/current"
exec uv run --frozen pipeline-videos remote "$@"
