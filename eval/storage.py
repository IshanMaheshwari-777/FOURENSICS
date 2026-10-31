"""Separate evaluation/demo run state from the auto-reloading live service."""
import sqlite3

from app.db import Database


def isolated_db(settings, name: str) -> Database:
    db = Database(settings.data_dir / f"{name}.sqlite3")
    for path in (settings.db_path, settings.data_dir / "evaluation.sqlite3"):
        if path == db.path or not path.exists():
            continue
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source:
            db._conn().executemany("INSERT OR IGNORE INTO llm_cache VALUES (?,?,?)",
                                  source.execute("SELECT prompt_hash, response_text, created_at FROM llm_cache"))
            db._conn().commit()
    return db
