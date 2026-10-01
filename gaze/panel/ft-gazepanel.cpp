// ft-gazepanel: the gaze calibration panel (docs/design.md, gaze/README.md). A SteamVR overlay
// fixed to the headset, so wherever you turn your head it stays in the same place in your
// view: a dot drawn at a head-relative direction is exactly that direction from the headset,
// which is what the gaze service needs to know where you were looking. It's drawn on the CPU
// and takes no input: the gaze service (gaze/ft-gazed) drives it, and the pointer helper
// passes it your presses (calaccept, calquit).
//
// The panel sits POINTER-like at --distance (1.5 m, about where Frametop's screens are, so
// the eyes converge as they do in use). "quick" is a small square, QUICK_DEG across, for the
// one-dot check; "full" is FULL_DEG across (4:3), with a solid background whose brightness the
// service sets per round (pupil size changes with it, and the tracker's error with it).
//
// Control socket: abstract unix datagram "@ft_gazepanel" (--socket NAME); a sender with an
// address gets "ok" or "error ...":
//   show quick|full              the panel, empty, in front of you
//   hide
//   bg <0..1>                    the background's brightness (full)
//   dot <yaw> <pitch> <state> [<progress 0..1>]
//                                the dot, head-relative degrees (yaw +left, pitch +up); state:
//                                look, capture (a ring filling to progress), done,
//                                fail, off
//   title <text> / text <text>   a line at the top / at the bottom (empty to clear)
//   ping
//
// Options: --watch-stdin (quit when stdin closes: the service runs it), --socket NAME,
// --distance METRES. Runs in the dev container (gaze/build.sh builds it into gaze/build).
#include <openvr.h>

#define STB_TRUETYPE_IMPLEMENTATION
#include "stb_truetype.h"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <thread>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
constexpr double kQuickDeg = 16;      // QUICK_DEG: the one-dot check's square
constexpr double kFullDeg = 64;       // FULL_DEG: the full calibration's width (4:3)
constexpr int kQuickPx = 320, kFullW = 1024, kFullH = 768;
std::atomic<bool> g_stop{false};

// ---------------------------------------------------------------- text (as screens/keyboard.cpp)

stbtt_fontinfo g_font;
std::vector<unsigned char> g_fontData;
bool g_fontOk = false;
struct Glyph {
    std::vector<unsigned char> bitmap;
    int w = 0, h = 0, xoff = 0, yoff = 0, advance = 0;
};
std::map<std::pair<uint32_t, int>, Glyph> g_glyphs;

void LoadFont() {
    std::string path;
    if (FILE *p = popen("fc-match -f '%{file}' 'Noto Sans' 2>/dev/null", "r")) {
        char buf[512];
        if (std::fgets(buf, sizeof buf, p)) path = buf;
        pclose(p);
    }
    if (path.empty()) path = "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf";
    if (FILE *f = std::fopen(path.c_str(), "rb")) {
        std::fseek(f, 0, SEEK_END);
        g_fontData.resize(size_t(std::ftell(f)));
        std::fseek(f, 0, SEEK_SET);
        g_fontOk = std::fread(g_fontData.data(), 1, g_fontData.size(), f) == g_fontData.size() &&
                   stbtt_InitFont(&g_font, g_fontData.data(), stbtt_GetFontOffsetForIndex(g_fontData.data(), 0));
        std::fclose(f);
    }
    if (!g_fontOk) std::fprintf(stderr, "ft-gazepanel: no font (%s); no text\n", path.c_str());
}

const Glyph &GetGlyph(uint32_t cp, int size) {
    auto [it, fresh] = g_glyphs.try_emplace({cp, size});
    Glyph &g = it->second;
    if (fresh) {
        const float scale = stbtt_ScaleForPixelHeight(&g_font, float(size));
        unsigned char *b = stbtt_GetCodepointBitmap(&g_font, 0, scale, int(cp), &g.w, &g.h, &g.xoff, &g.yoff);
        if (b) g.bitmap.assign(b, b + size_t(g.w) * g.h), stbtt_FreeBitmap(b, nullptr);
        int adv, lsb;
        stbtt_GetCodepointHMetrics(&g_font, int(cp), &adv, &lsb);
        g.advance = int(std::lround(adv * scale));
    }
    return g;
}

