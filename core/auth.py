"""
Authentication module — multi-user password hashing, session tokens, config persistence.
Config stored in data/auth.json. Uses bcrypt directly.
"""

import enum
import json
import os
import secrets
import threading
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

import bcrypt
import pyotp

logger = logging.getLogger(__name__)


from core.atomic_io import atomic_write_json as _atomic_write_json  # noqa: E402
from core.middleware import INTERNAL_TOOL_USER  # noqa: E402

DEFAULT_PRIVILEGES = {
    "can_use_agent": True,
    "can_use_browser": True,
    "can_use_bash": False,
    "can_use_documents": True,
    "can_use_research": True,
    "can_generate_images": True,
    # Video is a separate media capability because it may trigger a local
    # high-memory model run. Default it on for continuity with image generation;
    # administrators can disable it per account in the same privileges panel.
    "can_generate_videos": True,
    "can_manage_memory": True,
    "max_messages_per_day": 0,
    "allowed_models": [],
    "allowed_models_restricted": False,
    # Explicit "block every model" sentinel. An empty `allowed_models` list is
    # ambiguous — it's also what gets sent when the admin clicks "[All]" — so
    # we need a dedicated flag to express "this user may use no models at all"
    # distinctly from "this user has no restriction".
    "block_all_models": False,
}

# Admins get everything
ADMIN_PRIVILEGES = {k: (True if isinstance(v, bool) else (0 if isinstance(v, int) else [])) for k, v in DEFAULT_PRIVILEGES.items()}
ADMIN_PRIVILEGES["allowed_models_restricted"] = False
# Admins must never be blocked from using models — the generic dict
# comprehension above flips every boolean default to True, which would be
# backwards for this sentinel.
ADMIN_PRIVILEGES["block_all_models"] = False

from src.constants import AUTH_FILE, PASSWORD_MIN_LENGTH
DEFAULT_AUTH_PATH = AUTH_FILE
TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days

# Usernames the auth + middleware layer reserve as internal "synthetic owner"
# sentinels; they must never belong to a real account. The most dangerous is
# "internal-tool": `core.middleware.require_admin` treats any request whose
# `current_user == "internal-tool"` as the in-process tool loopback and grants
# admin, and because the cookie auth path sets `current_user` to the raw
# username, an account literally named "internal-tool" would be silently
# treated as an admin by every `require_admin`-gated route. "api" collides with
# the bearer-token owner-attribution sentinel. "demo"/"system" round out the
# synthetic-owner set the rest of the codebase already special-cases (see
# `_SYNTHETIC_OWNERS` in routes/assistant_routes.py and the matching guards in
# src/task_scheduler.py / routes/research_routes.py) — a real account with one
# of those names would be denied an assistant and inconsistently owner-scoped.
# Refuse to create or rename into any of them so the sentinels can't be
# impersonated. (Keep this in sync with that synthetic-owner set.)
RESERVED_USERNAMES = frozenset({INTERNAL_TOOL_USER, "api", "demo", "system"})


def normalize_known_username(users: Dict[str, Any], username: str | None) -> Optional[str]:
    """Return a normalized username only when it exists in the auth user map."""
    key = str(username or "").strip().lower()
    if not key or key not in users:
        return None
    return key


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


class SetAdminResult(enum.Enum):
    """Outcome of AuthManager.set_admin, so callers can map each case to a
    precise response instead of guessing from a bare bool."""
    OK = "ok"
    USER_NOT_FOUND = "user_not_found"
    NOT_AUTHORIZED = "not_authorized"   # requester is not an admin
    LAST_ADMIN = "last_admin"


