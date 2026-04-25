"""
LongMemEval evaluator — local re-implementation.

Port of the judge logic from https://github.com/xiaowu0162/LongMemEval's
`src/evaluation/evaluate_qa.py` (verbatim prompt templates). Uses GPT-4o as
the judge (model version 2024-08-06 — the exact version used by LongMemEval's
published baselines) so RecCli scores are directly comparable to their
leaderboard numbers.

Why local: keeps the judge prompt under version control alongside our
hypotheses, makes the evaluation reproducible without an external repo
clone, and lets us publish the prompt on reccli.com for transparency.

Usage:
    PYTHONPATH=packages python3 benchmarks/longmemeval/evaluate.py \
        --hyp benchmarks/longmemeval/results/oracle_full_20260418.jsonl \
        --ref benchmarks/longmemeval/data/longmemeval_oracle.json \
        --tag static

Output: writes a .eval.jsonl file with per-question verdicts and prints
aggregate accuracy + per-category breakdown.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages"))


# ---------------------------------------------------------------------------
# Judge prompts — VERBATIM from LongMemEval's evaluate_qa.py
# https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py
# Any change here breaks comparability with their leaderboard.
# ---------------------------------------------------------------------------

_GENERIC_TEMPLATE = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response is equivalent to the correct answer or contains all the intermediate "
    "steps to get the correct answer, you should also answer yes. If the response only "
    "contains a subset of the information required by the answer, answer no. \n\n"
    "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_TEMPORAL_TEMPLATE = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response is equivalent to the correct answer or contains all the intermediate "
    "steps to get the correct answer, you should also answer yes. If the response only "
    "contains a subset of the information required by the answer, answer no. In addition, "
    "do not penalize off-by-one errors for the number of days. If the question asks for the "
    "number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., "
    "predicting 19 days when the answer is 18), the model's response is still correct. \n\n"
    "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_KNOWLEDGE_UPDATE_TEMPLATE = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response contains some previous information along with an updated answer, the "
    "response should be considered as correct as long as the updated answer is the required "
    "answer.\n\n"
    "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_PREFERENCE_TEMPLATE = (
    "I will give you a question, a rubric for desired personalized response, and a response "
    "from a model. Please answer yes if the response satisfies the desired response. "
    "Otherwise, answer no. The model does not need to reflect all the points in the rubric. "
    "The response is correct as long as it recalls and utilizes the user's personal "
    "information correctly.\n\n"
    "Question: {}\n\nRubric: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_ABSTENTION_TEMPLATE = (
    "I will give you an unanswerable question, an explanation, and a response from a model. "
    "Please answer yes if the model correctly identifies the question as unanswerable. "
    "The model could say that the information is incomplete, or some other information is "
    "given but the asked information is not.\n\n"
    "Question: {}\n\nExplanation: {}\n\nModel Response: {}\n\n"
    "Does the model correctly identify the question as unanswerable? Answer yes or no only."
)


def get_anscheck_prompt(task: str, question: str, answer: str, response: str, abstention: bool) -> str:
    if abstention:
        return _ABSTENTION_TEMPLATE.format(question, answer, response)
    if task in ("single-session-user", "single-session-assistant", "multi-session"):
        return _GENERIC_TEMPLATE.format(question, answer, response)
    if task == "temporal-reasoning":
        return _TEMPORAL_TEMPLATE.format(question, answer, response)
    if task == "knowledge-update":
        return _KNOWLEDGE_UPDATE_TEMPLATE.format(question, answer, response)
    if task == "single-session-preference":
        return _PREFERENCE_TEMPLATE.format(question, answer, response)
    raise NotImplementedError(f"Unknown task type: {task}")


# ---------------------------------------------------------------------------
# Judge call with retry
# ---------------------------------------------------------------------------

def call_judge(client, model: str, prompt: str, max_retries: int = 4) -> str:
    import openai
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                n=1,
                temperature=0,
                max_tokens=10,
            )
            return resp.choices[0].message.content.strip()
        except (openai.RateLimitError, openai.APIError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Judge call failed after {max_retries} retries: {last_err}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

JUDGE_MODEL_ID = "gpt-4o-2024-08-06"  # Matches LongMemEval's reported runs


def run(hyp_path: Path, ref_path: Path, tag: str | None, judge_model: str):
    from reccli.runtime.config import Config
    import openai

    config = Config()
    api_key = config.get_api_key("openai")
    if not api_key:
        print("ERROR: OPENAI_API_KEY required for judge model.")
        sys.exit(1)
    client = openai.OpenAI(api_key=api_key)

    # Load references (oracle is a JSON array; s_cleaned may also be)
    try:
        references = json.load(open(ref_path))
    except Exception:
        references = [json.loads(l) for l in open(ref_path)]
    qid2entry = {e["question_id"]: e for e in references}

    # Load hypotheses (JSONL)
    try:
        hyps = [json.loads(l) for l in open(hyp_path)]
    except Exception:
        hyps = json.load(open(hyp_path))

    suffix = f".{tag}" if tag else ""
    result_path = hyp_path.with_suffix(hyp_path.suffix + f".eval{suffix}.jsonl")

    logs = []
    qtype_hits: Dict[str, list] = {}
    start = time.time()

    with open(result_path, "w") as out_f:
        for i, entry in enumerate(hyps, 1):
            qid = entry["question_id"]
            if qid not in qid2entry:
                print(f"Skipping {qid}: not in reference data.")
                continue
            ref = qid2entry[qid]
            qtype = ref["question_type"]
            q = ref["question"]
            ans = ref["answer"]
            hyp = entry.get("hypothesis", "")
            abstention = "_abs" in qid

            prompt = get_anscheck_prompt(qtype, q, ans, hyp, abstention)
            verdict_text = call_judge(client, judge_model, prompt)
            label = "yes" in verdict_text.lower()

            out_entry = {
                **entry,
                "question_type": qtype,
                "answer": ans,
                "autoeval_label": {"model": judge_model, "label": label},
            }
            out_f.write(json.dumps(out_entry) + "\n")
            out_f.flush()
            logs.append(out_entry)
            qtype_hits.setdefault(qtype, []).append(1 if label else 0)

            if i % 25 == 0 or i == len(hyps):
                elapsed = time.time() - start
                acc = sum(1 for e in logs if e["autoeval_label"]["label"]) / len(logs)
                print(f"[{i}/{len(hyps)}] running accuracy: {acc:.3f}  elapsed: {elapsed:.0f}s")

    total = len(logs)
    if total == 0:
        print("No entries scored.")
        return

    correct = sum(1 for e in logs if e["autoeval_label"]["label"])
    print(f"\n=== Results ({hyp_path.name}) ===")
    print(f"Overall accuracy: {correct / total:.4f}  ({correct}/{total})")
    print(f"Judge: {judge_model}")
    print("By question type:")
    for qt in sorted(qtype_hits):
        hits = qtype_hits[qt]
        print(f"  {qt:<30} {sum(hits) / len(hits):.4f}  ({sum(hits)}/{len(hits)})")
    print(f"\nSaved: {result_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate LongMemEval hypotheses with GPT-4o judge.")
    parser.add_argument("--hyp", required=True, help="Path to hypothesis JSONL")
    parser.add_argument("--ref", required=True, help="Path to reference JSON (oracle or s_cleaned)")
    parser.add_argument("--tag", default=None, help="Optional tag appended to output filename")
    parser.add_argument("--judge-model", default=JUDGE_MODEL_ID,
                        help=f"Judge model (default {JUDGE_MODEL_ID} — matches LongMemEval leaderboard)")
    args = parser.parse_args()

    run(Path(args.hyp), Path(args.ref), args.tag, args.judge_model)
