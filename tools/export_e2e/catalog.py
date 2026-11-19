"""The export suite's fixture catalog and the shape of a run, read from Backend's JSON.

Everything here is pure: it takes the API's JSON and returns plain data, so the
unit tests can check it offline.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "campaigns" / "export-e2e" / "catalog.json"

# Match rule keys and what they test on a run's profile.
RULE_KEYS = {
    "execution_type",
    "min_cards",
    "chart_kinds",
    "min_table_columns",
    "min_table_rows",
    "authored",
    "min_briefings",
    "min_categories",
    "ai_text_skipped",
    "has_results",
}
# Calls that make a run spend the owner's AI allowance. A script fixture may not
# contain them, and is always run as research (no `execution` overrides).
MODEL_CALL = re.compile(r"\bllm_call\s*\(")


@dataclass(frozen=True)
class Format:
    id: str
    menu_label: str
    implemented: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class Script:
    path: Path
    name: str
    description: str
    assets: tuple[str, ...]
    start: str
    end: str

    def source(self) -> str:
        return self.path.read_text()

    def spec_hash(self) -> str:
        """Identity of what a created run is made from; a change means a new run."""
        spec = {
            "source": self.source(),
            "name": self.name,
            "description": self.description,
            "assets": list(self.assets),
            "period": [self.start, self.end],
        }
        return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Derived:
    """A run the suite may create only with the owner's explicit approval of its AI cost.

    `monte_carlo` derives from the run pinned to the `parent` fixture; `parameter_search`
    searches a backtest definition made from `script`.
    """

    kind: str
    parent: str | None = None
    options: Mapping[str, object] = field(default_factory=dict)
    script: Script | None = None
    search: Mapping[str, object] = field(default_factory=dict)

    def spec_hash(self) -> str:
        spec = {
            "kind": self.kind,
            "parent": self.parent,
            "options": self.options,
            "search": self.search,
            "script": self.script.spec_hash() if self.script else None,
        }
        return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Fixture:
    id: str
    shape: str
    match: Mapping[str, object] | None = None
    prefer: str | None = None
    script: Script | None = None
    notes: tuple[str, ...] = ()
    create_cost: str | None = None
    derive: Derived | None = None
    # What this fixture's export must (not) show, beyond the generic checks.
    expect: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Catalog:
    fixtures: tuple[Fixture, ...]
    formats: tuple[Format, ...]
    chart_kinds: tuple[str, ...]
    purpose: str = ""

    def get(self, fixture_id: str) -> Fixture:
        for fixture in self.fixtures:
            if fixture.id == fixture_id:
                return fixture
        raise ValueError(
            f"No fixture {fixture_id!r}; choose one of {', '.join(f.id for f in self.fixtures)}."
        )

    def select(self, only: Iterable[str] | None) -> list[Fixture]:
        return [self.get(name) for name in only] if only else list(self.fixtures)


def check_creatable(source: str) -> None:
    """A script fixture must not make a model call; the suite never spends AI usage."""
    if MODEL_CALL.search(source):
        raise ValueError(
            "This fixture's script calls llm_call, which spends the owner's AI allowance; "
            "the export suite only creates runs that make no model call."
        )


def _script(fixture_id: str, spec: Mapping[str, object], folder: Path) -> Script:
    script_path = (folder / str(spec["path"])).resolve()
    if folder.resolve() not in script_path.parents or not script_path.is_file():
        raise ValueError(f"{fixture_id}: script {spec['path']} is not in the campaign")
    period = spec["period"]
    assert isinstance(period, Mapping)
    script = Script(
        path=script_path,
        name=str(spec["name"]),
        description=str(spec["description"]),
        assets=tuple(spec.get("assets", [])),  # type: ignore[arg-type]
        start=str(period["start"]),
        end=str(period["end"]),
    )
    check_creatable(script.source())
    return script


DERIVED_KINDS = ("monte_carlo", "parameter_search")
EXPECT_KEYS = {"absent_text", "notices", "sections_with_tables"}


def load_catalog(path: Path = CATALOG) -> Catalog:
    raw = json.loads(path.read_text())
    seen: set[str] = set()
    fixtures = []
    for item in raw["fixtures"]:
        fixture_id = item["id"]
        if fixture_id in seen or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", fixture_id):
            raise ValueError(f"Fixture ids must be unique simple names: {fixture_id!r}")
        seen.add(fixture_id)
        match = item.get("match")
        if match is not None and set(match) - RULE_KEYS:
            raise ValueError(f"{fixture_id}: unknown match keys {sorted(set(match) - RULE_KEYS)}")
        script = _script(fixture_id, item["script"], path.parent) if "script" in item else None
        derive = None
        if "derive" in item:
            spec = item["derive"]
            if spec.get("kind") not in DERIVED_KINDS or script is not None:
                raise ValueError(f"{fixture_id}: derive needs a kind of {DERIVED_KINDS} and no script")
            derive = Derived(
                kind=spec["kind"],
                parent=spec.get("parent"),
                options=spec.get("options", {}),
                script=_script(fixture_id, spec["script"], path.parent) if "script" in spec else None,
                search=spec.get("search", {}),
            )
            if (derive.kind == "monte_carlo" and not derive.parent) or (
                derive.kind == "parameter_search"
            ) != bool(derive.script):
                raise ValueError(f"{fixture_id}: Monte Carlo needs a parent fixture; a search needs a script")
        if match is None and script is None:
            raise ValueError(f"{fixture_id}: give a match rule, a script, or both")
        expect = item.get("expect", {})
        if set(expect) - EXPECT_KEYS:
            raise ValueError(f"{fixture_id}: unknown expect keys {sorted(set(expect) - EXPECT_KEYS)}")
        fixtures.append(
            Fixture(
                id=fixture_id,
                shape=item["shape"],
                match=match,
                prefer=item.get("prefer"),
                script=script,
                notes=tuple(item.get("notes", [])),
                create_cost=item.get("create_cost"),
                derive=derive,
                expect=expect,
            )
        )
    for fixture in fixtures:
        parent = fixture.derive.parent if fixture.derive else None
        if parent and parent not in seen:
            raise ValueError(f"{fixture.id}: no parent fixture {parent!r}")
    formats = tuple(
        Format(f["id"], f["menu_label"], bool(f["implemented"]), tuple(f.get("issues", [])))
        for f in raw["formats"]
    )
    return Catalog(tuple(fixtures), formats, tuple(raw["chart_kinds"]), raw.get("purpose", ""))


# --- Arrow IPC ---------------------------------------------------------------


def _table(buffer: bytes, position: int) -> int:
    return position + struct.unpack_from("<I", buffer, position)[0]


def _field(buffer: bytes, table: int, index: int) -> int | None:
    vtable = table - struct.unpack_from("<i", buffer, table)[0]
    size = struct.unpack_from("<H", buffer, vtable)[0]
    at = vtable + 4 + 2 * index
    if at >= vtable + size:
        return None
    offset = struct.unpack_from("<H", buffer, at)[0]
    return table + offset if offset else None


def arrow_row_count(buffer: bytes) -> int:
    """Rows in an Arrow IPC file or stream: the sum of its record batches' lengths.

    Reads only each message's flatbuffer header (Message.header_type,
    Message.header -> RecordBatch.length, Message.bodyLength), so no Arrow
    library is needed for a row count.
    """
    position = 8 if buffer[:6] == b"ARROW1" else 0
    rows = 0
    while position + 4 <= len(buffer):
        prefix = struct.unpack_from("<I", buffer, position)[0]
        if prefix == 0xFFFFFFFF:
            if position + 8 > len(buffer):
                break
            length = struct.unpack_from("<i", buffer, position + 4)[0]
            position += 8
        else:  # Pre-0.15 streams have no continuation marker.
            length = prefix
            position += 4
        if length <= 0:
            break  # End of stream; a file's footer follows.
        meta = buffer[position : position + length]
        if len(meta) < length:
            raise ValueError("Truncated Arrow message")
        root = _table(meta, 0)
        header_type = _field(meta, root, 1)
        body_at = _field(meta, root, 3)
        body = struct.unpack_from("<q", meta, body_at)[0] if body_at else 0
        if header_type is not None and meta[header_type] == 3:  # RecordBatch
            header = _field(meta, root, 2)
            if header is None:
                raise ValueError("Arrow record batch without a header")
            batch = _table(meta, header)
            length_at = _field(meta, batch, 0)
            rows += struct.unpack_from("<q", meta, length_at)[0] if length_at else 0
        position += length + body
    return rows


# --- A run's profile ------------------------------------------------------------

ALL = "ALL"


def result_categories(metadata: Mapping[str, Mapping[str, int | None]]) -> list[str]:
    """The categories the study page shows, as the Terminal's `resultCategories` picks them."""
    filled = [
        name
        for name, counts in metadata.items()
        if sum(
            (counts.get(key) or 0)
            for key in (
                "charts_count",
                "tables_count",
                "cards_count",
                "snapshot_cards_count",
                "content_count",
            )
        )
        > 0
    ]
    specific = [name for name in filled if name != ALL]
    return specific or filled


