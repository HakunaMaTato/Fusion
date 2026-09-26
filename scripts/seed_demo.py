"""Fill the configured database with demo tokens so the dashboard can be tried without credits.

Usage: python scripts/seed_demo.py [database-url]   (defaults to DATABASE_URL)
The heartbeat is only fresh for five minutes, so re-run it to see a healthy status page.
"""

import sys
from datetime import UTC, datetime

from app.config import get_settings
from app.demo import seed_demo
from app.storage.db import init_db, make_engine, make_session_factory


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else get_settings().database_url
    engine = make_engine(url)
    init_db(engine)
    with make_session_factory(engine)() as session:
        seed_demo(session, datetime.now(UTC))
    print(f"Seeded demo data into {url.split('///')[-1]}")


if __name__ == "__main__":
    main()
