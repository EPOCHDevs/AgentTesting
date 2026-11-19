"""The export suite's pure parts: catalog, run profiles, Arrow row counts, state and checks."""

from __future__ import annotations

import copy
import json
import struct
from pathlib import Path

import httpx
import pytest

from tools.export_e2e import backend as backend_module
from tools.export_e2e import checks, report
from tools.export_e2e.browser import pdf_media_box, pdf_pages, strip_images
from tools.export_e2e.catalog import (
    CATALOG,
    Fixture,
    Format,
    Profile,
    arrow_row_count,
    check_creatable,
    choose,
    load_catalog,
    matches,
    profile,
    result_categories,
    visible_columns,
)

RUN = "11111111-1111-4111-8111-111111111111"
STUDY = "22222222-2222-4222-8222-222222222222"
FORMATS = (
    Format("pdf", "PDF", True),
    Format("markdown", "Markdown", False, ("ENG-3031",)),
    Format("space", "Save to Space", False),
)


# --- catalog ----------------------------------------------------------------------


def test_the_campaign_catalog_loads_and_creates_nothing_that_calls_a_model() -> None:
    catalog = load_catalog()
    ids = [f.id for f in catalog.fixtures]
    assert len(ids) == len(set(ids))
    scripts = [f for f in catalog.fixtures if f.script]
    assert {f.id for f in scripts} == {"chart-gallery", "authored-literal"}
    for fixture in scripts:
        check_creatable(fixture.script.source())  # type: ignore[union-attr]
        assert fixture.script.assets == ()  # type: ignore[union-attr]  # Source-free.
    # Shapes that would spend AI usage to create are reuse-only and say why.
    for name in ("backtest", "monte-carlo", "tuning", "usage-limit-skipped"):
        fixture = catalog.get(name)
        assert fixture.script is None and fixture.create_cost
    assert "AreaRange" in catalog.chart_kinds and "HeatMap:Geographic" in catalog.chart_kinds
    assert [f.menu_label for f in catalog.formats] == ["PDF", "Markdown", "HTML", "Save to Space"]


def test_a_script_that_calls_a_model_is_refused() -> None:
    with pytest.raises(ValueError, match="llm_call"):
        check_creatable('notes = llm_call (facts={}, instructions="x")')
    check_creatable("# A comment may mention llm_call without calling it.\ngauge()(values=1.0)")


def write_catalog(tmp_path: Path, fixtures: list[dict]) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps({"formats": [{"id": "pdf", "menu_label": "PDF", "implemented": True}], "chart_kinds": [], "fixtures": fixtures})
    )
    return path


def test_catalog_rejects_unknown_rules_scripts_outside_the_campaign_and_model_calls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown match keys"):
        load_catalog(write_catalog(tmp_path, [{"id": "a", "shape": "x", "match": {"colour": "red"}}]))
    outside = tmp_path.parent / "outside.epochscript"
    outside.write_text("gauge()(values=1.0)")
    spec = {"name": "n", "description": "d", "period": {"start": "2025-01-02", "end": "2025-01-03"}}
    with pytest.raises(ValueError, match="not in the campaign"):
        load_catalog(write_catalog(tmp_path, [{"id": "a", "shape": "x", "script": {**spec, "path": "../outside.epochscript"}}]))
    (tmp_path / "paid.epochscript").write_text("x = llm_call(facts={})")
    with pytest.raises(ValueError, match="llm_call"):
        load_catalog(write_catalog(tmp_path, [{"id": "a", "shape": "x", "script": {**spec, "path": "paid.epochscript"}}]))
    with pytest.raises(ValueError, match="unique"):
        load_catalog(write_catalog(tmp_path, [{"id": "a", "shape": "x", "match": {}}] * 2))


def test_a_script_fixture_identity_changes_with_its_source(tmp_path: Path) -> None:
    (tmp_path / "s.epochscript").write_text("gauge()(values=1.0)")
    spec = {"name": "n", "description": "d", "path": "s.epochscript", "period": {"start": "2025-01-02", "end": "2025-01-03"}}
    catalog = load_catalog(write_catalog(tmp_path, [{"id": "a", "shape": "x", "script": spec}]))
    first = catalog.get("a").script.spec_hash()  # type: ignore[union-attr]
    (tmp_path / "s.epochscript").write_text("gauge()(values=2.0)")
    assert catalog.get("a").script.spec_hash() != first  # type: ignore[union-attr]


# --- Arrow row counts -------------------------------------------------------------------


def _message(header_type: int, rows: int, body: int) -> bytes:
    """A flatbuffer Message {version, header_type, header: RecordBatch {length}, bodyLength}."""
    buffer = bytearray(64)
    struct.pack_into("<I", buffer, 0, 16)  # Root table at 16.
    struct.pack_into("<6H", buffer, 4, 12, 20, 6, 4, 16, 8)  # Message vtable.
    struct.pack_into("<i", buffer, 16, 16 - 4)
    struct.pack_into("<B", buffer, 20, header_type)
    struct.pack_into("<h", buffer, 22, 4)  # MetadataVersion V5
    struct.pack_into("<q", buffer, 24, body)
    struct.pack_into("<I", buffer, 32, 48 - 32)  # header -> RecordBatch table at 48.
    struct.pack_into("<3H", buffer, 36, 6, 16, 8)  # RecordBatch vtable.
    struct.pack_into("<i", buffer, 48, 48 - 36)
    struct.pack_into("<q", buffer, 56, rows)
    return bytes(buffer)


def _stream(batches: list[int]) -> bytes:
    out = bytearray()
    for header_type, rows in [(1, 0), *[(3, n) for n in batches]]:
        meta = _message(header_type, rows, 16 if header_type == 3 else 0)
        out += struct.pack("<Ii", 0xFFFFFFFF, len(meta)) + meta
        out += bytes(16 if header_type == 3 else 0)
    return bytes(out + struct.pack("<Ii", 0xFFFFFFFF, 0))


def test_arrow_row_count_sums_record_batches_in_streams_and_files() -> None:
    stream = _stream([3, 5])
    assert arrow_row_count(stream) == 8
    file = b"ARROW1\x00\x00" + stream + b"footer-bytes" + struct.pack("<i", 12) + b"ARROW1"
    assert arrow_row_count(file) == 8
    assert arrow_row_count(_stream([])) == 0


# --- profiles and matching -------------------------------------------------------------------


def schema(**content: object) -> dict:
    return {
        "charts": [
            {"category": "Prices", "id": "c1", "title": "Close", "chart_def": {"kind": "Lines"}},
            {"category": "Grid", "id": "h1", "title": "Map", "chart_def": {"kind": "HeatMap", "layout": "Geographic"}},
            {"category": "Grid", "id": "h2", "title": "Matrix", "chart_def": {"kind": "HeatMap"}},
        ],
        "tables": [
            {"category": "Prices", "index": 0, "title": "Cards", "flavor": "Cards", "columns": [], "cards": [{}, {}]},
            {
                "category": "Prices",
                "index": 1,
                "title": "Rows",
                "flavor": "Detailed",
                "data_id": "t1",
                "columns": [
                    {"id": "a"},
                    {"id": "b", "suffix_column_id": "c"},
                    {"id": "c"},
                    {"id": "d", "content_role": "Guide"},
                    {"id": "e", "initially_hidden": True},
                ],
            },
        ],
        "content": content,
    }


EXECUTION = {
    "id": RUN,
    "definition_id": STUDY,
    "status": "completed",
    "execution_type": "research",
    "period": {"start": "2024-01-02", "end": "2024-03-28"},
    "created_at": 1700000000000,
}


def test_profile_reads_kinds_layouts_visible_columns_rows_and_written_up_content() -> None:
    content = {
        "categories": [],
        "sections": [{"executive_summary": {"briefing": {"items": [{"text": "x"}]}}}],
        "markdown": [{"markdown": "", "briefing": {"items": [{"text": "A note"}]}}, {"markdown": " ", "hidden": True}],
        "graphs": [],
    }
    whole = schema(**content) | {"ai_text": {"status": "skipped", "reason": "usage_limit"}}
    run = profile(
        EXECUTION,
        {"ALL": {"charts_count": 3, "tables_count": 2, "cards_count": 2}},
        {"ALL": whole},
        {("ALL", "t1"): 424},
        name="Study",
    )
    assert run.chart_kinds == ["HeatMap:Geographic", "HeatMap:Matrix", "Lines"]
    assert run.authored and run.briefings == 2 and run.notes == 1
    assert run.ai_text_skipped and run.cards == 2
    assert run.max_table_rows == 424 and run.max_table_columns == 2  # a, and b with c as its suffix.
    assert run.exhibit_categories == ["Grid", "Prices"]
    assert Profile.from_json(json.loads(json.dumps(run.to_json()))).to_json() == run.to_json()