def chart_kind(chart: Mapping[str, object]) -> str:
    """A chart's kind, with a heatmap's layout: `HeatMap:Matrix`, `HeatMap:Tiles`…"""
    definition = chart.get("chart_def") or {}
    assert isinstance(definition, Mapping)
    kind = str(definition.get("kind") or "Unknown")
    if kind == "HeatMap":
        return f"HeatMap:{definition.get('layout') or 'Matrix'}"
    return kind


def _supporting(owner: Mapping[str, object]) -> bool:
    return owner.get("content_role") in ("Guide", "Diagnostic")


def visible_columns(table: Mapping[str, object]) -> int:
    """Columns a table shows by default (no hidden, suffix or supporting columns)."""
    columns = [c for c in table.get("columns") or [] if isinstance(c, Mapping)]
    suffixes = {c.get("suffix_column_id") for c in columns if c.get("suffix_column_id")}
    return sum(
        1
        for c in columns
        if not c.get("initially_hidden") and c.get("id") not in suffixes and not _supporting(c)
    )


def skipped_ai_text(node: object) -> bool:
    """Whether Backend recorded AI text skipped for the usage limit anywhere in a schema."""
    if isinstance(node, Mapping):
        for key in ("ai_text", "aiText"):
            value = node.get(key)
            if (
                isinstance(value, Mapping)
                and value.get("status") == "skipped"
                and value.get("reason") == "usage_limit"
            ):
                return True
        return any(skipped_ai_text(value) for value in node.values())
    if isinstance(node, list):
        return any(skipped_ai_text(value) for value in node)
    return False


