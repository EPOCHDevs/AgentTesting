"""Create the explicit synthetic saved-model scoring notebook, without executing it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4


def notebook(repo: Path, receipt: Path, evidence: Path, output: Path, kernel_name: str = 'python3') -> None:
    if not all(path.is_absolute() for path in (repo, receipt, evidence, output)):
        raise ValueError('Use exact absolute repository, receipt, evidence and notebook paths')
    if output.exists() or evidence.exists():
        raise ValueError('Preserve existing notebooks/attempts; choose new output and evidence paths')
    cells = []
    def cell(kind: str, source: str) -> None:
        value = {'id': uuid4().hex[:8], 'cell_type': kind, 'metadata': {}, 'source': source.splitlines(keepends=True)}
        if kind == 'code': value.update(execution_count=None, outputs=[])
        cells.append(value)
    cell('markdown', '''# Saved-model scoring — owned acceptance harness

This notebook uses an already saved synthetic OLS model from the retained API
journey. **Run explicitly executes one scoring-only study.** It never trains,
saves, replaces or deletes a model. It verifies every prediction, stored training
baseline and score against the independent owned-fixture oracle, then audits
the completed execution's retained fit events.

Use the exact installed versioned SDKs and the qualified private services. Login
uses the normal AgentTesting token cache; credentials are never printed or saved
in this notebook. Rerunning a submitted cell stops on its existing evidence path
so an uncertain execution is inspected instead of resubmitted.
''')
    cell('code', f'''from pathlib import Path
import sys
from importlib.metadata import version
import epoch_sdk
import stratifyx

repo = Path({str(repo)!r})
receipt_path = Path({str(receipt)!r})
evidence = Path({str(evidence)!r})
sys.path.insert(0, str(repo))
runtime = {{"python_executable": sys.executable, "python_version": sys.version,
    "native_sdk": {{"version": version("stratifyx"), "module": stratifyx.__file__}},
    "shared_sdk": {{"version": version("epoch-sdk"), "module": epoch_sdk.__file__}}}}
print(runtime)
''')
    cell('code', '''import json
import httpx
from epoch_sdk.models_api import EpochModels
from stratifyx import StratifyX
from tools.model_lifecycle_e2e.platform_owned import caller, loopback
from tools.model_lifecycle_e2e.http_owned import execute, table_answers, write
from tools.model_lifecycle_e2e.platform_owned import effects
from tools.model_lifecycle_e2e.oracle import expected
from tools.model_lifecycle_e2e.source import fixture_source

original = json.loads(receipt_path.read_text())
assert original["status"] == "passed", "Qualify the API journey before the notebook"
native_url = loopback(original["native_url"])
platform_url = loopback(original["platform_url"])
token, owner = caller(Path.home() / ".config/epoch-agent-testing/local-login.json")
assert owner == original["owner"]
fixture = repo / "tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv"
native_root = Path(original["native_root"])
''')
    cell('code', '''assert not evidence.exists(), "Inspect the retained attempt before any further execution"
with httpx.Client(timeout=120, trust_env=False) as native_http, httpx.Client(timeout=30, trust_env=False) as model_http:
    models = EpochModels(base_url=platform_url, token=token, httpx_client=model_http, follow_redirects=False)
    selected = models.read_model(original["model_ref"], request_options={"max_retries": 0})
    assert selected.model.digest == original["digest"]
    assert selected.model.manifest_sha256 == original["manifest_sha256"]
    source = fixture_source(fixture, stage="saved", model_ref=selected.model.model_ref,
        model_manifest_sha256=selected.model.manifest_sha256)
    native = StratifyX(base_url=native_url, token=token, httpx_client=native_http, follow_redirects=False)
    _, execution = execute(native, source, "Notebook exact saved OLS scoring", evidence, original["dispatch"])
    table_answers(native, execution, evidence, expected(fixture)["continuation"])
    effects(native_root, owner, execution, 0, evidence / "fit-effects.json")
write(evidence / "notebook-receipt.json", {"status": "passed", "execution_id": execution, "runtime": runtime,
    "model_ref": original["model_ref"], "digest": original["digest"], "manifest_sha256": original["manifest_sha256"],
    "fit_count": 0, "scope": "real notebook API/SDK numerical scoring; owned fixture; no AI/Portal/direct-S3 claim"})
print({"status": "passed", "execution_id": execution, "fit_count": 0})
''')
    output.write_text(json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {
        'kernelspec': {'display_name': kernel_name, 'language': 'python', 'name': kernel_name},
        'language_info': {'name': 'python', 'version': '3.12'}}, 'cells': cells}, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kernel-name', default='python3', help='Exact installed/private kernel to execute this notebook')
    args = parser.parse_args()
    notebook(args.repo, args.receipt, args.evidence, args.output, args.kernel_name)


if __name__ == '__main__': main()
