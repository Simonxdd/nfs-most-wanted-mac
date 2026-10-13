/* R3 run 29 weaker-Mac presets (review/run29/presets-design.md; D-114 item 3, D-166). Setting `mac_preset`: 0 Auto
 * (this Mac), 1 Full, 2 Balanced, 3 Light. Auto picks from the chip (machdep.cpu.brand_string: generation and
 * base/Pro/Max/Ultra), the output pixels and nothing else: a fast Mac is game-thread bound (PERF_COSTS), so a preset
 * can only lower GPU cost, which is pixels x features / GPU class, all known before the first frame. Every constant is
 * INITIAL (refined from player logs). A tier is read-time caps in setting(): it never writes a saved
 * value, so Advanced keeps the player's choices and Full brings them back. A tier never raises cost.
 *   Balanced: upscale at least Quality (1.5x); smooth edges at most 2x.
 *   Light:    upscale at least Performance (2x); smooth edges off; ambient occlusion, softer shadows, lamp glow and
 *             denser tyre smoke off.
 * Untouched: HDR, the scenery fade, the post chain, rain, time of day, weather, the mirror, Detail level.
 * TEST ONLY: NFSMW_TEST_HW="M1 Pro,16,3840x2160" replaces the chip, the RAM (GB) and the output size.
 * The pure functions (mt_*) use no mod API, so tests/test_mac_tier.py compiles them on their own. */
#ifndef NFSMW_MAC_TIER_H
#define NFSMW_MAC_TIER_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { MT_AUTO = 0, MT_FULL = 1, MT_BALANCED = 2, MT_LIGHT = 3 };
enum { MT_CAP = 0, MT_FLOOR = 1, MT_FORCE = 2 };   /* min(saved, v) / max(saved, v) / v */
typedef struct { const char *key; int64_t value; int kind; } MtRow;
static const MtRow k_mt_balanced[] = {{"upscale", 1, MT_FLOOR}, {"antialiasing", 1, MT_CAP}};
static const MtRow k_mt_light[] = {{"upscale", 2, MT_FLOOR},     {"antialiasing", 0, MT_CAP}, {"ssao", 0, MT_FORCE},
                                   {"soft_shadows", 0, MT_FORCE}, {"lamp_glow", 0, MT_FORCE},  {"denser_smoke", 0, MT_FORCE}};
static const char *const k_mt_name[4] = {"Auto", "Full", "Balanced", "Light"};

/* "Apple M1 Pro" -> gen 1, class weight 2 (base 1, Pro 2, Max 4, Ultra 8). 0 when the string is not an Apple M chip. */
static int mt_parse_chip(const char *s, int *gen, int *cls) {
    const char *m = s ? strstr(s, "Apple M") : NULL;
    if (!m || m[7] < '1' || m[7] > '9') return 0;
    *gen = (int)strtol(m + 7, NULL, 10);
    *cls = strstr(m, "Ultra") ? 8 : strstr(m, "Max") ? 4 : strstr(m, "Pro") ? 2 : 1;
    return 1;
}
/* Demand: the output pixels against 4K, over the GPU score class x (1 + 0.2 x (gen - 1)). */
static double mt_demand(uint32_t w, uint32_t h, int gen, int cls) {
    const double score = (double)cls * (1.0 + 0.2 * (double)(gen - 1));
    return ((double)w * (double)h / (3840.0 * 2160.0)) / (score > 0.0 ? score : 1.0);
}
static int mt_pick(double demand) {
    return demand <= 0.35 ? MT_FULL : demand <= 0.70 ? MT_BALANCED : MT_LIGHT;
}
/* The tier's value for key over the saved v (the read-time chain); returns 1 when the tier changed it. */
static int mt_apply(int tier, const char *key, int64_t *v) {
    const MtRow *rows = tier == MT_BALANCED ? k_mt_balanced : tier == MT_LIGHT ? k_mt_light : NULL;
    const unsigned n = tier == MT_BALANCED ? sizeof k_mt_balanced / sizeof k_mt_balanced[0]
                     : tier == MT_LIGHT ? sizeof k_mt_light / sizeof k_mt_light[0] : 0;
    for (unsigned i = 0; i < n; ++i) {
        if (strcmp(key, rows[i].key)) continue;
        const int64_t w = rows[i].kind == MT_FORCE ? rows[i].value
                        : rows[i].kind == MT_CAP ? (*v < rows[i].value ? *v : rows[i].value)
                                                 : (*v > rows[i].value ? *v : rows[i].value);
        const int changed = w != *v;
        *v = w;
        return changed;
    }
    return 0;
}

#ifndef NFSMW_MAC_TIER_PURE
#include <sys/sysctl.h>
#if defined(__APPLE__)
#include <TargetConditionals.h>
#include <dlfcn.h>
#endif
/* Run 32 (owner's TV check: Auto logged "output 0x0 unknown: nothing lowered"): on the Mac desktop host the mod API's
 * screen_size has no value yet (only the web host fills that seam), so Auto never saw the screen and never lowered
 * anything. The main display's pixel size from CoreGraphics, found at run time (the mod links nothing new; a host
 * without it leaves the size unknown, as before). The game's own resolution list (resolution.h) is not changed. */
