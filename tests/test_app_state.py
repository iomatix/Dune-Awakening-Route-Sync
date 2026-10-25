import pytest
from dune_ops.app import DuneOpsApp
from dune_ops.models import MapInstanceDTO, ServerInstanceStatus


def test_app_initialization():
    state = DuneOpsApp()
    assert state.i18n.current_lang == "pl"
    assert state.active_map is None
    assert len(state.players) == 0


def test_app_refresh_maps_selection():
    state = DuneOpsApp()
    mock_maps = [
        MapInstanceDTO(
            serverset_name="set1",
            map_id="Survival_1",
            pod_name="pod-survival",
            status=ServerInstanceStatus.RUNNING,
            is_ready=True,
        ),
        MapInstanceDTO(
            serverset_name="set2",
            map_id="Overmap",
            pod_name="pod-overmap",
            status=ServerInstanceStatus.STOPPED,
            is_ready=False,
        ),
    ]

    state.discovery.get_map_instances = lambda: mock_maps
    state.refresh_maps()

    assert len(state.available_maps) == 2
    assert state.active_map is not None
    assert state.active_map.map_id == "Survival_1"