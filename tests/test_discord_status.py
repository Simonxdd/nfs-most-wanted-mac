"""Run 34 Discord status (D-181, ported from PR 9): mods/core/nfsmw/discord_status.h's worker against a fake Discord
(a Unix socket server at $TMPDIR/discord-ipc-0), compiled with a small driver. Off opens no socket; on sends the
handshake with the project's application id and the status; driving changes it; off clears it and closes; Discord's
error replies reach the log; quitting never waits on a Discord that does not answer."""
import subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#define DISCORD_STATUS_PURE 1
#define DISCORD_RETRY_S 1
#include "discord_status.h"
#include <errno.h>

static const char *mode;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static char seen[4096];
static int accepts;
static void note(const char *s) { pthread_mutex_lock(&mu); strcat(seen, s); strcat(seen, " "); pthread_mutex_unlock(&mu); }
static int has(const char *s) { pthread_mutex_lock(&mu); int r = strstr(seen, s) != NULL; pthread_mutex_unlock(&mu); return r; }

static int frame(int fd, uint32_t *op, char *buf, size_t size) {
    uint8_t h[8];
    if (recv(fd, h, 8, MSG_WAITALL) != 8) return -1;
    *op = h[0] | h[1] << 8 | (uint32_t)h[2] << 16 | (uint32_t)h[3] << 24;
    uint32_t len = h[4] | h[5] << 8 | (uint32_t)h[6] << 16 | (uint32_t)h[7] << 24;
    if (len >= size || recv(fd, buf, len, MSG_WAITALL) != (ssize_t)len) return -1;
    buf[len] = 0;
    return 0;
}
static void reply(int fd, uint32_t op, const char *json) { ds_write(fd, op, json); }

static void *server(void *arg) {
    const int ls = *(int *)arg;
    for (;;) {
        const int fd = accept(ls, NULL, NULL);
        if (fd < 0) return NULL;
        pthread_mutex_lock(&mu); ++accepts; pthread_mutex_unlock(&mu);
        uint32_t op; char buf[2048];
        while (frame(fd, &op, buf, sizeof buf) == 0) {
            if (op == 0) {
                note(strstr(buf, "\"client_id\":\"1559581678853431396\"") ? "hs-ok" : "hs-wrong-id");
                if (!strcmp(mode, "hang")) continue;
                if (!strcmp(mode, "badid")) { reply(fd, 2, "{\"code\":4000,\"message\":\"Invalid Client ID\"}"); break; }
                reply(fd, 1, "{\"cmd\":\"DISPATCH\",\"data\":{\"user\":{\"username\":\"secret-name\"}},\"evt\":\"READY\"}");
            } else {
                note(strstr(buf, "Driving in Rockport") ? "act-driving" : strstr(buf, "In the menus") ? "act-menus"
                     : strstr(buf, "\"activity\"") ? "act-other" : "act-clear");
                if (!strcmp(mode, "error"))
                    reply(fd, 1, "{\"cmd\":\"SET_ACTIVITY\",\"data\":{\"code\":4002,\"message\":\"child \\\"activity\\\" fails\"},\"evt\":\"ERROR\",\"nonce\":\"1\"}");
                else reply(fd, 1, "{\"cmd\":\"SET_ACTIVITY\",\"data\":{},\"evt\":null,\"nonce\":\"1\"}");
                if (!strcmp(mode, "restart") && accepts == 1) { usleep(300000); break; } /* Discord quits after the first status */
            }
        }
        note("eof");
        close(fd);
    }
}

static char logs[2048];
static void pump(int want, int ms) {
    for (int t = 0; t < ms; t += 10) {
        char line[240];
        if (ds_set_want(want, line, sizeof line)) { strcat(logs, "["); strcat(logs, line); strcat(logs, "]"); }
        usleep(10000);
    }
}

