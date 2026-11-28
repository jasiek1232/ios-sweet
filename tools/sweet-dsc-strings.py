#!/usr/bin/env python3
"""Disassemble a function in the dyld shared cache and show the strings it uses, without ipsw's symbol
index (`ipsw dyld disass --symbol ...` and its markup build that index first: tens of GB of RAM).

  tools/sweet-dsc-strings.py CACHE ADDR [--count N]

ADDR is an unslid shared-cache address (a crash report frame: image base - (sharedCache.base -
0x180000000) + imageOffset; sweet-hang.py prints them). Runs `ipsw dyld disass CACHE --vaddr ADDR
--count N --quiet`, follows each adrp + add / adrp + ldr pair to its target and prints what is there:
a C string, an Objective-C/CF string constant (its text), or the pointer stored there (selector and
class references). NSAssert-style failures load their condition / description text just before the
call into NSAssertionHandler, so the string loaded closest before the crashing offset is the message.
"""
import argparse
import re
import subprocess
import sys

MASK = (1 << 36) - 1   # shared-cache pointers carry slide-info bits above the address


def ipsw(*args):
    r = subprocess.run(['ipsw', 'dyld', *args], capture_output=True, text=True)
    return r.stdout + r.stderr


def dump(cache, addr, size=64):
    out = ipsw('dump', cache, hex(addr), '--size', str(size))
    data = bytearray()
    for line in out.splitlines():
        m = re.match(r'^\s*[0-9a-fA-F]{6,16}:?\s+((?:[0-9a-fA-F]{2}\s+){1,16})', line)
        if m:
            data += bytes(int(b, 16) for b in m.group(1).split())
    return bytes(data)


def cstr(b):
    s = b.split(b'\0', 1)[0]
    if len(s) >= 3 and all(32 <= c < 127 or c in (9, 10) for c in s):
        return s.decode()
    return None


def describe(cache, addr):
    b = dump(cache, addr)
    if not b:
        return '(could not read)'
    s = cstr(b)
    if s:
        return f'"{s}"'
    if len(b) >= 32:   # CFString constant: isa, flags, char *, length
        ptr = int.from_bytes(b[16:24], 'little') & MASK
        n = int.from_bytes(b[24:32], 'little')
        if 0x180000000 <= ptr < 0x300000000 and 0 < n < 4096:
            t = cstr(dump(cache, ptr, min(n + 1, 512)))
            if t:
                return f'@"{t}"'
    if len(b) >= 8:
        p = int.from_bytes(b[:8], 'little') & MASK
        if 0x180000000 <= p < 0x300000000:
            t = cstr(dump(cache, p, 96))
            return f'-> {hex(p)}' + (f' "{t}"' if t else '')
    return b[:16].hex()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cache')
    ap.add_argument('addr', type=lambda x: int(x, 0))
    ap.add_argument('--count', type=int, default=200)
    a = ap.parse_args()
    dis = ipsw('disass', a.cache, '--vaddr', hex(a.addr), '--count', str(a.count), '--quiet')
    pages = {}
    seen = {}
    for line in dis.splitlines():
        m = re.match(r'^\s*(?:0x)?([0-9a-fA-F]{9,16}):?\s+(.*)$', line)
        if not m:
            print(line)
            continue
        pc = int(m.group(1), 16)
        text = m.group(2)
        note = ''
        mm = re.search(r'\badrp\s+(x\d+),\s*#?(0x[0-9a-fA-F]+)', text)
        if mm:
            pages[mm.group(1)] = int(mm.group(2), 16)
        mm = re.search(r'\badd\s+(x\d+),\s*(x\d+),\s*#(0x[0-9a-fA-F]+|\d+)', text)
        if mm and mm.group(2) in pages:
            t = pages[mm.group(2)] + int(mm.group(3), 0)
            seen.setdefault(t, describe(a.cache, t))
            note = f'    ; {hex(t)} {seen[t]}'
        mm = re.search(r'\bldr\s+(x\d+),\s*\[(x\d+),\s*#(0x[0-9a-fA-F]+|\d+)\]', text)
        if mm and mm.group(2) in pages:
            t = pages[mm.group(2)] + int(mm.group(3), 0)
            seen.setdefault(t, describe(a.cache, t))
            note = f'    ; [{hex(t)}] {seen[t]}'
        print(f'{hex(pc)} (+{pc - a.addr:4d})  {text}{note}')
    if not seen:
        print('(no adrp-based references parsed; raw ipsw output above)', file=sys.stderr)


if __name__ == '__main__':
    main()
