/* Run 32 (GitHub issue 8: a crash about 2 s after start with a monitor that is also the audio output; review/run32/
 * issue8-crash.md): the game's sound thread (00820bd7) polls its DirectSound buffer about every 10 ms (00820907). When
 * the write cursor has not moved for 100 polls (the counter [0x9c229c], 0082096c-0082097a) it releases DirectSound and
 * its buffers and makes them again (008201ae, 00820762) while the sound engine still uses the old ones. On Windows the
 * cursor runs on the sound card's clock; here it follows what the Mac's output has really played, and an output that
 * CoreAudio is still bringing up (an external display's audio) can stand still for over a second: the restart then
 * runs, the freed buffer memory is reused and the game crashes (RECOMP_TEST_AUDIO_STALL=1000:2000 makes it run on any
 * Mac: two restarts in a 2 s stall). The host already handles a device that goes away or comes back, so the game's own
 * restart is never needed: the counter is held below 100 before each poll, and the first stall long enough to have
 * restarted is logged. Kill switch: NFSMW_SND_RESTART=1 lets the game restart as before (TEST). */
static uint32_t g_snd_stall_logged;
static void snd_stall_poll(const PopModApi *api, pop_cpu_v1 *c, PopHookInvocation *i, void *u) {
    (void)c; (void)i; (void)u;
    uint32_t n = 0;
    api->guest_read_u32(api, 0x009c229cu, &n);
    if (n < 90u) return;
    api->guest_write_u32(api, 0x009c229cu, 0);
    if (!__atomic_exchange_n(&g_snd_stall_logged, 1u, __ATOMIC_RELAXED))
        api->log(api, "core.nfsmw: sound output stood still for about a second (an output still starting); the game's "
                      "DirectSound restart skipped (it is not needed here)");
}
static void snd_stall_init(const PopModApi *api) {
    const char *t = getenv("NFSMW_SND_RESTART");
    if (t && t[0] == '1') { api->log(api, "core.nfsmw: TEST NFSMW_SND_RESTART=1: the game's DirectSound restart is allowed"); return; }
    if (install(0x00820907u, snd_stall_poll, POP_HOOK_BEFORE, 109) != POP_OK)
        api->log(api, "core.nfsmw: sound restart guard unavailable (hook)");
}
