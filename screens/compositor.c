// ft-screens: a minimal Wayland compositor that hosts the nested KWin and shows each of
// its screens as its own SteamVR panel. It replaces gamescope for Frametop:
//   - KWin (nested Wayland backend) opens one window per screen. We send each window its
//     size (xdg_toplevel configure), and KWin resizes that screen to match, so every
//     screen has its own resolution and shape (ultrawide, portrait, 4K...). gamescope
//     never sized its windows and drew them all into one canvas of at most 1920x1080.
//   - KWin renders into DMA-BUFs and hands them to us (linux-dmabuf). We draw nothing:
//     each buffer goes to SteamVR as the panel's texture (vr.cpp, ImportDmabuf).
//   - Pointer input from the panels (controller lasers, the 3D mouse) goes to KWin through
//     our seat, as if we were a normal desktop.
//
// Usage: ft-screens [--socket NAME] [--screen WxH@METRES]... [-- COMMAND ARGS...]
//   --socket    Wayland socket name in $XDG_RUNTIME_DIR (default ft-screens-0)
//   --screen    one per screen, in KWin's order (default: 3440x1440@2.4)
//   COMMAND     run with WAYLAND_DISPLAY set to our socket (e.g. the Frametop session)
// Runs in the dev container (wlroots 0.20); KWin connects from the host.
#define _GNU_SOURCE
#include <drm_fourcc.h>
#include <linux/input-event-codes.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <wayland-server-core.h>
#include <xkbcommon/xkbcommon.h>

#define WLR_USE_UNSTABLE
#include <wlr/interfaces/wlr_keyboard.h>
#include <wlr/render/dmabuf.h>
#include <wlr/render/drm_format_set.h>
#include <wlr/types/wlr_buffer.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_data_device.h>
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_linux_dmabuf_v1.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/types/wlr_shm.h>
#include <wlr/types/wlr_subcompositor.h>
#include <wlr/types/wlr_xdg_decoration_v1.h>
#include <wlr/types/wlr_xdg_shell.h>
#include <wlr/util/log.h>

#include "vr.h"

#define MAX_SCREENS 8

struct config {
    int width, height;
    double metres;
};

struct server;

// One KWin window = one screen.
struct screen {
    struct server *server;
    int index;
    struct wlr_xdg_toplevel *toplevel;
    struct wlr_buffer *held;      // on the panel now; unlocked when the next one arrives
    bool frame_pending;           // a commit waits for its frame callback
    struct wlr_xdg_toplevel_decoration_v1 *decoration;  // answered on the first commit
    struct wl_listener commit, destroy, decoration_destroy;
};

// Per client buffer: forget its import when it goes away.
struct tracked_buffer {
    struct wlr_buffer *buffer;
    struct wl_listener destroy;
    struct wl_list link;
};

struct server {
    struct wl_display *display;
    struct wl_event_loop *loop;
    struct wlr_seat *seat;
    struct wlr_keyboard keyboard;
    struct wlr_xdg_shell *xdg_shell;
    struct wlr_xdg_decoration_manager_v1 *decoration;
    struct wl_listener new_toplevel, new_decoration;
    struct screen *screens[MAX_SCREENS];
    struct config config[MAX_SCREENS];
    int n_config, n_screens;
    struct wl_list buffers;  // tracked_buffer
    struct wl_event_source *tick;
    struct screen *pointer_focus;
    pid_t child;
};

static uint32_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

// ---------------------------------------------------------------- buffers

static void buffer_destroyed(struct wl_listener *l, void *data) {
    struct tracked_buffer *t = wl_container_of(l, t, destroy);
    ft_vr_forget(t->buffer);
    wl_list_remove(&t->destroy.link);
    wl_list_remove(&t->link);
    free(t);
}

static void track_buffer(struct server *s, struct wlr_buffer *buffer) {
    struct tracked_buffer *t;
    wl_list_for_each(t, &s->buffers, link) if (t->buffer == buffer) return;
    t = calloc(1, sizeof *t);
    t->buffer = buffer;
    t->destroy.notify = buffer_destroyed;
    wl_signal_add(&buffer->events.destroy, &t->destroy);
    wl_list_insert(&s->buffers, &t->link);
}

// ---------------------------------------------------------------- screens

