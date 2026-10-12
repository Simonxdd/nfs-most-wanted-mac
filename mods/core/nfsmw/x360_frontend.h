/* 2.0.4: the Xbox 360 Stuff Pack's front end (menus, title and loading art, garages) from the player's own download.
 * Setup (tools/setup_kit/x360_stuff.py) copies the pack's exact FRONTEND/FrontB.lzc, FRONTEND/PLATFORMS/*.BIN,
 * GLOBAL/GlobalB.lzc and its GLOBAL/GlobalMemoryFile.bin beside the mod in x360fe/; this pushes that folder as a file
 * overlay, so the game opens them instead of its own (the game folder is never changed). Off (setting, or Look = PC
 * at start): the PC front end. Overlays are only accepted during mod init, so the switch applies at the next start. */
static void x360fe_init(const PopModApi *api) {
    if (!setting("x360_frontend", 1)) { api->log(api, "core.nfsmw: Xbox 360 front end off (setting or Look PC); PC front end"); return; }
    char path[2048];
    snprintf(path, sizeof path, "%s/x360fe/FRONTEND/FrontB.lzc", api->mod_dir(api));
    FILE *f = fopen(path, "rb");
    if (!f) { api->log(api, "core.nfsmw: Xbox 360 front end: no x360fe folder beside the mod (Stuff Pack not added); PC front end"); return; }
    fclose(f);
    snprintf(path, sizeof path, "%s/x360fe", api->mod_dir(api));
    uint32_t layer = 0;
    if (api->overlay_push(api, path, &layer) != POP_OK) { api->log(api, "core.nfsmw: Xbox 360 front end: overlay refused; PC front end"); return; }
    api->log(api, "core.nfsmw: Xbox 360 front end on (Stuff Pack: FrontB, GlobalB, garages)");
}
