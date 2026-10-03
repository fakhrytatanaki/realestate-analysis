"""Rule induction: turning misses into rules with an LLM.

This is the "fall back to the LLM" half of the state machine. Inputs no rule
handled are clustered into gaps (URL shapes for navigation, structural
fingerprints for extraction); each gap becomes one question to the model; the
answer is validated against real samples and, if it holds up, compiled into new
graph states in a new graph version. Next time, the same kind of input is a
rule hit and costs nothing.

The model never touches a value that ends up in a listing: it proposes
selectors, regexes and vocabulary, and deterministic code does the rest.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from realestate.application.rules import prompts
from realestate.application.rules.proposals import NavRuleBatch, NavRuleProposal, TemplateProposal
from realestate.application.rules.seeds import seed_navigation_graph
from realestate.domain.archive import ArchivedDocument, url_shape
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    NodeKind,
    RawDocumentStatus,
    RuleDomain,
    RuleOrigin,
)
from realestate.domain.exceptions import LlmError
from realestate.domain.ports.archive import CrawlFrontierRepository
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.domain.ports.llm import LlmMessage, StructuredLlm
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import RawDocumentRepository
from realestate.domain.ports.rules import (
    LlmDecisionRepository,
    RuleEngine,
    RuleGapRepository,
    RuleGraphRepository,
)
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGap, RuleGraph, RuleNode


@dataclass(frozen=True, slots=True)
class InductionSettings:
    nav_batch_size: int = 12
    nav_samples_per_gap: int = 8
    nav_url_scan_limit: int = 50_000
    max_repairs: int = 2
    max_attempts: int = 3
    prompt_budget_chars: int = 24_000
    #: Fingerprints within this many bits are one page design (see fingerprint.py).
    cluster_distance: int = 10
    extraction_samples: int = 4
    extraction_scan_limit: int = 500
    min_condition_ratio: float = 0.6
    min_valid_item_ratio: float = 0.6


@dataclass(slots=True)
class InductionReport:
    llm_calls: int = 0
    cache_hits: int = 0
    accepted: int = 0
    rejected: int = 0
    versions: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def merge(self, other: InductionReport) -> None:
        self.llm_calls += other.llm_calls
        self.cache_hits += other.cache_hits
        self.accepted += other.accepted
        self.rejected += other.rejected
        self.versions += other.versions
        self.notes += other.notes


@dataclass(slots=True)
class _Sample:
    ref: str
    document: ArchivedDocument


@dataclass(slots=True)
class _Answer:
    data: dict[str, Any] | None
    decision_id: UUID | None
    cached: bool
    raw_text: str


class RuleInductionService:
    def __init__(
        self,
        *,
        graphs: RuleGraphRepository,
        gaps: RuleGapRepository,
        decisions: LlmDecisionRepository,
        llm: StructuredLlm,
        engine: RuleEngine,
        documents: RawDocumentRepository,
        blob: BlobProvider,
        frontier: CrawlFrontierRepository,
        log: LogProvider,
        settings: InductionSettings | None = None,
    ) -> None:
        self._graphs = graphs
        self._gaps = gaps
        self._decisions = decisions
        self._llm = llm
        self._engine = engine
        self._documents = documents
        self._blob = blob
        self._frontier = frontier
        self._log = log
        self._settings = settings or InductionSettings()

    # -- gap collection ---------------------------------------------------

    async def collect_navigation_gaps(self, source_key: str) -> int:
        """Group unrouted URLs by shape; returns the number of open gaps."""
        await self._gaps.reset_open(source_key, RuleDomain.NAVIGATION)
        urls = await self._frontier.sample_urls(
            source_key, CrawlStatus.UNROUTED, limit=self._settings.nav_url_scan_limit
        )
        by_shape: dict[str, list[str]] = defaultdict(list)
        for url in urls:
            by_shape[url_shape(url)].append(url)
        for shape, members in by_shape.items():
            await self._gaps.record(
                source_key,
                RuleDomain.NAVIGATION,
                shape,
                samples=_spread(members, self._settings.nav_samples_per_gap),
                occurrences=len(members),
                max_samples=self._settings.nav_samples_per_gap,
            )
        largest = sorted(by_shape.items(), key=lambda item: -len(item[1]))[:5]
        await self._log.info(
            "navigation gaps collected",
            source_key=source_key,
            unrouted_urls=len(urls),
            shapes=len(by_shape),
            largest=", ".join(f"{shape} x{len(members)}" for shape, members in largest) or "-",
        )
        return len(by_shape)

    async def collect_extraction_gaps(self, source_key: str) -> int:
        """Cluster unrecognised documents by page design; returns open gaps touched."""
        await self._gaps.reset_open(source_key, RuleDomain.EXTRACTION)
        documents = await self._documents.list_by_status(
            source_key=source_key,
            status=RawDocumentStatus.UNRECOGNISED,
            limit=self._settings.extraction_scan_limit,
        )
        known = [
            gap.fingerprint
            for gap in await self._gaps.list(source_key, RuleDomain.EXTRACTION, limit=5000)
        ]
        touched: set[str] = set()
        for raw in documents:
            try:
                content = await self._blob.get(raw.blob_key)
            except Exception as exc:
                await self._log.warning(
                    "cannot read archived document", document_id=str(raw.id), error=str(exc)
                )
                continue
            document = ArchivedDocument.from_payload(
                content, content_type=raw.content_type, source_url=raw.source_url, meta=raw.meta
            )
            fingerprint = self._engine.fingerprint(document)
            nearest = min(
                known,
                key=lambda other: self._engine.fingerprint_distance(fingerprint, other),
                default=None,
            )
            if (
                nearest is not None
                and self._engine.fingerprint_distance(fingerprint, nearest)
                <= self._settings.cluster_distance
            ):
                fingerprint = nearest
            else:
                known.append(fingerprint)
            await self._gaps.record(
                source_key,
                RuleDomain.EXTRACTION,
                fingerprint,
                samples=[str(raw.id)],
                max_samples=self._settings.extraction_samples,
            )
            touched.add(fingerprint)
        await self._log.info(
            "extraction gaps collected",
            source_key=source_key,
            unrecognised_documents=len(documents),
            page_designs=len(touched),
        )
        return len(touched)

    # -- induction --------------------------------------------------------

    async def induce(
        self,
        source_key: str,
        *,
        domain: RuleDomain | None = None,
        max_calls: int = 10,
        domain_name: str = "",
    ) -> InductionReport:
        """Ask the model about the most frequent open gaps, within ``max_calls``."""
        report = InductionReport()
        if domain in (None, RuleDomain.NAVIGATION):
            report.merge(await self._induce_navigation(source_key, max_calls, domain_name))
        remaining = max_calls - report.llm_calls
        if domain in (None, RuleDomain.EXTRACTION) and remaining > 0:
            report.merge(await self._induce_extraction(source_key, remaining))
        return report

    async def _induce_navigation(
        self, source_key: str, budget: int, domain_name: str
    ) -> InductionReport:
        report = InductionReport()
        attempted: set[UUID] = set()  # ask about each gap at most once per call
        while report.llm_calls < budget:
            candidates = await self._gaps.list(
                source_key,
                RuleDomain.NAVIGATION,
                status=GapStatus.OPEN,
                limit=self._settings.nav_batch_size * 20,
            )
            open_gaps = [
                gap for gap in candidates if gap.occurrences > 0 and gap.id not in attempted
            ][: self._settings.nav_batch_size]
            if not open_gaps:
                break
            attempted.update(gap.id for gap in open_gaps)
            graph = await self._graphs.active(
                source_key, RuleDomain.NAVIGATION
            ) or seed_navigation_graph(source_key)
            messages = [
                LlmMessage("system", prompts.NAV_SYSTEM),
                LlmMessage(
                    "user",
                    prompts.nav_user_prompt(
                        domain_name or source_key,
                        open_gaps,
                        graph,
                        samples=self._settings.nav_samples_per_gap,
                    ),
                ),
            ]
            await self._log.info(
                "asking llm for navigation rules",
                groups=len(open_gaps),
                urls=sum(gap.occurrences for gap in open_gaps),
                shapes=", ".join(gap.fingerprint for gap in open_gaps[:4])
                + (" ..." if len(open_gaps) > 4 else ""),
            )
            try:
                answer = await self._ask(
                    "nav_rules",
                    prompts.NAV_PROMPT_VERSION,
                    messages,
                    prompts.NAV_SCHEMA,
                    prompts.NAV_TOOL,
                    prompts.NAV_TOOL_DESCRIPTION,
                )
            except LlmError as exc:
                report.notes.append(f"navigation: model unavailable: {exc}")
                break
            report.llm_calls += 0 if answer.cached else 1
            report.cache_hits += 1 if answer.cached else 0

            accepted, problems = self._validate_nav(answer.data, open_gaps)
            await self._settle(answer, valid=bool(accepted), error="; ".join(problems) or None)
            covered = {index for index, _ in accepted}
            if accepted:
                nodes, edges = [], []
                priority = graph.next_edge_priority()
                for offset, (index, rule) in enumerate(accepted):
                    key = f"nav.v{graph.version + 1}.{offset + 1}"
                    nodes.append(
                        RuleNode(
                            key=key,
                            kind=NodeKind.ROUTE,
                            action={
                                "decision": rule.decision,
                                "page_kind": rule.page_kind,
                                "priority": rule.priority,
                                "reason": rule.reason[:300],
                                "gap": open_gaps[index].fingerprint,
                            },
                            origin=RuleOrigin.LLM,
                            llm_decision_id=answer.decision_id,
                        )
                    )
                    edges.append(
                        RuleEdge(
                            from_key=ROOT_KEY,
                            to_key=key,
                            condition={"type": "url_regex", "pattern": rule.pattern},
                            priority=priority + offset,
                        )
                    )
                saved = await self._graphs.save_version(
                    graph.extended(
                        nodes=nodes, edges=edges, notes=f"llm: {len(nodes)} navigation rules"
                    )
                )
                report.versions.append(saved.version)
                report.accepted += len(nodes)
                for index in covered:
                    await self._gaps.mark_resolved(open_gaps[index].id, version=saved.version)
            for index, gap in enumerate(open_gaps):
                if index not in covered:
                    report.rejected += 1
                    reason = "; ".join(problems[-3:]) or "no valid rule proposed for this group"
                    await self._gaps.record_failure(
                        gap.id, reason, max_attempts=self._settings.max_attempts
                    )
            for index, rule in accepted:
                await self._log.info(
                    "navigation rule accepted",
                    group=index + 1,
                    decision=rule.decision,
                    page_kind=rule.page_kind,
                    pattern=rule.pattern,
                )
            for problem in problems[:10]:
                await self._log.info("navigation proposal rejected", problem=problem[:300])
            await self._log.info(
                "navigation induction round",
                gaps=len(open_gaps),
                covered=len(covered),
                accepted=len(accepted),
                problems=len(problems),
            )
        return report

    def _validate_nav(
        self, data: dict[str, Any] | None, gaps: Sequence[RuleGap]
    ) -> tuple[list[tuple[int, NavRuleProposal]], list[str]]:
        if data is None:
            return [], ["reply contained no JSON object"]
        raw_rules = data.get("rules") if isinstance(data, dict) else None
        if isinstance(data, dict) and raw_rules is None and "pattern" in data:
            raw_rules = [data]
        problems: list[str] = []
        all_samples = [url for gap in gaps for url in gap.samples]
        by_group: dict[int, list[tuple[int, NavRuleProposal, re.Pattern[str]]]] = defaultdict(list)
        for order, raw in enumerate(raw_rules or []):
            try:
                rule = NavRuleBatch(rules=[raw]).rules[0]
            except ValidationError as exc:
                problems.append(f"invalid rule {raw!r}: {exc.errors()[0]['msg']}")
                continue
            index = rule.group - 1
            if not 0 <= index < len(gaps):
                problems.append(f"rule for unknown group {rule.group}")
                continue
            pattern = re.compile(rule.pattern, re.IGNORECASE)
            own = gaps[index].samples
            hits = sum(1 for url in own if pattern.search(url))
            if hits == 0:
                problems.append(
                    f"group {rule.group}: pattern {rule.pattern!r} matches 0/{len(own)} samples"
                )
                continue
            if len(gaps) >= 4 and all_samples:
                share = sum(1 for url in all_samples if pattern.search(url)) / len(all_samples)
                if share >= 0.9:
                    problems.append(
                        f"group {rule.group}: pattern {rule.pattern!r} matches nearly every URL"
                    )
                    continue
            by_group[index].append((order, rule, pattern))

        # A group may mix kinds of pages, so several partial rules are fine --
        # but together they must cover most of the group.
        kept: list[tuple[int, int, NavRuleProposal]] = []
        for index, rules in by_group.items():
            own = gaps[index].samples
            covered = sum(1 for url in own if any(p.search(url) for _, _, p in rules))
            if covered / len(own) < 0.5:
                problems.append(
                    f"group {index + 1}: rules together match only {covered}/{len(own)} samples"
                )
                continue
            kept += [(order, index, rule) for order, rule, _ in rules]
        # The model was told rules apply in the order listed; keep that order.
        accepted = [(index, rule) for _, index, rule in sorted(kept, key=lambda item: item[0])]
        return accepted, problems

    async def _induce_extraction(self, source_key: str, budget: int) -> InductionReport:
        report = InductionReport()
        open_gaps = await self._gaps.list(
            source_key, RuleDomain.EXTRACTION, status=GapStatus.OPEN, limit=50
        )
        for gap in open_gaps:
            if report.llm_calls >= budget:
                break
            if gap.occurrences <= 0:
                continue
            samples = await self._load_samples(gap)
            if not samples:
                await self._gaps.record_failure(
                    gap.id, "sample documents unavailable", max_attempts=1
                )
                continue
            graph = await self._graphs.active(source_key, RuleDomain.EXTRACTION) or RuleGraph.empty(
                source_key, RuleDomain.EXTRACTION
            )
            outcome = await self._induce_template(graph, gap, samples, budget - report.llm_calls)
            report.merge(outcome)
        return report

    async def _induce_template(
        self, graph: RuleGraph, gap: RuleGap, samples: list[_Sample], budget: int
    ) -> InductionReport:
        report = InductionReport()
        primary = samples[0].document
        captured = primary.captured_at.date().isoformat() if primary.captured_at else "unknown"
        messages = [
            LlmMessage("system", prompts.TEMPLATE_SYSTEM),
            LlmMessage(
                "user",
                prompts.template_user_prompt(
                    url=primary.url,
                    captured=captured,
                    page_view=self._engine.prompt_view(
                        primary, budget_chars=self._settings.prompt_budget_chars
                    ),
                    vocab=graph.vocab,
                ),
            ),
        ]
        errors: list[str] = []
        for attempt in range(1, 2 + self._settings.max_repairs):
            if report.llm_calls >= budget:
                errors.append("LLM call budget exhausted")
                break
            await self._log.info(
                "asking llm for an extraction template",
                gap=gap.fingerprint,
                attempt=attempt,
                pages=gap.occurrences,
                samples=len(samples),
                url=primary.url,
                captured=captured,
            )
            try:
                answer = await self._ask(
                    "extraction_template",
                    prompts.TEMPLATE_PROMPT_VERSION,
                    messages,
                    prompts.TEMPLATE_SCHEMA,
                    prompts.TEMPLATE_TOOL,
                    prompts.TEMPLATE_TOOL_DESCRIPTION,
                )
            except LlmError as exc:
                errors = [f"model unavailable: {exc}"]
                report.notes.append(f"extraction: {errors[0]}")
                return report
            report.llm_calls += 0 if answer.cached else 1
            report.cache_hits += 1 if answer.cached else 0

            proposal, errors = self._parse_template(answer.data)
            if proposal is not None:
                errors = self._validate_template(graph, proposal, samples)
            await self._settle(answer, valid=not errors, error="; ".join(errors) or None)
            if proposal is not None and not errors:
                key = _template_key(graph, proposal.name)
                saved = await self._graphs.save_version(
                    graph.extended(
                        nodes=[
                            RuleNode(
                                key=key,
                                kind=NodeKind.TEMPLATE,
                                action=proposal.action(),
                                origin=RuleOrigin.LLM,
                                llm_decision_id=answer.decision_id,
                            )
                        ],
                        edges=[
                            RuleEdge(
                                from_key=ROOT_KEY,
                                to_key=key,
                                condition=proposal.condition(),
                                priority=graph.next_edge_priority(),
                            )
                        ],
                        vocab=proposal.vocab_dict(),
                        notes=f"llm: template {key} for gap {gap.fingerprint}",
                    )
                )
                await self._gaps.mark_resolved(gap.id, version=saved.version)
                report.accepted += 1
                report.versions.append(saved.version)
                await self._log.info(
                    "template accepted",
                    template=key,
                    version=saved.version,
                    page_kind=proposal.page_kind,
                )
                return report
            await self._log.info(
                "template attempt rejected",
                gap=gap.fingerprint,
                attempt=attempt,
                errors=" | ".join(error[:200] for error in errors[:4]),
            )
            messages = [
                *messages,
                LlmMessage(
                    "assistant",
                    json.dumps(answer.data, ensure_ascii=False)
                    if answer.data
                    else answer.raw_text[:4000],
                ),
                LlmMessage("user", prompts.template_feedback(errors)),
            ]
        report.rejected += 1
        await self._gaps.record_failure(
            gap.id, "; ".join(errors)[:2000], max_attempts=self._settings.max_attempts
        )
        await self._log.warning("template rejected", gap=gap.fingerprint, errors=errors[:5])
        return report

    def _parse_template(
        self, data: dict[str, Any] | None
    ) -> tuple[TemplateProposal | None, list[str]]:
        if data is None:
            return None, ["the reply contained no JSON object; call the tool with the template"]
        if "template" in data and isinstance(data["template"], dict):
            data = data["template"]
        try:
            return TemplateProposal.model_validate(data), []
        except ValidationError as exc:
            return None, [
                f"{'.'.join(str(part) for part in error['loc']) or 'template'}: {error['msg']}"
                for error in exc.errors()[:8]
            ]

    def _validate_template(
        self, graph: RuleGraph, proposal: TemplateProposal, samples: Sequence[_Sample]
    ) -> list[str]:
        """Try the proposal on real pages; empty list means accepted."""
        condition = proposal.condition()
        errors = self._engine.check_condition(condition)
        if errors:
            return errors
        if proposal.page_kind != "OTHER" and "title" not in proposal.fields:
            return ["fields must include 'title'"]
        if proposal.page_kind == "LIST" and not proposal.items:
            return ["a LIST template needs 'items'"]

        node = RuleNode(key="candidate", kind=NodeKind.TEMPLATE, action=proposal.action())
        # Vocabulary proposed with the template is part of what is being tested.
        trial = graph.extended(vocab=proposal.vocab_dict()) if proposal.vocab else graph
        reports = [
            self._engine.evaluate_candidate(
                trial,
                node=node,
                condition=condition,
                document=sample.document,
                document_ref=sample.ref,
                country_code=None,
            )
            for sample in samples
        ]
        primary = reports[0]
        if primary.error:
            return [primary.error]
        if not primary.condition_matched:
            return [
                "the conditions do not all hold on the page shown; "
                "check that each selector/pattern exists there"
            ]
        matched = [report for report in reports if report.condition_matched]
        if len(matched) / len(reports) < self._settings.min_condition_ratio:
            return [
                f"the conditions held on only {len(matched)} of {len(reports)} pages "
                "of this design; "
                "use cues common to the design rather than to one page"
            ]
        if proposal.page_kind == "OTHER":
            return []
        outcome = primary.outcome
        assert outcome is not None
        problems: list[str] = []
        if proposal.page_kind == "LIST" and outcome.items_total < 2:
            problems.append(
                f"items selector found {outcome.items_total} element(s); "
                "it must match every advert in the list"
            )
        elif (
            outcome.items_total
            and outcome.items_valid / outcome.items_total < self._settings.min_valid_item_ratio
        ):
            problems.append(
                f"only {outcome.items_valid} of {outcome.items_total} items produced a valid advert"
            )
        if outcome.vocab_misses:
            examples = ", ".join(
                repr(text[:60]) for text in list(dict.fromkeys(outcome.vocab_misses))[:4]
            )
            problems.append(
                f"no listing_type vocab pattern matched texts such as {examples}; add vocab "
                "patterns (SALE/RENT) matching this wording, or a listing_type field"
            )
        problems += [problem for problem in outcome.problems[:4] if "listing type" not in problem]
        # A field empty on every advert of every sample is a wrong selector or
        # regex (e.g. "m2" where the page says "Square Meters: 170").
        # Needs real evidence (2+ pages, 10+ adverts): one detail page without
        # bedrooms is not proof the bedrooms field is wrong.
        outcomes = [r.outcome for r in matched if r.outcome is not None and r.outcome.items_total]
        never_filled = set(outcome.empty_fields)
        for other_outcome in outcomes[1:]:
            never_filled &= set(other_outcome.empty_fields)
        if len(outcomes) < 2 or sum(o.items_total for o in outcomes) < 10:
            never_filled = set()
        for name in sorted(never_filled - {"currency"}):
            problems.append(
                f"field '{name}' produced no value on any advert of the {len(outcomes)} sample "
                "page(s); fix its selector/regex to match the page text, or drop the field"
            )
        if not problems and outcome.items_valid == 0:
            problems.append("no valid adverts were extracted")
        if not problems:
            drafts = outcome.drafts
            if (
                drafts
                and all(draft.price.amount is None for draft in drafts)
                and "price" in proposal.fields
            ):
                problems.append(
                    "the price field never produced a number; point it at the raw price text"
                )
            for other in matched[1:]:
                if other.outcome is None or not other.outcome.succeeded:
                    problems.append(
                        "the template found no valid adverts on another page of this "
                        f"design ({other.document_ref})"
                    )
                    break
        return problems

    async def _load_samples(self, gap: RuleGap) -> list[_Sample]:
        samples: list[_Sample] = []
        for ref in gap.samples[: self._settings.extraction_samples]:
            try:
                raw = await self._documents.get(UUID(ref))
                if raw is None:
                    continue
                content = await self._blob.get(raw.blob_key)
            except Exception:
                continue
            samples.append(
                _Sample(
                    ref=ref,
                    document=ArchivedDocument.from_payload(
                        content,
                        content_type=raw.content_type,
                        source_url=raw.source_url,
                        meta=raw.meta,
                    ),
                )
            )
        return samples

    # -- the model, with its ledger ---------------------------------------

    async def _ask(
        self,
        task: str,
        prompt_version: str,
        messages: Sequence[LlmMessage],
        schema: dict[str, Any],
        tool_name: str,
        tool_description: str,
    ) -> _Answer:
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "model": self._llm.model,
                    "version": prompt_version,
                    "messages": [(m.role, m.content) for m in messages],
                    "schema": schema,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        cached = await self._decisions.find_valid(
            task, fingerprint, self._llm.model, prompt_version
        )
        if cached is not None and cached.response is not None:
            await self._log.info("llm answer reused from the decision ledger", task=task)
            return _Answer(dict(cached.response), cached.id, True, cached.raw_text)
        await self._log.debug(
            "llm request",
            task=task,
            model=self._llm.model,
            prompt_chars=sum(len(m.content) for m in messages),
            messages=len(messages),
        )
        response = await self._llm.complete(
            messages, schema=schema, tool_name=tool_name, tool_description=tool_description
        )
        decision = await self._decisions.record(
            task=task,
            input_fp=fingerprint,
            model=response.model,
            prompt_version=prompt_version,
            request={"messages": [{"role": m.role, "chars": len(m.content)} for m in messages]},
            response=response.data,
            raw_text=response.raw_text,
            valid=False,  # settled after validation
            error=response.error,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            latency_ms=response.latency_ms,
        )
        await self._log.info(
            "llm call",
            task=task,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            latency_ms=response.latency_ms,
            parsed=response.data is not None,
            decision_id=str(decision.id),
        )
        await self._log.debug("llm reply", task=task, reply=response.raw_text[:2000])
        return _Answer(response.data, decision.id, False, response.raw_text)

    async def _settle(self, answer: _Answer, *, valid: bool, error: str | None) -> None:
        if answer.decision_id is not None and not answer.cached:
            await self._decisions.mark_valid(answer.decision_id, valid, error)


def _spread(items: Sequence[str], count: int) -> list[str]:
    """``count`` items spread evenly over ``items`` (they are in crawl order)."""
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(index * step)] for index in range(count)]


def _template_key(graph: RuleGraph, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60] or "template"
    return f"tpl.v{graph.version + 1}.{slug}"
