#!/usr/bin/env python3
"""
agent.py - A simple general-purpose AI agent CLI tool.

Features:
  - Reads user prompt from stdin
  - Accepts files via CLI to include in the prompt
  - Supports AGENT.md and SKILLS.md context files
  - MCP (Model Context Protocol) tool support
  - Works against any OpenAI-compatible API

Usage:
  echo "Explain this code" | python agent.py --file mycode.py
  python agent.py --agent-md --skills-md --file doc.txt
  python agent.py --mcp-server npx @modelcontextprotocol/server-filesystem /tmp
"""

import argparse
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration defaults (overridable via env vars or CLI flags)
# ---------------------------------------------------------------------------

DEFAULT_API_URL = os.environ.get(
    "AGENTPY_API_URL", "https://api.openai.com/v1/chat/completions"
)
DEFAULT_API_KEY = os.environ.get("AGENTPY_API_KEY", "")
DEFAULT_MODEL = os.environ.get("AGENTPY_MODEL", "gpt-4o")
DEFAULT_TEMPERATURE = float(os.environ.get("AGENTPY_TEMPERATURE", "0.7"))
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENTPY_MAX_TOKENS", "4096"))
DEFAULT_SYSTEM_PROMPT = os.environ.get(
    "AGENTPY_SYSTEM_PROMPT",
    "You are a helpful general-purpose AI assistant.",
)


# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) client
# ---------------------------------------------------------------------------


class MCPClient:
    """Minimal MCP client that communicates with an MCP server via stdio."""

    def __init__(self, command: list[str], verbose: bool = False):
        self.command = command
        self.verbose = verbose
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._request_id = 0
        self.tools: list[dict] = []

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send(self, obj: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        line = json.dumps(obj) + "\n"
        if self.verbose:
            print(f"[MCP →] {line.rstrip()}", file=sys.stderr)
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()

    def _recv(self) -> dict:
        assert self._proc is not None and self._proc.stdout is not None
        raw = self._proc.stdout.readline()
        if not raw:
            raise EOFError("MCP server closed stdout")
        if self.verbose:
            print(f"[MCP ←] {raw.decode().rstrip()}", file=sys.stderr)
        return json.loads(raw.decode())

    def start(self) -> None:
        """Start the MCP server process and perform the initialization handshake."""
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None if self.verbose else subprocess.DEVNULL,
        )

        # --- initialize ---
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "agent.py", "version": "1.0.0"},
                },
            }
        )
        resp = self._recv()
        if "error" in resp:
            raise RuntimeError(f"MCP initialize error: {resp['error']}")

        # --- initialized notification ---
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # --- list tools ---
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }
        )
        resp = self._recv()
        if "error" in resp:
            raise RuntimeError(f"MCP tools/list error: {resp['error']}")
        self.tools = resp.get("result", {}).get("tools", [])
        if self.verbose:
            print(
                f"[MCP] Discovered {len(self.tools)} tool(s): "
                f"{[t['name'] for t in self.tools]}",
                file=sys.stderr,
            )

    def call_tool(self, name: str, arguments: dict) -> Any:
        """Call an MCP tool and return its result content."""
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        resp = self._recv()
        if "error" in resp:
            raise RuntimeError(f"MCP tool '{name}' error: {resp['error']}")
        result = resp.get("result", {})
        content = result.get("content", [])
        # Flatten text content blocks into a single string
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)

    def stop(self) -> None:
        """Terminate the MCP server process."""
        if self._proc and self._proc.poll() is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
            except Exception:
                pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def as_openai_tools(self) -> list[dict]:
        """Convert MCP tool definitions to OpenAI function-calling format."""
        openai_tools = []
        for tool in self.tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "inputSchema",
                            {
                                "type": "object",
                                "properties": {},
                            },
                        ),
                    },
                }
            )
        return openai_tools


# ---------------------------------------------------------------------------
# OpenAI-compatible API client
# ---------------------------------------------------------------------------


