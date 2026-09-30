// Check the C++ model code against tracker/models.py on a recorded frame:
//   nettest MODELS_DIR FRAME.pgm cx cy size rotation [--int8]
// Runs the palm detector on that crop, then the landmark model on each palm's ROI, and
// prints what they found; tools/nettest_compare.py runs the Python side on the same input.
#include "nets.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

static bool read_pgm(const char *path, std::vector<uint8_t> &px, int &w, int &h) {
    std::ifstream f(path, std::ios::binary);
    std::string magic;
    int maxv;
    if (!(f >> magic >> w >> h >> maxv) || magic != "P5") return false;
    f.get();
    px.resize(size_t(w) * h);
    return bool(f.read(reinterpret_cast<char *>(px.data()), px.size()));
}

int main(int argc, char **argv) {
    if (argc < 7) return std::fprintf(stderr, "usage: nettest MODELS FRAME.pgm cx cy size rotation [--int8] [--bench N]\n"), 1;
    bool int8 = false;
    int bench = 0;
    for (int i = 7; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--int8")) int8 = true;
        else if (!std::strcmp(argv[i], "--bench") && i + 1 < argc) bench = std::atoi(argv[++i]);
    }
    Nets nets;
    std::string err;
    if (!nets.load(argv[1], int8, err)) return std::fprintf(stderr, "%s\n", err.c_str()), 1;
    std::vector<uint8_t> px;
    int w, h;
    if (!read_pgm(argv[2], px, w, h)) return std::fprintf(stderr, "can't read %s\n", argv[2]), 1;
    const Image img{px.data(), w, h, w};
    const V2 c{std::atof(argv[3]), std::atof(argv[4])};
    if (bench > 0) {   // steady-state timing: warm up, then the median of N calls each
        const Roi roi{c, std::atof(argv[5]), std::atof(argv[6])};
        auto time = [&](auto fn) {
            std::vector<double> t;
            for (int i = 0; i < bench + 5; ++i) {
                const auto t0 = std::chrono::steady_clock::now();
                fn();
                if (i >= 5) t.push_back(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count());
            }
            std::sort(t.begin(), t.end());
            return t[t.size() / 2];
        };
        const double pm = time([&] { nets.palms(img, c, roi.size, roi.rotation); });
        const double hm = time([&] { nets.landmarks(img, roi); });
        std::printf("bench%s: palm %.2f ms, hand %.2f ms (median of %d, one thread)\n", int8 ? " int8" : "", pm, hm, bench);
        return 0;
    }
    auto t0 = std::chrono::steady_clock::now();
    const auto palms = nets.palms(img, c, std::atof(argv[5]), std::atof(argv[6]));
    const double palm_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    std::printf("palm_ms %.2f\n", palm_ms);
    for (const Palm &p : palms) {
        const Roi r = p.roi();
        std::printf("palm %.3f  center %.1f %.1f  roi %.1f %.1f %.1f %.4f\n", p.score, p.center[0], p.center[1],
                    r.center[0], r.center[1], r.size, r.rotation);
        t0 = std::chrono::steady_clock::now();
        const Landmarks lm = nets.landmarks(img, r);
        const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        std::printf("hand_ms %.2f presence %.3f right %.3f\n", ms, lm.presence, lm.right);
        std::printf("pts");
        for (const V2 &q : lm.pts) std::printf(" %.1f %.1f", q[0], q[1]);
        std::printf("\n");
    }
    return 0;
}
