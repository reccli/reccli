"""
Auto-Reason + MMC — reasoning scaffold injection and parallel agent comparison.

Detects user intent (debug, design/planning) from the prompt text using
fast regex heuristics. When triggered:

- **Auto-Reason only**: Injects a diverge→converge→validate scaffold
- **MMC enabled**: Injects instructions for parallel agent execution where
  each agent independently runs the full reasoning scaffold with a varied
  problem framing, then the main agent extracts consensus from their outputs

MMC (Multiple Model Comparison) is self-consistency sampling applied to
coding tasks — when multiple independent reasoning paths converge on the
same answer, confidence is multiplicative.
"""

import re
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Intent detection patterns (scored by match count)
# ---------------------------------------------------------------------------

_DEBUG_PATTERNS = [
    r'\b(error|bug|crash|exception|traceback|stack\s*trace|segfault|panic)\b',
    r'\b(fails?|failing|broken|not\s+working|doesn\'t\s+work|won\'t\s+work)\b',
    r'\b(debug|diagnose|investigate|troubleshoot|figure\s+out\s+why)\b',
    r'\b[45][0-9]{2}\s*(error|status)?\b',
    r'\bwhy\s+(is|does|did|are|do)\b.*\b(not|wrong|fail|break)',
    r'\b(undefined|null\s+pointer|type\s*error|key\s*error|index\s*error)\b',
    r'\b(regression|flaky|intermittent|race\s+condition)\b',
]

_PLANNING_PATTERNS = [
    r'\b(architect(ure)?|design|approach|strategy|pattern)\b',
    r'\b(should\s+(we|i)|which\s+(approach|way|method)|best\s+way|trade-?offs?)\b',
    r'\b(plan|propose|recommend|evaluate|compare)\b',
    r'\b(schema|api\s+design|data\s+model|system\s+design|interface\s+design)\b',
    r'\bhow\s+should\s+(we|i)\b',
    r'\b(scalab|extensib|maintainab)(le|ility)\b',
    r'\b(let\'?s\s+(figure\s+out|plan|think\s+about))\b',
    r'\b(how\s+(do|can|would)\s+(we|i)\s+(do|implement|build|approach))\b',
    r'\b(refactor|restructure|reorganize|clean\s*up|simplify|extract|decouple)\b',
    r'\b(migrate|migration|move\s+to|switch\s+to|upgrade)\b',
]

_MODE_PATTERNS = [
    ('debug', _DEBUG_PATTERNS),
    ('planning', _PLANNING_PATTERNS),
]

# Difficulty / "more thinking" indicators. These don't classify the prompt
# topically — they're meta-signals that the user wants deeper reasoning,
# regardless of whether the prompt looks like a debug or planning request.
# Match any of these and we'll fall back to planning-mode MMC (the broader
# exploration scaffold) so users who say "think harder" / "I'm stuck" /
# "ultrathink" / "use mmc" actually get parallel reasoning instead of
# silently falling through detect_intent because their phrasing didn't match
# a topic regex.
_DIFFICULTY_PATTERNS = [
    # Explicit "more thinking" requests
    r'\bthink\s+(harder|deeper|more|carefully|thoroughly)\b',
    r'\b(more|deeper|harder)\s+(thinking|thought|reasoning|analysis)\b',
    r'\bgive\s+(it|me)\s+(more|deeper)\s+thought\b',
    r'\blet\'?s\s+(really|truly|actually)\s+think\b',
    r'\b(ultrathink|reason\s+thoroughly)\b',

    # Stuck / circular signals
    r'\b(stuck|stumped|going\s+in\s+circles|whack-?a-?mole)\b',
    r"\b(can'?t|cannot)\s+figure\s+(it|this)?\s*out\b",
    r'\bbeen\s+(at\s+this|trying\s+for)\b',

    # Difficulty-acknowledging phrasing
    r'\b(complex|tricky|nuanced|subtle|delicate|hard|difficult)\s+'
    r'(problem|issue|case|question|topic|design|bug|task)\b',
    r'\b(thorny|gnarly|nasty)\s+(problem|bug|issue|case)\b',

    # Multi-perspective / consensus requests
    r'\b(multiple|several|different)\s+(approaches|angles|perspectives|lenses|framings)\b',
    r'\bfrom\s+different\s+angles\b',
    r'\b(consider\s+all|all\s+the\s+(ways|angles|approaches))\b',

    # Explicit MMC mentions — user-side and agent-side
    r'\bmmc\b',
    r'\b(multi-?agent|parallel\s+agents?|multiple\s+models?)\b',
    r'\b(self-?consistency|consensus\s+sampling)\b',
]

# ---------------------------------------------------------------------------
# Auto-Reason scaffolds (used standalone or injected into each MMC agent)
# ---------------------------------------------------------------------------

_SCAFFOLDS = {
    'debug': (
        "Before implementing a fix, follow this reasoning scaffold:\n"
        "1. Reflect on 5-7 different possible sources of the problem\n"
        "2. Analyze the relevant code paths in the codebase for each hypothesis\n"
        "3. Distill down to the 1-2 most likely root causes\n"
        "4. Add targeted error logs or assertions to validate your assumptions\n"
        "5. Only after validation, implement the minimal fix\n"
        "Do not jump straight to a solution — systematic diagnosis prevents whack-a-mole fixes."
    ),
    'planning': (
        "Before recommending an approach, follow this reasoning scaffold:\n"
        "1. Consider 5-7 distinct approaches to this problem\n"
        "2. For each, evaluate: complexity, maintainability, performance, and risk\n"
        "3. Identify trade-offs between the top candidates\n"
        "4. Narrow to the 1-2 most promising approaches\n"
        "5. Justify your recommendation with specific trade-off analysis\n"
        "Do not default to the first viable approach — explore the solution space first."
    ),
}