class OpenAIClient:
    """Thin wrapper around an OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        verbose: bool = False,
    ):
        self.url = url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.verbose = verbose

    def _post(self, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            self.url, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from API: {body}") from exc

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if self.verbose:
            print(f"[API] POST {self.url} model={self.model}", file=sys.stderr)

        return self._post(payload)

    @staticmethod
    def extract_text(response: dict) -> str:
        """Extract the assistant's text content from a chat completion response."""
        choices = response.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content") or ""

    @staticmethod
    def extract_tool_calls(response: dict) -> list[dict]:
        """Extract tool_calls from a chat completion response (may be empty)."""
        choices = response.get("choices", [])
        if not choices:
            return []
        return choices[0].get("message", {}).get("tool_calls") or []


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------


def find_context_file(filename: str, start_dir: Optional[str] = None) -> Optional[Path]:
    """Walk up the directory tree looking for *filename*."""
    directory = Path(start_dir or os.getcwd()).resolve()
    while True:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def load_context_files(
    load_agent_md: bool = False,
    load_skills_md: bool = False,
) -> str:
    """Return a combined string with AGENT.md / SKILLS.md contents."""
    parts: list[str] = []
    for flag, name in [(load_agent_md, "AGENT.md"), (load_skills_md, "SKILLS.md")]:
        if not flag:
            continue
        path = find_context_file(name)
        if path:
            try:
                content = path.read_text(encoding="utf-8")
                parts.append(f"=== {name} ({path}) ===\n{content}")
                print(f"[context] Loaded {path}", file=sys.stderr)
            except OSError as exc:
                print(f"[warning] Could not read {path}: {exc}", file=sys.stderr)
        else:
            print(f"[warning] {name} not found in directory tree", file=sys.stderr)
    return "\n\n".join(parts)


def load_files(paths: list[str]) -> str:
    """Read each file and return a combined string."""
    parts: list[str] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            print(f"[warning] File not found: {p}", file=sys.stderr)
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            parts.append(f"=== File: {path} ===\n{content}")
            print(f"[context] Loaded file {path}", file=sys.stderr)
        except OSError as exc:
            print(f"[warning] Could not read {p}: {exc}", file=sys.stderr)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------


