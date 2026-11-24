"""Offline SQLite ledger backup/recovery. Stop ALL integration writers first.
Run inside the integration container with a private backup directory mounted.
Restore only alongside the matching ERP snapshot; reconcile before enabling writes.
"""
import argparse
import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def copy_db(source, target):
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Ledger integrity check failed")

def snapshot(source, destination):
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    db = destination / "approvals.sqlite3"
    copy_db(source, db)
    (destination / "SHA256").write_text(digest(db) + "\n")
    print("Ledger snapshot created:", destination)

def restore(source, target):
    db = source / "approvals.sqlite3"
    if digest(db) != (source / "SHA256").read_text().strip():
        raise RuntimeError("Ledger checksum mismatch")
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.lexists(str(target) + suffix):
            raise FileExistsError(str(target) + suffix)
    fd, name = tempfile.mkstemp(prefix=".ledger-restore-", dir=target.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        copy_db(db, tmp)
        # Atomic publication without replacing an existing ledger.
        os.link(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    print("Ledger restored. Keep mutations disabled pending reconciliation.")

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--writers-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if args.operation == "backup":
        snapshot(args.source, args.destination)
    else:
        restore(args.source, args.destination)

if __name__ == "__main__":
    main()
