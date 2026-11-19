"""The export suite's pure parts: catalog, run profiles, Arrow row counts, state and checks."""

from __future__ import annotations

import copy
import json
import struct
from pathlib import Path

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

    def validate(self, source, start, end, assets):  # noqa: ANN001, ANN201
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

    def wait(self, run_id):  # noqa: ANN001, ANN201
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
    assert "no notice prints" in detail["usage-limit-notice"]
    assert "the export has no notice" in detail["export-data"]
    obs = observation()
    notice = {"kind": "notice", "reason": "usage_limit", "text": checks.USAGE_LIMIT_NOTICE}
    obs["export"]["notices"] = [notice]
    obs["dom"]["notices"] = [checks.USAGE_LIMIT_NOTICE]
    assert statuses(obs, run=skipped)["usage-limit-notice"] == "pass"
    obs["dom"]["notices"] = []
    assert "page notices []" in failing(obs, skipped)["usage-limit-notice"]


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


def test_a_run_without_a_dashboard_should_report_nothing_to_print() -> None:
    empty = run_profile(RUN, name="Text answer", categories=[])
    obs = observation()
    obs["export"]["sections"] = []
    obs["dom"]["sections"] = []
    result = checks.evaluate(obs, Fixture("no-dashboard", "x", match={"has_results": False}), FORMATS, empty)
    nothing = next(r for r in result if r.check == "nothing-to-print")
    assert nothing.status == "fail" and "cover-only PDF (6 A4 pages)" in nothing.detail
    obs["print"] = {"ready": "error", "elapsed_s": 1.0, "message": "Nothing to print"}
    result = checks.evaluate(obs, Fixture("no-dashboard", "x"), FORMATS, empty)
    assert {r.check: r.status for r in result}["nothing-to-print"] == "pass"


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
