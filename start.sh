#!/bin/bash
# Trinethra v3 — Quick Start
set -e

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'; BOLD='\033[1m'

echo ""
echo -e "${BOLD}  Trinethra · Supervisor Feedback Analyzer${NC}"
echo -e "  DeepThought — Software Developer Assignment"
echo ""

# 1. Ollama
if ! command -v ollama &>/dev/null; then
  echo -e "  ${RED}✗${NC} Ollama not found. Install from https://ollama.com then re-run."
  exit 1
fi
echo -e "  ${GREEN}✓${NC} Ollama found"

# 2. Model
MODEL=${1:-llama3.2}
if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
  echo -e "  ↓ Pulling $MODEL (first time only, may take a few minutes)…"
  ollama pull "$MODEL"
fi
echo -e "  ${GREEN}✓${NC} Model: $MODEL"

# 3. Python deps
cd "$(dirname "$0")/backend"
if ! python -c "import fastapi,uvicorn,requests" 2>/dev/null; then
  echo -e "  ↓ Installing Python dependencies…"
  pip install -r requirements.txt -q
fi
echo -e "  ${GREEN}✓${NC} Dependencies ready"

echo ""
echo -e "  ${BLUE}→${NC} Starting server at ${BOLD}http://localhost:8000${NC}"
echo -e "  Open that URL in your browser."
echo ""

python main.py
