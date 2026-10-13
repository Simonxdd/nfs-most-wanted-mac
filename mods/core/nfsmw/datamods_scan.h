/* R3 run 29 modding stage 1 (review/run29/modding-design.md 2): the DataMods folder scan, plain C with no mod API so
 * tests/test_datamods.py compiles and runs it on its own. <profile>/DataMods mirrors the game folder in two shapes:
 * loose (a top-level entry named like a game folder: DataMods/GLOBAL/...) and one folder per mod
 * (DataMods/<mod>/GLOBAL/...). Loose files are the lowest layer, then the mod folders in natural A to Z order (a later
 * one wins); a folder whose name starts with "_" or "." (and __MACOSX) is parked. Every file gets one status in
 * DataMods/datamods-report.txt: OK (replaces a game file), NEW (not in the game folder), SAVE-RISK (GLOBAL data that
 * changes which careers load: attributes.bin always; other GLOBAL .bin/.bun/.lzc "may"), SHADOWED (a later layer has
 * the same path), REFUSED (program files: the kit's overlay never serves them from a layer either), NOT USED (outside
 * the seven game folders). Nothing in DataMods is changed except the report. */
#ifndef NFSMW_DATAMODS_SCAN_H
#define NFSMW_DATAMODS_SCAN_H
#include <ctype.h>
#include <dirent.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

#define DM_MAX_MODS 32
#define DM_MAX_FILES 20000
#define DM_MAX_DEPTH 12
enum { DM_OK, DM_NEW, DM_RISK, DM_RISK_SOFT, DM_SHADOWED, DM_REFUSED, DM_NOTUSED, DM_NSTATUS };
static const char *const k_dm_status[DM_NSTATUS] = {"OK", "NEW", "SAVE-RISK", "SAVE-RISK", "SHADOWED", "REFUSED",
                                                    "NOT USED"};
static const char *const k_dm_folders[] = {"GLOBAL", "CARS", "TRACKS", "FRONTEND", "SOUND", "MOVIES", "LANGUAGES"};

typedef struct {
    int layer;            /* 0 = loose, 1.. = mod folders in load order */
    int status;
    char rel[512];        /* path inside the layer, '/' separated, as on disk */
} dm_file;
typedef struct {
    int n_layers;                             /* layer 0 is the loose layer when has_loose */
    int has_loose;
    char layer_name[DM_MAX_MODS + 1][256];
    char layer_dir[DM_MAX_MODS + 1][PATH_MAX];
    char ignored[DM_MAX_MODS * 2][256];
    int n_ignored, n_over_limit, truncated;
    dm_file *files;
    int n_files;
    int count[DM_NSTATUS];
} dm_scan;

static int dm_ieq(const char *a, const char *b) {
    for (; *a && *b; ++a, ++b)
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
    return *a == *b;
}
static int dm_is_game_folder(const char *name) {
    for (unsigned i = 0; i < sizeof k_dm_folders / sizeof k_dm_folders[0]; ++i)
        if (dm_ieq(name, k_dm_folders[i])) return 1;
    return 0;
}
static int dm_parked(const char *name) {
    return name[0] == '_' || name[0] == '.' || dm_ieq(name, "__MACOSX");
}
static int dm_refused(const char *rel) {
    static const char *const ext[] = {".exe", ".dll", ".asi", ".com", ".bat", ".cmd", ".scr", ".lnk", ".dylib", ".so",
                                      ".app", ".pkg"};
    const char *base = strrchr(rel, '/');
    base = base ? base + 1 : rel;
    const size_t n = strlen(base);
    for (unsigned i = 0; i < sizeof ext / sizeof ext[0]; ++i) {
        const size_t m = strlen(ext[i]);
        if (n > m && dm_ieq(base + n - m, ext[i])) return 1;
    }
    return 0;
}
/* Natural order: digit runs compare as numbers, the rest case-insensitively. */
static int dm_natural_cmp(const char *a, const char *b) {
    while (*a && *b) {
        if (isdigit((unsigned char)*a) && isdigit((unsigned char)*b)) {
            unsigned long x = strtoul(a, (char **)&a, 10), y = strtoul(b, (char **)&b, 10);
            if (x != y) return x < y ? -1 : 1;
            continue;
        }
        const int ca = tolower((unsigned char)*a), cb = tolower((unsigned char)*b);
        if (ca != cb) return ca < cb ? -1 : 1;
        ++a, ++b;
    }
    return *a ? 1 : *b ? -1 : 0;
}
static int dm_cmp_names(const void *x, const void *y) { return dm_natural_cmp((const char *)x, (const char *)y); }
/* SAVE-RISK: hard for GLOBAL/attributes.bin (the VLT database; run 28: careers refused as "damaged"); soft for other
 * GLOBAL data files except the four 2.0.4 proved safe. */