def run_agent(
    client: OpenAIClient,
    messages: list[dict],
    mcp: Optional[MCPClient] = None,
    verbose: bool = False,
) -> str:
    """
    Run the agentic loop:
      1. Send messages to the LLM.
      2. If the LLM requests tool calls, execute them via MCP and loop.
      3. Return the final text response.
    """
    tools = mcp.as_openai_tools() if mcp and mcp.tools else None

    while True:
        response = client.chat(messages, tools=tools)
        finish_reason = response.get("choices", [{}])[0].get("finish_reason", "stop")
        tool_calls = OpenAIClient.extract_tool_calls(response)

        if tool_calls and mcp:
            # Append the assistant's tool-call message
            assistant_msg = response["choices"][0]["message"]
            messages.append(assistant_msg)

            # Execute each tool call
            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    arguments = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}

                print(f"[tool] Calling {tool_name}({arguments})", file=sys.stderr)
                try:
                    result = mcp.call_tool(tool_name, arguments)
                except Exception as exc:
                    result = f"Error: {exc}"
                    print(f"[tool] Error: {exc}", file=sys.stderr)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(result),
                    }
                )
        else:
            # No more tool calls – return the final text
            return OpenAIClient.extract_text(response)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.py",
        description=textwrap.dedent(
            """\
            A simple general-purpose AI agent.

            The user prompt is read from stdin. Additional context (files,
            AGENT.md, SKILLS.md) can be injected via CLI flags.
            MCP servers can be attached to give the agent tool-use capabilities.
        """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # --- API connection ---
    api_group = parser.add_argument_group("API connection")
    api_group.add_argument(
        "--url",
        default=DEFAULT_API_URL,
        metavar="URL",
        help=(
            "OpenAI-compatible chat completions endpoint "
            f"(default: {DEFAULT_API_URL}, env: AGENTPY_API_URL)"
        ),
    )
    api_group.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        metavar="KEY",
        help="API key / Bearer token (env: AGENTPY_API_KEY)",
    )
    api_group.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=f"Model name (default: {DEFAULT_MODEL}, env: AGENTPY_MODEL)",
    )
    api_group.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
        metavar="FLOAT",
        help=f"Sampling temperature (default: {DEFAULT_TEMPERATURE})",
    )
    api_group.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        metavar="INT",
        help=f"Maximum tokens in the response (default: {DEFAULT_MAX_TOKENS})",
    )

    # --- System prompt ---
    prompt_group = parser.add_argument_group("Prompt")
    prompt_group.add_argument(
        "--system",
        default=DEFAULT_SYSTEM_PROMPT,
        metavar="TEXT",
        help=(
            "System prompt (env: AGENTPY_SYSTEM_PROMPT, "
            f"default: '{DEFAULT_SYSTEM_PROMPT}')"
        ),
    )
    prompt_group.add_argument(
        "--prompt",
        metavar="TEXT",
        help=(
            "User prompt text. If omitted, the prompt is read from stdin. "
            "Can be combined with stdin by using '-' as a file."
        ),
    )

    # --- Context files ---
    ctx_group = parser.add_argument_group("Context")
    ctx_group.add_argument(
        "--file",
        "-f",
        action="append",
        default=[],
        metavar="PATH",
        dest="files",
        help=(
            "Attach a file's contents to the prompt. "
            "Use '-' to explicitly include stdin as a file. "
            "Can be repeated."
        ),
    )
    ctx_group.add_argument(
        "--agent-md",
        action="store_true",
        help="Search for AGENT.md in the directory tree and include it.",
    )
    ctx_group.add_argument(
        "--skills-md",
        action="store_true",
        help="Search for SKILLS.md in the directory tree and include it.",
    )

    # --- MCP ---
    mcp_group = parser.add_argument_group("MCP (Model Context Protocol)")
    mcp_group.add_argument(
        "--mcp-server",
        action="append",
        default=[],
        metavar="CMD_ARG",
        dest="mcp_servers",
        help=(
            "Launch an MCP server. Provide the full command as a single "
            "space-separated string, e.g. "
            "'--mcp-server \"npx @modelcontextprotocol/server-filesystem /tmp\"'. "
            "Can be repeated to attach multiple servers."
        ),
    )

    # --- Misc ---
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print debug information to stderr.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming (currently streaming is not implemented; this flag is a no-op).",
    )

    return parser


def read_stdin_prompt() -> str:
    """Read the user prompt from stdin."""
    if sys.stdin.isatty():
        print("Enter your prompt (Ctrl-D to finish):", file=sys.stderr)
    return sys.stdin.read()