std::vector<uint32_t> Codepoints(const std::string &s) {
    std::vector<uint32_t> out;
    for (const unsigned char *p = (const unsigned char *)s.c_str(); *p;) {
        uint32_t c = *p++;
        int more = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : c >= 0xC0 ? 1 : 0;
        if (more) c &= 0x3Fu >> more;
        for (; more && (*p & 0xC0) == 0x80; --more) c = c << 6 | (*p++ & 0x3F);
        out.push_back(c);
    }
    return out;
}

// ---------------------------------------------------------------- the picture

struct Panel {
    bool full = false;
    int w = kQuickPx, h = kQuickPx;
    double wDeg = kQuickDeg;  // across
    std::vector<uint8_t> px;
    double bg = 0.05;
    std::string title, text;
    bool dotOn = false;
    double dotYaw = 0, dotPitch = 0, progress = 0;
    std::string state = "off";
};

void Blend(Panel &p, int x, int y, double r, double g, double b, double a) {
    if (x < 0 || y < 0 || x >= p.w || y >= p.h || a <= 0) return;
    uint8_t *q = &p.px[(size_t(y) * p.w + x) * 4];
    a = std::min(a, 1.0);
    q[0] = uint8_t(std::lround(q[0] + (r * 255 - q[0]) * a));
    q[1] = uint8_t(std::lround(q[1] + (g * 255 - q[1]) * a));
    q[2] = uint8_t(std::lround(q[2] + (b * 255 - q[2]) * a));
    q[3] = uint8_t(std::lround(q[3] + (255 - q[3]) * a));
}

// A filled disc, or (inner > 0) a ring from inner to outer radius, anti-aliased; with sweep < 1,
// only that share of the ring, clockwise from the top.
void Disc(Panel &p, double cx, double cy, double outer, double inner, double r, double g, double b, double a,
          double sweep = 1) {
    const int x0 = int(cx - outer - 1), x1 = int(cx + outer + 1), y0 = int(cy - outer - 1), y1 = int(cy + outer + 1);
    for (int y = y0; y <= y1; ++y)
        for (int x = x0; x <= x1; ++x) {
            const double dx = x + 0.5 - cx, dy = y + 0.5 - cy, d = std::hypot(dx, dy);
            double cover = std::clamp(outer - d + 0.5, 0.0, 1.0);
            if (inner > 0) cover = std::min(cover, std::clamp(d - inner + 0.5, 0.0, 1.0));
            if (sweep < 1) {
                double ang = std::atan2(dx, -dy) / (2 * M_PI);  // 0 at the top, clockwise
                if (ang < 0) ang += 1;
                if (ang > sweep) continue;
            }
            Blend(p, x, y, r, g, b, a * cover);
        }
}

void Text(Panel &p, const std::string &s, int size, int cx, int cy, double lum) {
    if (!g_fontOk || s.empty()) return;
    const auto cps = Codepoints(s);
    int width = 0;
    for (uint32_t cp : cps) width += GetGlyph(cp, size).advance;
    int ascent, descent, gap;
    stbtt_GetFontVMetrics(&g_font, &ascent, &descent, &gap);
    const float scale = stbtt_ScaleForPixelHeight(&g_font, float(size));
    int x = cx - width / 2;
    const int baseline = cy + int(std::lround((ascent + descent) * scale / 2));
    for (uint32_t cp : cps) {
        const Glyph &g = GetGlyph(cp, size);
        for (int gy = 0; gy < g.h; ++gy)
            for (int gx = 0; gx < g.w; ++gx)
                Blend(p, x + g.xoff + gx, baseline + g.yoff + gy, lum, lum, lum, g.bitmap[size_t(gy) * g.w + gx] / 255.0);
        x += g.advance;
    }
}

// Head-relative direction -> panel pixel: the panel is a plane `d` in front of the headset.
void ToPixel(const Panel &p, double yaw, double pitch, double &x, double &y) {
    const double yr = yaw * M_PI / 180, pr = pitch * M_PI / 180;
    const double half = std::tan(p.wDeg * M_PI / 360);  // half the width, per unit of distance
    const double X = -std::tan(yr), Y = std::tan(pr) / std::cos(yr);
    x = (X / (2 * half) + 0.5) * p.w;
    y = (0.5 - Y / (2 * half) * p.w / p.h) * p.h;
}

