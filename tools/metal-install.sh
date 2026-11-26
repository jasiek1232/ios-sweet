#!/bin/bash
# Put the Turnip smoke test (vktest) on the phone's iOS root and make it run by itself.
#
#   sudo tools/metal-install.sh OLD.img metal-userland.tar.gz OUT.simg
#
# OLD.img  the image that is on the phone's userdata partition right now (never modified here)
# the tar  the "metal-userland" artifact of the GitHub workflow "metal"
# OUT.simg a sparse image (or - for none; use - : this bootloader refuses userdata writes, flash the whole NEW via adb) with only the changed blocks: fastboot flash userdata OUT.simg
#
# It works on a copy of OLD (16 GiB sparse: check `df -h` first), mounted read-write the way
# tools/sweet-root-tweaks.py wants it, and adds
#   /private/var/local/bin/vktest, /private/var/local/lib/libvulkan_freedreno.dylib
#   (+ m2vtest in /private/var/local/metal2/, SweetMetal2.bundle in /System/Library/Extensions (used with boot-arg
#   sweetmtl=2), /private/var/local/{mtl2.log,mtl2-clients,m2v-cache})
#   a launchd job org.sweet.vktest: runs vktest once, 150 s after boot (m2vtest once, 480 s after boot), stdout and stderr on the
#   console, environment TU_DARWIN_SERVICE=SweetGPUService TU_DARWIN_OPEN_TYPE=0x4147 (Turnip's darwin backend
#   otherwise looks for IOAcceleratorES). Boot with sweetgpu=5 or more so that the service exists.
# Afterwards the copy is what the phone holds: keep it as the OLD of the next run.
set -euo pipefail
[[ $# -eq 3 ]] || { sed -n '2,19p' "$0"; exit 2; }
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
old=$(realpath "$1") pkg=$(realpath "$2") out=$3
work=${WORK:-$(dirname "$old")}
new=$work/$(basename "${old%.img}")-metal.img
mnt=$work/metal-mnt
[[ ! -e $new ]] || { echo "refusing to overwrite $new (move it away, or use it as OLD)" >&2; exit 1; }
for t in fsck.hfsplus python3 tar; do command -v "$t" >/dev/null || { echo "missing $t" >&2; exit 1; }; done

stage=$(mktemp -d)
cleanup() { mountpoint -q "$mnt" 2>/dev/null && umount "$mnt" || true; rm -rf "$stage"; }
trap cleanup EXIT
tar -xzf "$pkg" -C "$stage"
for f in vktest libvulkan_freedreno.dylib; do [[ -s $stage/$f ]] || { echo "$pkg has no $f" >&2; exit 1; }; done

echo "== copy $old -> $new"
cp --sparse=always "$old" "$new"
fsck.hfsplus -fy "$new" || true
mkdir -p "$mnt"
mount -t hfsplus -o loop,force "$new" "$mnt"
[[ -d $mnt/System/Library/xpc ]] || { echo "not an iOS root: $mnt" >&2; exit 1; }

echo "== files"
install -d -o 0 -g 0 -m 755 "$mnt/private/var/local" "$mnt/private/var/local/bin" "$mnt/private/var/local/lib"
install -o 0 -g 0 -m 755 "$stage/vktest" "$mnt/private/var/local/bin/vktest"
install -o 0 -g 0 -m 755 "$stage/libvulkan_freedreno.dylib" "$mnt/private/var/local/lib/libvulkan_freedreno.dylib"
if [[ -d $stage/SweetMetal2.bundle ]]; then
	install -d -o 0 -g 0 -m 755 "$mnt/private/var/local/metal2"
	rm -rf "$mnt/private/var/local/metal2/SweetMetal2.bundle"
	cp -a "$stage/SweetMetal2.bundle" "$mnt/private/var/local/metal2/"
	[[ ! -s $stage/m2vtest ]] || install -o 0 -g 0 -m 755 "$stage/m2vtest" "$mnt/private/var/local/metal2/m2vtest"
	chown -R 0:0 "$mnt/private/var/local/metal2"
	# Metal for backboardd (boot with sweetmtl=2 and a kernel with patches 0096/0097): Metal.framework loads the plugin
	# named by the accelerator's MetalPluginName from /System/Library/Extensions; SweetMetal2 reads these files.
	install -d -o 0 -g 0 -m 755 "$mnt/System/Library/Extensions"
	rm -rf "$mnt/System/Library/Extensions/SweetMetal2.bundle"
	cp -a "$stage/SweetMetal2.bundle" "$mnt/System/Library/Extensions/"
	chown -R 0:0 "$mnt/System/Library/Extensions/SweetMetal2.bundle"
	install -o 0 -g 0 -m 666 /dev/null "$mnt/private/var/local/mtl2.log"
	printf 'SpringBoard\n' > "$mnt/private/var/local/mtl2-clients"
	chown 0:0 "$mnt/private/var/local/mtl2-clients"; chmod 644 "$mnt/private/var/local/mtl2-clients"
	install -d -o 0 -g 0 -m 777 "$mnt/private/var/local/m2v-cache"
# optional: TU_DEBUG_OPTS=sysmem sudo ./metal-install.sh ...  -> Turnip options + log (/var/local/turnip-<proc>.log) in every Metal client
if [[ -n ${TU_DEBUG_OPTS:-} ]]; then
	printf '%s\n' "$TU_DEBUG_OPTS" > "$mnt/private/var/local/mtl2-tu-debug"; chmod 644 "$mnt/private/var/local/mtl2-tu-debug"
else
	rm -f "$mnt/private/var/local/mtl2-tu-debug"
fi
fi

echo "== launchd job"
M2V="$mnt/private/var/local/metal2/m2vtest" python3 - "$mnt/System/Library/xpc/launchd.plist" <<'PY'
import os, plistlib, sys
path = sys.argv[1]
st = os.stat(path)
with open(path, 'rb') as f:
    plist = plistlib.load(f)
key = '/System/Library/LaunchDaemons/org.sweet.vktest.plist'
plist['LaunchDaemons'][key] = {
    'Label': 'org.sweet.vktest',
    'ProgramArguments': ['/private/var/local/bin/vktest', '/private/var/local/lib/libvulkan_freedreno.dylib'],
    'EnvironmentVariables': {'TU_DARWIN_SERVICE': 'SweetGPUService', 'TU_DARWIN_OPEN_TYPE': '0x4147'},
    'StartInterval': 150,
    'LaunchOnlyOnce': True,
    'JetsamProperties': {'JetsamMemoryLimit': 2048},
    'RunAtLoad': False,
    'UserName': 'root',
    'StandardOutPath': '/dev/console',
    'StandardErrorPath': '/dev/console',
}
if os.path.exists(os.path.join(os.path.dirname(os.environ['M2V']), 'm2vtest')):
    key2 = '/System/Library/LaunchDaemons/org.sweet.m2vtest.plist'
    plist['LaunchDaemons'][key2] = {
        'Label': 'org.sweet.m2vtest',
        'ProgramArguments': ['/private/var/local/metal2/m2vtest', '/private/var/local/lib/libvulkan_freedreno.dylib',
                             '/private/var/local/m2v-cache'],
        'EnvironmentVariables': {'TU_DARWIN_SERVICE': 'SweetGPUService', 'TU_DARWIN_OPEN_TYPE': '0x4147', 'NMB_LIST': '1'},
        'StartInterval': 480,
        'LaunchOnlyOnce': True,
        'JetsamProperties': {'JetsamMemoryLimit': 3072},
        'RunAtLoad': False,
        'UserName': 'root',
        'StandardOutPath': '/dev/console',
        'StandardErrorPath': '/dev/console',
    }
    print('org.sweet.m2vtest added')
with open(path, 'wb') as f:
    plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)
os.chown(path, st.st_uid, st.st_gid)
os.chmod(path, st.st_mode & 0o7777)
print('launchd.plist:', len(plist['LaunchDaemons']), 'jobs, org.sweet.vktest added')
PY

sync
umount "$mnt"
if [[ $out != - ]]; then
	echo "== sparse image"
	python3 "$here/sparsediff.py" "$old" "$new" "$out"
	ls -la "$out"
fi
echo
echo "Flash the whole $new (recovery + adb push to /dev/block/by-name/userdata, then compare sha256sum)."
echo "A diff ($out) is only valid while the phone has NOT booted since its last full flash."
echo "from now on the phone's image is $new (use it as OLD next time)"
