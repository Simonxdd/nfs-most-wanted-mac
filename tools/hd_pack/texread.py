"""The pixel path of the TPK reader, for the HD texture pack: every texture's TextureInfo fields (name, size, format,
mips, alpha usage, tiling bits, class) and its top level as RGBA, read straight from the player's game files.

The chunk layout, JDLZ and HUFF decoding come from tools/setup_kit/tpk.py (the 2.0.4 list reader, unchanged); this
module adds the format records and the image bytes:
  0xB3310000 info: 33310004 TextureInfo records (0x7C each), 33310005 format records (0x20: +0x14 the D3DFORMAT or
                   FourCC)
  0xB3320000 data: 33320002 data (image offsets from its 0x80-aligned start)
Key = name hash << 32 (the core mod's texture key, remaster_tex.h).

    python texread.py <file...>                 list textures (hash, name, size, format, mips, alpha usage, tiling)
    python texread.py --png <outdir> <file...>  also write each top level as <hash>.png (RGBA)
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'setup_kit'))
import tpk   # noqa: E402  (tools/setup_kit/tpk.py)

TPK, INFO, DATA = 0xB3300000, 0xB3310000, 0xB3320000
FOURCC = {b'DXT1': 'dxt1', b'DXT3': 'dxt3', b'DXT5': 'dxt5'}
D3DFMT = {21: 'a8r8g8b8', 22: 'x8r8g8b8', 41: 'p8'}


def level_bytes(fmt, w, h):
    if fmt == 'dxt1':
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
    if fmt in ('dxt3', 'dxt5'):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    if fmt == 'p8':
        return w * h
    return w * h * 4


def record(r, frec, blob, base, tpk_name):
    """One texture from its TextureInfo record r (0x7C), its format record (0x20 or None) and the bytes its image and
    palette placements count from (blob[base:])."""
    name = r[0x0C:0x24].split(b'\0')[0].decode('latin-1')
    h, cls, _parent, place, pplace, isz, psz = struct.unpack_from('<7I', r, 0x24)
    w, hh = struct.unpack_from('<hh', r, 0x44)
    fmt = None
    if frec is not None:
        tag = frec[0x14:0x18]
        fmt = FOURCC.get(tag) or D3DFMT.get(struct.unpack('<I', tag)[0])
    top = None
    if fmt and w > 0 and hh > 0:
        top = blob[base + place:base + place + level_bytes(fmt, w, hh)]
        if len(top) != level_bytes(fmt, w, hh):
            top = None
    pal = blob[base + pplace:base + pplace + psz] if (fmt == 'p8' and psz) else None
    return dict(key=f'{h:08x}00000000', hash=f'{h:08x}', name=name, w=w, h=hh, fmt=fmt or '?', mips=r[0x4E],
                tile=r[0x4F], alpha_usage=r[0x55], cls=f'{cls:08x}', tpk=tpk_name, top=top, palette=pal)


def textures(path):
    """Every texture in every TPK of the file: dicts with key, hash, name, w, h, fmt, mips, alpha_usage, tile, cls,
    tpk, top (the top level's bytes or None), palette (bytes or None)."""
    d = tpk.load(path)
    out = []
    for ta, tb in tpk.find(d, 0, len(d), TPK):
        sub = {cid: (a, b) for cid, a, b in tpk.chunks(d, ta, tb)}
        if INFO not in sub:
            continue
        info = {cid: (a, b) for cid, a, b in tpk.chunks(d, *sub[INFO])}
        ha, _hb = info.get(0x33310001, (0, 0))
        tpk_name = d[ha + 4:ha + 0x20].split(b'\0')[0].decode('latin-1') if ha else ''
        if 0x33310004 not in info and 0x33310003 in info:
            # Compressed TPK (the HUD styles): 33310003 = 24-byte rows (hash, offset from the TPK chunk, packed size,
            # unpacked size, flags, 0); each block is [image | TextureInfo 0x7C | format 0x20], JDLZ or HUFF packed.
            ca, cb = info[0x33310003]
            for i in range((cb - ca) // 24):
                _h, off, csz, _usz, _f, _z = struct.unpack_from('<6I', d, ca + i * 24)
                try:
                    blk = tpk.unpack(d[ta - 8 + off:ta - 8 + off + csz + 16])
                except ValueError:
                    continue
                if len(blk) < 0x9C:
                    continue
                r = blk[-0x9C:-0x20]   # the image sits at 0 in the block; placements describe the unpacked layout
                out.append(record(r, blk[-0x20:], blk, -struct.unpack_from('<I', r, 0x30)[0], tpk_name))
            continue
        if 0x33310004 not in info or DATA not in sub:
            continue
        data = {cid: (a, b) for cid, a, b in tpk.chunks(d, *sub[DATA])}
        if 0x33320002 not in data:
            continue
        ia, ib = info[0x33310004]
        fa, fb = info.get(0x33310005, (0, 0))
        da, _db = data[0x33320002]
        base = (da + 0x7F) & ~0x7F
        for i in range((ib - ia) // 0x7C):
            frec = d[fa + i * 0x20:fa + (i + 1) * 0x20] if fb - fa >= (i + 1) * 0x20 else None
            out.append(record(d[ia + i * 0x7C:ia + (i + 1) * 0x7C], frec, d, base, tpk_name))
    return out


def to_rgba(t):
    """The top level as a PIL RGBA image (DXT through PIL's DDS reader; P8 through its palette; ARGB swizzled)."""
    import io
    from PIL import Image
    fmt, w, h, top = t['fmt'], t['w'], t['h'], t['top']
    if top is None:
        raise ValueError('no image bytes')
    if fmt in ('dxt1', 'dxt3', 'dxt5'):
        hdr = [0] * 31
        hdr[0], hdr[1], hdr[2], hdr[3] = 124, 0x81007, h, w
        hdr[4] = len(top)
        hdr[6] = 1
        hdr[18], hdr[19] = 32, 0x4
        hdr[20] = int.from_bytes(fmt.upper().encode(), 'little')
        hdr[26] = 0x1000
        return Image.open(io.BytesIO(b'DDS ' + struct.pack('<31I', *hdr) + top)).convert('RGBA')
    if fmt == 'p8':
        pal = t['palette'] or bytes(1024)
        lut = [(pal[i * 4 + 2], pal[i * 4 + 1], pal[i * 4], pal[i * 4 + 3]) for i in range(len(pal) // 4)]  # B, G, R, A
        px = b''.join(bytes(lut[i]) if i < len(lut) else b'\0\0\0\0' for i in top)
        return Image.frombytes('RGBA', (w, h), px)
    if fmt in ('a8r8g8b8', 'x8r8g8b8'):
        im = Image.frombytes('RGBA', (w, h), top, 'raw', 'BGRA')
        if fmt == 'x8r8g8b8':
            im.putalpha(255)
        return im
    raise ValueError(f'no decoder for {fmt}')


def main(argv):
    out = None
    if argv[:1] == ['--png']:
        out, argv = Path(argv[1]), argv[2:]
        out.mkdir(parents=True, exist_ok=True)
    for f in argv:
        for t in textures(f):
            print(f"{t['hash']}\t{t['name']}\t{t['w']}x{t['h']}\t{t['fmt']}\tmips {t['mips']}\tau {t['alpha_usage']}"
                  f"\ttile {t['tile']}\t{t['tpk']}\t{Path(f).name}")
            if out and t['top'] is not None:
                try:
                    to_rgba(t).save(out / f"{t['hash']}.png")
                except Exception as e:   # recorded, the listing goes on
                    print(f'  png failed: {e}', file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1:])
