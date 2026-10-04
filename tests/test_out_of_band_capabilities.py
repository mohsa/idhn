import json

from idhn.audit.codex_events import out_of_band_items
from idhn.audit.m3_report import build_m3_report
from idhn.core.manifest import Manifest
from idhn.core.models import Task


def _write_events(run_dir, items: list[dict]) -> None:
    lines = [json.dumps({"type": "item.completed", "item": item}) for item in items]
    (run_dir / "codex-events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manifest() -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="demo", description="fix the bug"),
        capabilities={
            "filesystem": {"read": ["/workspace/**"], "write": [], "deny": []},
            "process": {"allow": ["cat"]},
            "network": {"allow": ["chatgpt.com"]},
            "git": {"allow": []},
        },
    )


def test_known_local_item_types_are_not_flagged(tmp_path):
    _write_events(
        tmp_path,
        [
            {"id": "i0", "type": "agent_message", "text": "hi"},
            {"id": "i1", "type": "command_execution", "command": "cat foo", "exit_code": 0},
            {"id": "i2", "type": "file_change", "changes": [{"path": "foo", "kind": "update"}], "status": "completed"},
            {"id": "i3", "type": "error", "message": "oops"},
        ],
    )

    assert out_of_band_items(tmp_path / "codex-events.jsonl") == []


def test_web_search_is_flagged_as_out_of_band(tmp_path):
    # The real shape observed in a live run (see
    # docs/web-search-out-of-band-capability.md): a genuine page fetch
    # performed entirely on Codex's own backend.
    web_search_item = {
        "id": "exec-abc",
        "type": "web_search",
        "query": "https://example.com",
        "action": {"type": "open_page", "url": "https://example.com"},
        "results": [{"type": "text_result", "domain": "example.com", "title": "Example Domain"}],
    }
    _write_events(tmp_path, [web_search_item])

    items = out_of_band_items(tmp_path / "codex-events.jsonl")

    assert len(items) == 1
    assert items[0]["type"] == "web_search"
    assert items[0]["action"]["url"] == "https://example.com"


def test_an_unrecognized_future_item_type_is_also_flagged(tmp_path):
    # The detector is an allowlist-of-known-local-shapes, not a list of
    # named out-of-band tools -- a brand new Codex tool this project has
    # never seen must still be flagged, not silently ignored.
    _write_events(tmp_path, [{"id": "i0", "type": "some_new_provider_tool", "payload": "..."}])

    items = out_of_band_items(tmp_path / "codex-events.jsonl")

    assert len(items) == 1
    assert items[0]["type"] == "some_new_provider_tool"


def test_no_events_file_returns_empty(tmp_path):
    assert out_of_band_items(tmp_path / "codex-events.jsonl") == []


def test_build_m3_report_surfaces_out_of_band_capabilities(tmp_path):
    _write_events(
        tmp_path,
        [
            {
                "id": "exec-abc",
                "type": "web_search",
                "query": "https://example.com",
                "action": {"type": "open_page", "url": "https://example.com"},
                "results": [],
            }
        ],
    )

    report = build_m3_report(tmp_path, _manifest())

    assert len(report["out_of_band_capabilities"]) == 1
    assert report["out_of_band_capabilities"][0]["type"] == "web_search"
