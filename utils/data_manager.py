from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional


DEFAULT_SETTINGS: Dict[str, Any] = {
    "guild_id": 1434305775572091063,
    "channels": {
        "transcript_id": 1434340181066125342,
        "transactions_id": 1434340158995562647,
    },
    "roles": {
        "buyer": 0,
        "support": 0,
        "cashseller": 0,
        "mm2_approver": 0,
    },
    "panel": {
        "banner": "assets/panel_banner.png",
        "teal_hex": "0x31C6D4",
        "options": {
            "cash_available": True,
            "builds_available": True,
        },
    },
}

DEFAULT_PRODUCTS: Dict[str, Any] = {}

DEFAULT_CASH: Dict[str, Any] = {
    "tiers": [],
    "seller_role_id": 0,
}

DEFAULT_ORDERS: Dict[str, Any] = {
    "orders": []
}

DEFAULT_STATS: Dict[str, Any] = {
    "totals": {
        "overall": 0,
        "robux": 0,
        "usd": 0,
        "adopt": 0,
        "mm2": 0,
    },
    "by_type": {
        "normal": 0,
        "file": 0,
        "key": 0,
        "role": 0,
    },
}


@dataclass(slots=True)
class JSONFile:
    name: str
    path: Path
    default: Dict[str, Any]
    lock: asyncio.Lock


class DataManager:
    def __init__(self, base_path: Path) -> None:
        self._files: Dict[str, JSONFile] = {
            "settings": JSONFile(
                "settings", base_path / "data" / "settings.json", DEFAULT_SETTINGS, asyncio.Lock()
            ),
            "products": JSONFile(
                "products", base_path / "data" / "products.json", DEFAULT_PRODUCTS, asyncio.Lock()
            ),
            "cash": JSONFile(
                "cash", base_path / "data" / "cash.json", DEFAULT_CASH, asyncio.Lock()
            ),
            "orders": JSONFile(
                "orders", base_path / "data" / "orders.json", DEFAULT_ORDERS, asyncio.Lock()
            ),
            "stats": JSONFile(
                "stats", base_path / "data" / "stats.json", DEFAULT_STATS, asyncio.Lock()
            ),
        }

    async def ensure_all(self) -> None:
        for info in self._files.values():
            info.path.parent.mkdir(parents=True, exist_ok=True)
            if not info.path.exists():
                await self._write(info, info.default)

    async def load(self, name: str) -> Dict[str, Any]:
        info = self._get(name)
        async with info.lock:
            return self._read(info)

    async def save(self, name: str, payload: Dict[str, Any]) -> None:
        info = self._get(name)
        async with info.lock:
            await self._write(info, payload)

    async def update(self, name: str, mutator: Callable[[Dict[str, Any]], Any]) -> Any:
        info = self._get(name)
        async with info.lock:
            data = self._read(info)
            result = mutator(data)
            await self._write(info, data)
            return result

    async def append_order(self, payload: Dict[str, Any]) -> None:
        async def mutate(data: Dict[str, Any]) -> None:
            data.setdefault("orders", []).append(payload)

        await self.update("orders", mutate)

    async def update_order_status(self, ticket_id: int, status: str, *, method: Optional[str] = None) -> None:
        async def mutate(data: Dict[str, Any]) -> None:
            for order in data.get("orders", []):
                if order.get("ticket_id") == ticket_id:
                    order["status"] = status
                    if method:
                        order["method"] = method
                    order["updated_at"] = datetime.now(timezone.utc).isoformat()
                    break

        await self.update("orders", mutate)

    def _get(self, name: str) -> JSONFile:
        if name not in self._files:
            raise KeyError(f"Unknown data file: {name}")
        return self._files[name]

    def _read(self, info: JSONFile) -> Dict[str, Any]:
        if not info.path.exists():
            self._write_blocking(info, info.default)
            return json.loads(json.dumps(info.default))
        try:
            data = json.loads(info.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._write_blocking(info, info.default)
            data = json.loads(json.dumps(info.default))
        return data

    async def _write(self, info: JSONFile, payload: Dict[str, Any]) -> None:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        info.path.write_text(text, encoding="utf-8")

    def _write_blocking(self, info: JSONFile, payload: Dict[str, Any]) -> None:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        info.path.write_text(text, encoding="utf-8")

