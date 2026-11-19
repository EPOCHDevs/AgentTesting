"""Judges one recorded observation of a study's export. Pure: JSON in, results out.

The expected texts are the Terminal's own (EpochClientPortal StudyExportMenu,
StudyPrintPage, PrintLayout and StudyPage/lib/export.ts). The browser runs in UTC,
so dates are formatted in UTC here too.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .catalog import Fixture, Format, Profile

MENU_LABEL = "Export this result"
MENU_NOTE = "A file is a copy: it can’t be updated. To keep people up to date, share the conversation."
PDF_DESCRIPTION = "The dashboard as pages, ready to print"
PRINT_ROW_LIMIT = 200
USAGE_LIMIT_NOTICE = "AI summary not generated: usage limit reached"
AI_TEXT_UNAVAILABLE_NOTICE = "AI summary not available for this run."
# Each notice's text, by its reason. "unavailable" is one notice for the run, under the cover.
NOTICE_TEXT = {"usage_limit": USAGE_LIMIT_NOTICE, "unavailable": AI_TEXT_UNAVAILABLE_NOTICE}
CHART_FAILED = "This chart couldn’t load."
GRAPH_MESSAGE = "Relationship graphs aren’t included in exports yet."
NO_DASHBOARD_MESSAGE = "This run has no dashboard to export."
# A wide table may print in column parts, each labelled with the columns it shows.
COLUMNS_LABEL = re.compile(r"^Columns? (\d+)(?:–(\d+))? of (\d+)$")
# A chart image is blank when it is (almost) one colour.
MIN_NON_BACKGROUND = 0.002
MIN_DISTINCT_COLOURS = 3
# PDF page sizes in points, as Chrome writes them.
PAPER_POINTS = {"A4": (595.92, 842.88), "Letter": (612.0, 792.0)}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Server-made PDFs (EpochPlatform docs/contracts/exports.md; the Terminal's pdfExport).
PREPARING_TOAST = "Preparing your PDF…"
NOT_DEPLOYED = "server export not deployed"
UNAVAILABLE = "unavailable"
LIMIT_REACHED = "hourly export limit reached"
# Platform's own 404 for a run that doesn't exist or isn't the caller's. Any other 404 to POST is a
# route that isn't there yet (Platform's "Not found", or a load balancer's), as the Terminal reads it.
RUN_NOT_FOUND = "Run not found"
SERVER_MAX_BYTES = 50 * 1024 * 1024
SERVER_TIMEOUT_S = 120
# Platform's exportFileName: the title cleaned for every file system, at most 120 characters.
FILE_NAME_CONTROLS = re.compile("[\u0000-\u001f\u007f-\u009f؜​-‏ -‮⁠-⁯﻿]")

PASS, FAIL, SKIP = "pass", "fail", "skip"


@dataclass
class Result:
    check: str
    status: str
    detail: str
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"check": self.check, "status": self.status, "detail": self.detail}
        if self.notes:
            value["notes"] = self.notes
        return value


def day(value: date | datetime) -> str:
    """dayjs "D MMM YYYY"."""
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def format_period(period: Mapping[str, str] | None) -> str | None:
    """The Terminal's `formatPeriod` of an API period ("2 Jan 2024 – 28 Mar 2024")."""
    if not period or not period.get("start") or not period.get("end"):
        return None
    start, end = (date.fromisoformat(period[k][:10]) for k in ("start", "end"))
    return f"{day(start)} – {day(end)}"


def expected_title(export: Mapping[str, object]) -> str:
    study = export["study"]
    assert isinstance(study, Mapping)
    return f"{study['title']} – {day(parse_instant(str(study['runDate'])))}"


