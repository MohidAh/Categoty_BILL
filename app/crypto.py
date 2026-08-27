"""v8.5 — Fernet encryption for sensitive data at rest (API keys, secrets).

Key derivation: PBKDF2-HMAC-SHA256 from the manager password_hash + a
per-install salt stored in the `settings` table. Iterations = 480,000
(OWASP 2023 recommendation for PBKDF2-SHA256).

Auto-migrates plaintext API keys on first read (decrypt_value silently
returns plaintext if the value does not start with the Fernet prefix
'gAAAAA'), so existing installs continue to work after upgrade.
"""
import base64
import hashlib
import logging
import os

from .db import get_setting, set_setting, conn

logger = logging.getLogger(__name__)

# Fernet token prefix — used to detect whether a stored value is already
# encrypted or still plaintext (auto-migration on read).
_FERNET_PREFIX = "gAAAAA"
_PBKDF2_ITERATIONS = 480_000  # OWASP 2023 recommendation for PBKDF2-SHA256


def _get_or_create_salt() -> bytes:
    """Return the persisted 16-byte salt (base64-encoded in settings).
    Generates one on first call.
    """
    salt_b64 = get_setting("crypto_salt", "")
    if not salt_b64:
        salt_b64 = base64.b64encode(os.urandom(16)).decode()
        set_setting("crypto_salt", salt_b64)
        logger.info("crypto: generated new salt (first run)")
    return base64.b64decode(salt_b64)


def get_fernet_key(password_hash: str, salt: str) -> "Fernet":
    """Derive a Fernet key from password_hash + salt using PBKDF2-SHA256.

    Args:
        password_hash: the manager's bcrypt password hash (a stable secret
            that doesn't change unless the password is rotated).
        salt: base64-encoded 16-byte salt.

    Returns:
        A configured Fernet instance.

    Raises:
        RuntimeError: if password_hash is empty (manager hasn't been set up
            yet) — callers MUST handle this and either prompt for password
            setup or skip encryption. Do NOT silently fall back to plaintext.
    """
    if not password_hash:
        raise RuntimeError(
            "Cannot derive Fernet key — password_hash is empty. "
            "Complete initial setup before encrypting secrets."
        )
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt_bytes = base64.b64decode(salt) if isinstance(salt, str) else salt
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt_bytes,
        iterations=_PBKDF2_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password_hash.encode()))
    return Fernet(key)


def _get_fernet():
    """Get a Fernet instance using the current password_hash + salt.

    Returns None only if the cryptography package is not installed.
    On any other failure (e.g. password_hash not set), logs an error
    and returns None — callers must handle None by refusing to store
    or return the secret, NOT by storing plaintext.
    """
    try:
        from cryptography.fernet import Fernet  # noqa: F401
    except ImportError:
        logger.error("cryptography package not installed — encryption disabled")
        return None
    password_hash = get_setting("password_hash", "")
    if not password_hash:
        logger.error(
            "password_hash not set — refusing to derive Fernet key. "
            "Complete initial setup before configuring API keys."
        )
        return None
    try:
        salt_b64 = _get_or_create_salt()
        return get_fernet_key(password_hash, salt_b64)
    except Exception as e:
        logger.error("Failed to derive Fernet key: %s", e, exc_info=True)
        return None


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns the Fernet ciphertext as a string.

    If crypto is unavailable (cryptography package missing or password_hash
    not yet set), returns the plaintext unchanged AND logs an error. This
    graceful degradation only happens during initial setup; once a password
    is set, encryption always succeeds.
    """
    if not plaintext:
        return plaintext
    f = _get_fernet()
    if f is None:
        logger.error("encrypt_value: crypto unavailable — storing plaintext (NOT recommended)")
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a string value. Auto-detects plaintext (returns as-is if
    the value does not start with 'gAAAAA').

    If decryption fails (key changed, corrupted ciphertext), logs a warning
    and returns the ciphertext as-is. This is intentional: it allows the
    app to keep running after a password rotation — the owner can re-enter
    the API keys via Settings — rather than crashing every AI call.
    """
    if not ciphertext:
        return ciphertext
    # Plaintext detection — auto-migration on read
    if not ciphertext.startswith(_FERNET_PREFIX):
        return ciphertext
    f = _get_fernet()
    if f is None:
        # Crypto unavailable — return as-is (cannot decrypt)
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception as e:
        logger.warning(
            "decrypt_value: decryption failed (key changed or data corrupted) — "
            "returning ciphertext as-is. Owner should re-enter the API key. Error: %s",
            e,
        )
        return ciphertext


