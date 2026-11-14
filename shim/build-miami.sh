#!/bin/bash
# Build the shim Image for the Motorola Edge 30 Neo (no mkbootimg, no gawk needed).
# Output: shim/out/Image. Pack it with tools/mkshimboot.py.
set -euo pipefail
cd "$(dirname "$0")"
CC=aarch64-linux-gnu-gcc
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"
mkdir -p out
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c; do
	$CC $CFLAGS -c $f -o out/${f%.*}.o
done
abs=$(aarch64-linux-gnu-readelf -r out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }
$CC $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o -o out/shim.elf
aarch64-linux-gnu-objcopy -O binary out/shim.elf out/Image
size=$(python3 - <<'PY'
import subprocess
for l in subprocess.check_output(['aarch64-linux-gnu-nm', 'out/shim.elf']).decode().split('\n'):
    p = l.split()
    if len(p) == 3 and p[2] == '_end':
        print(int(p[0], 16))
PY
)
truncate -s "$size" out/Image
ls -l out/Image
