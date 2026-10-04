// Unit tests for the side cameras' naming check (track/sides.h), on made-up cameras and a
// made-up hand: no models, no recordings. make check (in the dev container) builds and runs it.
#include "../track/sides.h"

#include <cstdio>
#include <cstdlib>

namespace {

int failures = 0;
#define CHECK(c)                                                         \
    do {                                                                 \
        if (!(c)) std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c), ++failures; \
    } while (0)

V3 cross(V3 a, V3 b) { return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]}; }

// An equidistant fisheye (k = 0) at origin, looking along look, image "down" toward head -y.
Camera camera(const char *name, V3 origin, V3 look, int w, int h, double f) {
    Camera c;
    c.name = name, c.width = w, c.height = h, c.fx = c.fy = f, c.cx = w / 2.0, c.cy = h / 2.0;
    const V3 z = unit(look), down{0, -1, 0};
    const V3 y = unit(down - z * dot(down, z)), x = cross(y, z);
    for (int i = 0; i < 3; ++i) c.R[i][0] = x[i], c.R[i][1] = y[i], c.R[i][2] = z[i];
    c.origin = origin;
    return c;
}

// MediaPipe-like world landmarks (m, hand-centred, flat: z = 0): x across the palm, y along the fingers
const double kHand[21][2] = {{0, 0},           {0.03, 0.03},     {0.05, 0.05},     {0.065, 0.07},   {0.075, 0.09},
                             {0.025, 0.09},    {0.025, 0.13},    {0.025, 0.155},   {0.025, 0.175},  {0.005, 0.095},
                             {0.005, 0.14},    {0.005, 0.165},   {0.005, 0.185},   {-0.015, 0.09},  {-0.015, 0.13},
                             {-0.015, 0.155},  {-0.015, 0.17},   {-0.033, 0.08},   {-0.033, 0.11},  {-0.033, 0.13},
                             {-0.033, 0.145}};

// The hand with its wrist at p, the palm facing toward `facing`, as cam sees it.
Landmarks view(const Camera &cam, V3 p, V3 facing) {
    const V3 n = unit(facing - p), up0{0, 1, 0};
    const V3 along = unit(up0 - n * dot(up0, n)), across = cross(along, n);
    Landmarks lm;
    for (int k = 0; k < 21; ++k) {
        const V3 q = p + across * kHand[k][0] + along * kHand[k][1];
        lm.pts[k] = cam.project(q, nullptr);
        lm.world[k][0] = kHand[k][0], lm.world[k][1] = kHand[k][1], lm.world[k][2] = 0;
    }
    lm.presence = 0.9;
    return lm;
}

Seen seen(const std::string &cam, const Landmarks &lm) { return Seen{cam, 0, Roi{}, lm, {}}; }

}  // namespace

