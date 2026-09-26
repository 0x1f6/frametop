// ft-pointer: the universal 3D mouse's brain (OpenVR overlay client, runs in the dev container).
//
// input-relay.py (pointer mode) sends mouse commands here; this program keeps the
// cursor, does collision against SteamVR's overlays, draws the free-space dot, and
// sends the ft_pointer driver the exact pose of its virtual controller.
//
//   relay -> @ft_pointer_helper -> ft-pointer -> @ft_pointer -> ft_pointer driver (inside vrserver)
//
// Cursor model:
//   - anchor: head position at the last recenter; yaw/pitch: direction from it (mouse-driven).
//   - Every frame a ray from the anchor is tested against every visible overlay
//     (ComputeOverlayIntersection). On a hit the cursor sits on that surface; otherwise
//     it floats `distance` metres out and a small dot overlay is shown there, which the
//     laser can hit, so SteamVR never draws a free-flying laser.
//   - Then the line of sight from the eye (not the anchor) to that point is tested too:
//     after the head moves, something nearer can cover the point, and the cursor goes on
//     whatever you see under it (panels close together in view, at different depths).
//   - Looks: the compositor ignores live changes to dashboard.laserRayWidthScale (only
//     the dashboard's own Settings screen reloads it), so the beam can't be switched
//     off per device. Instead the laser starts POINTER_ORIGIN_FRACTION (0.95) of the way
//     from the eye to the cursor, along the line of sight: what's left of the beam is a
//     few centimetres long and effectively invisible, and SteamVR's hit dot (sized by
//     distance from the origin) becomes tiny. Our own white dot is the visible cursor
//     everywhere: a non-interactive dot on panels (the laser passes through it), and an
//     interactive one in free space (the laser lands on it instead of flying off).
//   - The controller ray starts at the eye and aims at the cursor point. Everything here
//     is computed in the standing universe; the pose sent to the driver is converted to
//     SteamVR's raw tracking space (drivers report raw poses; on the Frame the standing
//     origin is ~1.6 m above the raw one, so sending standing coordinates put the laser
//     origin 1.6 m above the head). While our device
//     owns the dashboard pointer, dashboard.laserRayWidthScale is 0 so only the dot shows.
//     It's restored when a controller takes the pointer back.
//
// Headset off: when SteamVR says nobody is wearing the headset, the pointer is released and
// stays off until it's worn again, so the displays can sleep (see the main loop). While the
// pointer is off the helper also stops listing overlays with vrcmd, whose connection every
// second kept SteamVR from going to standby.
//
// Last used wins: when a real controller moves (picked up), the pointer is released
// at once (driver "hide", which also drops its hand role hint), so the controller gets
// its role and laser back. The next mouse input reconnects and claims the laser again.
// SteamVR gives a contested hand role to the most recently used device, and a held
// Frame controller counts as used (touch sensors). If our device hasn't got the hand
// role within a second of waking, the pointer is released (no orphan white dot) and
// mouse input can't wake it again for 2 s.
//
// Laser mode: with the dashboard closed, SteamVR keeps its laser mouse off until a
// click (the first click on a panel only turned it on, the second one clicked), and a
// laser that leaves every panel turns it off again. While the pointer is awake, the
// helper shows frametop.pointer.lasermode: a transparent 1 mm overlay 50 m below the
// head with VROverlayFlags_MakeOverlaysInteractiveIfVisible, which keeps SteamVR's
// laser mouse mode on as long as it's visible. It's hidden whenever the pointer is
// released, so controllers and VR games get the normal behaviour back.
//
// Tilt: while the left button is held (dragging a panel by its grab bar, which SteamVR
// moves rigidly with the controller), pressing the right button enters tilt mode. The
// right press is not forwarded; mouse motion then rotates the virtual controller around
// the grab point (horizontal: about the vertical axis, vertical: about the view's
// horizontal axis), so the panel turns around that pivot. The tilt accumulates for the
// whole drag: after the right button is released, the rotation stays applied (about the
// moving cursor point) so the grabbed panel keeps its new orientation, and pressing right
// again continues from it. Releasing the left button drops the panel; the tilted pose (and
// the drag lock) are held 0.5 s longer, because SteamVR's dashboard finishes a floating
// move up to 150 ms after the release (UndockedOverlay.endFloatingWindowMove measures the
// push distance first) and reads the controller pose again then.
//
// Scene-graph overlays: the dashboard's dock (valve.steam.gamepadui.bar) and the controls
// under floating windows (valve.steam.gamepadui.floatingfooter, undock and friends) have
// no texture (0x0) and a placeholder width, so ComputeOverlayIntersection never hits
// them. For those the ray is tested against the overlay's plane, within
// POINTER_SCENE_RADIUS (0.5 m) of its origin; the laser-catching dot sits 5 cm behind the
// plane, so the laser reaches the buttons and still lands on the dot between them.
// Only absolutely placed 0x0 overlays count as scene-graph: gamescope's app panels (the
// desktops) also report 0x0, but they're placed as dashboard tabs, stay up when the
// dashboard closes, and ComputeOverlayIntersection hits them normally.
//
// Panel edges: off a panel, the cursor stays on that panel's plane while it's within
// POINTER_EDGE_REACH (0.3 m) of the last point it touched, instead of jumping to
// POINTER_DISTANCE. A floating panel's resize margins and the window controls under it
// sit just outside the panel, and the laser has to start in front of that plane to reach
// them (a controller's laser always does: it starts at the hand). Like on scene-graph
// planes, the laser-catching dot sits 5 cm behind the plane.
//
// Drag lock: while the left button is held, the cursor keeps the distance it had at the
// press and collision is frozen, so dragging past a panel's edge (resizing, moving)
// doesn't jump the cursor to free space or swap in the laser-catching dot, which made
// SteamVR's resize snap back.
//
// Placement (for layout): SteamVR keeps a floating panel's position inside the
// dashboard, where nothing outside can set it, so the helper carries panels like a user
// would. It measures the panel (md::ScanPanel), aims the device at its grab bar
// (LAYOUT_GRAB_OFFSET, 7.5 cm below the bottom edge; the bands at 2-4 and 14-26 cm are
// other controls), presses, moves, and releases. While grabbed, the panel follows the
// device rigidly, except that the dashboard accelerates fast translations (0.1 m in 0.3 s
// moved it 0.19 m and turned it 8.5 deg, in jerky 25 ms steps right after the press). So
// the device hovers first, and the move is split into a rotation about the device origin
// (the eye) at 60 deg/s and a smooth 60 Hz slide at LAYOUT_SLIDE_SPEED (0.5 m/s; tested
// exact from 0.07 to 1 m/s). Scroll pushes along the panel normal, but only in whole notches of
// about 7 cm, so it isn't used. The result is measured again, and the move repeated up to
// twice while it's more than 1.5 cm or 1 deg off.
//
// Commands (datagrams on @ft_pointer_helper): show, hide, recenter, move <dyaw> <dpitch>,
// reload (re-read the settings below), debug (toggle a twice-a-second state log),
// and btn/scroll lines, which are forwarded to the driver unchanged. For layouts, with a
// reply datagram to the sender's (abstract) address:
//   place <overlay> <x> <y> <z> <yaw> <pitch> [roll [grab]]: centre in the standing
//     universe; the front faces back along the direction (yaw, pitch), turned by roll
//     (counterclockwise as seen, degrees) -> "ok ..." | "error ..."
//   measure <overlay> -> "ok cx cy cz width height xx xy xz yx yy yz zx zy zz" (centre,
//     size, and the panel's right, up, and front vectors)
//   head -> "ok x y z yaw pitch"
//   grabprobe <overlay>: log where below the panel SteamVR's laser hits something (to
//     find the grab bar again if a SteamVR update moves it)
//
// Settings (~/.config/frametop.conf): POINTER_DISTANCE (m, 1.5), POINTER_CURSOR_DEG
// (angular size of the dot, 0.4), POINTER_LASER_WIDTH (controller beam width to restore, 0.8),
// POINTER_ORIGIN_FRACTION (0.95): the laser starts this far along the eye-to-cursor line,
// but never closer than POINTER_ORIGIN_MARGIN (0.15 m) to the cursor point: SteamVR's
// small controls (undock, frame buttons) float a few centimetres in front of their
// panel, and a laser that starts behind them can't hit them.
#include <openvr.h>

