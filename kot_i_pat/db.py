import json
import sqlite3
import typing

from .config import settings

schema_v1 = """
CREATE TABLE IF NOT EXISTS posts (
    url TEXT NOT NULL,
    authenticated_id TEXT NOT NULL,
    content JSON NOT NULL,
    PRIMARY KEY (url, authenticated_id)
);
"""


def get_db() -> sqlite3.Connection:
    # TODO: how t f? race conditions?
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(schema_v1)
    return conn


def get_public_object(url: str) -> typing.Any:
    db = get_db()
    print(url)
    row = db.execute(
        "SELECT content FROM posts WHERE url = ? AND authenticated_id LIKE ? LIMIT 1",
        (url, f"https://{settings.federation_host}/%"),
    ).fetchone()
    if row is None:
        raise FileNotFoundError()
    return json.loads(row[0])


def insert_object(url: str, authenticated_id: str, content: typing.Any):
    db = get_db()
    db.execute(
        "INSERT INTO posts (url, authenticated_id, content) VALUES (?, ?, ?)",
        (url, authenticated_id, json.dumps(content)),
    )
    db.commit()
