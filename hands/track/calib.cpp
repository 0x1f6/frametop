#include <cstdlib>
#include "calib.h"

#include <json/json.h>
#include <unistd.h>

#include <algorithm>

#include <fstream>
#include <iterator>
#include <memory>

namespace {

double theta_d(const Camera &c, double t) {
    const double t2 = t * t;
    return t * (1 + t2 * (c.k[0] + t2 * (c.k[1] + t2 * (c.k[2] + t2 * c.k[3]))));
}

// 4x4 transform (row-major) from a {plus_x, plus_z, position} pose.
void pose(const Json::Value &d, double scale, double T[4][4]) {
    V3 x{d["plus_x"][0].asDouble(), d["plus_x"][1].asDouble(), d["plus_x"][2].asDouble()};
    V3 z{d["plus_z"][0].asDouble(), d["plus_z"][1].asDouble(), d["plus_z"][2].asDouble()};
    V3 y{z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]};
    for (int i = 0; i < 3; ++i) {
        T[i][0] = x[i], T[i][1] = y[i], T[i][2] = z[i];
        T[i][3] = d["position"][i].asDouble() * scale;
        T[3][i] = 0;
    }
    T[3][3] = 1;
}

void mul(const double A[4][4], const double B[4][4], double C[4][4]) {
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j) {
            C[i][j] = 0;
            for (int k = 0; k < 4; ++k) C[i][j] += A[i][k] * B[k][j];
        }
}

void invert_rigid(const double A[4][4], double B[4][4]) {
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) B[i][j] = A[j][i];
    for (int i = 0; i < 3; ++i) B[i][3] = -(B[i][0] * A[0][3] + B[i][1] * A[1][3] + B[i][2] * A[2][3]);
    B[3][0] = B[3][1] = B[3][2] = 0, B[3][3] = 1;
}

bool read_json(const char *path, Json::Value &v, std::string &err) {
    std::ifstream f(path);
    Json::CharReaderBuilder b;
    std::string e;
    if (!f || !Json::parseFromStream(b, f, &v, &e)) {
        err = std::string(path) + ": " + (f ? e : "can't open");
        return false;
    }
    return true;
}

}  // namespace

V2 Camera::project_cam(V3 p) const {
    const double r = std::hypot(p[0], p[1]);
    const double s = r > 1e-12 ? theta_d(*this, std::atan2(r, p[2])) / r : 0;
    return {fx * p[0] * s + cx, fy * p[1] * s + cy};
}

V3 Camera::unproject(V2 uv) const {
    const double mx = (uv[0] - cx) / fx, my = (uv[1] - cy) / fy, td = std::hypot(mx, my);
    double t = td;
    for (int i = 0; i < 8; ++i) {  // Newton on theta_d(t) = td
        const double t2 = t * t;
        const double df = 1 + t2 * (3 * k[0] + t2 * (5 * k[1] + t2 * (7 * k[2] + t2 * 9 * k[3])));
        t = std::clamp(t - (theta_d(*this, t) - td) / df, 0.0, M_PI);
    }
    const double s = td > 1e-12 ? std::sin(t) / td : 1;
    return {mx * s, my * s, std::cos(t)};
}

V3 Camera::ray(V2 uv) const {
    const V3 c = unproject(uv);
    return {R[0][0] * c[0] + R[0][1] * c[1] + R[0][2] * c[2], R[1][0] * c[0] + R[1][1] * c[1] + R[1][2] * c[2],
            R[2][0] * c[0] + R[2][1] * c[1] + R[2][2] * c[2]};
}

V2 Camera::project(V3 head, double *depth) const {
    const V3 d = head - origin;
    const V3 c{R[0][0] * d[0] + R[1][0] * d[1] + R[2][0] * d[2], R[0][1] * d[0] + R[1][1] * d[1] + R[2][1] * d[2],
               R[0][2] * d[0] + R[1][2] * d[1] + R[2][2] * d[2]};
    if (depth) *depth = c[2];
    return project_cam(c);
}

double Camera::off_axis(V2 uv) const { return std::acos(std::clamp(unproject(uv)[2], -1.0, 1.0)) * 180 / M_PI; }

// A headset file such as /persist/xrservice.json. In the dev container the host's / is at
// /run/host (distrobox doesn't mount /persist); off the Frame, FRAME_JOB_DEVICE_ROOT can
// point at a folder with copies of them.
static std::string device_path(const char *path) {
    if (const char *root = std::getenv("FRAME_JOB_DEVICE_ROOT")) return std::string(root) + path;
    const std::string host = std::string("/run/host") + path;
    return access(path, R_OK) != 0 && access(host.c_str(), R_OK) == 0 ? host : path;
}

