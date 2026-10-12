#!/usr/bin/env python3
"""The Xbox 360 Stuff Pack's front end (optional): the menus, title and loading art and the garages from the player's
own download of the Xbox 360 Stuff Pack (the unzipped "Easy Installation" folder). Only the pack's exact front-end
files are used: each one's path, size and SHA-256 must match FRONT_END below. They are copied as they are into an
overlay folder beside the game's mod (the game opens them instead of its own; the game folder is never changed). The
in-game switch "Xbox 360 front end" (F10 -> Xbox 360) turns them off at the next start.
Everything else in the download is named as not used (the Windows-only parts, the sound, shaders, the in-race HUD
files and the track bundle).

usage: x360_stuff.py <folder> <out folder>   <folder> = the "Easy Installation" folder, its Files folder, or any folder
                                             holding FRONTEND/FrontB.lzc; exit 0 when the front end was copied, 2 when
                                             no matching set was found (nothing is written then)
"""
import hashlib, shutil, subprocess, sys
from pathlib import Path

# The Stuff Pack's front-end files: GlobalB.lzc and its GlobalMemoryFile.bin only work as a pair (the game loads the
# memory file first; the pack's is a stub so its GlobalB chunks are used), so the set is used whole or not at all.
FRONT_END = {
    "FRONTEND/FrontB.lzc": (13895352, "893306a6292faee5360c691ba598e1c564c3bc5a138ca7376d8b5cb390ee1c3c"),
    "GLOBAL/GlobalB.lzc": (10339800, "46c800716026c9bda47b7e63fc301ba5b76ff6298728331e6ac11fcb4f67d70a"),
    "GLOBAL/GlobalMemoryFile.bin": (366, "da2df598dca42b3090df0d2abfc35b97dedf9030262c1a272d1100c9eee60e5f"),
    "FRONTEND/PLATFORMS/CAREER_SAFEHOUSE.BIN": (30793152, "e1de5ed2295ad4744d8bf3b2709356285cc04258343d106d5b66d109a6224a04"),
    "FRONTEND/PLATFORMS/PlatformCrib.BIN": (32331104, "368cdb853599ec93ea3e15325e78ed27cfcd7569e59b2d57041b7ef1e788b0ab"),
    "FRONTEND/PLATFORMS/CUSTOMIZATION_SHOP.BIN": (23209088, "6a9b061b4725341f3b1bd648bd4a742bd046308714892572f8418d5265725381"),
    "FRONTEND/PLATFORMS/CUSTOMIZATION_SHOP_BACKROOM.BIN": (23181360, "179a54f9a95af0cad4f9621276c32f8aee0210add723808ea816c418f2275446"),
    "FRONTEND/PLATFORMS/CAR_LOT.BIN": (27175072, "5588a60b44d8b5fdb5ea64fa212648c26c6671c61caf6bd1bd62d7e941bdd41d"),
}
# What the rest of the download is, by its top folder (or file), for the "not used" lines.
NOT_USED = [
    ("TRACKS/TexWizardX360", "the Xbox 360 textures (added by the texture step when they are in your game's TRACKS folder)"),
    ("GLOBAL/XenonEffects.tpk", "sparks and light trails (added by the XenonEffects step from your game's scripts folder)"),
    ("scripts/XenonEffects.tpk", "sparks and light trails (added by the XenonEffects step from your game's scripts folder)"),
    ("scripts", "the Windows add-on code (.asi): this port does that work itself"),
    ("SOUND", "the Xbox 360 sound and music: not used yet"),
    ("FX", "the Windows shaders: this port draws with its own"),
    ("GLOBAL/attributes.bin", "the game-data table: the game's own is kept"),
    ("GLOBAL", "the in-race HUD and dynamic texture files: the game's own are kept"),
    ("TRACKS", "the track bundle: the game's own is kept"),
    ("AlternativeShaders", "the alternative Windows shaders"),
    ("AlternativeVLT", "the alternative game-data table"),
    ("ProtonSupport", "the Linux (Proton) support files"),
    ("Previews", "the preview pictures"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def resolve_ci(base, rel):
    """base/rel with every part matched without regard to case; None when absent."""
    p = Path(base)
    for part in rel.split("/"):
        if not p.is_dir():
            return None
        hit = next((c for c in p.iterdir() if c.name.lower() == part.lower()), None)
        if hit is None:
            return None
        p = hit
    return p


def roots(folder):
    """Candidate folders holding FRONTEND/FrontB.lzc, in order: an X360-Stuff or "Easy Installation*" folder inside
    folder (a game folder), folder itself, its Files folder."""
    folder = Path(folder)
    out = []
    def add(c):
        if c and c.is_dir() and resolve_ci(c, "FRONTEND/FrontB.lzc") and c not in out:
            out.append(c)
    if folder.is_dir():
        for sub in sorted(folder.iterdir()):
            n = sub.name.lower()
            if sub.is_dir() and (n == "x360-stuff" or n.startswith("easy installation")):
                for c in roots(sub):
                    add(c)
    add(folder)
    add(resolve_ci(folder, "Files"))
    return out


def check(root, want=None):
    """([(rel, path)] matching, [rel] missing or different) for the front-end set."""
    want = FRONT_END if want is None else want
    ok, bad = [], []
    for rel, (size, digest) in want.items():
        p = resolve_ci(root, rel)
        if p and p.is_file() and p.stat().st_size == size and sha256(p) == digest:
            ok.append((rel, p))
        else:
            bad.append(rel)
    return ok, bad


def not_used(root, want=None):
    """[(what, count)] for every other file in the download (grouped by NOT_USED's first matching prefix)."""
    want = {k.lower() for k in (FRONT_END if want is None else want)}
    base = Path(root)
    tops = [base] + ([base.parent] if base.name.lower() == "files" else [])
    groups = {}
    for top in tops:
        for p in top.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            rel = p.relative_to(top).as_posix()
            if top == base.parent and rel.lower().startswith("files/"):
                continue
            if rel.lower() in want or top == base.parent and "/" not in rel:
                continue   # used, or the download's ReadMe / LICENSE
            what = next((w for k, w in NOT_USED if rel.lower() == k.lower() or rel.lower().startswith(k.lower() + "/")),
                        "other files")
            groups[what] = groups.get(what, 0) + 1
    return sorted(groups.items())


def copy(files, out):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    for rel, p in files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # An APFS clone when the disk allows it (no extra space), else a plain copy.
        if subprocess.run(["/bin/cp", "-c", str(p), str(dst)], capture_output=True).returncode != 0:
            shutil.copyfile(p, dst)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    folder, out = Path(sys.argv[1]), Path(sys.argv[2])
    cands = roots(folder)
    if not cands:
        print(f"Xbox 360 Stuff Pack front end: not added (no FRONTEND/FrontB.lzc in {folder}, its Files folder or an "
              "X360-Stuff / Easy Installation folder inside it)")
        sys.exit(2)
    root, ok, bad = None, [], []
    for c in cands:
        ok, bad = check(c)
        if not bad:
            root = c
            break
    if root is None:
        ok, bad = check(cands[0])
        print("Xbox 360 Stuff Pack front end: not added (these files are missing or not the pack's: "
              + ", ".join(bad) + "; the set is used whole or not at all)")
        sys.exit(2)
    copy(ok, out)
    print(f"Xbox 360 Stuff Pack front end: added (menus, title and loading art, garages: {len(ok)} files)")
    for what, n in not_used(root):
        print(f"  not used from the Stuff Pack: {what} ({n} file{'s' if n != 1 else ''})")


if __name__ == "__main__":
    main()
