#!/usr/bin/env python3
"""
Anthropic ↔︎ MCP client for bb_blackboard_mcp.py (STDIO)

- Spawns your MCP server (local stdio)
- Discovers its tools + input schemas
- Presents them to Claude via Anthropic Messages API
- Executes tool_use calls and returns tool_result blocks
- Auto-fills base_url (hard‑coded to https://learn.uq.edu.au/)

Usage:
    set ANTHROPIC_API_KEY=... (Windows) / export ANTHROPIC_API_KEY=... (POSIX)
    python anthropic_blackboard_agent.py --server ./bb_blackboard_mcp.py

Optional flags:
    --model claude-sonnet-4-20250514
    --headless (default: false)  # passed when Claude asks to login/list/etc

NOTE: The Blackboard base URL is now hardcoded to https://learn.uq.edu.au/ and the
BLACKBOARD_BASE_URL environment variable is ignored.
"""

import os, sys, json, argparse, asyncio
from typing import Any, Dict, List, Optional

from anthropic import Anthropic, APIError
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

SYSTEM_PROMPT = """\
You are a Blackboard helper + content scripting agent. You can list courses, list/download content, show
downloaded files, and SAVE generated video script JSON files using the MCP tools. An authenticated browser
profile (cookies + storage) is ALREADY persisted; DO NOT call the login tool unless evidence shows the
saved session has expired.

Tools:
- login(base_url, headless=false)                # ONLY if other tools clearly fail due to auth expiry
- list_courses(base_url, headless=false)
- list_content(base_url, course_url, headless=false, expand_sections=true)
- download(base_url, content_url, headless=false)
- save_json(filename, data, overwrite=false, pretty=true)  # store video/script JSON under transcripts/

Resources:
- resource://downloads               (JSON index of downloaded files)
- resource://downloads/{name}        (raw bytes of a file)

Login minimization policy:
- Assume the existing session is valid and skip calling login.
- Only invoke login AFTER one of these happens:
    * A tool returns obviously unauthenticated HTML (e.g., page contains a login form and no courses/files).
    * Two consecutive attempts to list courses/content return empty where content is expected.
    * The user explicitly asks to (re)login or mentions session expired / needs fresh auth / MFA.
- Never chain a login + another tool in the same turn; perform login only when necessary.

Guidelines:
1) Always include 'base_url' when a tool schema requires it (hardcoded to https://learn.uq.edu.au/).
2) For saving a generated video script, ALWAYS produce a minimal JSON object with exactly these fields:
                 {
                         "title": "<short 3-8 word title, describing topic>",
                         "description": "<1-2 concise sentences, citing sources>",
                         "dialogue": [ {"speaker": "A", "text": "..."}, {"speaker": "B", "text": "..."}, ... ]
                 }
         Rules:
         - Only one or two distinct speakers (e.g., A and B or Narrator). Keep speaker labels short.
         - No extra top-level keys. No scenes array, no metadata beyond those three required fields.
         - Keep each dialogue text brief (generally < 220 characters) and focused.
         - Use natural, clear language; no stage directions other than short inline parentheses when essential.
         After forming that object, call save_json with a descriptive filename (e.g. topic_keyword.json) passing the object as data.
3) When a user asks for files, consider calling 'download' first, then read 'resource://downloads'.
4) If the user says a course name, use 'list_courses' and pick the closest match.
5) Be explicit and show short summaries of tool results to the user.
6) Keep headless=false for interactive flows unless the user requests otherwise.
7) Avoid unnecessary login attempts to reduce MFA prompts and session churn.
"""

def to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of MCP types to plain JSON."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj

