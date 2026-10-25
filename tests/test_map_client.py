import json
import pytest
from dune_ops.map_client import MapServerClient


def test_execute_command_success():
    def dummy_runner(cmd: str):
        assert "GiveItem Player1 PowerPack5 1" in cmd
        return 0, ""

    client = MapServerClient(runner=dummy_runner)
    success, msg = client.execute_command("test-ns", "test-pod", "GiveItem Player1 PowerPack5 1")
    assert success is True
    assert "dispatched" in msg


def test_execute_command_empty():
    client = MapServerClient()
    success, msg = client.execute_command("test-ns", "test-pod", "   ")
    assert success is False
    assert "Empty command" in msg


def test_parse_player_dump_json():
    json_dump = json.dumps([
        {
            "character_id": "c_1",
            "character_name": "Zane",
            "steam_id": "76561198000000000",
            "ping_ms": 32,
            "coords": [19581.7, 232845.9, 8141.1],
            "equipped": [
                {"slot_id": 0, "slot_type": "Suit", "item_id": "PowerPack5", "qty": 1}
            ],
            "inventory": [
                {"slot_id": 1, "slot_type": "Backpack", "item_id": "BasicBuildingTool", "qty": 1}
            ]
        }
    ])

    client = MapServerClient()
    players = client.parse_player_dump(json_dump, "Survival_1")
    assert len(players) == 1
    assert players[0].character_name == "Zane"
    assert players[0].coordinates.x == 19581.7
    assert len(players[0].equipped_items) == 1
    assert players[0].equipped_items[0].item_id == "PowerPack5"
    assert len(players[0].inventory_items) == 1
    assert players[0].inventory_items[0].item_id == "BasicBuildingTool"


def test_parse_player_dump_regex_fallback():
    raw_log = (
        "LogIGW: Display: Player: Zane (SteamID: 76561198000000000, Ping: 18ms, Loc: X=1200.5 Y=3400.0 Z=150.2)\n"
        "LogIGW: Display: Some other engine trace"
    )

    client = MapServerClient()
    players = client.parse_player_dump(raw_log, "Survival_1")
    assert len(players) == 1
    assert players[0].character_name == "Zane"
    assert players[0].steam_id == "76561198000000000"
    assert players[0].ping_ms == 18
    assert players[0].coordinates.x == 1200.5


def test_cache_ttl_protection():
    call_count = 0

    def counting_runner(cmd: str):
        nonlocal call_count
        call_count += 1
        return 0, "[]"

    client = MapServerClient(runner=counting_runner, cache_ttl_sec=2.0)
    client.get_live_players("test-ns", "test-pod", "Survival_1")
    client.get_live_players("test-ns", "test-pod", "Survival_1")

    # Should only execute once due to TTL cache
    assert call_count == 1

    # Force refresh bypasses cache
    client.get_live_players("test-ns", "test-pod", "Survival_1", force_refresh=True)
    assert call_count == 2