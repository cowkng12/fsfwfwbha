import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

DEFAULT_DATABASE_URL = "sqlite:///./data/app.sqlite3"
DATABASE_FILENAME = "app.sqlite3"


def database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// DATABASE_URL is supported by the bundled repository")
    raw_path = url.removeprefix("sqlite:///")
    path = Path(raw_path)
    if url == DEFAULT_DATABASE_URL or not path.is_absolute():
        persistent_path = _persistent_database_path()
        if persistent_path and _prepare_database_parent(persistent_path):
            return persistent_path
    if not path.is_absolute():
        path = Path(__file__).parents[1] / path
    if not _prepare_database_parent(path):
        fallback_path = Path(__file__).parents[1] / "data" / DATABASE_FILENAME
        if path != fallback_path and _prepare_database_parent(fallback_path):
            return fallback_path
        raise PermissionError(f"Cannot create SQLite database directory: {path.parent}")
    return path


def database_storage_info() -> dict[str, object]:
    path = database_path()
    persistent_root = _persistent_root_for_path(path)
    render = bool(os.environ.get("RENDER"))
    is_mount = bool(persistent_root and persistent_root.exists() and os.path.ismount(persistent_root))
    persistent_disk_active = bool(persistent_root and (is_mount or _persistent_root_from_env()))
    return {
        "database_path": str(path),
        "persistent_root": str(persistent_root) if persistent_root else None,
        "persistent_disk_active": persistent_disk_active,
        "persistent_root_is_mount": is_mount,
        "render_env_detected": render,
        "database_url_is_default": get_settings().database_url == DEFAULT_DATABASE_URL,
        "database_url_is_relative": not Path(get_settings().database_url.removeprefix("sqlite:///")).is_absolute(),
        "warning": "SQLite is not using a persistent Render disk" if render and not persistent_disk_active else None,
    }


def _persistent_database_path() -> Path | None:
    explicit_file = _env_path("DATABASE_FILE") or _env_path("SQLITE_PATH")
    if explicit_file:
        return explicit_file
    root = _persistent_root_from_env()
    if root:
        return root / DATABASE_FILENAME
    render_disk = Path("/var/data")
    if render_disk.exists():
        return render_disk / DATABASE_FILENAME
    data_disk = Path("/data")
    if data_disk.exists():
        return data_disk / DATABASE_FILENAME
    return None


def _persistent_root_from_env() -> Path | None:
    for key in ("DATABASE_PERSISTENT_DIR", "SQLITE_PERSISTENT_DIR", "RENDER_DISK_PATH", "RENDER_PERSISTENT_DIR"):
        value = os.environ.get(key)
        if value:
            return Path(value)
    return None


def _persistent_root_for_path(path: Path) -> Path | None:
    roots = [_persistent_root_from_env(), Path("/var/data"), Path("/data")]
    normalized = str(path).replace("\\", "/")
    for root in [item for item in roots if item]:
        root_text = str(root).replace("\\", "/").rstrip("/")
        if normalized == root_text or normalized.startswith(f"{root_text}/"):
            return root
    return None


def _env_path(key: str) -> Path | None:
    value = os.environ.get(key)
    return Path(value) if value else None


def _prepare_database_parent(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path.parent, os.W_OK)


@contextmanager
def connect():
    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                collection_name TEXT NOT NULL,
                name TEXT NOT NULL,
                number TEXT,
                model_name TEXT,
                backdrop_name TEXT,
                symbol_name TEXT,
                image_url TEXT,
                price REAL NOT NULL,
                floor_price REAL,
                model_floor_price REAL,
                sales_count INTEGER,
                uses_count INTEGER,
                uses_total INTEGER,
                combo_listed_count INTEGER,
                combo_floor_price REAL,
                model_last_sale_at TEXT,
                model_recent_sales TEXT,
                current_owner TEXT,
                original_sender TEXT,
                original_recipient TEXT,
                original_gift_at TEXT,
                last_sale_at TEXT,
                last_sale_price REAL,
                last_sale_currency TEXT,
                initial_sale_at TEXT,
                initial_sale_price REAL,
                initial_sale_currency TEXT,
                initial_sale_stars INTEGER,
                received_at TEXT,
                export_at TEXT,
                next_resale_at TEXT,
                next_transfer_at TEXT,
                marketplace_url TEXT,
                telegram_url TEXT,
                first_seen_at TEXT,
                notified_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source, external_id)
            );

            CREATE INDEX IF NOT EXISTS idx_listings_filters
            ON listings (collection_name, backdrop_name, model_name, price);

            CREATE INDEX IF NOT EXISTS idx_listings_alert_scan
            ON listings (notified_at, updated_at, first_seen_at, price);

            CREATE INDEX IF NOT EXISTS idx_listings_updated_at
            ON listings (updated_at);

            CREATE TABLE IF NOT EXISTS research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_research_runs_created_at
            ON research_runs (created_at);

            CREATE TABLE IF NOT EXISTS notified_items (
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (source, external_id)
            );

            CREATE TABLE IF NOT EXISTS hidden_items (
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (source, external_id)
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                expires_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscription_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                currency TEXT NOT NULL,
                total_amount INTEGER NOT NULL,
                telegram_payment_charge_id TEXT,
                provider_payment_charge_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS search_preferences (
                user_id TEXT PRIMARY KEY,
                filters_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for statement in [
            "ALTER TABLE listings ADD COLUMN floor_price REAL",
            "ALTER TABLE listings ADD COLUMN model_floor_price REAL",
            "ALTER TABLE listings ADD COLUMN sales_count INTEGER",
            "ALTER TABLE listings ADD COLUMN uses_count INTEGER",
            "ALTER TABLE listings ADD COLUMN uses_total INTEGER",
            "ALTER TABLE listings ADD COLUMN combo_listed_count INTEGER",
            "ALTER TABLE listings ADD COLUMN combo_floor_price REAL",
            "ALTER TABLE listings ADD COLUMN model_last_sale_at TEXT",
            "ALTER TABLE listings ADD COLUMN model_recent_sales TEXT",
            "ALTER TABLE listings ADD COLUMN current_owner TEXT",
            "ALTER TABLE listings ADD COLUMN original_sender TEXT",
            "ALTER TABLE listings ADD COLUMN original_recipient TEXT",
            "ALTER TABLE listings ADD COLUMN original_gift_at TEXT",
            "ALTER TABLE listings ADD COLUMN last_sale_at TEXT",
            "ALTER TABLE listings ADD COLUMN last_sale_price REAL",
            "ALTER TABLE listings ADD COLUMN last_sale_currency TEXT",
            "ALTER TABLE listings ADD COLUMN initial_sale_at TEXT",
            "ALTER TABLE listings ADD COLUMN initial_sale_price REAL",
            "ALTER TABLE listings ADD COLUMN initial_sale_currency TEXT",
            "ALTER TABLE listings ADD COLUMN initial_sale_stars INTEGER",
            "ALTER TABLE listings ADD COLUMN received_at TEXT",
            "ALTER TABLE listings ADD COLUMN export_at TEXT",
            "ALTER TABLE listings ADD COLUMN next_resale_at TEXT",
            "ALTER TABLE listings ADD COLUMN next_transfer_at TEXT",
            "ALTER TABLE listings ADD COLUMN telegram_url TEXT",
            "ALTER TABLE listings ADD COLUMN first_seen_at TEXT",
            "ALTER TABLE listings ADD COLUMN notified_at TEXT",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
