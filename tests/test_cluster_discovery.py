import json
import pytest
from dune_ops.cluster_discovery import ClusterDiscoveryEngine
from dune_ops.models import ServerInstanceStatus


def test_namespace_discovery():
    mock_namespaces = {
        "items": [
            {"metadata": {"name": "default"}},
            {"metadata": {"name": "kube-system"}},
            {"metadata": {"name": "funcom-seabass-sh-7df0fcd3c71330f7-ymfyiy"}},
        ]
    }

    engine = ClusterDiscoveryEngine(runner=lambda cmd: json.dumps(mock_namespaces))
    ns = engine.resolve_namespace()
    assert ns == "funcom-seabass-sh-7df0fcd3c71330f7-ymfyiy"
    # Second call uses cache
    assert engine.resolve_namespace() == "funcom-seabass-sh-7df0fcd3c71330f7-ymfyiy"


def test_map_instance_parsing():
    mock_pods = {
        "items": [
            {
                "metadata": {"name": "sh-7df0fcd3c71330f7-ymfyiy-sg-survival-1-pod-1"},
                "status": {
                    "phase": "Running",
                    "podIP": "10.42.0.45",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
            {
                "metadata": {"name": "sh-7df0fcd3c71330f7-ymfyiy-sg-overmap-pod-1"},
                "status": {
                    "phase": "Running",
                    "podIP": "10.42.0.46",
                    "conditions": [{"type": "Ready", "status": "False"}],
                },
            },
            {
                "metadata": {"name": "sh-7df0fcd3c71330f7-ymfyiy-db-dbdepl-sts-0"},
                "status": {
                    "phase": "Running",
                    "podIP": "10.42.0.10",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
        ]
    }

    engine = ClusterDiscoveryEngine(runner=lambda cmd: json.dumps(mock_pods))
    engine._cached_namespace = "test-ns"

    instances = engine.get_map_instances()
    assert len(instances) == 2  # Database pod excluded

    survival = next(i for i in instances if i.map_id == "Survival_1")
    assert survival.status == ServerInstanceStatus.RUNNING
    assert survival.is_ready is True
    assert survival.pod_ip == "10.42.0.45"

    overmap = next(i for i in instances if i.map_id == "Overmap")
    assert overmap.status == ServerInstanceStatus.INITIALIZING
    assert overmap.is_ready is False