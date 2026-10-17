"""Refresh every monitored site, including frontend-created custom sites."""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
import risk

async def main():
    database.initialize_database()
    sites = database.list_sites()
    results = await risk.refresh_many(sites)
    print(f"Refreshed {len(results)} monitored sites (including custom sites).")

if __name__ == "__main__":
    asyncio.run(main())
