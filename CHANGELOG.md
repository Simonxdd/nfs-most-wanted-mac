# Changelog

## 2.0.4 — the Xbox 360 front end

- **The Xbox 360 Stuff Pack's front end, optional**: the Xbox 360 menus, title and loading art and garages, from your
  own download of the Stuff Pack. Put the unzipped *Easy Installation* folder in your game folder (or choose it in the
  Setup app) and build. Only the pack's front-end files are used, each one checked; the setup lists everything it
  leaves out. Your game folder is never changed.
- **F10 → Xbox 360 → Xbox 360 front end** turns it off (the PC menus come back at the next start); the *PC* look
  starts with the PC front end too.
- **Shader warm-up in the setup**: the setup now prepares the game's shaders on your Mac (about 25 minutes, offscreen
  Quick Races, no window), so the first drive no longer stutters while they are made. Optional (`--no-shader-seed`, or
  the checkbox in the Setup app).
- **Mods folder for texture mods**: drop a texture mod's folder (`.dds` files: HUD art, fonts, logos, road and
  building textures) into `~/Library/Application Support/NFS Most Wanted Native/Mods` and start the game; no rebuild.
  Files replace the game textures with the same name, in every Look; several mods load in A to Z order and a folder
  starting with `_` is skipped. `Mods/mods-report.txt` lists what loaded and why anything did not. The setup makes a
  list of your game's texture names (numbers and sizes only) so the report can check names and shapes. F10 →
  Graphics → Advanced → **Player mods** turns it off. See the companion guide's *Optional mods*.
- **If the game won't start, it says why**: a message with the reason and a **Show Log** button when the start stops
  before the game or the game quits within its first seconds; every start's log now begins with the app version,
  macOS and Mac model, for reports.
- **Locked game files no longer block an update**: if some files in your game folder have the Finder *Locked* flag,
  the app's own copies are unlocked, so building again (or moving the app to the Trash) works. Your game folder is
  not changed.

## 2.0.3 — the game's own movies

- **The PC game's own movies play**: the EA logo, the PSA and the intro at start-up, and the story scenes, tutorials
  and Blacklist videos in the career, from the `MOVIES` folder of your game. They never played before: the game asks
  for the US (`_ntsc`) movie names, and most PC copies have the European (`_pal`) ones, so it found nothing. The app
  now plays either, natively (the same movie player as the Xbox 360 movies, now with the EA movie format), with the
  picture zoomed to fill a widescreen display as on PC.
