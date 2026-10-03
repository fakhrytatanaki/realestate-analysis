"""The rule engine: walks rule graphs over URLs and archived documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from realestate.domain.archive import ArchivedDocument
from realestate.domain.enums import NodeKind, PageKind, RouteDecision
from realestate.domain.ports.rules import RuleEngine
from realestate.domain.rules import (
    CandidateReport,
    ExtractionOutcome,
    RouteOutcome,
    RuleGraph,
    RuleNode,
)
from realestate.infrastructure.extraction import prompt_view
from realestate.infrastructure.extraction.conditions import (
    CONDITION_TYPES,
    EvalContext,
    check,
    evaluate,
    identifying_cues,
)
from realestate.infrastructure.extraction.document import ParsedDocument, SelectorError
from realestate.infrastructure.extraction.fingerprint import (
    fingerprint_distance,
    structural_fingerprint,
)
from realestate.infrastructure.extraction.template import TemplateError, run_template

#: Page-kind marker on a route action meaning "infer it from link evidence".
FROM_EVIDENCE = "FROM_EVIDENCE"


class HtmlRuleEngine(RuleEngine):
    """Interprets graphs with selectolax, ``re``, embedded JSON and spaCy."""

    def route(self, graph: RuleGraph, url: str, evidence: Mapping[str, Any]) -> RouteOutcome | None:
        ctx = EvalContext(url=url, evidence=evidence)
        for node, _ in graph.candidates(lambda condition: evaluate(condition, ctx)):
            if node.kind is not NodeKind.ROUTE:
                continue
            outcome = _route_outcome(node, evidence)
            if outcome is not None:
                return outcome
        return None

    def extract(
        self, graph: RuleGraph, document: ArchivedDocument, *, country_code: str | None
    ) -> ExtractionOutcome | None:
        parsed = ParsedDocument(document)
        ctx = EvalContext(url=document.url, document=parsed, captured_at=document.captured_at)
        for node, path in graph.candidates(lambda condition: evaluate(condition, ctx)):
            if node.kind is not NodeKind.TEMPLATE:
                continue
            outcome = self._run(graph, node, parsed, country_code=country_code, path=path)
            if outcome is not None and outcome.succeeded:
                return outcome
            # Matched but produced nothing usable: fall through to the next
            # candidate, so a too-generic earlier rule cannot shadow a better one.
        return None

    def evaluate_candidate(
        self,
        graph: RuleGraph,
        *,
        node: RuleNode,
        condition: Mapping[str, Any],
        document: ArchivedDocument,
        document_ref: str,
        country_code: str | None,
    ) -> CandidateReport:
        parsed = ParsedDocument(document)
        ctx = EvalContext(url=document.url, document=parsed, captured_at=document.captured_at)
        try:
            matched = check(condition, ctx)
        except Exception as exc:
            return CandidateReport(document_ref, False, None, error=f"condition error: {exc}")
        try:
            outcome = _execute(graph, node, parsed, country_code=country_code, path=[node.key])
        except (TemplateError, SelectorError, ValueError) as exc:
            return CandidateReport(document_ref, matched, None, error=f"template error: {exc}")
        return CandidateReport(document_ref, matched, outcome)

    def check_condition(self, condition: Mapping[str, Any]) -> list[str]:
        problems: list[str] = []
        _check_tree(condition, problems)
        if not problems and not identifying_cues(condition):
            problems.append(
                "conditions do not identify a page design: add a dom_css with a distinctive "
                "selector, a text_regex or a json_path"
            )
        return problems

    def fingerprint(self, document: ArchivedDocument) -> str:
        return structural_fingerprint(ParsedDocument(document))

    def fingerprint_distance(self, left: str, right: str) -> int:
        return fingerprint_distance(left, right)

    def prompt_view(self, document: ArchivedDocument, *, budget_chars: int) -> str:
        return prompt_view.render(ParsedDocument(document), budget_chars=budget_chars)

    def _run(
        self,
        graph: RuleGraph,
        node: RuleNode,
        parsed: ParsedDocument,
        *,
        country_code: str | None,
        path: list[str],
    ) -> ExtractionOutcome | None:
        try:
            return _execute(graph, node, parsed, country_code=country_code, path=path)
        except (TemplateError, SelectorError, ValueError):
            return None


def _execute(
    graph: RuleGraph,
    node: RuleNode,
    parsed: ParsedDocument,
    *,
    country_code: str | None,
    path: list[str],
) -> ExtractionOutcome:
    result = run_template(
        node.action,
        parsed,
        vocab=graph.vocab,
        template_key=node.key,
        graph_version=graph.version,
        country_code=country_code,
    )
    return ExtractionOutcome(
        template_key=node.key,
        page_kind=result.page_kind,
        drafts=result.drafts,
        links=result.links if result.page_kind is not PageKind.OTHER else [],
        items_total=result.items_total,
        items_valid=len(result.drafts),
        problems=result.problems,
        vocab_misses=result.vocab_misses,
        path=path,
    )


def _route_outcome(node: RuleNode, evidence: Mapping[str, Any]) -> RouteOutcome | None:
    action = node.action
    try:
        decision = RouteDecision(str(action.get("decision", "")).upper())
    except ValueError:
        return None
    raw_kind = str(action.get("page_kind") or "").upper()
    page_kind: PageKind | None
    if raw_kind == FROM_EVIDENCE:
        rels = evidence.get("linked_as") or []
        page_kind = PageKind.DETAIL if "DETAIL" in rels else PageKind.LIST
    else:
        try:
            page_kind = PageKind(raw_kind) if raw_kind else None
        except ValueError:
            page_kind = None
    try:
        priority = max(0, min(100, int(action.get("priority", 50))))
    except (TypeError, ValueError):
        priority = 50
    return RouteOutcome(
        decision=decision, node_key=node.key, priority=priority, page_kind=page_kind
    )


_PROBE = ParsedDocument(
    ArchivedDocument(b"<html><body><p>probe</p></body></html>", "http://example.invalid/")
)


def _check_tree(condition: Mapping[str, Any], problems: list[str]) -> None:
    kind = condition.get("type") if isinstance(condition, Mapping) else None
    if kind not in CONDITION_TYPES:
        problems.append(f"unknown condition type {kind!r}; use one of {sorted(CONDITION_TYPES)}")
        return
    if kind in ("all", "any"):
        children = condition.get("conditions")
        if not isinstance(children, list) or not children:
            problems.append(f"'{kind}' needs a non-empty 'conditions' list")
            return
        for child in children:
            _check_tree(child, problems)
        return
    if kind == "not":
        child = condition.get("condition")
        if not isinstance(child, Mapping):
            problems.append("'not' needs a 'condition' object")
        else:
            _check_tree(child, problems)
        return
    # A tiny real document, so selectors and regexes are actually compiled.
    probe = EvalContext(url="http://example.invalid/", document=_PROBE)
    try:
        check(condition, probe)
    except KeyError as exc:
        problems.append(f"{kind} condition is missing {exc}")
    except Exception as exc:
        problems.append(f"{kind} condition is invalid: {exc}")
