"""Drop all tables from the SQLite database.

Usage: uv run python -m scripts.clean_db
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.config import settings


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
        tables = [row[0] for row in result]

        if not tables:
            print("No tables found.")
        else:
            await conn.execute(text("PRAGMA foreign_keys=OFF"))
            for t in tables:
                await conn.execute(text(f'DROP TABLE IF EXISTS "{t}"'))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            print(f"Dropped {len(tables)} table(s): {', '.join(tables)}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
