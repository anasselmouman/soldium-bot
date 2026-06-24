# Multi-Provider Verification Report (Post-Completion)

Generated after completing the provider-agnostic refactor.

## 1. API_URL usage

| Location | Role |
|----------|------|
| `services/provider_registry.py:seed_default_gozibra_provider` | **Migration/seed only** — `INSERT OR IGNORE` when Gozibra env keys exist |
| `soldium-dashboard/db_schema.py:_seed_default_gozibra` | **Migration/seed only** |
| `.env.example` (bot + dashboard) | Documentation |
| `scripts/verify_smm_keys.py` | Standalone CLI (not bot runtime) |

**Runtime bot/dashboard HTTP calls** resolve `api_base_url` from `providers.api_base_url` via `provider_registry.get_provider_record()` → `SMMManager(api_url=...)`.

No bare `SMMManager()` in bot handlers or `main.py`.

## 2. Multi-provider without `.env` API_URL

**Yes**, when providers are registered in DB with non-empty `api_base_url`:

| Flow | Resolution path |
|------|-----------------|
| Order creation | `get_provider_credentials_for_service` → `resolve_service_route` → `get_manager(slug, account)` |
| Order status | `smm_manager_for_order(order)` → `get_manager(order.provider_slug, order.api_account)` |
| Price sync | `fetch_provider_catalogs` → `smm_manager_for_account(account, slug)` per active pair |

`.env` `API_URL` is only read during optional Gozibra seed (`INSERT OR IGNORE`).

## 3. provider_registry coverage

All bot runtime provider operations use `services/provider_registry.py` (directly or via `services/smm_api_router.py`):

- Order submit, credentials, status polling
- Startup verification (`services/provider_ops.verify_providers_at_startup`)
- Admin balances (`fetch_all_provider_balances`)
- Price sync fetch
- Limits cache refresh (`refresh_all_provider_catalogs`)

**Removed bypasses:** `main.py`, `handlers/orders.py`, `handlers/admin.py`, `handlers/start.py` no longer instantiate `SMMManager()` without slug/account.

Dashboard: `services/smm_provider.py` + `services/provider_registry.py`.

## 4. Service identity

**Canonical identity:** `(provider_slug, external_service_id)`

**Bot catalog identity:** `catalog_id` (PK) — exposed as `item["id"]` and `item["catalog_id"]`

Schema (`database._migrate_smm_services_catalog_identity`):

- `catalog_id TEXT PRIMARY KEY`
- `external_service_id TEXT NOT NULL`
- `UNIQUE(provider_slug, external_service_id)`
- `service_id` retained as mirror of `external_service_id` for backward compatibility

Lookups:

- User catalog navigation: `find_service_location(catalog_id)` via `local_item_id` / `id`
- Provider limits: `get_provider_limits_from_db(provider_slug, external_service_id)`
- Price sync UPDATE: `WHERE provider_slug = ? AND external_service_id = ?`
- Limits cache key: `(provider_slug, external_service_id)`

**Cross-provider ID collision:** supported — test `test_same_external_id_different_providers`.

## 5. provider_price_sync failure isolation

- Per `(provider_slug, account)` fetch: `try/except` + `continue` — other providers proceed
- **Removed** cross-provider fallback in `_lookup_provider_entry`
- Row updates scoped to matching `provider_slug` only

## 6. Same external service_id across providers

| Area | Safe? |
|------|-------|
| Catalog storage | Yes — `UNIQUE(provider_slug, external_service_id)` |
| Service lookup | Yes — by `catalog_id` |
| Order creation | Yes — uses service's `provider_slug` + `external_service_id` |
| Order tracking | Yes — `orders.provider_slug` + `orders.api_account` |
| Price sync | Yes — provider-scoped UPDATE |
| Limits cache | Yes — `(provider_slug, id)` key |

## 7. Remaining hardcoded references

| Item | Status |
|------|--------|
| `LEGACY_GOZIBRA_SLUG = "gozibra"` | Seed/migration label only |
| `get_default_provider_slug()` | First active provider in DB — not hardcoded at runtime |
| `infer_account_from_text` keyword router | Fallback when `provider_api_account` unset (account only, not provider slug) |
| `orders.provider_slug DEFAULT 'gozibra'` | Legacy column default for old rows |
| Marketing copy mentioning Gozibra | UI text only (`utils/notices.py`) |

No runtime routing to Gozibra unless it is the configured/default active provider in DB.

## 8. Gozibra absent — other provider only

**Supported** when:

1. Active row in `providers` with `api_base_url`
2. Active row(s) in `provider_accounts` with `api_key_env` pointing to set `.env` vars
3. `smm_services` rows use `provider_slug` of the new provider
4. `.env` contains keys referenced by `provider_accounts`

Bot starts without requiring `SMM_KEY_*` at import (`config.py` — keys optional).

Gozibra seed skipped when no Gozibra keys in `.env`.

## 9. API key storage

- `provider_accounts.api_key_env` stores **env variable name only**
- `providers` has no key columns
- `resolve_api_key()` reads `os.environ[api_key_env]` at runtime
- Raw secrets never written to `users.db`

## 10. Architecture assessment

### Fully multi-provider ready

- DB schema: `providers`, `provider_accounts`, composite service identity
- `provider_registry` — single resolution layer
- Order pipeline (submit + track)
- Price sync (isolated per provider)
- Limits (provider-scoped cache + DB)
- Startup health + admin balances
- Dashboard provider client + manual order submit

### Backward compatibility preserved

- Existing Gozibra services: `provider_slug='gozibra'`, `catalog_id` = former `local_item_id`/`service_id`
- Existing orders: `provider_slug` column defaults/migration
- `service_id` column kept as mirror of `external_service_id`

### Optional future work (non-blocking)

- Admin UI to manage providers/accounts without SQL
- Protocol adapters beyond `gozibra_v2` string
- `verify_smm_keys.py` CLI refactor to use registry
- Dashboard catalog UI fields for `provider_slug` on create/edit

## Test results

- **188 passed** (bot test suite, excluding 2 pre-existing unrelated failures)
- New: `test_same_external_id_different_providers`
