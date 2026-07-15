#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -un)" != "deploy" ]]; then
  echo "Execute como o usuário deploy." >&2
  exit 1
fi

base="${PIPELINE_VIDEOS_BASE_DIR:-/home/deploy/pipeline-videos}"

sudo apt-get update
sudo apt-get install -y ca-certificates curl ffmpeg git fonts-dejavu-core

if ! command -v uv >/dev/null 2>&1 && [[ ! -x /home/deploy/.local/bin/uv ]]; then
  installer="/tmp/pipeline-videos-uv-installer.sh"
  curl -LsSf https://astral.sh/uv/install.sh -o "${installer}"
  sh "${installer}"
fi

mkdir -p "${base}/releases" "${base}/shared/jobs" "${base}/shared/projects"
mkdir -p "${base}/shared/music-library" "${base}/bin"
chmod 700 "${base}/shared/jobs"

if ! swapon --show=NAME --noheadings | grep -q .; then
  swapfile="/swapfile-pipeline-videos"
  if [[ ! -f "${swapfile}" ]]; then
    sudo fallocate -l 2G "${swapfile}"
    sudo chmod 600 "${swapfile}"
    sudo mkswap "${swapfile}"
  fi
  sudo swapon "${swapfile}"
  if ! grep -Fq "${swapfile} none swap sw 0 0" /etc/fstab; then
    sudo cp /etc/fstab "/etc/fstab.bak-pipeline-videos-$(date +%Y%m%d-%H%M%S)"
    echo "${swapfile} none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
  fi
fi

echo "Bootstrap concluído em ${base}."
