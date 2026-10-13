"""DX10 DDS writer and header reader for the HD texture pack (BC1 71, BC3 77, BC7 98, RGBA8 28; UNORM, never sRGB).

The core mod's loader (kit remaster_tex.h) reads exactly this layout: 'DDS ', the 124-byte header, the DX10 header,
then every mip level, top level first. write() is atomic: the file is written under a hidden temporary name beside
the target and renamed, so a stopped setup never leaves half a texture behind."""
import os
import struct
from pathlib import Path

DXGI = {'bc1': 71, 'bc3': 77, 'bc7': 98, 'rgba8': 28}
FORMAT_OF = {v: k for k, v in DXGI.items()}
BLOCK = {'bc1': 8, 'bc3': 16, 'bc7': 16}
HEADER_BYTES = 4 + 124 + 20


def level_size(fmt, w, h):
    if fmt in BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[fmt]
    return w * h * 4


def full_chain(w, h):
    """The number of mip levels down to 1x1: floor(log2(max side)) + 1."""
    return max(w, h).bit_length()


def encode_file(fmt, w, h, levels):
    """The DDS file's bytes. levels: list of bytes, top level first, each already encoded in `fmt`."""
    for i, data in enumerate(levels):
        lw, lh = max(w >> i, 1), max(h >> i, 1)
        if len(data) != level_size(fmt, lw, lh):
            raise ValueError(f'level {i}: {len(data)} bytes, expected {level_size(fmt, lw, lh)}')
    hdr = [0] * 31
    hdr[0] = 124
    hdr[1] = 0x1007 | 0x20000 | (0x80000 if fmt in BLOCK else 0x8)   # CAPS|HEIGHT|WIDTH|PIXELFORMAT|MIPMAPCOUNT|LINEARSIZE/PITCH
    hdr[2], hdr[3] = h, w
    hdr[4] = level_size(fmt, w, h) if fmt in BLOCK else w * 4
    hdr[6] = len(levels)
    hdr[18], hdr[19] = 32, 0x4                                       # ddspf: FOURCC
    hdr[20] = int.from_bytes(b'DX10', 'little')
    hdr[26] = 0x1000 | (0x400008 if len(levels) > 1 else 0)          # TEXTURE (+COMPLEX|MIPMAP)
    dx10 = [DXGI[fmt], 3, 0, 1, 0]                                   # format, TEXTURE2D, misc, arraySize, misc2
    return b'DDS ' + struct.pack('<31I', *hdr) + struct.pack('<5I', *dx10) + b''.join(levels)


def write(path, fmt, w, h, levels):
    """Write the DDS atomically (hidden temporary name, then rename)."""
    path = Path(path)
    data = encode_file(fmt, w, h, levels)
    tmp = path.with_name('.' + path.name + '.part')
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def read_header(path):
    """(fmt, width, height, levels, data bytes after the headers) of a DX10 DDS this module writes; ValueError if the
    file is not one, or if its size does not match its header (the kit refuses such files)."""
    path = Path(path)
    with open(path, 'rb') as f:
        head = f.read(HEADER_BYTES)
    size = path.stat().st_size
    if len(head) < HEADER_BYTES or head[:4] != b'DDS ':
        raise ValueError('not a DDS file')
    hdr = struct.unpack_from('<31I', head, 4)
    if hdr[0] != 124 or hdr[20] != int.from_bytes(b'DX10', 'little'):
        raise ValueError('not a DX10 DDS')
    dxgi, dim = struct.unpack_from('<2I', head, 128)
    fmt = FORMAT_OF.get(dxgi)
    if fmt is None or dim != 3:
        raise ValueError(f'DXGI format {dxgi} / dimension {dim} not supported')
    h, w, levels = hdr[2], hdr[3], max(hdr[6], 1)
    want = sum(level_size(fmt, max(w >> i, 1), max(h >> i, 1)) for i in range(levels))
    if size - HEADER_BYTES != want:
        raise ValueError(f'{size - HEADER_BYTES} data bytes, the header says {want}')
    return fmt, w, h, levels, want
