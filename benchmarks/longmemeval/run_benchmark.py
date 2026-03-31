"""
LongMemEval benchmark runner for RecCli.

For each question:
1. Ingests haystack_sessions into RecCli as .devsession files with embeddings
2. Builds the unified vector index
3. Searches for relevant context using the question
4. Passes context + question to an LLM for the answer
5. Outputs JSONL for the LongMemEval evaluator

Usage:
    PYTHONPATH=packages python3 benchmarks/longmemeval/run_benchmark.py \
        --data benchmarks/longmemeval/data/longmemeval_oracle.json \
        --output benchmarks/longmemeval/results/reccli_oracle.jsonl \
        --limit 10  # optional: run first N questions only
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# Add packages to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages"))

from reccli.session.devsession import DevSession
from reccli.retrieval.vector_index import build_unified_index
from reccli.retrieval.search import search
from reccli.retrieval.embeddings import get_embedding_provider


def ingest_sessions(sessions, dates, session_ids, sessions_dir, provider):
    """Convert LongMemEval haystack sessions into .devsession files."""
    for i, (session_msgs, date_str, sid) in enumerate(zip(sessions, dates, session_ids)):
        ds = DevSession(session_id=sid)
        ds.metadata["created_at"] = date_str
        ds.metadata["source"] = "longmemeval_benchmark"

        for msg in session_msgs:
            ds.conversation.append({
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": date_str,
            })

        # Generate embeddings
        try:
            ds.generate_embeddings(provider=provider, storage_mode="external")
        except Exception:
            pass  # Continue without embeddings — BM25 still works

        path = sessions_dir / f"session_{i:04d}.devsession"
        ds.save(path, skip_validation=True)


def retrieve_context(sessions_dir, question, provider, top_k=10):
    """Search RecCli's index for relevant context."""
    try:
        results = search(
            sessions_dir=sessions_dir,
            query=question,
            top_k=top_k,
            provider=provider,
        )
    except Exception:
        results = []

    context_parts = []
    for r in results:
        session = r.get("session", "")
        preview = r.get("content_preview", "")
        timestamp = r.get("timestamp", "")
        role = r.get("role", "")
        context_parts.append(f"[{timestamp}] [{role}] {preview}")

    return "\n".join(context_parts)


def answer_question(question, context, client, model):
    """Use an LLM to answer the question given retrieved context."""
    system = (
        "You are answering questions about past conversations. "
        "Use ONLY the provided context to answer. "
        "If the context does not contain enough information, say 'I don't know.' "
        "Be concise and factual."
    )

    user_msg = f"Context from past sessions:\n{context}\n\nQuestion: {question}\n\nAnswer:"

    try:
        if hasattr(client, "messages"):
            # Anthropic
            response = client.messages.create(
                model=model,
                max_tokens=200,
                temperature=0,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            return response.content[0].text.strip()
        elif hasattr(client, "chat"):
            # OpenAI
            response = client.chat.completions.create(
                model=model,
                max_tokens=200,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
            )
            return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {e}"

    return "Error: no LLM client"


def run_benchmark(data_path, output_path, limit=None, answer_model="claude-sonnet-4-6"):
    """Run the full LongMemEval benchmark."""
    from reccli.runtime.config import Config

    config = Config()
    provider = get_embedding_provider()

    # Set up LLM client for answering
    anthropic_key = config.get_api_key("anthropic")
    openai_key = config.get_api_key("openai")

    if anthropic_key:
        import anthropic
        llm_client = anthropic.Anthropic(api_key=anthropic_key)
    elif openai_key:
        from openai import OpenAI
        llm_client = OpenAI(api_key=openai_key)
    else:
        print("ERROR: No API key for answer generation. Need Anthropic or OpenAI.")
        sys.exit(1)

    # Load dataset
    print(f"Loading {data_path}...")
    data = json.load(open(data_path))
    if limit:
        data = data[:limit]
    print(f"Running {len(data)} questions")

    results = []
    start_time = time.time()

    for i, entry in enumerate(data):
        qid = entry["question_id"]
        question = entry["question"]
        q_type = entry["question_type"]

        print(f"[{i+1}/{len(data)}] {q_type}: {question[:60]}...", end=" ", flush=True)

        # Create temp directory for this question's sessions
        tmpdir = Path(tempfile.mkdtemp(prefix="longmemeval_"))
        sessions_dir = tmpdir / "devsession"
        sessions_dir.mkdir()

        try:
            # 1. Ingest sessions
            ingest_sessions(
                entry["haystack_sessions"],
                entry["haystack_dates"],
                entry["haystack_session_ids"],
                sessions_dir,
                provider,
            )

            # 2. Build index
            build_unified_index(sessions_dir, verbose=False)

            # 3. Retrieve context
            context = retrieve_context(sessions_dir, question, provider, top_k=10)

            # 4. Answer
            hypothesis = answer_question(question, context, llm_client, answer_model)
            print(f"→ {hypothesis[:50]}")

            results.append({
                "question_id": qid,
                "hypothesis": hypothesis,
            })

        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
                "question_id": qid,
                "hypothesis": f"Error: {e}",
            })

        finally:
            # Clean up temp directory
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Save results
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    elapsed = time.time() - start_time
    print(f"\nDone. {len(results)} answers in {elapsed:.1f}s")
    print(f"Output: {output_path}")
    print(f"\nTo evaluate, clone LongMemEval and run:")
    print(f"  python3 src/evaluation/evaluate_qa.py gpt-4o {output_path} {data_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run LongMemEval benchmark with RecCli")
    parser.add_argument("--data", required=True, help="Path to longmemeval JSON file")
    parser.add_argument("--output", required=True, help="Path to output JSONL file")
    parser.add_argument("--limit", type=int, default=None, help="Limit to first N questions")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="LLM model for answer generation")
    args = parser.parse_args()

    run_benchmark(args.data, args.output, args.limit, args.model)