void Draw(Panel &p) {
    p.px.assign(size_t(p.w) * p.h * 4, 0);
    const double pxPerDeg = p.w / p.wDeg;
    if (p.full) {
        for (size_t i = 0; i < p.px.size(); i += 4)
            p.px[i] = p.px[i + 1] = p.px[i + 2] = uint8_t(std::lround(p.bg * 255)), p.px[i + 3] = 255;
    } else {
        // The quick check: a dim rounded square, see-through, so it's clear of what's behind.
        const double r = p.w * 0.12;
        for (int y = 0; y < p.h; ++y)
            for (int x = 0; x < p.w; ++x) {
                const double dx = std::max({r - x - 0.5, x + 0.5 - (p.w - r), 0.0});
                const double dy = std::max({r - y - 0.5, y + 0.5 - (p.h - r), 0.0});
                const double cover = std::clamp(r - std::hypot(dx, dy) + 0.5, 0.0, 1.0);
                Blend(p, x, y, 0.06, 0.06, 0.07, 0.82 * cover);
            }
    }
    const bool light = p.full && p.bg > 0.5;  // a dark dot on the bright round
    const double ink = light ? 0.0 : 1.0, faint = light ? 0.2 : 0.75;
    const int titleSize = int(pxPerDeg * (p.full ? 1.5 : 1.1)), textSize = int(pxPerDeg * (p.full ? 1.2 : 0.9));
    Text(p, p.title, titleSize, p.w / 2, int(titleSize * 1.2), faint);
    Text(p, p.text, textSize, p.w / 2, p.h - int(textSize * 1.3), faint);
    if (!p.dotOn || p.state == "off") return;
    double x, y;
    ToPixel(p, p.dotYaw, p.dotPitch, x, y);
    const double core = 0.22 * pxPerDeg;
    if (p.state == "look") {
        // Still, so the panel looks solid and is drawn again only when something changes.
        const double rr = 0.75 * pxPerDeg;
        Disc(p, x, y, rr, rr - 0.12 * pxPerDeg, ink, ink, ink, 0.6);
        Disc(p, x, y, core, 0, ink, ink, ink, 1);
    } else if (p.state == "capture") {
        const double rr = 0.75 * pxPerDeg;
        Disc(p, x, y, rr, rr - 0.12 * pxPerDeg, ink, ink, ink, 0.25);
        Disc(p, x, y, rr, rr - 0.12 * pxPerDeg, 0.3, 0.85, 1.0, 1, std::clamp(p.progress, 0.0, 1.0));
        Disc(p, x, y, core, 0, ink, ink, ink, 1);
    } else if (p.state == "done") {
        Disc(p, x, y, 0.75 * pxPerDeg, 0, 0.25, 0.85, 0.4, 0.9);
        Disc(p, x, y, core, 0, 1, 1, 1, 1);
    } else if (p.state == "fail") {
        Disc(p, x, y, 0.75 * pxPerDeg, 0.6 * pxPerDeg, 0.95, 0.35, 0.3, 0.9);
        Disc(p, x, y, core, 0, ink, ink, ink, 1);
    }
}


}  // namespace

