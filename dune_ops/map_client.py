import json
import re
import subprocess
import time
from typing import Callable, Dict, List, Optional, Tuple
from dune_ops.models import CoordinatesDTO, ItemSlotDTO, PlayerLiveStateDTO


class MapServerClient:
    """Handles real-time live player inspection and DEV command dispatching to active game pods."""

    def __init__(
        self,
        vm_user: str = "dune",
        vm_ip: str = "192.168.1.57",
        runner: Optional[Callable[[str], Tuple[int, str]]] = None,
        cache_ttl_sec: float = 3.0,
    ):
        self.vm_user = vm_user
        self.vm_ip = vm_ip
        self._runner = runner or self._default_ssh_runner
        self.cache_ttl_sec = cache_ttl_sec
        self._state_cache: Dict[str, Tuple[float, List[PlayerLiveStateDTO]]] = {}

    def _default_ssh_runner(self, command: str) -> Tuple[int, str]:
        ssh_cmd = [
            "ssh.exe",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{self.vm_user}@{self.vm_ip}",
            command,
        ]
        try:
            proc = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=12)
            out = (proc.stdout + "\n" + proc.stderr).strip()
            return proc.returncode, out
        except Exception as e:
            return 1, str(e)

    def execute_command(
        self,
        namespace: str,
        pod_name: str,
        command_string: str,
    ) -> Tuple[bool, str]:
        """Dispatches an administrative or GM console command to the specified map pod."""
        clean_cmd = command_string.strip().replace('"', '\\"')
        if not clean_cmd:
            return False, "Empty command string"

        remote_cmd = (
            f"sudo kubectl exec -i {pod_name} -n {namespace} -- "
            f"sh -c \"echo '{clean_cmd}' >> /tmp/game_dev_cmd.pipe 2>/dev/null || true\""
        )

        code, out = self._runner(remote_cmd)
        if code == 0:
            return True, f"Command dispatched: {command_string}"
        return False, f"Dispatch failed: {out}"

    @staticmethod
    def parse_player_dump(raw_text: str, map_id: str) -> List[PlayerLiveStateDTO]:
        """
        Parses structured or line-delimited player state stream from Unreal Engine instance.
        Expected format per record (JSON or key-value block):
        {
            "character_id": "c1",
            "character_name": "PlayerName",
            "steam_id": "76561198000000000",
            "ping_ms": 28,
            "coords": [100.5, 200.0, 50.2],
            "equipped": [{"slot_id": 1, "slot_type": "Suit", "item_id": "PowerPack5", "qty": 1}],
            "inventory": []
        }
        """
        players: List[PlayerLiveStateDTO] = []
        if not raw_text.strip():
            return players

        # Try parsing full JSON array or stream of JSON objects
        try:
            data = json.loads(raw_text)
            records = data if isinstance(data, list) else [data]
            for rec in records:
                coords_list = rec.get("coords", [0.0, 0.0, 0.0])
                coords = CoordinatesDTO(
                    x=float(coords_list[0]) if len(coords_list) > 0 else 0.0,
                    y=float(coords_list[1]) if len(coords_list) > 1 else 0.0,
                    z=float(coords_list[2]) if len(coords_list) > 2 else 0.0,
                )

                equipped = [
                    ItemSlotDTO(
                        slot_id=eq.get("slot_id", idx),
                        slot_type=eq.get("slot_type", "Gear"),
                        item_id=eq.get("item_id", "Unknown"),
                        display_name=eq.get("display_name", eq.get("item_id", "Unknown")),
                        quantity=eq.get("qty", 1),
                        durability=eq.get("durability"),
                    )
                    for idx, eq in enumerate(rec.get("equipped", []))
                ]

                inventory = [
                    ItemSlotDTO(
                        slot_id=inv.get("slot_id", idx),
                        slot_type=inv.get("slot_type", "Backpack"),
                        item_id=inv.get("item_id", "Unknown"),
                        display_name=inv.get("display_name", inv.get("item_id", "Unknown")),
                        quantity=inv.get("qty", 1),
                        durability=inv.get("durability"),
                    )
                    for idx, inv in enumerate(rec.get("inventory", []))
                ]

                players.append(
                    PlayerLiveStateDTO(
                        character_id=str(rec.get("character_id", "")),
                        character_name=str(rec.get("character_name", "Unknown")),
                        steam_id=str(rec.get("steam_id", "")),
                        current_map_id=map_id,
                        ping_ms=int(rec.get("ping_ms", 0)),
                        coordinates=coords,
                        equipped_items=equipped,
                        inventory_items=inventory,
                    )
                )
            return players
        except json.JSONDecodeError:
            pass

        # Regex fallback for standard Unreal Engine server log format:
        # Player: Zane (SteamID: 76561198000000000, Ping: 24ms, Loc: X=19581 Y=232845 Z=8141)
        pattern = re.compile(
            r"Player:\s+(?P<name>\S+)\s+\(SteamID:\s*(?P<steam>\d+),\s*Ping:\s*(?P<ping>\d+)ms,\s*Loc:\s*X=(?P<x>[-\d.]+)\s*Y=(?P<y>[-\d.]+)\s*Z=(?P<z>[-\d.]+)\)",
            re.IGNORECASE,
        )

        for line in raw_text.splitlines():
            match = pattern.search(line)
            if match:
                players.append(
                    PlayerLiveStateDTO(
                        character_id=match.group("steam"),
                        character_name=match.group("name"),
                        steam_id=match.group("steam"),
                        current_map_id=map_id,
                        ping_ms=int(match.group("ping")),
                        coordinates=CoordinatesDTO(
                            x=float(match.group("x")),
                            y=float(match.group("y")),
                            z=float(match.group("z")),
                        ),
                    )
                )

        return players

    def get_live_players(
        self,
        namespace: str,
        pod_name: str,
        map_id: str,
        force_refresh: bool = False,
    ) -> List[PlayerLiveStateDTO]:
        """Fetches live player states with cache TTL protection against tick degradation."""
        now = time.time()
        if not force_refresh and map_id in self._state_cache:
            ts, cached_players = self._state_cache[map_id]
            if now - ts < self.cache_ttl_sec:
                return cached_players

        # Query live state from pod via memory pipe / diagnostic tail
        query_cmd = (
            f"sudo kubectl exec -i {pod_name} -n {namespace} -- "
            "cat /tmp/players_state.json 2>/dev/null || true"
        )
        code, out = self._runner(query_cmd)

        players = self.parse_player_dump(out, map_id) if code == 0 else []
        self._state_cache[map_id] = (now, players)
        return players