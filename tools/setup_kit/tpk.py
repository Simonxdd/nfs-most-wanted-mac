#!/usr/bin/env python3
"""TPK texture list reader (2.0.4): the name hash, stored name and size of every texture in the game's texture packs,
read straight from your game files for tools/setup_kit/mod_names.py. No pixels are read or written.

Layout as observed in the retail PC files (chunk ids; named after TextureInfo as the community documents it):
  0xB3300000 TPK            0xB3310000 info: 33310001 header (name, file, hash), 33310004 TextureInfo records (0x7C
                                             each: +0x0C stored name (24 bytes), +0x24 name hash, +0x44 width, height)
                                             33310003 compressed-TPK rows (the HUD styles; see textures())
Whole files may be JDLZ-compressed (JDLZ 02 10); the minimap's files are a stream of 0x0003A100 chunks, one JDLZ
block each. The HUD styles (GLOBAL/HUDS_Custom_*.bin) are compressed TPKs whose blocks are JDLZ or HUFF packed; both
are decoded here (our own readers of the two formats).

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


class Bits:
    """Big-endian bit reader for HUFF."""
    def __init__(self, data):
        self.data, self.at, self.value, self.count = data, 0, 0, 0

    def peek(self, size):
        while self.count < size and self.at < len(self.data):
            self.value = (self.value << 8) | self.data[self.at]
            self.count += 8
            self.at += 1
        if self.count < size:
            return self.value << (size - self.count)
        return self.value >> (self.count - size)

    def get(self, size):
        result = self.peek(size)
        if self.count < size:
            raise ValueError("truncated bitstream")
        self.count -= size
        self.value &= (1 << self.count) - 1
        return result

    def number(self):
        width = 2
        while not self.get(1):
            width += 1
            if width > 30:
                raise ValueError("unbounded variable number")
        return self.get(width) + (1 << width) - 4


def huff(data, max_output_bytes=2**20):
    """HUFF (EA's canonical Huffman packing, 'HUFF' 01 10; its stored size excludes the 16-byte header, unlike JDLZ):
    the code table, the symbol order, the coded bytes with a clue symbol for runs and literals, and the optional
    delta passes. Every size and table is bounded; anything malformed raises ValueError."""
    if len(data) < 16 or data[:6] != b"HUFF\x01\x10":
        raise ValueError("unsupported HUFF header")
    expected, compressed = struct.unpack_from("<II", data, 8)
    if not 0 < expected <= max_output_bytes or not 0 < compressed <= len(data) - 16:
        raise ValueError("invalid bounded sizes")
    bits = Bits(data[16:16 + compressed])
    kind = bits.get(16)
    if kind & 0x100:
        bits.get(32 if kind & 0x8000 else 24)
    kind &= ~0x100
    if kind not in (0x30FB, 0x32FB, 0x34FB, 0xB0FB, 0xB2FB, 0xB4FB):
        raise ValueError(f"unsupported coding {kind:04x}")
    count = bits.get(32 if kind & 0x8000 else 24)
    if count != expected:
        raise ValueError("inner/outer output-size mismatch")
    clue = bits.get(8)
    entries, base, total = [], 0, 0
    for width in range(1, 17):
        base <<= 1
        n = bits.number()
        if total + n > 256 or base + n > 1 << width:
            raise ValueError("invalid canonical code table")
        entries.append((width, base, n))
        base += n
        total += n
        if base == 1 << width:
            break
    else:
        raise ValueError("incomplete canonical code table")
    used, symbols, current = set(), [], 255
    for _ in range(total):
        skip = bits.number() + 1
        if skip > 256 - len(used):
            raise ValueError("invalid symbol permutation")
        while skip:
            current = (current + 1) & 255
            if current not in used:
                skip -= 1
        used.add(current)
        symbols.append(current)
    quick = [None] * 256
    full, cursor = {}, 0
    for width, start, n in entries:
        for code in range(start, start + n):
            symbol = symbols[cursor]
            cursor += 1
            full[width, code] = symbol
            if width <= 8:
                for i in range(code << (8 - width), (code + 1) << (8 - width)):
                    quick[i] = width, symbol
    output = bytearray()
    while True:
        entry = quick[bits.peek(8)]
        if entry is None:
            for width in range(9, 17):
                symbol = full.get((width, bits.peek(width)))
                if symbol is not None:
                    bits.get(width)
                    break
            else:
                raise ValueError("unassigned code")
        else:
            width, symbol = entry
            bits.get(width)
        if symbol != clue:
            output.append(symbol)
        else:
            run = bits.number()
            if run:
                if not output or len(output) + run > expected:
                    raise ValueError("invalid repeat")
                output.extend(bytes([output[-1]]) * run)
            elif bits.get(1):
                break
            else:
                output.append(bits.get(8))
        if len(output) > expected:
            raise ValueError("output overrun")
    if len(output) != expected:
        raise ValueError("early terminator")
    if kind in (0x32FB, 0xB2FB, 0x34FB, 0xB4FB):
        value = second = 0
        for i, delta in enumerate(output):
            value = (value + delta) & 255
            second = (second + value) & 255
            output[i] = second if kind in (0x34FB, 0xB4FB) else value
    return bytes(output)


def unpack(blk):
    """One packed block: JDLZ or HUFF."""
    if blk[:4] == b"JDLZ":
        return jdlz(blk)
    if blk[:4] == b"HUFF":
        return huff(blk, max_output_bytes=64 << 20)
    raise ValueError(f"unknown block codec {blk[:4]!r}")


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
                try:
                    blk = unpack(d[ta - 8 + off:ta - 8 + off + csz + 16])
                except ValueError:
                    continue   # a block neither reader accepts: that texture is left out
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
