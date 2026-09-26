// Small vector math shared by the pointer helper, the layout tool, and the probe, plus
// ScanPanel: measuring a floating dashboard panel, whose transform OpenVR won't give out.
// Header-only. Standing-universe coordinates unless a name says otherwise.
#pragma once

#include <openvr.h>

#include <cmath>

namespace md {

struct Vec3 {
    double x = 0, y = 0, z = 0;
};
inline Vec3 operator+(Vec3 a, Vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
inline Vec3 operator-(Vec3 a, Vec3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
inline Vec3 operator*(Vec3 a, double s) { return {a.x * s, a.y * s, a.z * s}; }
inline double Dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 Cross(Vec3 a, Vec3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
inline double Length(Vec3 a) { return std::sqrt(Dot(a, a)); }
inline Vec3 Normalize(Vec3 a) {
    const double n = Length(a);
    return n > 1e-9 ? a * (1.0 / n) : Vec3{0, 0, -1};
}

// yaw 0 = -Z (SteamVR forward), positive yaw turns left (about +Y), positive pitch looks up.
inline Vec3 Direction(double yawDeg, double pitchDeg) {
    const double y = yawDeg * M_PI / 180, p = pitchDeg * M_PI / 180;
    return {-std::sin(y) * std::cos(p), std::sin(p), -std::cos(y) * std::cos(p)};
}

// Rotation part of a pose matrix applied to a vector, and its transpose.
inline Vec3 Rotate(const vr::HmdMatrix34_t &m, Vec3 v) {
    return {m.m[0][0] * v.x + m.m[0][1] * v.y + m.m[0][2] * v.z, m.m[1][0] * v.x + m.m[1][1] * v.y + m.m[1][2] * v.z,
            m.m[2][0] * v.x + m.m[2][1] * v.y + m.m[2][2] * v.z};
}
inline Vec3 RotateInverse(const vr::HmdMatrix34_t &m, Vec3 v) {
    return {m.m[0][0] * v.x + m.m[1][0] * v.y + m.m[2][0] * v.z, m.m[0][1] * v.x + m.m[1][1] * v.y + m.m[2][1] * v.z,
            m.m[0][2] * v.x + m.m[1][2] * v.y + m.m[2][2] * v.z};
}
inline Vec3 Position(const vr::HmdMatrix34_t &m) { return {m.m[0][3], m.m[1][3], m.m[2][3]}; }

// Rodrigues: v rotated by angle (radians) about a unit axis.
inline Vec3 RotateAbout(Vec3 v, Vec3 axis, double angle) {
    const double c = std::cos(angle), s = std::sin(angle);
    return v * c + Cross(axis, v) * s + axis * (Dot(axis, v) * (1 - c));
}

// An orthonormal frame: device poses (-Z forward) and panels (+X right, +Y up, +Z out of the front).
struct Basis {
    Vec3 x, y, z;
};
// Coordinates of v in the basis, and back.
inline Vec3 ToBasis(const Basis &b, Vec3 v) { return {Dot(v, b.x), Dot(v, b.y), Dot(v, b.z)}; }
inline Vec3 FromBasis(const Basis &b, Vec3 v) { return b.x * v.x + b.y * v.y + b.z * v.z; }

// Device basis for a pointing direction with no roll: -Z along aim, +X horizontal.
inline Basis AimBasis(Vec3 aim) {
    const Vec3 z = Normalize(aim * -1.0);
    const Vec3 x = Normalize(Cross({0, 1, 0}, z));
    return {x, Cross(z, x), z};
}

// Panel basis for a panel facing the direction (yaw, pitch) points to. The front (+Z)
// faces back along that direction, toward whoever looks along it. roll turns the panel
// about its front normal, counterclockwise as you see it (90: a rotated-left monitor).
inline Basis PanelBasis(double yawDeg, double pitchDeg, double rollDeg = 0) {
    const Vec3 z = Direction(yawDeg, pitchDeg) * -1.0;
    const Vec3 x = Normalize(Cross({0, 1, 0}, z)), y = Cross(z, x);
    const double r = rollDeg * M_PI / 180, c = std::cos(r), s = std::sin(r);
    return {x * c + y * s, y * c - x * s, z};
}

// Quaternion (w, x, y, z) of a rotation whose matrix columns are the basis vectors.
inline void BasisQuat(const Basis &b, double q[4]) {
    const double m[3][3] = {{b.x.x, b.y.x, b.z.x}, {b.x.y, b.y.y, b.z.y}, {b.x.z, b.y.z, b.z.z}};
    const double trace = m[0][0] + m[1][1] + m[2][2];
    if (trace > 0) {
        const double s = 0.5 / std::sqrt(trace + 1);
        q[0] = 0.25 / s, q[1] = (m[2][1] - m[1][2]) * s, q[2] = (m[0][2] - m[2][0]) * s, q[3] = (m[1][0] - m[0][1]) * s;
    } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
        const double s = 2 * std::sqrt(1 + m[0][0] - m[1][1] - m[2][2]);
        q[0] = (m[2][1] - m[1][2]) / s, q[1] = 0.25 * s, q[2] = (m[0][1] + m[1][0]) / s, q[3] = (m[0][2] + m[2][0]) / s;
    } else if (m[1][1] > m[2][2]) {
        const double s = 2 * std::sqrt(1 + m[1][1] - m[0][0] - m[2][2]);
        q[0] = (m[0][2] - m[2][0]) / s, q[1] = (m[0][1] + m[1][0]) / s, q[2] = 0.25 * s, q[3] = (m[1][2] + m[2][1]) / s;
    } else {
        const double s = 2 * std::sqrt(1 + m[2][2] - m[0][0] - m[1][1]);
        q[0] = (m[1][0] - m[0][1]) / s, q[1] = (m[0][2] + m[2][0]) / s, q[2] = (m[1][2] + m[2][1]) / s, q[3] = 0.25 * s;
    }
}

// A panel measured by ScanPanel: centre, size, and frame (x right, y up, z out of the front).
struct Panel {
    bool found = false;
    Vec3 center;
    double width = 0, height = 0;
    Basis basis;
    int hits = 0;
};

// Cast rays from `from` over the whole sphere (step in degrees) at one overlay, and fit
// point = origin + u*U + v*V to the hits (least squares). ComputeOverlayIntersection
// works on floating dashboard panels, whose transforms aren't readable, and returns the
// texture coordinates of each hit; v runs bottom to top. It's local and fast: a 0.5-degree
// scan (about 230,000 rays) takes 0.1 s.
inline Panel ScanPanel(vr::VROverlayHandle_t h, Vec3 from, double step = 1.0) {
    Panel p;
    double ata[3][3] = {}, atb[3][3] = {};  // normal equations for [1 u v] -> (x, y, z)
    for (double pitch = -80; pitch <= 80; pitch += step)
        for (double yaw = -180; yaw < 180; yaw += step) {
            const Vec3 d = Direction(yaw, pitch);
            vr::VROverlayIntersectionParams_t params{};
            params.vSource = {float(from.x), float(from.y), float(from.z)};
            params.vDirection = {float(d.x), float(d.y), float(d.z)};
            params.eOrigin = vr::TrackingUniverseStanding;
            vr::VROverlayIntersectionResults_t hit{};
            if (!vr::VROverlay()->ComputeOverlayIntersection(h, &params, &hit)) continue;
            ++p.hits;
            const double row[3] = {1, hit.vUVs.v[0], hit.vUVs.v[1]};
            for (int i = 0; i < 3; ++i)
                for (int j = 0; j < 3; ++j) {
                    ata[i][j] += row[i] * row[j];
                    atb[i][j] += row[i] * hit.vPoint.v[j];
                }
        }
    if (p.hits < 6) return p;
    auto det3 = [](const double a[3][3]) {
        return a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) +
               a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]);
    };
    const double d = det3(ata);
    if (std::fabs(d) < 1e-12) return p;
    double x[3][3];  // x[k][j]: coefficient k (1, u, v) of coordinate j, by Cramer's rule
    for (int j = 0; j < 3; ++j)
        for (int k = 0; k < 3; ++k) {
            double t[3][3];
            for (int r = 0; r < 3; ++r)
                for (int c = 0; c < 3; ++c) t[r][c] = c == k ? atb[r][j] : ata[r][c];
            x[k][j] = det3(t) / d;
        }
    const Vec3 O{x[0][0], x[0][1], x[0][2]}, U{x[1][0], x[1][1], x[1][2]}, V{x[2][0], x[2][1], x[2][2]};
    p.found = true;
    p.center = O + U * 0.5 + V * 0.5;
    p.width = Length(U);
    p.height = Length(V);
    const Vec3 bx = Normalize(U), bz = Normalize(Cross(U, V));
    p.basis = {bx, Cross(bz, bx), bz};
    return p;
}

}  // namespace md