int main() {
    std::map<std::string, Camera> cams;
    cams["slam_left"] = camera("slam_left", {-0.045, 0, -0.03}, {-0.5, -0.6, -0.6}, 1056, 1024, 300);
    cams["slam_right"] = camera("slam_right", {0.045, 0, -0.03}, {0.5, -0.6, -0.6}, 1056, 1024, 300);
    cams["upper_left"] = camera("upper_left", {-0.03, 0.02, -0.04}, {-0.2, 0.1, -1}, 640, 480, 180);
    cams["upper_right"] = camera("upper_right", {0.03, 0.02, -0.04}, {0.2, 0.1, -1}, 640, 480, 180);
    const Camera &L = cams["slam_left"], &R = cams["slam_right"], &UL = cams["upper_left"], &UR = cams["upper_right"];
    const V3 hand{0.0, -0.22, -0.35}, eyes{0, 0, -0.03};
    const Landmarks inL = view(L, hand, eyes), inR = view(R, hand, eyes), inUL = view(UL, hand, eyes),
                    inUR = view(UR, hand, eyes);
    SideCheck sc(cams);
    CHECK(sc.usable());

    // 1. the side pair, named right: meets as named, not swapped
    SideCheck::Miss m = sc.test("slam_left", inL, "slam_right", inR);
    std::printf("side pair as named: miss %.4f m as named, %.4f swapped\n", m.m[0], m.m[1]);
    CHECK(m.m[0] >= 0 && m.m[0] < 0.001);
    CHECK(SideCheck::vote(m) == 0);
    // 2. the same images under each other's names (what a backwards ft-camd gives)
    m = sc.test("slam_left", inR, "slam_right", inL);
    std::printf("side pair swapped: miss %.4f m as named, %.4f swapped\n", m.m[0], m.m[1]);
    CHECK(m.m[1] >= 0 && m.m[1] < 0.001);
    CHECK(SideCheck::vote(m) == 1);
    // 3. a side camera with an upper one
    m = sc.test("slam_left", inL, "upper_left", inUL);
    CHECK(SideCheck::vote(m) == 0);
    m = sc.test("slam_right", inL, "upper_left", inUL);   // slam_left's image under slam_right's name
    CHECK(SideCheck::vote(m) == 1);
    m = sc.test("upper_right", inUR, "slam_left", inR);   // ... and the other way round, other order
    CHECK(SideCheck::vote(m) == 1);
    // 4. the upper pair alone says nothing
    CHECK(sc.add({seen("upper_left", inUL), seen("upper_right", inUR)}, 1) == 0);
    // 5. two different hands, one in each side camera, don't vote
    const V3 other{0.25, -0.3, -0.3};
    m = sc.test("slam_left", inL, "slam_right", view(R, other, eyes));
    std::printf("two hands: miss %.4f m as named, %.4f swapped\n", m.m[0], m.m[1]);
    CHECK(SideCheck::vote(m) == -1);
    // 6. a vote needs one way clearly better: both meeting about as well is no vote
    CHECK(SideCheck::vote({{0.004, 0.006}}) == -1);
    CHECK(SideCheck::vote({{0.004, 0.02}}) == 0);
    CHECK(SideCheck::vote({{-1, 0.003}}) == 1);
    CHECK(SideCheck::vote({{0.02, -1}}) == -1);   // too far apart to be one hand
    CHECK(SideCheck::vote({{-1, -1}}) == -1);

    // 7. the decision: min_clean votes and none against, or min_votes and at most max_other
    // against, over min_span_s
    const std::vector<Seen> named = {seen("slam_left", inL), seen("slam_right", inR)};
    const std::vector<Seen> swapped = {seen("slam_left", inR), seen("slam_right", inL)};
    const int64_t s = 1'000'000'000;
    sc.reset();
    for (int i = 0; i < 9; ++i) CHECK(sc.add(named, i * s / 5) == 1);
    CHECK(sc.verdict() == SideCheck::Undecided);   // 9 votes
    sc.add(named, 9 * s / 5);
    CHECK(sc.verdict() == SideCheck::AsNamed);     // 10 clean votes over 1.8 s
    std::printf("%s\n", sc.summary().c_str());
    // fast: 30 clean votes in 0.97 s aren't enough; the vote at 1.0 s is
    sc.reset();
    for (int i = 0; i < 30; ++i) sc.add(swapped, i * s / 30);
    CHECK(sc.votes[1] == 30 && sc.verdict() == SideCheck::Undecided);
    sc.add(swapped, s);
    CHECK(sc.verdict() == SideCheck::Swapped);
    CHECK(sc.median_miss_mm(1) >= 0 && sc.median_miss_mm(1) < 1);
    // one vote against: it waits for min_votes
    sc.reset();
    sc.add(swapped, 0);
    for (int i = 1; i < 20; ++i) sc.add(named, i * s / 10);
    CHECK(sc.verdict() == SideCheck::Undecided);   // 19 to 1
    sc.add(named, 2 * s);
    CHECK(sc.verdict() == SideCheck::AsNamed);     // 20 to 1
    // mixed evidence waits until the other way is at most a fifth of the votes
    sc.reset();
    for (int i = 0; i < 20; ++i) sc.add(named, i * s / 10);
    for (int i = 0; i < 6; ++i) sc.add(swapped, (20 + i) * s / 10);
    CHECK(sc.verdict() == SideCheck::Undecided);   // 6 of 26
    for (int i = 0; i < 4; ++i) sc.add(named, (26 + i) * s / 10);
    CHECK(sc.verdict() == SideCheck::AsNamed);     // 6 of 30
    // the stricter check after a decision
    sc.reset();
    sc.min_clean = 20, sc.min_votes = 40;
    for (int i = 0; i < 19; ++i) sc.add(named, i * s / 10);
    CHECK(sc.verdict() == SideCheck::Undecided);
    sc.add(named, 2 * s);
    CHECK(sc.verdict() == SideCheck::AsNamed);
    CHECK(sc.json().find("\"as_named\": 20") != std::string::npos);
    // without both side cameras it can't check
    std::map<std::string, Camera> one{{"slam_left", L}, {"upper_left", UL}};
    SideCheck none(one);
    CHECK(!none.usable());
    CHECK(none.add(named, 0) == 0);

    std::printf(failures ? "%d FAILED\n" : "sides_test: all passed\n", failures);
    return failures ? 1 : 0;
}
