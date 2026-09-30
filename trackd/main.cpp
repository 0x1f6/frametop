// fh-tracker: hands in 3D from fh-camd's ring, published for Frametop's ft-screens.
// The C++ version of tracker/live.py: the same scheduling, with the models on a few
// threads and no Python in the loop.
//
//   fh-tracker [--seconds N] [--threads N] [--int8] [--status S] [--models DIR] [--nice N]
//              [--no-publish] [--record DIR] [--swap-sides] ... (--help lists them all)
#include "io.h"
#include "record.h"

#include <sched.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>

#include <ctime>
#include <memory>

#include <csignal>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <thread>
#include <utility>

namespace {

volatile std::sig_atomic_t g_stop = 0, g_record = 0;

// which calibrated camera each capture pipe carries (XRService's fixed routing)
const char *camera_for_pipe(int node) {
    char path[64], name[64] = "";
    std::snprintf(path, sizeof path, "/sys/class/video4linux/video%d/name", node);
    std::ifstream f(path);
    f.getline(name, sizeof name);
    if (!std::strcmp(name, "msm_vfe3_video0")) return "slam_left";
    if (!std::strcmp(name, "msm_vfe4_video0")) return "slam_right";
    if (!std::strcmp(name, "msm_vfe2_video0")) return "upper_left";
    if (!std::strcmp(name, "msm_vfe2_video1")) return "upper_right";
    return nullptr;
}

double cpu_seconds() {
    rusage r;
    getrusage(RUSAGE_SELF, &r);
    return r.ru_utime.tv_sec + r.ru_stime.tv_sec + (r.ru_utime.tv_usec + r.ru_stime.tv_usec) / 1e6;
}

}  // namespace

