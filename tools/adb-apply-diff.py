#!/usr/bin/env python3
"""Apply the differences between OLD.img and NEW.img to the phone's userdata
partition over adb (OrangeFox/Lineage recovery), without fastboot.

  adb-apply-diff.py OLD.img NEW.img [--dev /dev/block/sda34] [--dry-run] [--no-verify]

OLD must equal what the partition holds now; NEW is the edited image.
Changed 4 KiB blocks (runs closer than 256 KiB are merged) are pushed to
/tmp on the phone in <=16 MiB pieces, dd'ed into place with fsync, then read
back and compared. Re-running is safe: it only rewrites what differs from OLD.
"""
import argparse, os, subprocess, sys, tempfile

BS = 4096
MERGE_GAP = 64          # blocks
MAX_PIECE = 4096        # blocks = 16 MiB


def adb(*a, **k):
    return subprocess.run(['adb', *a], check=True, **k)


def runs(old, new):
    size = os.path.getsize(new)
    if os.path.getsize(old) != size or size % BS:
        sys.exit('images must have the same size, a multiple of 4096')
    out, cur = [], None
    step = 2048
    with open(old, 'rb') as fo, open(new, 'rb') as fn:
        for base in range(0, size // BS, step):
            bo, bn = fo.read(step * BS), fn.read(step * BS)
            if bo == bn:
                continue
            for i in range(len(bn) // BS):
                if bo[i*BS:(i+1)*BS] != bn[i*BS:(i+1)*BS]:
                    b = base + i
                    if cur and b - cur[1] <= MERGE_GAP:
                        cur[1] = b + 1
                    else:
                        cur = [b, b + 1]
                        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('old'); ap.add_argument('new')
    ap.add_argument('--dev', default='/dev/block/sda34')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-verify', action='store_true')
    a = ap.parse_args()
    rs = runs(a.old, a.new)
    pieces = []
    for s, e in rs:
        while s < e:
            n = min(MAX_PIECE, e - s)
            pieces.append((s, n)); s += n
    total = sum(n for _, n in pieces) * BS
    print(f'{len(rs)} runs, {len(pieces)} pieces, {total/1048576:.1f} MiB to write')
    if a.dry_run:
        return
    adb('shell', 'true')
    tmp = tempfile.NamedTemporaryFile(delete=False); tmp.close()
    with open(a.new, 'rb') as fn:
        for k, (s, n) in enumerate(pieces, 1):
            fn.seek(s * BS)
            data = fn.read(n * BS)
            with open(tmp.name, 'wb') as t:
                t.write(data)
            adb('push', tmp.name, '/tmp/piece.bin', stdout=subprocess.DEVNULL)
            adb('shell', f'dd if=/tmp/piece.bin of={a.dev} bs=4096 seek={s} '
                         f'count={n} conv=notrunc,fsync 2>/dev/null && rm /tmp/piece.bin')
            if not a.no_verify:
                got = subprocess.run(['adb', 'exec-out',
                    f'dd if={a.dev} bs=4096 skip={s} count={n} 2>/dev/null'],
                    check=True, stdout=subprocess.PIPE).stdout
                if got != data:
                    sys.exit(f'VERIFY FAILED at block {s} (+{n}); stop and tell Claude')
            print(f'\r{k}/{len(pieces)}', end='', flush=True)
    os.unlink(tmp.name)
    print('\nDone. Phone userdata now equals NEW. Make NEW the next OLD.')


main()
