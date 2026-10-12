#!/usr/bin/env python3
"""TPK texture list reader (2.0.4): the name hash, stored name and size of every texture in the game's texture packs,
read straight from your game files for tools/setup_kit/mod_names.py. No pixels are read or written.

Layout as observed in the retail PC files (chunk ids; named after TextureInfo as the community documents it):
  0xB3300000 TPK            0xB3310000 info: 33310001 header (name, file, hash), 33310004 TextureInfo records (0x7C
                                             each: +0x0C stored name (24 bytes), +0x24 name hash, +0x44 width, height)
                                             33310003 compressed-TPK rows (the HUD styles; see textures())
Whole files may be JDLZ-compressed (JDLZ 02 10); the minimap's files are a stream of 0x0003A100 chunks, one JDLZ
block each. HUFF blocks (some compressed TPKs) are not decoded here: those textures are left out of the list (a mod
for them still loads; the report then says UNKNOWN NAME).

    python tpk.py <file...>     list textures (hash, stored name, width x height)
"""
import struct
import sys
from pathlib import Path

TPK, INFO = 0xB3300000, 0xB3310000


def jdlz(data):
    """JDLZ, EA's LZ variant."""
    if data[:6] != b"JDLZ\x02\x10":
        raise ValueError("unsupported JDLZ header")
    output_size, input_size = struct.unpack_from("<II", data, 8)
    pos, control, kind = 18, data[16] | 256, data[17] | 256
    out = bytearray()
    while len(out) < output_size:
        if control & 1:
            a, b = data[pos:pos + 2]
            pos += 2
            if kind & 1:
                run, distance = ((a >> 4) << 8 | b) + 3, (a & 15) + 1
            else:
                run, distance = (a & 31) + 3, ((a >> 5) << 8 | b) + 17
            if distance > len(out):
                raise ValueError("invalid back-reference")
            run = min(run, output_size - len(out))
            if run <= distance:
                out.extend(out[len(out) - distance:len(out) - distance + run])
            else:
                pattern = out[-distance:]
                out.extend((pattern * ((run + distance - 1) // distance))[:run])
            kind >>= 1
        else:
            out.append(data[pos])
            pos += 1
        control >>= 1
        if len(out) >= output_size:
            break
        if control == 1:
            control, pos = data[pos] | 256, pos + 1
        if kind == 1:
            kind, pos = data[pos] | 256, pos + 1
    if pos > input_size + 2:
        raise ValueError("JDLZ read past its input")
    return bytes(out)


def load(path):
    """The file's chunk stream: whole-file JDLZ decoded; a stream of 0x0003A100 JDLZ chunks decoded and joined."""
    d = Path(path).read_bytes()
    if d[:4] == b"JDLZ":
        return jdlz(d)
    if d[:4] == b"\x00\xa1\x03\x00":
        parts, pos = [], 0
        while pos + 8 <= len(d):
            cid, size = struct.unpack_from('<II', d, pos)
            if cid == 0x3A100:
                parts.append(jdlz(d[pos + 8:pos + 8 + size]))
            pos += 8 + size
        return b''.join(parts)
    return d


def chunks(d, start, end):
    pos = start
    while pos + 8 <= end:
        cid, size = struct.unpack_from('<II', d, pos)
        fin = pos + 8 + size
        if fin > end:
            return
        yield cid, pos + 8, fin
        pos = fin


def find(d, start, end, want, depth=0):
    for cid, a, b in chunks(d, start, end):
        if cid == want:
            yield a, b
        elif cid & 0x80000000 and depth < 8:
            yield from find(d, a, b, want, depth + 1)


def record(r):
    """One TextureInfo record (0x7C bytes): stored name, name hash, width, height."""
    name = r[0x0C:0x24].split(b'\0')[0].decode('latin-1')
    h = struct.unpack_from('<I', r, 0x24)[0]
    w, hh = struct.unpack_from('<hh', r, 0x44)
    return dict(hash=f'{h:08x}', name=name, w=w, h=hh)


def textures(path):
    """Every texture in every TPK of the file: dicts with hash (8 hex digits), name (as stored), w, h."""
    d = load(path)
    out = []
    for ta, tb in find(d, 0, len(d), TPK):
        sub = {cid: (a, b) for cid, a, b in chunks(d, ta, tb)}
        if INFO not in sub:
            continue
        info = {cid: (a, b) for cid, a, b in chunks(d, *sub[INFO])}
        if 0x33310004 not in info and 0x33310003 in info:
            # Compressed TPK (the HUD styles): 33310003 = 24-byte rows (hash, offset from the TPK chunk, packed size,
            # unpacked size, flags, 0); each block is [image | TextureInfo 0x7C | format 0x20], JDLZ or HUFF packed.
            ca, cb = info[0x33310003]
            for i in range((cb - ca) // 24):
                _h, off, csz, _usz, _f, _z = struct.unpack_from('<6I', d, ca + i * 24)
                blk = d[ta - 8 + off:ta - 8 + off + csz + 16]
                if blk[:4] != b"JDLZ":
                    continue   # HUFF: not decoded here (see the module note)
                try:
                    blk = jdlz(blk)
                except ValueError:
                    continue
                if len(blk) >= 0x9C:
                    out.append(record(blk[-0x9C:-0x20]))
            continue
        if 0x33310004 not in info:
            continue
        ia, ib = info[0x33310004]
        for i in range((ib - ia) // 0x7C):
            out.append(record(d[ia + i * 0x7C:ia + (i + 1) * 0x7C]))
    return out


def main(argv):
    for f in argv:
        for t in textures(f):
            print(f"{t['hash']}\t{t['name']}\t{t['w']}x{t['h']}\t{Path(f).name}")


if __name__ == '__main__':
    main(sys.argv[1:])
