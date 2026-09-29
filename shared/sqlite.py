"""Utilitários SQLite comuns.

Cada serviço usa estas funções apenas com o SEU banco (`orders.db` ou
`inventory.db`); nenhum banco é compartilhado entre serviços (RNF-003).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_SECONDS = 5.0


def connect(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """`check_same_thread=False` só quando a conexão passa de uma thread a outra sem uso
    simultâneo (dependência do FastAPI, criada numa thread do pool e usada em outra)."""
    connection = sqlite3.connect(
        path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None, check_same_thread=check_same_thread
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_database(path: Path, schema: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        connection.executescript(schema)
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transação de escrita: adquire o lock no início e faz rollback em qualquer erro."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
