// Small vector helpers for the tracker.
#pragma once

#include <array>
#include <cmath>

using V2 = std::array<double, 2>;
using V3 = std::array<double, 3>;

inline V3 operator+(V3 a, V3 b) { return {a[0] + b[0], a[1] + b[1], a[2] + b[2]}; }
inline V3 operator-(V3 a, V3 b) { return {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }
inline V3 operator*(V3 a, double s) { return {a[0] * s, a[1] * s, a[2] * s}; }
inline double dot(V3 a, V3 b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
inline double norm(V3 a) { return std::sqrt(dot(a, a)); }
inline V3 unit(V3 a) { double n = norm(a); return n > 0 ? a * (1 / n) : a; }

inline V2 operator+(V2 a, V2 b) { return {a[0] + b[0], a[1] + b[1]}; }
inline V2 operator-(V2 a, V2 b) { return {a[0] - b[0], a[1] - b[1]}; }
inline V2 operator*(V2 a, double s) { return {a[0] * s, a[1] * s}; }
inline double norm(V2 a) { return std::hypot(a[0], a[1]); }

inline double wrap_angle(double a) { return std::remainder(a, 2 * M_PI); }
