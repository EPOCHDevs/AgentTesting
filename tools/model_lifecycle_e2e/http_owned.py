"""Opt-in engineering journey through an explicitly selected loopback Backend.

Uses the installed generated native SDK and synthetic owned fixture only. This
does not create a campaign, call an LLM, deploy services or alter a shared stack.
Every numerical launch has a retained UUID before submission; unknown outcomes
stop the driver instead of resubmitting work. Retain a failed evidence directory.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pyarrow as pa
import pyarrow.ipc as ipc
from stratifyx import DataOption, DatePeriodConfig, RuntimeProfile, StratifyX

from .native_results import assert_tables
from .oracle import expected
from .source import fixture_source

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


def execute(client: StratifyX, source: str, name: str, root: Path, dispatch: str = "inprocess") -> tuple[str, str]:
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


def journey(url: str, token_file: Path, fixture: Path, root: Path, save_name: str,
            producer_execution: str | None = None) -> dict:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Choose the explicit loopback task Backend origin")
    if not root.is_absolute():
        raise ValueError("Choose a new absolute owned evidence directory")
    root.mkdir(mode=0o700)
    token = json.loads(token_file.read_text())["id_token"]
    oracle = expected(fixture)
    fit_source = fixture_source(fixture, stage="fit")
    with httpx.Client(timeout=90, trust_env=False) as transport:
        client = StratifyX(base_url=url.rstrip("/"), token=token,
                           httpx_client=transport, follow_redirects=False)
        original_saved = client.saved_models.list_explicitly_saved_models_with_bounded_metadata_pages(
            limit=100, request_options=OPTIONS)
        if original_saved.items or original_saved.next_cursor:
            raise ValueError("Use the private empty fixture account/store, not an existing model library")
        if producer_execution is None:
            _, producer_execution = execute(client, fit_source, "Owned OLS producer", root / "fit")
        else:
            (root / "fit").mkdir()
        original = client.studies_executions.get_the_per_execution_study_snapshot_as_run_source_assets_screen(
            producer_execution, request_options=OPTIONS)
        if original.study.source != fit_source:
            raise AssertionError("The selected original producer is not this exact owned fixture")
        fits = client.saved_models.list_reusable_fits_from_an_owned_completed_producer(
            producer_execution, limit=10, request_options=OPTIONS)
        if len(fits.items) != 1 or fits.next_cursor or fits.items[0].model_ref != "owned_fixture":
            raise AssertionError("Select exactly the owned producer's declared reusable fit")
        selected = fits.items[0]
        digest = selected.digest
        fit_pages = table_answers(client, producer_execution, root / "fit", oracle["holdout"])
        _, continuation = execute(client, fixture_source(fixture, stage="continue",
                                  producer_execution=producer_execution), "Owned exact continuation", root / "continue")
        continuation_pages = table_answers(client, continuation, root / "continue", oracle["continuation"])
        if client.saved_models.list_reusable_fits_from_an_owned_completed_producer(
                continuation, request_options=OPTIONS).items:
            raise AssertionError("Continuation unexpectedly produced a new fit")
        if client.saved_models.list_explicitly_saved_models_with_bounded_metadata_pages(
                limit=1, request_options=OPTIONS).items:
            raise AssertionError("Fitting or continuing populated the saved catalogue")
        before_save = client.studies_executions.list_executions(request_options=OPTIONS)
        write(root / "save-intent.json", {"source_execution_id": producer_execution,
              "fit_name": "owned_fixture", "expected_digest": digest, "name": save_name})

        def save():
            return client.saved_models.save_one_exact_fit_from_an_owned_completed_execution_without_refitting(
                source_execution_id=producer_execution, fit_name="owned_fixture",
                expected_digest=digest, name=save_name, request_options=OPTIONS)

        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = list(pool.map(lambda _: save(), range(2)))
        repeated = save()
        if (left != right or left != repeated or left.summary.digest != digest
                or left.artifact.summary.digest != digest
                or left.producer.execution_id != producer_execution or left.producer.study.source != fit_source):
            raise AssertionError("Equal Save calls changed identity, fitted state or pinned provenance")
        if client.studies_executions.list_executions(request_options=OPTIONS) != before_save:
            raise AssertionError("Saving launched numerical work")
        (root / "saved-detail.json").write_text(left.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n")
        saved_ref = left.summary.model_ref
    # A new HTTP client has no in-memory selection state to inherit.
    with httpx.Client(timeout=90, trust_env=False) as transport:
        client = StratifyX(base_url=url.rstrip("/"), token=token, httpx_client=transport, follow_redirects=False)
        reopened = client.saved_models.read_an_exact_saved_model_and_its_pinned_producer_study(
            saved_ref, request_options=OPTIONS)
        if reopened != left:
            raise AssertionError("Reopened saved model differs from its accepted receipt")
        _, consumer = execute(client, fixture_source(fixture, stage="saved", model_ref=saved_ref),
                              "Owned saved-model consumer", root / "saved")
        saved_pages = table_answers(client, consumer, root / "saved", oracle["continuation"])
        if client.saved_models.list_reusable_fits_from_an_owned_completed_producer(
                consumer, request_options=OPTIONS).items:
            raise AssertionError("Saved-model consumption unexpectedly produced a fit")
        page = client.saved_models.list_explicitly_saved_models_with_bounded_metadata_pages(
            limit=1, request_options=OPTIONS)
        if page.next_cursor or len(page.items) != 1 or page.items[0] != left.summary:
            raise AssertionError("The saved catalogue does not identify the single accepted resource")
    receipt = {"boundary": "generated Python SDK and real authenticated local HTTP",
               "producer_execution": producer_execution, "continuation_execution": continuation,
               "saved_consumer_execution": consumer, "saved_ref": saved_ref, "digest": digest,
               "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
               "page_sizes": {"holdout": fit_pages, "continuation": continuation_pages, "saved": saved_pages},
               "concurrent_and_repeated_save": "same accepted identity; zero additional execution identities",
               "pending": ["retained HTTP fit-event audit", "source cleanup/restart", "saved-Study scope HTTP",
                           "TypeScript HTTP", "Space/AI/Portal", "new pinned SPY scenario"]}
    write(root / "receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures/owned-observed-predictor.csv")
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".config/epoch-agent-testing/local-login.json")
    parser.add_argument("--save-name", required=True)
    parser.add_argument("--producer-execution", help="Reuse an already accepted exact owned fixture; never refit it")
    args = parser.parse_args()
    result = journey(args.backend_url, args.token_file, args.fixture, args.evidence,
                     args.save_name, args.producer_execution)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
