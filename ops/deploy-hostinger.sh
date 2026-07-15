#!/usr/bin/env bash
set -euo pipefail

sha="${1:-}"
if [[ ! "${sha}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Informe o SHA Git completo do release." >&2
  exit 2
fi

base="${PIPELINE_VIDEOS_BASE_DIR:-/home/deploy/pipeline-videos}"
repo_url="${PIPELINE_VIDEOS_REPO_URL:-git@github.com-pipeline-videos:Caf3z1n/pipeline-videos.git}"
release="${base}/releases/${sha}"
temporary="${base}/releases/.incoming-${sha}-$$"
export PATH="/home/deploy/.local/bin:${PATH}"

mkdir -p "${base}/releases" "${base}/shared/jobs" "${base}/shared/projects"
mkdir -p "${base}/shared/music-library" "${base}/bin"

if [[ ! -d "${release}/.git" ]]; then
  git clone --filter=blob:none --no-checkout "${repo_url}" "${temporary}"
  git -C "${temporary}" checkout --detach "${sha}"
  mv "${temporary}" "${release}"
fi

cd "${release}"
uv sync --frozen --no-dev

if [[ ! -L projects ]]; then
  mv projects projects.packaged
  ln -s "${base}/shared/projects" projects
fi
if [[ ! -L music-library ]]; then
  mv music-library music-library.packaged
  ln -s "${base}/shared/music-library" music-library
fi
ln -sfn "${base}/shared/.env" .env

uv run --frozen pipeline-videos doctor

ln -sfn "${release}" "${base}/current.next"
mv -Tf "${base}/current.next" "${base}/current"

install -m 0755 ops/deploy-hostinger.sh "${base}/bin/deploy-from-github"
install -m 0755 ops/video-job.sh "${base}/bin/video-job"
install -m 0755 ops/github-actions-dispatch.sh "${base}/bin/github-actions-dispatch"
sudo install -m 0644 ops/systemd/pipeline-videos-worker.service \
  /etc/systemd/system/pipeline-videos-worker.service
sudo install -m 0644 ops/systemd/pipeline-videos-worker.timer \
  /etc/systemd/system/pipeline-videos-worker.timer
sudo systemd-analyze verify /etc/systemd/system/pipeline-videos-worker.service
sudo systemd-analyze verify /etc/systemd/system/pipeline-videos-worker.timer
sudo systemctl daemon-reload
sudo systemctl enable --now pipeline-videos-worker.timer

echo "Release ${sha} ativado em ${base}/current"
