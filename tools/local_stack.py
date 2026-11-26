"""Start the local services; Ctrl+C stops only these child processes."""

import argparse
import asyncio
from collections.abc import Callable
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from tools.agent_testing import CONTAINER_HOST, Stack, check_stack
from tools.local_agent_processes import verify_local_agent_isolation
from tools.local_auth import CLI_CLIENT, CLIENT, POOL, REGION, cached_token, login, token_path
from tools.local_retained_cut import RetainedCut
from tools.local_ui_refresh import (
    CAPABILITY, Controller, Preview, clear_capability, probe, process_start, request_refresh, write_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Backend, AI and the Portal integrate on main; Epoch Platform integrates on dev.
PROJECT_BRANCHES = {
    "EpochBackend": "main",
    "EpochAI": "main",
    "EpochClientPortal": "main",
    "EpochPlatform": "dev",
}
# Behind the front door at the profile's backend_url, as behind api.<env>.epoch.trade's load
# balancer: Backend, and Epoch Platform's general service for Files and Space.
BACKEND_PORT = 9010
PLATFORM_PORT = 9020
FRONT_DOOR = Path(__file__).resolve().parent / "front_door.py"


def primary_paths(config_file: Path) -> tuple[dict, dict[str, Path]]:
    """The default stack always uses the primary sibling repositories."""
    config = tomllib.loads(config_file.read_text()) if config_file.exists() else {}
    paths = {
        "backend_binary": PROJECT_ROOT
        / "EpochBackend/build/bin/epoch_stratifyx_server",
        "backend_env": PROJECT_ROOT / "EpochBackend/services/stratifyx/.env.local",
        "ai_dir": PROJECT_ROOT / "EpochAI",
        "ui_dir": PROJECT_ROOT / "EpochClientPortal",
        "platform_dir": PROJECT_ROOT / "EpochPlatform",
    }
    unknown = set(config) - set(paths) - {"node_bin", "market_pages", "documents"}
    if unknown:
        raise ValueError(
            f"Unknown local launcher settings: {', '.join(sorted(unknown))}"
        )
    for key, expected in paths.items():
        if key in config:
            supplied = (
                config_file.resolve().parent / Path(config[key]).expanduser()
            ).resolve()
            if supplied != expected.resolve():
                raise ValueError(
                    f"{key} must use the primary project path {expected}. "
                    "Worktree overrides are not enabled; remove the old path from local-stack.toml."
                )
    resolved = {key: path.resolve() for key, path in paths.items()}
    for key in paths:
        if resolved[key] != paths[key]:
            raise ValueError(
                f"{key} must not be redirected by a symlink; use the fixed primary project path."
            )
    return config, resolved


# Local Markets pages are opt-in. The local stack uses the production vendor
# accounts, and automatic page refresh spent the shared Barchart hourly quota
# that production pages and local research runs also need (10 Nov).
DEFAULT_MARKET_PAGES = False


def configure_market_pages(
    binary: Path, enabled: bool | None, *, catalog_root: str | None = None
) -> dict[str, str]:
    """Apply an explicit local Markets choice to the generated runtime config."""
    if enabled is None:
        return {}
    if not isinstance(enabled, bool):
        raise ValueError("market_pages must be true or false.")
    if enabled and (
        not catalog_root
        or not Path(catalog_root).is_absolute()
        or not (Path(catalog_root) / "current/catalog_manifest.json").is_file()
    ):
        raise ValueError(
            "Market pages require EPOCH_CATALOG_ROOT in the primary Backend .env.local, "
            "pointing to an absolute path with a selected catalogue generation. "
            "Backend validates that generation; an unversioned asset file is insufficient."
        )
    path = binary.parent / "config.json"
    document = json.loads(path.read_text())
    plugins = [
        plugin
        for plugin in document.get("plugins", [])
        if plugin.get("name") == "epoch_stratifyx::server::MiPageRuntimeService"
    ]
    if not plugins and enabled:
        raise ValueError(
            "This Backend no longer hosts Markets page refresh. "
            "Keep market_pages=false; page production is owned by Epoch Platform."
        )
    if len(plugins) > 1:
        raise ValueError(
            "The primary Backend runtime must have exactly one MiPageRuntimeService."
        )
    # ENG-2946 retired this plugin. Disabled refresh needs no replacement plugin
    # or runtime edit; older Backend versions still receive their explicit pause.
    if plugins:
        options = plugins[0].setdefault("config", {})
        if not isinstance(options, dict):
            raise ValueError("The Backend MI configuration must be an object.")
        calendar = options.setdefault("calendar", {})
        if not isinstance(calendar, dict):
            raise ValueError("The Backend Calendar configuration must be an object.")
        calendar.update(enabled=enabled, refresh_mode="automatic" if enabled else "paused")
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    flag = "1" if enabled else "0"
    batch = (
        # Every deployed environment uses 128 (epoch-terraform #146). The
        # Backend default of 8 splits each Equities universe into ~800 batches.
        {"EPOCH_EQUITIES_SOURCE_BATCH_MEMBERS": "128"}
        if enabled
        else {}
    )
    return {
        **batch,
        # Equities runs through the Rates producer; FX, Commodities and Crypto
        # are separate programmes that are off unless enabled here.
        "EPOCH_MI_RATES_PAGE": flag,
        "EPOCH_MI_RATES_DAILY": flag,
        "EPOCH_MI_FX_PAGE": flag,
        "EPOCH_MI_COMMODITIES_PAGE": flag,
        "EPOCH_MI_CRYPTO_PAGE": flag,
        "EPOCH_MI_LOCAL_ATOMIC_INSIGHTS": "1",
        "EPOCH_MI_PAGE_AUTO_REFRESH_ENABLED": flag,
    }


def verify_primary_checkouts() -> dict[str, dict[str, str]]:
    """Read-only preflight; never switch branches or rewrite someone else's work."""
    records = {}
    for project, expected in PROJECT_BRANCHES.items():
        directory = PROJECT_ROOT / project
        if not (directory / ".git").is_dir():
            raise ValueError(
                f"{directory} must be a primary Git checkout, not a linked worktree."
            )

        def git(*args: str) -> str:
            try:
                return subprocess.check_output(
                    ["git", "-C", str(directory), *args],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                ).strip()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise ValueError(
                    f"Could not verify {project} against origin/{expected}; startup stopped."
                ) from None

        branch = git("branch", "--show-current")
        if branch != expected:
            raise ValueError(
                f"{project} is on {branch or 'a detached HEAD'}; check out {expected} first."
            )
        if git("status", "--porcelain", "--untracked-files=no"):
            raise ValueError(
                f"{project} has uncommitted tracked changes; resolve them before starting the default stack."
            )
        commit = git("rev-parse", "HEAD")
        remote = git(
            "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{expected}"
        ).split()[0]
        if commit != remote:
            try:
                ancestor = git("merge-base", commit, remote)
            except ValueError:
                ancestor = None
            if ancestor != remote:
                raise ValueError(
                    f"{project} is not synchronized with origin/{expected}. "
                    "Fetch and reconcile that checkout first; remote ancestry must be verified."
                )
        records[project] = {"path": str(directory), "branch": branch,
                            "commit": commit, "origin_commit": remote}
    return records


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path.resolve()


def write_private(path: Path, text: str) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(text)


# `langgraph up` builds from EpochAI's own directory, so its generated config,
# env file and compose override live there, excluded from `git status` locally.
UP_CONFIG, UP_ENV, UP_COMPOSE = (
    "langgraph-up.local.json",
    ".env.up.local",
    "docker-compose.up.local.yml",
)
CONTAINER_PORT = 8000  # langgraph-api's port inside its container
WORKER_TOKEN = "EPOCH_DOCUMENT_WORKER_TOKEN"


def source_access(
    state: Path, api_url: str, backend_values: dict[str, str], ai_values: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Enable public web research and document URL capture for a local stack.

    Platform's Files and the agent's Files workers share one private worker token,
    kept across restarts. The agent reaches Files, and the workers their routes,
    through the local API origin's front door. Web research also needs Exa and a
    recovery journal; the agent is the single executor, so a private directory
    serves. AWS document reading (PDF, images, DOCX) stays off unless its own
    settings are configured."""
    token = backend_values.get(WORKER_TOKEN) or ai_values.get(WORKER_TOKEN)
    if not token:
        path = state / "document-worker-token"
        if not path.is_file():
            write_private(path, secrets.token_urlsafe(48))
        token = path.read_text().strip()
    ai = {WORKER_TOKEN: token}
    if ai_values.get("EXA_API_KEY") and "EPOCH_WEB_RESEARCH_ENABLED" not in ai_values:
        ai["EPOCH_WEB_RESEARCH_ENABLED"] = "true"
        ai["EPOCH_WEB_RESEARCH_JOURNAL"] = str(private_dir(state / "ai/web-journal"))
    # Operator settings win, except the token Platform must share and where Files is:
    # an agent .env naming another environment's Files must not split a local run.
    files = {"EPOCH_DOCUMENT_BACKEND_URL": api_url, "EPOCH_FILES_WORKER_URL": api_url}
    return {WORKER_TOKEN: token}, {**ai, **ai_values, WORKER_TOKEN: token, **files}


DOCUMENT_SETTINGS = ("aws_profile", "account_id", "bucket")
CONTAINER_AWS = "/root/.aws"  # The agent image runs as root.


def document_access(settings: dict) -> dict[str, str]:
    """AWS document reading (PDF, images, DOCX) through Bedrock Data Automation.

    Off unless local-stack.toml has a [documents] table naming the AWS profile,
    its account and the private us-east-1 bucket that stages documents."""
    if not settings:
        return {}
    if set(settings) != set(DOCUMENT_SETTINGS):
        raise ValueError(f"[documents] needs exactly: {', '.join(DOCUMENT_SETTINGS)}")
    account = str(settings["account_id"])
    return {
        "EPOCH_DOCUMENT_PREPARATION_ENABLED": "true",
        "EPOCH_DOCUMENT_AWS_BUCKET": settings["bucket"],
        "EPOCH_DOCUMENT_AWS_ACCOUNT_ID": account,
        "EPOCH_DOCUMENT_BDA_PROFILE_ARN": (
            f"arn:aws:bedrock:us-east-1:{account}:data-automation-profile/us.data-automation-v1"
        ),
        "AWS_PROFILE": settings["aws_profile"],
    }


def check_document_access(env: dict[str, str], base: dict[str, str]) -> None:
    """Fail before launch, as the agent refuses to start without its AWS access."""
    if env.get("EPOCH_DOCUMENT_PREPARATION_ENABLED") != "true":
        return
    profile, bucket = env["AWS_PROFILE"], env["EPOCH_DOCUMENT_AWS_BUCKET"]
    aws = shutil.which("aws", path=base.get("PATH"))
    if not aws:
        raise ValueError("Document reading needs the AWS CLI to check its access.")

    def call(*args: str) -> str:
        done = subprocess.run(
            [aws, *args, "--profile", profile, "--output", "text"],
            env=base, capture_output=True, text=True, timeout=60,
        )
        if done.returncode:
            raise ValueError(
                f"Document reading cannot use AWS profile {profile}. "
                f"Sign in with: aws sso login --profile {profile}"
            )
        return done.stdout.strip()

    if call("sts", "get-caller-identity", "--query", "Account") != env["EPOCH_DOCUMENT_AWS_ACCOUNT_ID"]:
        raise ValueError(f"AWS profile {profile} is not the configured document account.")
    region = call(
        "s3api", "get-bucket-location", "--bucket", bucket,
        "--expected-bucket-owner", env["EPOCH_DOCUMENT_AWS_ACCOUNT_ID"],
        "--query", "LocationConstraint",
    )
    if region not in ("None", "us-east-1"):
        raise ValueError(f"Document bucket {bucket} is in {region}; it must be in us-east-1.")


def aws_mounts(env: dict[str, str], base: dict[str, str]) -> list[tuple[str, str]]:
    """The profile config and SSO sign-in cache, read-only, for document reading."""
    if env.get("EPOCH_DOCUMENT_PREPARATION_ENABLED") != "true":
        return []
    home = Path(base.get("HOME", Path.home())) / ".aws"
    config = Path(base.get("AWS_CONFIG_FILE", home / "config"))
    return [
        (str(config), f"{CONTAINER_AWS}/config"),
        (str(home / "sso/cache"), f"{CONTAINER_AWS}/sso/cache"),
    ]


def up_container_env(values: dict[str, str]) -> tuple[dict[str, str], list[str], list[str]]:
    """Point the agent's local env at the host from its container.

    Returns the env, the host directories it names to mount read-only, and the
    web recovery journal to mount writable."""
    env = dict(values)
    # The document and web workers stay on their loopback origin; a relay serves it.
    for key in ("EPOCH_BACKEND_URL", "EPOCH_CAMPAIGN_SERVER"):
        parts = urlsplit(env[key])
        env[key] = parts._replace(netloc=f"{CONTAINER_HOST}:{parts.port}").geturl()
    for key in ("EPOCH_AI_URL", "LANGGRAPH_API_URL"):
        env[key] = f"http://localhost:{CONTAINER_PORT}"
    env.pop("POSTGRES_URI", None)  # `up` brings its own Postgres.
    journal = env.get("EPOCH_WEB_RESEARCH_JOURNAL", "")
    writable = [journal] if journal.startswith("/") and Path(journal).is_dir() else []
    mounts = sorted(
        {
            value
            for value in env.values()
            if value.startswith(f"{PROJECT_ROOT}/") and Path(value).is_dir()
        }
        - set(writable)
    )
    return env, mounts, writable


def env_file_text(env: dict[str, str]) -> str:
    # Single quotes keep Compose from interpolating or unescaping secret values.
    for key, value in env.items():
        if "'" in value or "\n" in value:
            raise ValueError(f"{key} cannot be written to the container env file.")
    return "".join(f"{key}='{value}'\n" for key, value in sorted(env.items()))


RELAY = Path(__file__).resolve().parent / "loopback_forward.py"


def loopback_port(env: dict[str, str]) -> int | None:
    """The Backend port the workers expect on the container's own loopback."""
    parts = urlsplit(env.get("EPOCH_DOCUMENT_BACKEND_URL", ""))
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"):
        return parts.port
    return None


def up_compose_override(
    mounts: list[str],
    writable: list[str] = (),
    image: str = "",
    relay_port: int | None = None,
    mapped: list[tuple[str, str]] = (),
) -> str:
    lines = [
        "services:",
        "  langgraph-api:",
        "    extra_hosts:",
        f'      - "{CONTAINER_HOST}:host-gateway"',
    ]
    if mounts or writable or mapped:
        lines.append("    volumes:")
        lines += [f'      - "{mount}:{mount}:ro"' for mount in mounts]
        lines += [f'      - "{mount}:{mount}"' for mount in writable]
        lines += [f'      - "{host}:{container}:ro"' for host, container in mapped]
    if relay_port:
        # Shares the agent's network, so its loopback reaches the host Backend.
        lines += [
            "  backend-loopback:",
            f'    image: "{image}"',
            '    network_mode: "service:langgraph-api"',
            f'    entrypoint: ["python", "{RELAY}", "{relay_port}", "{CONTAINER_HOST}"]',
            "    volumes:",
            f'      - "{RELAY}:{RELAY}:ro"',
            "    restart: unless-stopped",
            "    healthcheck:",
            "      test: " + json.dumps(["CMD", "python", "-c", relay_health_probe(relay_port)]),
            "      interval: 30s",
            "      timeout: 5s",
            "      retries: 3",
            "      start_period: 5s",
        ]
    return "\n".join(lines) + "\n"


def relay_health_probe(port: int) -> str:
    """Probe the forwarded Backend, rather than the inherited API/Redis check."""
    return (
        "import http.client; "
        f"c=http.client.HTTPConnection('127.0.0.1',{port},timeout=3); "
        "c.request('GET','/health'); r=c.getresponse(); "
        "raise SystemExit(0 if r.status==200 else 1)"
    )


def exclude_locally(repo: Path, names: tuple[str, ...]) -> None:
    """Keep generated files out of `git status` without editing tracked ignores."""
    path = Path(
        subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
            text=True,
        ).strip()
    )
    path = path if path.is_absolute() else repo / path
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [name for name in names if name not in existing]
    if missing:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.write("".join(f"{name}\n" for name in missing))


def up_project(ai: Path) -> str:
    return ai.name.lower()  # Compose's project name for EpochAI's directory.


def prepare_up(
    ai: Path,
    langgraph: Path,
    values: dict[str, str],
    stack: Stack,
    port: int,
    commit: str,
    state: Path,
    base: dict[str, str],
    *,
    retained_image_id: str | None = None,
) -> tuple:
    if not shutil.which("docker", path=base.get("PATH")):
        raise ValueError("The up runtime needs Docker on PATH.")
    image = retained_image_id or f"epoch-ai-up:{commit[:12]}"
    if retained_image_id is not None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", retained_image_id):
            raise ValueError("Retained images require their immutable full ID.")
        found = subprocess.run(["docker", "image", "inspect", retained_image_id, "--format", "{{.Id}}"],
                               env=base, capture_output=True, text=True)
        if found.returncode or found.stdout.strip() != retained_image_id:
            raise ValueError("The exact retained image is missing; no build or pull is allowed.")
    env, mounts, writable = up_container_env(values)
    graph_config = json.loads((ai / "langgraph.json").read_text())
    graph_config["env"] = UP_ENV
    # One image per EpochAI commit; the default builder avoids a stale buildx one.
    write_private(ai / UP_ENV, env_file_text(env))
    write_private(ai / UP_CONFIG, json.dumps(graph_config, indent=2))
    write_private(
        ai / UP_COMPOSE,
        up_compose_override(mounts, writable, image, loopback_port(env), aws_mounts(env, base)),
    )
    exclude_locally(ai, (UP_CONFIG, UP_ENV, UP_COMPOSE))
    up_env = {**base, "BUILDX_BUILDER": "default"}
    if retained_image_id is None and subprocess.run(
        ["docker", "image", "inspect", image], env=up_env, capture_output=True
    ).returncode:
        log = state / "ai-build.log"
        print(f"Building agent image {image}; log: {log}", flush=True)
        with os.fdopen(
            os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as stream:
            built = subprocess.run(
                [str(langgraph), "build", "-c", UP_CONFIG, "-t", image],
                cwd=ai,
                env=up_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        if built.returncode:
            raise ValueError(f"The agent image build failed. Inspect {log}.")
    command = [
        str(langgraph),
        "up",
        "-c",
        UP_CONFIG,
        "-p",
        str(port),
        "-d",
        UP_COMPOSE,
        "--image",
        image,
        "--recreate",
        "--no-pull",
    ]
    return ("AI", command, ai, up_env, stack.ai_url + "/ok")


# Vite reads these files in production mode too; a change to them changes the bundle.
UI_ENV_FILES = (".env", ".env.local", ".env.production", ".env.production.local")


def ui_build_configuration(ui: Path, ui_env: dict[str, str], stack: Stack) -> tuple[dict[str, str], str]:
    origin = stack.ui_url.rstrip("/")
    build_env = {**ui_env, "VITE_LANGGRAPH_API_URL": f"{origin}/lg-api"}
    configuration = hashlib.sha256()
    for key in sorted(k for k in build_env if k.startswith("VITE_")):
        configuration.update(f"{key}={build_env[key]}\n".encode())
    for name in UI_ENV_FILES:
        if (ui / name).is_file():
            configuration.update(name.encode() + b"\0" + (ui / name).read_bytes())
    return build_env, configuration.hexdigest()


def prepare_ui_build(
    ui: Path,
    corepack: str,
    ui_env: dict[str, str],
    stack: Stack,
    port: int,
    commit: str,
    state: Path,
    *,
    source_guard: Callable[[], None] | None = None,
) -> tuple:
    """The Portal's production bundle, served by `vite preview`, as a deployment serves it.

    One bundle per EpochClientPortal commit and configuration, kept between restarts, as the
    agent keeps one image per EpochAI commit. The agent's CORS list does not include this
    origin, so the bundle talks to it through the same /lg-api proxy the dev server uses;
    `vite preview` applies vite.config.ts's proxy, targeting the real agent URL.
    """
    build_env, configuration = ui_build_configuration(ui, ui_env, stack)
    if source_guard is not None:
        source_guard()
    builds = private_dir(state / "ui-builds")
    bundle = builds / f"{commit[:12]}-{configuration[:12]}"
    if not (bundle / "index.html").is_file():
        partial = bundle.with_name(bundle.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        log = state / "ui-build.log"
        print(f"Building UI bundle {bundle.name}; log: {log}", flush=True)
        with os.fdopen(
            os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as stream:
            try:
                built = subprocess.run(
                    [corepack, "pnpm", "exec", "vite", "build", "--outDir", str(partial),
                     "--emptyOutDir"],
                    cwd=ui, env=build_env, stdout=stream, stderr=subprocess.STDOUT, timeout=180,
                )
            except subprocess.TimeoutExpired as error:
                raise ValueError(f"The UI build timed out. Inspect {log}.") from error
        if built.returncode or not (partial / "index.html").is_file():
            raise ValueError(f"The UI build failed. Inspect {log}.")
        if source_guard is not None:
            source_guard()
        partial.rename(bundle)
        # A running preview or a failed replacement may still need an older
        # immutable bundle. Building never deletes another generation.
    command = [
        corepack, "pnpm", "exec", "vite", "preview", "--outDir", str(bundle),
        "--host", "127.0.0.1", "--port", str(port), "--strictPort",
    ]
    if source_guard is not None:
        source_guard()
    return ("UI", command, ui, ui_env, stack.ui_url)


def start_preview(preview: Preview, log: Path) -> subprocess.Popen:
    with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        return subprocess.Popen(preview.command, cwd=preview.cwd, env=preview.env,
                                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)


def ui_preview_inputs(ui: Path) -> str:
    """Pin the actual preview config/dependencies; rollback must load the same ones.

    Epoch's current Vite config has package imports only. A changed config,
    dependency manifest, tsconfig or dotenv file requires a reviewed bootstrap,
    rather than claiming an old bundle alone restores its proxy behavior.
    """
    names = {*UI_ENV_FILES, "package.json", "pnpm-lock.yaml"}
    names.update(path.name for pattern in ("vite.config.*", "tsconfig*.json") for path in ui.glob(pattern))
    names.update("node_modules/" + package + "/package.json" for package in (
        "vite", "@rolldown/plugin-babel", "@sentry/vite-plugin", "@tailwindcss/vite", "@vitejs/plugin-react"))
    digest = hashlib.sha256()
    for name in sorted(names):
        path = ui / name
        digest.update(name.encode() + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<absent>")
        digest.update(b"\0")
    return digest.hexdigest()


def require_primary_ui_source(ui: Path, commit: str) -> None:
    actual = verify_primary_checkouts()["EpochClientPortal"]
    if actual["path"] != str(ui) or actual["commit"] != commit:
        raise ValueError("The primary Portal is not at the exact qualified target commit.")


def ui_refresh_controller(state: Path, stack: Stack, service: tuple, child, children: list) -> Controller:
    _name, command, ui, ui_env, url = service
    source = json.loads((state / "source-checkouts.json").read_text())["EpochClientPortal"]
    _env, configuration = ui_build_configuration(ui, ui_env, stack)
    initial = Preview(command, ui, ui_env, url, source["commit"], configuration)
    preview_inputs = ui_preview_inputs(ui)

    def guard() -> None:
        if ui_preview_inputs(ui) != preview_inputs:
            raise ValueError("Portal preview configuration/dependencies changed; keep the old UI and use a reviewed bootstrap.")

    def target_guard(commit: str) -> None:
        require_primary_ui_source(ui, commit)
        guard()

    def stage(commit: str, expected_configuration: str) -> Preview:
        # Read-only preflight; refresh cannot switch repositories or select a
        # worktree, and it cannot change another service's process/configuration.
        def source_guard() -> None:
            target_guard(commit)

        source_guard()
        _build_env, current_configuration = ui_build_configuration(ui, ui_env, stack)
        if current_configuration != expected_configuration:
            raise ValueError("The primary Portal configuration differs from the requested SHA256.")
        prepared = prepare_ui_build(ui, command[0], ui_env, stack, urlsplit(url).port or 3000,
                                    commit, state, source_guard=source_guard)
        source_guard()
        guard()
        return Preview(prepared[1], ui, ui_env, url, commit, current_configuration)

    return Controller(state, initial, child, children, stage, start_preview, stop,
                      guard=guard, target_guard=target_guard)


def prepare_reference_files(binary: Path, commit: str, state: Path, base: dict[str, str]) -> Path:
    """Export the actual local engine's reference generation; never borrow hosted metadata."""
    exporter = binary.with_name("epoch_reference_files")
    if not os.access(exporter, os.X_OK):
        raise ValueError(
            f"Missing {exporter}. Build the epoch_reference_files target in the primary Backend first."
        )
    generation = Path(tempfile.mkdtemp(prefix="reference-files-", dir=state))
    log = state / "reference-export.log"
    with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        result = subprocess.run(
            [str(exporter), "--output", str(generation), "--release", "local", "--git-sha", commit],
            cwd=binary.parent, env=base, stdout=stream, stderr=subprocess.STDOUT,
        )
    if result.returncode:
        raise ValueError(f"The local engine reference export failed. Inspect {log}.")
    manifest = json.loads((generation / "manifest.json").read_text())
    reference = json.loads((generation / "agents/reference-library.json").read_text())
    if (manifest.get("format") != "epoch-reference-files-v1"
            or manifest.get("engine", {}).get("git_sha") != commit
            or not manifest.get("version") or reference.get("version") != manifest["version"]):
        raise ValueError("The local engine exported an inconsistent reference generation.")
    return generation


def prepare_platform(
    platform: Path,
    node: str,
    base: dict[str, str],
    backend_values: dict[str, str],
    files_access: dict[str, str],
    project: Path,
    commit: str,
    state: Path,
    reference_root: Path,
) -> tuple:
    """Epoch Platform's general service, with Files and Space on local disk and no AWS account.

    Files keeps its objects in the engine's personal content directory, where the local job store
    reads saved datasets, and names studies from the engine's local definitions. The checkout's
    TypeScript is compiled in place first (incremental; Git ignores the output)."""
    log = state / "platform-build.log"
    with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        built = subprocess.run(
            [node, "node_modules/typescript/bin/tsc", "-b"],
            cwd=platform, env=base, stdout=stream, stderr=subprocess.STDOUT,
        )
    if built.returncode or not (platform / "apps/platform/dist/main.js").is_file():
        raise ValueError(f"The Platform build failed. Inspect {log}.")
    # No AWS credentials reach it: local Files must never fall back to a cloud store.
    env = {key: value for key, value in base.items() if not key.startswith("AWS_")}
    env.update({
        "AWS_CONFIG_FILE": os.devnull,
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": REGION,
        "EPOCH_PLATFORM_ROLE": "general",
        "EPOCH_ENVIRONMENT": "local",
        "EPOCH_PLATFORM_REVISION": commit,
        "PORT": str(PLATFORM_PORT),
        "COGNITO_USER_POOL_ID": POOL,
        "COGNITO_CLIENT_IDS": f"{CLIENT},{CLI_CLIENT}",
        "EPOCH_FILES_LOCAL_ROOT": str(project / "personal_content"),
        "EPOCH_DEFINITIONS_LOCAL_ROOT": str(project / "definitions"),
        "EPOCH_MODEL_EXPORT_SERVER_URL": f"http://127.0.0.1:{BACKEND_PORT}",
        "EPOCH_REFERENCE_LOCAL_ROOT": str(reference_root),
        **files_access,
    })
    # GDS is a shared catalogue service in deployed stacks too. Read only its
    # explicit connection settings; local Files and observation reads stay local.
    settings = platform / ".env.local"
    connection = read_env(settings) if settings.is_file() else {}
    discovery_url = connection.get("EPOCH_DISCOVERY_GDS_URL", "").strip()
    discovery_token = connection.get("EPOCH_DISCOVERY_SERVICE_TOKEN", "").strip()
    if bool(discovery_url) != bool(discovery_token):
        raise ValueError("Platform .env.local needs both discovery GDS URL and service token.")
    if discovery_url:
        env.update({
            "EPOCH_DISCOVERY_GDS_URL": discovery_url,
            "EPOCH_DISCOVERY_SERVICE_TOKEN": discovery_token,
            "EPOCH_DISCOVERY_NATIVE_URL": f"http://127.0.0.1:{BACKEND_PORT}",
        })
    # Identity has its own GDS credential; discovery configuration is not an
    # identity grant, and neither credential reaches Backend, AI or the browser.
    identity_url = connection.get("EPOCH_IDENTITY_GDS_URL", "").strip()
    identity_token = connection.get("EPOCH_IDENTITY_GDS_TOKEN", "").strip()
    if bool(identity_url) != bool(identity_token):
        raise ValueError("Platform .env.local needs both identity GDS URL and service token.")
    if identity_url:
        env.update({
            "EPOCH_IDENTITY_GDS_URL": identity_url,
            "EPOCH_IDENTITY_GDS_TOKEN": identity_token,
        })
    issuer = backend_values.get("EPOCH_TOKEN_ISSUER", "").strip()
    if issuer:
        # Delegated tokens from the local v1 host, which the local Backend signs.
        env["EPOCH_TOKEN_ISSUER"] = issuer
        env["EPOCH_TOKEN_JWKS_URL"] = f"http://127.0.0.1:{BACKEND_PORT}/api/v1/auth/jwks"
    return (
        "Platform",
        [node, "apps/platform/dist/main.js"],
        platform,
        env,
        f"http://127.0.0.1:{PLATFORM_PORT}/api/v1/platform/health",
    )


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"Missing service environment file: {path}")
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def local_ports(stack: Stack) -> list[int]:
    ports = []
    for url in (stack.backend_url, stack.ai_url, stack.ui_url):
        parts = urlsplit(url)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"localhost", "127.0.0.1"}
            or parts.path not in {"", "/"}
            or parts.port is None
        ):
            raise ValueError(
                "The launcher requires explicit localhost HTTP ports in the local profile."
            )
        ports.append(parts.port)
    if len(set(ports)) != 3:
        raise ValueError("Backend, AI and UI need three distinct ports.")
    return ports


def service_ports(stack: Stack) -> list[int]:
    """The profile's ports and the two behind the front door."""
    ports = local_ports(stack) + [BACKEND_PORT, PLATFORM_PORT]
    if len(set(ports)) != len(ports):
        raise ValueError(
            f"Ports {BACKEND_PORT} and {PLATFORM_PORT} are reserved for Backend and Platform behind the front door."
        )
    return ports


def check_ports(ports: list[int]) -> None:
    for port in ports:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise ValueError(
                    f"Local port {port} is occupied. Stop its owner or select other ports."
                ) from None


def prepare(
    config_file: Path,
    stack: Stack,
    state: Path,
    ai_runtime: str = "dev",
    ui_runtime: str = "dev",
    retained_cut: RetainedCut | None = None,
) -> list[tuple]:
    config, paths = primary_paths(config_file)
    revisions = verify_primary_checkouts()
    if retained_cut is not None and (ai_runtime != "up" or ui_runtime != "build"):
        raise ValueError("Retained rollback requires the local-up image and production UI bundle modes.")
    for name, revision in revisions.items():
        print(
            f"{name}: {revision['branch']} @ {revision['commit'][:10]} ({revision['path']})",
            flush=True,
        )
    binary, ai, ui = paths["backend_binary"], paths["ai_dir"], paths["ui_dir"]
    verify_local_agent_isolation(
        ai,
        (stack.backend_url, f"http://127.0.0.1:{BACKEND_PORT}", f"http://127.0.0.1:{PLATFORM_PORT}"),
    )
    runtime_ai_source = retained_cut.runtime_sources["EpochAI"] if retained_cut else revisions["EpochAI"]["commit"]
    platform = paths["platform_dir"]
    langgraph = ai / ".venv/bin/langgraph"
    for executable in (binary, langgraph):
        if not os.access(executable, os.X_OK):
            raise ValueError(
                f"Missing executable: {executable}. The launcher does not build or install products."
            )
    if not (ui / "node_modules").is_dir():
        raise ValueError(f"Install UI dependencies with pnpm in {ui} first.")
    if not (platform / "node_modules/typescript").is_dir():
        raise ValueError(f"Install Platform dependencies with pnpm in {platform} first.")
    ports = service_ports(stack)
    # Do not pass the caller's token or password to any server.
    base = {
        k: v
        for k, v in os.environ.items()
        if k
        in {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "LD_LIBRARY_PATH",
            "AWS_PROFILE",
            "AWS_SHARED_CREDENTIALS_FILE",
            "AWS_CONFIG_FILE",
        }
    }
    if "node_bin" in config:
        node_bin = (
            config_file.resolve().parent / Path(config["node_bin"]).expanduser()
        ).resolve()
        base["PATH"] = str(node_bin) + os.pathsep + base.get("PATH", "")
    corepack = shutil.which("corepack", path=base.get("PATH"))
    node = shutil.which("node", path=base.get("PATH"))
    if not corepack or not node:
        raise ValueError("Node 24+ and Corepack must be on PATH (or set node_bin).")
    base["COREPACK_ENABLE_NETWORK"] = "0"
    ui_env = portal_environment(base, stack)
    if retained_cut is not None:
        retained_cut.validate(root=PROJECT_ROOT, state=state, paths=paths, workspace=revisions,
                              identity=stack.identity(), environment=stack.environment,
                              ui_configuration=ui_build_configuration(paths["ui_dir"], ui_env, stack)[1],
                              preview_inputs=ui_preview_inputs(paths["ui_dir"]))
    if (
        int(
            subprocess.check_output([node, "--version"], env=base, text=True)
            .lstrip("v")
            .split(".")[0]
        )
        < 24
    ):
        raise ValueError(
            "EpochClientPortal needs Node 24+. Set node_bin in local-stack.toml."
        )

    common = {
        "EPOCH_ENVIRONMENT": "local",
        "EPOCH_LOCAL": "1",
        "EPOCH_JOB_STORE": "local",
        "EPOCH_SKIP_AUTH": "0",
        "AWS_EC2_METADATA_DISABLED": "true",
        "EPOCH_CAMPAIGN_SERVER": stack.backend_url,
        "EPOCH_BACKEND_URL": stack.backend_url,
        "EPOCH_PLATFORM_SERVER": stack.backend_url,
        "EPOCH_AI_URL": stack.ai_url,
        "LANGGRAPH_API_URL": stack.ai_url,
        "COGNITO_REGION": REGION,
        "COGNITO_USER_POOL_ID": POOL,
        "COGNITO_CLIENT_ID": CLIENT,
        # The Portal's client, and `epoch login`'s, whose ID tokens /v1/auth/token exchanges.
        "COGNITO_CLIENT_IDS": f"{CLIENT},{CLI_CLIENT}",
    }
    backend_values = read_env(paths["backend_env"])
    backend_env = {
        **base,
        **backend_values,
        **common,
        "EPOCH_GIT_SHA": revisions["EpochBackend"]["commit"],
        "PORT": str(BACKEND_PORT),
        "EPOCH_SKIP_LOAD_JOBS": "0",
        "EPOCH_PROJECT_DIR": str(private_dir(state / "backend")),
        "EPOCH_JOB_STAGING_DIR": str(private_dir(state / "backend/staging")),
        "EPOCH_ENDPOINT_CACHE_DIR": str(private_dir(state / "backend/cache")),
        "EPOCH_COMPILER_METADATA_DIR": str(binary.parent / "compiler_metadata"),
        **configure_market_pages(
            binary,
            config.get("market_pages", DEFAULT_MARKET_PAGES),
            catalog_root=backend_values.get("EPOCH_CATALOG_ROOT"),
        ),
    }

    # LangGraph loads its config env after process env. Supply local overrides
    # inside a private generated config, leaving the product's files untouched.
    graph_config = json.loads((ai / "langgraph.json").read_text())
    if graph_config.get("auth", {}).get("path"):
        # Studio must authenticate as the actual Cognito caller, not a workspace
        # StudioUser that cannot pass Epoch's per-owner thread authorization.
        graph_config["auth"]["disable_studio_auth"] = True
    env_source = graph_config.get("env", {})
    ai_values = read_env(ai / env_source) if isinstance(env_source, str) else env_source
    # Local-only credentials and provider choices override the graph's base env.
    # Keep them server-side; UI and Backend environments are built separately.
    if (ai / ".env.local").is_file():
        ai_values = {**ai_values, **read_env(ai / ".env.local")}
    # A local launch is a versioned deployment too. Keep operator overrides;
    # otherwise identify it by the clean, verified AI source actually launched.
    revision = ai_values.get("EPOCH_COMMENTARY_DEPLOYMENT_REVISION", "").strip()
    if not revision:
        revision = "local-" + runtime_ai_source
    ai_values = {
        **ai_values,
        "EPOCH_COMMENTARY_DEPLOYMENT_REVISION": revision,
        # Saved cloud metadata in a local .env does not identify this process.
        "LANGSMITH_LANGGRAPH_GIT_REF": revisions["EpochAI"]["branch"],
        "LANGSMITH_LANGGRAPH_GIT_REF_SHA": runtime_ai_source,
    }
    if retained_cut is None:
        revisions["EpochAI"]["commentary_deployment_revision"] = revision
    files_access, ai_values = source_access(state, stack.backend_url, backend_values, ai_values)
    ai_values = {**ai_values, **document_access(config.get("documents", {}))}
    check_document_access(ai_values, base)
    print(
        "Web research: "
        + ("on" if ai_values.get("EPOCH_WEB_RESEARCH_ENABLED", "").lower() in ("1", "true") else "off")
        + "; document URL capture: on; AWS document reading: "
        + ("on" if ai_values.get("EPOCH_DOCUMENT_PREPARATION_ENABLED") == "true" else "off"),
        flush=True,
    )
    reference_root = prepare_reference_files(binary, revisions["EpochBackend"]["commit"], state, base)
    if retained_cut is not None:
        reference = json.loads((reference_root / "manifest.json").read_text())
        if reference.get("version") != retained_cut.native_reference_version:
            raise ValueError("The primary reference export differs from the retained cut.")
    platform_service = prepare_platform(
        platform, node, base, backend_values, files_access,
        Path(backend_env["EPOCH_PROJECT_DIR"]), revisions["EpochPlatform"]["commit"], state, reference_root,
    )
    print(
        "Files and Space: Epoch Platform, on local disk in "
        f"{platform_service[3]['EPOCH_FILES_LOCAL_ROOT']}",
        flush=True,
    )
    (state / "source-checkouts.json").write_text(json.dumps(revisions, indent=2))
    print(f"Local commentary deployment: {revision}", flush=True)
    # The agent publishes `epoch login`'s client in its protected resource metadata.
    sign_in = {"EPOCH_SIGN_IN_CLIENT_ID": CLI_CLIENT}
    ai_env = {**base, **ai_values, **common, **sign_in}
    graph_config["env"] = {**ai_values, **common, **sign_in}
    graph_config["dependencies"] = [
        str(ai / dep) if dep.startswith(".") else dep
        for dep in graph_config.get("dependencies", [])
    ]

    def import_path(value: str) -> str:
        module, sep, name = value.partition(":")
        return str(ai / module) + sep + name if module.endswith(".py") else value

    for name, graph in graph_config["graphs"].items():
        if isinstance(graph, str):
            graph_config["graphs"][name] = import_path(graph)
        else:
            graph["path"] = import_path(graph["path"])
    for section, key in (("auth", "path"), ("http", "app")):
        if graph_config.get(section, {}).get(key):
            graph_config[section][key] = import_path(graph_config[section][key])
    index = graph_config.get("store", {}).get("index", {})
    if isinstance(index.get("embed"), str):
        index["embed"] = import_path(index["embed"])
    ai_state = private_dir(state / "ai")
    config_path = ai_state / "langgraph.json"
    with os.fdopen(
        os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as f:
        json.dump(graph_config, f)
    ai_env["PYTHONPATH"] = str(ai)
    ai_service = (
        prepare_up(
            ai,
            langgraph,
            {**ai_values, **common},
            stack,
            ports[1],
            runtime_ai_source,
            state,
            base,
            **({"retained_image_id": retained_cut.ai_image_id} if retained_cut else {}),
        )
        if ai_runtime == "up"
        else (
            "AI",
            [
                str(langgraph),
                "dev",
                "--config",
                str(config_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(ports[1]),
                "--no-reload",
                "--no-browser",
            ],
            ai_state,
            ai_env,
            stack.ai_url + "/ok",
        )
    )
    return [
        (
            "Backend",
            [str(binary), "--port", str(BACKEND_PORT)],
            state / "backend",
            backend_env,
            f"http://127.0.0.1:{BACKEND_PORT}/api/v1/health",
        ),
        platform_service,
        (
            "FrontDoor",
            [sys.executable, str(FRONT_DOOR), str(ports[0]), str(BACKEND_PORT), str(PLATFORM_PORT)],
            state,
            base,
            stack.backend_url + "/api/v1/platform/health",
        ),
        ai_service,
        ("UI", [corepack, "pnpm", "exec", "vite", "preview", "--outDir", str(retained_cut.ui_bundle),
                "--host", "127.0.0.1", "--port", str(ports[2]), "--strictPort"],
         ui, ui_env, stack.ui_url)
        if retained_cut is not None else prepare_ui_build(
            ui, corepack, ui_env, stack, ports[2],
            revisions["EpochClientPortal"]["commit"], state,
            source_guard=lambda: require_primary_ui_source(ui, revisions["EpochClientPortal"]["commit"]),
        )
        if ui_runtime == "build"
        else (
            "UI",
            [
                corepack,
                "pnpm",
                "run",
                "dev",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports[2]),
                "--strictPort",
            ],
            ui,
            ui_env,
            stack.ui_url,
        ),
    ]


def portal_environment(base: dict[str, str], stack: Stack) -> dict[str, str]:
    return {**base, "VITE_API_BASE_URL": stack.backend_url,
            "VITE_LANGGRAPH_API_URL": stack.ai_url, "VITE_LANGGRAPH_ASSISTANT_ID": stack.assistant_id,
            "VITE_COGNITO_USER_POOL_ID": POOL, "VITE_COGNITO_USER_POOL_CLIENT_ID": CLIENT}


def wait_ready(url: str, children: list, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2, trust_env=False) as http:
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in children):
                raise ValueError(
                    "A service exited during startup. Inspect its local log."
                )
            try:
                if http.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)
    raise ValueError(f"Service readiness timed out at {url}. Inspect its local log.")


def group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop(children: list, grace: float = 10) -> None:
    for child in reversed(children):
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        for child in children:
            child.poll()  # Reap group leaders; their descendants may still run.
        if not any(group_alive(child.pid) for child in children):
            return
        time.sleep(0.1)
    for child in reversed(children):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def opaque_stdin_token() -> str:
    """Read one caller-supplied credential without claims or login-cache access."""
    value = sys.stdin.read(65537)
    if not isinstance(value, str) or len(value) > 65536:
        raise ValueError("Provide one bounded opaque ID token on stdin.")
    token = value.rstrip("\r\n")
    if not token or any(character.isspace() for character in token):
        raise ValueError("Provide one nonempty opaque ID token on stdin.")
    return token


def main() -> None:
    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local-stack.toml"))
    parser.add_argument("--environments", type=Path, default=Path("environments.toml"))
    parser.add_argument("--email")
    parser.add_argument(
        "--id-token-stdin", action="store_true",
        help="Use an opaque ID token from stdin for readiness only; do not read or replace a login cache.",
    )
    parser.add_argument("--refresh-ui", action="store_true", help="Ask a capable running launcher to refresh only Portal.")
    parser.add_argument("--ui-commit", help="Exact qualified Portal commit for --refresh-ui.")
    parser.add_argument("--ui-configuration", help="Exact Portal build configuration SHA256 for --refresh-ui.")
    parser.add_argument("--retained-cut", type=Path, help="Explicit reviewed rollback manifest; workspace checks stay strict.")
    parser.add_argument("--retained-cut-admission", type=Path)
    parser.add_argument("--retained-cut-admission-sha")
    parser.add_argument(
        "--login", action="store_true", help="Replace the cached local login"
    )
    parser.add_argument(
        "--ai-runtime",
        choices=("dev", "up"),
        default="dev",
        help="Agent server: `langgraph dev` on the local profile (2024), or the "
        "licensed `langgraph up` server with parallel runs on local-up (2124)",
    )
    parser.add_argument(
        "--ui-runtime",
        choices=("build", "dev"),
        default="build",
        help="Portal: its production bundle served by `vite preview` (built once per commit), "
        "or the `vite` dev server with hot reload",
    )
    args = parser.parse_args()
    opaque_token = ""
    if args.id_token_stdin:
        if args.login or args.email or args.refresh_ui:
            parser.error("--id-token-stdin cannot be combined with --login, --email or --refresh-ui.")
        try:
            opaque_token = opaque_stdin_token()
        except ValueError as error:
            parser.error(str(error))
    children = []
    portal = None
    up_started = False
    state = private_dir(Path(".artifacts/local-stack"))
    retained = None
    if args.retained_cut or args.retained_cut_admission or args.retained_cut_admission_sha:
        if (not all((args.retained_cut, args.retained_cut_admission, args.retained_cut_admission_sha))
                or args.refresh_ui or args.ai_runtime != "up" or args.ui_runtime != "build"):
            parser.error("Retained rollback needs manifest/admission/SHA, --ai-runtime up and --ui-runtime build; no --refresh-ui.")
        if (args.config.resolve() != PROJECT_ROOT / "AgentTesting/local-stack.toml"
                or args.environments.resolve() != PROJECT_ROOT / "AgentTesting/environments.toml"):
            parser.error("Retained rollback requires the pinned primary launcher configuration paths.")
        try:
            retained = RetainedCut.load(args.retained_cut, args.retained_cut_admission, args.retained_cut_admission_sha)
        except (ValueError, OSError, KeyError, TypeError) as error:
            parser.exit(1, f"{error}\n")
    if args.refresh_ui:
        if not args.ui_commit or not args.ui_configuration:
            parser.error("--refresh-ui requires --ui-commit and --ui-configuration.")
        try:
            result = request_refresh(state, args.ui_commit, args.ui_configuration)
            print(json.dumps(result, indent=2))
            if result["status"] not in ("ready", "unchanged"):
                parser.exit(1, "Portal refresh failed; the saved result states rollback status.\n")
        except (ValueError, OSError, KeyError) as error:
            parser.exit(1, f"{error}\n")
        return
    if args.ui_commit or args.ui_configuration:
        parser.error("UI target options require --refresh-ui.")
    with (state / "launcher.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "The local launcher is already running.\n")
        try:
            stack = Stack.load(
                args.environments, "local-up" if args.ai_runtime == "up" else "local"
            )
            check_ports(service_ports(stack))
            services = prepare(args.config, stack, state, args.ai_runtime, args.ui_runtime,
                               **({"retained_cut": retained} if retained else {}))
            bearer = opaque_token if args.id_token_stdin else (
                login(args.email)
                if args.login or not token_path().exists()
                else cached_token(email=args.email)
            )
            for name, command, cwd, env, url in services:
                log = state / f"{name.lower()}.log"
                with os.fdopen(
                    os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
                ) as stream:
                    child = subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=env,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                children.append(child)
                if name == "AI" and args.ai_runtime == "up":
                    up_started = True
                print(f"Starting {name}; log: {log}", flush=True)
                # The up server imports the graphs inside its container first.
                up_agent = name == "AI" and args.ai_runtime == "up"
                wait_ready(url, children, 420 if up_agent else 180)
            receipt = asyncio.run(check_stack(stack, "Bearer " + bearer))
            if retained is not None:
                actual_ai = receipt.get("ai")
                if (not isinstance(actual_ai, dict) or not isinstance(actual_ai.get("deployment"), dict)
                        or actual_ai["deployment"].get("git_sha") != retained.runtime_sources["EpochAI"]
                        or actual_ai.get("reference_version") != retained.native_reference_version):
                    raise ValueError("The started retained runtime does not match its selected source/reference.")
                ui_index = next(i for i, service in enumerate(services) if service[0] == "UI")
                ui_service, ui_child = services[ui_index], children[ui_index]
                checked = probe(Preview(ui_service[1], ui_service[2], ui_service[3], ui_service[4],
                                        retained.runtime_sources["EpochClientPortal"], retained.ui_configuration),
                                ui_child, [p for p in children if p is not ui_child])
                retained.validate_served_ui(checked)
                receipt["retained_runtime_cut"] = {**retained.metadata(), "actual_ui_validation": checked}
                write_json(state / CAPABILITY, {"version": 1, "launcher_pid": os.getpid(),
                           "launcher_start": process_start(os.getpid()), "status": "disabled",
                           "mode": "retained-runtime-rollback"})
            (state / "health.json").write_text(
                json.dumps(receipt, indent=2, default=str)
            )
            non_ui = list(children)
            if args.ui_runtime == "build" and retained is None:
                ui_index = next(i for i, service in enumerate(services) if service[0] == "UI")
                ui_child = children[ui_index]
                non_ui.remove(ui_child)
                portal = ui_refresh_controller(state, stack, services[ui_index], ui_child, children)
            auth_note = (
                "Readiness used the supplied opaque token; no login cache was accessed."
                if args.id_token_stdin
                else "Local CLI login is cached. Browser login uses the same account."
            )
            print(
                f"Ready: {stack.ui_url}\n{auth_note}\n"
                "Ctrl+C stops these services. No research has been started.",
                flush=True,
            )
            last_ui_error = None
            while all(child.poll() is None for child in non_ui):
                if portal is not None:
                    try:
                        portal.tick()
                        last_ui_error = None
                    except (ValueError, OSError, RuntimeError, httpx.HTTPError) as error:
                        # A UI/control-file failure is not permission to stop
                        # Backend, AI, Platform or the front door.
                        if str(error) != last_ui_error:
                            print(f"Portal refresh needs attention: {error}", file=sys.stderr, flush=True)
                        last_ui_error = str(error)
                time.sleep(1)
            raise ValueError("A local service exited. Inspect the logs above.")
        except KeyboardInterrupt:
            print("\nStopping local services.")
        except (ValueError, OSError, RuntimeError, httpx.HTTPError) as exc:
            parser.exit(1, f"{exc}\n")
        finally:
            if portal is not None:
                portal.close()
            elif retained is not None:
                clear_capability(state, os.getpid(), process_start(os.getpid()))
            stop(children)
            if up_started:
                # Compose may leave the containers if `langgraph up` stopped early.
                ai_dir = primary_paths(args.config)[1]["ai_dir"]
                subprocess.run(
                    ["docker", "compose", "-p", up_project(ai_dir), "down", "--remove-orphans"],
                    capture_output=True,
                )


if __name__ == "__main__":
    main()
