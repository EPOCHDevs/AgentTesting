"""Owned local qualification of native model computation/provenance publication.

This intentionally stops before Platform Save. It creates synthetic fixture
executions on the explicitly selected task server and reads its private output
tree. It does not change a shared stack, call an LLM or contact a data vendor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from stratifyx import StratifyX

from .http_effects import fit_count, stable_lines
from .http_owned import execute, table_answers, write
from .oracle import expected
from .source import fixture_source


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def descriptor(native_root: Path, execution: str, source: str, owner: str, fits: int, receipt: Path) -> dict:
    directory = native_root / "research_studies" / owner / execution
    raw = (directory / "model_fits.json").read_bytes()
    value = json.loads(raw)
    completion = json.loads((directory / "manifest.json").read_bytes())["model_fits"]
    config = (directory / "config.json").read_bytes()
    witness = (directory / "config.sha256").read_text()
    assert completion == {"schema_version": 1, "sha256": sha(raw), "bytes": len(raw)}
    assert sha(config) == witness == value["producer_config_sha256"]
    assert value["schema_version"] == 1 and value["execution_id"] == execution
    assert value["owner_user_id"] == owner
    assert value["producer"]["study"]["source"] == source
    assert value["producer"]["execution_id"] == execution
    assert value["definition_id"] == value["producer"]["definition_id"]
    metadata = json.loads((directory / "metadata.json").read_bytes())
    assert value["producer"]["captured_at"] == metadata["created_at_ms"] > 0
    assert len(value["fits"]) == fits
    assert value["storage_location"] == ("server" if fits else "none")
    logs, logs_sha = stable_lines(directory / "logs.jsonl")
    events, events_sha = stable_lines(directory / "events.jsonl")
    effects = fit_count(logs, events, execution, fits)
    if fits:
        selected = value["fits"][0]
        assert selected["fit_name"] == "owned_fixture"
        manifest = selected["artifact"]["manifest"]
        binding = selected["artifact"]["binding"]
        assert manifest["family"] == binding["family"] == "OLS"
        assert manifest["producer_execution_id"] == execution and manifest["owner_user_id"] == owner
        candidates = list(native_root.glob(f"owners/{owner.encode().hex()}/models/v1/artifacts/{binding['digest'][7:]}"))
        assert len(candidates) == 1, "Use this fixture's explicit native model store root"
        payload = candidates[0].read_bytes()
        assert "sha256:" + sha(payload) == binding["digest"]
        assert manifest["supervised_training"]
        assert manifest["ordered_features"]
        assert "save_as" not in selected
        assert not list((native_root / "owners" / owner.encode().hex() / "models/v1/names").glob("*")), "Fitting must not publish a saved-library name"
    receipt.mkdir()
    (receipt / "model_fits.json").write_bytes(raw)
    write(receipt / "completion-model-fits.json", completion)
    write(receipt / "fit-effects.json", effects | {"logs_sha256": logs_sha, "events_sha256": events_sha})
    return value


def run(url: str, token_file: Path, native_root: Path, fixture: Path, evidence: Path, dispatch: str) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("Choose an explicit loopback task server")
    if not all(path.is_absolute() for path in (native_root, fixture, evidence)):
        raise ValueError("Use absolute private fixture/evidence paths")
    evidence.mkdir(mode=0o700)
    token = json.loads(token_file.read_text())["id_token"]
    # Identity is only an assertion witness here. The server authorizes and
    # verifies the normal signed caller credential on every API request.
    import base64
    part = token.split(".")[1]
    owner = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["sub"]
    oracle = expected(fixture)
    with httpx.Client(timeout=90, trust_env=False) as transport:
        client = StratifyX(base_url=url.rstrip("/"), token=token, httpx_client=transport, follow_redirects=False)
        source = fixture_source(fixture, stage="fit")
        definition, producer = execute(client, source, f"Owned native provenance ({dispatch})", evidence / "fit", dispatch)
        table_answers(client, producer, evidence / "fit", oracle["holdout"])
        produced = descriptor(native_root, producer, source, owner, 1, evidence / "producer")
        continued_source = fixture_source(fixture, stage="continue", producer_execution=producer)
        _, continued = execute(client, continued_source, f"Owned native continuation ({dispatch})", evidence / "continue", dispatch)
        table_answers(client, continued, evidence / "continue", oracle["continuation"])
        descriptor(native_root, continued, continued_source, owner, 0, evidence / "consumer")
        result = {"status": "passed", "scope": "native owned numerical/provenance output; no Platform Save qualification",
                  "dispatch": dispatch, "definition_id": definition, "producer_execution": producer,
                  "continuation_execution": continued, "digest": produced["fits"][0]["artifact"]["binding"]["digest"],
                  "producer_config_sha256": produced["producer_config_sha256"],
                  "effects": {"producer_fits": 1, "continuation_fits": 0}}
        write(evidence / "receipt.json", result)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--dispatch", choices=["inprocess", "subprocess"], required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.url, args.token_file, args.native_root, args.fixture, args.evidence, args.dispatch), indent=2))