class AuthManager:
    """Manages multi-user password + session-token auth system."""

    def __init__(self, auth_path: str = DEFAULT_AUTH_PATH):
        self.auth_path = auth_path
        self._sessions_path = os.path.join(os.path.dirname(auth_path), "sessions.json")
        self._config: Dict[str, Any] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._sessions_lock = threading.RLock()
        self._config_lock = threading.Lock()
        self._setup_lock = threading.Lock()
        self._load()
        self._load_sessions()
        self._migrate_single_user()
        self._drop_reserved_loaded_users()
        self._migrate_legacy_admin_role()

    def _load(self):
        try:
            if os.path.exists(self.auth_path):
                with open(self.auth_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                if "users" in self._config:
                    self._config["users"] = {k.strip().lower(): v for k, v in self._config["users"].items()}
                logger.info("Auth config loaded")
            else:
                self._config = {}
                logger.info("No auth config found — first-run setup required")
        except Exception as e:
            logger.error(f"Failed to load auth config: {e}")
            self._config = {}

    def _load_sessions(self):
        try:
            if os.path.exists(self._sessions_path):
                with open(self._sessions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                self._sessions = {k: v for k, v in data.items() if v.get("expiry", 0) > now}
                if len(data) != len(self._sessions):
                    self._save_sessions()
                logger.info(f"Loaded {len(self._sessions)} session(s) from disk")
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")
            self._sessions = {}

    def _save_sessions(self):
        try:
            with self._sessions_lock:
                snapshot = dict(self._sessions)
            _atomic_write_json(self._sessions_path, snapshot)
        except Exception as e:
            logger.error(f"Failed to save sessions: {e}")

    def _migrate_single_user(self):
        if "password_hash" in self._config and "users" not in self._config:
            old_user = str(self._config.get("username", "admin") or "admin").strip().lower()
            if old_user in RESERVED_USERNAMES:
                logger.warning("Migrating legacy single-user reserved username '%s' to 'admin'", old_user)
                old_user = "admin"
            self._config = {"users": {old_user: {"password_hash": self._config["password_hash"], "created": time.time(), "is_admin": True}}}
            self._save()
            logger.info(f"Migrated single-user auth to multi-user (admin: {old_user})")

    def _drop_reserved_loaded_users(self):
        users = self._config.get("users")
        if not isinstance(users, dict):
            return
        normalized, removed = {}, []
        for username, data in users.items():
            key = str(username or "").strip().lower()
            if not key:
                continue
            if key in RESERVED_USERNAMES:
                removed.append(key)
                continue
            normalized[key] = data
        if removed or normalized != users:
            self._config["users"] = normalized
            self._save()
        if removed:
            logger.warning("Removed reserved username(s) from auth config: %s", ", ".join(sorted(set(removed))))

    def _migrate_legacy_admin_role(self):
        changed = False
        for username, user in self.users.items():
            if user.get("role") == "admin" and "is_admin" not in user:
                user["is_admin"] = True
                changed = True
                logger.info(f"Migrated legacy admin role for '{username}'")
        if changed:
            self._save()

    def _save(self):
        _atomic_write_json(self.auth_path, self._config, indent=2)

    @property
    def users(self) -> Dict[str, Any]:
        return self._config.get("users", {})

    @property
    def signup_enabled(self) -> bool:
        return self._config.get("signup_enabled", False)

    @signup_enabled.setter
    def signup_enabled(self, value: bool):
        with self._config_lock:
            self._config["signup_enabled"] = value
            self._save()

    @property
    def is_configured(self) -> bool:
        return len(self.users) > 0

    def policy(self) -> dict:
        return {"password_min_length": PASSWORD_MIN_LENGTH, "reserved_usernames": sorted(RESERVED_USERNAMES), "signup_enabled": self.signup_enabled, "session_days": TOKEN_TTL // 86400}

    def setup(self, username: str, password: str) -> bool:
        with self._setup_lock:
            if self.is_configured:
                return False
            return self.create_user(username, password, is_admin=True)

    def create_user(self, username: str, password: str, is_admin: bool = False) -> bool:
        username = username.strip().lower()
        if not username or username in RESERVED_USERNAMES:
            return False
        with self._config_lock:
            if username in self.users:
                return False
            if "users" not in self._config:
                self._config["users"] = {}
            self._config["users"][username] = {"password_hash": _hash_password(password), "created": time.time(), "is_admin": is_admin, "privileges": dict(ADMIN_PRIVILEGES if is_admin else DEFAULT_PRIVILEGES)}
            self._save()
        logger.info(f"Created user '{username}' (admin={is_admin})")
        return True

    def delete_user(self, username: str, requesting_user: str) -> bool:
        username = username.strip().lower()
        with self._config_lock:
            if username not in self.users or username == requesting_user or not self.users.get(requesting_user, {}).get("is_admin"):
                return False
            try:
                from core.database import get_db_session, ApiToken
                with get_db_session() as db:
                    db.query(ApiToken).filter(ApiToken.owner == username).delete()
            except Exception:
                logger.warning(f"Failed to revoke API tokens for deleted user '{username}'")
                return False
            del self._config["users"][username]
            self._save()
        with self._sessions_lock:
            tokens = [tok for tok, sess in self._sessions.items() if sess.get("username") == username]
            for token in tokens:
                self._sessions.pop(token, None)
        self._save_sessions()
        return True

    def list_users(self) -> List[Dict[str, Any]]:
        return [
            {"username": username, "is_admin": bool(data.get("is_admin")), "privileges": self.get_privileges(username)}
            for username, data in self.users.items()
        ]

    def is_admin(self, username: Optional[str]) -> bool:
        return bool(username and self.users.get(username, {}).get("is_admin", False))

    def get_privileges(self, username: Optional[str]) -> Dict[str, Any]:
        if self.is_admin(username):
            return dict(ADMIN_PRIVILEGES)
        stored = self.users.get(username or "", {}).get("privileges", {})
        return {**DEFAULT_PRIVILEGES, **(stored if isinstance(stored, dict) else {})}

    def set_privileges(self, username: str, updates: Dict[str, Any]) -> bool:
        username = (username or "").strip().lower()
        if not username or self.is_admin(username) or username not in self.users:
            return False
        allowed = set(DEFAULT_PRIVILEGES)
        with self._config_lock:
            current = self.users[username].setdefault("privileges", dict(DEFAULT_PRIVILEGES))
            for key, value in (updates or {}).items():
                if key not in allowed:
                    continue
                if isinstance(DEFAULT_PRIVILEGES[key], bool):
                    current[key] = bool(value)
                elif isinstance(DEFAULT_PRIVILEGES[key], int):
                    current[key] = max(0, int(value or 0))
                elif isinstance(DEFAULT_PRIVILEGES[key], list):
                    current[key] = list(value) if isinstance(value, list) else []
            self._save()
        return True

    def create_session_trusted(self, username: str) -> Optional[str]:
        username = (username or "").strip().lower()
        if username not in self.users:
            return None
        token = secrets.token_urlsafe(32)
        with self._sessions_lock:
            self._sessions[token] = {"username": username, "expiry": time.time() + TOKEN_TTL}
        self._save_sessions()
        return token

    def validate_token(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        with self._sessions_lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if session.get("expiry", 0) < time.time():
                self._sessions.pop(token, None)
                self._save_sessions()
                return None
            return session.get("username")

    def revoke_token(self, token: Optional[str]):
        if token:
            with self._sessions_lock:
                self._sessions.pop(token, None)
            self._save_sessions()

    def revoke_user_sessions(self, username: str, keep_token: Optional[str] = None):
        username = (username or "").strip().lower()
        with self._sessions_lock:
            for token in list(self._sessions):
                if token != keep_token and self._sessions[token].get("username") == username:
                    self._sessions.pop(token, None)
        self._save_sessions()

    def change_password(self, username: str, current_password: str, new_password: str) -> bool:
        username = (username or "").strip().lower()
        if not username or username not in self.users or not _verify_password(current_password, self.users[username].get("password_hash", "")):
            return False
        with self._config_lock:
            self.users[username]["password_hash"] = _hash_password(new_password)
            self._save()
        return True

    def rename_user(self, old_username: str, new_username: str, requesting_user: str) -> bool:
        old_username = (old_username or "").strip().lower()
        new_username = (new_username or "").strip().lower()
        if not old_username or not new_username or new_username in RESERVED_USERNAMES:
            return False
        with self._config_lock:
            if old_username not in self.users or new_username in self.users:
                return False
            if not self.is_admin(requesting_user):
                return False
            self.users[new_username] = self.users.pop(old_username)
            self._save()
        with self._sessions_lock:
            for session in self._sessions.values():
                if session.get("username") == old_username:
                    session["username"] = new_username
        self._save_sessions()
        return True

    def totp_enabled(self, username: str) -> bool:
        return bool(self.users.get(username, {}).get("totp_secret"))

    def totp_generate_secret(self, username: str) -> Optional[str]:
        if username not in self.users:
            return None
        return pyotp.random_base32()

    def totp_confirm_enable(self, username: str, code: str) -> bool:
        secret = self.users.get(username, {}).get("totp_pending_secret")
        if not secret:
            secret = self.totp_generate_secret(username)
            if not secret:
                return False
            self.users[username]["totp_pending_secret"] = secret
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            return False
        self.users[username]["totp_secret"] = secret
        self.users[username].pop("totp_pending_secret", None)
        self._save()
        return True

    def totp_disable(self, username: str, password: str) -> bool:
        if username not in self.users or not _verify_password(password, self.users[username].get("password_hash", "")):
            return False
        self.users[username].pop("totp_secret", None)
        self._save()
        return True
