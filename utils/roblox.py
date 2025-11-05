from __future__ import annotations

import aiohttp


async def resolve_username(session: aiohttp.ClientSession, username: str) -> int | None:
    payload = {"usernames": [username.strip()], "excludeBannedUsers": False}
    async with session.post("https://users.roblox.com/v1/usernames/users", json=payload, timeout=15) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        matches = data.get("data", [])
        if not matches:
            return None
        return matches[0].get("id")


async def owns_gamepass(session: aiohttp.ClientSession, user_id: int, gamepass_id: int) -> bool:
    if user_id <= 0 or gamepass_id <= 0:
        return False
    url = f"https://inventory.roblox.com/v1/users/{user_id}/items/GamePass/{gamepass_id}"
    async with session.get(url, timeout=15) as resp:
        if resp.status != 200:
            return False
        payload = await resp.json()
        data = payload.get("data", [])
        return any(item.get("id") == gamepass_id for item in data)

