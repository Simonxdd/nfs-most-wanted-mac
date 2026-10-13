/* Run 27 scenery fade-in (owner D-153; review/run27/fadein-design.md + fadein-draw-point.md). A scenery instance
 * that starts being drawn in the main view fades in over ~0.3 s (an ordered 4x4 dither in the pixel shader) instead
 * of appearing in one frame. Setting `scenery_fade` (Off by default while in development), or TEST
 * NFSMW_TEST_FADEIN=1; NFSMW_TEST_FADEIN_MS (300; 0 = detect but never fade: the control); NFSMW_TEST_FADEIN_LOG=1.
 * Read on the waveb gen:
 *  - 00729120 decides one instance (thiscall: ecx section, [esp+4] index, [esp+8] cull info); a drawn instance
 *    appends the 12-byte item {model|flag, matrix, instance} at cull+0x8c. Main view: cull+0x80 == 00919650.
 *  - 00723fa0 walks the items -> 006da9b0 -> 006e0ab0 builds one record per mesh, rec+0x1c = the item's matrix
 *    pointer; the flush 006e2f50 sorts the records (std::sort, not a draw) and 006e0200 draws them in a loop: per
 *    record 006c8000(rec+0x1c matrix, view) at 006e03f4 sets the transform, then DrawIndexedPrimitive (006e05fc).
 * Hooks: slot 145 WRAP 00729120 (new instance -> start time; per frame, the matrices still fading -> fade 1..255),
 * slot 146 WRAP 006c8000 (private render state 243 = that matrix's fade, or 0, set only when it changes), slot 147
 * WRAP 006e0200 (243 back to 0 after the loop). Other views drawing the same instance fade with it (harmless); cars,
 * HUD, sky and particles never match a fading matrix. The backend (kit d3d9_metal.mm d9_msl_source) patches every
 * pixel shader at compile time when RECOMP_REMASTER_FADE=1, which this file sets at init. */
#ifndef SCENERY_FADE_H
#define SCENERY_FADE_H
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define SF_VIEW_PLAYER1 0x00919650u
#define SF_FRAME 0x009885b8u
#define SF_RS 243u
#define SF_TABLE 65536u
#define SF_FRAME_SLOTS 1024u
#define SF_FRAME_MAX 400u
typedef struct { uint32_t section, index, last_frame, start_frame; uint64_t start_ms; } SfEntry;
#define SF_CUT 120u   /* more new instances than this in one frame = a camera cut (look-back, intro, load): no fade */
typedef struct { uint32_t matrix; uint8_t fade; } SfFrameSlot;
static SfEntry *g_sf;
static SfFrameSlot g_sf_frame_tab[SF_FRAME_SLOTS];
static uint32_t g_sf_frame = 0xffffffffu, g_sf_frame_n, g_sf_cur, g_sf_ms = 300u, g_sf_lines;
static int g_sf_on, g_sf_log;
static uint64_t g_sf_new, g_sf_faded_entries, g_sf_sets, g_sf_full, g_sf_cuts;
static uint32_t g_sf_frame_new, g_sf_cut_ring[4] = {0xffffffffu, 0xffffffffu, 0xffffffffu, 0xffffffffu}, g_sf_cut_i;
static int sf_cut(uint32_t f) { for (int i = 0; i < 4; ++i) if (g_sf_cut_ring[i] == f) return 1; return 0; }

