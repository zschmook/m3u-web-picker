from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


def create_database_backup(
    source_db: Path,
    backup_dir: Path,
    retention_days: int = 30,
) -> Path:
    """Create and verify an online SQLite backup, then remove expired copies."""
    if not source_db.exists():
        raise FileNotFoundError(f"Database does not exist: {source_db}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    final_path = backup_dir / f"m3u_picker-{timestamp}.db"
    temporary_path = final_path.with_suffix(".db.tmp")

    try:
        with closing(sqlite3.connect(source_db, timeout=30)) as source:
            with closing(sqlite3.connect(temporary_path)) as destination:
                source.backup(destination)
                result = destination.execute("PRAGMA integrity_check").fetchone()
                if result != ("ok",):
                    raise RuntimeError(f"Backup integrity check failed: {result}")
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    cutoff = datetime.now() - timedelta(days=max(1, int(retention_days)))
    for path in backup_dir.glob("m3u_picker-????-??-??_??????.db"):
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            path.unlink(missing_ok=True)

    return final_path


def backup_from_environment(default_source: Path) -> Path:
    backup_dir = Path(os.environ.get("M3U_BACKUP_CONTAINER_DIR", "/backups"))
    retention = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))
    return create_database_backup(default_source, backup_dir, retention)