def test_result_categories_prefer_specific_categories_over_all() -> None:
    assert result_categories({"ALL": {"charts_count": 1}, "Risk": {"tables_count": 1}, "Empty": {"charts_count": 0}}) == ["Risk"]
    assert result_categories({"ALL": {"content_count": 2}}) == ["ALL"]
    assert result_categories({}) == []
    assert visible_columns({"columns": [{"id": "a"}, {"id": "b", "content_role": "Diagnostic"}]}) == 1


def run_profile(run_id: str, **fields: object) -> Profile:
    base = {"run_id": run_id, "study_id": STUDY, "execution_type": "research", "status": "completed", "created_at": 1, "period": None}
    return Profile(**(base | fields))  # type: ignore[arg-type]


def test_matching_explains_misses_and_choose_prefers_then_falls_back_to_taken_runs() -> None:
    wide = run_profile("wide", tables=[{"category": "A", "columns": 25, "rows": 3}], created_at=1)
    narrow = run_profile("narrow", tables=[{"category": "A", "columns": 7, "rows": 3}], created_at=2)
    failed = run_profile("failed", status="failed", tables=[{"category": "A", "columns": 30, "rows": 3}])
    fixture = Fixture("wide-table", "x", match={"min_table_columns": 7}, prefer="max_table_columns")
    assert matches({"min_table_columns": 8}, narrow) == ["widest table has 7 columns"]
    assert "status is failed" in matches({}, failed)
    assert choose(fixture, [narrow, wide, failed]).run_id == "wide"  # type: ignore[union-attr]
    assert choose(fixture, [narrow, wide], exclude={"wide"}).run_id == "narrow"  # type: ignore[union-attr]
    assert matches({"has_results": False}, run_profile("empty")) == []
    assert matches({"execution_type": ["monte_carlo", "montecarlo"]}, narrow) == ["execution type is research"]


# --- state and idempotent creation ------------------------------------------------------------------


def test_state_belongs_to_one_environment_and_account(tmp_path: Path) -> None:
    path = tmp_path / "state-dev.json"
    state = backend_module.State(path, "dev", "owner-a")
    state.fixtures["a"] = {"origin": "reused", "run_id": RUN}
    state.save()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert backend_module.State(path, "dev", "owner-a").fixtures["a"]["run_id"] == RUN
    with pytest.raises(ValueError, match="another environment or account"):
        backend_module.State(path, "dev", "owner-b")
    with pytest.raises(ValueError, match="another environment or account"):
        backend_module.State(path, "qa", "owner-a")


class FakeBackend:
    def __init__(self, fail_start: bool = False):
        self.calls: list[str] = []
        self.runs: dict[str, dict] = {}
        self.fail_start = fail_start

    def validate(self, source, start, end, assets, study_type="research"):  # noqa: ANN001, ANN201
        self.calls.append("validate")
        return {"status": "ok"}

    def create_definition(self, name, description, source, assets):  # noqa: ANN001, ANN201
        self.calls.append("definition")
        return STUDY

    def execution(self, run_id):  # noqa: ANN001, ANN201
        return self.runs.get(run_id)

    def start_execution(self, definition_id, execution_id, start, end):  # noqa: ANN001, ANN201
        self.calls.append(f"start {execution_id}")
        if self.fail_start:
            raise ConnectionError("dropped")
        self.runs[execution_id] = {"id": execution_id, "status": "completed", "execution_type": "research", "definition_id": definition_id}

    def wait(self, run_id, timeout=900):  # noqa: ANN001, ANN201
        return self.runs[run_id]


def test_creating_a_fixture_twice_or_after_an_interruption_never_duplicates(tmp_path: Path) -> None:
    (tmp_path / "s.epochscript").write_text("gauge()(values=1.0)")
    spec = {"name": "n", "description": "d", "path": "s.epochscript", "period": {"start": "2025-01-02", "end": "2025-01-03"}}
    fixture = load_catalog(write_catalog(tmp_path, [{"id": "a", "shape": "x", "script": spec}])).get("a")
    state = backend_module.State(tmp_path / "state.json", "dev", "owner")
    interrupted = FakeBackend(fail_start=True)
    with pytest.raises(ConnectionError):
        backend_module.create_fixture(interrupted, state, fixture, log=lambda _: None)  # type: ignore[arg-type]
    pending = json.loads((tmp_path / "state.json").read_text())["fixtures"]["a"]
    assert pending["definition_id"] == STUDY and pending["run_id"]  # Recorded before the request.
    resumed = FakeBackend()
    run_id = backend_module.create_fixture(resumed, state, fixture, log=lambda _: None)  # type: ignore[arg-type]
    assert run_id == pending["run_id"]
    assert resumed.calls == [f"start {run_id}"]  # Same definition, same client-chosen run id.
    again = backend_module.create_fixture(resumed, state, fixture, log=lambda _: None)  # type: ignore[arg-type]
    assert again == run_id and resumed.calls == [f"start {run_id}"]


# --- checks on a recorded observation ------------------------------------------------------------------

IMAGE = {"sha256": "ab" * 32, "length": 1000, "prefix": "data:image/png;base64,"}
ROWS = [[str(i), f"{i},000"] for i in range(250)]


def export_data() -> dict:
    return {
        "version": 1,
        "exportedAt": "2026-11-18T23:10:00.000Z",
        "study": {
            "id": STUDY,
            "title": "Election returns",
            "question": "How did returns differ?",
            "period": "2 Jan 2024 – 28 Mar 2024",
            "runId": RUN,
            "runDate": "2026-11-16T05:43:47.000Z",
        },
        "notices": [],
        "sections": [
            {
                "id": "s1",
                "title": "Overview",
                "blocks": [
                    {"kind": "heading", "text": "Key charts"},
                    {"kind": "chart", "id": "Prices:c1", "title": "Close", "image": IMAGE, "width": 720, "height": 400, "legend": [], "notes": []},
                    {"kind": "metrics", "metrics": [{"label": "A", "value": "1"}, {"label": "B", "value": "2"}]},
                    {"kind": "list", "marker": "Dot", "items": [{"text": "x", "sources": []}]},
                    {"kind": "text", "markdown": "Some *text*", "title": "Method"},
                ],
            },
            {
                "id": "s2",
                "title": "Rows",
                "blocks": [
                    {"kind": "table", "id": "Prices:1", "title": "All rows", "columns": [{"id": "a", "name": "A", "numeric": False}, {"id": "b", "name": "B", "numeric": True}], "rows": ROWS},
                    {"kind": "chart", "id": "graph:g", "title": "Links", "image": None, "width": 0, "height": 0, "legend": [], "notes": [], "message": checks.GRAPH_MESSAGE},
                ],
            },
        ],
    }


def dom() -> dict:
    return {
        "found": True,
        "cover": {
            "title": "Election returns",
            "exported": "Exported from Epoch on 18 Nov 2026",
            "facts": "Period: 2 Jan 2024 – 28 Mar 2024   ·   Run: 16 Nov 2026, 05:43",
            "question": "How did returns differ?",
        },
        "notices": [],
        "sections": [
            {
                "title": "Overview",
                "description": None,
                "units": [
                    {"heading": {"text": "Key charts"}, "block": {"tag": "figure", "text": "Close", "h4": [], "ps": [], "figure": {"title": "Close", "image": {"sha256": IMAGE["sha256"]}, "message": None}}},
                    {"heading": None, "block": {"tag": "div", "text": "A 1 B 2", "h4": [], "ps": [], "metrics": {"lists": 1, "labels": ["A", "B"]}}},
                    {"heading": None, "block": {"tag": "div", "text": "x", "h4": [], "ps": [], "list_items": 1}},
                    {"heading": None, "block": {"tag": "div", "text": "Method Some text", "h4": ["Method"], "ps": []}},
                ],
            },
            {
                "title": "Rows",
                "description": None,
                "units": [
                    {"heading": None, "block": {"tag": "div", "text": "All rows", "h4": ["All rows"], "ps": ["Showing 200 of 250 rows. The Markdown export has every row."], "tables": [{"header_cells": 2, "body_rows": 200}]}},
                    {"heading": None, "block": {"tag": "figure", "text": "Links", "h4": [], "ps": [checks.GRAPH_MESSAGE], "figure": {"title": "Links", "image": None, "message": checks.GRAPH_MESSAGE}}},
                ],
            },
        ],
    }


