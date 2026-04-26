"""Multi-mode-consensus parallel reasoning, on demand.

The hook in ``hooks/auto_reason.py`` handles the auto-surface case: when a
prompt's phrasing matches a topic or difficulty pattern at UserPromptSubmit
time, the hook injects scaffolding text that asks the main agent to spawn
parallel sub-agents.

This module handles the complementary explicit-invocation case: an agent
(or a script) can call ``run_mmc_consensus`` directly to dispatch the same
3-lens reasoning pass via subprocess CLI adapters, regardless of whether
the prompt would have triggered the hook. Same framings, same scaffold,
different invocation path.

Reuses the subprocess infrastructure from ``agent_providers`` — the same
auth surface ``audit_feature`` and ``propose_patch`` use, so MMC runs on
the caller's existing Claude Code or Codex subscription.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hooks.auto_reason import _FRAMINGS, _SCAFFOLDS, detect_intent

VALID_MODES = ("auto", "debug", "planning")


def _resolve_mode(prompt: str, mode: str) -> str:
    """Resolve mode='auto' via intent detection; fall back to 'planning'."""
    mode_normalized = (mode or "auto").strip().lower()
    if mode_normalized in {"debug", "planning"}:
        return mode_normalized
    if mode_normalized != "auto":
        raise ValueError(
            f"mode must be one of {VALID_MODES}, got {mode!r}"
        )
    detected = detect_intent(prompt)
    return detected or "planning"


def _build_agent_prompt(framing: str, scaffold: str, user_prompt: str) -> str:
    """Compose the full prompt for one MMC sub-agent.

    Each sub-agent receives:
    1. The lens (framing) it should reason from.
    2. The diverge→converge→validate scaffold for the resolved mode.
    3. The user's original prompt verbatim.
    """
    return (
        f"{framing}\n\n"
        f"{scaffold}\n\n"
        f"## Problem\n\n"
        f"{user_prompt}\n"
    )


def _dispatch_one(
    project_root: Path,
    provider: str,
    model: Optional[str],
    timeout: int,
    framing: str,
    scaffold: str,
    user_prompt: str,
    output_path: Optional[Path],
) -> Dict[str, Any]:
    """Dispatch a single framing to the provider CLI. Never raises."""
    from .agent_providers import run_provider_prompt

    full_prompt = _build_agent_prompt(framing, scaffold, user_prompt)
    try:
        result = run_provider_prompt(
            provider=provider,
            project_root=project_root,
            prompt=full_prompt,
            timeout_seconds=timeout,
            output_path=output_path if str(provider).lower() == "codex" else None,
            model=model,
        )
        return {
            "raw_output": result["raw_output"],
            "returncode": result["returncode"],
            "stderr": result["stderr"],
            "error": None,
        }
    except Exception as exc:
        return {
            "raw_output": "",
            "returncode": -1,
            "stderr": "",
            "error": str(exc),
        }


def run_mmc_consensus(
    prompt: str,
    *,
    mode: str = "auto",
    framings: Optional[List[str]] = None,
    provider: str = "claude",
    model: Optional[str] = None,
    working_directory: Optional[Path] = None,
    timeout_seconds: int = 600,
) -> Dict[str, Any]:
    """Dispatch a multi-lens parallel reasoning pass and return all responses.

    Each lens (framing) sees the same user prompt with a different analytical
    focus prepended, plus the diverge→converge→validate scaffold for the
    resolved mode. Sub-agents run independently — that independence is what
    makes the cross-lens comparison signal meaningful.

    Returns the raw responses for the caller to synthesize. The calling
    agent should compare conclusions, identify convergence (multiple lenses
    arriving at the same root cause / approach), and present synthesis with
    confidence levels — same protocol the hook scaffold describes.

    Args:
        prompt: The user's original problem statement.
        mode: ``"auto"`` (default; uses ``detect_intent`` then falls back to
            ``"planning"``), ``"debug"``, or ``"planning"``. Determines which
            scaffold and which framings are applied.
        framings: Override the default lenses for the resolved mode. When
            ``None``, uses the canonical 3 framings from
            ``hooks/auto_reason.py`` so the hook and this tool stay in sync.
        provider: ``"claude"`` or ``"codex"``. Auto-resolution should be
            done by the caller (e.g. ``mcp_server.run_mmc``).
        model: Optional model override. When ``None``, the CLI's compiled
            default applies.
        working_directory: Project root for codex's ``--cd``. Defaults to
            ``Path.cwd()`` when not provided.
        timeout_seconds: Per-sub-agent subprocess timeout.

    Returns a dict with the resolved mode, framings used, and one entry per
    sub-agent in ``responses``. Never raises — dispatch failures are
    surfaced via ``responses[i].error`` so callers can read partial results.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt must be non-empty")

    mode_resolved = _resolve_mode(prompt, mode)
    scaffold = _SCAFFOLDS[mode_resolved]

    if framings is None:
        framings_resolved = list(_FRAMINGS[mode_resolved])
    else:
        framings_resolved = [str(f).strip() for f in framings if str(f).strip()]
        if not framings_resolved:
            raise ValueError("framings must contain at least one non-empty entry")

    project_root = (
        Path(working_directory).expanduser().resolve()
        if working_directory
        else Path.cwd()
    )

    responses: List[Dict[str, Any]] = []
    for i, framing in enumerate(framings_resolved):
        # Each codex dispatch needs a unique --output-last-message file so
        # parallel-eligible runs in the future don't clobber each other.
        output_path = project_root / f".mmc_output_{i}.txt"
        try:
            result = _dispatch_one(
                project_root=project_root,
                provider=provider,
                model=model,
                timeout=timeout_seconds,
                framing=framing,
                scaffold=scaffold,
                user_prompt=prompt,
                output_path=output_path,
            )
        finally:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass

        responses.append({
            "framing_index": i,
            "framing": framing,
            **result,
        })

    return {
        "mode": mode_resolved,
        "mode_requested": mode,
        "provider": provider,
        "model": model,
        "framings_count": len(framings_resolved),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "responses": responses,
    }