bool load_calibration(std::map<std::string, Camera> &out, std::string &err) {
    Json::Value rig, dev;
    if (!read_json(device_path("/persist/xrservice.json").c_str(), rig, err) ||
        !read_json(device_path("/persist/device_config.json").c_str(), dev, err))
        return false;
    double cad_from_cam0[4][4], cad_from_head[4][4], head_from_cad[4][4], head_from_cam0[4][4];
    pose(dev["cv"]["cad_from_cal"], 1.0, cad_from_cam0);
    pose(dev["head"], 1.0, cad_from_head);
    invert_rigid(cad_from_head, head_from_cad);
    mul(head_from_cad, cad_from_cam0, head_from_cam0);
    for (const Json::Value &c : rig["cameras"]) {
        Camera cam;
        cam.name = c["sourceCamera"].asString();
        cam.width = c["width"].asInt(), cam.height = c["height"].asInt();
        for (const Json::Value &in : c["intrinsics"]) {
            if (in["cameraModel"].asString() != "kb") continue;
            cam.fx = in["fx"].asDouble(), cam.fy = in["fy"].asDouble();
            cam.cx = in["cx"].asDouble(), cam.cy = in["cy"].asDouble();
            cam.k[0] = in["k1"].asDouble(), cam.k[1] = in["k2"].asDouble();
            cam.k[2] = in["k3"].asDouble(), cam.k[3] = in["k4"].asDouble();
        }
        double cam0_from_cam[4][4], head_from_cam[4][4];
        pose(c["extrinsics"], 1e-3, cam0_from_cam);
        mul(head_from_cam0, cam0_from_cam, head_from_cam);
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) cam.R[i][j] = head_from_cam[i][j];
            cam.origin[i] = head_from_cam[i][3];
        }
        out[cam.name] = cam;
    }
    if (out.empty()) err = "no cameras in /persist/xrservice.json";
    return !out.empty();
}

bool load_color_calibration(std::map<std::string, Camera> &out, const std::string &left_node,
                            const std::string &right_node, bool crop_subtract, int scale, std::string &err) {
    // The module's EEPROM: some binary, then the calibration as JSON (world-readable)
    const std::string path = device_path("/sys/devices/platform/soc@0/ac15000.cci/i2c-0/0-0050/eeprom");
    std::ifstream f(path, std::ios::binary);
    const std::string raw((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    const size_t key = raw.find("\"alignment_method\"");
    const size_t start = key == std::string::npos ? key : raw.rfind('{', key);
    Json::Value rig, dev;
    std::string e;
    std::unique_ptr<Json::CharReader> reader(Json::CharReaderBuilder().newCharReader());
    if (start == std::string::npos || !reader->parse(raw.data() + start, raw.data() + raw.size(), &rig, &e))
        return err = path + ": no calibration JSON " + e, false;
    if (!read_json(device_path("/persist/device_config.json").c_str(), dev, err)) return false;
    double cad_from_head[4][4], head_from_cad[4][4];
    pose(dev["head"], 1.0, cad_from_head);
    invert_rigid(cad_from_head, head_from_cad);
    constexpr int kValidWidth = 1972;   // pixels per row XRService's buffers deliver (of 2464)
    int n = 0;
    for (const Json::Value &c : rig["cameras"]) {
        const std::string source = c["sourceCamera"].asString();
        const std::string name = source == "passthrough_left" ? left_node : source == "passthrough_right" ? right_node : "";
        if (name.empty()) continue;
        Camera cam;
        cam.name = name;
        cam.width = kValidWidth / scale, cam.height = c["height"].asInt() / scale;
        const double dx = crop_subtract ? c["cropRegion"]["x"].asDouble() : 0, dy = crop_subtract ? c["cropRegion"]["y"].asDouble() : 0;
        for (const Json::Value &in : c["intrinsics"]) {
            if (in["cameraModel"].asString() != "kb") continue;
            // integer pixel centres: sensor u -> image (u - crop + 0.5) / scale - 0.5
            cam.fx = in["fx"].asDouble() / scale, cam.fy = in["fy"].asDouble() / scale;
            cam.cx = (in["cx"].asDouble() - dx + 0.5) / scale - 0.5, cam.cy = (in["cy"].asDouble() - dy + 0.5) / scale - 0.5;
            cam.k[0] = in["k1"].asDouble(), cam.k[1] = in["k2"].asDouble();
            cam.k[2] = in["k3"].asDouble(), cam.k[3] = in["k4"].asDouble();
        }
        double cad_from_cam[4][4], head_from_cam[4][4];
        pose(c["extrinsics"], 1e-3, cad_from_cam);
        mul(head_from_cad, cad_from_cam, head_from_cam);
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) cam.R[i][j] = head_from_cam[i][j];
            cam.origin[i] = head_from_cam[i][3];
        }
        out[name] = cam;
        ++n;
    }
    if (n != 2) err = path + ": expected passthrough_left and passthrough_right";
    return n == 2;
}

V3 triangulate(const V3 *origins, const V3 *dirs, const double *weights, int n, double *rms) {
    double A[3][3] = {}, b[3] = {};
    for (int v = 0; v < n; ++v) {
        const V3 &d = dirs[v], &o = origins[v];
        for (int i = 0; i < 3; ++i)
            for (int j = 0; j < 3; ++j) {
                const double P = (i == j ? 1.0 : 0.0) - d[i] * d[j];
                A[i][j] += weights[v] * P;
                b[i] += weights[v] * P * o[j];
            }
    }
    // Cramer's rule for the 3x3 system
    auto det3 = [](const double m[3][3]) {
        return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
               m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    };
    const double D = det3(A);
    V3 p{};
    for (int c = 0; c < 3; ++c) {
        double M[3][3];
        for (int i = 0; i < 3; ++i)
            for (int j = 0; j < 3; ++j) M[i][j] = j == c ? b[i] : A[i][j];
        p[c] = std::fabs(D) > 1e-18 ? det3(M) / D : 0;
    }
    if (rms) {
        double s = 0;
        for (int v = 0; v < n; ++v) {
            const V3 off = p - origins[v];
            const V3 perp = off - dirs[v] * dot(off, dirs[v]);
            s += dot(perp, perp);
        }
        *rms = std::sqrt(s / n);
    }
    return p;
}
