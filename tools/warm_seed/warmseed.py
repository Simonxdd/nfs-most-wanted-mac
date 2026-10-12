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
    # 2.0.4: the Xbox 360 Stuff Pack front end has its own menus (and shaders): one short drive boots to its main
    # menu and stays there. Track None = menus only (the race drives above use the PC menus, whose path PREFIX is).
    ("x360-menus", None, {"x360_frontend": 1}, {}),
]
# Lines only the world draws (the first scenery item, the weather cycle, light pools with items; that line also
# prints "0 terrain-piece items" in the menus): a drive that logged one reached the race.
WORLD = re.compile(r"core\.nfsmw: (Xbox scenery lights: \d+ draw items so far|Weather: |Xbox light pools: [1-9]\d* terrain)")
# From boot to the Quick Race circuit list on a fresh profile, every step seen in frames (run 28,
# harness/r28/pathcheck.py): skip the intro movies, dismiss the title, answer "Create a new alias?" (No is highlighted),
# Right Right = Quick Race, Enter; the Quick Race menu opens on Quick Play (a random race, drag included): Right to the
# next entry, Enter, Enter on Circuit (the first mode) = the circuit list. (The earlier path skipped the alias answer
# and entered Challenge Series.)
PREFIX = [(20000, None), (0, "RETURN"), (12000, None), (0, "RETURN"), (8000, None), (0, "RETURN"), (8000, None),
          (0, "RIGHT"), (2000, None), (0, "RIGHT"), (2000, None), (0, "RETURN"), (5000, None), (0, "RIGHT"),
          (2000, None), (0, "RETURN"), (5000, None), (0, "RETURN"), (5000, None)]
KEY = re.compile(r"^[0-9a-f]{64}$")


def say(msg):
    print("shader warm-up: " + msg, flush=True)


def script(track, race_ms):
    lines = []
    if track is None:                      # menus only: boot, skip the intro and title, answer the profile question
        return "wait 20000\n" + "".join(f"key RETURN down\nwait 160\nkey RETURN up\nwait {w}\n" for w in (12000, 30000)) + "quit\n"

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
    # hold the accelerator (automatic gearbox): the car moves along the track, so more of it is drawn (run 28: with
    # no driver the car sat on the start line for the whole race)
    lines += ["key UP down", f"wait {race_ms}", "key UP up", "quit"]
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
               "core.nfsmw/height": 720, "core.nfsmw/weather": 0,
               "core.nfsmw/x360_frontend": 0})     # the race drives' menu path (PREFIX) is the PC front end's
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
    n = sum(1 for k in order.read_text().split() if KEY.match(k)) if order.is_file() else 0
    world = bool(WORLD.search((run / "run.log").read_text(errors="replace")))
    return n, world


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
    done, raced, unconfirmed = [], 0, []
    for i, (name, track, settings, extra) in enumerate(rows):
        run = work / name
        mark = run / "done"
        # run 28 review F2: a drive is reused only if the shaders' sources AND its own script and settings are the
        # same (a corrected menu path must drive again; the earlier marks keyed only the shader sources)
        mid = hashlib.sha256(f"{fp}|{script(track, a.race_seconds * 1000)}|{sorted(settings.items())}|"
                             f"{sorted(extra.items())}".encode()).hexdigest()
        if mark.is_file() and mark.read_text().split()[:1] == [mid]:
            done.append(run / "ShaderWarm")
            raced += track is not None and mark.read_text().split()[2:3] == ["race"]
            continue
        say(f"drive {i + 1} of {len(rows)} ({name})")
        n, world = drive(exe, core, run, track, settings, extra, a.race_seconds)
        if n == 0:
            say(f"drive {name} recorded nothing (see {run / 'run.log'}); left out")
            continue
        if track is not None:
            if world:
                raced += 1
            else:
                unconfirmed.append(name)
                say(f"drive {name}: the race was not seen in its log (menus only?); kept")
                if raced == 0:
                    # run 28: the first race drive never reached the world: the menu path does not work on this copy
                    # of the game, so the other drives would only repeat the menus. Stop (Setup goes on without it).
                    raise SystemExit("shader warm-up: the first drive did not reach a race (see "
                                     f"{run / 'run.log'}); skipped, the first drive will stutter once")
        mark.write_text(f"{mid} {n} {'race' if world else 'menus'}\n")
        done.append(run / "ShaderWarm")
    if not done:
        raise SystemExit("shader warm-up: no drive recorded anything; skipped (the first drive will stutter once)")
    libs, pipes = merge(done, out)
    races = sum(1 for r in rows if r[1] is not None)
    say(f"seed ready ({libs} libraries, {pipes} pipelines from {len(done)} of {len(rows)} drives; races reached "
        f"{raced} of {races})")


if __name__ == "__main__":
    main()
