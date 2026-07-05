import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings


def database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// DATABASE_URL is supported by the bundled repository")
    path = Path(url.removeprefix("sqlite:///"))
    if not path.is_absolute():
        path = Path(__file__).parents[1] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
                marketplace_url TEXT,
                first_seen_at TEXT,
                notified_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source, external_id)
            );

            CREATE INDEX IF NOT EXISTS idx_listings_filters
            ON listings (collection_name, backdrop_name, model_name, price);

            CREATE TABLE IF NOT EXISTS research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        for statement in [
            "ALTER TABLE listings ADD COLUMN floor_price REAL",
            "ALTER TABLE listings ADD COLUMN model_floor_price REAL",
            "ALTER TABLE listings ADD COLUMN first_seen_at TEXT",
            "ALTER TABLE listings ADD COLUMN notified_at TEXT",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
