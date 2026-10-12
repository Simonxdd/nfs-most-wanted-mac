"""2.0.3: the game's own VP6 movies play through the native movie adapter (native/usa_movies.cpp) and the kit's
FFmpeg. These check the wiring that a build alone would not catch: FFmpeg has the EA container, VP6 and EA audio,
the notice lists them, Launch turns PC movies on for every player, and the adapter's title list covers every PC
movie name. Needs no movie files."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PC_TITLES = (["attract_movie", "ealogo", "psa"] + [f"blacklist_{i:02d}" for i in range(1, 16)] +
             [f"{t}_tutorial" for t in ("bounty", "drag", "pursuit", "speedtrap", "tollbooth")] +
             [f"storyfmv_{t}" for t in ("bla134", "bus12", "cro06_coh06a", "her136", "pin11", "rac01", "rap30",
                                        "raz08", "saf25")])


def test_ffmpeg_has_the_ea_movie_parts():
    for path in ("kit/cmake/Dependencies.cmake", "kit/third_party/ffmpeg/NOTICE.md"):
        text = (ROOT / path).read_text()
        assert "--enable-decoder=vp6,adpcm_ea_r3" in text and "--enable-demuxer=ea" in text, path


def test_launch_turns_pc_movies_on_for_everyone():
    launch = (ROOT / "tools/setup_kit/defaults/Launch").read_text()
    on = [line for line in launch.splitlines() if "RECOMP_NFSMW_PC_MOVIES" in line and not line.lstrip().startswith("#")]
    assert on == ["export RECOMP_NFSMW_PC_MOVIES=1"]   # unconditional, outside the Xbox 360 movie branch


def test_every_pc_movie_title_is_on_the_adapters_list():
    names = set(re.findall(r'"([a-z0-9_]+)_english_ntsc\.wmv"', (ROOT / "native/usa_movie_manifest.h").read_text()))
    assert len(PC_TITLES) == 32
    assert set(PC_TITLES) <= names
    assert names - set(PC_TITLES) == {"eahd_bumper"}
