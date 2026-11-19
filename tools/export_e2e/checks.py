"""Judges one recorded observation of a study's export. Pure: JSON in, results out.

The expected texts are the Terminal's own (EpochClientPortal StudyExportMenu,
StudyPrintPage, PrintLayout and StudyPage/lib/export.ts). The browser runs in UTC,
so dates are formatted in UTC here too.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .catalog import Fixture, Format, Profile

MENU_LABEL = "Export this result"
MENU_NOTE = "A file is a copy: it can’t be updated. To keep people up to date, share the conversation."
PDF_DESCRIPTION = "The dashboard as pages, ready to print"
PRINT_ROW_LIMIT = 200
USAGE_LIMIT_NOTICE = "AI summary not generated: usage limit reached"
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


def notices(export: Mapping[str, object]) -> list[Mapping[str, object]]:
    found = list(export.get("notices") or [])  # type: ignore[arg-type]
    return found + [b for b in all_blocks(export) if b.get("kind") == "notice"]


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
        if pdf.get("href") != expected:
            problems.append(f"PDF opens {pdf.get('href')!r}, expected {expected!r}")
        if pdf.get("target") != "_blank" or "noopener" not in str(pdf.get("rel") or ""):
            problems.append("PDF does not open in a new tab without an opener")
    offered = ", ".join(str(i.get("label")) for i in items)
    if problems:
        return Result("export-menu", FAIL, "; ".join(problems))
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
        if run.ai_text_skipped and not notices(export):
            problems.append("Backend skipped AI text for the usage limit, but the export has no notice")
        if notices(export) and not run.ai_text_skipped:
            problems.append("the export has a usage-limit notice Backend did not record")
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
    export, dom = obs.get("export"), obs.get("dom")
    if not isinstance(export, Mapping) or not isinstance(dom, Mapping):
        return Result("usage-limit-notice", SKIP, "No export data")
    found = notices(export)
    expected = bool(run and run.ai_text_skipped)
    wrong = [n for n in found if n.get("text") != USAGE_LIMIT_NOTICE]
    top = list(export.get("notices") or [])  # type: ignore[arg-type]
    drawn = [n for n in dom.get("notices") or [] if n]  # type: ignore[union-attr]
    problems = []
    if wrong:
        problems.append(f"notice text {wrong[0].get('text')!r}")
    if drawn != [n.get("text") for n in top]:
        problems.append(f"page notices {drawn}, expected {[n.get('text') for n in top]}")
    if expected and not found:
        problems.append("Backend skipped AI text for the usage limit, but no notice prints")
    if problems:
        return Result("usage-limit-notice", FAIL, "; ".join(problems))
    if found:
        return Result("usage-limit-notice", PASS, f"{len(found)} usage-limit notices print {USAGE_LIMIT_NOTICE!r}")
    return Result("usage-limit-notice", PASS, "No AI text was skipped; no notice expected or printed")


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
    if rule.get("ai_text_skipped") and not notices(export):
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
            check_pdfs(obs),
        ]
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
