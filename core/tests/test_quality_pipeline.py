"""Data-quality machinery: audit, curated seeds, induction gates, provenance, coverage.

These close the gaps the wayback assessment measured: success statuses that
hid wrong data, rule upgrades that never reached parsed documents, and a crawl
that only ever saw the first year it enumerated.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from realestate import cli
from realestate.application.services.archive_audit_service import (
    ArchiveAuditService,
    completeness,
    diff_against_stored,
    problem_reason,
)
from realestate.application.services.archive_crawl_service import cursor_key, select_captures
from realestate.application.services.rule_induction_service import _Sample, _SourceContext
from realestate.application.services.rule_seed_service import RuleSeedService
from realestate.bootstrap import Container
from realestate.config.settings import Settings
from realestate.domain.archive import ArchivedDocument, Capture, FrontierEntry, surt_key
from realestate.domain.enums import (
    CrawlStatus,
    ListingType,
    NodeKind,
    RawDocumentKind,
    RawDocumentStatus,
    RuleDomain,
    RuleOrigin,
    RunTrigger,
)
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.gold import GoldDocument, GoldItem, score
from realestate.domain.models import RawPayload
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode
from realestate.infrastructure.gold.file_gold_set import FileGoldSet
from tests.archive_fakes import TEMPLATES, ScriptedLlm, fixture_document
from tests.conftest import NullLogProvider, make_draft
from tests.test_extraction_quality import DETAIL_2013, LOGO_DETAIL
from tests.test_rule_induction_and_crawl import SOURCE, Harness, captures, proposal

GOLD_ROOT = Path(__file__).resolve().parent / "fixtures" / "wayback_olx_eg" / "gold"


async def archive_bytes(
    harness: Harness,
    content: bytes,
    url: str,
    timestamp: str,
    *,
    status: RawDocumentStatus = RawDocumentStatus.PENDING,
) -> str:
    payload = RawPayload(
        content=content,
        kind=RawDocumentKind.HTML,
        content_type="text/html",
        source_url=url,
        meta={"timestamp": timestamp, "original_url": url, "page_kind_hint": "DETAIL"},
    )
    blob = await harness.blob.put(
        f"{SOURCE}/{surt_key(url).replace(')', '_').replace(',', '_').replace('/', '_')}"
        f"-{timestamp}.html",
        content,
        content_type="text/html",
    )
    document = await harness.documents.create(source_key=SOURCE, payload=payload, blob=blob)
    if status is RawDocumentStatus.UNRECOGNISED:
        await harness.documents.mark_unrecognised(document.id, "test")
    return str(document.id)


def detail_html(iid: str) -> bytes:
    return DETAIL_2013.replace("{iid}", iid).encode()


def detail_url(iid: str) -> str:
    return f"http://cairo.olx.com.eg:80/apartment-iid-{iid}"


def audit_service(harness: Harness, *, gold: Any = None) -> ArchiveAuditService:
    return ArchiveAuditService(
        registry=harness.registry,
        documents=harness.documents,  # type: ignore[arg-type]
        blob=harness.blob,
        engine=harness.engine,
        graphs=harness.graphs,
        listings=harness.listings,
        log=NullLogProvider(),
        gold=gold,
    )


def seeder(harness: Harness) -> RuleSeedService:
    return RuleSeedService(registry=harness.registry, graphs=harness.graphs, log=NullLogProvider())


async def test_curated_seed_cli_keeps_its_audited_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    container = Container(Settings())
    container.__dict__.update(
        registry=harness.registry,
        log=NullLogProvider(),
        seeder=seeder(harness),
        audit=audit_service(harness),
    )
    monkeypatch.setattr(cli, "Container", lambda _: container)
    monkeypatch.setattr(container, "init_db", AsyncMock())
    monkeypatch.setattr(container, "aclose", AsyncMock())
    args = cli.build_parser().parse_args(["rules", "seed", "--source", SOURCE, "--dry-run"])
    assert await cli.run(args) == 0
    output = capsys.readouterr().out
    assert "extraction graph v0 -> v1" in output and "seed-candidate" in output
    assert harness.graphs.saved == []
    assert "rule_seeds" not in container.__dict__
    assert harness.served == [] and harness.llm.calls == []


# -- curated seeds -------------------------------------------------------------------


async def test_seed_installs_curated_templates_ahead_of_induced_ones(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    induced = await harness.graphs.save_version(
        RuleGraph.empty(SOURCE, RuleDomain.EXTRACTION).extended(
            nodes=[RuleNode("tpl.v1.logo", NodeKind.TEMPLATE, LOGO_DETAIL, RuleOrigin.LLM)],
            edges=[RuleEdge(ROOT_KEY, "tpl.v1.logo", {"type": "dom_css", "css": "#the-item"})],
        )
    )

    plan = await seeder(harness).plan(SOURCE)
    curated = [t for t in TEMPLATES if not t.startswith("_") and t != "vocab"]
    assert plan.added == [f"olx.{name}" for name in curated] and plan.retired == []
    candidate = plan.candidate
    llm_edge = next(e for e in candidate.edges if e.to_key == "tpl.v1.logo")
    assert all(
        edge.priority < llm_edge.priority
        for edge in candidate.edges
        if edge.to_key != llm_edge.to_key
    )
    assert all(candidate.node(key).origin is RuleOrigin.HUMAN for key in plan.added)

    saved = await seeder(harness).apply(plan)
    assert saved.version == induced.version + 1
    again = await seeder(harness).plan(SOURCE)
    assert not again.changes and len(again.unchanged) == len(curated)

    retired = await seeder(harness).plan(SOURCE, retire=["tpl.v1.logo"])
    assert retired.retired == ["tpl.v1.logo"] and not retired.candidate.has_node("tpl.v1.logo")
    with pytest.raises(ConfigurationError):
        await seeder(harness).plan(SOURCE, retire=["olx.a2_2013_list"])
    with pytest.raises(ConfigurationError):
        await seeder(harness).plan(SOURCE, retire=["no.such.node"])


# -- audit -----------------------------------------------------------------------------


async def test_audit_reports_blob_integrity_identity_completeness_and_diff(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await harness.archive("a2_2013_list", status=RawDocumentStatus.PARSED)
    for iid in ("535715253", "537756309"):
        await archive_bytes(harness, detail_html(iid), detail_url(iid), "20130821130341")
    await seeder(harness).apply(await seeder(harness).plan(SOURCE))
    # A stored row the replay no longer produces, and one it will fill.
    await harness.listings.upsert_many([make_draft(external_id="u:deadbeef")], source_key=SOURCE)
    await harness.listings.upsert_many(
        [make_draft(external_id="487590261", amount=None)], source_key=SOURCE
    )

    report = await audit_service(harness).audit(SOURCE)

    assert (report.documents, report.blob_mismatch, report.blob_missing) == (3, 0, 0)
    assert report.page_kinds == {"LIST": 1, "DETAIL": 2}
    assert report.detail_page_ids == report.detail_emitted_ids == 2
    assert report.identity_collisions == {} and report.hashed_ids == 0
    assert report.unique_ids == 32 and report.completeness["area"] >= 2
    assert report.diff["lost"] == 1 and report.diff["stored_hashed_ids"] == 1
    assert report.diff["fills"]["price"] == 1
    assert "identity" in report.as_dict() and report.summary_lines()

    # Corrupted bytes are reported, not silently replayed.
    raw = next(iter(harness.documents.documents.values()))
    harness.documents.documents[raw.id] = replace(raw, sha256="0" * 64)  # type: ignore[call-overload]
    assert (await audit_service(harness).audit(SOURCE)).blob_mismatch == 1


def test_audit_helpers() -> None:
    assert problem_reason("item 3: identity conflict: url says 1, template says 2") == (
        "identity conflict"
    )
    assert problem_reason("item 0: listing type unknown (no vocabulary match)") == (
        "listing type unknown"
    )
    drafts = [make_draft(external_id="a"), make_draft(external_id="b", amount=None)]
    assert completeness(drafts)["price"] == 1
    diff = diff_against_stored({"a": drafts[0]}, [])
    assert (diff["added"], diff["lost"]) == (1, 0)


async def test_audit_scores_gold_labels(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await seeder(harness).apply(await seeder(harness).plan(SOURCE))
    report = await audit_service(harness, gold=FileGoldSet(GOLD_ROOT)).audit(SOURCE)
    assert report.gold is not None
    assert report.gold["items"] == 7 and report.gold["rate"] >= 0.95, report.gold


# -- gold ------------------------------------------------------------------------------


async def test_gold_labels_load_and_score_exactly() -> None:
    documents = await FileGoldSet(GOLD_ROOT).documents(SOURCE)
    assert {doc.ref for doc in documents} == {"a2_2013_list", "a2_2013_detail"}
    assert await FileGoldSet(GOLD_ROOT).documents("other_source") == []

    item = GoldItem("1", "Flat", Decimal("100.00"), "EGP", "SALE")
    sale = ListingType.SALE
    good = make_draft(external_id="1", title="  flat ", amount=Decimal("100"), listing_type=sale)
    wrong = make_draft(external_id="1", title="Flat", amount=Decimal("101"), listing_type=sale)
    assert score([good], [item]).exact == 1
    result = score([wrong], [item])
    assert (result.exact, dict(result.misses)) == (0, {"amount": 1})
    assert dict(score([], [item]).misses) == {"external_id": 1}


# -- induction gates ---------------------------------------------------------------------


def samples_of(*iids: str) -> list[_Sample]:
    return [
        _Sample(
            ref=iid,
            document=ArchivedDocument(
                detail_html(iid), detail_url(iid), captured_at=datetime(2013, 8, 21, tzinfo=UTC)
            ),
        )
        for iid in iids
    ]


def detail_proposal(**fields: Any) -> dict[str, Any]:
    base = {
        "title": [{"css": "#olx_item_title .h1"}],
        "url": [{"css": "input.share-url", "attr": "value"}],
        "category": [{"css": "#levelpath a#firstpath2"}],
        "price": [{"css": ".item-highlights.price"}],
    }
    return {
        "name": "detail",
        "page_kind": "DETAIL",
        "conditions": [{"type": "dom_css", "css": "#the-item"}],
        "items": None,
        "fields": {**base, **fields},
        "vocab": TEMPLATES["vocab"],
    }


async def validate(
    harness: Harness, answer: dict[str, Any], samples: list[_Sample], **context: Any
) -> list[str]:
    induction = harness.induction
    parsed, errors = induction._parse_template(answer)
    if parsed is None:
        return errors
    base = await induction._source_context(SOURCE)
    graph = context.pop("graph", RuleGraph.empty(SOURCE, RuleDomain.EXTRACTION))
    return induction._validate_template(
        graph, parsed, samples, _SourceContext(base.identity, base.country_code, **context)
    )


async def test_a_sound_detail_proposal_passes(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    assert await validate(harness, detail_proposal(), samples_of("1", "2", "3")) == []


async def test_induction_rejects_overescaped_regexes(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    answer = detail_proposal(
        bedrooms=[{"css": "#description-text", "regex": "Bedrooms:\\\\s*(\\\\d+)"}]
    )
    errors = await validate(harness, answer, samples_of("1", "2"))
    assert errors and "escaped twice" in errors[0]


async def test_induction_rejects_a_home_page_url_recipe(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    answer = detail_proposal(url=[{"css": "#headerlogolink", "attr": "href"}])
    errors = await validate(harness, answer, samples_of("1", "2"))
    assert any("site home" in error for error in errors), errors


async def test_induction_rejects_one_id_for_different_adverts(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    answer = detail_proposal(url=[{"const": "http://cairo.olx.com.eg/featured-iid-1"}])
    errors = await validate(harness, answer, samples_of("1001", "1002"))
    assert any("same external id" in error for error in errors), errors


async def test_induction_rejects_a_price_regex_that_drops_the_amount(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    answer = detail_proposal(price=[{"css": ".item-highlights.price", "regex": "([\\d,.]+)"}])
    errors = await validate(harness, answer, samples_of("1", "2"))
    assert any("other than the amount" in error for error in errors), errors


async def test_detail_fields_need_three_pages_of_evidence(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    answer = detail_proposal(area=[{"css": ".no-such-thing"}])
    assert await validate(harness, answer, samples_of("1", "2")) == []
    errors = await validate(harness, answer, samples_of("1", "2", "3"))
    assert any("field 'area'" in error for error in errors), errors


async def test_induction_rejects_a_gold_regression(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    good = {"page_kind": "DETAIL", "items": None, "fields": detail_proposal()["fields"]}
    current = RuleGraph.empty(SOURCE, RuleDomain.EXTRACTION).extended(
        nodes=[RuleNode("good", NodeKind.TEMPLATE, good, RuleOrigin.LLM)],
        edges=[RuleEdge(ROOT_KEY, "good", {"type": "dom_css", "css": "#the-item"})],
        vocab=TEMPLATES["vocab"],
    )
    gold = [
        GoldDocument(
            ref="g",
            document=samples_of("777")[0].document,
            items=(
                GoldItem(
                    "777",
                    "Apartment for Sale in Nasr City — Cairo",
                    Decimal("165000.00"),
                    "EGP",
                    "SALE",
                ),
            ),
        )
    ]
    # Richer (wins on fields filled) but reads the title from the wrong element.
    worse = detail_proposal(
        title=[{"css": "#levelpath a#firstpath2"}],
        description=[{"css": "#description-text"}],
        bedrooms=[{"css": "#description-text", "regex": "Bedrooms:\\s*(\\d+)"}],
    )
    errors = await validate(harness, worse, samples_of("1", "2"), graph=current, gold=gold)
    assert any("less accurate" in error for error in errors), errors
    # Also richer, but with the right title: it must outrank "good" to take
    # effect at all (a copy of it would not), and gold does not get worse.
    better = detail_proposal(description=[{"css": "#description-text"}])
    assert await validate(harness, better, samples_of("1", "2"), graph=current, gold=gold) == []
    duplicate = await validate(
        harness, detail_proposal(), samples_of("1", "2"), graph=current, gold=gold
    )
    assert any("good still wins" in error for error in duplicate), duplicate


# -- parse provenance, replay, rebuild --------------------------------------------------


async def test_parse_reports_replay_runs_stale_reparse_and_rebuild(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await seeder(harness).apply(await seeder(harness).plan(SOURCE))
    newer = await archive_bytes(harness, detail_html("42"), detail_url("42"), "20131101000000")
    older = await archive_bytes(harness, detail_html("42"), detail_url("42"), "20130101000000")

    result = await harness.ingestion.parse_pending(SOURCE)
    # (The in-memory repository inserts per draft; the real one upserts.)
    assert result.created == 2
    graph = await harness.graphs.active(SOURCE, RuleDomain.EXTRACTION)
    assert graph is not None
    for document_id in (newer, older):
        raw = await harness.documents.get(UUID(document_id))
        assert raw.status is RawDocumentStatus.PARSED and raw.graph_version == graph.version
        assert raw.parse_report["template_key"] == "olx.a2_2013_detail_item"
        assert raw.parse_report["template_origin"] == "HUMAN"
        assert raw.parse_report["hint_mismatch"] is False
    runs = list(harness.ingestion._runs.runs.values())  # type: ignore[attr-defined]
    (replay,) = [run for run in runs if run.trigger is RunTrigger.REPLAY]
    assert replay.stats["documents"] == 2 and replay.stats["with_listings"] == 2

    # Nothing is stale until the graph moves on; then every parsed document is.
    assert (await harness.ingestion.reparse_stale(SOURCE, graph_version=graph.version)).created == 0
    upgraded = await harness.graphs.save_version(graph.extended(notes="upgrade"))
    restaled = await harness.ingestion.reparse_stale(SOURCE, graph_version=upgraded.version)
    assert restaled.created + restaled.updated + restaled.unchanged == 2  # both documents
    assert {
        raw.graph_version
        for raw in harness.documents.documents.values()  # type: ignore[attr-defined]
    } == {upgraded.version}

    before = await harness.listings.count(SOURCE)
    rebuilt = await harness.ingestion.rebuild(SOURCE)
    assert rebuilt.created == 2 and await harness.listings.count(SOURCE) == 2 < before


# -- crawl coverage ------------------------------------------------------------------------


def test_capture_cap_applies_per_year() -> None:
    def entry(i: int, year: int, digest: str, status: CrawlStatus = CrawlStatus.DISCOVERED):
        return FrontierEntry(i, SOURCE, "k", f"{year}0{(i % 9) + 1}01000000", "u", digest, status)

    captures_ = [
        entry(1, 2011, "A"),
        entry(2, 2011, "B"),
        entry(3, 2011, "C"),
        entry(4, 2013, "A", CrawlStatus.FETCHED),
        entry(5, 2013, "D"),
    ]
    # Each year gets its own slot; the same content in another year still counts.
    assert select_captures(captures_, 1) == {3}
    assert select_captures(captures_, 2) == {1, 3, 5}


async def test_crawl_enumerates_every_missing_year_and_adopts_legacy_cursors(
    tmp_path: Path,
) -> None:
    url = "http://www.olx.com.eg/houses-apartments-for-sale-cat-367"
    years = [Capture(surt_key(url), f"{year}0305032637", url, f"D{year}") for year in (2011, 2012)]
    harness = Harness(tmp_path, ScriptedLlm(), years, years=(2011, 2013))
    # 2011 was enumerated under the old cursor naming; the frontier is not empty.
    await harness.cursors.save(SOURCE, "cdx:2011", resume_key=None, done=True)
    await harness.frontier.add_captures(SOURCE, years[:1])

    report = await harness.crawler.crawl(SOURCE, rounds=1, max_fetches=0)

    assert report.enumeration is not None and report.enumeration.years_completed == [2012, 2013]
    assert {year for _, year, _ in harness.index.requests} == {2012, 2013}
    assert await harness.cursors.get(SOURCE, cursor_key("olx.com.eg", 2011)) == (None, True)
    coverage = {year.year: year for year in await harness.crawler.coverage(SOURCE)}
    assert coverage[2012].enumeration == "done" and "2012Q1" in coverage[2012].quarters


async def test_crawl_releases_claims_left_by_an_interrupted_run(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await harness.frontier.add_captures(SOURCE, captures()[:1])
    await harness.frontier.set_route([1], status=CrawlStatus.QUEUED, priority=90)
    claimed = await harness.frontier.claim_queued(
        SOURCE, limit=5, now=datetime.now(UTC) - timedelta(hours=1)
    )
    assert [entry.status for entry in claimed] == [CrawlStatus.FETCHING]

    await harness.crawler.crawl(
        SOURCE, rounds=1, max_fetches=5, max_llm_calls=0, enumerate_missing=False
    )

    assert harness.frontier.rows[1].status is CrawlStatus.FETCHED


async def test_parsed_documents_with_identity_problems_become_extraction_gaps(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    document_id = await archive_bytes(harness, detail_html("9"), detail_url("9"), "20130101000000")
    uuid = UUID(document_id)
    await harness.documents.mark_parsed(
        uuid, graph_version=1, report={"identity_problems": 1, "items_total": 1, "items_valid": 0}
    )
    assert await harness.induction.collect_extraction_gaps(SOURCE) == 1
    healthy = {"identity_problems": 0, "items_total": 30, "items_valid": 29}
    await harness.documents.mark_parsed(uuid, graph_version=1, report=healthy)
    assert await harness.induction.collect_extraction_gaps(SOURCE) == 0


def test_fixture_document_helper_still_works() -> None:
    assert fixture_document("a2_2013_detail").url.endswith("iid-568736822")
    assert proposal("a2_2013_detail")["page_kind"] == "DETAIL"