#include "vrmath.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {

using namespace md;

std::map<std::string, std::string> ReadConfig() {
    std::map<std::string, std::string> conf;
    const char *home = std::getenv("HOME");
    std::ifstream in(std::string(home ? home : "") + "/.config/frametop.conf");
    std::string line;
    while (std::getline(in, line)) {
        line = line.substr(0, line.find('#'));
        const auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        auto trim = [](std::string s) {
            s.erase(0, s.find_first_not_of(" \t"));
            s.erase(s.find_last_not_of(" \t") + 1);
            return s;
        };
        conf[trim(line.substr(0, eq))] = trim(line.substr(eq + 1));
    }
    return conf;
}

double ConfDouble(const std::map<std::string, std::string> &c, const char *key, double fallback) {
    auto it = c.find(key);
    return it == c.end() ? fallback : std::atof(it->second.c_str());
}

int AbstractSocket(const char *name, bool bindIt) {
    const int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (bindIt) {
        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        std::memcpy(addr.sun_path + 1, name, std::strlen(name));
        const socklen_t len = offsetof(sockaddr_un, sun_path) + 1 + std::strlen(name);
        if (bind(fd, reinterpret_cast<sockaddr *>(&addr), len) != 0) {
            std::perror("bind @ft_pointer_helper (already running?)");
            std::exit(1);
        }
    }
    return fd;
}

void SendTo(int fd, const char *name, const std::string &msg) {
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::memcpy(addr.sun_path + 1, name, std::strlen(name));
    const socklen_t len = offsetof(sockaddr_un, sun_path) + 1 + std::strlen(name);
    sendto(fd, msg.data(), msg.size(), 0, reinterpret_cast<sockaddr *>(&addr), len);
}

// Overlay keys, refreshed in the background from `vrcmd --overlays` (OpenVR has no
// public call to enumerate other apps' overlays). Hidden ones are listed too: the
// window controls under a floating panel only appear while something hovers the
// panel, and the cursor has to find them the moment they do, not a second later.
// Paused while the pointer is off: each vrcmd run connects to SteamVR as a new app, and a new
// app every second kept SteamVR (and the headset's displays) from going to standby.
class OverlayList {
public:
    void Start() {
        thread_ = std::thread([this] {
            while (running_) {
                if (!paused_) Refresh();
                // Wait a second, or less when the pointer wakes (refresh right away then).
                for (int i = 0; i < 10 && running_; ++i) {
                    const bool wasPaused = paused_;
                    std::this_thread::sleep_for(std::chrono::milliseconds(100));
                    if (wasPaused && !paused_) break;
                }
            }
        });
    }
    void SetPaused(bool paused) { paused_ = paused; }
    void Stop() {
        running_ = false;
        if (thread_.joinable()) thread_.join();
    }
    std::vector<std::string> Keys() {
        std::lock_guard<std::mutex> guard(lock_);
        return keys_;
    }

private:
    void Refresh() {
        FILE *p = popen("LD_LIBRARY_PATH=/opt/steamvr/bin/linuxarm64 /opt/steamvr/bin/linuxarm64/vrcmd --overlays 2>/dev/null", "r");
        if (!p) return;
        std::vector<std::string> keys;
        char line[1024];
        while (std::fgets(line, sizeof line, p)) {
            // 'key' -- 'name', WxH visible VROverlayType_...
            if (line[0] != '\'') continue;
            const char *end = std::strchr(line + 1, '\'');
            if (!end) continue;
            const std::string key(line + 1, size_t(end - (line + 1)));
            const std::string rest(end);
            if (rest.find("Thumbnail") != std::string::npos || rest.find("Subview") != std::string::npos) continue;
            if (key.rfind("system.pointer", 0) == 0 || key.rfind("system.cursor", 0) == 0 ||
                key.rfind("frametop.pointer", 0) == 0 || key.rfind("frametop.guide", 0) == 0 ||
                key == "system.HeadsetView" || key == "system.toast")
                continue;
            keys.push_back(key);
        }
        pclose(p);
        std::lock_guard<std::mutex> guard(lock_);
        keys_ = std::move(keys);
    }

    std::thread thread_;
    std::atomic<bool> paused_{false};
    std::atomic<bool> running_{true};
    std::mutex lock_;
    std::vector<std::string> keys_;
};

vr::HmdMatrix34_t Billboard(Vec3 at, Vec3 eye) {
    // Overlay faces +Z; point +Z at the eye, keep +Y roughly up.
    const Vec3 z = Normalize(eye - at);
    const Vec3 x = Normalize(Cross({0, 1, 0}, z));
    const Vec3 y = Cross(z, x);
    vr::HmdMatrix34_t m{};
    const Vec3 cols[3] = {x, y, z};
    for (int c = 0; c < 3; ++c) {
        m.m[0][c] = float(cols[c].x);
        m.m[1][c] = float(cols[c].y);
        m.m[2][c] = float(cols[c].z);
    }
    m.m[0][3] = float(at.x);
    m.m[1][3] = float(at.y);
    m.m[2][3] = float(at.z);
    return m;
}