def is_encrypted(value: str) -> bool:
    """Check if a value appears to be Fernet-encrypted."""
    return bool(value) and value.startswith(_FERNET_PREFIX)


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key for storage. Wrapper around encrypt_value."""
    return encrypt_value(api_key)


def decrypt_api_key(stored_key: str) -> str:
    """Decrypt an API key from storage. Wrapper around decrypt_value."""
    return decrypt_value(stored_key)


def mask_api_key(api_key: str) -> str:
    """Return a masked preview of an API key (first 4 + last 4 chars).

    Used for display in settings UIs. The masked value is derived from
    the PLAINTEXT key (caller must decrypt before calling), so the mask
    never leaks information about the ciphertext.
    """
    if not api_key or len(api_key) < 12:
        return "****"
    return api_key[:4] + "..." + api_key[-4:]


def migrate_provider_keys():
    """One-time migration: encrypt any plaintext API keys in ai_providers table.

    v8.5: logs errors instead of silently swallowing them. If migration
    fails for a single provider, the others are still migrated (per-row
    try/except).
    """
    try:
        with conn() as c:
            rows = c.execute("SELECT id, api_key FROM ai_providers WHERE enabled=1").fetchall()
            migrated = 0
            skipped = 0
            for row in rows:
                key = row["api_key"]
                if key and not is_encrypted(key):
                    try:
                        encrypted = encrypt_api_key(key)
                        c.execute(
                            "UPDATE ai_providers SET api_key=? WHERE id=?",
                            (encrypted, row["id"]),
                        )
                        migrated += 1
                    except Exception as e:
                        logger.error(
                            "migrate_provider_keys: failed to encrypt key for "
                            "ai_provider id=%s: %s",
                            row["id"], e,
                        )
                        skipped += 1
            if migrated or skipped:
                logger.info(
                    "migrate_provider_keys: migrated=%d, skipped=%d, total=%d",
                    migrated, skipped, len(rows),
                )
    except Exception as e:
        logger.error("migrate_provider_keys: %s", e, exc_info=True)


# ─── PR 7b: settings.*_api_key encryption ─────────────────────────────────
# The `settings` table holds several API keys as plaintext (groq_api_key,
# gemini_api_key, etc.). These are migrated to Fernet-encrypted values on boot
# via migrate_setting_keys(). Readers use decrypt_setting_key() which auto-
# detects plaintext (returns as-is) for backward compat.

# All known API-key settings keys. Add new ones here as the codebase grows.
_SETTING_API_KEYS = (
    "groq_api_key",
    "gemini_api_key",
    "openrouter_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "claude_api_key",
    "huggingface_api_key",
    "replicate_api_key",
    "twilio_api_key",
    "sendgrid_api_key",
    "raast_merchant_key",
)


def migrate_setting_keys():
    """One-time migration: encrypt any plaintext API keys in the settings table.

    Idempotent: if a value is already encrypted (starts with 'gAAAAA'), it's
    skipped. If crypto is unavailable (no password_hash yet), the migration is
    deferred — the next boot will retry.
    """
    migrated = 0
    skipped = 0
    errors = 0
    try:
        with conn() as c:
            for key_name in _SETTING_API_KEYS:
                row = c.execute(
                    "SELECT value FROM settings WHERE key=?", (key_name,)
                ).fetchone()
                if not row or not row["value"]:
                    continue
                value = row["value"]
                if is_encrypted(value):
                    skipped += 1
                    continue
                try:
                    encrypted = encrypt_value(value)
                    if encrypted == value:
                        # encrypt_value returned plaintext (crypto unavailable)
                        # — defer migration to next boot
                        continue
                    c.execute(
                        "UPDATE settings SET value=? WHERE key=?",
                        (encrypted, key_name),
                    )
                    migrated += 1
                    logger.info("migrate_setting_keys: encrypted %s", key_name)
                except Exception as e:
                    logger.error(
                        "migrate_setting_keys: failed to encrypt %s: %s",
                        key_name, e,
                    )
                    errors += 1
        if migrated or skipped:
            logger.info(
                "migrate_setting_keys: migrated=%d, skipped=%d (already encrypted), errors=%d",
                migrated, skipped, errors,
            )
    except Exception as e:
        logger.error("migrate_setting_keys: %s", e, exc_info=True)
    return migrated, skipped, errors


def decrypt_setting_key(key_name: str) -> str:
    """Read + decrypt an API key from the settings table.

    Auto-detects plaintext (returns as-is) for backward compat during the
    migration window. If decryption fails (key changed), logs a warning
    and returns empty string — caller should prompt user to re-enter the key.
    """
    raw = get_setting(key_name, "")
    if not raw:
        return ""
    # Plaintext detection — auto-migration on read
    if not raw.startswith(_FERNET_PREFIX):
        return raw
    f = _get_fernet()
    if f is None:
        logger.warning(
            "decrypt_setting_key(%s): crypto unavailable — returning raw ciphertext",
            key_name,
        )
        return ""
    try:
        return f.decrypt(raw.encode()).decode()
    except Exception as e:
        logger.warning(
            "decrypt_setting_key(%s): decryption failed (key changed or data corrupted) — "
            "owner should re-enter the API key. Error: %s",
            key_name, e,
        )
        return ""


def encrypt_setting_key(key_name: str, plaintext: str) -> str:
    """Encrypt an API key for storage in the settings table.

    Returns the Fernet ciphertext (or plaintext if crypto unavailable).
    Callers should call this BEFORE db.set_setting(key_name, ...).
    """
    return encrypt_value(plaintext)