static void mt_main_display_pixels(uint32_t *w, uint32_t *h) {
#if defined(__APPLE__) && TARGET_OS_OSX
    typedef uint32_t (*MainFn)(void);
    typedef void *(*CopyFn)(uint32_t);
    typedef size_t (*PixFn)(void *);
    typedef void (*RelFn)(void *);
    MainFn main_id = (MainFn)dlsym(RTLD_DEFAULT, "CGMainDisplayID");
    CopyFn copy = (CopyFn)dlsym(RTLD_DEFAULT, "CGDisplayCopyDisplayMode");
    PixFn pw = (PixFn)dlsym(RTLD_DEFAULT, "CGDisplayModeGetPixelWidth");
    PixFn ph = (PixFn)dlsym(RTLD_DEFAULT, "CGDisplayModeGetPixelHeight");
    RelFn rel = (RelFn)dlsym(RTLD_DEFAULT, "CGDisplayModeRelease");
    if (!(main_id && copy && pw && ph && rel)) return;
    void *mode = copy(main_id());
    if (!mode) return;
    const size_t a = pw(mode), b = ph(mode);
    rel(mode);
    if (a && b && a < 65536 && b < 65536) *w = (uint32_t)a, *h = (uint32_t)b;
#else
    (void)w, (void)h;
#endif
}
static int g_mt_tier = MT_FULL;   /* the tier in force (Full until mac_tier_init) */
static int tier_follow(const char *key, int64_t *v) {
    return g_mt_tier > MT_FULL && mt_apply(g_mt_tier, key, v);
}
static void mac_tier_init(const PopModApi *api) {
    const int64_t choice = setting("mac_preset", MT_FULL);
    char chip[128] = "";
    size_t n = sizeof chip;
    uint64_t ram = 0;
    size_t rn = sizeof ram;
    if (sysctlbyname("machdep.cpu.brand_string", chip, &n, NULL, 0) != 0) chip[0] = 0;
    if (sysctlbyname("hw.memsize", &ram, &rn, NULL, 0) != 0) ram = 0;
    unsigned gb = (unsigned)(ram >> 30);
    uint32_t w = (uint32_t)setting("width", 0), h = (uint32_t)setting("height", 0);
    if (!(w && h) && api->screen_size) api->screen_size(api, &w, &h);
    if (!(w && h)) mt_main_display_pixels(&w, &h);
    const char *t = getenv("NFSMW_TEST_HW");   /* TEST ONLY: "M1 Pro,16,3840x2160" */
    if (t && *t) {
        char c[64] = "";
        unsigned tw = 0, th = 0;
        if (sscanf(t, "%63[^,],%u,%ux%u", c, &gb, &tw, &th) == 4) {
            snprintf(chip, sizeof chip, "Apple %s", c);
            w = tw, h = th;
        }
    }
    int gen = 0, cls = 0;
    const int known = mt_parse_chip(chip, &gen, &cls);
    const double demand = known && w && h ? mt_demand(w, h, gen, cls) : 0.0;
    const int autopick = known && w && h ? mt_pick(demand) : MT_FULL;
    char line[320];
    if (choice == MT_AUTO) {
        g_mt_tier = autopick;
        if (known && w && h)
            snprintf(line, sizeof line, "core.nfsmw: Mac preset Auto -> %s (%s, %u GB, output %ux%u, demand %.2f)%s",
                     k_mt_name[g_mt_tier], chip, gb, w, h, demand, t && *t ? " [TEST NFSMW_TEST_HW]" : "");
        else
            snprintf(line, sizeof line, "core.nfsmw: Mac preset Auto -> Full (chip \"%s\" or output %ux%u unknown: "
                     "nothing lowered)", chip, w, h);
    } else {
        g_mt_tier = choice >= MT_FULL && choice <= MT_LIGHT ? (int)choice : MT_FULL;
        snprintf(line, sizeof line, "core.nfsmw: Mac preset %s (chosen; Auto would pick %s on %s, %u GB, output %ux%u)",
                 k_mt_name[g_mt_tier], k_mt_name[autopick], chip[0] ? chip : "an unknown chip", gb, w, h);
    }
    api->log(api, line);
    if (g_mt_tier == MT_BALANCED)
        api->log(api, "core.nfsmw: Mac preset Balanced: upscaling at least Quality (1.5x), smooth edges at most 2x");
    else if (g_mt_tier == MT_LIGHT)
        api->log(api, "core.nfsmw: Mac preset Light: upscaling Performance (2x), smooth edges off; ambient occlusion, "
                      "softer shadows, lamp glow and denser tyre smoke off");
}
#endif
#endif
