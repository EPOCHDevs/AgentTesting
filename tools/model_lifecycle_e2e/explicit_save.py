"""Bounded explicit-Save orchestration, importable without installed native SDKs.

The caller owns exact completed-fit selection, client construction and before/after
catalogue/execution checks. These controls do not simulate server idempotency.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

if TYPE_CHECKING:
    from epoch_sdk.models_api.types import SavedModelSummary


def save_with_changed_ids_and_names(
    save: Callable[[str, str], SavedModelSummary], evidence: Path,
) -> SavedModelSummary:
    """Race two first saves, then repeat with another ID/name after readiness."""
    first = [(str(uuid4()), name) for name in
             ('Owned selected OLS', 'Concurrent name for the same fit')]

    def record(name, value):
        (evidence/name).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')

    record('first-save-intents.json', [{'request_id': ref, 'name': name} for ref, name in first])

    def once(index):
        ref, name = first[index]
        try:
            result = save(ref, name)
        except Exception as error:
            record(f'first-save-{index}-unconfirmed.json', {'error_type': type(error).__name__,
                'request_id': ref, 'action': 'STOP; no automatic resubmission'})
            raise
        record(f'first-save-{index}-response.json', result.model_dump(mode='json'))
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(once, range(2)))
    saved = results[0]
    assert all(item == saved for item in results), 'Concurrent saves created different models'
    assert saved.name in {name for _, name in first}
    ref, name = str(uuid4()), 'A later name must not rename the model'
    record('ready-repeat-intent.json', {'request_id': ref, 'name': name})
    assert save(ref, name) == saved, 'Repeated Save changed the original model or name'
    return saved