static int dm_save_risk(const char *rel) {
    if (strncasecmp(rel, "GLOBAL/", 7) || strchr(rel + 7, '/')) return 0;
    const char *f = rel + 7;
    if (dm_ieq(f, "attributes.bin")) return DM_RISK;
    if (dm_ieq(f, "GlobalB.lzc") || dm_ieq(f, "GlobalMemoryFile.bin")) return 0;
    const char *dot = strrchr(f, '.');
    return dot && (dm_ieq(dot, ".bin") || dm_ieq(dot, ".bun") || dm_ieq(dot, ".lzc")) ? DM_RISK_SOFT : 0;
}
static void dm_add(dm_scan *s, int layer, const char *rel, const char *game_dir) {
    if (s->n_files >= DM_MAX_FILES) { s->truncated = 1; return; }
    if (!s->files) s->files = (dm_file *)calloc(DM_MAX_FILES, sizeof(dm_file));
    if (!s->files) { s->truncated = 1; return; }
    dm_file *f = &s->files[s->n_files++];
    f->layer = layer;
    snprintf(f->rel, sizeof f->rel, "%s", rel);
    const char *slash = strchr(rel, '/');
    char top[256];
    snprintf(top, sizeof top, "%.*s", slash ? (int)(slash - rel) : (int)strlen(rel), rel);
    if (dm_refused(rel)) f->status = DM_REFUSED;
    else if (!slash || !dm_is_game_folder(top)) f->status = DM_NOTUSED;
    else {
        const int risk = dm_save_risk(rel);
        char gp[PATH_MAX];
        struct stat st;
        snprintf(gp, sizeof gp, "%s/%s", game_dir ? game_dir : "", rel);
        f->status = risk ? risk : (game_dir && stat(gp, &st) == 0) ? DM_OK : DM_NEW;
    }
}
static void dm_walk(dm_scan *s, int layer, const char *dir, const char *rel, int depth, const char *game_dir) {
    if (depth > DM_MAX_DEPTH) return;
    DIR *d = opendir(dir);
    if (!d) return;
    char (*names)[256] = (char (*)[256])malloc(512 * 256);   /* on the heap: the walk recurses */
    if (!names) { closedir(d); return; }
    int n = 0;
    struct dirent *e;
    while ((e = readdir(d)) && n < 512) {
        if (e->d_name[0] == '.') continue;   /* ., .., .DS_Store and hidden files */
        snprintf(names[n++], 256, "%s", e->d_name);
    }
    closedir(d);
    qsort(names, (size_t)n, 256, dm_cmp_names);
    for (int i = 0; i < n; ++i) {
        char path[PATH_MAX], r[512];
        snprintf(path, sizeof path, "%s/%s", dir, names[i]);
        snprintf(r, sizeof r, "%s%s%s", rel, *rel ? "/" : "", names[i]);
        struct stat st;
        if (lstat(path, &st) != 0) continue;
        if (S_ISLNK(st.st_mode) && (stat(path, &st) != 0 || S_ISDIR(st.st_mode))) continue;   /* no linked folders inside */
        if (S_ISDIR(st.st_mode)) dm_walk(s, layer, path, r, depth + 1, game_dir);
        else if (S_ISREG(st.st_mode)) dm_add(s, layer, r, game_dir);
    }
    free(names);
}
static int dm_cmp_rel_layer(const void *x, const void *y) {
    const dm_file *a = (const dm_file *)x, *b = (const dm_file *)y;
    const int c = strcasecmp(a->rel, b->rel);
    return c ? c : a->layer - b->layer;
}
/* Scans dir (created when missing) against game_dir; fills s (free with dm_free). Returns the number of layers. */
static int dm_scan_dir(dm_scan *s, const char *dir, const char *game_dir) {
    memset(s, 0, sizeof *s);
    mkdir(dir, 0755);
    DIR *d = opendir(dir);
    if (!d) return 0;
    char mods[256][256];
    int n_mods = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..") || !strcmp(e->d_name, "datamods-report.txt")) continue;
        char path[PATH_MAX];
        struct stat st;
        snprintf(path, sizeof path, "%s/%s", dir, e->d_name);
        if (stat(path, &st) != 0) continue;   /* follows a linked mod folder one level */
        if (S_ISDIR(st.st_mode) && dm_is_game_folder(e->d_name)) { s->has_loose = 1; continue; }
        if (dm_parked(e->d_name)) {
            if (e->d_name[0] != '.' && s->n_ignored < DM_MAX_MODS * 2)
                snprintf(s->ignored[s->n_ignored++], sizeof s->ignored[0], "%s", e->d_name);
            continue;
        }
        if (S_ISDIR(st.st_mode) && n_mods < 256) snprintf(mods[n_mods++], sizeof mods[0], "%s", e->d_name);
        else if (S_ISREG(st.st_mode)) dm_add(s, -1, e->d_name, game_dir);   /* a loose top-level file: NOT USED */
    }
    closedir(d);
    qsort(mods, (size_t)n_mods, sizeof mods[0], dm_cmp_names);
    if (s->has_loose) {
        snprintf(s->layer_name[0], sizeof s->layer_name[0], "(loose files)");
        snprintf(s->layer_dir[0], sizeof s->layer_dir[0], "%s", dir);
        s->n_layers = 1;
        DIR *l = opendir(dir);
        char tops[16][256];
        int nt = 0;
        while (l && (e = readdir(l)) && nt < 16)
            if (dm_is_game_folder(e->d_name)) snprintf(tops[nt++], sizeof tops[0], "%s", e->d_name);
        if (l) closedir(l);
        for (int i = 0; i < nt; ++i) {
            char p[PATH_MAX];
            snprintf(p, sizeof p, "%s/%s", dir, tops[i]);
            dm_walk(s, 0, p, tops[i], 1, game_dir);
        }
    }
    for (int i = 0; i < n_mods; ++i) {
        if (s->n_layers >= DM_MAX_MODS + (s->has_loose ? 1 : 0)) { ++s->n_over_limit; continue; }
        const int L = s->n_layers++;
        snprintf(s->layer_name[L], sizeof s->layer_name[0], "%s", mods[i]);
        snprintf(s->layer_dir[L], sizeof s->layer_dir[0], "%s/%s", dir, mods[i]);
        dm_walk(s, L, s->layer_dir[L], "", 0, game_dir);
    }
    /* SHADOWED: the same path (any case) in a later layer; only files the game would read take part */
    if (s->n_files) qsort(s->files, (size_t)s->n_files, sizeof(dm_file), dm_cmp_rel_layer);
    for (int i = 0; i + 1 < s->n_files; ++i) {
        dm_file *a = &s->files[i];
        if (a->layer < 0 || a->status == DM_REFUSED || a->status == DM_NOTUSED) continue;
        for (int j = i + 1; j < s->n_files && !strcasecmp(s->files[j].rel, a->rel); ++j)
            if (s->files[j].layer > a->layer && s->files[j].status != DM_REFUSED) { a->status = DM_SHADOWED; break; }
    }
    for (int i = 0; i < s->n_files; ++i) ++s->count[s->files[i].status];
    return s->n_layers;
}
/* The report, rewritten at every scan. on = the setting (an off scan is not written). */
static int dm_write_report(const dm_scan *s, const char *dir, const char *game_dir) {
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/datamods-report.txt", dir);
    FILE *f = fopen(path, "w");
    if (!f) return 0;
    fprintf(f, "DataMods report (rewritten at every start)\nFolder: %s\nGame folder: %s\n\n", dir, game_dir ? game_dir : "?");
    fprintf(f, "Load order (a later one wins):\n");
    for (int i = 0; i < s->n_layers; ++i) fprintf(f, "  %d. %s\n", i + 1, s->layer_name[i]);
    if (!s->n_layers) fprintf(f, "  (nothing)\n");
    for (int i = 0; i < s->n_ignored; ++i) fprintf(f, "Ignored (name starts with _): %s\n", s->ignored[i]);
    if (s->n_over_limit) fprintf(f, "Not loaded: %d more mod folders (the limit is %d)\n", s->n_over_limit, DM_MAX_MODS);
    if (s->truncated) fprintf(f, "Not scanned: files beyond %d\n", DM_MAX_FILES);
    fprintf(f, "\nFiles: %d OK, %d new, %d save-risk, %d shadowed, %d refused, %d not used\n", s->count[DM_OK],
            s->count[DM_NEW], s->count[DM_RISK] + s->count[DM_RISK_SOFT], s->count[DM_SHADOWED], s->count[DM_REFUSED],
            s->count[DM_NOTUSED]);
    for (int i = 0; i < s->n_files; ++i) {
        const dm_file *x = &s->files[i];
        const char *why = x->status == DM_RISK ? "  (changes which careers load: your saves are backed up)"
                        : x->status == DM_RISK_SOFT ? "  (may change which careers load)"
                        : x->status == DM_REFUSED ? "  (program files never run here)"
                        : x->status == DM_NOTUSED ? "  (not a game folder)"
                        : x->status == DM_SHADOWED ? "  (a later mod has the same file)" : "";
        fprintf(f, "%-9s %s/%s%s\n", k_dm_status[x->status], x->layer < 0 ? "." : s->layer_name[x->layer], x->rel, why);
    }
    fclose(f);
    return 1;
}
static void dm_free(dm_scan *s) { free(s->files); s->files = NULL; }
#endif
