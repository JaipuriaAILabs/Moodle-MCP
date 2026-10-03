"""Persistent OAuth state, so DEPLOYS NO LONGER LOG EVERYONE OUT.

FastMCP's OAuthProxy keeps all its OAuth state — dynamic client registrations,
JTI→upstream-token mappings, refresh-token metadata — in a pluggable
`client_storage` (AsyncKeyValue). Its default is a DiskStore under the app
home, which is EPHEMERAL on Render: every deploy wiped it, invalidating every
issued token and forcing all signed-in faculty back through Google.

This module provides a Supabase-backed store (table: mcp_oauth_kv) and wraps
it in the same FernetEncryptionWrapper FastMCP uses for its own default.
OAuth state uses a dedicated database role and an independent
OAUTH_STORAGE_ENCRYPTION_KEY, so rotating access-token signing keys does not
destroy sessions and a data-reader compromise cannot mutate OAuth state.

The dedicated ``mcp_oauth_writer`` role has CRUD on this one operational table
and no access to student data (enforced by grants + RLS).
"""
import logging
import random

import httpx
from key_value.aio.stores.base import BaseStore
# py-key-value-aio 0.4 (required by FastMCP 3) made its utility package async-local.
# Keep a fallback solely so an in-place rolling deploy can import during dependency
# replacement; the production pin resolves the first branch.
try:
    from key_value.aio._utils.managed_entry import ManagedEntry
    from key_value.aio._utils.serialization import BasicSerializationAdapter
except ImportError:  # pragma: no cover - FastMCP 2 rolling-upgrade compatibility
    from key_value.shared.utils.managed_entry import ManagedEntry
    from key_value.shared.utils.serialization import BasicSerializationAdapter
from typing_extensions import override

log = logging.getLogger("moodle-mcp.oauth-storage")

_TABLE = "mcp_oauth_kv"


class SupabaseKVStore(BaseStore):
    """AsyncKeyValue over one Supabase (PostgREST) table. Async httpx client —
    never blocks the event loop. Values are stored as opaque text (the caller
    wraps this store in encryption); expiry rides in a queryable column so the
    base class's TTL semantics survive restarts too."""

    def __init__(self, *, url: str, apikey: str, bearer: str,
                 default_collection: str | None = None):
        self._rest = f"{url.rstrip('/')}/rest/v1/{_TABLE}"
        self._headers = {"apikey": apikey, "Authorization": f"Bearer {bearer}",
                         "Content-Type": "application/json"}
        self._http = httpx.AsyncClient(timeout=10.0)
        self._adapter = BasicSerializationAdapter(date_format="isoformat",
                                                  value_format="dict")
        super().__init__(default_collection=default_collection,
                         serialization_adapter=self._adapter, stable_api=True)

    @override
    async def _get_managed_entry(self, *, key: str, collection: str) -> ManagedEntry | None:
        r = await self._http.get(
            self._rest, headers=self._headers,
            params={"collection": f"eq.{collection}", "key": f"eq.{key}",
                    "select": "value", "limit": "1"})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        try:
            return self._adapter.load_json(json_str=rows[0]["value"])
        except Exception:  # noqa: BLE001 — a corrupt row reads as a miss, never a crash
            log.warning("undecodable oauth-kv row (collection=%s) — treating as miss",
                        collection)
            return None

    @override
    async def _put_managed_entry(self, *, key: str, collection: str,
                                 managed_entry: ManagedEntry) -> None:
        payload = {"collection": collection, "key": key,
                   "value": self._adapter.dump_json(entry=managed_entry, key=key,
                                                    collection=collection),
                   "expires_at": managed_entry.expires_at_isoformat,
                   "updated_at": "now()"}
        r = await self._http.post(
            self._rest, headers={**self._headers,
                                 "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "collection,key"}, json=[payload])
        r.raise_for_status()
        if random.random() < 0.02:  # opportunistic purge of expired rows (~1 in 50 writes)
            try:
                await self._http.delete(
                    self._rest, headers=self._headers,
                    params={"expires_at": "lt.now()"})
            except httpx.HTTPError:  # purge is hygiene, never a failure path
                pass

    @override
    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        r = await self._http.delete(
            self._rest, headers={**self._headers, "Prefer": "return=representation"},
            params={"collection": f"eq.{collection}", "key": f"eq.{key}"})
        r.raise_for_status()
        return bool(r.json())


_ENCRYPTION_SALT = "moodle-mcp-oauth-storage-encryption-v1"


def oauth_fernet(encryption_material: str):
    """Fernet (single key) or MultiFernet (keyset) for OAuth-storage encryption.

    OAUTH_STORAGE_ENCRYPTION_KEY may be a COMMA-SEPARATED keyset to make key
    rotation zero-downtime: values are encrypted with the FIRST (current) key and
    decrypted by trying each key in turn (MultiFernet). To rotate, put the new key
    first and keep the previous one for a grace period — already-registered clients
    and issued tokens keep decrypting, so nobody is logged out. A single value
    derives exactly the same Fernet as before, so existing rows stay readable.

    Returns None when no material is configured. Each key is derived with the same
    KDF + fixed salt as the original single-key build, so a value that produced a
    working key before produces the identical key now.
    """
    from cryptography.fernet import Fernet, MultiFernet
    from fastmcp.server.auth.jwt_issuer import derive_jwt_key

    materials = [m.strip() for m in (encryption_material or "").split(",") if m.strip()]
    if not materials:
        return None
    fernets = [Fernet(key=derive_jwt_key(low_entropy_material=m, salt=_ENCRYPTION_SALT))
               for m in materials]
    return fernets[0] if len(fernets) == 1 else MultiFernet(fernets)


def build_oauth_storage(settings):
    """Build the encrypted store only when both dedicated credentials exist.

    There is intentionally no fallback to the student-data key or OAuth JWT
    signing key: those credentials have different rotation and blast-radius
    requirements.
    """
    storage_key = settings.oauth_storage_key()
    encryption_material = settings.oauth_storage_encryption_key
    if not (settings.supabase_url and storage_key and encryption_material):
        if settings.oauth_enabled():
            log.warning("OAuth persistence disabled until SUPABASE_OAUTH_STORAGE_KEY and "
                        "OAUTH_STORAGE_ENCRYPTION_KEY are configured")
        return None
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    fernet = oauth_fernet(encryption_material)
    # Count real (non-empty) keys, mirroring oauth_fernet's own parsing — so a trailing
    # comma ("a,") doesn't misreport "2-key set" when only one Fernet is built.
    key_count = len([m for m in encryption_material.split(",") if m.strip()])
    if key_count > 1:
        log.info("OAuth storage: %d-key set — rotation-tolerant decryption enabled", key_count)
    store = SupabaseKVStore(
        url=settings.supabase_url,
        apikey=settings.supabase_anon_key or settings.oauth_storage_key(),
        bearer=settings.oauth_storage_key())
    # raise_on_decryption_error=False: a row written under a key no longer in the set
    # (e.g. after a hard key change with no grace key) reads as a MISS, so the client
    # cleanly re-registers instead of the OAuth endpoint failing on a phantom row.
    return FernetEncryptionWrapper(key_value=store, fernet=fernet,
                                   raise_on_decryption_error=False)
