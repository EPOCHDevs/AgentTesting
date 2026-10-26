import json

import pytest

from tools.local_stack import configure_market_pages


def test_no_market_choice_leaves_runtime_untouched(tmp_path):
    # No read or write is required when this optional setting is absent.
    assert configure_market_pages(tmp_path / "server", None) == {}
    assert list(tmp_path.iterdir()) == []


def test_explicit_market_choice_preserves_other_runtime_configuration(tmp_path):
    path = tmp_path / "config.json"
    original = {
        "listeners": [{"address": "127.0.0.1", "port": 9000}],
        "plugins": [
            {"name": "unrelated", "config": {"keep": True}},
            {
                "name": "epoch_stratifyx::server::MiPageRuntimeService",
                "config": {
                    "poll_seconds": 30,
                    "calendar": {"economic_seconds": 600},
                },
            },
        ],
    }
    path.write_text(json.dumps(original))
    catalog = tmp_path / "catalog"
    (catalog / "current").mkdir(parents=True)
    # This check only establishes selection. Native startup validates contents.
    (catalog / "current/catalog_manifest.json").write_text("{}")
    env = configure_market_pages(tmp_path / "server", True, catalog_root=str(catalog))
    updated = json.loads(path.read_text())
    assert updated["listeners"] == original["listeners"]
    assert updated["plugins"][0] == original["plugins"][0]
    assert updated["plugins"][1]["config"] == {
        "poll_seconds": 30,
        "calendar": {
            "enabled": True,
            "refresh_mode": "automatic",
            "economic_seconds": 600,
        },
    }
    assert env == {
        "EPOCH_MI_RATES_PAGE": "1",
        "EPOCH_MI_RATES_DAILY": "1",
        "EPOCH_MI_LOCAL_ATOMIC_INSIGHTS": "1",
        "EPOCH_MI_PAGE_AUTO_REFRESH_ENABLED": "1",
    }
    env = configure_market_pages(tmp_path / "server", False)
    assert (
        env["EPOCH_MI_RATES_PAGE"] == env["EPOCH_MI_PAGE_AUTO_REFRESH_ENABLED"] == "0"
    )
    assert json.loads(path.read_text())["plugins"][1]["config"]["calendar"] == {
        "enabled": False,
        "refresh_mode": "paused",
        "economic_seconds": 600,
    }


@pytest.mark.parametrize("value", ["true", 1, 0, {}, []])
def test_market_choice_requires_a_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="true or false"):
        configure_market_pages(tmp_path / "server", value)


def test_missing_native_plugin_is_explicit_and_does_not_edit_config(tmp_path):
    path = tmp_path / "config.json"
    original = '{"plugins": []}'
    path.write_text(original)
    with pytest.raises(ValueError, match="exactly one"):
        configure_market_pages(tmp_path / "server", False)
    assert path.read_text() == original


@pytest.mark.parametrize("catalog_root", [None, "relative/catalog"])
def test_enabling_market_pages_requires_selected_catalog_before_editing(tmp_path, catalog_root):
    path = tmp_path / "config.json"
    path.write_text("unchanged")
    with pytest.raises(ValueError, match="EPOCH_CATALOG_ROOT"):
        configure_market_pages(tmp_path / "server", True, catalog_root=catalog_root)
    assert path.read_text() == "unchanged"


def test_unselected_catalog_does_not_enable_market_pages(tmp_path):
    with pytest.raises(ValueError, match="selected catalogue"):
        configure_market_pages(tmp_path / "server", True, catalog_root=str(tmp_path))
