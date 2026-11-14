# ios-sweet on the Motorola Edge 30 Neo (miami, SM6375) - first port

Status: untested beyond the shim. Verified on a real phone: shim boot via `fastboot boot`, framebuffer
console, MMU/caches, PSCI, GICv3 + virtual timer + SGI, UFS reads (LUN 4 = `userdata`).
NOT verified: XNU boot, the payload loader, the root image.

## What is ported
- `shim/`: GIC, UFS, framebuffer, PS_HOLD, IMEM addresses; console text is also copied into the
  ramoops console zone; the loader scans the initrd for the payload (Android boot header v3
  hands over vendor ramdisk + generic ramdisk glued together).
- `tools/mkadt.py`: GIC, UFS, framebuffer, memory (`--highmem` = 3 GiB at 0x180000000), core clocks.
- `xnu/patches/0010,0012`: framebuffer at 0x85200000, mapped through the 32 MiB-aligned block 0x84000000.
- `xnu/patches/0028`: UFS root device on LUN 4.
- `tools/mkshimboot.py`: packs shim + payload into a v3 boot image from YOUR phone's stock boot.img.

## What is NOT ported (boot with these args)
Touch (the phone has a Goodix GT9916S on SPI, ios-sweet drives a GT9896 on I2C), power/volume keys
(different PMICs), battery (fake by default), USB (patches 0072-0077 hard-code the other SoC's USB
addresses; `sweetusb=0` stops them touching hardware). No GPU, Wi-Fi, audio, camera, modem.

## Steps
1. Fork the repo on GitHub, apply `git apply ios-miami.patch`, push to `main`.
2. Build the kernel: `gh workflow run xnu.yml -R YOU/ios-sweet`, then
   `gh run download RUN_ID -R YOU/ios-sweet -n xnu-kernel -D xnu/out`. (macOS runner, ~minutes.)
   Expect compile errors the first time - paste them to me.
3. Device tree: extract `d22ap.adt` from the iPhone X IPSW as in README step 2, then
   `python3 tools/mkadt.py shim/out/miami.adt --highmem`.
4. Payload (note: first boot is single-core, read-only root, no hardware drivers):
   ```
   python3 tools/mkpayload.py xnu/out/kernel.development.sweet shim/out/miami.adt shim/out/payload.bin \
     --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin launchd_unsecure_cache=1 wdt=-1 sweetfb_hz=30 sweetkeys=0 sweettouch=0 sweetusb=0'
   ```
5. Shim image: `shim/build-miami.sh` (needs `gcc-aarch64-linux-gnu`), then pack:
   `python3 tools/mkshimboot.py stock/boot.img stock/vendor_boot.img shim/out/Image shim/out/payload.bin miami-boot.img`
6. Root image: README step 2 (Linux, `apfs-fuse`, `hfsprogs`). Flash it into `userdata` - THIS ERASES
   ANDROID'S USER DATA (system partitions are untouched):
   `fastboot -S 256M flash userdata ios16-root.simg`   (this bootloader: max-sparse-size 256 MiB)
7. `fastboot boot miami-boot.img`

## Going back to Android
Android's system partitions are not modified. Erase/re-create `userdata` (fastboot `-w` or a recovery
factory reset) and boot normally. Keep the backups of persist, modemst1/2, fsg, utags, etc.
