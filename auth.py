import hashlib
import hmac
import os
import secrets
import base64
import json
from datetime import datetime, timedelta

MIN_SECRET_KEY_LENGTH = 32


class SecretKeyError(RuntimeError):
    """The service must not start: the signing key is missing or too short in production."""


def load_secret_key(environ) -> tuple:
    """Return (key, configured) for the token signing key.

    On Render (RENDER=true) a missing or short SECRET_KEY stops the start, so tokens can never be
    signed with a key that changes on every restart or that is easy to guess. Elsewhere a missing
    key gets a random one for this process only, as before. The value is never included in errors.
    """
    # The value is used exactly as set, so an existing deployment keeps the same signing key.
    value = environ.get("SECRET_KEY") or ""
    if (environ.get("RENDER") or "").strip().lower() == "true":
        if not value.strip():
            raise SecretKeyError("SECRET_KEY is not set on Render: the service does not start without it")
        if len(value) < MIN_SECRET_KEY_LENGTH:
            raise SecretKeyError(f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters on Render")
        return value, True
    if value.strip():
        return value, True
    return secrets.token_hex(32), False


# An empty value is treated as missing: an empty HMAC key would let anyone sign tokens.
SECRET_KEY, SECRET_KEY_CONFIGURED = load_secret_key(os.environ)

def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = os.urandom(16)
    pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + "$" + pwdhash.hex()

def verify_password(stored_password: str, provided_password: str) -> bool:
    """Verify stored password against provided password."""
    try:
        if not stored_password or "$" not in stored_password:
            return False
        salt_hex, pwdhash_hex = stored_password.split("$")
        salt = bytes.fromhex(salt_hex)
        stored_hash = bytes.fromhex(pwdhash_hex)
        pwdhash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return hmac.compare_digest(pwdhash, stored_hash)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: timedelta = timedelta(days=7)) -> str:
    """Create a signed token containing payload data and expiration."""
    payload = data.copy()
    expire = datetime.utcnow() + expires_delta
    payload["exp"] = expire.timestamp()

    payload_json = json.dumps(payload, sort_keys=True)
    payload_bytes = payload_json.encode('utf-8')
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode('utf-8')

    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        payload_b64.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return f"{payload_b64}.{signature}"

def verify_access_token(token: str) -> dict:
    """Verify signed token and return payload if valid, else None."""
    try:
        if not token or "." not in token:
            return None
        payload_b64, signature = token.split(".", 1)

        expected_signature = hmac.new(
            SECRET_KEY.encode('utf-8'),
            payload_b64.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return None

        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode('utf-8'))
        payload = json.loads(payload_bytes.decode('utf-8'))

        if payload.get("exp", 0) < datetime.utcnow().timestamp():
            return None

        return payload
    except Exception:
        return None