async def run_chat(args: argparse.Namespace) -> None:
    # Hardcoded Blackboard base URL (trailing slash preserved)
    base_url_env = "https://learn.uq.edu.au/"

    # 1) connect to local MCP server (stdio)
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[args.server],
        env=os.environ.copy(),
        cwd=os.getcwd(),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # list tools & map to Anthropic "tools" format
            tools_resp = await session.list_tools()
            mcp_tools = tools_resp.tools

            tools_for_claude: List[Dict[str, Any]] = []
            for t in mcp_tools:
                tools_for_claude.append({
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": to_jsonable(t.inputSchema),
                })

            # helper: execute an MCP tool by name
            async def exec_mcp_tool(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
                # auto-inject base_url if required by schema and not provided
                schema = next((x for x in mcp_tools if x.name == name), None)
                if schema and getattr(schema, "inputSchema", None):
                    props = (getattr(schema.inputSchema, "properties", None) or {})
                    needs_base = "base_url" in props
                    if needs_base and "base_url" not in tool_input:
                        tool_input = {**tool_input, "base_url": base_url_env}
                # default headless flag if schema has it and user didn't pass it
                if schema and getattr(schema, "inputSchema", None):
                    props = (getattr(schema.inputSchema, "properties", None) or {})
                    if "headless" in props and "headless" not in tool_input:
                        tool_input["headless"] = bool(args.headless)

                result = await session.call_tool(name, tool_input)
                # Prefer structured content; fall back to text content
                out: Dict[str, Any] = {}
                if result.structuredContent is not None:
                    out["structured"] = to_jsonable(result.structuredContent)
                # collect any textual blocks too
                texts: List[str] = []
                for c in result.content:
                    if isinstance(c, types.TextContent):
                        texts.append(c.text)
                if texts:
                    out["text"] = "\n".join(texts)
                if not out:
                    out["text"] = "(no content)"
                return out

            client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            model = args.model

            print("Connected. Start chatting!  (type 'exit' to quit)\n")
            # Anthropic Messages API: system prompt must be top-level `system=...`,
            # not a message in `messages`.
            history: List[Dict[str, Any]] = []

            while True:
                try:
                    user = input("you › ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not user or user.lower() in {"exit", "quit"}:
                    break

                history.append({"role": "user", "content": user})

                # loop until Claude no longer requests tool_use
                while True:
                    try:
                       resp = client.messages.create(
                           model=model,
                           system=SYSTEM_PROMPT,
                           max_tokens=1200,
                           messages=history,
                           tools=tools_for_claude,
                    )
                    except APIError as e:
                        print(f"[Anthropic error] {e}", file=sys.stderr)
                        break

                    # add assistant content to history
                    history.append({"role": "assistant", "content": to_jsonable(resp.content)})

                    # collect tool_use calls (could be parallel)
                    tool_uses = [c for c in resp.content if getattr(c, "type", None) == "tool_use"]
                    if not tool_uses:
                        # final text already added above; print any text blocks
                        for c in resp.content:
                            if getattr(c, "type", None) == "text":
                                print(c.text)
                        break

                    # execute all tool calls, then return results in ONE user message
                    tool_result_blocks: List[Dict[str, Any]] = []
                    for tu in tool_uses:
                        name = tu.name
                        tool_input = dict(tu.input or {})
                        out = await exec_mcp_tool(name, tool_input)

                        # present a compact human summary in the console
                        pretty = out.get("text") or json.dumps(out.get("structured"), indent=2)
                        print(f"\n[tool:{name}]")
                        if pretty:
                            # show a shortened preview
                            preview = pretty if len(pretty) < 1200 else pretty[:1200] + " …"
                            print(preview)

                        # build tool_result block for Claude
                        # Prefer structured when available; otherwise send text
                        if "structured" in out:
                            content_payload: Any = out["structured"]
                        else:
                            content_payload = out.get("text", "")

                        tool_result_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": content_payload
                        })

                    # return all tool results together
                    history.append({"role": "user", "content": tool_result_blocks})

    print("bye!")

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--server", required=True, help="Path to bb_blackboard_mcp.py")
    p.add_argument("--model", default="claude-3-5-haiku-latest",
                   help="Anthropic model (default: %(default)s)")
    p.add_argument("--headless", action="store_true",
                   help="Pass headless=True to tools that accept it when not specified by Claude.")
    return p.parse_args()

if __name__ == "__main__":
    # sanity checks
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)
    asyncio.run(run_chat(parse_args()))
