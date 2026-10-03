"""Ports for rule graphs, their gaps, the LLM ledger, and the rule engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from realestate.domain.archive import ArchivedDocument, IdentityPolicy
from realestate.domain.enums import GapStatus, RuleDomain
from realestate.domain.rules import (
    CandidateReport,
    ExtractionOutcome,
    LlmDecision,
    RouteOutcome,
    RuleGap,
    RuleGraph,
    RuleNode,
)


class RuleGraphRepository(ABC):
    """Immutable, versioned rule graphs."""

    @abstractmethod
    async def active(self, source_key: str, domain: RuleDomain) -> RuleGraph | None:
        """The version currently in force, or ``None`` if there is none yet."""

    @abstractmethod
    async def get(self, source_key: str, domain: RuleDomain, version: int) -> RuleGraph | None:
        """One specific version."""

    @abstractmethod
    async def save_version(self, graph: RuleGraph) -> RuleGraph:
        """Persist ``graph`` as the new active version, retiring the previous one."""

    @abstractmethod
    async def versions(self, source_key: str, domain: RuleDomain) -> list[RuleGraph]:
        """Every version, newest first."""


class RuleSeedProvider(ABC):
    """Load and validate reviewed initial graphs without performing persistence."""

    @abstractmethod
    def load(self, source_key: str) -> Sequence[RuleGraph]:
        """Both decision domains, or raise ValueError for unsupported/invalid seeds."""


class RuleGapRepository(ABC):
    """Clusters of unhandled inputs awaiting induction."""

    @abstractmethod
    async def reset_counts(self, source_key: str, domain: RuleDomain) -> None:
        """Zero the occurrence counts of unresolved gaps before a fresh recount.

        Covers ``OPEN``, ``FAILED`` and ``NEEDS_HUMAN``, so every count is the
        current backlog rather than a sum over collections.
        """

    @abstractmethod
    async def record(
        self,
        source_key: str,
        domain: RuleDomain,
        fingerprint: str,
        *,
        samples: Sequence[str],
        occurrences: int = 1,
        max_samples: int = 8,
    ) -> RuleGap:
        """Add occurrences and samples to a gap, creating or reopening it.

        New samples come first, so a gap shows its current inputs rather than
        the ones it was created with. ``RESOLVED`` gaps reopen. ``FAILED`` and
        ``NEEDS_HUMAN`` gaps keep counting without reopening, except that a
        ``FAILED`` gap reopens once it outgrows its size at failure
        (:func:`~realestate.domain.rules.failed_gap_outgrown`).
        """

    @abstractmethod
    async def list(
        self,
        source_key: str,
        domain: RuleDomain | None = None,
        *,
        status: GapStatus | None = None,
        limit: int = 100,
    ) -> list[RuleGap]:
        """Gaps, most frequent first."""

    @abstractmethod
    async def mark_resolved(self, gap_id: UUID, *, version: int) -> None: ...

    @abstractmethod
    async def record_failure(
        self, gap_id: UUID, error: str, *, max_attempts: int, attempted_with: str | None = None
    ) -> RuleGap:
        """Count a failed induction; the gap becomes ``FAILED`` at ``max_attempts``.

        ``attempted_with`` (``model|prompt version``) is kept so that a later
        model or prompt can be given another try (:meth:`reopen_superseded`).
        """

    @abstractmethod
    async def mark_needs_human(self, gap_id: UUID, reason: str) -> None:
        """Park a gap induction cannot resolve (a curated rule wins on its inputs)."""

    @abstractmethod
    async def reopen_failed(self, source_key: str, domain: RuleDomain | None = None) -> int:
        """Give ``FAILED`` and ``NEEDS_HUMAN`` gaps a fresh set of attempts (manual)."""

    @abstractmethod
    async def reopen_superseded(
        self, source_key: str, domain: RuleDomain, *, attempted_with: str
    ) -> int:
        """Reopen ``FAILED`` gaps last attempted with a different model or prompt."""


class LlmDecisionRepository(ABC):
    """Every model answer, valid or not: cache, audit trail and cost ledger."""

    @abstractmethod
    async def find_valid(
        self, task: str, input_fp: str, model: str, prompt_version: str
    ) -> LlmDecision | None:
        """A previous valid answer to exactly this prompt, if any."""

    @abstractmethod
    async def record(
        self,
        *,
        task: str,
        input_fp: str,
        model: str,
        prompt_version: str,
        request: Mapping[str, Any],
        response: Mapping[str, Any] | None,
        raw_text: str,
        valid: bool,
        error: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        source_key: str | None = None,
    ) -> LlmDecision: ...

    @abstractmethod
    async def mark_valid(self, decision_id: UUID, valid: bool, error: str | None = None) -> None:
        """Update the verdict once the answer has been validated."""

    @abstractmethod
    async def totals(
        self, task_prefix: str | None = None, *, source_key: str | None = None
    ) -> dict[str, int]:
        """``{"calls", "valid", "tokens_in", "tokens_out"}`` for reporting."""


class RuleEngine(ABC):
    """Interprets rule graphs against URLs and archived documents.

    Synchronous on purpose: evaluation is CPU-bound and side-effect free.
    """

    @abstractmethod
    def route(self, graph: RuleGraph, url: str, evidence: Mapping[str, Any]) -> RouteOutcome | None:
        """Walk the navigation graph for one URL; ``None`` is a miss."""

    @abstractmethod
    def extract(
        self,
        graph: RuleGraph,
        document: ArchivedDocument,
        *,
        country_code: str | None,
        identity: IdentityPolicy | None = None,
    ) -> ExtractionOutcome | None:
        """Walk the extraction graph for one document; ``None`` is a miss.

        ``identity``, when given, derives external ids from advert URLs.
        """

    def explain_miss(
        self, graph: RuleGraph, document: ArchivedDocument, *, country_code: str | None
    ) -> list[str]:
        """Bounded non-contact diagnostics for a miss; engines may provide none."""
        return []

    def advert_links(self, document: ArchivedDocument, *, identity: IdentityPolicy | None) -> int:
        """Distinct adverts the page links to, other than itself (0 without a policy).

        Evidence against calling a page "without adverts": only the source's
        identity policy can tell an advert link from any other.
        """
        return 0

    @abstractmethod
    def evaluate_candidate(
        self,
        graph: RuleGraph,
        *,
        node: RuleNode,
        condition: Mapping[str, Any],
        document: ArchivedDocument,
        document_ref: str,
        country_code: str | None,
        identity: IdentityPolicy | None = None,
    ) -> CandidateReport:
        """Try one proposed template on one document, outside the graph."""

    @abstractmethod
    def check_condition(self, condition: Mapping[str, Any]) -> list[str]:
        """Problems with a proposed condition: unknown types, bad regexes or
        selectors, or nothing that identifies a page design. Empty means usable."""

    @abstractmethod
    def fingerprint(self, document: ArchivedDocument) -> str:
        """A structural fingerprint: similar templates give nearby values."""

    @abstractmethod
    def fingerprint_distance(self, left: str, right: str) -> int:
        """How different two fingerprints are; small means same template."""

    @abstractmethod
    def prompt_view(self, document: ArchivedDocument, *, budget_chars: int) -> str:
        """A compact, model-readable rendering of the document's structure."""
