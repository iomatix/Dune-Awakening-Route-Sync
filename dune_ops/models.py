from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class ServerInstanceStatus(str, Enum):
    RUNNING = "running"
    INITIALIZING = "initializing"
    STOPPED = "stopped"
    UNHEALTHY = "unhealthy"


class CoordinatesDTO(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class ItemSlotDTO(BaseModel):
    slot_id: int
    slot_type: str
    item_id: str
    display_name: str
    quantity: int = 1
    durability: Optional[float] = None


class PlayerLiveStateDTO(BaseModel):
    character_id: str
    character_name: str
    steam_id: str
    current_map_id: str
    ping_ms: int = 0
    coordinates: CoordinatesDTO = Field(default_factory=CoordinatesDTO)
    equipped_items: List[ItemSlotDTO] = Field(default_factory=list)
    inventory_items: List[ItemSlotDTO] = Field(default_factory=list)


class MapInstanceDTO(BaseModel):
    serverset_name: str
    map_id: str
    pod_name: Optional[str] = None
    pod_ip: Optional[str] = None
    igw_port: int = 7888
    status: ServerInstanceStatus = ServerInstanceStatus.STOPPED
    is_ready: bool = False
    player_count: int = 0
    max_players: int = 40


class CommandParameterDTO(BaseModel):
    name: str
    type: str  # "string", "int", "float", "player_id", "item_id"
    required: bool = True
    default: Optional[str] = None


class CommandDefinitionDTO(BaseModel):
    command: str
    category: str
    syntax: str
    description_key: str
    parameters: List[CommandParameterDTO] = Field(default_factory=list)
    example: str


class ItemCatalogEntryDTO(BaseModel):
    item_id: str
    canonical_name: str
    category: str = "general"
    max_stack: int = 1