static void screen_commit(struct wl_listener *l, void *data) {
    struct screen *sc = wl_container_of(l, sc, commit);
    struct wlr_xdg_surface *xdg = sc->toplevel->base;
    if (xdg->initial_commit) {
        // First commit: tell KWin the size of this screen.
        const struct config *c = &sc->server->config[sc->index < sc->server->n_config ? sc->index : 0];
        wlr_xdg_toplevel_set_size(sc->toplevel, c->width, c->height);
        wlr_xdg_toplevel_set_activated(sc->toplevel, true);
        if (sc->decoration)
            wlr_xdg_toplevel_decoration_v1_set_mode(sc->decoration, WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
        return;
    }
    struct wlr_buffer *buffer = xdg->surface->current.buffer;
    static int logged;
    if (logged < 6) {
        ++logged;
        wlr_log(WLR_INFO, "screen %d: commit, buffer %p (%dx%d), mapped %d", sc->index + 1, (void *)buffer,
                buffer ? buffer->width : 0, buffer ? buffer->height : 0, xdg->surface->mapped);
    }
    if (!buffer) return;
    sc->frame_pending = true;
    if (buffer == sc->held) return;
    struct wlr_dmabuf_attributes a;
    if (!wlr_buffer_get_dmabuf(buffer, &a)) {
        static bool warned;
        if (!warned) wlr_log(WLR_ERROR, "screen %d: not a DMA-BUF (shm?); skipped", sc->index + 1);
        warned = true;
        return;
    }
    struct ft_dmabuf b = {.width = a.width, .height = a.height, .format = a.format, .modifier = a.modifier,
                          .n_planes = a.n_planes};
    for (int i = 0; i < a.n_planes && i < 4; ++i) {
        b.offset[i] = a.offset[i];
        b.stride[i] = a.stride[i];
        b.fd[i] = a.fd[i];
    }
    track_buffer(sc->server, buffer);
    if (!ft_vr_screen_present(sc->index, buffer, &b)) return;
    // Keep this buffer until the next frame replaces it, so SteamVR never samples a
    // buffer KWin is drawing into; then let KWin have the previous one back.
    wlr_buffer_lock(buffer);
    if (sc->held) wlr_buffer_unlock(sc->held);
    sc->held = buffer;
}

static void screen_destroy(struct wl_listener *l, void *data) {
    struct screen *sc = wl_container_of(l, sc, destroy);
    wlr_log(WLR_INFO, "screen %d closed", sc->index + 1);
    if (sc->held) wlr_buffer_unlock(sc->held);
    ft_vr_screen_destroy(sc->index);
    if (sc->server->pointer_focus == sc) sc->server->pointer_focus = NULL;
    if (sc->decoration) wl_list_remove(&sc->decoration_destroy.link);
    sc->server->screens[sc->index] = NULL;
    wl_list_remove(&sc->commit.link);
    wl_list_remove(&sc->destroy.link);
    free(sc);
}

static void new_toplevel(struct wl_listener *l, void *data) {
    struct server *s = wl_container_of(l, s, new_toplevel);
    struct wlr_xdg_toplevel *toplevel = data;
    int index = 0;
    while (index < MAX_SCREENS && s->screens[index]) ++index;
    if (index == MAX_SCREENS) {
        wlr_log(WLR_ERROR, "more than %d screens; ignoring one", MAX_SCREENS);
        return;
    }
    struct screen *sc = calloc(1, sizeof *sc);
    sc->server = s;
    sc->index = index;
    sc->toplevel = toplevel;
    s->screens[index] = sc;
    const struct config *c = &s->config[index < s->n_config ? index : 0];
    wlr_log(WLR_INFO, "screen %d: KWin window, %dx%d, %.2f m wide", index + 1, c->width, c->height, c->metres);
    ft_vr_screen_create(index, c->metres, s->n_config > index + 1 ? s->n_config : index + 1);
    sc->commit.notify = screen_commit;
    wl_signal_add(&toplevel->base->surface->events.commit, &sc->commit);
    sc->destroy.notify = screen_destroy;
    wl_signal_add(&toplevel->events.destroy, &sc->destroy);
}

// KWin asks for server-side decorations for its screens; we draw none. It asks before its
// first commit, when a configure isn't allowed yet, so the answer waits for that commit.
static void decoration_destroyed(struct wl_listener *l, void *data) {
    struct screen *sc = wl_container_of(l, sc, decoration_destroy);
    wl_list_remove(&sc->decoration_destroy.link);
    sc->decoration = NULL;
}

static void new_decoration(struct wl_listener *l, void *data) {
    struct server *s = wl_container_of(l, s, new_decoration);
    struct wlr_xdg_toplevel_decoration_v1 *d = data;
    for (int i = 0; i < MAX_SCREENS; ++i) {
        struct screen *sc = s->screens[i];
        if (!sc || sc->toplevel != d->toplevel) continue;
        sc->decoration = d;
        sc->decoration_destroy.notify = decoration_destroyed;
        wl_signal_add(&d->events.destroy, &sc->decoration_destroy);
        if (d->toplevel->base->initialized)
            wlr_xdg_toplevel_decoration_v1_set_mode(d, WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
    }
}

// ---------------------------------------------------------------- input from the panels

static void handle_vr_event(const struct ft_event *e, void *data) {
    struct server *s = data;
    if (e->type == FT_QUIT) {
        wl_display_terminate(s->display);
        return;
    }
    if (e->screen < 0 || e->screen >= MAX_SCREENS || !s->screens[e->screen]) return;
    struct screen *sc = s->screens[e->screen];
    struct wlr_surface *surface = sc->toplevel->base->surface;
    const uint32_t t = now_ms();
    switch (e->type) {
        case FT_MOTION:
        case FT_BUTTON:
            if (s->pointer_focus != sc) {
                wlr_seat_pointer_notify_enter(s->seat, surface, e->x, e->y);
                s->pointer_focus = sc;
            }
            wlr_seat_pointer_notify_motion(s->seat, t, e->x, e->y);
            if (e->type == FT_BUTTON) {
                wlr_seat_pointer_notify_button(s->seat, t, e->button,
                                               e->pressed ? WL_POINTER_BUTTON_STATE_PRESSED
                                                          : WL_POINTER_BUTTON_STATE_RELEASED);
                if (e->pressed) wlr_seat_keyboard_notify_enter(s->seat, surface, NULL, 0, NULL);
            }
            break;
        case FT_SCROLL:
            if (s->pointer_focus != sc) break;
            if (e->dy != 0)
                wlr_seat_pointer_notify_axis(s->seat, t, WL_POINTER_AXIS_VERTICAL_SCROLL, e->dy * 15,
                                             (int32_t)(e->dy * 120), WL_POINTER_AXIS_SOURCE_WHEEL,
                                             WL_POINTER_AXIS_RELATIVE_DIRECTION_IDENTICAL);
            if (e->dx != 0)
                wlr_seat_pointer_notify_axis(s->seat, t, WL_POINTER_AXIS_HORIZONTAL_SCROLL, e->dx * 15,
                                             (int32_t)(e->dx * 120), WL_POINTER_AXIS_SOURCE_WHEEL,
                                             WL_POINTER_AXIS_RELATIVE_DIRECTION_IDENTICAL);
            break;
        case FT_LEAVE:
            if (s->pointer_focus == sc) {
                wlr_seat_pointer_notify_clear_focus(s->seat);
                s->pointer_focus = NULL;
            }
            break;
        default:
            break;
    }
    wlr_seat_pointer_notify_frame(s->seat);
}

// Every ~11 ms (90 Hz): SteamVR events, and frame callbacks for screens that committed.
static int tick(void *data) {
    struct server *s = data;
    ft_vr_poll(handle_vr_event, s);
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    for (int i = 0; i < MAX_SCREENS; ++i) {
        struct screen *sc = s->screens[i];
        if (sc && sc->frame_pending) {
            sc->frame_pending = false;
            wlr_surface_send_frame_done(sc->toplevel->base->surface, &now);
        }
    }
    wl_event_source_timer_update(s->tick, 11);
    return 0;
}

static int child_exited(int sig, void *data) {
    struct server *s = data;
    int status;
    pid_t pid;
    while ((pid = waitpid(-1, &status, WNOHANG)) > 0)
        if (pid == s->child) {
            wlr_log(WLR_INFO, "session exited");
            wl_display_terminate(s->display);
        }
    return 0;
}

// Keys from the input relay (physical keyboards): "key <evdev code> <1 press|0 release>".
// They go to the screen KWin has keyboard focus on (the last one clicked), but not while
// the SteamVR dashboard is open: typing belongs to Steam then.
static void handle_key(struct server *s, uint32_t code, int value, char *reply, int size) {
    if (value == 2) return (void)snprintf(reply, size, "ok repeat ignored");  // KWin repeats itself
    if (!s->seat->keyboard_state.focused_surface) return (void)snprintf(reply, size, "ok no focus");
    if (ft_vr_dashboard_visible()) return (void)snprintf(reply, size, "ok dashboard open");
    struct wlr_keyboard_key_event ev = {
        .time_msec = now_ms(), .keycode = code, .update_state = true,
        .state = value ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED};
    wlr_keyboard_notify_key(&s->keyboard, &ev);  // keeps the xkb state and modifiers
    wlr_seat_keyboard_notify_modifiers(s->seat, &s->keyboard.modifiers);
    wlr_seat_keyboard_notify_key(s->seat, ev.time_msec, code, ev.state);
    snprintf(reply, size, "ok");
}

// Control socket: abstract datagram @ft_screens. Here: "size <screen> <w> <h>" (a new
// resolution, live) and "key <code> <value>"; the rest is in vr.cpp (ft_vr_command).
static int control_readable(int fd, uint32_t mask, void *data) {
    struct server *s = data;
    char buf[512], reply[2048];
    struct sockaddr_un from;
    socklen_t len = sizeof from;
    ssize_t n;
    while ((n = recvfrom(fd, buf, sizeof buf - 1, MSG_DONTWAIT, (struct sockaddr *)&from, &len)) > 0) {
        buf[n] = 0;
        unsigned code;
        int value, index, w, h;
        if (sscanf(buf, "size %d %d %d", &index, &w, &h) == 3) {
            // A new resolution for a screen, live: KWin resizes the screen to match.
            if (index < 1 || index > MAX_SCREENS || !s->screens[index - 1] || w < 320 || h < 200 || w > 16384 ||
                h > 16384) {
                snprintf(reply, sizeof reply, "error bad screen or size");
            } else {
                if (index - 1 < s->n_config) s->config[index - 1].width = w, s->config[index - 1].height = h;
                wlr_xdg_toplevel_set_size(s->screens[index - 1]->toplevel, w, h);
                snprintf(reply, sizeof reply, "ok");
            }
        } else if (sscanf(buf, "key %u %d", &code, &value) == 2) {
            handle_key(s, code, value, reply, sizeof reply);
            len = sizeof from;
            continue;  // no reply: keys are fire-and-forget
        } else {
            ft_vr_command(buf, reply, sizeof reply);
        }
        if (len > offsetof(struct sockaddr_un, sun_path))
            sendto(fd, reply, strlen(reply), MSG_DONTWAIT, (struct sockaddr *)&from, len);
        len = sizeof from;
    }
    return 0;
}

static int open_control_socket(void) {
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    const char name[] = "ft_screens";
    memcpy(addr.sun_path + 1, name, sizeof name - 1);
    if (bind(fd, (struct sockaddr *)&addr, offsetof(struct sockaddr_un, sun_path) + 1 + sizeof name - 1) != 0) {
        wlr_log(WLR_ERROR, "can't bind @ft_screens (another ft-screens running?)");
        close(fd);
        return -1;
    }
    return fd;
}

static int stop(int sig, void *data) {
    wl_display_terminate(((struct server *)data)->display);
    return 0;
}

// ---------------------------------------------------------------- setup

static void keyboard_led(struct wlr_keyboard *kb, uint32_t leds) {}
static const struct wlr_keyboard_impl keyboard_impl = {.name = "ft-screens-keyboard", .led_update = keyboard_led};

static bool setup_dmabuf(struct server *s) {
    struct stat st;
    const char *node = "/dev/dri/renderD128";
    if (stat(node, &st) != 0) {
        wlr_log(WLR_ERROR, "no %s", node);
        return false;
    }
    struct wlr_linux_dmabuf_feedback_v1 fb = {.main_device = st.st_rdev};
    wl_array_init(&fb.tranches);
    struct wlr_linux_dmabuf_feedback_v1_tranche *tr = wlr_linux_dmabuf_feedback_add_tranche(&fb);
    tr->target_device = st.st_rdev;
    const uint32_t formats[] = {DRM_FORMAT_XRGB8888, DRM_FORMAT_ARGB8888};
    for (size_t f = 0; f < 2; ++f) {
        uint64_t mods[64];
        const int n = ft_vr_modifiers(formats[f], mods, 64);
        for (int i = 0; i < n; ++i) wlr_drm_format_set_add(&tr->formats, formats[f], mods[i]);
        wlr_log(WLR_INFO, "dmabuf: format 0x%x, %d modifiers SteamVR can import", formats[f], n);
    }
    struct wlr_linux_dmabuf_v1 *dmabuf = wlr_linux_dmabuf_v1_create(s->display, 4, &fb);
    wlr_linux_dmabuf_feedback_v1_finish(&fb);
    return dmabuf != NULL;
}

int main(int argc, char **argv) {
    struct server s = {0};
    const char *socket_name = "ft-screens-0";
    char **command = NULL;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--socket") == 0 && i + 1 < argc) {
            socket_name = argv[++i];
        } else if (strcmp(argv[i], "--screen") == 0 && i + 1 < argc && s.n_config < MAX_SCREENS) {
            struct config *c = &s.config[s.n_config];
            c->metres = 0;
            if (sscanf(argv[++i], "%dx%d@%lf", &c->width, &c->height, &c->metres) < 2) {
                fprintf(stderr, "bad --screen %s (want WxH@METRES)\n", argv[i]);
                return 2;
            }
            if (c->metres <= 0) c->metres = 1.5 * c->width / 1920.0;
            ++s.n_config;
        } else if (strcmp(argv[i], "--") == 0) {
            command = &argv[i + 1];
            break;
        } else {
            fprintf(stderr, "usage: %s [--socket NAME] [--screen WxH@METRES]... [-- COMMAND ARGS...]\n", argv[0]);
            return 2;
        }
    }
    if (s.n_config == 0) s.config[s.n_config++] = (struct config){3440, 1440, 2.4};

    setvbuf(stdout, NULL, _IOLBF, 0);  // vr.cpp prints to stdout; keep it in order with the log
    wlr_log_init(WLR_INFO, NULL);
    if (!ft_vr_init()) return 1;

    s.display = wl_display_create();
    s.loop = wl_display_get_event_loop(s.display);
    wl_list_init(&s.buffers);
    wlr_compositor_create(s.display, 6, NULL);
    wlr_subcompositor_create(s.display);
    const uint32_t shm_formats[] = {DRM_FORMAT_ARGB8888, DRM_FORMAT_XRGB8888};  // wlroots wants DRM codes
    wlr_shm_create(s.display, 1, shm_formats, 2);
    if (!setup_dmabuf(&s)) return 1;
    wlr_data_device_manager_create(s.display);

    s.xdg_shell = wlr_xdg_shell_create(s.display, 3);
    s.new_toplevel.notify = new_toplevel;
    wl_signal_add(&s.xdg_shell->events.new_toplevel, &s.new_toplevel);
    s.decoration = wlr_xdg_decoration_manager_v1_create(s.display);
    s.new_decoration.notify = new_decoration;
    wl_signal_add(&s.decoration->events.new_toplevel_decoration, &s.new_decoration);

    s.seat = wlr_seat_create(s.display, "seat0");
    wlr_keyboard_init(&s.keyboard, &keyboard_impl, "ft-screens-keyboard");
    struct xkb_context *xkb = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    struct xkb_keymap *keymap = xkb_keymap_new_from_names(xkb, NULL, XKB_KEYMAP_COMPILE_NO_FLAGS);
    wlr_keyboard_set_keymap(&s.keyboard, keymap);
    xkb_keymap_unref(keymap);
    xkb_context_unref(xkb);
    wlr_seat_set_keyboard(s.seat, &s.keyboard);
    wlr_seat_set_capabilities(s.seat, WL_SEAT_CAPABILITY_POINTER | WL_SEAT_CAPABILITY_KEYBOARD);

    if (wl_display_add_socket(s.display, socket_name) != 0) {
        wlr_log(WLR_ERROR, "can't create socket %s (another ft-screens?)", socket_name);
        return 1;
    }
    char path[256];
    snprintf(path, sizeof path, "%s/%s", getenv("XDG_RUNTIME_DIR") ? getenv("XDG_RUNTIME_DIR") : "/tmp", socket_name);
    wlr_log(WLR_INFO, "listening on %s; %d screen(s) configured", path, s.n_config);

    const int control = open_control_socket();
    if (control < 0) return 1;
    wl_event_loop_add_fd(s.loop, control, WL_EVENT_READABLE, control_readable, &s);

    s.tick = wl_event_loop_add_timer(s.loop, tick, &s);
    wl_event_source_timer_update(s.tick, 11);
    wl_event_loop_add_signal(s.loop, SIGINT, stop, &s);
    wl_event_loop_add_signal(s.loop, SIGTERM, stop, &s);
    wl_event_loop_add_signal(s.loop, SIGCHLD, child_exited, &s);

    if (command && command[0]) {
        s.child = fork();
        if (s.child == 0) {
            setenv("WAYLAND_DISPLAY", path, 1);
            execvp(command[0], command);
            perror(command[0]);
            _exit(127);
        }
    }

    wl_display_run(s.display);

    wlr_log(WLR_INFO, "stopping");
    if (s.child > 0) kill(s.child, SIGTERM);
    wl_display_destroy_clients(s.display);
    ft_vr_shutdown();
    wl_display_destroy(s.display);
    return 0;
}
