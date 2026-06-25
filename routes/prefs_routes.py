"""User preferences API — per-user key/value store backed by a JSON file."""
import json
import os
from typing import Optional
from fastapi import APIRouter, Request
from src.auth_helpers import get_current_user
from src.constants import USER_PREFS_FILE

PREFS_FILE = USER_PREFS_FILE


def _load():
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(prefs):
    os.makedirs(os.path.dirname(PREFS_FILE) or ".", exist_ok=True)
    temp = f"{PREFS_FILE}.tmp.{os.getpid()}"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, PREFS_FILE)


def _load_for_user(user: Optional[str] = None) -> dict:
    prefs = _load()
    if "_users" not in prefs:
        return dict(prefs)
    if user is None:
        users = prefs["_users"]
        return dict(next(iter(users.values()), {}))
    return dict(prefs["_users"].get(user, {}))


def _save_for_user(user: Optional[str], prefs: dict):
    all_prefs = _load()
    if user is None:
        if "_users" in all_prefs and all_prefs["_users"]:
            first = next(iter(all_prefs["_users"]))
            all_prefs["_users"][first] = prefs
            _save(all_prefs)
            return
        _save(prefs)
        return
    if "_users" not in all_prefs:
        all_prefs = {"_users": {}}
    all_prefs["_users"][user] = prefs
    _save(all_prefs)


def setup_prefs_routes():
    router = APIRouter(tags=["preferences"])

    @router.get("/api/prefs")
    async def get_all_prefs(request: Request):
        return _load_for_user(get_current_user(request))

    @router.get("/api/prefs/{key}")
    async def get_pref(request: Request, key: str):
        return {"key": key, "value": _load_for_user(get_current_user(request)).get(key)}

    @router.put("/api/prefs/{key}")
    async def set_pref(request: Request, key: str, body: dict):
        user = get_current_user(request)
        prefs = _load_for_user(user)
        prefs[key] = body.get("value")
        _save_for_user(user, prefs)
        return {"key": key, "value": prefs[key]}

    from routes.video_routes import setup_video_routes
    router.include_router(setup_video_routes())
    return router
