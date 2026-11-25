"""Maintained opaque credential boundary for explicitly owned local evidence.

Expected owner is an admission input, never a decoded token claim. Live drivers
must compare it with actual owned producer/artifact metadata before reuse.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from dataclasses import dataclass
import re
from typing import Callable

PROVIDER = Path('/home/adesola/EpochDev/EpochAI/tools/cognito_auth.py')
PROVIDER_SHA256 = '04281614f60473dac30325b23389c165d5ef9fe9282785250fb87170a242a9ed'
TokenProvider = Callable[[], str | None]


@dataclass(frozen=True)
class PrimaryReadiness:
    sha256: str
    native_source: str


def maintained_provider() -> str | None:
    if PROVIDER.is_symlink() or hashlib.sha256(PROVIDER.read_bytes()).hexdigest() != PROVIDER_SHA256:
        raise ValueError('The admitted maintained opaque token provider changed')
    spec = importlib.util.spec_from_file_location('epoch_primary_opaque_credentials', PROVIDER)
    if spec is None or spec.loader is None:
        raise ValueError('The maintained opaque token provider cannot be loaded')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.get_id_token()


def caller(expected_owner: str, provider: TokenProvider | None = None) -> tuple[str, str]:
    if not isinstance(expected_owner, str) or not expected_owner:
        raise ValueError('An explicit expected owned-resource principal is required')
    selected = maintained_provider if provider is None else provider
    token = selected()
    if not isinstance(token, str) or not token:
        raise ValueError('The maintained provider returned no opaque credential; stop')
    return token, expected_owner


def admit_primary(path: Path, digest: str, *, native_url: str, platform_url: str,
                   native_root: Path, expected_owner: str) -> PrimaryReadiness:
    """Read-only root packet join; no credential or runtime call occurs here."""
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Use the bounded actual root primary readiness packet')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise ValueError('Actual root readiness bytes changed')
    value = json.loads(body)
    if (value.get('kind') != 'ROOT_ADMITTED_PRIMARY_MODEL_LIFECYCLE_RUNTIME_V1'
            or value.get('ready') is not True
            or value.get('native_url') != native_url.rstrip('/')
            or value.get('platform_url') != platform_url.rstrip('/')
            or value.get('native_root') != str(native_root)
            or value.get('owner_user_id') != expected_owner):
        raise ValueError('The actual primary/runtime/expected-owner admission differs')
    source = value.get('native_source')
    if not isinstance(source, str) or not re.fullmatch('[a-f0-9]{40}', source):
        raise ValueError('Actual served Native source is required')
    return PrimaryReadiness(digest, source)
