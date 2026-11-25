"""Shared native execution/Arrow helpers; the intermediate Save driver is retired.

Uses the installed generated native SDK and synthetic owned fixture only. This
does not create a campaign, call an LLM, deploy services or alter a shared stack.
Every numerical launch has a retained UUID before submission; unknown outcomes
stop the driver instead of resubmitting work. Retain a failed evidence directory.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx
import pyarrow as pa
import pyarrow.ipc as ipc
from stratifyx import DataOption, DatePeriodConfig, RuntimeProfile, StratifyX

from .native_results import assert_tables

OPTIONS = {"max_retries": 0}


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def table_answers(client: StratifyX, execution: str, root: Path, reference: dict) -> dict:
    output = client.sdk_requests.get_execution_outputs(execution, request_options=OPTIONS)
    if not output.response_id:
        raise AssertionError("The completed execution has no actual answer receipt")
    response = client.research_responses.get_research_response(output.response_id, request_options=OPTIONS)
    tables, page_sizes = {}, {}
    for part in response.content.outputs:
        if not hasattr(part, "source"):
            raise AssertionError("The owned fixture returned an undeclared non-table answer")
        source = part.source
        if source.execution_id != execution or part.name in tables:
            raise AssertionError("The answer belongs to another execution or repeats a part")
        rows, sizes = [], []
        for offset in range(0, source.row_count, 3):
            raw = b"".join(client.research_responses.read_research_response_table(
                execution, table=source.table, field_ids=source.field_ids,
                offset=offset, page_size=3, request_options=OPTIONS))
            table = ipc.open_stream(pa.BufferReader(raw)).read_all()
            if table.column_names != source.field_ids:
                raise AssertionError("The Arrow transfer changed the declared field order")
            columns = [column.cast(pa.int64()).to_pylist() if pa.types.is_timestamp(column.type)
                       else column.to_pylist() for column in table.columns]
            batch = list(map(list, zip(*columns)))
            if len(batch) != min(3, source.row_count - offset):
                raise AssertionError("The Arrow transfer omitted declared rows")
            rows.extend(batch)
            sizes.append(len(batch))
        tables[part.name] = {"columns": source.field_ids, "rows": rows}
        page_sizes[part.name] = sizes
    assert_tables(tables, reference)
    write(root / "answers.json", {"tables": tables, "page_sizes": page_sizes,
                                  "response_id": output.response_id, "response_version": output.response_version})
    return page_sizes


def execute(client: StratifyX, source: str, name: str, root: Path, dispatch: str = "inprocess",
            *, checkpoint_only: bool = False) -> tuple[str, str]:
    root.mkdir()
    (root / "source.epoch").write_text(source)
    execution = str(uuid4())
    intent = {"execution_id": execution, "stage": "create-definition", "accepted": False}
    write(root / "intent.json", intent)
    created = client.studies_definitions.create_manual_study_definition(
        data=DataOption(assets=[]), description="Synthetic owned lifecycle fixture; no vendor inputs.",
        name=name, source=source, request_options=OPTIONS)
    intent.update(definition_id=created.id, stage="execute")
    write(root / "intent.json", intent)
    if checkpoint_only:
        accepted = client.studies_executions.start_execute_a_study(
            id=created.id, execution_id=execution,
            runtime=RuntimeProfile(dispatch=dispatch), request_options=OPTIONS)
    else:
        accepted = client.studies_executions.start_execute_a_study(
            id=created.id, execution_id=execution,
            period=DatePeriodConfig(start=date(2022, 1, 1), end=date(2025, 1, 31)),
            runtime=RuntimeProfile(dispatch=dispatch), request_options=OPTIONS)
    if accepted.id != execution:
        raise AssertionError("Native execution identity differs from the retained launch intent")
    intent.update(stage="poll", accepted=True)
    write(root / "intent.json", intent)
    deadline = time.monotonic() + 120
    while True:
        job = client.studies_executions.get_execution_by_id(execution, request_options=OPTIONS)
        status = job.status.value if hasattr(job.status, "value") else str(job.status)
        intent["status"] = status
        write(root / "intent.json", intent)
        if status.lower() in {"completed", "failed", "cancelled"}:
            if status.lower() != "completed":
                raise AssertionError("The owned native execution did not complete successfully")
            break
        if time.monotonic() > deadline:
            raise TimeoutError("Owned execution deadline exceeded; read the retained UUID before doing more work")
        time.sleep(0.5)
    return created.id, execution



def main() -> None:
    raise SystemExit("The intermediate Backend-owned saved-model driver is retired. "
        "Use tools.model_lifecycle_e2e.platform_owned with explicit Native and Platform origins. "
        "Historical receipts remain evidence for their original cut only.")


if __name__ == "__main__":
    main()
