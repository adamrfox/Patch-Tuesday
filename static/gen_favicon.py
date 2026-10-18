#!/usr/bin/env python3 -I
"""Generate favicon.ico with embedded 32x32 PNG at 32x32."""
import struct, zlib, binascii

def png_chunk(chunk_type, data):
    """Create a PNG chunk."""
    chunk = chunk_type + data
    crc = binascii.crc32(chunk) & 0xffffffff
    return struct.pack('>I', len(data)) + chunk + struct.pack('>I', crc)

# Create dark circle with wrench icon at 32x32
pixels = []
cx, cy = 16, 16
for y in range(32):
    for x in range(32):
        dx, dy = x - cx, y - cy
        d2 = dx*dx + dy*dy
        
        if d2 > 225:  # outside circle
            pixels.extend([40, 40, 62, 255])
        elif ((x-9)**2 + (y-11)**2 < 6) or ((x-7)**2 + (y-13)**2 < 4):
            # Wrench highlight spots
            pixels.extend([255, 107, 53, 255])
        else:
            pixels.extend([46, 46, 70, 255])

raw = bytes(pixels)

# Build PNG
png = b'\x89PNG\r\n\x1a\n'
ihdr = struct.pack('>IIBBBBB', 32, 32, 8, 6, 0, 0, 0)
png += png_chunk(b'IHDR', ihdr)

idat_data = b''
for y in range(32):
    idat_data += b'\x00' + raw[y*32*4:(y+1)*32*4]
png += png_chunk(b'IDAT', zlib.compress(idat_data))
png += png_chunk(b'IEND', b'')

with open('/home/midway/patch-tuesday/static/favicon.png', 'wb') as f:
    f.write(png)
print("Generated favicon.png (32x32)")
