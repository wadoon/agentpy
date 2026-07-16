# agentpy

A simple, dependency-free general-purpose AI agent for the command line.

- **Works with any OpenAI-compatible API** (OpenAI, Ollama, LM Studio, vLLM, …)
- **MCP (Model Context Protocol) tool support** – attach any MCP server to give the agent real-world capabilities
- **Context injection** – pipe files, `AGENT.md`, and `SKILLS.md` straight into the prompt
- **Zero runtime dependencies** – pure Python standard library, Python ≥ 3.11

---

## Installation

### pipx (recommended for CLI use)

```bash
pipx install git+https://github.com/example/agentpy.git
```

### uv

```bash
# run without installing
uvx --from git+https://github.com/example/agentpy.git agent

# or install into a project / virtual environment
uv add git+https://github.com/example/agentpy.git
```

### pip

```bash
pip install git+https://github.com/example/agentpy.git
```

### From source

```bash
git clone https://github.com/example/agentpy.git
cd agentpy
pip install -e .
```

After installation the `agent` command is available on your `$PATH`.

---

## Quick start

```bash
export AGENTPY_API_KEY="sk-..."

# Ask a question
echo "What is the capital of France?" | agent

# Explain a file
echo "Summarise this script" | agent --file agent.py

# Use a local model via Ollama
echo "Hello!" | agent --url http://localhost:11434/v1/chat/completions --model llama3
```

---

## Configuration

All options can be set via **environment variables** or **CLI flags** (flags take precedence).

| Environment variable  | Default                                           | Description                    |
| --------------------- | ------------------------------------------------- | ------------------------------ |
| `AGENTY_API_KEY`      | _(empty)_                                         | Bearer token sent to the API   |
| `AGENTPY_API_URL`     | `https://api.openai.com/v1/chat/completions`      | Chat completions endpoint      |
| `AGENTPY_MODEL`       | `gpt-4o`                                          | Model name                     |
| `AGENTPY_TEMPERATURE` | `0.7`                                             | Sampling temperature           |
| `AGENTPY_MAX_TOKENS`  | `4096`                                            | Maximum tokens in the response |
| `AGENT_SYSTEM_PROMPT` | `You are a helpful general-purpose AI assistant.` | System prompt                  |

---

## CLI reference

```
agent [OPTIONS]
```

The **user prompt** is read from **stdin** by default. Use `--prompt` to supply it inline.

### API connection

| Flag                  | Description                                                          |
| --------------------- | -------------------------------------------------------------------- |
| `--url URL`           | OpenAI-compatible chat completions endpoint (env: `AGENTPY_API_URL`) |
| `--api-key KEY`       | API key / Bearer token (env: `AGENTY_API_KEY`)                       |
| `--model MODEL`       | Model name (env: `AGENTPY_MODEL`, default: `gpt-4o`)                 |
| `--temperature FLOAT` | Sampling temperature (default: `0.7`)                                |
| `--max-tokens INT`    | Maximum tokens in the response (default: `4096`)                     |

### Prompt

| Flag            | Description                                                  |
| --------------- | ------------------------------------------------------------ |
| `--system TEXT` | Override the system prompt (env: `AGENT_SYSTEM_PROMPT`)      |
| `--prompt TEXT` | Provide the user prompt inline instead of reading from stdin |

### Context

| Flag                     | Description                                                            |
| ------------------------ | ---------------------------------------------------------------------- |
| `-f PATH`, `--file PATH` | Attach a file's contents to the prompt. Use `-` for stdin. Repeatable. |
| `--agent-md`             | Search the directory tree for `AGENT.md` and include it                |
| `--skills-md`            | Search the directory tree for `SKILLS.md` and include it               |

### MCP (Model Context Protocol)

| Flag                      | Description                                                         |
| ------------------------- | ------------------------------------------------------------------- |
| `--mcp-server "CMD ARGS"` | Launch an MCP server and expose its tools to the agent. Repeatable. |

### Misc

| Flag              | Description                                                |
| ----------------- | ---------------------------------------------------------- |
| `-v`, `--verbose` | Print debug information (requests, MCP messages) to stderr |

---

## Examples

### Summarise a file

```bash
echo "Give me a one-paragraph summary" | agent --file report.pdf
```

### Multi-file context

```bash
echo "Find inconsistencies between these two files" | agent -f spec.md -f implementation.py
```

### Pipe stdin as a file attachment

```bash
cat server.log | agent --prompt "What errors occurred?" --file -
```

### Use AGENT.md / SKILLS.md project context

```bash
echo "Refactor the auth module" | agent --agent-md --skills-md --file auth.py
```

### Attach an MCP filesystem server

```bash
echo "List all Python files under /tmp and show me the largest one" | \
  agent --mcp-server "npx @modelcontextprotocol/server-filesystem /tmp"
```

### Multiple MCP servers

```bash
echo "Search the web for the latest Python release and save a summary to /tmp/py.txt" | \
  agent \
    --mcp-server "npx @modelcontextprotocol/server-brave-search" \
    --mcp-server "npx @modelcontextprotocol/server-filesystem /tmp"
```

### Use a local Ollama model

```bash
export AGENTPY_API_URL=http://localhost:11434/v1/chat/completions
export AGENTPY_MODEL=llama3.2
echo "Explain recursion" | agent
```

---

## How it works

1. The user prompt (stdin or `--prompt`) is combined with any attached files and context documents into a single user message.
2. The message is sent to the configured chat completions endpoint.
3. If the model requests **tool calls** (and an MCP server is attached), the agent executes them and feeds the results back to the model.
4. Steps 2–3 repeat until the model produces a final text response, which is printed to stdout.