def units(blocks: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """PrintLayout's `units`: each heading with the block after it."""
    result: list[dict[str, object]] = []
    for block in blocks:
        last = result[-1] if result else None
        if block.get("kind") == "heading":
            result.append({"heading": block})
        elif last is not None and "heading" in last and "block" not in last:
            last["block"] = block
        else:
            result.append({"block": block})
    return result


def all_blocks(export: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [
        block
        for section in export.get("sections") or []  # type: ignore[union-attr]
        for block in section.get("blocks") or []
    ]


def notices(export: Mapping[str, object], reason: str | None = None) -> list[Mapping[str, object]]:
    """The export's AI text notices, at the top and in sections; older exports have no reason."""
    found = list(export.get("notices") or [])  # type: ignore[arg-type]
    found += [b for b in all_blocks(export) if b.get("kind") == "notice"]
    return [n for n in found if reason is None or n.get("reason", "usage_limit") == reason]


def norm(value: object) -> object:
    """Text as a page shows it: whitespace collapsed and trimmed."""
    return " ".join(value.split()) if isinstance(value, str) else value


def _image(block: Mapping[str, object]) -> Mapping[str, object] | None:
    image = block.get("image")
    return image if isinstance(image, Mapping) else None


def _block_mismatch(expected: Mapping[str, object], seen: Mapping[str, object] | None) -> str | None:
    """Why a rendered block does not show the export block, or None when it does."""
    kind = expected.get("kind")
    if seen is None:
        return f"{kind} block missing"
    title = norm(expected.get("title"))
    if kind == "chart":
        figure = seen.get("figure")
        if not isinstance(figure, Mapping):
            return f"chart {title!r} is not drawn as a figure"
        if figure.get("title") != title:
            return f"chart title {figure.get('title')!r}, expected {title!r}"
        image = _image(expected)
        drawn = figure.get("image")
        if image:
            if not isinstance(drawn, Mapping):
                return f"chart {title!r} has no image"
            if drawn.get("sha256") != image.get("sha256"):
                return f"chart {title!r} shows a different image than the export data"
        elif drawn:
            return f"chart {title!r} shows an image the export data does not have"
        else:
            message = norm(expected.get("message") or CHART_FAILED)
            if figure.get("message") != message:
                return f"chart {title!r} says {figure.get('message')!r}, expected {message!r}"
        return None
    if kind == "table":
        if title not in (seen.get("h4") or []):
            return f"table {title!r} has no title"
        if expected.get("message"):
            if norm(expected["message"]) not in (seen.get("ps") or []):
                return f"table {title!r} does not say {expected['message']!r}"
            return None
        tables = seen.get("tables") or []
        if not tables:
            return f"table {title!r} is not drawn"
        return _table_parts_mismatch(expected, tables)
    if kind == "metrics":
        metrics = seen.get("metrics")
        count = len(expected.get("metrics") or [])  # type: ignore[arg-type]
        if not isinstance(metrics, Mapping) or len(metrics.get("labels") or []) != count:
            return f"metrics {title or ''!r}: expected {count} values"
        return None
    if kind == "list":
        count = len(expected.get("items") or [])  # type: ignore[arg-type]
        if seen.get("list_items") != count:
            return f"briefing {title or ''!r} lists {seen.get('list_items')} items, expected {count}"
        return None
    if kind == "notice":
        if seen.get("tag") != "p" or seen.get("text") != norm(expected.get("text")):
            return f"notice {expected.get('text')!r} not shown"
        return None
    if kind == "text":
        if not seen.get("text"):
            return "text block is empty"
        if title and title not in (seen.get("h4") or []):
            return f"text {title!r} has no title"
        return None
    return f"unknown block kind {kind!r}"


def _table_parts_mismatch(
    expected: Mapping[str, object], tables: Sequence[Mapping[str, object]]
) -> str | None:
    """A table printed whole, or in column parts: each part the first column plus its own slice,
    labelled "Columns a–b of N", together showing every column once, each printing the rows."""
    title = norm(expected.get("title"))
    names = [norm(c.get("name")) for c in expected.get("columns") or []]  # type: ignore[union-attr]
    shown = min(len(expected.get("rows") or []), PRINT_ROW_LIMIT) or 1  # type: ignore[arg-type]  # "No rows to show."

    def columns_differ(drawn: Mapping[str, object], want: list[object]) -> str | None:
        if "headers" in drawn:
            return None if drawn["headers"] == want else f"columns {drawn['headers']}, expected {want}"
        count = drawn.get("header_cells")
        return None if count == len(want) else f"{count} columns, expected {len(want)}"

    labels = [(t.get("part") or {}).get("label") for t in tables]
    if len(tables) == 1 and not labels[0]:
        wrong = columns_differ(tables[0], names)
        if wrong:
            return f"table {title!r} has {wrong}"
        if tables[0].get("body_rows") != shown:
            return f"table {title!r} prints {tables[0].get('body_rows')} rows, expected {shown}"
        return None
    covered: list[int] = []
    for index, (drawn, label) in enumerate(zip(tables, labels), start=1):
        found = COLUMNS_LABEL.match(label or "")
        if not found:
            return f"table {title!r} part {index} has no 'Columns a–b of {len(names)}' label"
        first, last, total = int(found[1]), int(found[2] or found[1]), int(found[3])
        if total != len(names) or not 1 <= first <= last <= len(names):
            return f"table {title!r} part {label!r} doesn't fit its {len(names)} columns"
        part_title = (drawn.get("part") or {}).get("title")  # type: ignore[union-attr]
        if part_title != title:
            return f"table {title!r} part {label!r} is titled {part_title!r}"
        own = names[first - 1 : last]
        wrong = columns_differ(drawn, own if first == 1 else [names[0], *own])
        if wrong:
            return f"table {title!r} part {label!r} has {wrong}"
        if drawn.get("body_rows") != shown:
            return f"table {title!r} part {label!r} prints {drawn.get('body_rows')} rows, expected {shown}"
        covered.extend(range(first, last + 1))
    if covered != list(range(1, len(names) + 1)):
        return f"table {title!r} parts show columns {covered}, not 1 to {len(names)} once each"
    return None


def check_no_dashboard_menu(obs: Mapping[str, object]) -> Result:
    """A run that published no dashboard offers no PDF: the placeholder, or no Export at all."""
    menu = obs.get("menu") or {}
    assert isinstance(menu, Mapping)
    kind = menu.get("kind") or ("menu" if menu.get("found") else "absent")
    if kind == "placeholder":
        return Result("export-menu", PASS, "No dashboard: Export is the “isn’t available yet” placeholder")
    if kind == "absent":
        return Result("export-menu", PASS, "No dashboard: the study page shows no Export")
    offered = ", ".join(str(i.get("label")) for i in menu.get("items") or [])  # type: ignore[union-attr]
    return Result("export-menu", FAIL, f"The run published no dashboard, yet Export offers {offered or 'a menu'}")


def check_menu(obs: Mapping[str, object], formats: Sequence[Format]) -> Result:
    menu = obs.get("menu") or {}
    assert isinstance(menu, Mapping)
    if not menu.get("found"):
        return Result("export-menu", FAIL, str(menu.get("error") or "No Export menu"))
    problems = []
    if menu.get("label") != MENU_LABEL:
        problems.append(f"label {menu.get('label')!r}")
    if menu.get("note") != MENU_NOTE:
        problems.append(f"note {menu.get('note')!r}")
    items = list(menu.get("items") or [])  # type: ignore[arg-type]
    known = {f.menu_label for f in formats}
    unknown = [i.get("label") for i in items if i.get("label") not in known]
    if unknown:
        problems.append(f"unrecognised items {unknown}: add a format check for them")
    pdf = next((i for i in items if i.get("label") == "PDF"), None)
    expected = f"/print/studies/{obs['study_id']}?run={obs['run_id']}"
    if pdf is None:
        problems.append("no PDF item")
    else:
        if pdf.get("description") != PDF_DESCRIPTION:
            problems.append(f"PDF description {pdf.get('description')!r}")
        # A Terminal with server PDFs makes PDF an action, not a link (pdf-server-ui clicks it).
        if pdf.get("href") is not None:
            if pdf.get("href") != expected:
                problems.append(f"PDF opens {pdf.get('href')!r}, expected {expected!r}")
            if pdf.get("target") != "_blank" or "noopener" not in str(pdf.get("rel") or ""):
                problems.append("PDF does not open in a new tab without an opener")
    offered = ", ".join(str(i.get("label")) for i in items)
    if problems:
        return Result("export-menu", FAIL, "; ".join(problems))
    if pdf and pdf.get("href") is None:
        return Result("export-menu", PASS, f"Offers {offered}; note correct; PDF is made on the server (no print link)")
    return Result("export-menu", PASS, f"Offers {offered}; note and print link correct")


def check_formats(obs: Mapping[str, object], formats: Sequence[Format]) -> list[Result]:
    offered = obs.get("formats") or {}
    assert isinstance(offered, Mapping)
    results = []
    for fmt in formats:
        name = f"format-{fmt.id}"
        if not (offered.get(fmt.id) or {}).get("offered"):
            results.append(Result(name, SKIP, f"{fmt.menu_label} is not offered by this build"))
        elif fmt.implemented:
            results.append(Result(name, PASS, f"{fmt.menu_label} offered and checked below"))
        else:
            results.append(
                Result(name, FAIL, f"{fmt.menu_label} is offered, but the suite has no check for it yet")
            )
    return results


def check_ready(obs: Mapping[str, object]) -> Result:
    printed = obs.get("print") or {}
    assert isinstance(printed, Mapping)
    ready = printed.get("ready")
    elapsed = printed.get("elapsed_s")
    if ready == "true":
        return Result("print-ready", PASS, f"data-export-ready=true after {elapsed}s")
    if ready == "error":
        return Result("print-ready", FAIL, f"data-export-ready=error: {printed.get('message')!r}")
    return Result("print-ready", FAIL, f"No data-export-ready within {elapsed}s: {printed.get('message')!r}")


def check_clean(obs: Mapping[str, object]) -> Result:
    page = obs.get("print_page") or {}
    assert isinstance(page, Mapping)
    console = list(page.get("console") or [])  # type: ignore[arg-type]
    failed = list(page.get("failed_requests") or [])  # type: ignore[arg-type]
    if console or failed:
        parts = [f"console: {c}" for c in console[:5]] + [
            f"{r.get('method')} {r.get('url')} {r.get('status') or r.get('error')}" for r in failed[:5]
        ]
        return Result(
            "print-page-clean", FAIL, f"{len(console)} console errors, {len(failed)} failed requests: " + " | ".join(parts)
        )
    return Result("print-page-clean", PASS, "No console errors or failed requests")


def check_no_ai(obs: Mapping[str, object]) -> Result:
    runs = list(obs.get("ai_runs") or [])  # type: ignore[arg-type]
    if runs:
        return Result("no-assistant-runs", FAIL, f"Started assistant runs: {runs[:3]}")
    return Result("no-assistant-runs", PASS, "No assistant run was started")


def check_export_data(obs: Mapping[str, object], run: Profile | None) -> Result:
    export = obs.get("export")
    if not isinstance(export, Mapping):
        return Result("export-data", FAIL, "No embedded export JSON")
    study = export.get("study") or {}
    assert isinstance(study, Mapping)
    problems = []
    if export.get("version") != 1:
        problems.append(f"version {export.get('version')!r}")
    if study.get("runId") != obs.get("run_id"):
        problems.append(f"runId {study.get('runId')!r}")
    if study.get("id") != obs.get("study_id"):
        problems.append(f"study id {study.get('id')!r}")
    if run is not None:
        title = run.name or "Untitled study"
        if study.get("title") != title:
            problems.append(f"title {study.get('title')!r}, the run's study is {title!r}")
        period = format_period(run.period)
        if study.get("period") != period:
            problems.append(f"period {study.get('period')!r}, the run's is {period!r}")
        if run.ai_text_skipped and not notices(export, "usage_limit"):
            problems.append("Backend skipped AI text for the usage limit, but the export has no notice")
        if notices(export, "usage_limit") and not run.ai_text_skipped:
            problems.append("the export has a usage-limit notice Backend did not record")
        if run.ai_text_unavailable and not notices(export, "unavailable"):
            problems.append("Backend has failed AI text, but the export has no 'unavailable' notice")
        if notices(export, "unavailable") and not run.ai_text_unavailable:
            problems.append("the export has an 'unavailable' notice, but Backend's AI text didn't fail")
    sections = export.get("sections") or []
    if problems:
        return Result("export-data", FAIL, "; ".join(problems))
    return Result(
        "export-data",
        PASS,
        f"version 1, {len(sections)} sections, {len(all_blocks(export))} blocks; identity and period match the run",  # type: ignore[arg-type]
    )


def check_sections(obs: Mapping[str, object]) -> Result:
    export, dom = obs.get("export"), obs.get("dom")
    if not isinstance(export, Mapping) or not isinstance(dom, Mapping) or not dom.get("found"):
        return Result("sections-and-blocks", FAIL, "The print layout was not rendered")
    sections = list(export.get("sections") or [])  # type: ignore[arg-type]
    drawn = list(dom.get("sections") or [])  # type: ignore[arg-type]
    problems = []
    if len(sections) != len(drawn):
        problems.append(f"{len(drawn)} sections printed, the export has {len(sections)}")
    for index, (section, seen) in enumerate(zip(sections, drawn)):
        where = f"section {index + 1} {section.get('title')!r}"
        if seen.get("title") != norm(section.get("title")):
            problems.append(f"{where} printed as {seen.get('title')!r}")
        if (norm(section.get("description")) or None) != (seen.get("description") or None):
            problems.append(f"{where} description differs")
        expected = units(section.get("blocks") or [])
        found = list(seen.get("units") or [])
        if len(expected) != len(found):
            problems.append(f"{where}: {len(found)} parts printed, expected {len(expected)}")
        for part, unit in zip(expected, found):
            heading = part.get("heading")
            if heading is not None:
                got = (unit.get("heading") or {}).get("text")
                if got != norm(heading.get("text")):  # type: ignore[union-attr]
                    problems.append(f"{where}: heading {got!r}, expected {heading.get('text')!r}")  # type: ignore[union-attr]
            if "block" in part:
                mismatch = _block_mismatch(part["block"], unit.get("block"))  # type: ignore[arg-type]
                if mismatch:
                    problems.append(f"{where}: {mismatch}")
            if unit.get("extra"):
                problems.append(f"{where}: unexpected extra elements in a part")
    total = len(all_blocks(export))
    if problems:
        more = f" (+{len(problems) - 8} more)" if len(problems) > 8 else ""
        return Result("sections-and-blocks", FAIL, "; ".join(problems[:8]) + more)
    split = [
        len(unit["block"]["tables"])
        for section in drawn
        for unit in section.get("units") or []
        if unit.get("block") and len(unit["block"].get("tables") or []) > 1
    ]
    parts = f"; {len(split)} wide tables printed in {sum(split)} column parts" if split else ""
    return Result("sections-and-blocks", PASS, f"All {len(sections)} sections and {total} blocks printed in order{parts}")


def check_images(obs: Mapping[str, object]) -> Result:
    export = obs.get("export")
    if not isinstance(export, Mapping):
        return Result("chart-images", SKIP, "No export data")
    charts = [b for b in all_blocks(export) if b.get("kind") == "chart"]
    if not charts:
        return Result("chart-images", SKIP, "The export has no charts")
    stats = list(obs.get("images") or [])  # type: ignore[arg-type]
    with_image = [c for c in charts if _image(c)]
    problems, notes = [], []
    if len(stats) != len(with_image):
        problems.append(f"{len(stats)} images printed, the export has {len(with_image)}")
    for chart, stat in zip(with_image, stats):
        title = chart.get("title")
        if not stat.get("decoded") or not stat.get("natural_width") or not stat.get("natural_height"):
            problems.append(f"{title!r} did not decode ({stat.get('error')})")
            continue
        if (stat.get("non_background") or 0) < MIN_NON_BACKGROUND or (
            stat.get("distinct_colors") or 0
        ) < MIN_DISTINCT_COLOURS:
            problems.append(
                f"{title!r} is blank ({stat.get('distinct_colors')} colours, {stat.get('non_background')} drawn)"
            )
        width, height = chart.get("width") or 0, chart.get("height") or 0
        if width and height:
            drawn = stat["natural_width"] / stat["natural_height"]
            if abs(drawn - width / height) > 0.02:  # type: ignore[operator]
                problems.append(f"{title!r} is {stat['natural_width']}x{stat['natural_height']}, not {width}:{height}")
    for chart in charts:
        if _image(chart):
            continue
        if str(chart.get("id", "")).startswith("graph:") and chart.get("message") == GRAPH_MESSAGE:
            notes.append(f"{chart.get('title')!r}: {GRAPH_MESSAGE} (documented limitation)")
        else:
            problems.append(f"{chart.get('title')!r} has no image: {chart.get('message')!r}")
    if problems:
        return Result("chart-images", FAIL, "; ".join(problems[:8]), notes)
    return Result("chart-images", PASS, f"{len(with_image)} chart images decoded and not blank", notes)


def check_overflow(obs: Mapping[str, object]) -> Result:
    overflow = obs.get("overflow")
    if not isinstance(overflow, Mapping):
        return Result("table-cells-fit", SKIP, "Not measured")
    tables = [t for paper in overflow.values() for t in paper]
    if not tables:
        return Result("table-cells-fit", SKIP, "The export has no tables")
    problems = []
    for paper, measured in overflow.items():
        for table in measured:
            name = (table.get("title") or f"table {table.get('index')}") + (
                f" ({table['label']})" if table.get("label") else ""
            )
            if table.get("overflowing_cells"):
                examples = ", ".join(
                    f"row {e['row']} col {e['column']} {e['text']!r} +{e['overflow_px']}px" for e in table.get("examples", [])[:2]
                )
                problems.append(f"{paper}: {name!r} has {table['overflowing_cells']} cells past their column ({examples})")
            if (table.get("past_page_px") or 0) > 1:
                problems.append(f"{paper}: {name!r} runs {table['past_page_px']}px past the page")
            if (table.get("clipped_px") or 0) > 1:
                problems.append(f"{paper}: {name!r} is clipped by {table['clipped_px']}px")
    count = len(next(iter(overflow.values())))
    if problems:
        return Result("table-cells-fit", FAIL, "; ".join(problems[:6]))
    return Result("table-cells-fit", PASS, f"{count} tables fit A4 and Letter widths; no cell overflows its column")


def check_words(obs: Mapping[str, object]) -> Result:
    """Cell and header text keeps its words whole; only numbers break at separators."""
    overflow = obs.get("overflow")
    if not isinstance(overflow, Mapping) or not any(overflow.values()):
        return Result("table-words-intact", SKIP, "The export has no tables")
    problems = []
    for paper, measured in overflow.items():
        broken = [t for t in measured if t.get("broken_words")]
        total = sum(int(t["broken_words"]) for t in broken)
        if broken:
            examples = "; ".join(
                f"{t.get('title') or t.get('index')!r}{' ' + t['label'] if t.get('label') else ''} ({t.get('columns')} columns): "
                + ", ".join(repr(e["word"]) for e in t.get("broken_examples", [])[:4])
                for t in broken[:3]
            )
            problems.append(f"{paper}: {total} words broken mid-word in {len(broken)} tables, e.g. {examples}")
    if problems:
        return Result("table-words-intact", FAIL, " | ".join(problems))
    return Result("table-words-intact", PASS, "No word in a table breaks across lines mid-word")


def check_row_limit(obs: Mapping[str, object]) -> Result:
    export, dom = obs.get("export"), obs.get("dom")
    if not isinstance(export, Mapping) or not isinstance(dom, Mapping):
        return Result("row-limit", SKIP, "No export data")
    tables = [b for b in all_blocks(export) if b.get("kind") == "table" and not b.get("message")]
    if not tables:
        return Result("row-limit", SKIP, "The export has no tables with rows")
    drawn = [
        unit.get("block")
        for section in dom.get("sections") or []  # type: ignore[union-attr]
        for unit in section.get("units") or []
        if unit.get("block") and unit["block"].get("tables") and unit["block"].get("h4")
    ]
    problems, long_tables = [], 0
    by_title: dict[str, list[Mapping[str, object]]] = {}
    for block in drawn:
        for title in block.get("h4") or []:  # type: ignore[union-attr]
            by_title.setdefault(title, []).append(block)  # type: ignore[arg-type]
    for table in tables:
        rows = len(table.get("rows") or [])  # type: ignore[arg-type]
        line = f"Showing {PRINT_ROW_LIMIT} of {rows} rows. The Markdown export has every row."
        candidates = by_title.get(str(norm(table.get("title"))), [])
        if not candidates:
            problems.append(f"{table.get('title')!r} not printed")
            continue
        block = candidates.pop(0)
        # Each printed part (or the whole table) carries the rows and its limit line.
        for drawn in block["tables"]:  # type: ignore[union-attr]
            part = drawn.get("part") or {}
            name = f"{table.get('title')!r}" + (f" ({part['label']})" if part.get("label") else "")
            limit = part.get("limit_line") if "part" in drawn else next(
                (p for p in block.get("ps") or [] if str(p).startswith("Showing ")), None  # type: ignore[union-attr]
            )
            if rows > PRINT_ROW_LIMIT:
                if drawn.get("body_rows") != PRINT_ROW_LIMIT:
                    problems.append(f"{name} prints {drawn.get('body_rows')} of {rows} rows")
                if limit != line:
                    problems.append(f"{name} has no limit line {line!r}")
            elif limit:
                problems.append(f"{name} has a limit line with only {rows} rows")
        long_tables += rows > PRINT_ROW_LIMIT
    if problems:
        return Result("row-limit", FAIL, "; ".join(problems[:6]))
    if long_tables:
        return Result("row-limit", PASS, f"{long_tables} tables over {PRINT_ROW_LIMIT} rows print {PRINT_ROW_LIMIT} rows and the limit line")
    return Result("row-limit", PASS, f"No table over {PRINT_ROW_LIMIT} rows; all {len(tables)} print every row with no limit line")


def check_notices(obs: Mapping[str, object], run: Profile | None) -> Result:
    """AI text notices: usage-limit ones where Backend skipped text, and one 'unavailable' notice
    under the cover when other AI text failed; each with its own text, as the page prints it."""
    export, dom = obs.get("export"), obs.get("dom")
    if not isinstance(export, Mapping) or not isinstance(dom, Mapping):
        return Result("ai-text-notices", SKIP, "No export data")
    found = notices(export)
    wrong = [n for n in found if n.get("text") != NOTICE_TEXT.get(str(n.get("reason", "usage_limit")))]
    top = list(export.get("notices") or [])  # type: ignore[arg-type]
    drawn = [n for n in dom.get("notices") or [] if n]  # type: ignore[union-attr]
    unavailable = notices(export, "unavailable")
    problems = []
    if wrong:
        problems.append(f"notice {wrong[0].get('reason')!r} reads {wrong[0].get('text')!r}")
    if drawn != [n.get("text") for n in top]:
        problems.append(f"page notices {drawn}, expected {[n.get('text') for n in top]}")
    if run is not None and run.ai_text_skipped and not notices(export, "usage_limit"):
        problems.append("Backend skipped AI text for the usage limit, but no notice prints")
    if run is not None and run.ai_text_unavailable and len(unavailable) != 1:
        problems.append(f"Backend's AI text failed: expected one 'unavailable' notice, found {len(unavailable)}")
    if any(n not in top for n in unavailable):
        problems.append("an 'unavailable' notice prints inside a section, not under the cover")
    if problems:
        return Result("ai-text-notices", FAIL, "; ".join(problems))
    if found:
        texts = ", ".join(sorted({repr(n.get("text")) for n in found}))
        return Result("ai-text-notices", PASS, f"{len(found)} notices print {texts}")
    return Result("ai-text-notices", PASS, "No AI text was skipped or failed; no notice expected or printed")


def check_internal_reasons(obs: Mapping[str, object], fixture: Fixture, run: Profile | None) -> Result:
    """Backend's own reasons for failed AI text (written for engineers) never reach the file."""
    export = obs.get("export")
    if not isinstance(export, Mapping):
        return Result("no-internal-reasons", SKIP, "No export data")
    # Each sentence of Backend's reasons, so a partial copy is caught too, and the fixture's own.
    sentences = [
        sentence.strip(" .")
        for error in (run.ai_text_errors if run else [])
        for sentence in re.split(r"(?<=[.;:])\s+", " ".join(error.split()))
        if len(sentence.split()) >= 4
    ]
    phrases = [*sentences, *fixture.expect.get("absent_text", [])]  # type: ignore[misc]
    phrases = list(dict.fromkeys(" ".join(str(p).split()) for p in phrases if str(p).strip()))
    if not phrases:
        return Result("no-internal-reasons", SKIP, "Backend recorded no AI text failure for this run")
    places = {
        "JSON": json.dumps(export, ensure_ascii=False),
        "page": str(obs.get("article_text") or ""),
        "PDF": str(obs.get("pdf_text") or ""),
    }
    leaks = [
        f"{where} contains {phrase[:60]!r}"
        for phrase in phrases
        for where, text in places.items()
        if phrase.casefold() in " ".join(text.split()).casefold()
    ]
    if leaks:
        return Result("no-internal-reasons", FAIL, "; ".join(leaks[:6]))
    checked = ", ".join(where for where, text in places.items() if text)
    return Result("no-internal-reasons", PASS, f"None of {len(phrases)} Backend reasons appear in the {checked}")


def check_expectations(obs: Mapping[str, object], fixture: Fixture) -> Result:
    """A fixture's own expectations: notice counts by reason and sections that print tables."""
    export, dom = obs.get("export"), obs.get("dom")
    if not isinstance(export, Mapping) or not isinstance(dom, Mapping):
        return Result("fixture-expectations", SKIP, "No export data")
    problems, facts = [], []
    for reason, count in (fixture.expect.get("notices") or {}).items():  # type: ignore[union-attr]
        found = len(notices(export, reason))
        facts.append(f"{found} {reason} notice(s)")
        if found != count:
            problems.append(f"{found} {reason!r} notices, expected {count}")
    for title in fixture.expect.get("sections_with_tables") or []:  # type: ignore[union-attr]
        section = next((s for s in export.get("sections") or [] if norm(s.get("title")) == norm(title)), None)  # type: ignore[union-attr]
        tables = [b for b in (section or {}).get("blocks") or [] if b.get("kind") == "table" and b.get("rows")]
        drawn = next((s for s in dom.get("sections") or [] if s.get("title") == norm(title)), None)  # type: ignore[union-attr]
        printed = sum(len((u.get("block") or {}).get("tables") or []) for u in (drawn or {}).get("units") or [])
        facts.append(f"{title!r}: {len(tables)} tables, {printed} printed")
        if not tables or not printed:
            problems.append(f"section {title!r} exports {len(tables)} tables with rows and prints {printed}")
    if problems:
        return Result("fixture-expectations", FAIL, "; ".join(problems))
    return Result("fixture-expectations", PASS, "; ".join(facts))


def check_cover(obs: Mapping[str, object]) -> Result:
    export, dom = obs.get("export"), obs.get("dom")
    if not isinstance(export, Mapping) or not isinstance(dom, Mapping) or not dom.get("cover"):
        return Result("cover", FAIL, "No cover printed")
    study = export["study"]
    cover = dom["cover"]
    assert isinstance(study, Mapping) and isinstance(cover, Mapping)
    problems = []
    if cover.get("title") != norm(study.get("title")):
        problems.append(f"title {cover.get('title')!r}")
    exported = f"Exported from Epoch on {day(parse_instant(str(export['exportedAt'])))}"
    if cover.get("exported") != exported:
        problems.append(f"{cover.get('exported')!r}, expected {exported!r}")
    facts = str(cover.get("facts") or "")
    run_at = parse_instant(str(study["runDate"]))
    wanted = [
        study.get("period") and f"Period: {study['period']}",
        study.get("assets") and f"Assets: {study['assets']}",
        f"Run: {day(run_at)}, {run_at:%H:%M}",
    ]
    for fact in filter(None, wanted):
        if fact not in facts:
            problems.append(f"missing {fact!r}")
    if not study.get("period") and "Period:" in facts:
        problems.append("prints a period the export does not have")
    if not study.get("assets") and "Assets:" in facts:
        problems.append("prints assets the export does not have")
    if study.get("question") and not cover.get("question"):
        problems.append("question missing")
    if problems:
        return Result("cover", FAIL, "; ".join(problems))
    shown = " · ".join(filter(None, wanted))  # type: ignore[arg-type]
    return Result("cover", PASS, f"Title, question and {shown}")


def check_document_title(obs: Mapping[str, object]) -> Result:
    export = obs.get("export")
    if not isinstance(export, Mapping):
        return Result("document-title", SKIP, "No export data")
    expected = expected_title(export)
    if obs.get("document_title") != expected:
        return Result("document-title", FAIL, f"{obs.get('document_title')!r}, expected {expected!r}")
    return Result("document-title", PASS, repr(expected))


def check_pdfs(obs: Mapping[str, object]) -> Result:
    pdfs = obs.get("pdf")
    if not isinstance(pdfs, Mapping):
        return Result("pdf", SKIP, "No PDF made")
    problems, parts = [], []
    for paper, (width, height) in PAPER_POINTS.items():
        made = pdfs.get(paper)
        if not isinstance(made, Mapping):
            problems.append(f"no {paper} PDF")
            continue
        parts.append(f"{paper} {made.get('pages')} pages")
        if (made.get("bytes") or 0) < 1000 or (made.get("pages") or 0) < 1:
            problems.append(f"{paper} PDF is empty")
        box = made.get("media_box") or []
        if len(box) != 4 or abs(box[2] - width) > 2 or abs(box[3] - height) > 2:  # type: ignore[index, operator]
            problems.append(f"{paper} page is {box}, expected {width}x{height}pt")
    if problems:
        return Result("pdf", FAIL, "; ".join(problems))
    return Result("pdf", PASS, ", ".join(parts))


def check_shape(obs: Mapping[str, object], fixture: Fixture, run: Profile | None) -> Result:
    """The export actually exhibits the shape the fixture stands for."""
    export = obs.get("export")
    if not isinstance(export, Mapping):
        return Result("fixture-shape", SKIP, "No export data")
    rule = fixture.match or {}
    blocks = all_blocks(export)
    tables = [b for b in blocks if b.get("kind") == "table" and not b.get("message")]
    charts = [b for b in blocks if b.get("kind") == "chart"]
    problems, facts = [], []
    min_columns = int(rule.get("min_table_columns", 0))  # type: ignore[arg-type]
    if min_columns:
        widest = max((len(t.get("columns") or []) for t in tables), default=0)  # type: ignore[arg-type]
        facts.append(f"widest printed table {widest} columns")
        if widest < min_columns:
            problems.append(f"no printed table has {min_columns}+ columns (widest {widest})")
    min_rows = int(rule.get("min_table_rows", 0))  # type: ignore[arg-type]
    if min_rows:
        longest = max((len(t.get("rows") or []) for t in tables), default=0)  # type: ignore[arg-type]
        facts.append(f"longest table {longest} rows")
        if longest < min_rows:
            problems.append(f"no exported table has {min_rows}+ rows (longest {longest})")
    if run is not None:
        kinds = {f"{c['category']}:{c['id']}": str(c["kind"]) for c in run.charts}
        exported = sorted({kinds.get(str(c.get("id")), "") for c in charts} - {""})
        if exported:
            facts.append("chart kinds " + ", ".join(exported))
        missing = sorted(set(rule.get("chart_kinds", [])) - set(exported))  # type: ignore[arg-type]
        if missing:
            problems.append(f"no exported {', '.join(missing)} chart")
        if run.category_count > 1:
            facts.append(f"{run.category_count} dashboard categories")
        minimum = int(rule.get("min_categories", 0))  # type: ignore[arg-type]
        printed = len(export.get("sections") or [])  # type: ignore[arg-type]
        if minimum and printed < minimum:
            problems.append(f"{printed} sections printed for {run.category_count} categories")
    if rule.get("ai_text_skipped") and not notices(export, "usage_limit"):
        problems.append("no usage-limit notice exported")
    if problems:
        return Result("fixture-shape", FAIL, "; ".join(problems))
    return Result("fixture-shape", PASS, "; ".join(facts) or fixture.shape)


def check_nothing_to_print(obs: Mapping[str, object]) -> Result:
    """A completed run without a dashboard: the documented outcome is data-export-ready=error."""
    printed = obs.get("print") or {}
    assert isinstance(printed, Mapping)
    ready = printed.get("ready")
    if ready == "error":
        message = " ".join(str(printed.get("message") or "").split())
        if NO_DASHBOARD_MESSAGE in message:
            return Result("nothing-to-print", PASS, f"data-export-ready=error: {NO_DASHBOARD_MESSAGE!r}")
        return Result("nothing-to-print", FAIL, f"data-export-ready=error, but it says {message[:200]!r}, not {NO_DASHBOARD_MESSAGE!r}")
    if ready == "true":
        export = obs.get("export") if isinstance(obs.get("export"), Mapping) else {}
        sections = len(export.get("sections") or [])  # type: ignore[union-attr]
        pages = ((obs.get("pdf") or {}).get("A4") or {}).get("pages")  # type: ignore[union-attr]
        return Result(
            "nothing-to-print",
            FAIL,
            f"The run published no dashboard, yet the print page is ready with {sections} sections "
            f"and prints a cover-only PDF ({pages} A4 pages); documented: data-export-ready=error "
            "when there's nothing to print",
        )
    return Result("nothing-to-print", FAIL, f"No data-export-ready within {printed.get('elapsed_s')}s")


# --- server-made PDFs ------------------------------------------------------------------


def file_title(title: str) -> str:
    """Platform's `fileTitle`: no control or bidi characters, path separators or characters
    Windows refuses, no leading or trailing dots and spaces, at most 120 characters."""
    cleaned = FILE_NAME_CONTROLS.sub(" ", unicodedata.normalize("NFC", title))
    cleaned = re.sub(r'[\\/:*?"<>|]', " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    trimmed = re.sub(r"^[\s.]+|[\s.]+$", "", cleaned[:120])
    return trimmed or "Study"


def expected_file_name(export: Mapping[str, object]) -> str:
    """"<study title> – <run date>.pdf", the run date in UTC, as Platform names the file."""
    study = export["study"]
    assert isinstance(study, Mapping)
    return f"{file_title(str(study['title']))} – {day(parse_instant(str(study['runDate'])))}.pdf"


def server_unavailable(status: object, message: object) -> str | None:
    """Why a refused POST means the server export isn't live (a skip), or None when it is a real answer.

    404 is a route that isn't deployed, unless Platform says the run isn't found; 503 is exports
    not available. The Terminal falls back to printing in the browser on both.
    """
    if status == 404 and message != RUN_NOT_FOUND:
        return NOT_DEPLOYED
    if status == 503:
        return UNAVAILABLE
    return None


def _answer(post: Mapping[str, object]) -> str:
    status, message = post.get("status") or post.get("error"), post.get("message")
    return f"POST {status}" + (f" {message!r}" if message else "")


def _not_live(post: Mapping[str, object]) -> Result | None:
    """The skip for a POST that found no live service or the hourly limit, else None."""
    reason = server_unavailable(post.get("status"), post.get("message"))
    if reason:
        return Result("", SKIP, f"{reason} ({_answer(post)})")
    if post.get("status") == 429:
        return Result("", SKIP, f"{LIMIT_REACHED} ({_answer(post)}, Retry-After {post.get('retry_after')})")
    return None


def _timeline(server: Mapping[str, object]) -> str:
    steps = " → ".join(f"{t.get('state')} {t.get('at_s')}s" for t in server.get("transitions") or [])  # type: ignore[union-attr]
    return f"{steps} ({server.get('polls')} polls)"


def check_pdf_server(obs: Mapping[str, object], empty: bool) -> Result:
    """The run's PDF made on the server through the API: ready within 2 minutes, a PDF with pages,
    under 50 MB, named "<title> – <run date>.pdf". A run without a dashboard is refused (422), or
    accepted and then failed cleanly as `no_dashboard`."""
    name = "pdf-server"
    server = obs.get("server_pdf")
    if not isinstance(server, Mapping):
        return Result(name, SKIP, "Not tried")
    if server.get("error"):
        return Result(name, FAIL, f"The suite could not complete the export: {server['error']}")
    post = server.get("post") or {}
    assert isinstance(post, Mapping)
    skipped = _not_live(post)
    if skipped:
        return Result(name, SKIP, skipped.detail)
    status, final = post.get("status"), server.get("export") or {}
    assert isinstance(final, Mapping)
    if empty:
        if status == 422:
            return Result(name, PASS, f"No dashboard: refused with {_answer(post)}")
        if status == 202 and server.get("outcome") == "final":
            reason = (final.get("failure") or {}).get("reason")  # type: ignore[union-attr]
            if final.get("state") == "failed" and reason == "no_dashboard":
                return Result(name, PASS, f"No dashboard: accepted, then failed as no_dashboard: {_timeline(server)}")
            return Result(name, FAIL, f"The run published no dashboard, yet its export ended {final.get('state')} ({reason}): {_timeline(server)}")
        return Result(name, FAIL, f"The run published no dashboard: {_answer(post)}, expected 422 or a no_dashboard failure")
    if status == 422:
        return Result(name, SKIP, f"Platform can't export this run on the server ({_answer(post)}); the Terminal prints it in the browser")
    if status != 202:
        return Result(name, FAIL, f"{_answer(post)}, expected 202")
    notes = [f"GET answered {e.get('status') or e.get('error')} at {e.get('at_s')}s" for e in server.get("errors") or []]  # type: ignore[union-attr]
    if server.get("outcome") == "timeout":
        return Result(name, FAIL, f"Not final within {SERVER_TIMEOUT_S}s: {_timeline(server)}", notes)
    if server.get("outcome") == "gone":
        return Result(name, FAIL, f"The export was gone (GET 404) while queued: {_timeline(server)}", notes)
    if final.get("state") != "ready":
        failure = final.get("failure") or {}
        assert isinstance(failure, Mapping)
        return Result(name, FAIL, f"Ended {final.get('state')} {failure.get('reason')!r} {failure.get('message')!r}: {_timeline(server)}", notes)
    got = server.get("download") or {}
    assert isinstance(got, Mapping)
    problems = []
    if got.get("status") != 200 or got.get("error"):
        problems.append(f"the download answered {got.get('status') or got.get('error')}")
    elif got.get("over_limit") or (got.get("bytes") or 0) >= SERVER_MAX_BYTES:
        problems.append("the file is 50 MB or more")
    else:
        if not got.get("pdf_header"):
            problems.append("the file is not a PDF (no %PDF- header)")
        if (got.get("pages") or 0) < 1:
            problems.append("the PDF has no pages")
    if not str(got.get("content_disposition") or "").startswith("attachment"):
        problems.append(f"Content-Disposition {got.get('content_disposition')!r}, expected an attachment")
    file_name = final.get("file_name")
    export = obs.get("export")
    if isinstance(export, Mapping):
        if file_name != expected_file_name(export):
            problems.append(f"file_name {file_name!r}, expected {expected_file_name(export)!r}")
    else:
        notes.append("The file name was not compared: the print page gave no export data")
    if problems:
        return Result(name, FAIL, "; ".join(problems) + f" ({_timeline(server)})", notes)
    size = f"{(got.get('bytes') or 0) / 1024:.0f} kB"
    return Result(name, PASS, f"{_timeline(server)}; {got.get('pages')} pages, {size}; {file_name!r}", notes)


def outline(export: Mapping[str, object]) -> list[str]:
    """The export's section titles, headings and table titles, in print order."""
    labels: list[str] = []
    for section in export.get("sections") or []:  # type: ignore[union-attr]
        labels.append(str(norm(section.get("title") or "")))
        for block in section.get("blocks") or []:
            if block.get("kind") == "heading":
                labels.append(str(norm(block.get("text") or "")))
            elif block.get("kind") == "table":
                labels.append(str(norm(block.get("title") or "")))
    return [label for label in labels if label]


def in_order(text: str, labels: Sequence[str]) -> list[str]:
    """The labels found in the text one after another. Line and page breaks are spaces, and case
    is ignored (headings print in capitals), so different page breaks don't matter."""
    flat = " ".join(text.split()).casefold()
    found, at = [], 0
    for label in labels:
        where = flat.find(label.casefold(), at)
        if where >= 0:
            found.append(label)
            at = where + len(label.casefold())
    return found


def check_pdf_server_text(obs: Mapping[str, object]) -> Result:
    """The server PDF has the browser-print PDF's section headings and table titles, in its order."""
    name = "pdf-server-text"
    server = obs.get("server_pdf")
    if not isinstance(server, Mapping) or not isinstance(server.get("download"), Mapping) or "text" not in server:
        return Result(name, SKIP, "No server PDF to compare")
    export, browser, made = obs.get("export"), obs.get("pdf_text"), server.get("text")
    if not isinstance(export, Mapping):
        return Result(name, SKIP, "No export data to take headings and table titles from")
    if not isinstance(browser, str) or not isinstance(made, str):
        return Result(name, SKIP, "No PDF text: pdftotext (poppler) is not installed")
    labels = outline(export)
    if not labels:
        return Result(name, SKIP, "The export has no section headings or table titles")
    want, got = in_order(browser, labels), in_order(made, labels)
    pages = f"browser A4 {((obs.get('pdf') or {}).get('A4') or {}).get('pages')} pages, server {(server.get('download') or {}).get('pages')}"  # type: ignore[union-attr]
    notes = []
    if len(want) < len(labels):
        notes.append(f"{len(labels) - len(want)} of {len(labels)} headings and titles aren't in order in the browser-print PDF either")
    if got == want:
        return Result(name, PASS, f"{len(want)} section headings and table titles in the browser-print PDF's order ({pages})", notes)
    first = next(i for i in range(max(len(want), len(got))) if i >= len(got) or i >= len(want) or want[i] != got[i])
    missing = [label for label in want if label not in got]
    detail = f"first difference at #{first + 1}: browser {want[first] if first < len(want) else None!r}, server {got[first] if first < len(got) else None!r}"
    if missing:
        detail += f"; not in order in the server PDF: {missing[:4]}"
    return Result(name, FAIL, f"{detail} ({pages})", notes)


def check_pdf_server_cancel(obs: Mapping[str, object]) -> Result:
    """DELETE on a queued export answers 204, and the export then reads `cancelled`."""
    name = "pdf-server-cancel"
    probe = obs.get("server_pdf_cancel") or {}
    assert isinstance(probe, Mapping)
    post = probe.get("post") or {}
    assert isinstance(post, Mapping)
    skipped = _not_live(post)
    if skipped:
        return Result(name, SKIP, skipped.detail)
    if post.get("status") != 202:
        return Result(name, FAIL, f"{_answer(post)}, expected 202")
    deleted, after = probe.get("delete") or {}, probe.get("after") or {}
    assert isinstance(deleted, Mapping) and isinstance(after, Mapping)
    if deleted.get("status") != 204:
        return Result(name, FAIL, f"DELETE answered {deleted.get('status') or deleted.get('error')} {deleted.get('message')!r}, expected 204")
    if after.get("status") != 200 or after.get("state") != "cancelled":
        return Result(name, FAIL, f"After DELETE, GET answered {after.get('status')} with state {after.get('state')!r}, expected 'cancelled'")
    return Result(name, PASS, f"DELETE on a {probe.get('state_before')} export answered 204; it then reads 'cancelled'")


def check_pdf_server_other_owner(obs: Mapping[str, object]) -> Result:
    """Another owner reading this owner's export gets 404, as for an unknown ID."""
    name = "pdf-server-other-owner"
    probe = obs.get("server_pdf_other_owner") or {}
    assert isinstance(probe, Mapping)
    if not probe.get("second_identity"):
        return Result(name, SKIP, "The suite signs in as one owner; another owner's read of an export isn't tried")
    if not probe.get("export_id"):
        return Result(name, SKIP, "No export was made to read as another owner")
    got = probe.get("get") or {}
    assert isinstance(got, Mapping)
    if got.get("status") != 404:
        return Result(name, FAIL, f"Another owner's GET answered {got.get('status') or got.get('error')}, expected 404")
    return Result(name, PASS, "Another owner's GET answered 404")


def check_pdf_server_ui(obs: Mapping[str, object]) -> Result:
    """Export → PDF in the Terminal: "Preparing your PDF…", then the file downloads; or, where the
    server export isn't live, the print layout opens in a new tab (a skip, not a failure)."""
    name = "pdf-server-ui"
    ui = obs.get("server_pdf_ui") or {}
    assert isinstance(ui, Mapping)
    if ui.get("error"):
        return Result(name, FAIL, str(ui["error"]))
    toasts = [t.get("text") for t in ui.get("toasts") or []]  # type: ignore[union-attr]
    popup = ui.get("popup") or {}
    assert isinstance(popup, Mapping)
    expected = f"/print/studies/{ui.get('study_id')}"
    printed = popup.get("path") == expected and popup.get("run") == ui.get("run_id")
    if ui.get("build") == "print-link":
        if not printed:
            return Result(name, FAIL, f"Export → PDF opened {popup.get('path')!r}, not the run's print layout")
        return Result(name, SKIP, f"{NOT_DEPLOYED}: this Terminal build has no server PDF; Export → PDF opens the print layout in a new tab")
    problems = [] if PREPARING_TOAST in toasts else [f"no “{PREPARING_TOAST}” toast (saw {toasts})"]
    post = {"status": ui.get("post_status"), "message": ui.get("post_message")}
    if post["status"] == 202:
        got = ui.get("download") or {}
        assert isinstance(got, Mapping)
        if not got:
            problems.append(f"no download within {ui.get('waited_s')}s")
        elif got.get("failure"):
            problems.append(f"the download {got.get('suggested_filename')!r} failed: {got['failure']}")
        elif not got.get("pdf_header"):
            problems.append(f"the download {got.get('suggested_filename')!r} is not a PDF")
        if problems:
            return Result(name, FAIL, "; ".join(problems))
        return Result(name, PASS, f"“{PREPARING_TOAST}”, then {got.get('suggested_filename')!r} downloaded ({got.get('bytes')} bytes, a PDF) after {got.get('at_s')}s")
    skipped = _not_live(post)
    fallback = (
        server_unavailable(post["status"], post["message"])
        or ("the run can't be exported on the server" if post["status"] == 422 else None)
    )
    if fallback and not printed:
        problems.append(f"after {_answer(post)} the print layout didn't open (popup {popup.get('path')!r})")
    if problems or not (skipped or fallback):
        return Result(name, FAIL, "; ".join(problems) or f"{_answer(post)}, expected 202 or a fallback status")
    if fallback:
        return Result(name, SKIP, f"{fallback} ({_answer(post)}): “{PREPARING_TOAST}”, then the print layout opened in a new tab")
    return Result(name, SKIP, f"{LIMIT_REACHED} ({_answer(post)})")


def evaluate(
    obs: Mapping[str, object], fixture: Fixture, formats: Sequence[Format], run: Profile | None
) -> list[Result]:
    empty = run is not None and run.status == "completed" and not run.categories
    results = [
        check_no_dashboard_menu(obs) if empty else check_menu(obs, formats),
        *check_formats(obs, formats),
        check_nothing_to_print(obs) if empty else check_ready(obs),
    ]
    ready = (obs.get("print") or {}).get("ready") == "true"  # type: ignore[union-attr]
    if ready:
        results += [
            check_export_data(obs, run),
            check_shape(obs, fixture, run),
            check_cover(obs),
            check_document_title(obs),
            check_sections(obs),
            check_images(obs),
            check_overflow(obs),
            check_words(obs),
            check_row_limit(obs),
            check_notices(obs, run),
            check_internal_reasons(obs, fixture, run),
            *([check_expectations(obs, fixture)] if fixture.expect.get("notices") or fixture.expect.get("sections_with_tables") else []),
            check_pdfs(obs),
        ]
    # Server-made PDFs, in observations recorded since they were checked. The attempt-wide probes
    # (cancel, another owner, the Terminal's Export → PDF) are on one fixture's observation.
    if "server_pdf" in obs:
        results.append(check_pdf_server(obs, empty))
        if not empty:
            results.append(check_pdf_server_text(obs))
    if "server_pdf_cancel" in obs:
        results.append(check_pdf_server_cancel(obs))
    if "server_pdf_other_owner" in obs:
        results.append(check_pdf_server_other_owner(obs))
    if "server_pdf_ui" in obs:
        results.append(check_pdf_server_ui(obs))
    results += [check_clean(obs), check_no_ai(obs)]
    return results


def outcome(results: Iterable[Result]) -> str:
    return FAIL if any(r.status == FAIL for r in results) else PASS


def chart_coverage(
    kinds: Sequence[str], observed: Mapping[str, tuple[Profile | None, Mapping[str, object] | None, str]]
) -> dict[str, list[dict[str, str]]]:
    """For each required chart kind, the fixtures whose export printed one, and how it fared.

    `observed` maps fixture id to (profile, observation, images check status).
    """
    coverage: dict[str, list[dict[str, str]]] = {kind: [] for kind in kinds}
    for fixture_id, (run, obs, status) in observed.items():
        if run is None or not isinstance(obs, Mapping) or not isinstance(obs.get("export"), Mapping):
            continue
        by_id = {f"{c['category']}:{c['id']}": str(c["kind"]) for c in run.charts}
        printed = {
            by_id.get(str(c.get("id")), "")
            for c in all_blocks(obs["export"])  # type: ignore[arg-type]
            if c.get("kind") == "chart"
        }
        for kind in sorted(printed - {""}):
            coverage.setdefault(kind, []).append({"fixture": fixture_id, "images": status})
    return coverage
