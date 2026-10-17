"""SQLite storage for construction sites."""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import json
# Implements the storage functions

DATABASE_PATH = Path(
    os.environ.get("PLANIT_DATABASE_PATH", Path(__file__).with_name("sites.db"))
)
CATALOG_PATH = Path(__file__).with_name("data") / "eia_plants.json"
SITE_METADATA = {
    "eia_plant_id": "INTEGER", "utility": "TEXT", "county": "TEXT",
    "state": "TEXT", "plant_type": "TEXT", "source": "TEXT",
    "source_url": "TEXT", "source_period": "TEXT",
}

# Opens a database conects, commits successful changes, rolls back
# failed changes, closes connection afterwards.
# Handles storage and ID assignment
@contextmanager
def connect():
    # Each operation gets its own connection, safe for FastAPI's worker threads.
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()

# Creates the sites table if needed. Adds demo site without overwritting
def initialize_database():
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180)
            )
            """
        )
        # Preserve the existing demo site without overwriting saved data.
        connection.execute(
            """
            INSERT OR IGNORE INTO sites (id, name, latitude, longitude)
            VALUES (?, ?, ?, ?)
            """,
            (1, "Demo Job Site", 25.75, -80.70),
        )
        # Add metadata to existing installations without changing saved site IDs.
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(sites)")}
        for name, kind in SITE_METADATA.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE sites ADD COLUMN {name} {kind}")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS sites_eia_id ON sites(eia_plant_id)")
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        for plant in catalog["plants"]:
            connection.execute(
                """INSERT INTO sites
                (name, latitude, longitude, eia_plant_id, utility, county, state,
                 plant_type, source, source_url, source_period)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(eia_plant_id) DO UPDATE SET
                    name=excluded.name, latitude=excluded.latitude, longitude=excluded.longitude,
                    utility=excluded.utility, county=excluded.county, state=excluded.state,
                    plant_type=excluded.plant_type, source=excluded.source,
                    source_url=excluded.source_url, source_period=excluded.source_period""",
                (plant["name"], plant["latitude"], plant["longitude"], plant["eia_plant_id"],
                 plant["utility"], plant["county"], plant["state"], plant["plant_type"],
                 catalog["source"], catalog["source_url"], catalog["source_period"]),
            )
        connection.execute("""CREATE TABLE IF NOT EXISTS risk_snapshots (
            site_id INTEGER PRIMARY KEY,
            generated_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS provider_cache (
            cache_key TEXT PRIMARY KEY,
            expires_at REAL NOT NULL,
            payload TEXT NOT NULL
        )""")

# Retrieves all sties
def list_sites():
    with connect() as connection:
        rows = connection.execute("SELECT * FROM sites ORDER BY id").fetchall()
        return [dict(row) for row in rows]

# Retrieves one site by ID
def find_site(site_id: int):
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM sites WHERE id = ?", (site_id,)
        ).fetchone()
        return dict(row) if row is not None else None

# Saves a asite. Returns it with ID
def insert_site(name: str, latitude: float, longitude: float):
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO sites (name, latitude, longitude) VALUES (?, ?, ?)",
            (name, latitude, longitude),
        )
        row = connection.execute(
            "SELECT * FROM sites WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)


def save_risk(site_id, risk):
    with connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO risk_snapshots VALUES (?, ?, ?)",
            (site_id, risk["generated_at"], json.dumps(risk, allow_nan=False)),
        )


def load_risk(site_id):
    with connect() as connection:
        row = connection.execute("SELECT payload FROM risk_snapshots WHERE site_id = ?", (site_id,)).fetchone()
        return json.loads(row["payload"]) if row else None


def save_refreshed_risk(site_id, risk):
    import ml_risk

    with connect() as connection:
        # Read the newest ML submission after provider requests finish. Hold the
        # write lock while retaining it so a refresh cannot erase an intervening result.
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload FROM risk_snapshots WHERE site_id = ?", (site_id,)).fetchone()
        previous = json.loads(row["payload"]) if row else None
        risk["ml_assessment"] = ml_risk.retain_on_refresh(previous, risk)
        connection.execute("INSERT OR REPLACE INTO risk_snapshots VALUES (?, ?, ?)",
                           (site_id, risk["generated_at"], json.dumps(risk, allow_nan=False)))
    return risk


def load_provider_cache(key, now):
    with connect() as connection:
        row = connection.execute(
            "SELECT payload FROM provider_cache WHERE cache_key = ? AND expires_at > ?", (key, now)
        ).fetchone()
        return json.loads(row["payload"]) if row else None


def save_provider_cache(key, expires_at, payload):
    with connect() as connection:
        connection.execute("DELETE FROM provider_cache WHERE expires_at < ?", (expires_at - 86400,))
        connection.execute("INSERT OR REPLACE INTO provider_cache VALUES (?, ?, ?)",
                           (key, expires_at, json.dumps(payload, allow_nan=False)))
