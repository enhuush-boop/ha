#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

echo "[1/4] Creating Python 3.12 virtual environment (.venv)"
uv venv --python 3.12 .venv

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/4] Installing matched CPU PyTorch + torchvision"
# PyPI's torchvision wheel is CUDA-built. Install the matching CPU pair from
# PyTorch's index so `import torchvision` works on CPU-only machines.
uv pip install --index-url https://download.pytorch.org/whl/cpu \
  torch==2.14.1 torchvision==0.29.1

echo "[3/4] Installing backend dependencies"
uv pip install -r backend/requirements.txt

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  echo "[4/4] Created backend/.env from the example. Edit it to add API keys."
else
  echo "[4/4] backend/.env already exists; leaving it unchanged."
fi

echo
echo "Environment ready: .venv/ and backend/.env"
echo
echo "Next steps:"
echo "  1. Download the pinned model weights (~1.2 GB, one time):"
echo "       .venv/bin/python scripts/warmup_models.py"
echo "  2. Start the backend (keep this terminal open):"
echo "       ./scripts/run_backend.sh"
echo "  3. Load extension/ as an unpacked extension in Chrome/Edge:"
echo "       chrome://extensions -> Developer mode -> Load unpacked"