int main(int argc, char **argv) {
    bool watchStdin = false;
    std::string sockName = "ft_gazepanel";
    double distance = 1.5;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--watch-stdin")) watchStdin = true;
        else if (!std::strcmp(argv[i], "--socket") && i + 1 < argc) sockName = argv[++i];
        else if (!std::strcmp(argv[i], "--distance") && i + 1 < argc) distance = std::clamp(std::atof(argv[++i]), 0.5, 5.0);
        else {
            std::fprintf(stderr, "usage: %s [--watch-stdin] [--socket NAME] [--distance METRES]\n", argv[0]);
            return 2;
        }
    }
    std::signal(SIGINT, [](int) { g_stop = true; });
    std::signal(SIGTERM, [](int) { g_stop = true; });
    if (watchStdin)
        std::thread([] {
            char c[256];
            while (read(0, c, sizeof c) > 0) {
            }
            g_stop = true;
        }).detach();

    int sock = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::memcpy(addr.sun_path + 1, sockName.data(), std::min(sockName.size(), sizeof addr.sun_path - 2));
    if (bind(sock, reinterpret_cast<sockaddr *>(&addr), socklen_t(offsetof(sockaddr_un, sun_path) + 1 + sockName.size())) != 0) {
        std::fprintf(stderr, "ft-gazepanel: @%s is taken (another copy running?)\n", sockName.c_str());
        return 1;
    }

    // As Frametop's other SteamVR clients: background first, so we never start vrserver.
    vr::EVRInitError err = vr::VRInitError_None;
    vr::VR_Init(&err, vr::VRApplication_Background);
    if (err == vr::VRInitError_None) {
        vr::VR_Shutdown();
        vr::VR_Init(&err, vr::VRApplication_Overlay);
    }
    if (err != vr::VRInitError_None) {
        std::fprintf(stderr, "ft-gazepanel: SteamVR: %s\n", vr::VR_GetVRInitErrorAsEnglishDescription(err));
        return 1;
    }
    vr::IVROverlay *ov = vr::VROverlay();
    vr::VROverlayHandle_t h = vr::k_ulOverlayHandleInvalid;
    if (ov->CreateOverlay("frametop.gazepanel", "Frametop gaze calibration", &h) != vr::VROverlayError_None) {
        std::fprintf(stderr, "ft-gazepanel: can't create the overlay (another copy running?)\n");
        return 1;
    }
    ov->SetOverlaySortOrder(h, 250);  // in front of Frametop's screens and the pointer's dot
    LoadFont();

    Panel p;
    bool visible = false, dirty = false;
    auto place = [&] {
        const double half = std::tan(p.wDeg * M_PI / 360);
        vr::HmdMatrix34_t m{};
        m.m[0][0] = m.m[1][1] = m.m[2][2] = 1;
        m.m[2][3] = float(-distance);
        ov->SetOverlayTransformTrackedDeviceRelative(h, vr::k_unTrackedDeviceIndex_Hmd, &m);
        ov->SetOverlayWidthInMeters(h, float(2 * distance * half));
    };
    std::fprintf(stderr, "ft-gazepanel running: @%s, %.2f m\n", sockName.c_str(), distance);

    while (!g_stop) {
        char buf[512];
        sockaddr_un from{};
        socklen_t fromLen = sizeof from;
        ssize_t n;
        while ((n = recvfrom(sock, buf, sizeof buf - 1, 0, reinterpret_cast<sockaddr *>(&from), &fromLen)) > 0) {
            buf[n] = 0;
            std::string reply = "ok";
            char word[16] = "", state[16] = "";
            double a = 0, b = 0, c = 0;
            if (!std::strncmp(buf, "show ", 5)) {
                p.full = !std::strcmp(buf + 5, "full");
                p.w = p.full ? kFullW : kQuickPx;
                p.h = p.full ? kFullH : kQuickPx;
                p.wDeg = p.full ? kFullDeg : kQuickDeg;
                p.title.clear(), p.text.clear(), p.dotOn = false, p.state = "off";
                place();
                ov->ShowOverlay(h);
                visible = dirty = true;
            } else if (!std::strcmp(buf, "hide")) {
                ov->HideOverlay(h);
                visible = false;
            } else if (std::sscanf(buf, "bg %lf", &a) == 1) {
                p.bg = std::clamp(a, 0.0, 1.0), dirty = true;
            } else if (std::sscanf(buf, "dot %lf %lf %15s %lf", &a, &b, state, &c) >= 3) {
                p.dotYaw = a, p.dotPitch = b, p.state = state, p.progress = c;
                p.dotOn = std::strcmp(state, "off") != 0, dirty = true;
            } else if (!std::strncmp(buf, "title", 5)) {
                p.title = buf[5] == ' ' ? buf + 6 : "", dirty = true;
            } else if (!std::strncmp(buf, "text", 4)) {
                p.text = buf[4] == ' ' ? buf + 5 : "", dirty = true;
            } else if (std::sscanf(buf, "%15s", word) == 1 && !std::strcmp(word, "ping")) {
                reply = visible ? "ok shown" : "ok hidden";
            } else {
                reply = "error unknown command";
            }
            if (fromLen > offsetof(sockaddr_un, sun_path))
                sendto(sock, reply.data(), reply.size(), 0, reinterpret_cast<sockaddr *>(&from), fromLen);
            fromLen = sizeof from;
        }
        vr::VREvent_t ev;
        while (vr::VRSystem()->PollNextEvent(&ev, sizeof ev))
            if (ev.eventType == vr::VREvent_Quit) {
                vr::VRSystem()->AcknowledgeQuit_Exiting();
                g_stop = true;
            }
        if (visible && dirty) {
            Draw(p);
            ov->SetOverlayRaw(h, p.px.data(), uint32_t(p.w), uint32_t(p.h), 4);
            dirty = false;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(visible ? 10 : 50));
    }
    ov->DestroyOverlay(h);
    vr::VR_Shutdown();
    return 0;
}
