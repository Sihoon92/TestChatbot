from dataclasses import dataclass
from typing import Any

from app.config import Settings


@dataclass
class AppState:
    graph: Any
    db_path: str
    settings: Settings
