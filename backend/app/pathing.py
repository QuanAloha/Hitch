from dataclass import dataclass
from typing import Optional, Any

@dataclass
class location_node:
    node_type: string,
    stop_id: string,
    location: Optional[dict[str, Any]] = None