def observation() -> dict:
    return {
        "run_id": RUN,
        "study_id": STUDY,
        "ai_runs": [],
        "menu": {
            "found": True,
            "label": checks.MENU_LABEL,
            "note": checks.MENU_NOTE,
            "items": [{"label": "PDF", "description": checks.PDF_DESCRIPTION, "href": f"/print/studies/{STUDY}?run={RUN}", "target": "_blank", "rel": "noopener noreferrer"}],
        },
        "formats": {"pdf": {"offered": True}, "markdown": {"offered": False}, "space": {"offered": False}},
        "print": {"ready": "true", "elapsed_s": 4.2},
        "document_title": "Election returns – 16 Nov 2026",
        "export": export_data(),
        "dom": dom(),
        "images": [{"index": 0, "decoded": True, "natural_width": 1440, "natural_height": 800, "distinct_colors": 80, "non_background": 0.2}],
        "overflow": {
            "A4": [{"index": 0, "title": "All rows", "overflowing_cells": 0, "past_page_px": 0, "clipped_px": 0, "examples": []}],
            "Letter": [{"index": 0, "title": "All rows", "overflowing_cells": 0, "past_page_px": 0, "clipped_px": 0, "examples": []}],
        },
        "pdf": {"A4": {"bytes": 50000, "pages": 6, "media_box": [0, 0, 595.92, 842.88]}, "Letter": {"bytes": 50000, "pages": 7, "media_box": [0, 0, 612, 792]}},
        "print_page": {"console": [], "failed_requests": []},
    }


FIXTURE = Fixture("long-table", "Table with more than 200 rows", match={"min_table_rows": 201})
PROFILE = run_profile(
    RUN,
    name="Election returns",
    period={"start": "2024-01-02", "end": "2024-03-28"},
    categories=["ALL"],
    charts=[{"category": "Prices", "id": "c1", "kind": "Lines"}],
    tables=[{"category": "Prices", "columns": 2, "rows": 250}],
)


def statuses(obs: dict, fixture: Fixture = FIXTURE, run: Profile | None = PROFILE) -> dict[str, str]:
    return {r.check: r.status for r in checks.evaluate(obs, fixture, FORMATS, run)}


def failing(obs: dict, run: Profile | None = PROFILE) -> dict[str, str]:
    return {r.check: r.detail for r in checks.evaluate(obs, FIXTURE, FORMATS, run) if r.status == "fail"}


def test_a_faithful_export_passes_every_check() -> None:
    results = checks.evaluate(observation(), FIXTURE, FORMATS, PROFILE)
    assert {r.check: r.status for r in results if r.status != "pass"} == {
        "format-markdown": "skip",
        "format-space": "skip",
        "no-internal-reasons": "skip",  # Backend recorded no AI text failure.
    }
    images = next(r for r in results if r.check == "chart-images")
    assert images.notes and "Relationship graphs" in images.notes[0]
    assert "1 tables over 200 rows" in next(r.detail for r in results if r.check == "row-limit")
    assert checks.outcome(results) == "pass"


def test_the_menu_flags_new_formats_and_a_wrong_print_link() -> None:
    obs = observation()
    obs["menu"]["items"].append({"label": "Word"})
    obs["menu"]["items"][0]["href"] = f"/print/studies/other?run={RUN}"
    detail = failing(obs)["export-menu"]
    assert "unrecognised items ['Word']" in detail and "expected '/print/studies/" in detail
    offered = observation()
    offered["formats"]["markdown"] = {"offered": True}
    assert failing(offered)["format-markdown"] == "Markdown is offered, but the suite has no check for it yet"


def test_print_readiness_failures_stop_the_layout_checks() -> None:
    obs = observation()
    obs["print"] = {"ready": "error", "elapsed_s": 1.0, "message": "This run couldn’t be loaded."}
    result = statuses(obs)
    assert result["print-ready"] == "fail" and "sections-and-blocks" not in result
    obs["print"] = {"ready": None, "elapsed_s": 180.0, "message": ""}
    assert "No data-export-ready within 180.0s" in failing(obs)["print-ready"]


def test_missing_blocks_wrong_images_and_headings_fail_sections() -> None:
    obs = observation()
    obs["dom"]["sections"][0]["units"].pop(2)
    assert "section 1 'Overview': 3 parts printed, expected 4" in failing(obs)["sections-and-blocks"]
    obs = observation()
    obs["dom"]["sections"][0]["units"][0]["block"]["figure"]["image"]["sha256"] = "00"
    obs["dom"]["sections"][0]["units"][0]["heading"]["text"] = "Other"
    detail = failing(obs)["sections-and-blocks"]
    assert "different image" in detail and "heading 'Other'" in detail
    obs = observation()
    obs["dom"]["sections"].pop()
    assert "1 sections printed, the export has 2" in failing(obs)["sections-and-blocks"]


def test_blank_or_missing_chart_images_fail() -> None:
    obs = observation()
    obs["images"][0] |= {"distinct_colors": 1, "non_background": 0.0}
    assert "'Close' is blank" in failing(obs)["chart-images"]
    obs = observation()
    obs["export"]["sections"][1]["blocks"][1] |= {"id": "Prices:c2", "message": checks.CHART_FAILED}
    obs["dom"]["sections"][1]["units"][1]["block"]["figure"]["message"] = checks.CHART_FAILED
    assert "has no image" in failing(obs)["chart-images"]


def test_overflowing_cells_and_tables_past_the_page_fail() -> None:
    obs = observation()
    obs["overflow"]["Letter"][0] |= {"overflowing_cells": 2, "examples": [{"row": 3, "column": 1, "text": "1,234,567,890", "overflow_px": 14}]}
    obs["overflow"]["A4"][0] |= {"past_page_px": 40}
    detail = failing(obs)["table-cells-fit"]
    assert "Letter: 'All rows' has 2 cells past their column" in detail and "A4: 'All rows' runs 40px past the page" in detail


def test_long_tables_need_the_limit_line_and_short_ones_must_not_have_it() -> None:
    obs = observation()
    obs["dom"]["sections"][1]["units"][0]["block"]["ps"] = []
    assert "has no limit line 'Showing 200 of 250 rows." in failing(obs)["row-limit"]
    obs = observation()
    obs["export"]["sections"][1]["blocks"][0]["rows"] = ROWS[:150]
    obs["dom"]["sections"][1]["units"][0]["block"]["tables"][0]["body_rows"] = 150
    detail = failing(obs)
    assert "has a limit line with only 150 rows" in detail["row-limit"]
    assert "no exported table has 201+ rows" in detail["fixture-shape"]


def test_usage_limit_notices_must_match_backend_and_print() -> None:
    skipped = copy.deepcopy(PROFILE)
    skipped.ai_text_skipped = True
    detail = failing(observation(), skipped)
    assert "no notice prints" in detail["ai-text-notices"]
    assert "the export has no notice" in detail["export-data"]
    obs = observation()
    notice = {"kind": "notice", "reason": "usage_limit", "text": checks.USAGE_LIMIT_NOTICE}
    obs["export"]["notices"] = [notice]
    obs["dom"]["notices"] = [checks.USAGE_LIMIT_NOTICE]
    assert statuses(obs, run=skipped)["ai-text-notices"] == "pass"
    obs["dom"]["notices"] = []
    assert "page notices []" in failing(obs, skipped)["ai-text-notices"]


def test_cover_title_and_pdf_size_are_checked() -> None:
    obs = observation()
    obs["dom"]["cover"]["facts"] = "Run: 16 Nov 2026, 05:43"
    obs["document_title"] = "Election returns"
    obs["pdf"]["Letter"]["media_box"] = [0, 0, 595.92, 842.88]
    detail = failing(obs)
    assert "missing 'Period: 2 Jan 2024 – 28 Mar 2024'" in detail["cover"]
    assert "expected 'Election returns – 16 Nov 2026'" in detail["document-title"]
    assert "Letter page is" in detail["pdf"]
    obs = observation()
    obs["export"]["study"]["period"] = "1 Jan 2020 – 2 Jan 2020"
    assert "the run's is '2 Jan 2024 – 28 Mar 2024'" in failing(obs)["export-data"]


