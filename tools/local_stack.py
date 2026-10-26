"""Start the three local services; Ctrl+C stops only these child processes."""

import argparse
import asyncio
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from tools.agent_testing import Stack, check_stack
from tools.local_auth import CLIENT, POOL, REGION, cached_token, login, token_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_BRANCHES = {
    "EpochBackend": "main",
    "EpochAI": "dev",
    "EpochClientPortal": "dev",
}


def primary_paths(config_file: Path) -> tuple[dict, dict[str, Path]]:
    """The default stack always uses the three primary sibling repositories."""
    config = tomllib.loads(config_file.read_text()) if config_file.exists() else {}
    paths = {
        "backend_binary": PROJECT_ROOT
        / "EpochBackend/build/bin/epoch_stratifyx_server",
        "backend_env": PROJECT_ROOT / "EpochBackend/services/stratifyx/.env.local",
        "ai_dir": PROJECT_ROOT / "EpochAI",
        "ui_dir": PROJECT_ROOT / "EpochClientPortal",
    }
    unknown = set(config) - set(paths) - {"node_bin"}
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
            raise ValueError(
                f"{project} is not synchronized with origin/{expected}. Update that checkout first."
            )
        records[project] = {"path": str(directory), "branch": branch, "commit": commit}
    return records


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path.resolve()


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
                "The launcher requires explicit localhost HTTP ports in [local]."
            )
        ports.append(parts.port)
    if len(set(ports)) != 3:
        raise ValueError("Backend, AI and UI need three distinct ports.")
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


def prepare(config_file: Path, stack: Stack, state: Path) -> list[tuple]:
    config, paths = primary_paths(config_file)
    revisions = verify_primary_checkouts()
    for name, revision in revisions.items():
        print(
            f"{name}: {revision['branch']} @ {revision['commit'][:10]} ({revision['path']})",
            flush=True,
        )
    (state / "source-checkouts.json").write_text(json.dumps(revisions, indent=2))
    binary, ai, ui = paths["backend_binary"], paths["ai_dir"], paths["ui_dir"]
    langgraph = ai / ".venv/bin/langgraph"
    for executable in (binary, langgraph):
        if not os.access(executable, os.X_OK):
            raise ValueError(
                f"Missing executable: {executable}. The launcher does not build or install products."
            )
    if not (ui / "node_modules").is_dir():
        raise ValueError(f"Install UI dependencies with pnpm in {ui} first.")
    ports = local_ports(stack)
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
        "EPOCH_AI_URL": stack.ai_url,
        "LANGGRAPH_API_URL": stack.ai_url,
        "COGNITO_REGION": REGION,
        "COGNITO_USER_POOL_ID": POOL,
        "COGNITO_CLIENT_ID": CLIENT,
        "COGNITO_CLIENT_IDS": CLIENT,
    }
    backend_env = {
        **base,
        **read_env(paths["backend_env"]),
        **common,
        "PORT": str(ports[0]),
        "EPOCH_SKIP_LOAD_JOBS": "0",
        "EPOCH_PROJECT_DIR": str(private_dir(state / "backend")),
        "EPOCH_JOB_STAGING_DIR": str(private_dir(state / "backend/staging")),
        "EPOCH_ENDPOINT_CACHE_DIR": str(private_dir(state / "backend/cache")),
        "EPOCH_COMPILER_METADATA_DIR": str(binary.parent / "compiler_metadata"),
    }

    # LangGraph loads its config env after process env. Supply local overrides
    # inside a private generated config, leaving the product's files untouched.
    graph_config = json.loads((ai / "langgraph.json").read_text())
    env_source = graph_config.get("env", {})
    ai_values = read_env(ai / env_source) if isinstance(env_source, str) else env_source
    ai_env = {**base, **ai_values, **common}
    graph_config["env"] = {**ai_values, **common}
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
    ui_env = {
        **base,
        "VITE_API_BASE_URL": stack.backend_url,
        "VITE_LANGGRAPH_API_URL": stack.ai_url,
        "VITE_LANGGRAPH_ASSISTANT_ID": stack.assistant_id,
        "VITE_COGNITO_USER_POOL_ID": POOL,
        "VITE_COGNITO_USER_POOL_CLIENT_ID": CLIENT,
    }
    return [
        (
            "Backend",
            [str(binary), "--port", str(ports[0])],
            state / "backend",
            backend_env,
            stack.backend_url + "/api/v1/health",
        ),
        (
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
        ),
        (
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


def main() -> None:
    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local-stack.toml"))
    parser.add_argument("--environments", type=Path, default=Path("environments.toml"))
    parser.add_argument("--email")
    parser.add_argument(
        "--login", action="store_true", help="Replace the cached local login"
    )
    args = parser.parse_args()
    children = []
    state = private_dir(Path(".artifacts/local-stack"))
    with (state / "launcher.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "The local launcher is already running.\n")
        try:
            stack = Stack.load(args.environments, "local")
            check_ports(local_ports(stack))
            services = prepare(args.config, stack, state)
            bearer = (
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
                print(f"Starting {name}; log: {log}", flush=True)
                wait_ready(url, children, 180)
            receipt = asyncio.run(check_stack(stack, "Bearer " + bearer))
            (state / "health.json").write_text(
                json.dumps(receipt, indent=2, default=str)
            )
            print(
                f"Ready: {stack.ui_url}\nLocal CLI login is cached. Browser login uses the same account.\n"
                "Ctrl+C stops these three services. No research has been started.",
                flush=True,
            )
            while all(child.poll() is None for child in children):
                time.sleep(1)
            raise ValueError("A local service exited. Inspect the logs above.")
        except KeyboardInterrupt:
            print("\nStopping local services.")
        except (ValueError, OSError, RuntimeError, httpx.HTTPError) as exc:
            parser.exit(1, f"{exc}\n")
        finally:
            stop(children)


if __name__ == "__main__":
    main()
