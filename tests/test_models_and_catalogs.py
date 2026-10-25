import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dune_ops.catalogs import CatalogRegistry
from dune_ops.models import CoordinatesDTO, ItemSlotDTO, PlayerLiveStateDTO


def test_player_live_state_validation():
    player = PlayerLiveStateDTO(
        character_id="char_1",
        character_name="Zane",
        steam_id="76561198000000000",
        current_map_id="Survival_1",
        coordinates=CoordinatesDTO(x=100.0, y=200.0, z=50.0),
        equipped_items=[
            ItemSlotDTO(
                slot_id=1,
                slot_type="Armor_Suit",
                item_id="PowerPack5",
                display_name="Power Pack 5"
            )
        ]
    )
    assert player.character_name == "Zane"
    assert player.equipped_items[0].item_id == "PowerPack5"


def test_catalog_load_and_search(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    items = [
        {"item_id": "PowerPack5", "canonical_name": "Power Pack 5", "category": "tools", "max_stack": 1},
        {"item_id": "FuelCanister", "canonical_name": "Fuel Canister", "category": "resources", "max_stack": 10}
    ]
    with open(data_dir / "items_catalog.json", "w", encoding="utf-8") as f:
        json.dump(items, f)

    registry = CatalogRegistry(data_dir=data_dir)
    assert len(registry.items) == 2

    res = registry.search_items(query="power")
    assert len(res) == 1
    assert res[0].item_id == "PowerPack5"


@patch("subprocess.run")
def test_sync_from_database(mock_run, tmp_path: Path):
    data_dir = tmp_path / "data"
    registry = CatalogRegistry(data_dir=data_dir)

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = "PowerPack5\nFuelCanister\nBasicBuildingTool\n"
    mock_run.return_value = mock_process

    success, count, msg = registry.sync_from_database()
    assert success is True
    assert count == 3
    assert "powerpack5" in registry.items
    assert (data_dir / "items_catalog.json").exists()