NO_DASHBOARD = Fixture("no-dashboard", "x", match={"has_results": False})


def test_a_run_without_a_dashboard_offering_a_cover_only_pdf_fails() -> None:
    empty = run_profile(RUN, name="Text answer", categories=[])
    obs = observation()
    obs["export"]["sections"] = []
    obs["dom"]["sections"] = []
    result = {r.check: r for r in checks.evaluate(obs, NO_DASHBOARD, FORMATS, empty)}
    assert result["export-menu"].status == "fail" and "Export offers PDF" in result["export-menu"].detail
    nothing = result["nothing-to-print"]
    assert nothing.status == "fail" and "cover-only PDF (6 A4 pages)" in nothing.detail


@pytest.mark.parametrize("kind", ["placeholder", "absent"])
def test_a_run_without_a_dashboard_passes_with_no_pdf_and_an_error_page(kind: str) -> None:
    empty = run_profile(RUN, name="Text answer", categories=[])
    obs = {
        "run_id": RUN,
        "study_id": STUDY,
        "ai_runs": [],
        "menu": {"found": False, "kind": kind, "items": [], "no_dashboard_notice": True},
        "formats": {"pdf": {"offered": False}, "markdown": {"offered": False}, "space": {"offered": False}},
        "print": {"ready": "error", "elapsed_s": 1.0, "message": "This run has no dashboard to export.\n"},
        "print_page": {"console": [], "failed_requests": []},
    }
    result = {r.check: r.status for r in checks.evaluate(obs, NO_DASHBOARD, FORMATS, empty)}
    assert result == {
        "export-menu": "pass",
        "format-pdf": "skip",
        "format-markdown": "skip",
        "format-space": "skip",
        "nothing-to-print": "pass",
        "print-page-clean": "pass",
        "no-assistant-runs": "pass",
    }
    obs["print"]["message"] = "This run couldn’t be loaded."
    assert "not 'This run has no dashboard to export.'" in next(
        r.detail for r in checks.evaluate(obs, NO_DASHBOARD, FORMATS, empty) if r.check == "nothing-to-print"
    )


# --- a wide table printed in column parts ---------------------------------------------------------

NAMES = [f"C{i}" for i in range(1, 25)]


def part(label: str | None, headers: list[str], rows: int = 6, limit: str | None = None) -> dict:
    return {"part": {"title": "Forecasts", "label": label, "limit_line": limit}, "header_cells": len(headers), "headers": headers, "body_rows": rows}


def wide_observation(parts: list[dict], rows: int = 6) -> dict:
    obs = observation()
    table = {"kind": "table", "id": "A:1", "title": "Forecasts", "columns": [{"id": n, "name": n, "numeric": True} for n in NAMES], "rows": [NAMES] * rows}
    obs["export"]["sections"][1]["blocks"][0] = table
    obs["dom"]["sections"][1]["units"][0]["block"] = {"tag": "div", "text": "Forecasts", "h4": ["Forecasts"] * len(parts), "ps": [], "tables": parts}
    return obs


GOOD_PARTS = [
    part("Columns 1–8 of 24", NAMES[0:8]),
    part("Columns 9–15 of 24", ["C1", *NAMES[8:15]]),
    part("Columns 16–22 of 24", ["C1", *NAMES[15:22]]),
    part("Columns 23–24 of 24", ["C1", *NAMES[22:24]]),
]


def test_a_wide_table_printed_in_column_parts_matches_its_json() -> None:
    obs = wide_observation(copy.deepcopy(GOOD_PARTS))
    result = {r.check: r for r in checks.evaluate(obs, FIXTURE, FORMATS, PROFILE)}
    assert result["sections-and-blocks"].status == "pass"
    assert "1 wide tables printed in 4 column parts" in result["sections-and-blocks"].detail
    single = [part("Column 9 of 9", ["C1", "C9"])]
    assert checks._table_parts_mismatch({"title": "Forecasts", "columns": [{"name": n} for n in NAMES[:9]], "rows": [[]] * 6}, [part("Columns 1–8 of 9", NAMES[:8]), *single]) is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda parts: parts.pop(2), "parts show columns"),
        (lambda parts: parts[1].update(headers=NAMES[8:15]), "part 'Columns 9–15 of 24' has columns"),
        (lambda parts: parts[1]["part"].update(label=None), "part 2 has no 'Columns a–b of 24' label"),
        (lambda parts: parts[3].update(body_rows=5), "prints 5 rows, expected 6"),
        (lambda parts: parts[0]["part"].update(label="Columns 1–8 of 25"), "doesn't fit its 24 columns"),
    ],
)
def test_column_parts_must_cover_every_column_with_the_row_label(change, message: str) -> None:  # noqa: ANN001
    parts = copy.deepcopy(GOOD_PARTS)
    change(parts)
    assert message in failing(wide_observation(parts))["sections-and-blocks"]


def test_every_column_part_of_a_long_table_carries_the_limit_line() -> None:
    line = "Showing 200 of 250 rows. The Markdown export has every row."
    parts = [part(p["part"]["label"], p["headers"], rows=200, limit=line) for p in GOOD_PARTS]
    assert statuses(wide_observation(parts, rows=250))["row-limit"] == "pass"
    parts[2]["part"]["limit_line"] = None
    assert "(Columns 16–22 of 24) has no limit line" in failing(wide_observation(parts, rows=250))["row-limit"]


def test_noisy_print_pages_and_assistant_runs_fail() -> None:
    obs = observation()
    obs["print_page"] = {"console": ["TypeError: x"], "failed_requests": [{"method": "GET", "url": "https://x/y", "status": 500}]}
    obs["ai_runs"] = [{"method": "POST", "url": "https://agent/threads/t/runs/stream"}]
    detail = failing(obs)
    assert "1 console errors, 1 failed requests" in detail["print-page-clean"]
    assert "Started assistant runs" in detail["no-assistant-runs"]


def test_chart_kind_coverage_and_report() -> None:
    coverage = checks.chart_coverage(["Lines", "AreaRange"], {"long-table": (PROFILE, observation(), "pass")})
    assert coverage == {"Lines": [{"fixture": "long-table", "images": "pass"}], "AreaRange": []}
    results = [r.to_json() for r in checks.evaluate(observation(), FIXTURE, FORMATS, PROFILE)]
    text = report.render(
        {
            "attempt": "a",
            "environment": "dev",
            "ui_url": "https://app",
            "backend_url": "https://api",
            "started_at": "t",
            "fixtures": [
                {"id": "long-table", "shape": "x", "run_id": RUN, "origin": "reused", "outcome": "pass", "results": results},
                {"id": "tuning", "shape": "Tuning", "outcome": "uncovered", "reason": "No run; costs AI."},
            ],
            "chart_kinds": coverage,
            "formats": {"PDF": "offered and checked"},
        }
    )
    assert "| AreaRange | not covered | — |" in text and "**tuning** (Tuning): No run; costs AI." in text


def test_evidence_helpers_strip_images_and_count_pdf_pages() -> None:
    data = {"sections": [{"blocks": [{"kind": "chart", "image": "data:image/png;base64,AAAA"}, {"kind": "text"}]}]}
    stripped = strip_images(data)
    assert stripped["sections"][0]["blocks"][0]["image"]["length"] == 26
    assert data["sections"][0]["blocks"][0]["image"].startswith("data:")  # Original untouched.
    pdf = b"%PDF-1.4 /Type /Pages /Count 2 /Type /Page /MediaBox [0 0 612 792] /Type/Page"
    assert pdf_pages(pdf) == 2 and pdf_media_box(pdf) == [0.0, 0.0, 612.0, 792.0]


def test_catalog_path_is_the_campaign() -> None:
    assert CATALOG.parts[-3:] == ("campaigns", "export-e2e", "catalog.json")


