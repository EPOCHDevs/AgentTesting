"""The attempt's summary as Markdown. Pure."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

SYMBOL = {"pass": "pass", "fail": "**FAIL**", "skip": "skip"}


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(summary: Mapping[str, object]) -> str:
    lines = [
        f"# Export end-to-end: {summary['attempt']}",
        "",
        f"- Environment: {summary['environment']} (Terminal {summary['ui_url']}, Backend {summary['backend_url']})",
        f"- Started {summary['started_at']}, finished {summary.get('finished_at', '—')}",
        f"- Suite commit {summary.get('suite_commit', 'unknown')}; Chrome {summary.get('chrome', 'unknown')}; Playwright {summary.get('playwright', 'unknown')}",
        "- Browser timezone UTC; dates on the cover and in the file name are UTC.",
        "",
        "## Results",
        "",
        "| Fixture | Shape | Run | Origin | Outcome | Failed checks |",
        "|---|---|---|---|---|---|",
    ]
    fixtures: Sequence[Mapping[str, object]] = summary["fixtures"]  # type: ignore[assignment]
    for item in fixtures:
        failed = [r["check"] for r in item.get("results", []) if r["status"] == "fail"]  # type: ignore[index, union-attr]
        lines.append(
            "| "
            + " | ".join(
                _cell(v)
                for v in (
                    item["id"],
                    item["shape"],
                    item.get("run_id") or "—",
                    item.get("origin") or "—",
                    SYMBOL.get(str(item["outcome"]), item["outcome"]),
                    ", ".join(failed) or "—",
                )
            )
            + " |"
        )
    checked = [f for f in fixtures if f.get("results")]
    if checked:
        names: list[str] = []
        for item in checked:
            for result in item["results"]:  # type: ignore[union-attr]
                if result["check"] not in names:
                    names.append(result["check"])
        lines += ["", "## Checks", "", "| Check | " + " | ".join(str(f["id"]) for f in checked) + " |"]
        lines.append("|---|" + "---|" * len(checked))
        for name in names:
            row = []
            for item in checked:
                found = next((r for r in item["results"] if r["check"] == name), None)  # type: ignore[union-attr]
                row.append(SYMBOL.get(found["status"], found["status"]) if found else "—")
            lines.append(f"| {name} | " + " | ".join(row) + " |")
    coverage: Mapping[str, Sequence[Mapping[str, str]]] = summary.get("chart_kinds", {})  # type: ignore[assignment]
    if coverage:
        lines += ["", "## Chart kinds", "", "| Kind | Printed by | Images |", "|---|---|---|"]
        for kind, found in coverage.items():
            lines.append(
                f"| {kind} | "
                + (", ".join(f["fixture"] for f in found) or "not covered")
                + " | "
                + (", ".join(SYMBOL.get(f["images"], f["images"]) for f in found) or "—")
                + " |"
            )
    formats: Mapping[str, str] = summary.get("formats", {})  # type: ignore[assignment]
    if formats:
        lines += ["", "## Formats", ""]
        lines += [f"- {name}: {state}" for name, state in formats.items()]
    uncovered = [f for f in fixtures if f["outcome"] == "uncovered"]
    if uncovered:
        lines += ["", "## Not covered", ""]
        lines += [f"- **{f['id']}** ({f['shape']}): {f.get('reason')}" for f in uncovered]
    problems = [f for f in fixtures if f["outcome"] in ("fail", "error")]
    if problems:
        lines += ["", "## Failures", ""]
        for item in problems:
            lines.append(f"### {item['id']}")
            if item.get("error"):
                lines.append(f"- error: {item['error']}")
            for result in item.get("results", []):  # type: ignore[union-attr]
                if result["status"] == "fail":
                    lines.append(f"- {result['check']}: {result['detail']}")
            lines.append(f"- Evidence: `{item.get('evidence')}`")
            lines.append("")
    notes = [(f["id"], n) for f in fixtures for r in f.get("results", []) for n in r.get("notes", [])]  # type: ignore[union-attr]
    notes += [(f["id"], n) for f in fixtures for n in f.get("notes", [])]  # type: ignore[union-attr]
    if notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {fixture}: {note}" for fixture, note in notes]
    cost = summary.get("cost")
    if isinstance(cost, Mapping):
        lines += ["", "## Cost", "", str(cost.get("statement", ""))]
        for label in ("usage_before", "usage_after"):
            usage = cost.get(label)
            if isinstance(usage, Mapping):
                meters = ", ".join(
                    f"{m['meter']} {m['used']}/{m['limit']}" for m in usage.get("meters", [])  # type: ignore[union-attr]
                )
                lines.append(f"- {label.replace('_', ' ')} ({usage.get('read_at')}): {meters}")
    lines.append("")
    return "\n".join(lines)