static uint64_t sf_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)(ts.tv_nsec / 1000000);
}
static uint32_t sf_hash(uint32_t m) { return (m >> 4) * 2654435761u; }
static void sf_frame_add(uint32_t matrix, uint8_t fade) {
    if (g_sf_frame_n >= SF_FRAME_MAX) { ++g_sf_full; return; }
    for (uint32_t i = 0, h = sf_hash(matrix); i < SF_FRAME_SLOTS; ++i) {
        SfFrameSlot *s = &g_sf_frame_tab[(h + i) & (SF_FRAME_SLOTS - 1)];
        if (s->matrix == matrix) { if (fade < s->fade) s->fade = fade; return; }
        if (!s->matrix) { s->matrix = matrix; s->fade = fade; ++g_sf_frame_n; return; }
    }
}
static uint8_t sf_frame_get(uint32_t matrix) {
    if (!g_sf_frame_n || !matrix) return 0;
    for (uint32_t i = 0, h = sf_hash(matrix); i < SF_FRAME_SLOTS; ++i) {
        const SfFrameSlot *s = &g_sf_frame_tab[(h + i) & (SF_FRAME_SLOTS - 1)];
        if (s->matrix == matrix) return s->fade;
        if (!s->matrix) return 0;
    }
    return 0;
}
static void sf_set_rs(uint32_t v) {
    if (v == g_sf_cur) return;
    const uint32_t a[2] = {SF_RS, v};
    pfx_call(get_u32(0x00982bdcu), 0xe4, a, 2);   /* the device's SetRenderState, as smoke_light.h sets 241 */
    g_sf_cur = v;
    ++g_sf_sets;
}
static void sf_cull_wrap(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)user;
    const uint32_t section = cpu->ecx, sp = cpu->esp;
    const uint32_t index = get_u32(sp + 4u), cull = get_u32(sp + 8u);
    if (!section || !cull || get_u32(cull + 0x80u) != SF_VIEW_PLAYER1) { api->call_next(api, inv, cpu); return; }
    const uint32_t cursor = get_u32(cull + 0x8cu);
    api->call_next(api, inv, cpu);
    const uint32_t frame = get_u32(SF_FRAME);
    if (frame != g_sf_frame) {   /* a new frame's main-view cull: last frame's fading set is done */
        g_sf_frame = frame;
        g_sf_frame_new = 0;
        if (g_sf_frame_n) { memset(g_sf_frame_tab, 0, sizeof g_sf_frame_tab); g_sf_frame_n = 0; }
    }
    if (get_u32(cull + 0x8cu) <= cursor) return;   /* not drawn this time */
    SfEntry *e = &g_sf[(((section >> 4) * 31u) ^ (index * 2654435761u)) & (SF_TABLE - 1)];
    const uint64_t now = sf_now_ms();
    const int known = e->section == section && e->index == index && frame - e->last_frame <= 2u;
    if (!known) {
        e->section = section; e->index = index; e->start_ms = now; e->start_frame = frame;
        ++g_sf_new;
        if (++g_sf_frame_new == SF_CUT && !sf_cut(frame)) {   /* a cut: this frame's instances appear at once */
            g_sf_cut_ring[g_sf_cut_i++ & 3u] = frame;
            memset(g_sf_frame_tab, 0, sizeof g_sf_frame_tab);
            g_sf_frame_n = 0;
            ++g_sf_cuts;
        }
        if (g_sf_log && g_sf_lines < 2000u && frame > 2u) {
            const uint32_t inst = get_u32(section + 0x20u) + index * 0x40u;
            char line[160];
            snprintf(line, sizeof line, "core.nfsmw: TEST fade: frame %u new instance section %08x idx %u pos %.1f %.1f %.1f",
                     frame, section, index, get_float(inst + 0x20u), get_float(inst + 0x24u), get_float(inst + 0x28u));
            api->log(api, line);
            ++g_sf_lines;
        }
    }
    e->last_frame = frame;
    if (!g_sf_ms) return;   /* the control: detect, never fade */
    const uint64_t age = now - e->start_ms;
    if (age >= g_sf_ms || sf_cut(e->start_frame)) return;
    const uint32_t matrix = get_u32(cursor + 4u);
    if (!matrix) return;   /* identity instances share one matrix: never fade those */
    sf_frame_add(matrix, (uint8_t)(1u + (uint32_t)(age * 254u / g_sf_ms)));
    ++g_sf_faded_entries;
}
static void sf_matrix_wrap(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)user;
    const uint8_t f = sf_frame_get(get_u32(cpu->esp + 4u));
    if (f || g_sf_cur) sf_set_rs(f);
    api->call_next(api, inv, cpu);
}
static void sf_draw_loop_wrap(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)user;
    api->call_next(api, inv, cpu);
    if (g_sf_cur) sf_set_rs(0);
}
static void scenery_fade_init(const PopModApi *api) {
    const char *t = getenv("NFSMW_TEST_FADEIN"), *v;
    const int test = t && *t == '1';
    if (!test && setting("scenery_fade", 1) != 1) return;
    if ((v = getenv("NFSMW_TEST_FADEIN_MS")) && *v) g_sf_ms = (uint32_t)atoi(v);
    if (g_sf_ms > 2000u) g_sf_ms = 2000u;
    g_sf_log = (v = getenv("NFSMW_TEST_FADEIN_LOG")) && *v == '1';
    g_sf = (SfEntry *)calloc(SF_TABLE, sizeof *g_sf);
    int ok = 0;
    if (g_sf) {
        ok += hook_slot_free(0x00729120u, 145) &&
              api->hook_install(api, 0x00729120u, sf_cull_wrap, POP_HOOK_WRAP, NULL, &g_hooks[145]) == POP_OK;
        ok += hook_slot_free(0x006c8000u, 146) &&
              api->hook_install(api, 0x006c8000u, sf_matrix_wrap, POP_HOOK_WRAP, NULL, &g_hooks[146]) == POP_OK;
        ok += hook_slot_free(0x006e0200u, 147) &&
              api->hook_install(api, 0x006e0200u, sf_draw_loop_wrap, POP_HOOK_WRAP, NULL, &g_hooks[147]) == POP_OK;
    }
    g_sf_on = ok == 3;
    if (g_sf_on) setenv("RECOMP_REMASTER_FADE", "1", 0);
    char line[160];
    snprintf(line, sizeof line, "core.nfsmw: scenery fade-in on: %d of 3 hooks; %u ms%s", ok, g_sf_ms,
             g_sf_ms ? "" : " (TEST control: detect only)");
    api->log(api, line);
}
static void scenery_fade_exit(const PopModApi *api) {
    if (!g_sf_on) return;
    char line[224];
    snprintf(line, sizeof line, "core.nfsmw: scenery fade-in: new main-view instances %llu, fading entries %llu, "
             "render-state sets %llu, frames over the %u fading cap %llu, camera cuts %llu",
             (unsigned long long)g_sf_new, (unsigned long long)g_sf_faded_entries, (unsigned long long)g_sf_sets,
             SF_FRAME_MAX, (unsigned long long)g_sf_full, (unsigned long long)g_sf_cuts);
    api->log(api, line);
    free(g_sf);
    g_sf = NULL;
    g_sf_on = 0;
}
#endif