def test_words_broken_mid_word_in_table_cells_fail() -> None:
    obs = observation()
    obs["overflow"]["A4"][0] |= {"columns": 24, "broken_words": 3, "broken_examples": [{"row": 1, "column": 2, "word": "N|o|v"}, {"row": 2, "column": 1, "word": "Succes|s"}]}
    detail = failing(obs)["table-words-intact"]
    assert "A4: 3 words broken mid-word in 1 tables" in detail and "'N|o|v'" in detail
    assert statuses(observation())["table-words-intact"] == "pass"


# --- a local Terminal against the environment's Backend --------------------------------------------

from tools.agent_testing import Stack  # noqa: E402
from tools.export_e2e import browser, target  # noqa: E402

DEV_STACK = Stack(
    ai_url="https://agent.example.test",
    backend_url="https://api.example.test",
    ui_url="https://app.example.test",
    environment="dev",
    user_pool_id="us-west-1_Pool",
    user_pool_client_id="client123",
)


@pytest.mark.parametrize(
    ("given", "origin"),
    [
        ("http://localhost:3002", "http://localhost:3002"),
        ("http://localhost:3002/", "http://localhost:3002"),
        ("https://127.0.0.1:8443", "https://127.0.0.1:8443"),
        ("http://[::1]:5173", "http://[::1]:5173"),
        ("http://terminal.localhost:3000", "http://terminal.localhost:3000"),
    ],
)
def test_a_local_terminal_origin_is_accepted(given: str, origin: str) -> None:
    assert target.override_origin(given, "dev") == origin


@pytest.mark.parametrize(
    "given",
    ["https://app.dev.epoch.trade.evil.test", "http://192.168.1.4:3000", "http://localhost:3002/studies", "http://user:pw@localhost:3002", "ftp://localhost", "http://localhost:3002?x=1"],
)
def test_other_origins_never_receive_the_sign_in(given: str) -> None:
    with pytest.raises(ValueError):
        target.override_origin(given, "dev")


def test_an_overridden_terminal_is_recorded_in_the_attempt_identity() -> None:
    assert target.identity(DEV_STACK) == DEV_STACK.identity()
    services = target.identity(DEV_STACK, "http://localhost:3002")
    assert services["ui_url"] == "http://localhost:3002"
    assert services["backend_url"] == "https://api.example.test"
    assert services["ui_url_override"] == {"replaces": "https://app.example.test", "backend_and_sign_in_from": "dev"}


def test_sign_in_is_seeded_on_the_overridden_origin_without_the_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import jwt

    key = "an-unused-test-signing-key-of-32-bytes!"
    access = jwt.encode({"username": "user-1", "client_id": "client123", "exp": 9999999999}, key, algorithm="HS256")
    tokens = {"access_token": access, "id_token": "id.token.value", "refresh_token": "refresh-secret"}
    seen = {}

    def cached(path, login_command, min_validity):  # noqa: ANN001, ANN202
        seen["min_validity"] = min_validity
        return tokens

    monkeypatch.setattr(browser.local_auth, "cached_tokens", cached)
    state = browser.storage_state(DEV_STACK, "http://localhost:3002")
    (origin,) = state["origins"]  # type: ignore[misc]
    assert origin["origin"] == "http://localhost:3002"
    items = {i["name"]: i["value"] for i in origin["localStorage"]}
    prefix = "CognitoIdentityServiceProvider.client123"
    assert items[f"{prefix}.LastAuthUser"] == "user-1"
    assert items[f"{prefix}.user-1.idToken"] == "id.token.value"
    assert items[f"{prefix}.user-1.accessToken"] == access
    assert "refresh-secret" not in json.dumps(state)
    assert seen["min_validity"] >= 30 * 60
    assert browser.storage_state(DEV_STACK)["origins"][0]["origin"] == "https://app.example.test"  # type: ignore[index]
    text = report.render(
        {"attempt": "a", "environment": "dev", "ui_url": "http://localhost:3002", "ui_url_override_of": "https://app.example.test", "backend_url": "https://api", "started_at": "t", "fixtures": []}
    )
    assert "Terminal override:** http://localhost:3002 replaces https://app.example.test" in text


# --- Monte Carlo and tuning: created only with approval, once ------------------------------------------


def test_derived_fixtures_need_their_parent_and_are_never_plain_scripts() -> None:
    catalog = load_catalog()
    monte_carlo, tuning = catalog.get("monte-carlo"), catalog.get("tuning")
    assert monte_carlo.derive.kind == "monte_carlo" and monte_carlo.derive.parent == "backtest"  # type: ignore[union-attr]
    assert tuning.derive.kind == "parameter_search" and tuning.derive.parent == "backtest"  # type: ignore[union-attr]
    assert tuning.derive.search["budget"]["max_trials"] == 2  # type: ignore[index, union-attr]
    assert "tune_space(" in tuning.derive.script.source()  # type: ignore[union-attr]


def test_usage_delta_reports_meter_and_kind_movement() -> None:
    before = {"meters": [{"meter": "study_ai", "used": 55.0}], "breakdown": [{"kind": "monte_carlo", "used": 0}]}
    after = {"meters": [{"meter": "study_ai", "used": 57.5}], "breakdown": [{"kind": "monte_carlo", "used": 2.5}]}
    assert backend_module.usage_delta(before, after) == {"kind:monte_carlo": 2.5, "meter:study_ai": 2.5}


class PaidBackend(FakeBackend):
    def __init__(self, status: str = "completed"):
        super().__init__()
        self.status = status
        self.usage_reads = 0

    def usage(self):  # noqa: ANN201
        self.usage_reads += 1
        return {"read_at": str(self.usage_reads), "meters": [{"meter": "study_ai", "used": 55.0 + self.usage_reads}], "breakdown": []}

    def snapshot(self, run_id):  # noqa: ANN001, ANN201
        return {"study": {"data": {"assets": ["SEC_abc-Stocks"]}}}

    def start_monte_carlo(self, parent_id, execution_id, options):  # noqa: ANN001, ANN201
        self.calls.append(f"monte carlo {parent_id} {execution_id}")
        self.runs[execution_id] = {"id": execution_id, "status": self.status, "execution_type": "montecarlo", "definition_id": STUDY}

    def parameter_search(self, definition_id, body):  # noqa: ANN001, ANN201
        if body.get("dry_run"):
            self.calls.append("dry run")
            return {"trial_count": 2, "est_duration_secs": 90}
        self.calls.append(f"search {body['execution_id']}")
        self.runs[body["execution_id"]] = {"id": body["execution_id"], "status": self.status, "execution_type": "parameter_search", "definition_id": definition_id}
        return {"id": body["execution_id"], "trial_count": 2}

    def create_definition(self, name, description, source, assets):  # noqa: ANN001, ANN201
        self.calls.append(f"definition {assets}")
        return STUDY


def test_a_paid_run_is_made_once_with_meters_read_around_it(tmp_path: Path) -> None:
    catalog = load_catalog()
    state = backend_module.State(tmp_path / "state.json", "dev", "owner")
    paid = PaidBackend()
    run_id = backend_module.create_paid_fixture(paid, state, catalog.get("monte-carlo"), RUN, log=lambda _: None, settle=0)  # type: ignore[arg-type]
    assert paid.calls == [f"monte carlo {RUN} {run_id}"]
    entry = state.fixtures["monte-carlo"]
    assert entry["paid"] and entry["parent_run_id"] == RUN
    assert entry["usage_delta"]["meter:study_ai"] == 2.0  # Read before the request and a minute after it settled.
    assert backend_module.create_paid_fixture(paid, state, catalog.get("monte-carlo"), RUN, log=lambda _: None, settle=0) == run_id  # type: ignore[arg-type]
    assert paid.calls == [f"monte carlo {RUN} {run_id}"]
    search = backend_module.create_paid_fixture(paid, state, catalog.get("tuning"), RUN, log=lambda _: None, settle=0)  # type: ignore[arg-type]
    assert paid.calls[1:] == ["validate", "definition ['SEC_abc-Stocks']", "dry run", f"search {search}"]


def test_a_failed_paid_run_is_reported_not_retried(tmp_path: Path) -> None:
    catalog = load_catalog()
    state = backend_module.State(tmp_path / "state.json", "dev", "owner")
    failing_backend = PaidBackend(status="failed")
    with pytest.raises(RuntimeError, match="failed"):
        backend_module.create_paid_fixture(failing_backend, state, catalog.get("monte-carlo"), RUN, log=lambda _: None, settle=0)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="failed"):
        backend_module.create_paid_fixture(failing_backend, state, catalog.get("monte-carlo"), RUN, log=lambda _: None, settle=0)  # type: ignore[arg-type]
    assert len([c for c in failing_backend.calls if c.startswith("monte carlo")]) == 1



