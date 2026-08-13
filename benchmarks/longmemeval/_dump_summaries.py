"""Dump raw conversation + actual summary side-by-side for diagnostic analysis."""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/will/coding-projects/RecCli/benchmarks/longmemeval")
DIAG = ROOT / "results/diagnose_summaries_20260426"

DATA = json.load(open(ROOT / "data/longmemeval_oracle.json"))
GOLD = {e["question_id"]: e for e in DATA}

A = {}
with open(ROOT / "results/oracle_openai_summarize_50_20260426.jsonl") as f:
    for line in f:
        r = json.loads(line); A[r["question_id"]] = r["hypothesis"]
B = {}
with open(ROOT / "results/oracle_openai_nosummary_50_20260426.jsonl") as f:
    for line in f:
        r = json.loads(line); B[r["question_id"]] = r["hypothesis"]

def fmt_summary(s):
    if not s:
        return "(no summary)"
    out = []
    out.append(f"OVERVIEW: {s.get('overview', '(none)')[:400]}")
    for cat in ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]:
        items = s.get(cat, [])
        if items:
            out.append(f"  {cat.upper()}:")
            for it in items:
                title = it.get("title") or it.get("decision") or it.get("description") or it.get("issue") or it.get("step") or ""
                detail = it.get("detail") or it.get("rationale") or it.get("solution") or ""
                out.append(f"    - {title}")
                if detail:
                    out.append(f"      detail: {detail[:300]}")
    return "\n".join(out)

# Process LOST cases first, then GAINED
target = sys.argv[1] if len(sys.argv) > 1 else "LOST"
qdirs = sorted(d for d in DIAG.iterdir() if d.is_dir() and d.name.startswith(target))

for qdir in qdirs:
    qid = qdir.name.split("_", 2)[-1]
    if qid.startswith("REAL_"):
        qid = qid[5:]
    g = GOLD[qid]
    print("\n" + "#"*80)
    print(f"# {qdir.name}")
    print("#"*80)
    print(f"Q:    {g['question']}")
    print(f"GOLD: {g['answer']}")
    print(f"A (with-summary): {A[qid]}")
    print(f"B (no-summary):   {B[qid]}")

    for sf in sorted(qdir.glob("*.devsession")):
        with open(sf) as f:
            d = json.load(f)
        print(f"\n  --- {sf.name} ---")
        print(f"  RAW CONVERSATION ({len(d['conversation'])} msgs):")
        for i, m in enumerate(d["conversation"]):
            content = m["content"].replace("\n", " ")[:200]
            print(f"    [{i}] {m['role']}: {content}")
        print(f"\n  SUMMARY:")
        print("  " + fmt_summary(d.get("summary")).replace("\n", "\n  "))
