"""
Agentic LongMemEval benchmark — tests RecCli as users actually experience it in Claude Code.

Unlike `run_benchmark.py` (which passes 10 search previews into a one-shot QA prompt),
this runner simulates the real Claude Code flow: the agent receives the question and
must actively call `search_history` and `expand_search_result` tools to drill into
prior sessions, then formulate an answer.

This captures the product's actual differentiator — exact drill-down from summary
preview to full conversation slice — which the static benchmark misses.

Usage:
    PYTHONPATH=packages python3 benchmarks/longmemeval/run_agentic_benchmark.py \
        --data benchmarks/longmemeval/data/longmemeval_oracle.json \
        --output benchmarks/longmemeval/results/reccli_agentic_oracle.jsonl \
        --limit 10
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages"))

from reccli.session.devsession import DevSession
from reccli.retrieval.vector_index import build_unified_index
from reccli.retrieval.search import search, expand_result, search_by_time_range
from reccli.retrieval.embeddings import get_embedding_provider


# ---------------------------------------------------------------------------
# Tool definitions — mirror the real MCP surface
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_history",
        "description": (
            "Search past session history using hybrid retrieval "
            "(dense embeddings + BM25 + RRF). Returns top-k results with "
            "content_preview (first 200 chars) and result_id. "
            "Use expand_search_result on a specific result_id to see the full conversation slice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 5, max 15).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_by_time",
        "description": (
            "Search session history within a date/time range. Use this for temporal "
            "questions like 'what happened before October 15?' or 'what did I discuss "
            "in June?'. Dates are ISO format: YYYY-MM-DD for day-granularity, or "
            "YYYY-MM-DDTHH:MM:SS for finer granularity. If end_time is omitted, "
            "defaults to end of start_time's day."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {
                    "type": "string",
                    "description": "Start of range (ISO format, e.g. '2023-06-15').",
                },
                "end_time": {
                    "type": "string",
                    "description": "End of range (ISO format). Optional — defaults to end of start_time's day.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional text filter to narrow within the time range.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 20, max 30).",
                    "default": 20,
                },
            },
            "required": ["start_time"],
        },
    },
    {
        "name": "list_sessions",
        "description": (
            "List all recorded sessions sorted by date (newest first) with message counts "
            "and start timestamps. Use this to orient yourself on what sessions exist "
            "across the timeline before searching — especially useful for multi-session "
            "questions about event ordering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max sessions to list (default 30).",
                    "default": 30,
                },
            },
        },
    },
    {
        "name": "expand_search_result",
        "description": (
            "Expand a search result_id into its surrounding conversation "
            "(±context_window messages). Use this to drill into a search hit "
            "and see the actual reasoning, decisions, or full message content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "result_id": {
                    "type": "string",
                    "description": "A result_id returned by search_history or search_by_time.",
                },
                "context_window": {
                    "type": "integer",
                    "description": "Number of messages before/after (default 5).",
                    "default": 5,
                },
            },
            "required": ["result_id"],
        },
    },
    {
        "name": "inspect_result_id",
        "description": (
            "Inspect the metadata of a result_id without expanding its context — shows "
            "hit type (message/span/summary_item), session, timestamp, and kind. "
            "Useful when you want to check what a result is before deciding whether to expand it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "result_id": {
                    "type": "string",
                    "description": "A result_id to inspect.",
                },
            },
            "required": ["result_id"],
        },
    },
]


SYSTEM_PROMPT = (
    "You are answering questions about past conversations that have been recorded "
    "and indexed by RecCli, a temporal memory engine. "
    "You DO NOT have the conversation text in your prompt — you must retrieve it "
    "using the available tools.\n\n"
    "Tool guidance:\n"
    "- search_history: natural-language hybrid search. Best first move for topic questions.\n"
    "- search_by_time: filter by date range. Use for temporal questions like "
    "'before October 15' or 'what happened in June'. Much better than search_history "
    "when the question is primarily date-bounded.\n"
    "- list_sessions: see the full timeline of recorded sessions. Useful when the question "
    "spans multiple sessions or event ordering matters.\n"
    "- expand_search_result: drill into a hit to see the full conversation slice (not just "
    "the 200-char preview). Use whenever a preview looks relevant but is truncated.\n"
    "- inspect_result_id: peek at a result's metadata without fetching the full content.\n\n"
    "Approach:\n"
    "1. Pick the best starting tool for the question shape (temporal questions → search_by_time, "
    "topic questions → search_history, ordering questions → often list_sessions first).\n"
    "2. Iterate — previews are truncated at 200 chars, so expand_search_result is often needed.\n"
    "3. For multi-event questions (e.g. 'how many days between X and Y?'), run SEPARATE searches "
    "for each event rather than one combined search.\n"
    "4. Once you have enough evidence, give a concise, factual answer. "
    "If the evidence is genuinely missing, say 'I don't know.' Do not speculate.\n\n"
    "Be concise. The user wants the answer, not your reasoning process."
)


# ---------------------------------------------------------------------------
# Tool executors — call the same functions the MCP server wraps
# ---------------------------------------------------------------------------

def _format_search_results(results):
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        content = (r.get("content_preview") or "")[:200]
        score = r.get("final_score") or r.get("rrf_score") or 0
        rid = r.get("id") or r.get("result_id", "")
        session = r.get("session", "")
        lines.append(f"{i}. [{session}] (score: {score:.3f})")
        lines.append(f"   {content}")
        if rid:
            lines.append(f"   result_id: {rid}")
    return "\n".join(lines)


def _format_expand_result(result):
    if result is None:
        return "Result not found or invalid result_id."
    lines = []
    msgs = result.get("context_messages", [])
    hit_type = result.get("hit_type", "message")
    lines.append(f"[{hit_type} hit — messages {result.get('context_start')}:{result.get('context_end')}]")
    for m in msgs:
        role = m.get("role", "?")
        content = (m.get("content") or "")[:500]
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def _format_list_sessions(sessions_dir):
    """List .devsession files with message counts + first timestamp."""
    files = sorted(
        sessions_dir.glob("*.devsession"),
        key=lambda p: p.stat().st_mtime,
    )
    if not files:
        return "No recorded sessions."
    lines = [f"**{len(files)} session(s)** (chronological):"]
    for sf in files:
        try:
            s = DevSession.load(sf, verify_checksums=False)
            first_ts = s.conversation[0].get("timestamp", "") if s.conversation else ""
            lines.append(f"- {sf.stem} — {len(s.conversation)} msgs, started {first_ts[:16]}")
        except Exception:
            lines.append(f"- {sf.stem} — (failed to load)")
    return "\n".join(lines)


def _format_inspect(sessions_dir, result_id):
    """Return structured metadata about a result_id without its full content."""
    import json as _json
    index_path = sessions_dir / "index.json"
    if not index_path.exists():
        return f"No index at {index_path}."
    try:
        with open(index_path) as f:
            index = _json.load(f)
    except Exception as e:
        return f"Failed to load index: {e}"
    for v in index.get("unified_vectors", []):
        if v.get("id") == result_id:
            msg_id = v.get("message_id", "")
            hit_type = "message"
            if any(msg_id.startswith(p) for p in ("dec_", "chg_", "prb_", "iss_", "nxt_")):
                hit_type = "summary_item"
            elif msg_id.startswith("spn_"):
                hit_type = "span"
            return _json.dumps({
                "result_id": result_id,
                "hit_type": hit_type,
                "session": v.get("session"),
                "message_id": msg_id,
                "message_index": v.get("message_index"),
                "timestamp": v.get("timestamp"),
                "kind": v.get("kind"),
                "content_preview": (v.get("content_preview") or "")[:160],
            }, indent=2)
    # Fall back to parsing the id directly
    parts = result_id.rsplit("_msg_", 1)
    if len(parts) == 2:
        try:
            return _json.dumps({
                "result_id": result_id,
                "hit_type": "message",
                "session": parts[0],
                "message_index": int(parts[1]),
                "note": "parsed-only — not in index",
            }, indent=2)
        except ValueError:
            pass
    return f"result_id '{result_id}' not found in index."


def exec_tool(name, args, sessions_dir, provider):
    try:
        if name == "search_history":
            query = args.get("query", "")
            top_k = min(int(args.get("top_k", 5)), 15)
            results = search(
                sessions_dir=sessions_dir,
                query=query,
                top_k=top_k,
                provider=provider,
            )
            return _format_search_results(results)
        elif name == "search_by_time":
            start_time = args.get("start_time", "")
            end_time = args.get("end_time", "") or (start_time[:10] if len(start_time) >= 10 else start_time)
            query = args.get("query", "") or None
            top_k = min(int(args.get("top_k", 20)), 30)
            results = search_by_time_range(
                sessions_dir,
                start_time=start_time,
                end_time=end_time,
                query=query,
                top_k=top_k,
            )
            if not results:
                return f"No messages in range {start_time} to {end_time}."
            return _format_search_results(results)
        elif name == "list_sessions":
            return _format_list_sessions(sessions_dir)
        elif name == "expand_search_result":
            rid = args.get("result_id", "")
            window = int(args.get("context_window", 5))
            result = expand_result(sessions_dir, rid, window)
            return _format_expand_result(result)
        elif name == "inspect_result_id":
            return _format_inspect(sessions_dir, args.get("result_id", ""))
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

def run_agentic(question, sessions_dir, provider, client, model, max_turns=8,
                max_tokens_per_turn=2000):
    """Run the agent loop and return (final_answer, turn_count, tool_call_count)."""
    messages = [{"role": "user", "content": question}]
    tool_calls = 0

    for turn in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens_per_turn,
            temperature=0,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            # Extract final text
            text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
            return ("\n".join(text_parts).strip() or "(empty)", turn + 1, tool_calls)

        if response.stop_reason == "tool_use":
            # Capture the assistant turn, then execute tools and feed results back
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    tool_calls += 1
                    result_text = exec_tool(block.name, block.input, sessions_dir, provider)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Stopped for other reason (max_tokens, refusal, etc.)
        text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return (
            f"(stopped: {response.stop_reason}) " + "\n".join(text_parts).strip(),
            turn + 1,
            tool_calls,
        )

    return ("(max turns exceeded)", max_turns, tool_calls)


# ---------------------------------------------------------------------------
# Ingest (shared pattern with run_benchmark.py)
# ---------------------------------------------------------------------------

def ingest_sessions(sessions, dates, session_ids, sessions_dir, provider):
    for i, (session_msgs, date_str, sid) in enumerate(zip(sessions, dates, session_ids)):
        ds = DevSession(session_id=sid)
        ds.metadata["created_at"] = date_str
        ds.metadata["source"] = "longmemeval_agentic"
        for msg in session_msgs:
            ds.conversation.append({
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": date_str,
            })
        try:
            ds.generate_embeddings(provider=provider, storage_mode="external")
        except Exception:
            pass
        path = sessions_dir / f"session_{i:04d}.devsession"
        ds.save(path, skip_validation=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_benchmark(data_path, output_path, limit=None, model="claude-sonnet-4-6", max_turns=6):
    from reccli.runtime.config import Config

    config = Config()
    anthropic_key = config.get_api_key("anthropic")
    if not anthropic_key:
        print("ERROR: Anthropic key required for the agentic runner.")
        sys.exit(1)

    import anthropic
    client = anthropic.Anthropic(api_key=anthropic_key)
    provider = get_embedding_provider()

    data = json.load(open(data_path))
    if limit:
        data = data[:limit]
    print(f"Running {len(data)} questions through the agentic RecCli loop (max_turns={max_turns}, model={model})")

    results = []
    total_tool_calls = 0
    start = time.time()

    for i, entry in enumerate(data):
        qid = entry["question_id"]
        question = entry["question"]
        q_type = entry["question_type"]
        print(f"[{i+1}/{len(data)}] {q_type}: {question[:60]}...", end=" ", flush=True)

        tmpdir = Path(tempfile.mkdtemp(prefix="longmemeval_agentic_"))
        sessions_dir = tmpdir / "devsession"
        sessions_dir.mkdir()

        try:
            ingest_sessions(
                entry["haystack_sessions"],
                entry["haystack_dates"],
                entry["haystack_session_ids"],
                sessions_dir,
                provider,
            )
            build_unified_index(sessions_dir, verbose=False)

            hypothesis, turns, tool_calls = run_agentic(
                question, sessions_dir, provider, client, model, max_turns=max_turns
            )
            total_tool_calls += tool_calls
            print(f"→ [turns={turns} tools={tool_calls}] {hypothesis[:60]}")

            results.append({
                "question_id": qid,
                "hypothesis": hypothesis,
                "turns": turns,
                "tool_calls": tool_calls,
            })
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"question_id": qid, "hypothesis": f"Error: {e}", "turns": 0, "tool_calls": 0})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    elapsed = time.time() - start
    avg_tools = total_tool_calls / len(results) if results else 0
    print(f"\nDone. {len(results)} answers in {elapsed:.1f}s "
          f"(avg {avg_tools:.1f} tool calls/question, total {total_tool_calls})")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run LongMemEval through the RecCli agentic loop")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--max-turns", type=int, default=8,
                        help="Max agent turns per question (default 8).")
    args = parser.parse_args()

    run_benchmark(args.data, args.output, args.limit, args.model, args.max_turns)
