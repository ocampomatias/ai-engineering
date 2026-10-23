#!/usr/bin/env bash
# Arranque en una instrucción: API + Redis 8 + Phoenix con Docker Compose.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Creé .env desde .env.example. Completá ANTHROPIC_API_KEY (y PINECONE_API_KEY para el RAG en Pinecone) y volvé a correr ./run.sh"
  exit 1
fi

docker compose up --build -d
echo "Consola:  http://localhost:8000"
echo "API docs: http://localhost:8000/docs"
echo "Phoenix:  http://localhost:6006"