# --- #286's polish: balanced column parts, one "unavailable" notice, no internal reasons ------------

BALANCED_PARTS = [
    part("Columns 1–7 of 24", NAMES[0:7]),
    part("Columns 8–13 of 24", ["C1", *NAMES[7:13]]),
    part("Columns 14–19 of 24", ["C1", *NAMES[13:19]]),
    part("Columns 20–24 of 24", ["C1", *NAMES[19:24]]),
]


def test_balanced_column_parts_with_own_slice_labels_pass() -> None:
    obs = wide_observation(copy.deepcopy(BALANCED_PARTS))
    assert statuses(obs)["sections-and-blocks"] == "pass"
    ten = [{"name": n} for n in NAMES[:10]]
    halves = [part("Columns 1–6 of 10", NAMES[:6]), part("Columns 7–10 of 10", ["C1", *NAMES[6:10]])]
    assert checks._table_parts_mismatch({"title": "Forecasts", "columns": ten, "rows": [[]] * 6}, halves) is None
    gap = copy.deepcopy(BALANCED_PARTS)
    gap[2]["part"]["label"], gap[2]["headers"] = "Columns 15–19 of 24", ["C1", *NAMES[14:19]]
    assert "parts show columns" in failing(wide_observation(gap))["sections-and-blocks"]


MONTE_CARLO = Fixture(
    "monte-carlo",
    "x",
    expect={
        "absent_text": ["retired post-run output contract"],
        "notices": {"unavailable": 1},
        "sections_with_tables": ["Rows"],
    },
)
REASON = "This run was admitted under the retired post-run output contract. Its saved results remain readable."


def failed_text_run() -> Profile:
    run = copy.deepcopy(PROFILE)
    run.ai_text_unavailable = True
    run.ai_text_errors = [REASON]
    return run


def unavailable_observation() -> dict:
    obs = observation()
    obs["export"]["notices"] = [{"kind": "notice", "reason": "unavailable", "text": checks.AI_TEXT_UNAVAILABLE_NOTICE}]
    obs["dom"]["notices"] = [checks.AI_TEXT_UNAVAILABLE_NOTICE]
    obs["article_text"] = "Election returns\nAI summary not available for this run.\nOverview"
    obs["pdf_text"] = "Election returns AI summary not available for this run."
    return obs


def test_failed_ai_text_prints_one_unavailable_notice_and_no_internal_reason() -> None:
    result = {r.check: r for r in checks.evaluate(unavailable_observation(), MONTE_CARLO, FORMATS, failed_text_run())}
    for name in ("ai-text-notices", "no-internal-reasons", "fixture-expectations", "export-data"):
        assert result[name].status == "pass", (name, result[name].detail)
    assert "1 unavailable notice(s)" in result["fixture-expectations"].detail


def test_a_leaked_backend_reason_or_a_missing_or_doubled_notice_fails() -> None:
    leaked = unavailable_observation()
    leaked["pdf_text"] += "\nThis run was admitted under the retired post-run\noutput contract."
    found = {r.check: r for r in checks.evaluate(leaked, MONTE_CARLO, FORMATS, failed_text_run())}["no-internal-reasons"]
    assert found.status == "fail" and "PDF contains 'This run was admitted under the retired post-run output" in found.detail
    partial = unavailable_observation()
    partial["article_text"] += " Its saved results remain readable"
    assert "page contains 'Its saved results remain readable'" in failing(partial, failed_text_run())["no-internal-reasons"]
    missing = observation()
    detail = {r.check: r.detail for r in checks.evaluate(missing, MONTE_CARLO, FORMATS, failed_text_run()) if r.status == "fail"}
    assert "expected one 'unavailable' notice, found 0" in detail["ai-text-notices"]
    assert "no 'unavailable' notice" in detail["export-data"]
    assert "0 'unavailable' notices, expected 1" in detail["fixture-expectations"]
    doubled = unavailable_observation()
    doubled["export"]["sections"][0]["blocks"].append({"kind": "notice", "reason": "unavailable", "text": checks.AI_TEXT_UNAVAILABLE_NOTICE})
    assert "found 2" in failing(doubled, failed_text_run())["ai-text-notices"]
    wrong = unavailable_observation()
    wrong["export"]["notices"][0]["text"] = checks.USAGE_LIMIT_NOTICE
    assert "notice 'unavailable' reads" in failing(wrong, failed_text_run())["ai-text-notices"]


def test_an_expected_section_without_tables_fails() -> None:
    obs = unavailable_observation()
    obs["export"]["sections"][1]["blocks"] = [b for b in obs["export"]["sections"][1]["blocks"] if b["kind"] != "table"]
    obs["dom"]["sections"][1]["units"].pop(0)
    detail = {r.check: r.detail for r in checks.evaluate(obs, MONTE_CARLO, FORMATS, failed_text_run()) if r.status == "fail"}
    assert "section 'Rows' exports 0 tables with rows and prints 0" in detail["fixture-expectations"]


def test_the_monte_carlo_fixture_carries_its_expectations() -> None:
    expect = load_catalog().get("monte-carlo").expect
    assert expect["notices"] == {"unavailable": 1}
    assert "retired post-run output contract" in expect["absent_text"]
    assert expect["sections_with_tables"] == ["Method and statistical checks"]
    profile_json = json.loads(json.dumps(failed_text_run().to_json()))
    assert Profile.from_json(profile_json).ai_text_errors == [REASON]


# --- server-made PDFs (Platform's export routes) ---------------------------------------------------

from tools.export_e2e import server_pdf  # noqa: E402

EXPORT_ID = "0190a1b2-c3d4-7e5f-8a6b-1c2d3e4f5a6b"
SIGNED = "https://exports.example.test/exports/owner/x/export.pdf?X-Amz-Signature=secret"
PDF_BYTES = b"%PDF-1.7 /Type /Pages /Count 2 /Type /Page /Type /Page %%EOF"
FILE_NAME = "Election returns – 16 Nov 2026.pdf"


def exported(state: str, **fields: object) -> dict:
    return {"export_id": EXPORT_ID, "execution_id": RUN, "format": "pdf", "paper": "a4", "state": state, **fields}


READY = exported("ready", file_name=FILE_NAME, download_url=SIGNED, download_expires_at="2026-11-19T09:05:00Z")


class Clock:
    """Monotonic time that only moves when the poll sleeps."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeExports:
    """Platform's routes with scripted answers; records every call in order."""

    def __init__(self, post: server_pdf.Answer, reads: list[server_pdf.Answer] | None = None, delete: int = 204):
        self.post, self.reads, self.delete_status = post, list(reads or []), delete
        self.calls: list[str] = []

    def create(self, run_id: str, paper: str = "a4") -> server_pdf.Answer:
        self.calls.append(f"POST {paper}")
        return self.post

    def get(self, export_id: str) -> server_pdf.Answer:
        self.calls.append("GET")
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]

    def cancel(self, export_id: str) -> server_pdf.Answer:
        self.calls.append("DELETE")
        return server_pdf.Answer(self.delete_status)


def ok(body: dict) -> server_pdf.Answer:
    return server_pdf.Answer(200, body)


ACCEPTED = server_pdf.Answer(202, exported("queued"))
NOT_DEPLOYED = server_pdf.Answer(404, message="Not found")


def test_the_poll_reads_every_two_seconds_and_records_each_state_with_its_time() -> None:
    clock = Clock()
    reads = [ok(exported("queued")), server_pdf.Answer(500, message="The export's result could not be read"), ok(READY)]
    final, record = server_pdf.poll(FakeExports(ACCEPTED, reads).get, exported("queued"), began=clock(), clock=clock, sleep=clock.sleep)
    assert final["state"] == "ready" and clock.sleeps == [2.0, 2.0, 2.0]
    assert record["transitions"] == [{"state": "queued", "at_s": 0.0}, {"state": "ready", "at_s": 6.0}]
    assert record["polls"] == 3 and record["outcome"] == "final"
    assert record["errors"] == [{"status": 500, "message": "The export's result could not be read", "at_s": 4.0}]


