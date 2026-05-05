"""Phase implementations for the agent-code orchestrator.

The pipeline executes phases in the order defined by `PIPELINE`. Each phase
implements the `Phase` interface defined in `phases.base`.
"""

from __future__ import annotations

from phases.base import Phase, PhaseContext, PhaseOutcome
from phases.classification import ClassificationPhase
from phases.comprehension import ComprehensionPhase
from phases.dor import DorPhase
from phases.e2e_writing import E2eWritingPhase
from phases.implementation import ImplementationPhase
from phases.planning import PlanningPhase
from phases.pr_creation import PrCreationPhase
from phases.review import ReviewPhase

PIPELINE: tuple[Phase, ...] = (
    ClassificationPhase(),
    DorPhase(),
    ComprehensionPhase(),
    PlanningPhase(),
    E2eWritingPhase(),
    ImplementationPhase(),
    ReviewPhase(),
    PrCreationPhase(),
)

__all__ = [
    "PIPELINE",
    "ClassificationPhase",
    "ComprehensionPhase",
    "DorPhase",
    "E2eWritingPhase",
    "ImplementationPhase",
    "Phase",
    "PhaseContext",
    "PhaseOutcome",
    "PlanningPhase",
    "PrCreationPhase",
    "ReviewPhase",
]
