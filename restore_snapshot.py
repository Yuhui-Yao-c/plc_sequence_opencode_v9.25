# -*- coding: utf-8 -*-
"""restore_snapshot.py — restore a project from PROJECT_SNAPSHOT_V9.14.txt.

Usage:
    python restore_snapshot.py <snapshot.txt> <target_dir>

Verifies three layers before writing any file:
  1. base64 -> zip bytes : zip_sha256 must match snapshot CHECKSUMS
  2. zip inner files     : per-file SHA256 must match MANIFEST rows
  3. manifest text       : manifest_sha256 must match CHECKSUMS
Then writes files and re-verifies every written file byte-for-byte.
Exits non-zero on any mismatch. Requires only Python 3.8+ standard library.
"""
import sys, os, re, json, hashlib, base64, zipfile

def sha256(b):
    return hashlib.sha256(b).hexdigest()

def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    snap_path, target = sys.argv[1], sys.argv[2]
    text = open(snap_path, encoding='utf-8').read()

    man = re.search(r'# MANIFEST_BEGIN\n(.*?)\n# MANIFEST_END', text, re.S)
    chk = re.search(r'# CHECKSUMS: manifest_sha256=([0-9a-f]{64}) zip_sha256=([0-9a-f]{64}) zip_bytes=(\d+)', text)
    b64 = re.search(r'# ===== SNAPSHOT_BASE64_BEGIN =====\n(.*?)\n# ===== SNAPSHOT_BASE64_END =====', text, re.S)
    if not (man and chk and b64):
        print('ERROR: snapshot structure markers not found'); return 1
    manifest_sha, zip_sha, zip_size = chk.group(1), chk.group(2), int(chk.group(3))
    rows = {}
    for line in man.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        rel, size, digest = line.rsplit('|', 2)
        rows[rel] = (int(size), digest)
    if sha256(man.group(1).encode('utf-8')) != manifest_sha:
        print('ERROR: manifest checksum mismatch'); return 1

    raw = base64.b64decode(''.join(b64.group(1).split()))
    if len(raw) != zip_size or sha256(raw) != zip_sha:
        print('ERROR: zip payload checksum mismatch (%d bytes)' % len(raw)); return 1

    import io
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = set(zf.namelist())
    if names != set(rows):
        print('ERROR: zip content differs from manifest:',
              'extra=%s missing=%s' % (sorted(names - set(rows))[:5], sorted(set(rows) - names)[:5]))
        return 1

    os.makedirs(target, exist_ok=True)
    bad = []
    for rel, (size, digest) in sorted(rows.items()):
        data = zf.read(rel)
        if len(data) != size or sha256(data) != digest:
            bad.append(rel); continue
        dst = os.path.join(target, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst) or target, exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        with open(dst, 'rb') as f:
            if sha256(f.read()) != digest:
                bad.append(rel)
    if bad:
        print('ERROR: %d file(s) failed verification: %s' % (len(bad), bad[:10]))
        return 1
    print('RESTORED OK: %d files -> %s' % (len(rows), os.path.abspath(target)))
    print('manifest_sha256:', manifest_sha)
    print('zip_sha256    :', zip_sha)
    return 0

if __name__ == '__main__':
    sys.exit(main())
