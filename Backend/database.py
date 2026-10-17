"""SQLite storage for construction sites."""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
# Implements the storage functions

DATABASE_PATH = Path(
    os.environ.get("PLANIT_DATABASE_PATH", Path(__file__).with_name("sites.db"))
)

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
