"""Export business data to a Microsoft Access ``.mdb`` file.

This is an *additional export* so the data can be opened in Access. It is NOT a PostgreSQL backup
and cannot be restored by the app.

Windows requirement: the **Microsoft Access Database Engine (x64)** - installed with Microsoft
Office, or free as the "Access Database Engine 2016 Redistributable" - so that the 64-bit ODBC
driver "Microsoft Access Driver (*.mdb, *.accdb)" and the ACE OLE DB provider exist.

How it is built: ADOX (via PowerShell) creates an empty Jet-4 ``.mdb``; tables and rows are then
written through ODBC; relationships (foreign keys) are added after loading so orphan data is detected;
finally the file is re-opened and every table's row count (and money totals) is checked against
PostgreSQL. Success is reported only if that verification passes.

Type mapping (Access/Jet limits): UUID -> TEXT(36); long text/JSON -> MEMO; money -> CURRENCY
(exact fixed-point, 4 decimals); timestamps -> DATETIME in UTC; booleans -> YESNO; bigint -> DOUBLE.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
import subprocess
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text, Uuid, select
from sqlalchemy.orm import Session

from database.models import Base
from services.errors import BusinessRuleError

log = logging.getLogger(__name__)
DRIVER = "Microsoft Access Driver (*.mdb, *.accdb)"
PROVIDERS = ("Microsoft.ACE.OLEDB.16.0", "Microsoft.ACE.OLEDB.12.0")

# Dependency order. Credentials (owners) and internal sync state are deliberately not exported.
EXPORT_TABLES = ["membership_plans", "members", "trainers", "subscriptions", "payments", "attendance",
                 "trainer_attendance", "expenses", "gym_settings", "whatsapp_logs", "backup_history", "audit_logs"]


class MdbUnavailableError(BusinessRuleError):
    """The Access driver / provider is not installed."""


class MdbExportError(BusinessRuleError):
    pass


@dataclass(frozen=True)
class MdbExportResult:
    path: Path
    table_counts: dict[str, int]
    relationships: int
    size_bytes: int


def check_available() -> None:
    import pyodbc

    pyodbc.pooling = False  # pooled connections keep the .mdb (and its .ldb lock file) open

    if DRIVER not in pyodbc.drivers():
        raise MdbUnavailableError(
            f"The Access ODBC driver '{DRIVER}' is not installed (64-bit). Install the 'Microsoft Access "
            "Database Engine 2016 Redistributable (x64)' from Microsoft, then try again.")


def _create_empty_mdb(path: Path) -> None:
    script = (
        "$ErrorActionPreference='Stop';$ok=$false;"
        f"foreach($p in '{PROVIDERS[0]}','{PROVIDERS[1]}'){{try{{$c=New-Object -ComObject ADOX.Catalog;"
        f"$null=$c.Create(\"Provider=$p;Data Source={path};Jet OLEDB:Engine Type=5\");$ok=$true;break}}catch{{}}}};"
        "if(-not $ok){exit 3}"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=120,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0 or not path.is_file():
        raise MdbUnavailableError(
            "Could not create an Access database. The 'Microsoft Access Database Engine (x64)' "
            "(ACE OLE DB provider) must be installed.")


def _access_type(col) -> str:
    t = col.type
    if isinstance(t, Uuid):
        return "TEXT(36)"
    if isinstance(t, Boolean):
        return "YESNO"
    if isinstance(t, Numeric):
        return "CURRENCY"
    if isinstance(t, BigInteger):
        return "DOUBLE"
    if isinstance(t, Integer):
        return "LONG"
    if isinstance(t, (Date, DateTime)):
        return "DATETIME"
    if isinstance(t, String) and not isinstance(t, Text) and t.length and t.length <= 255:
        return f"TEXT({t.length})"
    return "MEMO"  # Text, long strings and JSON


def _to_access(value, col):
    if value is None:
        return None
    t = col.type
    if isinstance(t, Uuid):
        return str(value)
    if isinstance(t, DateTime):
        return value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    if isinstance(t, Date):
        return datetime(value.year, value.month, value.day)
    if isinstance(t, Numeric):
        return Decimal(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


@contextmanager
def connect(path: Path, **kwargs):
    """pyodbc's own ``with`` only commits; this really closes so the .mdb is released."""
    import pyodbc

    conn = pyodbc.connect(f"Driver={{{DRIVER}}};DBQ={path};", **kwargs)
    try:
        yield conn
    finally:
        conn.close()


def _q(name: str) -> str:
    return f"[{name}]"


