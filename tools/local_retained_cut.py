"""Explicit, reviewed rollback artifacts; never an ancestry or build fallback.

The primary workspace still passes the normal fresh-main preflight. This cut
selects two previously qualified immutable artifacts without resetting it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import cast

from tools.agent_testing import ServiceIdentity


PROJECTS = {"EpochAI", "EpochBackend", "EpochClientPortal", "EpochPlatform"}
INPUTS = (
    "AgentTesting/local-stack.toml", "AgentTesting/environments.toml",
    "EpochAI/langgraph.json", "EpochAI/requirements.txt", "EpochAI/.env", "EpochAI/.env.local",
    "EpochBackend/services/stratifyx/.env.local", "EpochBackend/build/bin/config.json",
    "EpochPlatform/.env.local", "EpochPlatform/package.json", "EpochPlatform/pnpm-lock.yaml",
    "EpochClientPortal/vite.config.ts", "EpochClientPortal/package.json", "EpochClientPortal/pnpm-lock.yaml",
    "EpochClientPortal/.env", "EpochClientPortal/.env.local", "EpochClientPortal/.env.production",
    "EpochClientPortal/.env.production.local",
)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_hashes(root: Path) -> dict[str, str | None]:
    return {name: file_hash(root / name) if (root / name).is_file() else None for name in INPUTS}


def bundle_hashes(bundle: Path) -> dict[str, str]:
    result = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise ValueError("Retained bundles must not contain symlinks.")
        if path.is_file():
            result[str(path.relative_to(bundle))] = file_hash(path)
    if "index.html" not in result or not any(name.startswith("assets/") and name.endswith(".js") for name in result):
        raise ValueError("The retained UI has no complete index/entry assets.")
    return result


def _pins(value: object) -> dict[str, str]:
    if (not isinstance(value, dict) or set(value) != PROJECTS
            or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40}", v) for v in value.values())):
        raise ValueError("Retained cuts require exact source pins for all four primary projects.")
    return dict(value)


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class RetainedCut:
    manifest_path: Path
    manifest_sha256: str
    admission_path: Path
    admission_sha256: str
    stack: ServiceIdentity
    workspace_sources: dict[str, str]
    runtime_sources: dict[str, str]
    ai_image_id: str
    ui_bundle: Path
    ui_files: dict[str, str]
    ui_configuration: str
    preview_inputs: str
    configuration_inputs: dict[str, str | None]
    native_binary_sha256: str
    reference_exporter_sha256: str
    native_reference_version: str
    source_receipt: dict[str, str]

    @classmethod
    def load(cls, manifest: Path, admission: Path, admission_sha256: str) -> RetainedCut:
        if not _sha(admission_sha256):
            raise ValueError("Supply the exact reviewed retained-cut admission SHA256.")
        for path in (manifest, admission):
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid():
                raise ValueError("Retained-cut files must be owned local files, not symlinks.")
        if file_hash(admission) != admission_sha256:
            raise ValueError("The retained-cut admission changed.")
        reviewed = json.loads(admission.read_text())
        if (not isinstance(reviewed, dict)
                or set(reviewed) != {"schema_version", "purpose", "manifest_sha256", "verdict", "reviewer_thread_id"}
                or type(reviewed["schema_version"]) is not int or reviewed["schema_version"] != 1
                or reviewed["purpose"] != "retained-runtime-rollback" or reviewed["verdict"] != "pass"
                or not isinstance(reviewed["reviewer_thread_id"], str) or not reviewed["reviewer_thread_id"].strip()
                or reviewed["manifest_sha256"] != file_hash(manifest)):
            raise ValueError("A retained cut requires its exact independent passing admission.")
        value = json.loads(manifest.read_text())
        expected = {"schema_version", "mode", "stack", "workspace_sources", "runtime_sources", "ai_image_id",
                    "ui_bundle", "ui_files", "ui_configuration", "preview_inputs", "configuration_inputs",
                    "native_binary_sha256", "reference_exporter_sha256", "native_reference_version", "source_receipt"}
        if (not isinstance(value, dict) or set(value) != expected
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["mode"] != "retained-runtime-rollback"):
            raise ValueError("Unknown retained-cut schema, mode or fields.")
        workspace, runtime = _pins(value["workspace_sources"]), _pins(value["runtime_sources"])
        if any(workspace[name] != runtime[name] for name in ("EpochBackend", "EpochPlatform")):
            raise ValueError("Retained rollback cannot substitute Backend or Platform source.")
        if not isinstance(value["ai_image_id"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["ai_image_id"]):
            raise ValueError("The retained AI image requires its immutable full ID.")
        for key in ("ui_configuration", "preview_inputs", "native_binary_sha256", "reference_exporter_sha256"):
            if not _sha(value[key]):
                raise ValueError(f"Invalid retained {key} SHA256.")
        if not isinstance(value["native_reference_version"], str) or not re.fullmatch(r"[0-9a-f]{16}", value["native_reference_version"]):
            raise ValueError("Invalid retained native reference version.")
        if (not isinstance(value["configuration_inputs"], dict) or set(value["configuration_inputs"]) != set(INPUTS)
                or not isinstance(value["source_receipt"], dict) or set(value["source_receipt"]) != {"path", "sha256"}
                or not isinstance(value["ui_files"], dict) or not value["ui_files"]
                or not isinstance(value["stack"], dict) or set(value["stack"]) != ServiceIdentity.__required_keys__
                or any(not isinstance(v, str) for v in value["stack"].values())
                or any(v is not None and not _sha(v) for v in value["configuration_inputs"].values())
                or any(not isinstance(k, str) or not _sha(v) for k, v in value["ui_files"].items())
                or not _sha(value["source_receipt"]["sha256"])
                or any(not isinstance(v, str) or not Path(v).is_absolute()
                       for v in (value["ui_bundle"], value["source_receipt"]["path"]))):
            raise ValueError("The retained cut has an incomplete configuration or artifact inventory.")
        return cls(manifest.resolve(), file_hash(manifest), admission.resolve(), admission_sha256,
                   cast(ServiceIdentity, value["stack"]), workspace, runtime, value["ai_image_id"], Path(value["ui_bundle"]),
                   value["ui_files"], value["ui_configuration"], value["preview_inputs"],
                   value["configuration_inputs"], value["native_binary_sha256"], value["reference_exporter_sha256"],
                   value["native_reference_version"], value["source_receipt"])

    def validate(self, *, root: Path, state: Path, paths: dict[str, Path], workspace: dict,
                 identity: ServiceIdentity, environment: str, ui_configuration: str, preview_inputs: str) -> None:
        """Called only after the unchanged normal fresh-main checkout preflight."""
        if (file_hash(self.manifest_path) != self.manifest_sha256
                or file_hash(self.admission_path) != self.admission_sha256):
            raise ValueError("The admitted retained-cut files changed after loading.")
        if environment != "local-up" or identity != self.stack:
            raise ValueError("The retained cut belongs to a different local stack.")
        if {name: workspace[name]["commit"] for name in PROJECTS} != self.workspace_sources:
            raise ValueError("Current primary workspace differs from the reviewed rollback workspace.")
        if input_hashes(root) != self.configuration_inputs or (ui_configuration, preview_inputs) != (self.ui_configuration, self.preview_inputs):
            raise ValueError("Retained configuration/graph/dependency inputs changed.")
        native = paths["backend_binary"]
        if file_hash(native) != self.native_binary_sha256 or file_hash(native.with_name("epoch_reference_files")) != self.reference_exporter_sha256:
            raise ValueError("The retained cut requires the exact primary native binaries.")
        bundle = self.ui_bundle
        if (bundle.is_symlink() or not bundle.resolve(strict=True).is_relative_to((state / "ui-builds").resolve())
                or bundle_hashes(bundle) != self.ui_files):
            raise ValueError("The original managed retained UI bundle changed or moved.")
        receipt_path = Path(self.source_receipt["path"])
        if file_hash(receipt_path) != self.source_receipt["sha256"]:
            raise ValueError("The retained runtime qualification receipt changed.")
        receipt = json.loads(receipt_path.read_text())
        aliases = {"EpochAI": "ai", "EpochBackend": "backend", "EpochClientPortal": "portal", "EpochPlatform": "platform"}
        try:
            if (any(receipt["source_pins"][alias] != self.runtime_sources[name] for name, alias in aliases.items())
                    or receipt["image"] != self.ai_image_id or receipt["ui_bundle"] != str(bundle)
                    or receipt["health"]["services"] != identity
                    or receipt["health"]["ai"]["deployment"]["git_sha"] != self.runtime_sources["EpochAI"]
                    or receipt["health"]["ai"]["reference_version"] != self.native_reference_version
                    or receipt["health"]["backend"]["git_sha"] != self.runtime_sources["EpochBackend"]
                    or receipt["ui_index_sha256"] != self.ui_files["index.html"]
                    or not receipt["ui_asset"].startswith("/assets/")
                    or self.ui_files.get(receipt["ui_asset"].lstrip("/")) != receipt["ui_asset_sha256"]):
                raise ValueError("The retained artifacts do not match their qualified runtime receipt.")
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("The retained runtime qualification receipt is incomplete.") from error
        for name in ("langgraph.json", "requirements.txt"):
            old = subprocess.check_output(["git", "-C", str(paths["ai_dir"]), "show",
                                           self.runtime_sources["EpochAI"] + ":" + name])
            if hashlib.sha256(old).hexdigest() != file_hash(paths["ai_dir"] / name):
                raise ValueError("Current graph/dependency declarations cannot launch the retained AI image.")
        actual = subprocess.run(["docker", "image", "inspect", self.ai_image_id, "--format", "{{.Id}}"],
                                capture_output=True, text=True)
        if actual.returncode or actual.stdout.strip() != self.ai_image_id:
            raise ValueError("The exact retained AI image is missing; rollback never builds or pulls it.")

    def metadata(self) -> dict:
        return {"mode": "retained-runtime-rollback", "manifest": str(self.manifest_path),
                "manifest_sha256": self.manifest_sha256, "admission": str(self.admission_path),
                "admission_sha256": self.admission_sha256, "workspace_sources": self.workspace_sources,
                "runtime_sources": self.runtime_sources, "ai_image_id": self.ai_image_id,
                "ui_bundle": str(self.ui_bundle), "ui_configuration": self.ui_configuration,
                "native_reference_version": self.native_reference_version,
                "portal_refresh_capability": "disabled in retained mode"}

    def validate_served_ui(self, checked: dict) -> None:
        if (checked.get("index_sha256") != self.ui_files["index.html"]
                or not isinstance(checked.get("entry_assets"), dict) or not checked["entry_assets"]
                or any(not isinstance(name, str) or not name.startswith("/assets/")
                       or self.ui_files.get(name.lstrip("/")) != digest
                       for name, digest in checked["entry_assets"].items())):
            raise ValueError("Served retained UI differs from its admitted artifact inventory.")
