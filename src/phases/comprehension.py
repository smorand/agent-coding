"""Comprehension phase (FR-005).

Reads the ticket, the workspace `CLAUDE.md` (if present), and a curated
slice of `.agent_docs/*.md`, then asks the configured LLM to synthesize a
short comprehension report consumed by the planning phase. Persists
`comprehension.json` under `.agent_work/<ticket-id>/` containing the
markdown report, the source files used, the model identity, and the
token usage.

Design choices for the MVP:

- The set of source files is deterministic (CLAUDE.md, ticket, the union
  of all `.agent_docs/*.md` filtered against a per-ticket keyword set).
  Tool-calling is intentionally out of scope; the phase is a single
  LLM round-trip. Phases that need richer navigation (planning,
  implementation) will use the registry.
- Hard caps on per-source and total input bytes avoid runaway prompts.
- When no LLM client is configured (e.g., the run has no config.yaml),
  the phase logs and returns CONTINUE, preserving the skeleton behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from llm.base import ChatMessage, LlmError, Role
from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from state import PhaseName

if TYPE_CHECKING:
    from collections.abc import Iterable

    from llm.base import LlmClient

logger = logging.getLogger(__name__)

COMPREHENSION_REPORT_FILENAME = "comprehension.json"

DEFAULT_PER_FILE_CAP_BYTES = 16_000
DEFAULT_TOTAL_CAP_BYTES = 64_000
DEFAULT_AGENT_DOCS_CAP = 8

CLAUDE_MD_FILENAME = "CLAUDE.md"
AGENT_DOCS_DIRNAME = ".agent_docs"

SYSTEM_PROMPT = (
    "You are the comprehension phase of an autonomous coding agent. "
    "Read the project context and the ticket, then produce a concise "
    "comprehension report in markdown under 1200 words that the planning "
    "phase will consume.\n\n"
    "The report MUST include these sections, in order, with the exact "
    "headers below:\n"
    "## Context\n"
    "## Ticket understanding\n"
    "## Relevant areas of the codebase\n"
    "## Open questions\n"
    "## Risks\n\n"
    "Be specific, cite filenames, do not invent code that is not in the "
    "sources. If a section has nothing to report, write 'None.'."
)


@dataclass(frozen=True)
class ComprehensionSource:
    """One file included as context for the LLM call."""

    label: str
    path: str
    bytes_used: int


@dataclass(frozen=True)
class ComprehensionReport:
    """Persisted artifact recording the comprehension call."""

    summary: str
    sources: tuple[ComprehensionSource, ...]
    model: str
    input_tokens: int
    output_tokens: int
    generated_at: datetime


class ComprehensionPhase(Phase):
    """Synthesize a comprehension report from the ticket and project docs (FR-005)."""

    name = PhaseName.COMPREHENSION

    __slots__ = ("_agent_docs_cap", "_llm_client", "_per_file_cap", "_total_cap", "_workspace")

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        workspace: Path | None = None,
        per_file_cap_bytes: int = DEFAULT_PER_FILE_CAP_BYTES,
        total_cap_bytes: int = DEFAULT_TOTAL_CAP_BYTES,
        agent_docs_cap: int = DEFAULT_AGENT_DOCS_CAP,
    ) -> None:
        self._llm_client = llm_client
        self._workspace = workspace
        self._per_file_cap = per_file_cap_bytes
        self._total_cap = total_cap_bytes
        self._agent_docs_cap = agent_docs_cap

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Read context, call the LLM, persist the report. CONTINUE on success."""
        if self._llm_client is None:
            logger.info("comprehension: no LLM client configured, skipping synthesis")
            return PhaseOutcome()
        workspace = self._resolve_workspace(ctx)
        ticket_path = Path(ctx.ticket_path)
        try:
            sources, prompt_user = await asyncio.to_thread(
                self._collect_context,
                workspace,
                ticket_path,
            )
        except OSError as exc:
            logger.exception("comprehension: failed to collect sources")
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"comprehension failed to read sources: {exc}",
            )
        try:
            response = await self._llm_client.complete(
                [
                    ChatMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT),
                    ChatMessage(role=Role.USER, content=prompt_user),
                ]
            )
        except LlmError as exc:
            logger.warning("comprehension LLM call failed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"comprehension LLM call failed: {exc}",
            )
        report = ComprehensionReport(
            summary=response.content,
            sources=sources,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            generated_at=datetime.now(UTC),
        )
        await asyncio.to_thread(self._persist_report, ctx.work_dir, report)
        logger.info(
            "comprehension synthesized (model=%s, %d input / %d output tokens, %d sources)",
            report.model,
            report.input_tokens,
            report.output_tokens,
            len(report.sources),
        )
        return PhaseOutcome()

    def _resolve_workspace(self, ctx: PhaseContext) -> Path:
        if self._workspace is not None:
            return self._workspace
        # Canonical layout: workspace/.agent_work/<ticket-id>/ -> parent.parent
        return ctx.work_dir.parent.parent

    def _collect_context(
        self,
        workspace: Path,
        ticket_path: Path,
    ) -> tuple[tuple[ComprehensionSource, ...], str]:
        ticket_text = ticket_path.read_text(encoding="utf-8") if ticket_path.exists() else ""
        keywords = _extract_keywords(ticket_text)
        sources: list[ComprehensionSource] = []
        budget = self._total_cap
        sections: list[str] = []

        ticket_excerpt, ticket_used = _truncate(ticket_text, min(self._per_file_cap, budget))
        budget -= ticket_used
        sources.append(ComprehensionSource(label="ticket", path=str(ticket_path), bytes_used=ticket_used))
        sections.append(_format_section("Ticket", ticket_path.name, ticket_excerpt))

        claude_md = workspace / CLAUDE_MD_FILENAME
        if claude_md.exists() and budget > 0:
            text = claude_md.read_text(encoding="utf-8")
            excerpt, used = _truncate(text, min(self._per_file_cap, budget))
            budget -= used
            sources.append(ComprehensionSource(label="claude_md", path=str(claude_md), bytes_used=used))
            sections.append(_format_section("CLAUDE.md", CLAUDE_MD_FILENAME, excerpt))

        agent_docs_dir = workspace / AGENT_DOCS_DIRNAME
        if agent_docs_dir.is_dir() and budget > 0:
            for doc_path in _select_agent_docs(agent_docs_dir, keywords, self._agent_docs_cap):
                if budget <= 0:
                    break
                text = doc_path.read_text(encoding="utf-8")
                excerpt, used = _truncate(text, min(self._per_file_cap, budget))
                budget -= used
                rel = doc_path.relative_to(workspace).as_posix()
                sources.append(ComprehensionSource(label="agent_docs", path=str(doc_path), bytes_used=used))
                sections.append(_format_section("Agent Doc", rel, excerpt))
        prompt = "\n\n".join(sections)
        return tuple(sources), prompt

    @staticmethod
    def _persist_report(work_dir: Path, report: ComprehensionReport) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / COMPREHENSION_REPORT_FILENAME
        payload = {
            "summary": report.summary,
            "model": report.model,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "generated_at": report.generated_at.isoformat(),
            "sources": [{"label": s.label, "path": s.path, "bytes_used": s.bytes_used} for s in report.sources],
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


_KEYWORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_KEYWORD_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "have",
        "has",
        "are",
        "not",
        "use",
        "should",
        "must",
        "ticket",
        "user",
        "story",
        "criteria",
        "acceptance",
        "description",
        "title",
    }
)


