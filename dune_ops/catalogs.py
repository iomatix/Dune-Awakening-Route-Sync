import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dune_ops.models import CommandDefinitionDTO, ItemCatalogEntryDTO


class CatalogRegistry:
    """Manages dynamic and cached item definitions and GM engine commands."""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent / "data"
        self.data_dir = data_dir
        self.items_cache_file = self.data_dir / "items_catalog.json"
        self.commands_file = self.data_dir / "commands_catalog.json"

        self.items: Dict[str, ItemCatalogEntryDTO] = {}
        self.commands: Dict[str, CommandDefinitionDTO] = {}
        self.load_local_cache()

    def load_local_cache(self) -> None:
        """Loads cached items and engine commands from local disk."""
        self.items.clear()
        self.commands.clear()

        if self.items_cache_file.exists():
            try:
                with open(self.items_cache_file, "r", encoding="utf-8") as f:
                    raw_items = json.load(f)
                    for item in raw_items:
                        dto = ItemCatalogEntryDTO.model_validate(item)
                        self.items[dto.item_id.lower()] = dto
            except Exception:
                self.items.clear()

        if self.commands_file.exists():
            try:
                with open(self.commands_file, "r", encoding="utf-8") as f:
                    raw_commands = json.load(f)
                    for cmd in raw_commands:
                        dto = CommandDefinitionDTO.model_validate(cmd)
                        self.commands[dto.command.lower()] = dto
            except Exception:
                self.commands.clear()

    @staticmethod
    def _categorize_item_id(item_id: str) -> str:
        """Infers category heuristically from Unreal Engine asset naming convention."""
        lower = item_id.lower()
        if any(x in lower for x in ["knife", "sword", "rifle", "pistol", "gun", "weapon"]):
            return "weapons"
        if any(x in lower for x in ["suit", "armor", "helmet", "boots", "gloves", "shield"]):
            return "armor"
        if any(x in lower for x in ["water", "food", "ration", "spice", "booster"]):
            return "consumables"
        if any(x in lower for x in ["bike", "vehicle", "ornithopter", "car"]):
            return "vehicles"
        if any(x in lower for x in ["tool", "cutter", "scanner", "pack"]):
            return "tools"
        return "resources"

    def sync_from_database(self, vm_user: str = "dune", vm_ip: str = "192.168.1.57") -> Tuple[bool, int, str]:
        """
        Executes query on the cluster DB to pull all registered template_ids,
        updates memory registry, and writes fresh cache to disk.
        """
        query_cmd = (
            "ssh.exe -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            f"{vm_user}@{vm_ip} \""
            "sudo kubectl get pods -A -l app.kubernetes.io/name=dbdepl -o jsonpath='{.items[0].metadata.name}' | "
            "xargs -I {} sudo kubectl exec -i {} -n funcom-seabass-sh-7df0fcd3c71330f7-ymfyiy -- "
            "psql -h 127.0.0.1 -p 15432 -U dune -d dune -t -A -c "
            "'SELECT DISTINCT template_id FROM items WHERE template_id IS NOT NULL AND template_id != \\'\\' ORDER BY template_id;'\""
        )

        try:
            res = subprocess.run(query_cmd, shell=True, capture_output=True, text=True, timeout=15)
            if res.returncode != 0:
                return False, 0, f"Query error: {res.stderr.strip()}"

            raw_ids = [line.strip() for line in res.stdout.splitlines() if line.strip() and not line.startswith("Warning")]
            if not raw_ids:
                return False, 0, "No template_ids returned from database."

            for template_id in raw_ids:
                # Pretty-format display name (e.g. ContractScavengers1HoltzmanAmplifier -> Contract Scavengers 1 Holtzman Amplifier)
                clean_name = re.sub(r"([a-z])([A-Z])", r"\1 \2", template_id)
                category = self._categorize_item_id(template_id)

                dto = ItemCatalogEntryDTO(
                    item_id=template_id,
                    canonical_name=clean_name,
                    category=category,
                    max_stack=1
                )
                self.items[template_id.lower()] = dto

            # Atomically save to local cache
            self.data_dir.mkdir(parents=True, exist_ok=True)
            export_data = [item.model_dump() for item in self.items.values()]
            with open(self.items_cache_file, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            return True, len(raw_ids), f"Successfully synchronized {len(raw_ids)} items."

        except Exception as e:
            return False, 0, f"Synchronization exception: {e}"

    def search_items(self, query: str, category: Optional[str] = None) -> List[ItemCatalogEntryDTO]:
        q = query.lower().strip()
        results = []
        for item in self.items.values():
            if category and category != "all" and item.category != category:
                continue
            if not q or q in item.item_id.lower() or q in item.canonical_name.lower():
                results.append(item)
        return results

    def get_command(self, cmd_name: str) -> Optional[CommandDefinitionDTO]:
        return self.commands.get(cmd_name.lower().strip())