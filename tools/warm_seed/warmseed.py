#!/usr/bin/env python3
"""The shader warm-up (2.0.4): made on this Mac by the setup, so the first drive does not stutter.

    warmseed.py --cache DIR --out DIR [--drives N] [--race-seconds S]

The game's shaders are translated to Metal while it draws; each new one costs a short hold the first time it is seen.
The warm-up records them ahead: the built game runs offscreen through a few Quick Races with its own AI driving, in
the settings combinations a player is most likely to use (rain, HDR, the Xbox 360 and PC looks, the Advanced
effects), and every shader it translates is kept. The packager puts the result in the app
(Contents/Resources/ShaderWarmSeed); at start the game compiles them in the background before they are needed.
The shaders are your Mac's own: made here from your game, never downloaded. Resumable: run it again and it carries on
from the drives already done. Progress lines: "shader warm-up: drive N of M" and "shader warm-up: seed ready (...)".
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# One drive per row: (name, Quick Race circuit index, mod settings, extra environment). The settings combinations
# are the ones the developers' own warm-up records (weather 0 unless the row is a rain drive; HDR never with 4x, as
# the game caps HDR at 2x). Circuits rotate through the three boroughs.
DRIVES = [
    ("rain-hdr", 16, {"weather": 1, "antialiasing": 1}, {"NFSMW_TEST_WEATHER_START": "0.65", "RECOMP_TEST_HDR_FLOAT": "1"}),
    ("adv-b", 5, {"lit_smoke": 1, "atmosphere": 1, "vehicle_lights": 1, "antialiasing": 2, "natural_water": 2}, {}),
    ("x360", 8, {"look": 1}, {}),
    ("pc", 12, {"look": 2}, {}),
    ("hdr", 14, {"antialiasing": 1}, {"RECOMP_TEST_HDR_FLOAT": "1"}),
    ("default", 0, {}, {}),
    ("adv-a", 2, {"lit_smoke": 1, "atmosphere": 1, "vehicle_lights": 1, "mirror_shadows": 1, "mirror_pools": 1,
                  "mirror_msaa": 1, "mirror_glass": 50, "antialiasing": 1, "natural_water": 1}, {}),
    ("rain", 21, {"weather": 1}, {"NFSMW_TEST_WEATHER_START": "0.65"}),
]
# From boot to the Quick Race circuit list on a fresh profile: skip the intro movies and the title, answer the
# profile question, Quick Race, Circuit (the HD texture tour's path).
PREFIX = [(20000, None), (0, "RETURN"), (12000, None), (0, "RETURN"), (8000, None), (0, "RIGHT"), (1600, None),
          (0, "RIGHT"), (2500, None), (0, "RETURN"), (5000, None), (0, "RIGHT"), (2000, None), (0, "RETURN"),
          (5000, None), (0, "RETURN"), (5000, None)]
KEY = re.compile(r"^[0-9a-f]{64}$")


def say(msg):
    print("shader warm-up: " + msg, flush=True)


def script(track, race_ms):
    lines = []

    def press(k, after):
        lines.extend([f"key {k} down", "wait 160", f"key {k} up", f"wait {after}"])
    for wait, key in PREFIX:
        if key:
            press(key, 0)
        else:
            lines.append(f"wait {wait}")
    for _ in range(track):
        press("RIGHT", 1100)
    press("RETURN", 5000)    # the race options
    press("RETURN", 5000)    # the car list
    press("RETURN", 2500)    # this car: the transmission question
    press("RETURN", 20000)   # Auto: the race loads and starts
    lines += [f"wait {race_ms}", "quit"]
    return "\n".join(lines) + "\n"


def core_dir(work):
    """The built core mod plus the files the setup made from your game (light pools, texture-pack map, packs)."""
    core = work / "core"
    shutil.rmtree(core, ignore_errors=True)
    shutil.copytree(ROOT / "build/recomp/mods/core/nfsmw", core / "nfsmw")
    gen = ROOT / "build/generated"
    if gen.is_dir():
        for p in gen.rglob("*"):
            if p.is_file():
                t = core / "nfsmw" / p.relative_to(gen)
                t.parent.mkdir(parents=True, exist_ok=True)
                if subprocess.run(["/bin/cp", "-c", str(p), str(t)], capture_output=True).returncode != 0:
                    shutil.copyfile(p, t)
    return core


def smoke():
    exe = ROOT / "build/recomp/pop_smoke"
    if not exe.exists():
        say("building the offscreen runner (a few minutes, once per build)")
        subprocess.run([sys.executable, str(ROOT / "tools/build.py"), "--target", "smoke"], cwd=ROOT, check=True,
                       stdout=sys.stdout, stderr=subprocess.STDOUT)
    if not exe.exists():
        raise SystemExit("the offscreen runner (build/recomp/pop_smoke) could not be built")
    return exe


def drive(exe, core, run, track, settings, extra, race_s):
    shutil.rmtree(run, ignore_errors=True)
    prof = run / "profile"
    prof.mkdir(parents=True)
    d = ROOT / "tools/setup_kit/defaults"
    shutil.copyfile(d / "registry.json", prof / "registry.json")
    ms = json.loads((d / "mod-settings.json").read_text())
    ms.update({"core.nfsmw/unlock_all": 1, "core.nfsmw/surround_audio": 0, "core.nfsmw/width": 1280,
               "core.nfsmw/height": 720, "core.nfsmw/weather": 0})
    ms.update({"core.nfsmw/" + k: v for k, v in settings.items()})
    (prof / "mod-settings.json").write_text(json.dumps(ms, indent=1, sort_keys=True) + "\n")
    (run / "drive.script").write_text(script(track, race_s * 1000))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RECOMP_", "POPM_", "NFSMW_"))}
    env.update(RECOMP_PROFILE_DIR=str(prof), RECOMP_REGISTRY=str(prof / "registry.json"),
               RECOMP_CORE_MODS_DIR=str(core), RECOMP_SMOKE_SECONDS=str(race_s + 150),
               RECOMP_HOST_PRESENT_PACING="duration", RECOMP_D3D9_SCALE="1", SDL_AUDIO_DRIVER="dummy",
               RECOMP_D3D9_SHADER_WARM_SEED="", RECOMP_D3D9_SHADER_WARM_DIR=str(run / "ShaderWarm"), **extra)
    with (run / "run.log").open("w") as log:
        p = subprocess.Popen([str(exe), "--script", str(run / "drive.script")], cwd=ROOT, env=env, stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            p.wait(timeout=race_s + 240)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, 15)
            p.wait(timeout=30)
    order = run / "ShaderWarm/order.txt"
    return sum(1 for k in order.read_text().split() if KEY.match(k)) if order.is_file() else 0


def merge(folders, out):
    """Union of the drives' recordings in first-use order; each source kept only when its file is there."""
    tmp = out.with_name(out.name + ".staging")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    order, pipes, seen = [], [], set()
    for d in folders:
        if not (d / "order.txt").is_file():
            continue
        for k in (d / "order.txt").read_text().split():
            if KEY.match(k) and k not in seen and (d / f"{k}.msl").is_file():
                shutil.copyfile(d / f"{k}.msl", tmp / f"{k}.msl")
                seen.add(k)
                order.append(k)
        if (d / "pipelines.txt").is_file():
            pipes += [l for l in (d / "pipelines.txt").read_text().splitlines() if l.strip()]
    pipes = list(dict.fromkeys(pipes))
    (tmp / "order.txt").write_text("".join(k + "\n" for k in order))
    (tmp / "pipelines.txt").write_text("".join(l + "\n" for l in pipes))
    stamp = [f"fingerprint {fingerprint()}",
             f"generated {time.strftime('%Y-%m-%d %H:%M:%S')} from {len(folders)} drives on this Mac (setup)",
             "source setup", f"libraries {len(order)} pipelines {len(pipes)}"] + [f"{h}  {f}" for f, h in parts()]
    (tmp / "stamp.txt").write_text("\n".join(stamp) + "\n")
    shutil.rmtree(out, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(out)
    return len(order), len(pipes)


def parts():
    """The shader generator's sources: a seed belongs to the build made from exactly these."""
    files = ["kit/dx/d3d9_shadergen.cpp", "kit/dx/d3d9_msl.h", "kit/dx/d3d9_shader.cpp", "kit/dx/d3d9_shader.h"]
    return [(f, hashlib.sha256((ROOT / f).read_bytes()).hexdigest()) for f in files if (ROOT / f).is_file()]


def fingerprint():
    return hashlib.sha256("".join(f"{h}  {f}\n" for f, h in parts()).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, default=ROOT / "build/warm")
    ap.add_argument("--out", type=Path, default=ROOT / "build/warm/seed")
    ap.add_argument("--drives", type=int, default=len(DRIVES))
    ap.add_argument("--race-seconds", type=int, default=150)
    a = ap.parse_args()
    cache, out = a.cache.expanduser().resolve(), a.out.expanduser().resolve()
    rows = DRIVES[:max(0, min(a.drives, len(DRIVES)))]
    if not rows:
        say("no drives asked for; nothing to do")
        return
    work = cache / "drives"
    work.mkdir(parents=True, exist_ok=True)
    fp = fingerprint()
    exe = smoke()
    core = core_dir(cache)
    done = []
    for i, (name, track, settings, extra) in enumerate(rows):
        run = work / name
        mark = run / "done"
        if mark.is_file() and mark.read_text().split()[:1] == [fp]:
            done.append(run / "ShaderWarm")
            continue
        say(f"drive {i + 1} of {len(rows)} ({name})")
        n = drive(exe, core, run, track, settings, extra, a.race_seconds)
        if n == 0:
            say(f"drive {name} recorded nothing (see {run / 'run.log'}); left out")
            continue
        mark.write_text(f"{fp} {n}\n")
        done.append(run / "ShaderWarm")
    if not done:
        raise SystemExit("shader warm-up: no drive recorded anything; skipped (the first drive will stutter once)")
    libs, pipes = merge(done, out)
    say(f"seed ready ({libs} libraries, {pipes} pipelines from {len(done)} of {len(rows)} drives)")


if __name__ == "__main__":
    main()