def build_user_message(
    prompt: str,
    file_context: str,
    md_context: str,
) -> str:
    """Assemble the full user message from prompt + attached context."""
    parts: list[str] = []

    if md_context:
        parts.append("## Context files\n\n" + md_context)

    if file_context:
        parts.append("## Attached files\n\n" + file_context)

    if prompt.strip():
        parts.append("## User request\n\n" + prompt.strip())

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Collect the user prompt
    # ------------------------------------------------------------------
    stdin_used_as_file = "-" in args.files

    if args.prompt:
        prompt_text = args.prompt
        # If stdin is also requested as a file, it will be handled below
    elif not stdin_used_as_file:
        # Default: read prompt from stdin
        prompt_text = read_stdin_prompt()
    else:
        prompt_text = ""

    # ------------------------------------------------------------------
    # 2. Load attached files
    # ------------------------------------------------------------------
    file_paths = [f for f in args.files if f != "-"]
    file_context = load_files(file_paths)

    # If '-' was given as a file, read stdin and treat it as a file attachment
    if stdin_used_as_file:
        if sys.stdin.isatty():
            print("Enter file content via stdin (Ctrl-D to finish):", file=sys.stderr)
        stdin_content = sys.stdin.read()
        if stdin_content:
            stdin_block = f"=== File: <stdin> ===\n{stdin_content}"
            file_context = (file_context + "\n\n" + stdin_block).strip()

    # ------------------------------------------------------------------
    # 3. Load AGENT.md / SKILLS.md
    # ------------------------------------------------------------------
    md_context = load_context_files(
        load_agent_md=args.agent_md,
        load_skills_md=args.skills_md,
    )

    # ------------------------------------------------------------------
    # 4. Build the messages list
    # ------------------------------------------------------------------
    user_content = build_user_message(prompt_text, file_context, md_context)

    if not user_content.strip():
        print("[error] No prompt provided. Use stdin or --prompt.", file=sys.stderr)
        sys.exit(1)

    messages: list[dict] = [
        {"role": "system", "content": args.system},
        {"role": "user", "content": user_content},
    ]

    if args.verbose:
        print("[messages]", file=sys.stderr)
        for m in messages:
            role = m["role"]
            snippet = m["content"][:200].replace("\n", "↵")
            print(f"  [{role}] {snippet}…", file=sys.stderr)

    # ------------------------------------------------------------------
    # 5. Start MCP servers
    # ------------------------------------------------------------------
    mcp_clients: list[MCPClient] = []
    for server_cmd_str in args.mcp_servers:
        cmd = server_cmd_str.split()
        mcp = MCPClient(cmd, verbose=args.verbose)
        try:
            mcp.start()
            mcp_clients.append(mcp)
            print(
                f"[mcp] Connected to '{server_cmd_str}' " f"({len(mcp.tools)} tool(s))",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"[warning] Could not start MCP server '{server_cmd_str}': {exc}",
                file=sys.stderr,
            )

    # Merge all MCP clients into one virtual client for simplicity
    # (tools from all servers are merged; calls are dispatched by name)
    combined_mcp: Optional[MCPClient] = None
    if mcp_clients:
        combined_mcp = _merge_mcp_clients(mcp_clients, verbose=args.verbose)

    # ------------------------------------------------------------------
    # 6. Create the API client and run the agent loop
    # ------------------------------------------------------------------
    client = OpenAIClient(
        url=args.url,
        api_key=args.api_key,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        verbose=args.verbose,
    )

    try:
        answer = run_agent(client, messages, mcp=combined_mcp, verbose=args.verbose)
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        for mcp in mcp_clients:
            mcp.stop()

    # ------------------------------------------------------------------
    # 7. Print the answer
    # ------------------------------------------------------------------
    print(answer)


# ---------------------------------------------------------------------------
# Helper: merge multiple MCPClient instances into one dispatcher
# ---------------------------------------------------------------------------


class _MergedMCPClient(MCPClient):
    """A virtual MCPClient that dispatches tool calls to the correct server."""

    def __init__(self, clients: list[MCPClient], verbose: bool = False):
        super().__init__([], verbose=verbose)
        self._clients = clients
        self._tool_map: dict[str, MCPClient] = {}
        for c in clients:
            for tool in c.tools:
                self._tool_map[tool["name"]] = c
        self.tools = [t for c in clients for t in c.tools]

    def call_tool(self, name: str, arguments: dict) -> Any:
        owner = self._tool_map.get(name)
        if owner is None:
            raise RuntimeError(f"No MCP server provides tool '{name}'")
        return owner.call_tool(name, arguments)

    def start(self) -> None:
        pass  # already started

    def stop(self) -> None:
        pass  # stopped individually


def _merge_mcp_clients(clients: list[MCPClient], verbose: bool = False) -> MCPClient:
    if len(clients) == 1:
        return clients[0]
    return _MergedMCPClient(clients, verbose=verbose)


if __name__ == "__main__":
    main()
