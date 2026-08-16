#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "== Doom Core V0.12.19 setup =="

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js not found."
  exit 1
fi

NODE_MAJOR="$(node -p "Number(process.versions.node.split('.')[0])")"
if [ "$NODE_MAJOR" -lt 22 ]; then
  echo "ERROR: Node >=22 required. Found $(node -v)"
  exit 1
fi

echo "Node: $(node -v)"
echo "npm:  $(npm -v)"

npm install

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env"
fi

npm run typecheck
echo
echo "Setup complete."
echo "Run: ./run.sh"
