// The OpenVR side of ft-screens (vr.cpp), called from the wlroots compositor (compositor.c).
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct ft_dmabuf {
    int width, height;
    uint32_t format;    // DRM_FORMAT_*
    uint64_t modifier;  // DRM_FORMAT_MOD_*
    int n_planes;
    uint32_t offset[4], stride[4];
    int fd[4];
};

enum ft_event_type { FT_MOTION, FT_BUTTON, FT_SCROLL, FT_LEAVE, FT_QUIT };

struct ft_event {
    enum ft_event_type type;
    int screen;
    double x, y;      // FT_MOTION: buffer pixels from the top left
    uint32_t button;  // FT_BUTTON: linux BTN_*
    bool pressed;
    double dx, dy;    // FT_SCROLL: notches (positive dy: scroll down)
};

bool ft_vr_init(void);
void ft_vr_shutdown(void);
// Modifiers SteamVR can import for a DRM format. Returns the count (at most max).
int ft_vr_modifiers(uint32_t format, uint64_t *out, int max);
// The SteamVR dashboard is open (typing belongs to it then, not to the screens).
bool ft_vr_dashboard_visible(void);
// A panel for screen `index`, width in metres, placed in a row in front of the head.
void ft_vr_screen_create(int index, double metres, int count);
void ft_vr_screen_destroy(int index);
// Show a client buffer (identified by `key`) on the screen's panel. False if SteamVR
// can't import it.
bool ft_vr_screen_present(int index, const void *key, const struct ft_dmabuf *buf);
// A buffer is going away: drop its import.
void ft_vr_forget(const void *key);
// Poll panel input and SteamVR events.
void ft_vr_poll(void (*handle)(const struct ft_event *, void *), void *data);
// A command from the control socket (@ft_screens); writes the reply (see vr.cpp).
void ft_vr_command(const char *command, char *reply, int reply_size);

#ifdef __cplusplus
}
#endif