def _table_rows(session: Session, table) -> list[tuple]:
    rows = list(session.execute(select(table)).all())
    if table.name == "gym_settings":  # machine-specific settings stay out of the export
        key_idx = table.c.keys().index("key")
        rows = [r for r in rows if not str(r[key_idx] or "").startswith("local.")]
    return rows


def _create_table_sql(table) -> str:
    cols = []
    for col in table.columns:
        null = "" if col.nullable and not col.primary_key else " NOT NULL"
        cols.append(f"{_q(col.name)} {_access_type(col)}{null}")
    pk = ", ".join(_q(c.name) for c in table.primary_key.columns)
    return f"CREATE TABLE {_q(table.name)} ({', '.join(cols)}, CONSTRAINT {_q('pk_' + table.name)} PRIMARY KEY ({pk}))"


def export_to_mdb(session: Session, dest: Path) -> MdbExportResult:
    """Write ``dest`` (overwriting). On any failure the partial file is removed."""
    import pyodbc

    check_available()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f".{dest.stem}.{uuid.uuid4().hex[:6]}.partial.mdb")
    try:
        _create_empty_mdb(partial)
        tables = [Base.metadata.tables[n] for n in EXPORT_TABLES]
        expected: dict[str, int] = {}
        money_totals: dict[tuple[str, str], Decimal] = {}
        with connect(partial, autocommit=False) as conn:
            cur = conn.cursor()
            for table in tables:
                cur.execute(_create_table_sql(table))
            conn.commit()
            for table in tables:
                rows = _table_rows(session, table)
                expected[table.name] = len(rows)
                money_cols = [c for c in table.columns if isinstance(c.type, Numeric)]
                for c in money_cols:
                    idx = table.c.keys().index(c.name)
                    money_totals[(table.name, c.name)] = sum((Decimal(r[idx] or 0) for r in rows), Decimal("0"))
                if not rows:
                    continue
                cols = list(table.columns)
                sql = (f"INSERT INTO {_q(table.name)} ({', '.join(_q(c.name) for c in cols)}) "
                       f"VALUES ({', '.join('?' for _ in cols)})")
                cur.executemany(sql, [[_to_access(v, c) for v, c in zip(row, cols)] for row in rows])
                conn.commit()
            exported = set(EXPORT_TABLES)
            fk_count = 0
            for table in tables:
                for fk in table.foreign_key_constraints:
                    if fk.referred_table.name not in exported:
                        continue
                    local = ", ".join(_q(c.name) for c in fk.columns)
                    remote = ", ".join(_q(e.column.name) for e in fk.elements)
                    try:
                        cur.execute(f"ALTER TABLE {_q(table.name)} ADD CONSTRAINT {_q(fk.name)} FOREIGN KEY "
                                    f"({local}) REFERENCES {_q(fk.referred_table.name)} ({remote})")
                    except pyodbc.Error as exc:
                        raise MdbExportError(
                            f"Relationship {table.name}.{local} -> {fk.referred_table.name} could not be created "
                            f"(orphaned data?): {exc}") from exc
                    fk_count += 1
            conn.commit()
        _verify(partial, expected, money_totals)
        dest.unlink(missing_ok=True)
        partial.replace(dest)
        return MdbExportResult(dest, expected, fk_count, dest.stat().st_size)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        for leftover in partial.parent.glob(f".{dest.stem}.*.ldb"):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:  # still held by the driver; Access removes it when the last handle closes
                log.debug("Could not remove lock file %s", leftover)


def _verify(path: Path, expected: dict[str, int], money_totals: dict[tuple[str, str], Decimal]) -> None:
    """Re-open the finished file and compare it with what PostgreSQL said we exported."""
    import pyodbc

    try:
        ctx = connect(path, readonly=True)
        conn = ctx.__enter__()
    except pyodbc.Error as exc:
        raise MdbExportError(f"The exported .mdb cannot be opened: {exc}") from exc
    try:
        cur = conn.cursor()
        for name, count in expected.items():
            actual = cur.execute(f"SELECT COUNT(*) FROM {_q(name)}").fetchone()[0]
            if actual != count:
                raise MdbExportError(f"Verification failed: {name} has {actual} rows, expected {count}.")
        for (name, column), total in money_totals.items():
            actual = Decimal(cur.execute(f"SELECT SUM({_q(column)}) FROM {_q(name)}").fetchone()[0] or 0)
            if actual.quantize(Decimal("0.01")) != total.quantize(Decimal("0.01")):
                raise MdbExportError(f"Verification failed: {name}.{column} totals {actual}, expected {total}.")
    finally:
        ctx.__exit__(None, None, None)