- **Converted Xbox 360 movies** (the community *Xbox 360 Converted Movies* pack, in your game's `MOVIES` folder) play
  too, fitted to the screen, with the EA HD bumper at start-up.
- Your own Xbox 360 disc movies (2.0.2) still come first for every title they cover.

## 2.0.2 — Xbox 360 movies

- **Your own Xbox 360 movies, optional.** Put your copy of the US Xbox 360 disc's movies in an `X360-Movies` folder
  in your game folder (or choose it in the Setup app) and build: the game plays the Xbox 360 intro with the EA HD
  bumper and the story scenes from them. Only the exact original files are
  used (each one's size and checksum are checked); the movies are not part of this download.
- Without them, the boot keeps the PC game's own movie order (2.0 asked for the Xbox 360-only EA HD bumper there). The
  PC game's own movies still don't play in this version; the guide now says so.
- Setup's image library (Pillow) updated to 12.3.0 (security fixes).

## 2.0.1 — fixes

- **Upscaling no longer darkens the picture.** With *Upscaling* on (Quality or Performance), the mid-tones were about
  10 % darker than at native resolution: ambient occlusion read a stair-stepped copy of the upscaled scene's depth
  and shaded every slope (roads, walls). The depth copy is now smooth on surfaces and sharp at edges; the upscaled
  picture matches native brightness. Native resolution is unchanged.
- **A rare crash at start-up is fixed** (about 1 start in 900, a few seconds after the sound starts; the game closed
  without a report): the list of the game's threads could be changed while another thread was reading it.
- **Updating rebuilds the game from scratch once.** The Setup kept the earlier version's compiled files to save time,
  and in some cases parts of an earlier build were mixed into the new one (the likely cause of a black screen with a
  nearly empty F10 menu after updating to 2.0.0). A new version now never reuses them.
- A crash message now also names where in the app's own code the fault happened, to make reports easier to follow.

## 2.0.0 — Definitive Edition

- **Look** (Definitive / Xbox 360 / PC) replaces *Overall look*; every finished improvement follows it, and each keeps
  its own switch under Advanced.
- **Definitive look, extended:** smooth leaves and fences, soft particles, natural wet roads, road sun sheen, car
  reflections, building glass, water, wall depth, ambient occlusion, sun rays, lamp glow, softer
  shadows, garage floor shadows, denser tyre smoke and the Stuff Pack's extra smoke layers; a golden colour filter
  (Auto / On / Off).
- **Day and weather cycles** (60 and 20 minutes; both optional; the weather starts at a random point each session).
- **Real HDR** picture, tone-mapped to your TV's range; with HDR on, 4x anti-aliasing runs as 2x.
- **Anti-aliasing** is Off / 2x / 4x (default Off; a saved 4x from 1.0 is moved to Off once).
- Settings menu in seven pages (Graphics, Display, Audio, Xbox 360, PC, Mods, Controls); an Xbox 360 page; rumble
  style, cobbles and metal rumble and a crash fade; Dolby Digital on by default for new profiles. Settings still in
  development are listed, marked *(in development)*, and stay off.
- More Extra Options: always rain, all police on the minimap, bounty / prologue / Busted-scene fixes, one-lap
  percent, HUD on look-back, subtitles, heat keys, World Map in races, no catch-up.
- Retired: the scene-depth blur switch (always on), mirror glow, grass blades.
- Setup: the rebuilt effects are now assembled at setup from `effects/` (nothing prebuilt in the repository), the
  light pools also cover the tunnel props, and the Xbox 360 Stuff Pack's texture list is converted for the new
  *Xbox 360 textures* setting.
- Known issues: the first minutes of driving after a new build may hitch once per new shader; rain at 4K with HDR and
  2x anti-aliasing is near the GPU's limit on an M5 Pro; the mouse pointer is offset after resizing the window to
  another aspect ratio. No HD texture pack yet (a later release makes it on your Mac).

## 1.0.3 — setup fix for newer Pythons

- **Setup stopped with the pypi.org message on Macs with Python 3.13 or later** (for example from Homebrew), although
  the internet was fine (#5): one build tool has no ready-made package for those Pythons. The setup now uses Python
  3.11 or 3.12 (or downloads its own 3.12), remakes the environment an earlier run left, installs ready-made packages
  only, and says plainly whether the network or something else stopped it (the full output is in `build/pip.log`).
- The Setup app has a **Copy the log** button for bug reports.

## 1.0.2 — change any button

- **Remap your controller in the game's own menu:** Options → Controls, press R2 for the Controller page, pick an
  action and press the button you want. Changes stay (1.0.1 put its own layout back, so a remapped button returned
  as "Button 1") and are saved with your profile. Circle puts this port's layout back.
- The Controls screen names your controller's buttons (*Cross / A*, *R2 / RT*, *Left Stick Up*) and, on the keyboard
  page, your keys (it showed none).
- A trigger is a full analog pedal for any action you give it.
- The Setup app shows its Read Me itself (**Read Me** button), for Macs that will not open the text file on the disk
  image.

## 1.0.1 — setup fixes

- **Setup on Macs with an Intel-only Python** (for example an old Homebrew in `/usr/local` from an Intel Mac): the setup
  stopped at Ghidra with "unable to load libxcrun ... need 'x86_64'" on macOS 27, whose Command Line Tools have no Intel
  half. It now uses only a native Apple Silicon Python (or its own), and rebuilds an environment an earlier run made.
- **Blocked connection to pypi.org:** the setup retries longer and, if the package index stays unreachable, says so
  plainly (VPN, proxy or firewall app) instead of pip's "No matching distribution found". Checking the game folder
  (`--check-only`) no longer downloads anything.
- **Older FFmpeg installed elsewhere** (for example in `/usr/local/include`): the build always uses the bundled FFmpeg's
  own headers (thanks DarthMDev).

If 1.0 stopped partway for you, open the new Setup and click **Build** again: it continues where it can.

## 1.0.0 — first public release

The native Mac port of Need for Speed: Most Wanted (2005), with the setup kit that builds it from the player's own
PC game folder. See the README for the feature list.

- **Setup app:** a small Mac app (on the release page) that builds the game from your own game folder: choose the
  folder, click Build, then open the game. It installs Apple's Command Line Tools if needed, shows the progress and
  keeps the Mac awake. `./setup.sh` in Terminal does the same.
- Rebuilt post-process effects (glow, auto brightness, motion blur, depth of field, edge darkening, shadow lift)
  run on the port's own effects host and need only the PC game.
- Light pools are built at setup from the PC game's own lamps and effects.
- Optional: TexWizard texture packs (for example the Xbox 360 Stuff Pack's textures) and the XenonEffects sparks
  and light trails, converted by the setup from the player's own copies.
- Skip the music track while driving (T, or L3 on a controller), as Extra Options' SkipTrackAnywhere; both on by
  default.
- Extra Options camera and screenshot keys (all off by default): freeze camera (F9 or Pause), free camera
  (Backspace), light keys (H, O), save/load positions; plus unlock everything and barrier removal.
- Fix: a draw using a volume texture with a level-of-detail clamp no longer shares its state version with the
  pipeline, so the render queue can no longer carry its raised mip level into the next draw (or reuse an older
  table for it).
