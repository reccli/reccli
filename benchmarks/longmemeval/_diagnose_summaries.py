"""
One-off diagnostic: re-summarize the 9 swung qids with cleanup disabled,
then dump the actual summary text alongside the raw source conversation.

Outputs to: benchmarks/longmemeval/results/diagnose_summaries_20260426/
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages"))

from reccli.session.devsession import DevSession
from reccli.retrieval.embeddings import get_embedding_provider

# Force OpenAI for both summarization and (unused) embeddings
os.environ["RECCLI_LLM_PROVIDER"] = "openai"

ROOT = Path("/Users/will/coding-projects/RecCli/benchmarks/longmemeval")
OUT = ROOT / "results" / "diagnose_summaries_20260426"
OUT.mkdir(parents=True, exist_ok=True)

QIDS = [
    # 5 real losses (A wrong, B right)
    ("LOST_REAL", "08f4fc43"),
    ("LOST_REAL", "gpt4_d9af6064"),
    ("LOST_REAL", "d01c6aa8"),
    ("LOST_REAL", "gpt4_d31cdae3"),
    ("LOST_REAL", "gpt4_78cf46a3"),
    # 2 real gains (A right, B wrong)
    ("GAINED_REAL", "gpt4_b4a80587"),
    ("GAINED_REAL", "gpt4_88806d6e"),
    # 2 noise (same answer, judge labeled differently)
    ("NOISE", "gpt4_2d58bcd6"),
    ("NOISE", "gpt4_70e84552"),
]

DATA = json.load(open(ROOT / "data/longmemeval_oracle.json"))
GOLD = {e["question_id"]: e for e in DATA}

provider = get_embedding_provider()

for label, qid in QIDS:
    g = GOLD[qid]
    qdir = OUT / f"{label}_{qid}"
    qdir.mkdir(exist_ok=True)
    print(f"\n=== {label} {qid} ===")
    print(f"Q: {g['question']}")
    print(f"GOLD: {g['answer']}")

    # Build + summarize each haystack session
    for i, (msgs, date_str, sid) in enumerate(
        zip(g["haystack_sessions"], g["haystack_dates"], g["haystack_session_ids"])
    ):
        ds = DevSession(session_id=sid)
        ds.metadata["created_at"] = date_str
        ds.metadata["source"] = "longmemeval_diagnostic"
        for m in msgs:
            ds.conversation.append({
                "role": m["role"],
                "content": m["content"],
                "timestamp": date_str,
            })
        ok = ds.generate_summary()
        # Persist the .devsession (with summary). Skip embeddings — we don't need them here.
        out_path = qdir / f"session_{i:04d}.devsession"
        ds.save(out_path, skip_validation=True)
        print(f"  session {i}: summary {'OK' if ok else 'FAIL'} → {out_path.name}")

print(f"\nDone. Summaries saved under {OUT}/")
