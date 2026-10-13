/* R3 run 29 modding stage 1 (review/run29/modding-design.md; owner D-119, D-166): changed game files from
 * <profile>/DataMods, served through the kit's file overlay (profile > the layers pushed last > ... > the game folder;
 * writes never reach the game folder). The scan and the report are datamods_scan.h; this pushes one layer per mod
 * (loose files first, then the mod folders A to Z, so a later folder wins) AFTER the Stuff Pack front end, so a data
 * mod wins over it too. Overlays are only accepted during mod init: a change applies at the next start. Setting
 * data_mods (on by default since 3.0); RECOMP_DATAMODS_DIR (the harness) names another folder. */
#include "datamods_scan.h"
static void datamods_init(const PopModApi *api) {
    if (!setting("data_mods", 0)) { api->log(api, "core.nfsmw: data mods off (setting)"); return; }
    const char *dd = getenv("RECOMP_DATAMODS_DIR"), *pd = getenv("RECOMP_PROFILE_DIR"), *exe = getenv("RECOMP_EXE");
    char dir[PATH_MAX], game[PATH_MAX];
    if (dd && *dd) snprintf(dir, sizeof dir, "%s", dd);
    else if (pd && *pd) snprintf(dir, sizeof dir, "%s/DataMods", pd);
    else { api->log(api, "core.nfsmw: data mods: no profile folder; nothing loaded"); return; }
    const char *slash = exe ? strrchr(exe, '/') : NULL;
    snprintf(game, sizeof game, "%.*s", slash ? (int)(slash - exe) : 0, slash ? exe : "");
    static dm_scan s;
    dm_scan_dir(&s, dir, game[0] ? game : NULL);
    dm_write_report(&s, dir, game[0] ? game : NULL);
    char line[PATH_MAX + 256];
    snprintf(line, sizeof line, "core.nfsmw: data mods: %s: %d files in %d layers (%s), %d refused, %d save-risk; report "
             "DataMods/datamods-report.txt", dir, s.n_files, s.n_layers, s.has_loose ? "loose files first" : "no loose files",
             s.count[DM_REFUSED], s.count[DM_RISK] + s.count[DM_RISK_SOFT]);
    api->log(api, line);
    for (int i = 0; i < s.n_files; ++i)
        if (s.files[i].status == DM_RISK) {
            snprintf(line, sizeof line, "core.nfsmw: data mods: WARNING %s from \"%s\": careers saved without it are "
                     "refused as \"damaged\" while it is in (and the reverse); remove the mod to get them back",
                     s.files[i].rel, s.layer_name[s.files[i].layer]);
            api->log(api, line);
        }
    for (int i = 0; i < s.n_layers; ++i) {
        uint32_t id = 0;
        const PopModStatus st = api->overlay_push(api, s.layer_dir[i], &id);
        snprintf(line, sizeof line, st == POP_OK ? "core.nfsmw: data mods: layer \"%s\" on (id %u)"
                                                 : "core.nfsmw: data mods: layer \"%s\" refused by the overlay (status %d)",
                 s.layer_name[i], st == POP_OK ? (unsigned)id : (unsigned)st);
        api->log(api, line);
    }
    dm_free(&s);
}
