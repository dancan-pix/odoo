#!/usr/bin/env bash
# Retain newest N verified backups per database; dry-run unless --apply.
set -euo pipefail
umask 077
[[ $# == 3 || $# == 4 ]] || { echo "Usage: $0 ABSOLUTE_BACKUP_ROOT DATABASE KEEP [--apply]" >&2; exit 2; }
root="$1"
db="$2"
keep="$3"
mode="${4:-}"
[[ "$root" == /* && -d "$root" && ! -L "$root" && "$(stat -c %a "$root")" == 700 ]] || exit 2
[[ "$db" =~ ^[a-zA-Z][a-zA-Z0-9_]{0,62}$ && "$keep" =~ ^[1-9][0-9]{0,3}$ ]] || exit 2
[[ -z "$mode" || "$mode" == --apply ]] || exit 2
exec 9>"$root/.backup.lock"
flock -n 9
python3 - "$root" "$db" "$keep" "$mode" <<'PY'
import hashlib, pathlib, re, shutil, sys
root, db, keep, mode = sys.argv[1:]
root = pathlib.Path(root)
files = ('database.dump', 'database.toc', 'filestore.tar', 'filestore.toc', 'database.name', 'created.utc')
valid = []
for p in root.iterdir():
    if not re.fullmatch(re.escape(db) + r'-[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9]+', p.name):
        continue
    if p.is_symlink() or not p.is_dir():
        raise SystemExit('Unsafe backup candidate: ' + str(p))
    for name in (*files, 'SHA256SUMS'):
        f = p / name
        if f.is_symlink() or not f.is_file():
            raise SystemExit('Invalid backup member: ' + str(f))
    lines = []
    for name in files:
        h = hashlib.sha256()
        with (p / name).open('rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        lines.append(f'{h.hexdigest()}  {name}\n')
    if (p / 'SHA256SUMS').read_text() != ''.join(lines) or (p / 'database.name').read_text().strip() != db:
        raise SystemExit('Integrity failure; no backups removed: ' + str(p))
    valid.append(p)
valid.sort(key=lambda p: p.name, reverse=True)
for p in valid[int(keep):]:
    print(('Removing ' if mode == '--apply' else 'Would remove ') + str(p), flush=True)
    if mode == '--apply':
        shutil.rmtree(p)
print(f'Verified {len(valid)} backups; retaining {min(len(valid), int(keep))}.')
PY