def test_the_poll_stops_at_two_minutes_or_when_the_export_is_gone() -> None:
    clock = Clock()
    final, record = server_pdf.poll(FakeExports(ACCEPTED, [ok(exported("queued"))]).get, exported("queued"), began=clock(), clock=clock, sleep=clock.sleep)
    assert record["outcome"] == "timeout" and record["polls"] == 60 and record["elapsed_s"] == 120.0
    assert final["state"] == "queued"
    clock = Clock()
    _, gone = server_pdf.poll(FakeExports(ACCEPTED, [server_pdf.Answer(404, message="Export not found")]).get, exported("queued"), began=clock(), clock=clock, sleep=clock.sleep)
    assert gone["outcome"] == "gone" and gone["polls"] == 1


def fetched(data: bytes = PDF_BYTES, **info: object):  # noqa: ANN201
    def fetch(url: str) -> tuple[dict, bytes]:
        assert url == SIGNED
        return {"status": 200, "content_type": "application/pdf", "content_disposition": 'attachment; filename="x.pdf"', "bytes": len(data), **info}, data

    return fetch


def made(tmp_path: Path, exports: FakeExports, fetch=None, text: str | None = "text") -> dict:  # noqa: ANN001
    clock = Clock()
    return server_pdf.export_run(
        exports, RUN, tmp_path, clock=clock, sleep=clock.sleep, fetch=fetch or fetched(), text=lambda _path: text
    )


def server_obs(server: dict, **more: object) -> dict:
    obs = observation()
    obs["server_pdf"] = server
    obs.update(more)
    return obs


def server_results(obs: dict, run: Profile | None = PROFILE) -> dict[str, checks.Result]:
    return {r.check: r for r in checks.evaluate(obs, FIXTURE, FORMATS, run) if r.check.startswith("pdf-server")}


def test_a_ready_server_pdf_is_downloaded_checked_and_kept_without_its_signed_address(tmp_path: Path) -> None:
    exports = FakeExports(ACCEPTED, [ok(exported("queued")), ok(READY)])
    server = made(tmp_path, exports)
    assert exports.calls == ["POST a4", "GET", "GET"]
    assert (tmp_path / "server-export-a4.pdf").read_bytes() == PDF_BYTES
    assert server["download"]["pages"] == 2 and server["download"]["pdf_header"] is True
    assert server["export"]["download_url"] == server_pdf.REDACTED
    assert "X-Amz-Signature" not in json.dumps(server) and "secret" not in json.dumps(server)
    result = server_results(server_obs(server))["pdf-server"]
    assert result.status == "pass", result.detail
    assert result.detail.startswith("queued 0.0s → ready 4.0s (2 polls); 2 pages") and repr(FILE_NAME) in result.detail


@pytest.mark.parametrize(
    ("post", "detail"),
    [
        (NOT_DEPLOYED, "server export not deployed (POST 404 'Not found')"),
        (server_pdf.Answer(404), "server export not deployed (POST 404)"),  # A load balancer's page.
        (server_pdf.Answer(503, message="Exports are not available"), "unavailable (POST 503 'Exports are not available')"),
        (server_pdf.Answer(429, message="You've made 20 exports this hour. Try again later.", retry_after="60"), "hourly export limit reached"),
    ],
)
def test_a_service_that_isnt_live_is_a_skip_not_a_failure(tmp_path: Path, post: server_pdf.Answer, detail: str) -> None:
    exports = FakeExports(post)
    obs = server_obs(made(tmp_path, exports))
    obs["server_pdf_cancel"] = server_pdf.cancel_probe(exports, RUN)
    results = server_results(obs)
    assert results["pdf-server"].status == "skip" and results["pdf-server"].detail.startswith(detail)
    assert results["pdf-server-cancel"].status == "skip" and results["pdf-server-cancel"].detail.startswith(detail)
    assert results["pdf-server-text"].status == "skip"
    assert exports.calls == ["POST a4", "POST a4"]  # Nothing more is asked of a refused export.
    assert checks.outcome(checks.evaluate(obs, FIXTURE, FORMATS, PROFILE)) == "pass"


def test_platform_not_finding_the_owners_run_or_an_unexpected_answer_fails(tmp_path: Path) -> None:
    assert server_results(server_obs(made(tmp_path, FakeExports(server_pdf.Answer(404, message="Run not found")))))["pdf-server"].status == "fail"
    assert server_results(server_obs(made(tmp_path, FakeExports(server_pdf.Answer(409, message="The run hasn't completed")))))["pdf-server"].status == "fail"
    refused = server_results(server_obs(made(tmp_path, FakeExports(server_pdf.Answer(422, message="This run can't be exported on the server")))))
    assert refused["pdf-server"].status == "skip" and "prints it in the browser" in refused["pdf-server"].detail


def test_a_slow_failed_or_wrong_server_pdf_fails(tmp_path: Path) -> None:
    exports = FakeExports(ACCEPTED, [ok(exported("queued"))])
    slow = made(tmp_path, exports)
    assert exports.calls[-1] == "DELETE" and slow["cancelled_after_timeout"] == {"status": 204}
    assert "Not final within 120s" in server_results(server_obs(slow))["pdf-server"].detail
    failure = {"reason": "render_failed", "message": "The export couldn’t be made. Try again."}
    failed = made(tmp_path, FakeExports(ACCEPTED, [ok(exported("failed", failure=failure))]))
    assert "Ended failed 'render_failed'" in server_results(server_obs(failed))["pdf-server"].detail
    named = made(tmp_path, FakeExports(ACCEPTED, [ok({**READY, "file_name": "Election returns.pdf"})]))
    assert f"expected {FILE_NAME!r}" in server_results(server_obs(named))["pdf-server"].detail
    html = made(tmp_path, FakeExports(ACCEPTED, [ok(READY)]), fetch=fetched(b"<html>"))
    assert "not a PDF" in server_results(server_obs(html))["pdf-server"].detail
    huge = made(tmp_path, FakeExports(ACCEPTED, [ok(READY)]), fetch=fetched(over_limit=True))
    assert "50 MB or more" in server_results(server_obs(huge))["pdf-server"].detail
    inline = made(tmp_path, FakeExports(ACCEPTED, [ok(READY)]), fetch=fetched(content_disposition="inline"))
    assert "expected an attachment" in server_results(server_obs(inline))["pdf-server"].detail


def test_a_run_without_a_dashboard_is_refused_or_fails_cleanly(tmp_path: Path) -> None:
    empty = run_profile(RUN, categories=[])

    def judged(exports: FakeExports) -> checks.Result:
        obs = {"run_id": RUN, "study_id": STUDY, "menu": {"found": False, "kind": "placeholder"}, "print": {"ready": "error"}, "server_pdf": made(tmp_path, exports)}
        found = {r.check: r for r in checks.evaluate(obs, NO_DASHBOARD, FORMATS, empty)}
        assert "pdf-server-text" not in found
        return found["pdf-server"]

    assert judged(FakeExports(server_pdf.Answer(422, message="This run can't be exported on the server"))).status == "pass"
    no_dashboard = exported("failed", failure={"reason": "no_dashboard", "message": "x"})
    assert judged(FakeExports(ACCEPTED, [ok(no_dashboard)])).status == "pass"
    printed = judged(FakeExports(ACCEPTED, [ok(READY)]))
    assert printed.status == "fail" and "ended ready" in printed.detail
    assert judged(FakeExports(NOT_DEPLOYED)).status == "skip"


BROWSER_TEXT = "Exported from Epoch on 18 Nov 2026\nElection returns\nOVERVIEW\nKEY CHARTS\nClose\n\x0cROWS\nAll rows\nA B\n0 0,000\n"


def test_the_server_pdf_has_the_browser_pdfs_headings_and_table_titles_in_order(tmp_path: Path) -> None:
    obs = server_obs(made(tmp_path, FakeExports(ACCEPTED, [ok(READY)]), text="Election returns OVERVIEW\x0c KEY\nCHARTS Close ROWS\x0cAll\nrows"))
    obs["pdf_text"] = BROWSER_TEXT
    assert checks.outline(obs["export"]) == ["Overview", "Key charts", "Rows", "All rows"]
    result = server_results(obs)["pdf-server-text"]
    assert result.status == "pass" and result.detail.startswith("4 section headings and table titles")
    obs["server_pdf"]["text"] = "Election returns ROWS All rows OVERVIEW KEY CHARTS"
    result = server_results(obs)["pdf-server-text"]
    assert result.status == "fail" and "first difference at #3: browser 'Rows', server None" in result.detail
    assert "not in order in the server PDF: ['Rows', 'All rows']" in result.detail
    obs["server_pdf"]["text"] = "OVERVIEW KEY CHARTS ROWS"
    assert "not in order in the server PDF: ['All rows']" in server_results(obs)["pdf-server-text"].detail
    obs["pdf_text"] = None
    assert "pdftotext" in server_results(obs)["pdf-server-text"].detail