std::vector<uint8_t> DotTexture(int size) {
    // White dot with a dark rim, soft edge, transparent outside.
    std::vector<uint8_t> px(size * size * 4, 0);
    const double c = (size - 1) / 2.0, r = size * 0.42, rim = size * 0.10;
    for (int y = 0; y < size; ++y)
        for (int x = 0; x < size; ++x) {
            const double d = std::hypot(x - c, y - c);
            const double a = std::clamp(r - d + 0.5, 0.0, 1.0);
            const bool inner = d < r - rim;
            uint8_t *p = &px[(y * size + x) * 4];
            const uint8_t v = inner ? 255 : 40;
            p[0] = p[1] = p[2] = v;
            p[3] = uint8_t(a * 235);
        }
    return px;
}

}  // namespace

// Placement speeds (see "Placement" at the top).
constexpr double kPlaceDegPerSec = 60;      // tested: 40 deg/s is applied exactly

int main() {
    double freeDistance = 1.5, cursorDeg = 0.4, originFraction = 0.95, originMargin = 0.15, sceneRadius = 0.5,
           edgeReach = 0.3, grabOffset = 0.075,
           slideSpeed = 0.5;
    auto loadConfig = [&] {
        const auto conf = ReadConfig();
        freeDistance = std::clamp(ConfDouble(conf, "POINTER_DISTANCE", 1.5), 0.3, 10.0);
        cursorDeg = std::clamp(ConfDouble(conf, "POINTER_CURSOR_DEG", 0.4), 0.05, 5.0);
        originFraction = std::clamp(ConfDouble(conf, "POINTER_ORIGIN_FRACTION", 0.95), 0.0, 0.98);
        originMargin = std::clamp(ConfDouble(conf, "POINTER_ORIGIN_MARGIN", 0.15), 0.0, 1.0);
        sceneRadius = std::clamp(ConfDouble(conf, "POINTER_SCENE_RADIUS", 0.5), 0.05, 2.0);
        edgeReach = std::clamp(ConfDouble(conf, "POINTER_EDGE_REACH", 0.3), 0.0, 2.0);
        grabOffset = std::clamp(ConfDouble(conf, "LAYOUT_GRAB_OFFSET", 0.075), 0.0, 1.0);
        slideSpeed = std::clamp(ConfDouble(conf, "LAYOUT_SLIDE_SPEED", 0.5), 0.02, 2.0);
    };
    loadConfig();
    const float laserWidth = float(ConfDouble(ReadConfig(), "POINTER_LASER_WIDTH", 0.8));

    vr::EVRInitError err = vr::VRInitError_None;
    while (true) {
        vr::VR_Init(&err, vr::VRApplication_Overlay);
        if (err == vr::VRInitError_None) break;
        std::fprintf(stderr, "waiting for SteamVR: %s\n", vr::VR_GetVRInitErrorAsEnglishDescription(err));
        std::this_thread::sleep_for(std::chrono::seconds(2));
    }
    auto *sys = vr::VRSystem();
    auto *overlay = vr::VROverlay();

    vr::VROverlayHandle_t cursor = vr::k_ulOverlayHandleInvalid;
    overlay->CreateOverlay("frametop.pointer.cursor", "Frametop pointer", &cursor);
    const int texSize = 64;
    auto tex = DotTexture(texSize);
    overlay->SetOverlayRaw(cursor, tex.data(), texSize, texSize, 4);
    overlay->SetOverlayInputMethod(cursor, vr::VROverlayInputMethod_Mouse);  // the laser can land on it
    overlay->SetOverlaySortOrder(cursor, 200);
    // Same dot, not interactive, drawn on panels at the hit point; the laser passes through.
    vr::VROverlayHandle_t marker = vr::k_ulOverlayHandleInvalid;
    overlay->CreateOverlay("frametop.pointer.marker", "Frametop pointer marker", &marker);
    overlay->SetOverlayRaw(marker, tex.data(), texSize, texSize, 4);
    overlay->SetOverlayInputMethod(marker, vr::VROverlayInputMethod_None);
    overlay->SetOverlaySortOrder(marker, 201);
    // Laser mode (see the top of the file).
    vr::VROverlayHandle_t laserMode = vr::k_ulOverlayHandleInvalid;
    overlay->CreateOverlay("frametop.pointer.lasermode", "Frametop pointer laser mode", &laserMode);
    std::vector<uint8_t> clear(4 * 4 * 4, 0);
    overlay->SetOverlayRaw(laserMode, clear.data(), 4, 4, 4);
    overlay->SetOverlayWidthInMeters(laserMode, 0.001f);
    overlay->SetOverlayInputMethod(laserMode, vr::VROverlayInputMethod_Mouse);  // the flag needs an input method
    overlay->SetOverlayFlag(laserMode, vr::VROverlayFlags_MakeOverlaysInteractiveIfVisible, true);
    vr::HmdMatrix34_t below{};
    below.m[0][0] = below.m[1][1] = below.m[2][2] = 1;
    below.m[1][3] = -50;
    overlay->SetOverlayTransformTrackedDeviceRelative(laserMode, vr::k_unTrackedDeviceIndex_Hmd, &below);
    bool laserModeShown = false;
    // Controller beams keep the user's width; nothing here changes it any more.
    vr::VRSettings()->SetFloat("dashboard", "laserRayWidthScale", laserWidth);

    const int in = AbstractSocket("ft_pointer_helper", true);
    const int out = AbstractSocket(nullptr, false);
    OverlayList overlays;
    overlays.Start();
    std::map<std::string, vr::VROverlayHandle_t> handles;
    std::map<std::string, bool> sceneGraph;  // no texture: plane test instead of ComputeOverlayIntersection
    std::map<std::string, bool> visible;     // refreshed every 50 ms
    auto lastVisible = std::chrono::steady_clock::now();
    // The plane of the last panel the cursor was on, and the last point on it (panel edges).
    Vec3 edgePoint, edgeNormal, edgeLast;
    std::string edgeKey;

    bool active = false, recenter = false, anchored = false;
    using Clock = std::chrono::steady_clock;
    Clock::time_point lastMouse{}, claimAt{}, claimRelease{}, wokeAt{}, noWakeUntil{};
    bool claimPending = false, claimHeld = false;
    // Tilt mode (see top of file).
    bool leftHeld = false, tilting = false, tiltStart = false, swallowedRight = false;
    double tiltYaw = 0, tiltPitch = 0;
    double dragDistance = 0, lastDistance = 1.5;  // drag lock: distance from the anchor at the press
    Clock::time_point dropHoldUntil{};  // after a left release: keep the drag pose this long
    bool debug = false;
    std::string lastHit;
    auto lastDebug = Clock::now();
    vr::VROverlayHandle_t systemPointer = vr::k_ulOverlayHandleInvalid;
    overlay->FindOverlay("system.pointer", &systemPointer);
    Vec3 pivot, tiltOrigin, lastPoint, lastOrigin, lastAim{0, 0, -1};
    Basis tiltBasis{};
    bool headsetOff = false;  // nobody is wearing the headset (see the main loop)
    auto wake = [&](Clock::time_point t) {
        if (t < noWakeUntil || headsetOff) return;
        wokeAt = t;
        active = true;
        recenter = true;
        SendTo(out, "ft_pointer", "show");
        claimPending = true;  // take the laser without clicking, once SteamVR has bound the device
        claimAt = t + std::chrono::milliseconds(300);
    };
    Vec3 anchor;
    double yaw = 0, pitch = 0;
    vr::TrackedDeviceIndex_t ours = vr::k_unTrackedDeviceIndexInvalid;
    auto lastSlow = std::chrono::steady_clock::now() - std::chrono::seconds(10);

    // --- Panel placement (see "Placement" at the top of the file) ---
    // Device pose, given in the standing universe, sent to the driver in raw space.
    auto sendPose = [&](Vec3 originStanding, const Basis &b) {
        vr::TrackedDevicePose_t s, r;
        sys->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0, &s, 1);
        sys->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseRawAndUncalibrated, 0, &r, 1);
        const auto &S = s.mDeviceToAbsoluteTracking, &R = r.mDeviceToAbsoluteTracking;
        auto toRaw = [&](Vec3 v) { return Rotate(R, RotateInverse(S, v)); };
        const Vec3 o = Position(R) + toRaw(originStanding - Position(S));
        double q[4];
        BasisQuat({toRaw(b.x), toRaw(b.y), toRaw(b.z)}, q);
        char msg[200];
        std::snprintf(msg, sizeof msg, "posq %.5f %.5f %.5f %.6f %.6f %.6f %.6f", o.x, o.y, o.z, q[0], q[1], q[2], q[3]);
        SendTo(out, "ft_pointer", msg);
    };
    auto sleepMs = [](int ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); };
    auto headPos = [&] {
        vr::TrackedDevicePose_t s;
        sys->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0, &s, 1);
        return std::make_pair(s.bPoseIsValid, Position(s.mDeviceToAbsoluteTracking));
    };
    // Borrow the device and the laser for a placement: connect, claim, laser mode on.
    auto borrow = [&] {
        SendTo(out, "ft_pointer", "show");
        overlay->ShowOverlay(laserMode);
        overlay->HideOverlay(cursor);
        overlay->HideOverlay(marker);
        sleepMs(active ? 50 : 400);  // a fresh connect needs SteamVR to bind the device
        SendTo(out, "ft_pointer", "btn a 1");
        sleepMs(60);
        SendTo(out, "ft_pointer", "btn a 0");
    };
    auto giveBack = [&] {
        if (!active) {
            SendTo(out, "ft_pointer", "hide");
            overlay->HideOverlay(laserMode);
            laserModeShown = false;
        }
    };
    auto findPanel = [&](const char *key, Panel &p, Vec3 &eye) -> std::string {
        vr::VROverlayHandle_t h;
        if (overlay->FindOverlay(key, &h) != vr::VROverlayError_None) return std::string("no overlay ") + key;
        bool valid;
        std::tie(valid, eye) = headPos();
        if (!valid) return "no head pose (headset off?)";
        p = ScanPanel(h, eye, 0.5);
        if (!p.found) return std::string("panel not visible: ") + key;
        return "";
    };

    // Spike: aim down from the panel's bottom edge, 1 cm a step, and log where SteamVR's
    // laser hits something (the hit dot shows) and whether the window controls are up.
    auto grabProbe = [&](const char *key) {
        Panel p;
        Vec3 eye;
        const std::string err = findPanel(key, p, eye);
        if (!err.empty()) {
            std::printf("grabprobe: %s\n", err.c_str());
            std::fflush(stdout);
            return;
        }
        borrow();
        vr::VROverlayHandle_t footer = vr::k_ulOverlayHandleInvalid;
        overlay->FindOverlay("valve.steam.gamepadui.floatingfooter", &footer);
        const Vec3 bottom = p.center - p.basis.y * (p.height / 2);
        std::printf("grabprobe %s: center (%.3f %.3f %.3f) %.3f x %.3f m\n", key, p.center.x, p.center.y, p.center.z,
                    p.width, p.height);
        for (int cm = -5; cm <= 45; ++cm) {
            const Vec3 target = bottom - p.basis.y * (cm / 100.0);
            sendPose(eye, AimBasis(target - eye));
            sleepMs(90);
            const bool dot = systemPointer != vr::k_ulOverlayHandleInvalid && overlay->IsOverlayVisible(systemPointer);
            const bool foot = footer != vr::k_ulOverlayHandleInvalid && overlay->IsOverlayVisible(footer);
            std::printf("  %+3d cm below the bottom edge: steamvr_dot=%d footer=%d", cm, dot, foot);
            if (foot) {
                vr::ETrackingUniverseOrigin uo;
                vr::HmdMatrix34_t t{};
                if (overlay->GetOverlayTransformAbsolute(footer, &uo, &t) == vr::VROverlayError_None) {
                    const Vec3 f = Position(t) - p.center;
                    std::printf(" footer at panel (%.3f %.3f %.3f)", Dot(f, p.basis.x), Dot(f, p.basis.y),
                                Dot(f, p.basis.z));
                }
            }
            std::printf("\n");
        }
        std::fflush(stdout);
        giveBack();
    };

    // Carry a floating panel so its centre lands on `target` with frame `bt` (see
    // "Placement" at the top). Returns "ok ..." or "error ...".
    auto place = [&](const char *key, Vec3 target, const Basis &bt, double grabBelow) -> std::string {
        Panel p;
        Vec3 eye;
        std::string err = findPanel(key, p, eye);
        if (!err.empty()) return "error " + err;
        auto offBy = [&](const Panel &q, double &cm, double &deg) {
            cm = Length(q.center - target) * 100;
            const double c = (Dot(q.basis.x, bt.x) + Dot(q.basis.y, bt.y) + Dot(q.basis.z, bt.z) - 1) / 2;
            deg = std::acos(std::clamp(c, -1.0, 1.0)) * 180 / M_PI;
        };
        double cm, deg;
        int moves = 0;
        borrow();
        for (int attempt = 0; attempt < 3; ++attempt) {
            offBy(p, cm, deg);
            if (cm < 1.5 && deg < 1.0) break;
            // The rigid motion that takes the panel to the target: rotate by R, then move.
            auto turn = [&](Vec3 v) { return FromBasis(bt, ToBasis(p.basis, v)); };
            double q[4];
            BasisQuat({turn({1, 0, 0}), turn({0, 1, 0}), turn({0, 0, 1})}, q);
            const double angle = 2 * std::acos(std::clamp(q[0], -1.0, 1.0));
            const Vec3 axis = std::sin(angle / 2) > 1e-6 ? Normalize({q[1], q[2], q[3]}) : Vec3{0, 1, 0};
            const Vec3 grab = p.center - p.basis.y * (p.height / 2 + grabBelow);
            const Basis d0 = AimBasis(grab - eye);
            const Vec3 o1 = target + turn(eye - p.center);  // device origin at the end
            ++moves;
            sendPose(eye, d0);
            sleepMs(150);  // hover: the window controls come up
            SendTo(out, "ft_pointer", "btn trigger 1");
            sleepMs(150);
            // 1. Rotate about the device origin (the eye): the dashboard applies it exactly.
            const int rsteps = std::max(4, int(angle * 180 / M_PI / kPlaceDegPerSec * 60));
            for (int i = 1; i <= rsteps; ++i) {
                const double a = angle * i / rsteps;
                auto r = [&](Vec3 v) { return RotateAbout(v, axis, a); };
                sendPose(eye, {r(d0.x), r(d0.y), r(d0.z)});
                sleepMs(16);
            }
            // 2. Slide the device slowly: fast moves are accelerated by the dashboard.
            const Basis d1{turn(d0.x), turn(d0.y), turn(d0.z)};
            const int tsteps = std::max(4, int(Length(o1 - eye) / slideSpeed * 60));
            for (int i = 1; i <= tsteps; ++i) {
                sendPose(eye + (o1 - eye) * (double(i) / tsteps), d1);
                sleepMs(16);
            }
            sleepMs(100);
            SendTo(out, "ft_pointer", "btn trigger 0");
            sleepMs(600);  // the dashboard re-reads the pose up to 150 ms after the release
            err = findPanel(key, p, eye);
            if (!err.empty()) break;
        }
        giveBack();
        if (!err.empty()) return "error after the move: " + err;
        offBy(p, cm, deg);
        char msg[160];
        std::snprintf(msg, sizeof msg, "ok %s off by %.1f cm, %.1f deg after %d move%s", key, cm, deg, moves,
                      moves == 1 ? "" : "s");
        std::printf("place: %s\n", msg);
        std::fflush(stdout);
        return msg;
    };

    std::printf("ft-pointer running: free distance %.2f m, dot %.2f deg\n", freeDistance, cursorDeg);
    std::fflush(stdout);

    while (true) {
        // The headset off: SteamVR drops the HMD's activity to idle as soon as it comes off.
        // An awake pointer (a connected controller, SteamVR's laser mode forced on) kept the
        // displays from sleeping, so it's released at once, and the mouse can't wake it until
        // the headset is back on (then the first mouse input does).
        const auto level = sys->GetTrackedDeviceActivityLevel(vr::k_unTrackedDeviceIndex_Hmd);
        headsetOff = level == vr::k_EDeviceActivityLevel_Idle || level == vr::k_EDeviceActivityLevel_Standby ||
                     level == vr::k_EDeviceActivityLevel_Idle_Timeout;
        if (headsetOff && active) {
            active = false;
            claimPending = claimHeld = false;
            overlay->HideOverlay(cursor);
            overlay->HideOverlay(marker);
            SendTo(out, "ft_pointer", "btn a 0");
            SendTo(out, "ft_pointer", "hide");
            std::printf("headset off: pointer released\n");
            std::fflush(stdout);
        }

        // Commands from the relay.
        char buf[256];
        ssize_t n;
        sockaddr_un from{};
        socklen_t fromLen = sizeof from;
        while ((n = recvfrom(in, buf, sizeof buf - 1, 0, reinterpret_cast<sockaddr *>(&from), &fromLen)) > 0) {
            buf[n] = 0;
            // Reply to the sender (the layout tool binds an abstract address to get answers).
            const sockaddr_un sender = from;
            const socklen_t senderLen = fromLen;
            fromLen = sizeof from;
            auto reply = [&](const std::string &msg) {
                if (senderLen > offsetof(sockaddr_un, sun_path))
                    sendto(out, msg.data(), msg.size(), 0, reinterpret_cast<const sockaddr *>(&sender), senderLen);
            };
            const bool mouseInput = std::strncmp(buf, "move", 4) == 0 || std::strncmp(buf, "btn", 3) == 0 ||
                                    std::strncmp(buf, "scroll", 6) == 0;
            if (mouseInput) lastMouse = Clock::now();
            // Any mouse input wakes the pointer (after a controller took over, or a helper restart).
            if (!active && mouseInput) wake(Clock::now());
            double a, b;
            char key[128];
            double px, py, pz, pyaw, ppitch, proll = 0, pgrab = -1;
            if (std::sscanf(buf, "grabprobe %127s", key) == 1) {
                grabProbe(key);
                continue;
            }
            if (std::sscanf(buf, "place %127s %lf %lf %lf %lf %lf %lf %lf", key, &px, &py, &pz, &pyaw, &ppitch, &proll,
                            &pgrab) >= 6) {
                reply(place(key, {px, py, pz}, PanelBasis(pyaw, ppitch, proll), pgrab >= 0 ? pgrab : grabOffset));
                continue;
            }
            if (std::sscanf(buf, "measure %127s", key) == 1) {
                Panel p;
                Vec3 eye;
                const std::string err = findPanel(key, p, eye);
                char msg[400] = "";
                if (err.empty())
                    std::snprintf(msg, sizeof msg, "ok %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f",
                                  p.center.x, p.center.y, p.center.z, p.width, p.height, p.basis.x.x, p.basis.x.y,
                                  p.basis.x.z, p.basis.y.x, p.basis.y.y, p.basis.y.z, p.basis.z.x, p.basis.z.y,
                                  p.basis.z.z);
                reply(err.empty() ? msg : "error " + err);
                continue;
            }
            if (std::strncmp(buf, "head", 4) == 0) {
                vr::TrackedDevicePose_t h;
                sys->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0, &h, 1);
                const Vec3 e = Position(h.mDeviceToAbsoluteTracking);
                const Vec3 f = Rotate(h.mDeviceToAbsoluteTracking, {0, 0, -1});
                char msg[200];
                std::snprintf(msg, sizeof msg, "ok %.4f %.4f %.4f %.2f %.2f", e.x, e.y, e.z,
                              std::atan2(-f.x, -f.z) * 180 / M_PI, std::asin(std::clamp(f.y, -1.0, 1.0)) * 180 / M_PI);
                reply(h.bPoseIsValid ? msg : "error no head pose (headset off?)");
                continue;
            }
            if (std::strncmp(buf, "debug", 5) == 0) {
                debug = !debug;
                std::printf("debug %s\n", debug ? "on" : "off");
                std::fflush(stdout);
                continue;
            }
            if (std::strncmp(buf, "btn trigger 1", 13) == 0) {
                leftHeld = true;
                dragDistance = lastDistance;
                tiltYaw = tiltPitch = 0;  // a new drag starts untilted
                dropHoldUntil = {};
            } else if (std::strncmp(buf, "btn trigger 0", 13) == 0) {
                leftHeld = false;
                tilting = false;
                // Hold the drag pose (tilt, frozen distance) while SteamVR finishes the drop.
                dropHoldUntil = Clock::now() + std::chrono::milliseconds(500);
            } else if (std::strncmp(buf, "btn b 1", 7) == 0 && leftHeld) {
                tilting = tiltStart = swallowedRight = true;  // right press while dragging: tilt, no right-click
                continue;
            } else if (std::strncmp(buf, "btn b 0", 7) == 0 && swallowedRight) {
                tilting = swallowedRight = false;
                continue;
            }
            if (tilting && std::sscanf(buf, "move %lf %lf", &a, &b) == 2) {
                tiltYaw += a;
                tiltPitch = std::clamp(tiltPitch + b, -80.0, 80.0);
                continue;
            }
            if (std::sscanf(buf, "move %lf %lf", &a, &b) == 2) {
                if (!anchored) recenter = true;
                yaw += a;
                while (yaw > 180) yaw -= 360;
                while (yaw < -180) yaw += 360;
                pitch = std::clamp(pitch + b, -85.0, 85.0);
            } else if (std::strncmp(buf, "recenter", 8) == 0) {
                recenter = true;
            } else if (std::strncmp(buf, "reload", 6) == 0) {
                loadConfig();
                std::printf("reloaded: free distance %.2f m, dot %.2f deg, origin %.2f\n", freeDistance, cursorDeg,
                            originFraction);
                std::fflush(stdout);
            } else if (std::strncmp(buf, "show", 4) == 0) {
                if (!active) wake(Clock::now());
            } else if (std::strncmp(buf, "hide", 4) == 0) {
                active = false;
                overlay->HideOverlay(cursor);
                overlay->HideOverlay(marker);
                SendTo(out, "ft_pointer", "hide");
            } else {
                SendTo(out, "ft_pointer", buf);  // btn, scroll
            }
        }

        vr::TrackedDevicePose_t all[vr::k_unMaxTrackedDeviceCount];
        sys->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0.011f, all, vr::k_unMaxTrackedDeviceCount);
        const vr::TrackedDevicePose_t &hmd = all[0];
        vr::TrackedDevicePose_t hmdRaw;
        sys->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseRawAndUncalibrated, 0.011f, &hmdRaw, 1);
        const auto tnow = Clock::now();

        // Claim pulse (switchlaserhand on the driver's "a" button, no click).
        if (claimPending && tnow >= claimAt) {
            SendTo(out, "ft_pointer", "btn a 1");
            claimPending = false;
            claimHeld = true;
            claimRelease = tnow + std::chrono::milliseconds(60);
        } else if (claimHeld && tnow >= claimRelease) {
            SendTo(out, "ft_pointer", "btn a 0");
            claimHeld = false;
        }

        // The overlay list is only needed while the pointer is awake (see OverlayList).
        overlays.SetPaused(!active || headsetOff);

        // Laser mode on while the pointer is awake.
        if (active != laserModeShown) {
            laserModeShown = active;
            if (active) overlay->ShowOverlay(laserMode);
            else overlay->HideOverlay(laserMode);
            if (debug) std::printf("laser mode %s\n", active ? "forced on" : "released");
            if (debug) std::fflush(stdout);
        }

        // Didn't get the hand role (a held controller keeps it): release, back off.
        if (active && tnow - wokeAt > std::chrono::seconds(1) && ours != vr::k_unTrackedDeviceIndexInvalid &&
            sys->GetControllerRoleForTrackedDeviceIndex(ours) == vr::TrackedControllerRole_Invalid) {
            active = false;
            claimPending = claimHeld = false;
            overlay->HideOverlay(cursor);
            overlay->HideOverlay(marker);
            SendTo(out, "ft_pointer", "btn a 0");
            SendTo(out, "ft_pointer", "hide");
            noWakeUntil = tnow + std::chrono::seconds(2);
            std::printf("no hand role (a controller is in use): pointer released\n");
            std::fflush(stdout);
        }

        // Last used wins: a real controller being moved releases the pointer.
        if (active && tnow - lastMouse > std::chrono::milliseconds(500)) {
            for (vr::TrackedDeviceIndex_t i = 1; i < vr::k_unMaxTrackedDeviceCount; ++i) {
                if (i == ours || !all[i].bPoseIsValid) continue;
                if (sys->GetTrackedDeviceClass(i) != vr::TrackedDeviceClass_Controller) continue;
                const auto &v = all[i].vVelocity.v, &w = all[i].vAngularVelocity.v;
                const double speed = std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
                const double spin = std::sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
                if (speed > 0.35 || spin > 2.0) {
                    active = false;
                    claimPending = claimHeld = false;
                    overlay->HideOverlay(cursor);
                    overlay->HideOverlay(marker);
                    SendTo(out, "ft_pointer", "btn a 0");
                    SendTo(out, "ft_pointer", "hide");
                    std::printf("controller %u moved: pointer released\n", i);
                    std::fflush(stdout);
                    break;
                }
            }
        }
        const auto &hm = hmd.mDeviceToAbsoluteTracking.m;
        const Vec3 eye{hm[0][3], hm[1][3], hm[2][3]};

        if (recenter && hmd.bPoseIsValid) {
            anchor = eye;
            const Vec3 f{-hm[0][2], -hm[1][2], -hm[2][2]};
            yaw = std::atan2(-f.x, -f.z) * 180 / M_PI;
            pitch = std::asin(std::clamp(f.y, -1.0, 1.0)) * 180 / M_PI;
            anchored = true;
            recenter = false;
        }

        // Slow work, once a second: overlay handles, our device index, laser width.
        const auto now = std::chrono::steady_clock::now();
        if (now - lastSlow > std::chrono::seconds(1)) {
            lastSlow = now;
            handles.clear();
            for (const auto &key : overlays.Keys()) {
                vr::VROverlayHandle_t h;
                if (overlay->FindOverlay(key.c_str(), &h) != vr::VROverlayError_None) continue;
                handles[key] = h;
                // Scene-graph overlays are placed absolutely. Other overlays can report no
                // texture too (gamescope's app panels, placed as dashboard tabs, share theirs
                // from another process), and ComputeOverlayIntersection handles those.
                uint32_t tw = 0, th = 0;
                overlay->GetOverlayTextureSize(h, &tw, &th);
                vr::VROverlayTransformType tt = vr::VROverlayTransform_Invalid;
                overlay->GetOverlayTransformType(h, &tt);
                // ft-screens' panels (frametop.screen.N) are 0x0 and absolute too (a shared
                // texture), but they're real panels of any size.
                sceneGraph[key] = (tw == 0 || th == 0) && tt == vr::VROverlayTransform_Absolute &&
                                  key.rfind("frametop.screen.", 0) != 0;
            }
            ours = vr::k_unTrackedDeviceIndexInvalid;
            for (vr::TrackedDeviceIndex_t i = 0; i < vr::k_unMaxTrackedDeviceCount; ++i) {
                char type[64] = "";
                sys->GetStringTrackedDeviceProperty(i, vr::Prop_ControllerType_String, type, sizeof type);
                if (std::strcmp(type, "ft_pointer") == 0) ours = i;
            }

        }

        if (now - lastVisible > std::chrono::milliseconds(50) || visible.size() != handles.size()) {
            lastVisible = now;
            visible.clear();
            for (const auto &[key, h] : handles) visible[key] = overlay->IsOverlayVisible(h);
        }

        if (active && anchored && hmd.bPoseIsValid && tilting) {
            // Rotate the device around the grab point; the grabbed panel turns with it.
            if (tiltStart) {
                pivot = lastPoint;
                tiltOrigin = lastOrigin;
                tiltBasis = AimBasis(lastAim);
                tiltStart = false;  // angles carry on from any earlier tilt in this drag
                overlay->HideOverlay(cursor);
                overlay->HideOverlay(marker);
            }
            const double yr = tiltYaw * M_PI / 180, pr = tiltPitch * M_PI / 180;
            const Vec3 up{0, 1, 0}, side = tiltBasis.x;
            auto turn = [&](Vec3 v) { return RotateAbout(RotateAbout(v, side, pr), up, yr); };
            const Vec3 originStanding = pivot + turn(tiltOrigin - pivot);
            const Basis b{turn(tiltBasis.x), turn(tiltBasis.y), turn(tiltBasis.z)};
            const auto &S = hmd.mDeviceToAbsoluteTracking, &R = hmdRaw.mDeviceToAbsoluteTracking;
            auto toRaw = [&](Vec3 v) { return Rotate(R, RotateInverse(S, v)); };  // directions: raw <- standing
            const Vec3 originRaw = Position(R) + toRaw(originStanding - eye);
            double q[4];
            BasisQuat({toRaw(b.x), toRaw(b.y), toRaw(b.z)}, q);
            char msg[200];
            std::snprintf(msg, sizeof msg, "posq %.5f %.5f %.5f %.6f %.6f %.6f %.6f", originRaw.x, originRaw.y,
                          originRaw.z, q[0], q[1], q[2], q[3]);
            SendTo(out, "ft_pointer", msg);
        } else if (active && anchored && hmd.bPoseIsValid) {
            const bool dragging = leftHeld || tnow < dropHoldUntil;
            if (!dragging) tiltYaw = tiltPitch = 0;  // drop finished: back to plain pointing
            const Vec3 dir = Direction(yaw, pitch);
            // Nearest visible overlay along a ray (frozen while dragging).
            struct Hit {
                double along = 1e9;
                std::string key;
                bool scene = false;
                Vec3 point, normal;
            };
            auto nearest = [&](Vec3 from, Vec3 d) {
                Hit h;
                for (const auto &[key, handle] : handles) {
                    if (!visible[key]) continue;
                    if (sceneGraph[key]) {
                        // Plane test: overlay origin and its +Z normal, within sceneRadius of the origin.
                        vr::ETrackingUniverseOrigin uo;
                        vr::HmdMatrix34_t t{};
                        if (overlay->GetOverlayTransformAbsolute(handle, &uo, &t) != vr::VROverlayError_None) continue;
                        const Vec3 center = Position(t), normal{t.m[0][2], t.m[1][2], t.m[2][2]};
                        const double denom = Dot(d, normal);
                        if (std::fabs(denom) < 1e-4) continue;
                        const double along = Dot(center - from, normal) / denom;
                        const Vec3 at = from + d * along;
                        if (along > 0.05 && along < h.along && std::sqrt(Dot(at - center, at - center)) <= sceneRadius)
                            h.along = along, h.key = key, h.scene = true, h.point = at, h.normal = normal;
                        continue;
                    }
                    vr::VROverlayIntersectionParams_t params{};
                    params.vSource = {float(from.x), float(from.y), float(from.z)};
                    params.vDirection = {float(d.x), float(d.y), float(d.z)};
                    params.eOrigin = vr::TrackingUniverseStanding;
                    vr::VROverlayIntersectionResults_t r{};
                    if (overlay->ComputeOverlayIntersection(handle, &params, &r) && r.fDistance > 0.05f &&
                        r.fDistance < h.along) {
                        h.along = r.fDistance, h.key = key, h.scene = false;
                        h.point = {r.vPoint.v[0], r.vPoint.v[1], r.vPoint.v[2]};
                        h.normal = {r.vNormal.v[0], r.vNormal.v[1], r.vNormal.v[2]};
                    }
                }
                return h;
            };
            Hit first;
            if (!dragging) first = nearest(anchor, dir);
            double best = first.along;
            std::string bestKey = first.key;
            bool bestScene = first.scene;
            Vec3 bestPoint = first.point, bestNormal = first.normal;
            bool onEdge = false;
            if (!dragging && best < 1e8 && !bestScene) {
                edgeKey = bestKey, edgePoint = bestPoint, edgeNormal = Normalize(bestNormal), edgeLast = bestPoint;
            } else if (!dragging && best >= 1e8 && !edgeKey.empty() && visible[edgeKey]) {
                // Just off a panel: stay on its plane (see "Panel edges" at the top).
                const double denom = Dot(dir, edgeNormal);
                if (std::fabs(denom) > 1e-4) {
                    const double along = Dot(edgePoint - anchor, edgeNormal) / denom;
                    const Vec3 at = anchor + dir * along;
                    if (along > 0.05 && std::sqrt(Dot(at - edgeLast, at - edgeLast)) <= edgeReach) {
                        best = along;
                        bestKey = edgeKey;
                        onEdge = true;
                    }
                }
            }
            // While dragging: keep the press-time distance and show the non-interactive marker.
            // On a scene-graph plane or a panel's edge: the laser-catching dot goes 5 cm behind it.
            double distance = dragging ? dragDistance : (best < 1e8 ? best : freeDistance);
            Vec3 point = anchor + dir * distance;
            bool occluded = false;
            if (!dragging) {
                // The cursor lands on what you see under it: the ray above starts at the anchor,
                // not the eye, so after leaning it can pick a panel that something nearer
                // covers from where you are now (panels close together in view, at different
                // depths). Anything in front of the point on the eye's line of sight wins.
                const double toPoint = std::sqrt(Dot(point - eye, point - eye));
                const Hit front = nearest(eye, Normalize(point - eye));
                if (front.along < toPoint - 0.02) {
                    occluded = true;
                    bestKey = front.key, bestScene = front.scene;
                    point = front.point;
                    if (!front.scene) edgeKey = front.key, edgePoint = front.point, edgeNormal = Normalize(front.normal),
                                      edgeLast = front.point;
                    distance = std::sqrt(Dot(point - anchor, point - anchor));
                    best = distance;
                    onEdge = false;
                }
                lastDistance = distance, lastHit = bestKey;
            }
            const bool onScene = !dragging && ((bestScene && best < 1e8) || onEdge);
            const bool onPanel = dragging || (best < 1e8 && !onScene);

            {
                // On a panel: the non-interactive marker, pulled 5 mm toward the eye so it
                // draws on top. In free space: the interactive dot the laser lands on.
                const vr::VROverlayHandle_t show = onPanel ? marker : cursor, hide = onPanel ? cursor : marker;
                const Vec3 at = onPanel ? point + Normalize(eye - point) * 0.005 : onScene ? point + dir * 0.05 : point;
                const double dist = std::sqrt(Dot(at - eye, at - eye));
                overlay->SetOverlayWidthInMeters(show, float(2 * dist * std::tan(cursorDeg * M_PI / 360)));
                auto m = Billboard(at, eye);
                overlay->SetOverlayTransformAbsolute(show, vr::TrackingUniverseStanding, &m);
                overlay->ShowOverlay(show);
                overlay->HideOverlay(hide);
            }

            // Controller ray: from the eye, aimed at the cursor point, converted from the
            // standing universe to raw tracking space via the HMD's pose in both.
            const Vec3 aimStanding = Normalize(point - eye);
            const auto &S = hmd.mDeviceToAbsoluteTracking, &R = hmdRaw.mDeviceToAbsoluteTracking;
            const Vec3 aim = Rotate(R, RotateInverse(S, aimStanding));  // raw <- head <- standing
            // Origin partway along the line of sight to the cursor (smaller hit dot).
            const double toPoint = std::sqrt(Dot(point - eye, point - eye));
            const double originDist = std::max(0.0, std::min(toPoint * originFraction, toPoint - originMargin));
            const Vec3 originStanding = eye + Normalize(point - eye) * originDist;
            const Vec3 eyeRaw = Position(R) + Rotate(R, RotateInverse(S, originStanding - eye));
            lastPoint = point, lastOrigin = originStanding, lastAim = aimStanding;  // tilt starts from here
            if (debug && tnow - lastDebug > std::chrono::milliseconds(500)) {
                lastDebug = tnow;
                if (systemPointer == vr::k_ulOverlayHandleInvalid) overlay->FindOverlay("system.pointer", &systemPointer);
                std::printf("dbg %s hit=%s dist=%.2f eye->point=%.2f origin=%.2f yaw=%.1f pitch=%.1f steamvr_dot=%d primary=%u\n",
                            dragging ? "DRAG" : occluded ? "INFRONT" : onEdge ? "EDGE" : onScene ? "SCENE" : (best < 1e8 ? "PANEL" : "FREE"), lastHit.empty() ? "-" : lastHit.c_str(),
                            distance, toPoint, originDist, yaw, pitch,
                            systemPointer != vr::k_ulOverlayHandleInvalid && overlay->IsOverlayVisible(systemPointer),
                            overlay->GetPrimaryDashboardDevice());
                std::fflush(stdout);
            }
            const double ayaw = std::atan2(-aim.x, -aim.z) * 180 / M_PI;
            const double apitch = std::asin(std::clamp(aim.y, -1.0, 1.0)) * 180 / M_PI;
            if (dragging && (tiltYaw != 0 || tiltPitch != 0)) {
                // Keep this drag's tilt applied, about the current cursor point.
                const double yr = tiltYaw * M_PI / 180, pr = tiltPitch * M_PI / 180;
                const Basis base = AimBasis(aimStanding);
                const Vec3 up{0, 1, 0}, side = base.x;
                auto turn = [&](Vec3 v) { return RotateAbout(RotateAbout(v, side, pr), up, yr); };
                const Vec3 o = point + turn(originStanding - point);
                const Basis b{turn(base.x), turn(base.y), turn(base.z)};
                auto toRaw = [&](Vec3 v) { return Rotate(R, RotateInverse(S, v)); };
                const Vec3 oRaw = Position(R) + toRaw(o - eye);
                double q[4];
                BasisQuat({toRaw(b.x), toRaw(b.y), toRaw(b.z)}, q);
                char msg[200];
                std::snprintf(msg, sizeof msg, "posq %.5f %.5f %.5f %.6f %.6f %.6f %.6f", oRaw.x, oRaw.y, oRaw.z, q[0],
                              q[1], q[2], q[3]);
                SendTo(out, "ft_pointer", msg);
            } else {
                char msg[160];
                std::snprintf(msg, sizeof msg, "pose %.5f %.5f %.5f %.4f %.4f", eyeRaw.x, eyeRaw.y, eyeRaw.z, ayaw, apitch);
                SendTo(out, "ft_pointer", msg);
            }
        }

        vr::VREvent_t ev;
        while (sys->PollNextEvent(&ev, sizeof ev)) {
            if (ev.eventType == vr::VREvent_Quit) {
                sys->AcknowledgeQuit_Exiting();

                overlays.Stop();
                vr::VR_Shutdown();
                return 0;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(8));
    }
}
