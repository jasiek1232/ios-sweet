#!/usr/bin/env python3
"""Pack the shim + a SWEETXNU payload into an Android boot image (header v3) for the
Motorola Edge 30 Neo (miami), using the phone's own stock boot.img as the template.

usage: mkshimboot.py STOCK_BOOT.img STOCK_VENDOR_BOOT.img SHIM_Image PAYLOAD.bin OUT.img

Header v3 has no ramdisk field for the vendor ramdisk; the bootloader hands the kernel
vendor ramdisk + our ramdisk glued together. The shim finds the payload by scanning, but the
payload must start 8-byte aligned, so we prepend the few padding bytes that make
(vendor_ramdisk_size + pad) a multiple of 8. Read-only on every input. Nothing here flashes.
"""
import hashlib, struct, sys

PAGE = 4096


def align(n):
    return (n + PAGE - 1) // PAGE * PAGE


def main():
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    stock_p, vendor_p, shim_p, payload_p, out_p = sys.argv[1:]
    stock = open(stock_p, 'rb').read()
    vendor = open(vendor_p, 'rb').read(4096)
    shim = bytearray(open(shim_p, 'rb').read())
    payload = open(payload_p, 'rb').read()

    if stock[:8] != b'ANDROID!' or struct.unpack_from('<I', stock, 40)[0] != 3:
        sys.exit('STOCK_BOOT.img is not an Android boot image, header v3')
    if vendor[:8] != b'VNDRBOOT' or struct.unpack_from('<I', vendor, 8)[0] != 3:
        sys.exit('STOCK_VENDOR_BOOT.img is not a vendor_boot image, header v3')
    ksize = struct.unpack_from('<I', stock, 8)[0]
    vendor_rd = struct.unpack_from('<I', vendor, 24)[0]
    kernel = stock[PAGE:PAGE + ksize]

    if kernel[56:60] == b'ARM\x64':
        shim[8:16] = kernel[8:16]      # text_offset from the stock kernel
        shim[24:32] = kernel[24:32]    # flags
    else:
        print('WARNING: stock kernel has no ARM64 Image header; keeping shim defaults')
    shim[16:24] = struct.pack('<Q', align(len(shim)))   # image_size

    pad = (-vendor_rd) % 8
    ramdisk = b'\0' * pad + payload
    print('vendor ramdisk %d bytes -> pad %d, payload %d bytes' % (vendor_rd, pad, len(payload)))

    header = bytearray(stock[:PAGE])
    struct.pack_into('<I', header, 8, len(shim))        # kernel_size
    struct.pack_into('<I', header, 12, len(ramdisk))    # ramdisk_size

    def padded(b):
        return bytes(b) + b'\0' * (align(len(b)) - len(b))

    out = bytes(header) + padded(shim) + padded(ramdisk)
    open(out_p, 'wb').write(out)
    print('wrote %s: %d bytes, sha256 %s' % (out_p, len(out), hashlib.sha256(out).hexdigest()))


main()
