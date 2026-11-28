#!/usr/bin/env python3
"""Turn launchd jobs on the phone off and on, over SSH (no reflash).

  tools/sweet-launchd.py list [TEXT]            jobs whose label contains TEXT (* = turned off by this tool)
  tools/sweet-launchd.py off LABEL...           turn jobs off (effective at the next boot)
  tools/sweet-launchd.py on LABEL...            undo
  tools/sweet-launchd.py show LABEL             print one job

  --host root@192.168.7.2 (default), or an ~/.ssh/config name such as phone

This iOS boots with launchd_unsecure_cache=1: launchd reads its jobs from the one file
/System/Library/xpc/launchd.plist (the LaunchDaemons folder is not read). "off" sets Disabled=true
in the job there and remembers that it did (key _SweetOff), so "on" only undoes its own changes.
A disabled job registers no Mach services, so a client looking one up gets an error at once
instead of waiting forever for a daemon that never answers.
The first change keeps the original next to it as launchd.plist.sweet-orig.
Files written on the phone reach the disk within about 30 s: wait that long before a hard reboot.
"""
import argparse
import os
import plistlib
import subprocess
import sys
import tempfile

PATH = '/System/Library/xpc/launchd.plist'


def ssh(host, cmd, data=None):
    r = subprocess.run(['ssh', host, cmd], input=data, capture_output=True)
    if r.returncode:
        sys.exit(f'ssh {host} {cmd!r} failed: {r.stderr.decode(errors="replace").strip()}')
    return r.stdout


def load(host):
    raw = ssh(host, f'cat {PATH}')
    return raw, plistlib.loads(raw)


def save(host, plist, raw_before):
    out = plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)
    # keep the very first original; write a temporary copy, check it arrived whole, then overwrite in place
    # (cat > keeps the file's owner and mode)
    ssh(host, f'[ -e {PATH}.sweet-orig ] || cp {PATH} {PATH}.sweet-orig')
    ssh(host, f'cat > {PATH}.sweet-new', out)
    got = ssh(host, f'cat {PATH}.sweet-new')
    if got != out:
        sys.exit('upload came back different; launchd.plist left as it was')
    ssh(host, f'cat {PATH}.sweet-new > {PATH} && rm {PATH}.sweet-new')
    print(f'{PATH} updated ({len(raw_before)} -> {len(out)} bytes). Reboot the phone to apply '
          '(wait ~30 s first so it reaches the disk).')


def jobs(plist):
    for section in ('LaunchDaemons', 'LaunchAgents'):
        for key, job in plist.get(section, {}).items():
            if isinstance(job, dict):
                yield section, key, job


def find(plist, label):
    hits = [(s, k, j) for s, k, j in jobs(plist) if j.get('Label') == label]
    if not hits:
        near = sorted({j.get('Label', '') for _, _, j in jobs(plist) if label.lower() in j.get('Label', '').lower()})
        sys.exit(f'no job labelled {label}' + (f'; similar: {", ".join(near[:10])}' if near else ''))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='root@192.168.7.2')
    ap.add_argument('cmd', choices=['list', 'off', 'on', 'show'])
    ap.add_argument('args', nargs='*')
    a = ap.parse_args()
    raw, plist = load(a.host)

    if a.cmd == 'list':
        text = a.args[0].lower() if a.args else ''
        rows = sorted((j.get('Label', '?'), s, j) for s, k, j in jobs(plist) if text in j.get('Label', '').lower())
        for label, s, j in rows:
            mark = '*' if '_SweetOff' in j else ('-' if j.get('Disabled') else ' ')
            print(f'{mark} {label:60s} {s}')
        print(f'{len(rows)} jobs (* off by this tool, - disabled by Apple)')
        return
    if a.cmd == 'show':
        for s, k, j in find(plist, a.args[0]):
            print(s, k)
            print(plistlib.dumps(j).decode())
        return

    changed = 0
    for label in a.args:
        for s, k, j in find(plist, label):
            if a.cmd == 'off':
                if '_SweetOff' in j:
                    print(f'{label}: already off')
                    continue
                j['_SweetOff'] = {'Disabled': j['Disabled']} if 'Disabled' in j else {}
                j['Disabled'] = True
                print(f'{label}: off')
            else:
                old = j.pop('_SweetOff', None)
                if old is None:
                    print(f'{label}: not turned off by this tool, left alone')
                    continue
                if 'Disabled' in old:
                    j['Disabled'] = old['Disabled']
                else:
                    j.pop('Disabled', None)
                print(f'{label}: on')
            changed += 1
    if changed:
        save(a.host, plist, raw)


if __name__ == '__main__':
    main()
