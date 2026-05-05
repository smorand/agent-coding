"""Planning phase (FR-006).

Reads the ticket and the persisted comprehension report, asks the
configured LLM to produce a structured plan with three sections (PLAN,
TODO, INFRA NEEDS), writes them as separate markdown files under
`.agent_work/<ticket-id>/`, and validates the declared infrastructure
requirements against the workspace's `docker-compose.yml`.

If any requirement names a known service (postgres, redis, mysql, …)
that is not present in `docker-compose.yml`, the phase appends a
templated comment to the ticket and halts the pipeline with exit code 1
(per E2E-025). Otherwise it returns CONTINUE so the orchestrator
advances to the E2E writing phase.

When no LLM client is configured (e.g., the run has no config.yaml),
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
    from llm.base import LlmClient

logger = logging.getLogger(__name__)

PLAN_FILENAME = "plan.md"
TODO_FILENAME = "todo.md"
INFRA_NEEDS_FILENAME = "infra_needs.md"
PLANNING_REPORT_FILENAME = "planning.json"
COMPREHENSION_REPORT_FILENAME = "comprehension.json"

DOCKER_COMPOSE_CANDIDATES: tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

KNOWN_SERVICES: frozenset[str] = frozenset(
    {
        "postgres",
        "postgresql",
        "mysql",
        "mariadb",
        "redis",
        "mongodb",
        "mongo",
        "elasticsearch",
        "rabbitmq",
        "kafka",
        "minio",
        "memcached",
        "etcd",
        "clickhouse",
    }
)

SECTION_HEADERS = {
    "plan": "## PLAN",
    "todo": "## TODO",
    "infra": "## INFRA NEEDS",
}

SYSTEM_PROMPT = (
    "You are the planning phase of an autonomous coding agent. Given the "
    "ticket and the comprehension report, produce a structured plan that "
    "the implementation phase will follow.\n\n"
    "Output format - your response MUST contain the three sections below in "
    "this exact order with these exact headers:\n\n"
    "## PLAN\n\n"
    "A concise plan: high-level approach, files to touch (one line each "
    "with rationale), expected diff size. Under 400 words.\n\n"
    "## TODO\n\n"
    "An ordered checklist of actionable tasks for the implementation phase, "
    "one per line as `- [ ] <task>`. Each task must be specific and "
    "verifiable. Aim for 5 to 15 tasks.\n\n"
    "## INFRA NEEDS\n\n"
    "Declare every infrastructure requirement (services, environment "
    "variables, binaries) needed by the implementation, one bullet per "
    "requirement. If none are needed, write a single line: 'None.'.\n\n"
    "Be specific. Cite filenames from the comprehension report. Do not "
    "invent modules that are not in the codebase."
)


@dataclass(frozen=True)
class PlanningArtifacts:
    """The three markdown sections extracted from the LLM response."""

    plan: str
    todo: str
    infra_needs: str


@dataclass(frozen=True)
class PlanningReport:
    """Persisted artifact recording the planning call."""

    artifacts: PlanningArtifacts
    model: str
    input_tokens: int
    output_tokens: int
    generated_at: datetime


@dataclass(frozen=True)
class InfraIssue:
    """One unsatisfied infrastructure requirement."""

    requirement: str
    service: str
    reason: str


class PlanningPhase(Phase):
    """Plan the implementation and declare infra needs (FR-006)."""

    name = PhaseName.PLANNING

    __slots__ = ("_llm_client", "_workspace")

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        workspace: Path | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._workspace = workspace

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Read context, call the LLM, persist artifacts, validate infra."""
        if self._llm_client is None:
            logger.info("planning: no LLM client configured, skipping synthesis")
            return PhaseOutcome()
        workspace = self._resolve_workspace(ctx)
        ticket_path = Path(ctx.ticket_path)
        try:
            user_prompt = await asyncio.to_thread(
                self._build_user_prompt,
                ticket_path,
                ctx.work_dir,
            )
        except OSError as exc:
            logger.exception("planning: failed to read inputs")
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"planning failed to read inputs: {exc}",
            )
        try:
            response = await self._llm_client.complete(
                [
                    ChatMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT),
                    ChatMessage(role=Role.USER, content=user_prompt),
                ]
            )
        except LlmError as exc:
            logger.warning("planning LLM call failed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"planning LLM call failed: {exc}",
            )
        try:
            artifacts = parse_planning_response(response.content)
        except ValueError as exc:
            logger.warning("planning response could not be parsed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"planning response malformed: {exc}",
            )
        report = PlanningReport(
            artifacts=artifacts,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            generated_at=datetime.now(UTC),
        )
        await asyncio.to_thread(self._persist_artifacts, ctx.work_dir, report)
        issues = await asyncio.to_thread(
            validate_infra,
            workspace,
            artifacts.infra_needs,
        )
        if issues:
            await asyncio.to_thread(self._append_comment, ticket_path, issues)
            logger.warning(
                "planning halted: %d unsatisfied infra requirement(s) (%s)",
                len(issues),
                ", ".join(issue.requirement for issue in issues),
            )
            return PhaseOutcome(
                kind=OutcomeKind.HALT_DOR_FAILED,
                message=("infrastructure not provisioned: " + ", ".join(issue.requirement for issue in issues)),
            )
        logger.info(
            "planning synthesized (model=%s, %d input / %d output tokens)",
            report.model,
            report.input_tokens,
            report.output_tokens,
        )
        return PhaseOutcome()

    def _resolve_workspace(self, ctx: PhaseContext) -> Path:
        if self._workspace is not None:
            return self._workspace
        # Canonical layout: workspace/.agent_work/<ticket-id>/ -> parent.parent
        return ctx.work_dir.parent.parent

    @staticmethod
    def _build_user_prompt(ticket_path: Path, work_dir: Path) -> str:
        ticket_text = ticket_path.read_text(encoding="utf-8") if ticket_path.exists() else ""
        comprehension_path = work_dir / COMPREHENSION_REPORT_FILENAME
        comprehension_summary = ""
        if comprehension_path.exists():
            try:
                payload = json.loads(comprehension_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
            comprehension_summary = str(payload.get("summary", ""))
        sections = [f"### Ticket: {ticket_path.name}\n\n{ticket_text}"]
        if comprehension_summary:
            sections.append(f"### Comprehension report\n\n{comprehension_summary}")
        return "\n\n".join(sections)

    @staticmethod
    def _persist_artifacts(work_dir: Path, report: PlanningReport) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / PLAN_FILENAME).write_text(report.artifacts.plan, encoding="utf-8")
        (work_dir / TODO_FILENAME).write_text(report.artifacts.todo, encoding="utf-8")
        (work_dir / INFRA_NEEDS_FILENAME).write_text(report.artifacts.infra_needs, encoding="utf-8")
        meta = {
            "model": report.model,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "generated_at": report.generated_at.isoformat(),
        }
        (work_dir / PLANNING_REPORT_FILENAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _append_comment(ticket_path: Path, issues: tuple[InfraIssue, ...]) -> None:
        with ticket_path.open("a", encoding="utf-8") as fh:
            fh.write("\n")
            fh.write(format_infra_comment(issues))


def parse_planning_response(text: str) -> PlanningArtifacts:
    """Split the LLM response on the three section headers; raise ValueError if malformed."""
    plan_idx = text.find(SECTION_HEADERS["plan"])
    todo_idx = text.find(SECTION_HEADERS["todo"])
    infra_idx = text.find(SECTION_HEADERS["infra"])
    if plan_idx < 0 or todo_idx < 0 or infra_idx < 0:
        missing = [label for label, idx in (("plan", plan_idx), ("todo", todo_idx), ("infra", infra_idx)) if idx < 0]
        msg = f"missing required section(s): {', '.join(missing)}"
        raise ValueError(msg)
    if not (plan_idx < todo_idx < infra_idx):
        msg = "section headers must appear in order: PLAN, TODO, INFRA NEEDS"
        raise ValueError(msg)
    plan = text[plan_idx + len(SECTION_HEADERS["plan"]) : todo_idx].strip()
    todo = text[todo_idx + len(SECTION_HEADERS["todo"]) : infra_idx].strip()
    infra = text[infra_idx + len(SECTION_HEADERS["infra"]) :].strip()
    if not plan or not todo or not infra:
        msg = "all three sections must contain non-empty content"
        raise ValueError(msg)
    return PlanningArtifacts(plan=plan, todo=todo, infra_needs=infra)


_BULLET_PATTERN = re.compile(r"^[-*]\s+(.+)$", re.MULTILINE)


def parse_infra_needs(text: str) -> tuple[str, ...]:
    """Extract bullet-list requirements from `infra_needs.md` text."""
    stripped = text.strip()
    if not stripped or stripped.lower().startswith("none"):
        return ()
    matches = _BULLET_PATTERN.findall(stripped)
    return tuple(item.strip() for item in matches if item.strip())


def detect_service(requirement: str) -> str | None:
    """Return the known service name mentioned in `requirement`, or None."""
    lowered = requirement.lower()
    for service in KNOWN_SERVICES:
        if re.search(rf"\b{re.escape(service)}\b", lowered):
            return service
    return None


def find_compose_file(workspace: Path) -> Path | None:
    """Return the first existing docker-compose file in `workspace`, or None."""
    for name in DOCKER_COMPOSE_CANDIDATES:
        candidate = workspace / name
        if candidate.exists():
            return candidate
    return None


def compose_declares_service(compose_path: Path, service: str) -> bool:
    """Best-effort textual check that `compose_path` declares `service` (or its image)."""
    try:
        text = compose_path.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    pattern = re.compile(rf"\b{re.escape(service)}\b")
    return bool(pattern.search(text))


def validate_infra(workspace: Path, infra_needs_text: str) -> tuple[InfraIssue, ...]:
    """Return any infra requirement whose mentioned service is not in docker-compose."""
    requirements = parse_infra_needs(infra_needs_text)
    if not requirements:
        return ()
    compose_path = find_compose_file(workspace)
    issues: list[InfraIssue] = []
    for requirement in requirements:
        service = detect_service(requirement)
        if service is None:
            continue  # not a service we recognize; trust the human
        if compose_path is None or not compose_declares_service(compose_path, service):
            reason = (
                f"service {service!r} not found in docker-compose"
                if compose_path is not None
                else "no docker-compose file present in the workspace"
            )
            issues.append(InfraIssue(requirement=requirement, service=service, reason=reason))
    return tuple(issues)


def format_infra_comment(issues: tuple[InfraIssue, ...]) -> str:
    """Format the templated comment appended to the ticket on infra failure."""
    lines = ["<!-- agent-code planning report -->", "", "## Infrastructure not provisioned", ""]
    for issue in issues:
        lines.append(
            f"- Infrastructure requirement {issue.requirement!r} is not provisioned in this "
            f"project. Please add a docker-compose service or update the ticket."
        )
    lines.append("")
    return "\n".join(lines)
