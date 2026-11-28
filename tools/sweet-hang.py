#!/usr/bin/env python3
"""Symbolicate watchdog / hang reports (.ips with bug_type 509: "failed to terminate gracefully",
0x8badf00d) and ordinary crash reports (bug_type 309) from /private/var/mobile/Library/Logs/CrashReporter.

  tools/sweet-hang.py CACHE REPORT.ips [REPORT.ips ...] [--all] [--proc NAME]

CACHE is the dyld_shared_cache_arm64 of this iOS (with its .01 ... .symbols files next to it). These reports are stackshots of every process; this prints the hung
app's threads (or --proc NAME's), each blocked thread's wait ("mach_msg receive", "unfair lock owned
by thread N", ...) and where its turnstile points (the thread / process it waits for), and the
stack: shared-cache frames resolved with `ipsw dyld a2s` (the addresses in the report are unslid),
main-binary frames as offsets from its Mach-O header (+0x100000000 = its own vmaddr, for
llvm-objdump). Threads that are only idle in a run loop or a workqueue are left out unless --all.
"""
import argparse
import json
import os
import re
import subprocess
import sys

_memo = {}


def a2s(cache, addr):
    if addr in _memo:
        return _memo[addr]
    # no --cache: that makes ipsw parse every symbol and all ObjC metadata of the whole cache (minutes,
    # several GB of RAM); a direct lookup per address is quick
    r = subprocess.run(['ipsw', 'dyld', 'a2s', cache, hex(addr)], capture_output=True, text=True)
    out = [l for l in r.stdout.splitlines() if l.startswith('0x')]
    sym = out[-1].split(': ', 1)[1] if out else '?'
    m = re.search(r'dylib=(\S+)', r.stderr + r.stdout)
    lib = os.path.basename(m.group(1).strip('"')) if m else ''
    _memo[addr] = f'{lib}: {sym}' if lib else sym
    print(f'  {len(_memo)} addresses looked up', end='\r', file=sys.stderr, flush=True)
    return _memo[addr]


IDLE = ('mach_msg2_trap', 'mach_msg_trap', '__workq_kernreturn', 'start_wqthread', '__semwait_signal',
        'CFRunLoopRun', '__CFRunLoopServiceMachPort')


def frames(cache, images, th):
    out = []
    for idx, off in th.get('userFrames', []):
        uuid, base, kind = images[idx] if idx < len(images) else ('?', 0, '?')
        if kind == 'S':
            out.append(a2s(cache, base + off))
        elif kind == 'P':
            out.append(f'main binary +{hex(0x100000000 + off)}')
        else:
            out.append(f'{uuid[:8]}+{hex(off)} ({kind})')
    return out


def crash(cache, path, H, B):
    """bug_type 309: an ordinary crash report (EXC_*, abort, ObjC exception)."""
    imgs = B.get('usedImages', [])
    sc = B.get('sharedCache', {})
    slide = sc.get('base', 0x180000000) - 0x180000000 if sc else 0
    print(f'##### {os.path.basename(path)}')
    print(f'{B.get("procName")}[{B.get("pid")}] at {B.get("captureTime")}')
    for k in ('exception', 'termination', 'asi', 'ktriageinfo'):
        if k in B:
            print(f'  {k}: {json.dumps(B[k])[:1500]}')

    def sym(fr):
        im = imgs[fr['imageIndex']] if fr.get('imageIndex') is not None and fr['imageIndex'] < len(imgs) else {}
        off = fr.get('imageOffset', 0)
        name = im.get('name') or os.path.basename(im.get('path', '')) or '?'
        if fr.get('symbol'):
            return f'{name}: {fr["symbol"]} + {fr.get("symbolLocation", 0)}'
        if im.get('source') == 'S' or (sc and im.get('base', 0) - slide >= 0x180000000 and im.get('base', 0) - slide < 0x300000000):
            return a2s(cache, im['base'] - slide + off)
        return f'{name} +{hex(0x100000000 + off)}' if im.get('source') == 'P' or fr['imageIndex'] == 0 else f'{name} +{hex(off)}'

    if 'lastExceptionBacktrace' in B:
        print('\n  last exception backtrace (where the exception was thrown):')
        for i, fr in enumerate(B['lastExceptionBacktrace']):
            print(f'    {i:2d} {sym(fr)}')
    ft = B.get('faultingThread', 0)
    for n, th in enumerate(B.get('threads', [])):
        if n != ft and not th.get('triggered'):
            continue
        print(f'\n  crashed thread {n} {th.get("queue", "")} {th.get("name", "")}')
        for i, fr in enumerate(th.get('frames', [])):
            print(f'    {i:2d} {sym(fr)}')
    print()


def report(cache, path, show_all, procname):
    text = open(path, errors='replace').read()
    head, body = text.split('\n', 1)
    B = json.loads(body)
    H = json.loads(head)
    if 'stackshot' not in B:
        return crash(cache, path, H, B)
    snap = B['stackshot']
    pp = snap['processByPid']
    images = snap['binaryImages']
    owner = {tid: pid for pid, p in pp.items() for tid in p['threadById']}
    if procname:
        pids = [pid for pid, p in pp.items() if p.get('procname') == procname]
        if not pids:
            sys.exit(f'{path}: no process {procname}')
        pid = pids[0]
    else:
        pid = str(B['pid'])
    p = pp[pid]
    print(f'##### {os.path.basename(path)}')
    print(f'{p["procname"]}[{pid}] at {B.get("captureTime")}: {B["termination"]["details"][0]}')
    waits = p.get('waitInfo', []) + p.get('turnstileInfo', [])

    def about(tid):
        return [w.split(': ', 1)[1] for w in waits if w.startswith(f'thread {tid}:')]

    for tid, th in sorted(p['threadById'].items(), key=lambda kv: kv[1].get('dispatch_queue_label') != 'com.apple.main-thread'):
        info = about(tid)
        fr = frames(cache, images, th)
        idle = not info and any(any(k in f for k in IDLE) for f in fr[:3])
        if idle and not show_all:
            continue
        label = th.get('dispatch_queue_label') or th.get('name') or ''
        print(f'\n  thread {tid} {label} {",".join(th.get("state", []))}')
        for w in info:
            m = re.search(r'blocked on (\d+)', w)
            extra = ''
            if m and m.group(1) in owner and owner[m.group(1)] != pid:
                o = pp[owner[m.group(1)]]
                ot = o['threadById'][m.group(1)]
                extra = f'  -> {o["procname"]}[{owner[m.group(1)]}] thread {m.group(1)} ' \
                        f'{ot.get("dispatch_queue_label") or ot.get("name") or ""} {",".join(ot.get("state", []))}'
            print(f'    waits: {w}{extra}')
        for i, f in enumerate(fr):
            print(f'    {i:2d} {f}')
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cache')
    ap.add_argument('reports', nargs='+')
    ap.add_argument('--all', action='store_true', help='also threads that are just idle')
    ap.add_argument('--proc', help='show this process from the stackshot instead of the hung app')
    a = ap.parse_args()
    if not os.path.exists(a.cache):
        sys.exit(f'no shared cache at {a.cache}')
    for r in a.reports:
        report(a.cache, r, a.all, a.proc)


if __name__ == '__main__':
    main()