def _extract_keywords(text: str, *, limit: int = 20) -> frozenset[str]:
    """Extract a set of significant lower-case keywords from `text`."""
    if not text:
        return frozenset()
    raw = _KEYWORD_PATTERN.findall(text.lower())
    keywords: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if token in _KEYWORD_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return frozenset(keywords)


def _select_agent_docs(
    agent_docs_dir: Path,
    keywords: frozenset[str],
    cap: int,
) -> Iterable[Path]:
    """Score `.agent_docs/*.md` files by keyword overlap with the ticket; return top `cap`."""
    candidates = sorted(agent_docs_dir.glob("*.md"))
    if not candidates:
        return ()
    if not keywords:
        return tuple(candidates[:cap])
    scored: list[tuple[int, Path]] = []
    for path in candidates:
        stem = path.stem.lower()
        score = sum(1 for kw in keywords if kw in stem)
        scored.append((-score, path))
    # Sort by descending score, then by path for determinism.
    scored.sort(key=lambda item: (item[0], str(item[1])))
    return tuple(path for _, path in scored[:cap])


def _truncate(text: str, max_bytes: int) -> tuple[str, int]:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes."""
    if max_bytes <= 0:
        return "", 0
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, len(encoded)
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + "\n... [truncated]", len(truncated.encode("utf-8"))


def _format_section(label: str, name: str, body: str) -> str:
    return f"### {label}: {name}\n\n{body}"
