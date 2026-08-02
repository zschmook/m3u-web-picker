#!/usr/bin/env python3
from backup import backup_from_environment
from core import DB_PATH, db_connect


def main() -> None:
    # Ensure the database and tables exist before the first backup.
    conn = db_connect()
    conn.close()
    path = backup_from_environment(DB_PATH)
    print(f"Created verified backup: {path}")


if __name__ == "__main__":
    main()