int main(int argc, char **argv) {
    mode = argv[1];
    setenv("TMPDIR", argv[2], 1);
    const int ls = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un sa = {.sun_family = AF_UNIX};
    snprintf(sa.sun_path, sizeof sa.sun_path, "%s/discord-ipc-0", argv[2]);
    if (bind(ls, (struct sockaddr *)&sa, sizeof sa) || listen(ls, 4)) { perror("bind"); return 2; }
    pthread_t st; pthread_create(&st, NULL, server, (void *)&ls);
    if (!strcmp(mode, "off")) {
        pump(DS_OFF, 1500);
    } else if (!strcmp(mode, "on")) {
        pump(DS_MENUS, 1500);
        pump(DS_DRIVING, 2500);
        pump(DS_OFF, 1000);
    } else if (!strcmp(mode, "error") || !strcmp(mode, "badid")) {
        pump(DS_MENUS, 1500);
    } else if (!strcmp(mode, "restart")) {
        pump(DS_MENUS, 3500);
    } else if (!strcmp(mode, "hang")) {
        pump(DS_MENUS, 1000);
    }
    struct timeval a, b;
    gettimeofday(&a, NULL);
    discord_status_stop();
    gettimeofday(&b, NULL);
    usleep(200000);
    const long stop_ms = (b.tv_sec - a.tv_sec) * 1000 + (b.tv_usec - a.tv_usec) / 1000;
    pthread_mutex_lock(&mu);
    printf("accepts=%d started=%d stop_ms=%ld\nseen=%s\nlogs=%s\n", accepts, g_ds_started, stop_ms, seen, logs);
    pthread_mutex_unlock(&mu);
    return 0;
}
'''


def run(mode):
    with tempfile.TemporaryDirectory(dir='/tmp') as tmp:   # /tmp: a Unix socket path must stay under 104 bytes
        src, exe = Path(tmp) / 'd.c', Path(tmp) / 'd'
        src.write_text(DRIVER)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-I',
                        str(ROOT / 'mods/core/nfsmw'), str(src), '-o', str(exe), '-lpthread'], check=True)
        out = subprocess.run([str(exe), mode, tmp], capture_output=True, text=True, check=True, timeout=30).stdout
        head, seen, logs = out.splitlines()
        vals = dict(kv.split('=') for kv in head.split())
        return {k: int(v) for k, v in vals.items()}, seen[len('seen='):].split(), logs[len('logs='):]


def test_off_opens_nothing():
    vals, seen, logs = run('off')
    assert vals['accepts'] == 0 and seen == [] and logs == ''


def test_on_menus_driving_then_off_clears_and_closes():
    vals, seen, logs = run('on')
    assert vals['accepts'] == 1
    assert seen == ['hs-ok', 'act-menus', 'act-driving', 'act-clear', 'eof'], seen
    assert logs == '[connected][disconnected]', logs
    assert 'secret-name' not in logs


def test_discord_error_reply_is_logged_once():
    vals, seen, logs = run('error')
    assert seen[:2] == ['hs-ok', 'act-menus'] and seen.count('act-menus') == 1, seen
    assert '[Discord refused the status: code 4002, "child "activity" fails"]' in logs, logs


def test_refused_application_id_is_logged():
    vals, seen, logs = run('badid')
    assert seen[0] == 'hs-ok'
    assert logs.count('[Discord refused the connection: code 4000, "Invalid Client ID"]') == 1, logs
    assert seen.count('hs-ok') >= 2, seen   # retried, logged once
    assert 'connected' not in logs


def test_quit_does_not_wait_on_a_hung_discord():
    vals, seen, logs = run('hang')
    assert seen[0] == 'hs-ok'
    assert vals['stop_ms'] < 300, vals


def test_status_comes_back_after_discord_restarts():
    vals, seen, logs = run('restart')
    assert seen[:5] == ['hs-ok', 'act-menus', 'eof', 'hs-ok', 'act-menus'], seen   # the same state, shown again
    assert logs.startswith('[connected][disconnected][connected]'), logs
