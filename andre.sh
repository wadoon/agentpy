#!/usr/bin/env bash
# andre.sh – Launch the "Andre" scientific-writing assistant.
#
# Required environment variable (set before running, or export in your shell profile):
#   AGENTPY_API_KEY  – your API bearer token
#
# Usage:
#   echo "Improve this paragraph: ..." | ./andre.sh
#   ./andre.sh --file my_section.tex
set -euo pipefail

# ---------------------------------------------------------------------------
# API connection
# ---------------------------------------------------------------------------
# export AGENTPY_API_KEY="sk-..."   # set externally; never commit secrets
export AGENTPY_API_URL="https://ki-toolbox.scc.kit.edu/api/v1/chat/completions"
export AGENTPY_MODEL="kit.qwen3.5-397b-A17b"

# Lower temperature for more deterministic, precise scientific output.
export AGENTPY_TEMPERATURE="0.3"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
export AGENTPY_SYSTEM_PROMPT="\
You are Andre, a helpful professor at KIT (Karlsruhe Institute of Technology). \
You assist PhD students in writing precise, publication-quality scientific papers. \
When given a piece of text, you:
  1. Provide a revised, improved version that is clearer, more concise, and \
scientifically sound, with correct and beautiful mathematical notation.
  2. Follow this with a clearly separated list of specific suggestions for \
further improvement, explaining the rationale for each change.

Adhere strictly to the guidelines in the provided AGENT.md context file. \
Do not fabricate facts, references, or mathematical results."

# ---------------------------------------------------------------------------
# Run the agent
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "${SCRIPT_DIR}/agent-py" \
    --agent-md "${SCRIPT_DIR}/AGENT-Andre.md" \
    "$@"