int main(int argc, char **argv) {
    double seconds = 0, status = 5;
    int threads = 3, niceness = 5;
    bool int8 = false, publish = true, track = true, swap_sides = false;
    std::string models = std::string(argv[0]).substr(0, std::string(argv[0]).rfind('/') + 1) + "../models/ncnn";
    std::string record, ring_path = FH_RING_PATH;
    // SteamOS starts user processes on CPUs 0-4 and keeps 5-7 (two A720s and the X4) for
    // SteamVR's compositor, whose threads there run at real-time priority, so they always
    // win. XRService pins its head tracking to 2-3. probes/core_ab.py (2026-09-29, headset
    // on, 3 rounds): on 5-7 a step took 8.4 ms against 13.2 on 2-4, latency 9.6 against
    // 14.1 ms, and the compositor's late frames and CPU/GPU time didn't change.
    std::vector<int> cpus = {5, 6, 7};
    // How crops are equalized. CLAHE helps the palm search find hands (about 10% more in the
    // dim recording), but makes the landmarks jitter, so they get plain crops.
    Contrast palm_contrast, hand_contrast{Contrast::None};
    double keep_presence = 0.5;   // landmark presence a tracked view needs to stay
    double record_for = 120;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        const bool more = i + 1 < argc;
        if (a == "--seconds" && more) seconds = std::atof(argv[++i]);
        else if (a == "--threads" && more) threads = std::max(1, std::atoi(argv[++i]));
        else if (a == "--status" && more) status = std::atof(argv[++i]);
        else if (a == "--models" && more) models = argv[++i];
        else if (a == "--nice" && more) niceness = std::atoi(argv[++i]);
        else if (a == "--int8") int8 = true;
        else if (a == "--no-publish") publish = false;
        else if (a == "--swap-sides") swap_sides = true;
        else if (a == "--record-only") track = publish = false;
        else if (a == "--ring" && more) ring_path = argv[++i];
        else if (a == "--record" && more) record = argv[++i];
        else if (a == "--record-for" && more) record_for = std::atof(argv[++i]);
        else if (a == "--keep-presence" && more) keep_presence = std::atof(argv[++i]);
        else if (a == "--contrast" && more) {
            if (!Contrast::parse_pair(argv[++i], palm_contrast, hand_contrast))
                return std::fprintf(stderr, "--contrast MODE or PALM/HAND, each clahe[:CLIP]|none|stretch\n"), 1;
        } else if (a == "--cpus" && more) {
            cpus.clear();
            for (char *p = argv[++i]; *p;) {
                cpus.push_back(int(std::strtol(p, &p, 10)));
                if (*p == ',') ++p;
                else if (*p) break;
            }
            if (cpus.empty()) cpus = {5, 6, 7};
        }
        else {
            std::printf("usage: %s [--seconds N] [--threads N] [--int8] [--status S] [--models DIR] [--nice N] [--no-publish]\n"
                        "          [--record DIR] [--record-for S] [--record-only] [--cpus 5,6,7] [--swap-sides]\n"
                        "          [--keep-presence P] (0.5) [--ring PATH] (fh-camd's, or fh-ringplay's)\n"
                        "          [--contrast MODE|PALM/HAND] (clahe[:CLIP], none, stretch; default clahe:2/none)\n"
                        "Recording saves every frame set for S seconds (120) to DIR/sets.bin, for fh-replay; SIGUSR1\n"
                        "starts one in captures/rec-<time> next to trackd. --record-only records without tracking, so it\n"
                        "can run beside a tracking fh-tracker. With fh-camd --with-dark, recordings also get each\n"
                        "camera's newest dark frame, as <name>_dk.\n",
                        argv[0]);
            return a == "--help" ? 0 : 1;
        }
    }
    if (nice(niceness) < 0) std::perror("nice");   // the VR stack wins contested CPUs
    std::signal(SIGINT, [](int) { g_stop = 1; });
    std::signal(SIGTERM, [](int) { g_stop = 1; });
    std::signal(SIGUSR1, [](int) { g_record = 1; });

    std::string err;
    std::map<std::string, Camera> calib;
    Ring ring;
    Nets nets;
    Publisher pub;
    std::unique_ptr<Recorder> rec;
    uint64_t rec_start = 0;
    auto start_recording = [&](const std::string &dir, std::string &e) {
        rec = std::make_unique<Recorder>();
        if (!rec->open(dir, e)) return rec.reset(), false;
        rec_start = mono_ns();
        std::printf("recording to %s for %.0f s\n", dir.c_str(), record_for);
        std::fflush(stdout);
        return true;
    };
    if (!load_calibration(calib, err) || !ring.open(ring_path.c_str(), err) || !nets.load(models, int8, err) ||
        (publish && !pub.open(err)) || (!record.empty() && !start_recording(record, err))) {
        std::fprintf(stderr, "%s\n", err.c_str());
        return 1;
    }
    if (!ring.alive()) return std::fprintf(stderr, "fh-camd isn't running (no heartbeat)\n"), 1;

    std::map<std::string, int> index;   // calibration name -> ring camera
    // "<name>_dk" -> ring camera (fh-camd --with-dark): recorded only. Recorded names hold 15
    // characters, so "upper_right_dark" wouldn't fit.
    std::map<std::string, int> dark;
    std::map<std::string, Camera> used;
    for (int i = 0; i < ring.cameras(); ++i) {
        // fh-camd's cameras by capture pipe; fh-ringplay's (no device) by the name it gives
        const char *name = camera_for_pipe(ring.camera(i).node);
        if (!name && ring.camera(i).node < 0) name = ring.camera(i).name;
        if (!name || !calib.count(name)) continue;
        if (ring.camera(i).flags & FH_CAM_DARK) dark[std::string(name) + "_dk"] = i;
        else index[name] = i, used[name] = calib[name];
    }
    // fh-camd tells the side cameras' buffers apart by XRService's allocation order, which
    // some XRService restarts reverse; tools/check_sides.py --ring tells when.
    if (swap_sides && index.count("slam_left") && index.count("slam_right")) {
        std::swap(index["slam_left"], index["slam_right"]);
        if (dark.count("slam_left_dk") && dark.count("slam_right_dk")) std::swap(dark["slam_left_dk"], dark["slam_right_dk"]);
        std::printf("side cameras swapped (--swap-sides)\n");
    }
    std::printf("cameras:");
    for (auto &[name, i] : index) std::printf(" %s=video%d", name.c_str(), ring.camera(i).node);
    std::printf("  models: %s%s, %d threads on CPUs", models.c_str(), int8 ? " (int8)" : "", threads);
    for (int c : cpus) std::printf(" %d", c);
    std::printf("\n");

    cpu_set_t set;   // the main loop too
    CPU_ZERO(&set);
    for (int c : cpus) CPU_SET(c, &set);
    if (sched_setaffinity(0, sizeof set, &set) < 0) std::perror("sched_setaffinity");
    nets.set_contrast(palm_contrast, hand_contrast);
    Pool pool(threads, cpus);
    Tracker tracker(used, nets, pool);
    tracker.set_keep_presence(keep_presence);
    std::map<std::string, std::vector<uint8_t>> pixels;
    std::map<std::string, uint64_t> last;
    const uint64_t start = mono_ns();
    uint64_t t_status = start, next_ns = 0;
    double cpu0 = cpu_seconds();
    std::vector<double> lat;
    double hands_sum = 0, resid_sum = 0;
    int resid_n = 0, left_sets = 0, right_sets = 0, both_sets = 0;

    while (!g_stop && (seconds <= 0 || (mono_ns() - start) / 1e9 < seconds)) {
        if (!ring.alive()) return std::fprintf(stderr, "fh-camd stopped\n"), 2;
        // a new frame set: every camera has a newer frame, taken at the same moment
        std::map<std::string, uint64_t> latest;
        bool ready = true;
        for (auto &[name, i] : index) {
            latest[name] = ring.latest(i);
            ready = ready && latest[name] > last[name];
        }
        if (!ready) {
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            continue;
        }
        // not needed at the current rate, and not recorded: skip it without copying images
        if (track && !rec && !g_record) {
            uint64_t t0 = UINT64_MAX, t1 = 0;
            bool ok = true;
            for (auto &[name, i] : index) {
                fh_ring_slot_t meta;
                ok = ok && ring.meta(i, latest[name], &meta);
                if (ok) t0 = std::min(t0, meta.capture_ns), t1 = std::max(t1, meta.capture_ns);
            }
            if (ok && t1 - t0 <= 3'000'000 && t0 < next_ns) {
                last = latest;
                continue;
            }
        }
        std::map<std::string, Image> images;
        std::vector<SetFrame> frames;
        uint64_t tmin = UINT64_MAX, tmax = 0, dq = 0;
        bool ok = true;
        for (auto &[name, i] : index) {
            fh_ring_slot_t meta;
            ok = ok && ring.read(i, latest[name], pixels[name], &meta);
            if (!ok) break;
            const auto &c = ring.camera(i);
            images[name] = {pixels[name].data(), int(c.width), int(c.height), int(c.width)};
            frames.push_back({name, pixels[name].data(), c.width, c.height, meta.capture_ns, meta.dqbuf_ns});
            tmin = std::min(tmin, meta.capture_ns), tmax = std::max(tmax, meta.capture_ns), dq = std::max(dq, meta.dqbuf_ns);
        }
        if (!ok || tmax - tmin > 3'000'000) {   // torn, or a camera is a frame behind
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        last = latest;
        if (g_record && !rec) {
            g_record = 0;
            char name[64];
            const std::time_t now = std::time(nullptr);
            std::strftime(name, sizeof name, "rec-%Y%m%d-%H%M%S", std::localtime(&now));
            const std::string here = std::string(argv[0]).substr(0, std::string(argv[0]).rfind('/') + 1);
            const std::string dir = here + "../captures";
            mkdir(dir.c_str(), 0755);
            std::string e;
            if (!start_recording(dir + "/" + name, e)) std::fprintf(stderr, "%s\n", e.c_str());
        }
        if (rec) {   // about 80 MB/s, twice that with dark frames
            if ((mono_ns() - rec_start) / 1e9 < record_for) {
                for (auto &[name, i] : dark) {   // the newest dark frame of each camera, as it is
                    fh_ring_slot_t meta;
                    const uint64_t n = ring.latest(i);
                    const auto &c = ring.camera(i);
                    if (n && ring.read(i, n, pixels[name], &meta))
                        frames.push_back({name, pixels[name].data(), c.width, c.height, meta.capture_ns, meta.dqbuf_ns});
                }
                rec->add(frames);
            } else {
                const size_t n = rec->written(), d = rec->dropped();
                rec.reset();   // writes out what's queued
                std::printf("recording done: %zu sets, %zu dropped\n", n, d);
                std::fflush(stdout);
                if (!track) break;
            }
        }
        if (!track && rec && status > 0 && (mono_ns() - t_status) / 1e9 >= status) {
            std::printf("%5.1fs  recorded %zu sets, dropped %zu\n", (mono_ns() - start) / 1e9, rec->written(), rec->dropped());
            std::fflush(stdout);
            t_status = mono_ns();
        }
        if (!track || tmin < next_ns) continue;   // not needed yet at the current rate
        const auto hands = tracker.step(images, int64_t(tmin));
        next_ns = tmin + uint64_t((tracker.interval() - 0.005) * 1e9);
        if (publish) pub.write(hands, uint64_t(int64_t(tmin) - raw_minus_mono_ns()));
        lat.push_back((mono_ns() - dq) / 1e6);
        hands_sum += double(hands.size());
        bool on_left = false, on_right = false;   // by where the wrist is, not the model's label
        for (const Hand *h : hands) {
            if (h->residual >= 0) resid_sum += h->residual * 1000, ++resid_n;
            (h->pts[0][0] < 0 ? on_left : on_right) = true;
        }
        left_sets += on_left, right_sets += on_right, both_sets += on_left && on_right;

        const uint64_t now = mono_ns();
        if (status > 0 && (now - t_status) / 1e9 >= status) {
            const double dt = (now - t_status) / 1e9, cpu1 = cpu_seconds();
            const Stats &s = tracker.stats;
            std::sort(lat.begin(), lat.end());
            std::printf("%5.1fs %4.1f sets/s  hands %.2f views %zu  palm %3d calls %4.1f ms/batch  hand %3d calls %4.1f ms/batch  "
                        "step %4.1f ms  latency %4.1f ms  resid %.1f mm  CPU %3.0f%%\n",
                        (now - start) / 1e9, s.sets / dt, s.sets ? hands_sum / s.sets : 0, tracker.views(), s.palm_calls,
                        s.palm_batches ? s.palm_ms / s.palm_batches : 0, s.hand_calls,
                        s.hand_batches ? s.hand_ms / s.hand_batches : 0, s.sets ? s.step_ms / s.sets : 0,
                        lat.empty() ? 0 : lat[lat.size() / 2], resid_n ? resid_sum / resid_n : 0, 100 * (cpu1 - cpu0) / dt);
            if (s.sets)
                std::printf("        sets with a hand: left %2.0f%% right %2.0f%% both %2.0f%%  views lost %d, handoff misses %d, "
                            "dups %d, splits %d  hands new %d merged %d forgotten %d%s\n",
                            100.0 * left_sets / s.sets, 100.0 * right_sets / s.sets, 100.0 * both_sets / s.sets, s.lost,
                            s.handoff_miss, s.dups, s.splits, s.created, s.merged, s.forgotten,
                            !rec ? "" : ("  recorded " + std::to_string(rec->written()) + " dropped " +
                                         std::to_string(rec->dropped())).c_str());
            for (const Hand *h : hands)
                std::printf("        hand %d %-5s views %d wrist %+.3f %+.3f %+.3f m  scale %.2f  speed %.2f m/s\n", h->id,
                            h->right() ? "right" : "left", h->nviews, h->pts[0][0], h->pts[0][1], h->pts[0][2], h->scale,
                            h->speed);
            std::fflush(stdout);
            tracker.stats = Stats{};
            t_status = now, cpu0 = cpu1;
            lat.clear(), hands_sum = 0, resid_sum = 0, resid_n = 0, left_sets = right_sets = both_sets = 0;
        }
    }
    if (publish) pub.write({}, mono_ns());
    return 0;
}