def _has_text(note: Mapping[str, object]) -> bool:
    briefing = note.get("briefing")
    if isinstance(briefing, Mapping) and any(
        isinstance(item, Mapping) and str(item.get("text", "")).strip()
        for item in briefing.get("items") or []
    ):
        return True
    return bool(str(note.get("markdown") or "").strip())


def is_authored(schema: Mapping[str, object]) -> bool:
    """The Terminal's `hasAuthoredContent`: a written-up dashboard, laid out by its author."""
    content = schema.get("content")
    if not isinstance(content, Mapping):
        return False
    return bool(
        content.get("categories")
        or content.get("sections")
        or content.get("graphs")
        or any(
            isinstance(note, Mapping) and not note.get("hidden") and _has_text(note)
            for note in content.get("markdown") or []
        )
    )


@dataclass
class Profile:
    run_id: str
    study_id: str
    execution_type: str
    status: str
    created_at: int
    period: Mapping[str, str] | None
    categories: list[str] = field(default_factory=list)
    authored: bool = False
    briefings: int = 0
    notes: int = 0
    ai_text_skipped: bool = False
    # AI text that failed or is unavailable for another reason, and Backend's reasons for it.
    ai_text_unavailable: bool = False
    ai_text_errors: list[str] = field(default_factory=list)
    cards: int = 0
    charts: list[dict[str, object]] = field(default_factory=list)
    tables: list[dict[str, object]] = field(default_factory=list)
    name: str | None = None

    @property
    def chart_kinds(self) -> list[str]:
        return sorted({str(chart["kind"]) for chart in self.charts})

    @property
    def exhibit_categories(self) -> list[str]:
        """The dashboard's own categories (its sections), from its charts and tables."""
        return sorted({str(x["category"]) for x in [*self.charts, *self.tables]})

    @property
    def category_count(self) -> int:
        return len(self.exhibit_categories)

    @property
    def exhibits(self) -> int:
        """Charts, tables and cards: how much a run gives an export to print."""
        return len(self.charts) + len(self.tables) + self.cards

    @property
    def max_table_columns(self) -> int:
        return max((int(t["columns"]) for t in self.tables), default=0)

    @property
    def max_table_rows(self) -> int:
        return max((int(t["rows"]) for t in self.tables if t.get("rows") is not None), default=0)

    def to_json(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "study_id": self.study_id,
            "name": self.name,
            "execution_type": self.execution_type,
            "status": self.status,
            "created_at": self.created_at,
            "period": dict(self.period) if self.period else None,
            "categories": self.categories,
            "authored": self.authored,
            "briefings": self.briefings,
            "notes": self.notes,
            "ai_text_skipped": self.ai_text_skipped,
            "ai_text_unavailable": self.ai_text_unavailable,
            "ai_text_errors": self.ai_text_errors,
            "cards": self.cards,
            "chart_kinds": self.chart_kinds,
            "exhibit_categories": self.exhibit_categories,
            "charts": self.charts,
            "tables": self.tables,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> Profile:
        fields = {k: v for k, v in value.items() if k not in ("chart_kinds", "exhibit_categories")}
        return cls(**fields)  # type: ignore[arg-type]


def profile(
    execution: Mapping[str, object],
    metadata: Mapping[str, Mapping[str, int | None]] | None = None,
    schemas: Mapping[str, Mapping[str, object]] | None = None,
    row_counts: Mapping[tuple[str, str], int] | None = None,
    name: str | None = None,
) -> Profile:
    """A run's export-relevant shape from its execution, metadata and dashboard schemas.

    `schemas` maps each category read (including "ALL") to its schema; `row_counts`
    maps (category, data_id) to a Detailed table's row count.
    """
    result = Profile(
        run_id=str(execution["id"]),
        study_id=str(execution.get("definition_id") or ""),
        execution_type=str(execution.get("execution_type") or ""),
        status=str(execution.get("status") or ""),
        created_at=int(execution.get("created_at") or 0),  # type: ignore[arg-type]
        period=execution.get("period"),  # type: ignore[arg-type]
        name=name,
    )
    if metadata is None or schemas is None:
        return result
    row_counts = row_counts or {}
    result.categories = result_categories(metadata)
    whole = schemas.get(ALL) or {}
    result.authored = is_authored(whole)
    content = whole.get("content") if isinstance(whole.get("content"), Mapping) else {}
    assert isinstance(content, Mapping)
    markdown = [n for n in content.get("markdown") or [] if isinstance(n, Mapping)]
    result.notes = sum(1 for note in markdown if not note.get("hidden") and _has_text(note))
    result.briefings = sum(1 for note in markdown if note.get("briefing")) + sum(
        1
        for section in content.get("sections") or []
        if isinstance(section, Mapping)
        and isinstance(section.get("executive_summary"), Mapping)
        and section["executive_summary"].get("briefing")
    )
    result.ai_text_skipped = any(skipped_ai_text(schema) for schema in schemas.values())
    for part in [*(content.get("sections") or []), *(content.get("categories") or [])]:
        state = part.get("interpretation") if isinstance(part, Mapping) else None
        if not isinstance(state, Mapping) or skipped_ai_text(state):
            continue
        if state.get("status") not in ("Ready", "Generating"):
            result.ai_text_unavailable = True
            if str(state.get("error") or "").strip() and state["error"] not in result.ai_text_errors:
                result.ai_text_errors.append(str(state["error"]))
    charts: dict[tuple[str, str], dict[str, object]] = {}
    tables: dict[tuple[str, object], dict[str, object]] = {}
    for read_category, schema in schemas.items():
        for chart in schema.get("charts") or []:
            key = (str(chart.get("category")), str(chart.get("id")))
            charts.setdefault(
                key,
                {
                    "category": key[0],
                    "id": key[1],
                    "kind": chart_kind(chart),
                    "title": chart.get("title"),
                },
            )
        for table in schema.get("tables") or []:
            key = (str(table.get("category")), table.get("index"))
            if key in tables:
                continue
            flavor = table.get("flavor")
            cards = len(table.get("cards") or [])
            result.cards += cards
            data_id = str(table.get("data_id") or "")
            tables[key] = {
                "category": key[0],
                "index": key[1],
                "title": table.get("title"),
                "flavor": flavor,
                "columns": visible_columns(table),
                "cards": cards,
                "rows": row_counts.get((read_category, data_id)),
                "data_id": data_id,
                "read_category": read_category,
            }
    for schema in schemas.values():
        for group in schema.get("snapshot_cards") or []:
            if isinstance(group, Mapping):
                result.cards += len(group.get("cards") or [])
    result.charts = list(charts.values())
    result.tables = list(tables.values())
    return result


def detailed_tables(schema: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Tables whose rows are read from Arrow (not Cards or Summary grids)."""
    return [
        table
        for table in schema.get("tables") or []
        if isinstance(table, Mapping)
        and table.get("flavor") not in ("Cards", "Summary")
        and table.get("columns")
        and table.get("data_id")
    ]


def matches(rule: Mapping[str, object], run: Profile) -> list[str]:
    """The ways `run` fails `rule`; an empty list means it matches."""
    misses = []
    if run.status != "completed":
        misses.append(f"status is {run.status}")
    wanted = rule.get("execution_type")
    if wanted is not None:
        allowed = [wanted] if isinstance(wanted, str) else list(wanted)  # type: ignore[arg-type]
        if run.execution_type not in allowed:
            misses.append(f"execution type is {run.execution_type}")
    if run.cards < int(rule.get("min_cards", 0)):  # type: ignore[arg-type]
        misses.append(f"{run.cards} cards")
    missing = sorted(set(rule.get("chart_kinds", [])) - set(run.chart_kinds))  # type: ignore[arg-type]
    if missing:
        misses.append(f"no {', '.join(missing)} chart")
    if run.max_table_columns < int(rule.get("min_table_columns", 0)):  # type: ignore[arg-type]
        misses.append(f"widest table has {run.max_table_columns} columns")
    if run.max_table_rows < int(rule.get("min_table_rows", 0)):  # type: ignore[arg-type]
        misses.append(f"longest table has {run.max_table_rows} rows")
    if "authored" in rule and run.authored != rule["authored"]:
        misses.append("authored" if run.authored else "not authored")
    if run.briefings < int(rule.get("min_briefings", 0)):  # type: ignore[arg-type]
        misses.append(f"{run.briefings} briefings")
    if run.category_count < int(rule.get("min_categories", 0)):  # type: ignore[arg-type]
        misses.append(f"{run.category_count} dashboard categories")
    if "has_results" in rule and bool(run.categories) != rule["has_results"]:
        misses.append("has results" if run.categories else "has no results")
    if "ai_text_skipped" in rule and run.ai_text_skipped != rule["ai_text_skipped"]:
        misses.append("AI text not skipped" if rule["ai_text_skipped"] else "AI text skipped")
    return misses


def choose(fixture: Fixture, runs: Iterable[Profile], exclude: Iterable[str] = ()) -> Profile | None:
    """The run to reuse for a fixture: matching runs, best by `prefer`, then newest."""
    if fixture.match is None:
        return None
    excluded = set(exclude)
    candidates = [
        run for run in runs if run.run_id not in excluded and not matches(fixture.match, run)
    ]
    if not candidates:
        return None
    prefer = fixture.prefer
    return max(
        candidates,
        key=lambda run: (getattr(run, prefer) if prefer else 0, run.created_at, run.run_id),
    )
