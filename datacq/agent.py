#!/usr/bin/env python3
"""
Anthropic ↔︎ MCP client for scraping-based Blackboard MCP (Playwright)

Tools expect (legacy) parameters like headless in some cases; base URL is hardcoded in the server.

Usage:
    export ANTHROPIC_API_KEY=...
    python agent.py --server datacq/bb_mcp.py [--headless]
"""

import os, sys, json, argparse, asyncio, time, contextlib
from typing import Any, Dict, List, Optional, Sequence, Callable

from anthropic import Anthropic, APIError
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

SYSTEM_PROMPT = """You are an efficient Blackboard scraping MCP agent producing SHORT grounded video script JSON files.
Workflow:
    1. Call list_courses FIRST (never call login upfront). Use 'authenticated' flag in its response.
       - Only if authenticated == false should you then call login, and afterwards retry list_courses.
    2. Pick first 3–4 real courses (skip training/archived if obvious).
    3. For each uncovered course: list_content(course_url) -> choose ONE promising PDF content page (lecture/workbook style; skip quizzes/discussions).
    4. download(content_url) -> read_pdf_text(file_name) until aggregated extracted text for that course/topic ≥400 chars (download another PDF from same course if needed).
    5. As soon as you have ≥400 chars for a distinct course/topic, save_json a script (title (course code); dialogue list with characters Speaker A / Speaker B only) then move to a different course (round-robin) before returning.
    6. Rotate styles per script: dialog, mentor->learner, monologue, anecdote (cycle).
Constraints:
    - Do NOT call login unless a tool explicitly indicates unauthenticated (authenticated=false) or repeated 0 courses after list_courses.
    - Never script without prior successful read_pdf_text evidence (≥400 chars) for that specific topic.
    - No hallucinations; base content strictly on extracted PDF text. Keep reasoning terse.
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

async def _run_session_and_tools(server: str) -> tuple[ClientSession, List[Any], List[Dict[str, Any]], object]:
    """Start MCP session; returns (session, raw_tool_list, anthropic_tool_schema_list, stdio_ctx).

    stdio_ctx MUST be closed by caller after session close to avoid anyio cancellation issues
    (particularly visible on Windows: "Attempted to exit cancel scope in a different task").
    """
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server],
        env=os.environ.copy(),
        cwd=os.getcwd(),
    )
    stdio_ctx = stdio_client(server_params)
    read, write = await stdio_ctx.__aenter__()  # type: ignore
    session_ctx = ClientSession(read, write)
    await session_ctx.__aenter__()  # type: ignore
    await session_ctx.initialize()
    tools_resp = await session_ctx.list_tools()
    mcp_tools = tools_resp.tools
    tools_for_claude: List[Dict[str, Any]] = []
    for t in mcp_tools:
        tools_for_claude.append({
            "name": t.name,
            "description": t.description or "",
            "input_schema": to_jsonable(t.inputSchema),
        })
    return session_ctx, mcp_tools, tools_for_claude, stdio_ctx

async def run_scripted(
    prompts: Sequence[str],
    server: str,
    model: str = "claude-3-5-haiku-latest",
    *,
    headless: bool = False,
    base_url: str = "https://learn.uq.edu.au/",
    max_tokens: int = 1200,
    verbose: bool = False,
    tool_logger: Optional[Callable[[str, str], None]] = None,
) -> List[str]:
    """Programmatic (non-interactive) conversation runner using same tool loop logic as run_chat.

    Each user prompt in `prompts` is processed to completion (including any tool_use loops) before the
    next one begins. Returns the final assistant natural language output (concatenated text blocks) for
    each prompt in order. Tool invocations triggered by the prompts (e.g., save_json) will have effects
    on disk identical to interactive mode.

    Parameters:
        prompts: ordered sequence of user messages to send.
        server: path to bb_blackboard_mcp.py
        model: Anthropic model
        headless: default headless flag for tools when not specified
        base_url: Blackboard base URL injected automatically when required by tool schemas
        max_tokens: per-response max tokens
        verbose: if True, prints tool call previews to stdout
    """
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    # Start session & gather tools
    session, mcp_tools, tools_for_claude, stdio_ctx = await _run_session_and_tools(server)

    async def exec_mcp_tool(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        schema = next((x for x in mcp_tools if x.name == name), None)
        if schema and getattr(schema, "inputSchema", None):
            props = (getattr(schema.inputSchema, "properties", None) or {})
            if "headless" in props and "headless" not in tool_input:
                tool_input["headless"] = bool(headless)
        result = await session.call_tool(name, tool_input)
        out: Dict[str, Any] = {}
        if result.structuredContent is not None:
            out["structured"] = to_jsonable(result.structuredContent)
        texts: List[str] = []
        for c in result.content:
            if isinstance(c, types.TextContent):
                texts.append(c.text)
        if texts:
            out["text"] = "\n".join(texts)
        if not out:
            out["text"] = "(no content)"
        preview = out.get("text") or json.dumps(out.get("structured"), indent=2)
        if verbose:
            print(f"[tool:{name}] {preview[:500]}" + ("…" if preview and len(preview) > 500 else ""))
        if tool_logger:
            try:
                tool_logger(name, (preview or "")[:500])
            except Exception:
                pass
        return out

    history: List[Dict[str, Any]] = []
    finals: List[str] = []

    try:
        for user_prompt in prompts:
            history.append({"role": "user", "content": user_prompt})
            last_text = ""
            while True:
                try:
                    resp = client.messages.create(
                        model=model,
                        system=SYSTEM_PROMPT,
                        max_tokens=max_tokens,
                        messages=history,
                        tools=tools_for_claude,
                    )
                except APIError as e:
                    raise RuntimeError(f"Anthropic error: {e}") from e
                history.append({"role": "assistant", "content": to_jsonable(resp.content)})
                tool_uses = [c for c in resp.content if getattr(c, "type", None) == "tool_use"]
                if not tool_uses:
                    # gather any text blocks
                    texts: List[str] = [c.text for c in resp.content if getattr(c, "type", None) == "text"]
                    last_text = "\n".join(texts)
                    break
                # Execute tool calls then append results as a user message
                result_blocks: List[Dict[str, Any]] = []
                for tu in tool_uses:
                    out = await exec_mcp_tool(tu.name, dict(tu.input or {}))
                    if "structured" in out:
                        payload: Any = out["structured"]
                    else:
                        payload = out.get("text", "")
                    result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": payload
                    })
                history.append({"role": "user", "content": result_blocks})
            finals.append(last_text)
    finally:
        # Close session then underlying stdio transport (suppress noisy cancellation errors)
        try:
            await session.__aexit__(None, None, None)  # type: ignore
        except Exception:
            pass
        try:
            await stdio_ctx.__aexit__(None, None, None)  # type: ignore
        except Exception:
            pass
    return finals

def run_scripted_sync(
    prompts: Sequence[str],
    server: str,
    model: str = "claude-3-5-haiku-latest",
    **kwargs: Any
) -> List[str]:
    """Synchronous wrapper around run_scripted for convenience in GUI environments."""
    return asyncio.run(run_scripted(prompts, server, model, **kwargs))

async def run_chat(args: argparse.Namespace) -> None:
    # Interactive mode preserved; uses default hardcoded base URL.
    session_prompts: List[str] = []  # built incrementally from stdin
    # We reuse run_scripted logic but keep streaming experience; so we replicate tool loop inline.
    base_url = "https://learn.uq.edu.au/"
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY", file=sys.stderr)
        return
    session, mcp_tools, tools_for_claude, stdio_ctx = await _run_session_and_tools(args.server)

    async def exec_mcp_tool(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        schema = next((x for x in mcp_tools if x.name == name), None)
        if schema and getattr(schema, "inputSchema", None):
            props = (getattr(schema.inputSchema, "properties", None) or {})
            if "headless" in props and "headless" not in tool_input:
                tool_input["headless"] = bool(args.headless)
        result = await session.call_tool(name, tool_input)
        out: Dict[str, Any] = {}
        if result.structuredContent is not None:
            out["structured"] = to_jsonable(result.structuredContent)
        texts: List[str] = []
        for c in result.content:
            if isinstance(c, types.TextContent):
                texts.append(c.text)
        if texts:
            out["text"] = "\n".join(texts)
        if not out:
            out["text"] = "(no content)"
        return out

    history: List[Dict[str, Any]] = []
    print("Connected. Start chatting!  (type 'exit' to quit)\n")
    try:
        while True:
            try:
                user = input("you › ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user or user.lower() in {"exit", "quit"}:
                break
            history.append({"role": "user", "content": user})
            while True:
                try:
                    resp = client.messages.create(
                        model=args.model,
                        system=SYSTEM_PROMPT,
                        max_tokens=1200,
                        messages=history,
                        tools=tools_for_claude,
                    )
                except APIError as e:
                    print(f"[Anthropic error] {e}", file=sys.stderr)
                    break
                history.append({"role": "assistant", "content": to_jsonable(resp.content)})
                tool_uses = [c for c in resp.content if getattr(c, "type", None) == "tool_use"]
                if not tool_uses:
                    for c in resp.content:
                        if getattr(c, "type", None) == "text":
                            print(c.text)
                    break
                tool_result_blocks: List[Dict[str, Any]] = []
                for tu in tool_uses:
                    out = await exec_mcp_tool(tu.name, dict(tu.input or {}))
                    pretty = out.get("text") or json.dumps(out.get("structured"), indent=2)
                    print(f"\n[tool:{tu.name}]")
                    if pretty:
                        preview = pretty if len(pretty) < 1200 else pretty[:1200] + " …"
                        print(preview)
                    if "structured" in out:
                        payload: Any = out["structured"]
                    else:
                        payload = out.get("text", "")
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": payload
                    })
                history.append({"role": "user", "content": tool_result_blocks})
    finally:
        with contextlib.suppress(Exception):
            await session.__aexit__(None, None, None)  # type: ignore
        with contextlib.suppress(Exception):
            await stdio_ctx.__aexit__(None, None, None)  # type: ignore
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
