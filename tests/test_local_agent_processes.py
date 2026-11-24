from pathlib import Path

import pytest

from tools.local_agent_processes import (
    LocalAgentProcess,
    local_agent_processes,
    verify_local_agent_isolation,
)


def test_different_agent_port_does_not_isolate_same_dataset_broker():
    old = LocalAgentProcess(1522798, 2032, "old-audit", "/missing/langgraph.json")
    with pytest.raises(ValueError, match="Different public API ports") as error:
        verify_local_agent_isolation(
            Path("/ai"),
            ("http://localhost:9000",),
            processes=[old],
            probe=lambda _: "http://127.0.0.1:9000",
        )
    assert "1522798" in str(error.value)
    assert "old-audit" in str(error.value)


def test_direct_backend_and_platform_ports_are_part_of_the_same_local_stack():
    for port in (9000, 9010, 9020):
        with pytest.raises(ValueError, match="shares the local Backend"):
            verify_local_agent_isolation(
                Path("/ai"),
                (
                    "http://localhost:9000",
                    "http://127.0.0.1:9010",
                    "http://127.0.0.1:9020",
                ),
                processes=[LocalAgentProcess(20, 2032, None, None)],
                probe=lambda _, port=port: f"http://localhost:{port}",
            )


@pytest.mark.parametrize("port", [None, 2032])
def test_unreadable_or_missing_health_cannot_establish_isolation(port):
    with pytest.raises(ValueError, match="unconfirmed Backend isolation"):
        verify_local_agent_isolation(
            Path("/ai"),
            ("http://localhost:9000",),
            processes=[LocalAgentProcess(20, port, None, None)],
            probe=lambda _: None,
        )


def test_another_backend_url_does_not_prove_an_isolated_queue():
    with pytest.raises(
        ValueError, match="does not prove a separate durable dataset queue"
    ):
        verify_local_agent_isolation(
            Path("/ai"),
            ("http://localhost:9000",),
            processes=[LocalAgentProcess(20, 2032, "other-test", None)],
            probe=lambda _: "http://localhost:9191",
        )


def test_malformed_health_url_is_unconfirmed_and_credentials_are_not_displayed():
    for url in ("http://[", "http://localhost:bad"):
        with pytest.raises(ValueError, match="unconfirmed Backend isolation"):
            verify_local_agent_isolation(
                Path("/ai"),
                ("http://localhost:9000",),
                processes=[LocalAgentProcess(20, 2032, None, None)],
                probe=lambda _, url=url: url,
            )
    with pytest.raises(ValueError) as error:
        verify_local_agent_isolation(
            Path("/ai"),
            ("http://localhost:9000",),
            processes=[LocalAgentProcess(20, 2032, None, None)],
            probe=lambda _: (
                "http://private-user:private-password@localhost:9000?token=private-token"
            ),
        )
    assert "private-" not in str(error.value)


def test_default_prepare_refuses_competing_producer_before_builds(
    tmp_path, monkeypatch
):
    from tools import local_stack
    from tools.agent_testing import Stack

    monkeypatch.setattr(
        local_stack,
        "primary_paths",
        lambda _: (
            {},
            {
                "backend_binary": tmp_path / "server",
                "ai_dir": tmp_path / "ai",
                "ui_dir": tmp_path / "ui",
            },
        ),
    )
    monkeypatch.setattr(local_stack, "verify_primary_checkouts", dict)

    def reject(*_args):
        raise ValueError("another producer shares the local Backend")

    monkeypatch.setattr(local_stack, "verify_local_agent_isolation", reject)
    with pytest.raises(ValueError, match="another producer"):
        local_stack.prepare(
            tmp_path / "config",
            Stack.load(Path("environments.toml"), "local-up"),
            tmp_path,
        )


def test_inventory_scopes_epochai_servers_and_does_not_emit_credentials(tmp_path):
    ai = tmp_path / "EpochAI"
    ai.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    for pid, cwd, args in [
        (
            12,
            ai,
            [
                str(ai / ".venv/bin/python"),
                str(ai / ".venv/bin/langgraph"),
                "dev",
                "--port",
                "2032",
                "--config",
                "/missing/config",
            ],
        ),
        (13, tmp_path, ["python", "unrelated.py"]),
        (
            14,
            tmp_path,
            ["python", "/other/.venv/bin/langgraph", "dev", "--port", "2222"],
        ),
    ]:
        p = proc / str(pid)
        p.mkdir()
        (p / "cwd").symlink_to(cwd)
        (p / "cmdline").write_bytes(
            b"\0".join(value.encode() for value in args) + b"\0"
        )
        (p / "environ").write_bytes(
            b"CODEX_THREAD_ID=old-audit\0OPENAI_API_KEY=must-not-be-emitted\0"
        )
    found = local_agent_processes(ai, proc)
    assert found == [LocalAgentProcess(12, 2032, "old-audit", "/missing/config")]
    assert "must-not-be-emitted" not in repr(found)


def test_module_entrypoint_and_missing_port_remain_visible(tmp_path):
    ai = tmp_path / "EpochAI"
    ai.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    p = proc / "42"
    p.mkdir()
    (p / "cwd").symlink_to(ai)
    (p / "cmdline").write_bytes(b"python\0-m\0langgraph_cli\0dev\0")
    assert local_agent_processes(ai, proc) == [LocalAgentProcess(42, None, None, None)]