# ---------------------------------------------------------------------------
# MMC agent framing variations
# ---------------------------------------------------------------------------

_DEBUG_FRAMINGS = [
    (
        "You are analyzing this problem with a focus on RECENT CHANGES. "
        "What changed most recently that could have introduced this issue? "
        "Start from the latest modifications and work backwards."
    ),
    (
        "You are analyzing this problem with a focus on DATA FLOW. "
        "Trace the data from input to the point of failure. "
        "Where does the data get transformed, and which transformation could be wrong?"
    ),
    (
        "You are analyzing this problem with a focus on ASSUMPTIONS. "
        "What assumptions does the code make about its inputs, environment, or state? "
        "Which of those assumptions might be violated?"
    ),
]

_PLANNING_FRAMINGS = [
    (
        "You are evaluating this problem with a focus on SIMPLICITY. "
        "What is the simplest possible solution that fully addresses the requirements? "
        "Prefer fewer moving parts and less abstraction."
    ),
    (
        "You are evaluating this problem with a focus on ROBUSTNESS. "
        "What solution would be most resilient to future changes, edge cases, "
        "and unexpected inputs? Prefer defensive approaches."
    ),
    (
        "You are evaluating this problem with a focus on PERFORMANCE AND SCALE. "
        "What solution would perform best as data grows and usage increases? "
        "Consider the hot paths and bottlenecks."
    ),
]

_FRAMINGS = {
    'debug': _DEBUG_FRAMINGS,
    'planning': _PLANNING_FRAMINGS,
}


def detect_intent(prompt: str) -> Optional[str]:
    """Detect user intent from prompt text.

    Returns 'debug', 'planning', or None. Two axes are considered:

    1. **Topic axis** (debug / planning) — pattern match count is the score;
       highest wins. Ties are broken by preferring 'planning' (the broader
       exploration scaffold; the debug scaffold assumes something is broken).

    2. **Difficulty axis** (``_DIFFICULTY_PATTERNS``) — meta-signals that the
       user wants more thinking, regardless of topic. When the topic axis
       finds nothing but difficulty signals are present, fall back to
       'planning' so prompts like "think harder", "I'm stuck", "ultrathink",
       or literal "use mmc" actually trigger the MMC scaffold instead of
       silently falling through.
    """
    text = prompt.lower()

    scores = {}
    for mode, patterns in _MODE_PATTERNS:
        score = sum(1 for p in patterns if re.search(p, text))
        if score > 0:
            scores[mode] = score

    if scores:
        # Topic detected — existing tie-broken-by-planning ranking applies.
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if len(ranked) >= 2 and ranked[0][1] == ranked[1][1]:
            return 'planning'
        return ranked[0][0]

    # No topic match — but difficulty signals are present? Default to planning.
    if any(re.search(p, text) for p in _DIFFICULTY_PATTERNS):
        return 'planning'

    return None


def get_reasoning_scaffold(prompt: str) -> Optional[str]:
    """Return auto-reason scaffold text if intent is detected, else None.

    This is the standalone scaffold without MMC parallel execution.
    """
    intent = detect_intent(prompt)
    if intent is None:
        return None
    return f"[RecCli Auto-Reason: {intent.title()} Mode]\n{_SCAFFOLDS[intent]}"


def get_mmc_protocol(prompt: str) -> Optional[str]:
    """Return full MMC parallel execution protocol if intent is detected.

    When MMC is enabled, this replaces the simple scaffold with instructions
    for Claude to spawn parallel agents, each with the full reasoning scaffold
    plus a unique problem framing, then extract consensus.
    """
    intent = detect_intent(prompt)
    if intent is None:
        return None

    scaffold = _SCAFFOLDS[intent]
    framings = _FRAMINGS[intent]

    # Build the agent spawn instructions
    agent_blocks = []
    for i, framing in enumerate(framings, 1):
        agent_blocks.append(
            f"**Agent {i}** — {framing}\n"
            f"Give this agent the user's problem and this reasoning scaffold:\n"
            f"---\n{scaffold}\n---"
        )

    agents_text = "\n\n".join(agent_blocks)

    return (
        f"[RecCli MMC: {intent.title()} Mode — Parallel Reasoning]\n"
        f"This problem benefits from multiple independent analyses. "
        f"Use the Agent tool to launch {len(framings)} agents IN PARALLEL "
        f"(all in a single message with multiple tool calls). "
        f"Each agent will independently reason through the problem with a different "
        f"analytical lens, following the full diverge→converge scaffold.\n\n"
        f"{agents_text}\n\n"
        f"**After all agents return**, compare their conclusions:\n"
        f"1. Identify where 2+ agents converged on the same root cause or approach\n"
        f"2. Convergent conclusions have high confidence — report these first\n"
        f"3. Unique conclusions from a single agent may still be valuable — note them as lower confidence\n"
        f"4. Present your synthesis to the user with confidence levels before taking action\n\n"
        f"IMPORTANT: Each agent prompt MUST include the full problem context "
        f"(the user's message, relevant file paths, error messages). "
        f"Agents do not share context with each other — that independence is what makes the comparison valid."
    )