def test_a_queued_export_is_cancelled_with_no_read_before_the_delete() -> None:
    exports = FakeExports(ACCEPTED, [ok(exported("cancelled"))])
    probe = server_pdf.cancel_probe(exports, RUN)
    assert exports.calls == ["POST a4", "DELETE", "GET"]
    obs = observation() | {"server_pdf_cancel": probe}
    assert server_results(obs)["pdf-server-cancel"].status == "pass"
    stuck = server_pdf.cancel_probe(FakeExports(ACCEPTED, [ok(READY)]), RUN)
    assert "expected 'cancelled'" in server_results(observation() | {"server_pdf_cancel": stuck})["pdf-server-cancel"].detail
    refused = server_pdf.cancel_probe(FakeExports(ACCEPTED, [ok(exported("cancelled"))], delete=500), RUN)
    assert "DELETE answered 500" in server_results(observation() | {"server_pdf_cancel": refused})["pdf-server-cancel"].detail


def test_another_owners_read_is_skipped_without_a_second_identity() -> None:
    alone = server_results(observation() | {"server_pdf_other_owner": server_pdf.other_owner_probe(None, EXPORT_ID)})
    assert alone["pdf-server-other-owner"].status == "skip" and "one owner" in alone["pdf-server-other-owner"].detail
    for status, outcome in ((404, "pass"), (200, "fail")):
        other = FakeExports(ACCEPTED, [server_pdf.Answer(status)])
        probe = server_pdf.other_owner_probe(other, EXPORT_ID)  # type: ignore[arg-type]
        assert server_results(observation() | {"server_pdf_other_owner": probe})["pdf-server-other-owner"].status == outcome


PREPARING = [{"text": checks.PREPARING_TOAST, "at_ms": 40}]
PRINT_TAB = {"path": f"/print/studies/{STUDY}", "run": RUN}


@pytest.mark.parametrize(
    ("ui", "status", "detail"),
    [
        ({"build": "print-link", "popup": PRINT_TAB, "toasts": []}, "skip", "server export not deployed: this Terminal build has no server PDF"),
        ({"build": "print-link", "popup": {"path": "/studies/x", "run": None}, "toasts": []}, "fail", "not the run's print layout"),
        ({"build": "server", "post_status": 404, "post_message": "Not found", "popup": PRINT_TAB, "toasts": PREPARING}, "skip", "server export not deployed (POST 404 'Not found'): “Preparing your PDF…”, then the print layout opened"),
        ({"build": "server", "post_status": 503, "popup": PRINT_TAB, "toasts": PREPARING}, "skip", "unavailable (POST 503)"),
        ({"build": "server", "post_status": 422, "popup": PRINT_TAB, "toasts": PREPARING}, "skip", "the run can't be exported on the server"),
        ({"build": "server", "post_status": 404, "toasts": PREPARING}, "fail", "the print layout didn't open"),
        ({"build": "server", "post_status": 404, "popup": PRINT_TAB, "toasts": []}, "fail", "no “Preparing your PDF…” toast"),
        ({"build": "server", "post_status": 202, "toasts": PREPARING, "waited_s": 130, "download": {"suggested_filename": FILE_NAME, "bytes": 900, "pdf_header": True, "at_s": 9.1}}, "pass", f"then {FILE_NAME!r} downloaded"),
        ({"build": "server", "post_status": 202, "toasts": PREPARING, "waited_s": 130}, "fail", "no download within 130s"),
        ({"build": "server", "post_status": 202, "toasts": PREPARING, "download": {"suggested_filename": "x.pdf", "pdf_header": False}}, "fail", "is not a PDF"),
        ({"build": "server", "post_status": 202, "toasts": PREPARING, "download": {"suggested_filename": "x.pdf", "failure": "canceled"}}, "fail", "failed: canceled"),
        ({"error": "The study page showed no Export menu within 120s"}, "fail", "no Export menu"),
    ],
)
def test_export_pdf_in_the_terminal_downloads_or_falls_back_to_the_print_tab(ui: dict, status: str, detail: str) -> None:
    obs = observation() | {"server_pdf_ui": {"run_id": RUN, "study_id": STUDY, **ui}}
    result = server_results(obs)["pdf-server-ui"]
    assert result.status == status and detail in result.detail, result.detail


def test_a_server_pdf_menu_item_is_an_action_not_a_print_link() -> None:
    obs = observation()
    obs["menu"]["items"][0] = {"label": "PDF", "description": checks.PDF_DESCRIPTION, "href": None, "target": None, "rel": None}
    menu = next(r for r in checks.evaluate(obs, FIXTURE, FORMATS, PROFILE) if r.check == "export-menu")
    assert menu.status == "pass" and "made on the server" in menu.detail


def test_an_observation_recorded_before_server_pdfs_gains_no_server_rows() -> None:
    assert server_results(observation()) == {}


def test_the_expected_file_name_is_platforms_cleaned_title_and_utc_run_date() -> None:
    assert checks.file_title(' Q3/Q4: "CPI" <impact>?. ') == "Q3 Q4 CPI impact"
    assert checks.file_title("...") == "Study" and len(checks.file_title("x" * 300)) == 120
    assert checks.file_title("a‮b\u0007c") == "a b c"
    assert checks.expected_file_name(export_data()) == FILE_NAME


def test_route_errors_keep_platforms_message_and_never_the_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(404, json={"error": "DataNotFound", "message": "Not found", "timestamp": "t"})
        raise httpx.ConnectError(f"cannot reach {SIGNED}", request=request)

    with httpx.Client(base_url="https://api.example.test", transport=httpx.MockTransport(handler)) as client:
        exports = server_pdf.Exports(client.request)
        assert exports.create(RUN).record() == {"status": 404, "message": "Not found"}
        assert exports.get(EXPORT_ID).record() == {"status": 0, "error": "ConnectError"}
    assert server_pdf.withhold_urls(f"Timeout waiting for {SIGNED}") == "Timeout waiting for [URL withheld]"


def test_the_file_is_downloaded_without_the_bearer_and_never_past_50_mb() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=PDF_BYTES, headers={"content-disposition": "attachment"})

    info, data = server_pdf.download(SIGNED, transport=httpx.MockTransport(handler))
    assert data == PDF_BYTES and info["status"] == 200 and "authorization" not in seen[0].headers
    info, data = server_pdf.download(SIGNED, limit=10, transport=httpx.MockTransport(handler))
    assert info["over_limit"] is True


def test_the_report_lists_server_pdf_results_and_the_cost_stays_honest() -> None:
    posts = [{"fixture": "a", "via": "api", "status": 404}, {"fixture": "b", "via": "api", "status": 404}, {"fixture": "a", "via": "api (cancel)", "status": 202}]
    statement = server_pdf.cost_statement(posts)
    assert "asked Platform for 3 exports (1× 202, 2× 404); 1 were made" in statement
    assert "makes no model call and creates no run" in statement and server_pdf.cost_statement([]) == ""
    assert "(1× no answer)" in server_pdf.cost_statement([{"fixture": "a", "via": "api", "status": 0}])
    obs = server_obs({"paper": "a4", "post": {"status": 404, "message": "Not found"}})
    results = [r.to_json() for r in checks.evaluate(obs, FIXTURE, FORMATS, PROFILE)]
    text = report.render(
        {
            "attempt": "a", "environment": "dev", "ui_url": "https://app", "backend_url": "https://api", "started_at": "t",
            "fixtures": [{"id": "long-table", "shape": "x", "run_id": RUN, "origin": "reused", "outcome": "pass", "results": results}],
            "cost": {"statement": "This attempt created no run." + statement},
        }
    )
    assert "| pdf-server | skip |" in text and "| pdf-server-text | skip |" in text
    assert "| long-table | pdf-server | skip | server export not deployed (POST 404 'Not found') |" in text
    assert "creates no run." in text
