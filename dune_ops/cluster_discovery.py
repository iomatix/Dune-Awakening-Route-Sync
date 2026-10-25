import json
import re
import subprocess
from typing import Callable, List, Optional
from dune_ops.models import MapInstanceDTO, ServerInstanceStatus


class ClusterDiscoveryEngine:
    """Discovers active Dune: Awakening map server pods and validates their runtime readiness."""

    def __init__(
        self,
        vm_user: str = "dune",
        vm_ip: str = "192.168.1.57",
        runner: Optional[Callable[[str], str]] = None,
    ):
        self.vm_user = vm_user
        self.vm_ip = vm_ip
        self._runner = runner or self._default_ssh_runner
        self._cached_namespace: Optional[str] = None

    def _default_ssh_runner(self, command: str) -> str:
        ssh_cmd = [
            "ssh.exe",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{self.vm_user}@{self.vm_ip}",
            command,
        ]
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"Remote command execution failed: {proc.stderr.strip()}")
        return proc.stdout

    def resolve_namespace(self) -> Optional[str]:
        """Discovers the active Funcom seabass namespace."""
        if self._cached_namespace:
            return self._cached_namespace

        raw_json = self._runner("sudo kubectl get namespaces -o json")
        data = json.loads(raw_json)
        for item in data.get("items", []):
            ns = item.get("metadata", {}).get("name", "")
            if ns.startswith("funcom-seabass-"):
                self._cached_namespace = ns
                return ns
        return None

    @staticmethod
    def _extract_map_id(pod_name: str) -> str:
        """Extracts human-readable Map ID from Funcom pod name pattern."""
        match = re.search(r"-sg-([a-zA-Z0-9_-]+)-pod-", pod_name)
        if match:
            raw_segment = match.group(1)
            # Standardize e.g. survival-1 -> Survival_1, overmap -> Overmap
            parts = raw_segment.split("-")
            capitalized = [p.capitalize() for p in parts]
            return "_".join(capitalized)
        return pod_name

    def get_map_instances(self) -> List[MapInstanceDTO]:
        """Pulls running pods across the cluster and maps them to MapInstanceDTO objects."""
        namespace = self.resolve_namespace()
        if not namespace:
            return []

        raw_json = self._runner(f"sudo kubectl get pods -n {namespace} -o json")
        payload = json.loads(raw_json)
        instances: List[MapInstanceDTO] = []

        for pod in payload.get("items", []):
            metadata = pod.get("metadata", {})
            pod_name = metadata.get("name", "")

            # Filter only dedicated map server pods (e.g. sg-survival, sg-overmap)
            if "-sg-" not in pod_name:
                continue

            status_block = pod.get("status", {})
            phase = status_block.get("phase", "").lower()
            pod_ip = status_block.get("podIP")

            # Validate ready condition
            is_ready = False
            for cond in status_block.get("conditions", []):
                if cond.get("type") == "Ready" and cond.get("status") == "True":
                    is_ready = True
                    break

            if phase == "running" and is_ready:
                status = ServerInstanceStatus.RUNNING
            elif phase == "running" and not is_ready:
                status = ServerInstanceStatus.INITIALIZING
            elif phase in ["pending"]:
                status = ServerInstanceStatus.INITIALIZING
            elif phase in ["failed", "unknown"]:
                status = ServerInstanceStatus.UNHEALTHY
            else:
                status = ServerInstanceStatus.STOPPED

            map_id = self._extract_map_id(pod_name)
            serverset = pod_name.split("-pod-")[0] if "-pod-" in pod_name else pod_name

            instances.append(
                MapInstanceDTO(
                    serverset_name=serverset,
                    map_id=map_id,
                    pod_name=pod_name,
                    pod_ip=pod_ip,
                    igw_port=7888,
                    status=status,
                    is_ready=is_ready,
                    player_count=0,
                    max_players=40,
                )
            )

        return sorted(instances, key=lambda x: x.map_id)