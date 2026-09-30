#include "pinch.h"

#include "io.h"

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstdlib>
#include <cstring>

namespace {

constexpr int kThumbTip = 4, kIndexTip = 8;

void put3(float out[3], V3 v) {
    for (int k = 0; k < 3; ++k) out[k] = float(v[k]);
}

}  // namespace

void Pinch::update(const std::vector<const Hand *> &hands, const std::vector<Seen> &views, int64_t t_ns) {
    events.clear();
    for (int s = 0; s < 2; ++s) {
        fh_pinch_t &o = side_[s];
        const bool down = o.flags & FH_PINCH_DOWN;
        // the hand: while down, the one the pinch began on; else the best tracked hand of this side
        const Hand *h = nullptr;
        for (const Hand *c : hands) {
            if (down ? c->id != follow_[s] : c->right() != (s == 1)) continue;
            if (!h || c->frames > h->frames) h = c;
        }
        world_d[s] = tri_d[s] = -1;
        if (!h) {
            o.flags &= ~FH_PINCH_TRACKED;
            if (down && (t_ns - seen_ns_[s]) / 1e9 > p_.grace_s) end(s, t_ns, true);
            continue;
        }
        seen_ns_[s] = t_ns;
        tri_d[s] = norm(h->pts[kThumbTip] - h->pts[kIndexTip]);
        double sum = 0;
        int n = 0;
        for (const Seen &v : views) {
            if (v.hand != h->id) continue;
            const V3 a{v.lm.world[kThumbTip][0], v.lm.world[kThumbTip][1], v.lm.world[kThumbTip][2]};
            const V3 b{v.lm.world[kIndexTip][0], v.lm.world[kIndexTip][1], v.lm.world[kIndexTip][2]};
            sum += norm(a - b), ++n;
        }
        if (n) world_d[s] = sum / n * h->scale;
        const double d = p_.triangulated || world_d[s] < 0 ? tri_d[s] : world_d[s];
        const V3 point = (h->smooth[kThumbTip] + h->smooth[kIndexTip]) * 0.5;
        o.flags |= FH_PINCH_TRACKED;
        o.hand_id = uint32_t(h->id);
        o.distance = float(d);
        o.strength = float(std::clamp((p_.end_m - d) / (p_.end_m - p_.begin_m), 0.0, 1.0));
        put3(o.point, point);
        if (!down) {
            if (d < p_.begin_m) {
                o.flags = (o.flags | FH_PINCH_DOWN) & ~FH_PINCH_LOST;
                ++o.begins;
                o.begin_ns = uint64_t(t_ns);
                put3(o.begin_point, point);
                follow_[s] = h->id;
                open_frames_[s] = 0;
                events.push_back({s, "begin", t_ns, d, point});
            }
        } else if (d > p_.end_m) {
            if (++open_frames_[s] >= p_.end_frames) end(s, t_ns, false);
        } else {
            open_frames_[s] = 0;
        }
    }
}

void Pinch::end(int s, int64_t t_ns, bool lost) {
    fh_pinch_t &o = side_[s];
    o.flags = (o.flags & ~FH_PINCH_DOWN) | (lost ? FH_PINCH_LOST : 0);
    ++o.ends;
    o.end_ns = uint64_t(t_ns);
    follow_[s] = 0;
    events.push_back({s, lost ? "lost" : "end", t_ns, o.distance, {o.point[0], o.point[1], o.point[2]}});
}

void Pinch::release(int64_t t_ns) {
    events.clear();
    for (int s = 0; s < 2; ++s) {
        side_[s].flags &= ~FH_PINCH_TRACKED;
        if (side_[s].flags & FH_PINCH_DOWN) end(s, t_ns, true);
    }
}

bool Pinch::engaged() const {
    for (const fh_pinch_t &o : side_)
        if ((o.flags & FH_PINCH_DOWN) || ((o.flags & FH_PINCH_TRACKED) && o.strength > 0.3f)) return true;
    return false;
}

bool GesturePublisher::open(const Pinch &pinch, std::string &err) {
    const std::string path = run_dir() + "/gestures";
    const int fd = ::open(path.c_str(), O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0 || ftruncate(fd, sizeof(fh_gestures_t)) < 0) return err = path + ": " + std::strerror(errno), false;
    void *m = mmap(nullptr, sizeof(fh_gestures_t), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return err = path + ": can't map it", false;
    out_ = static_cast<fh_gestures_t *>(m);
    // keep the counters a previous tracker left, so a reader doesn't see them jump back
    const bool ours = !std::memcmp(out_->magic, FH_GESTURES_MAGIC, 8) && out_->version == FH_GESTURES_VERSION;
    if (!ours) {
        std::memset(out_, 0, sizeof *out_);
        std::memcpy(out_->magic, FH_GESTURES_MAGIC, 8);
        out_->version = FH_GESTURES_VERSION;
        out_->size = sizeof(fh_gestures_t);
    }
    seq_ = out_->seq / 2 + 1;
    // a pinch the last tracker left down (it crashed) is over: count its end, as lost
    __atomic_store_n(&out_->seq, 2 * ++seq_ - 1, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    for (fh_pinch_t &o : out_->pinch)
        if (o.begins != o.ends) {
            o.ends = o.begins;
            o.end_ns = mono_ns();
            o.flags = (o.flags & ~FH_PINCH_DOWN) | FH_PINCH_LOST;
        }
    __atomic_store_n(&out_->seq, 2 * seq_, __ATOMIC_RELEASE);
    out_->begin_m = float(pinch.params().begin_m);
    out_->end_m = float(pinch.params().end_m);
    return true;
}

void GesturePublisher::write(const Pinch &pinch, uint64_t capture_ns) {
    __atomic_store_n(&out_->seq, 2 * ++seq_ - 1, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    for (int s = 0; s < 2; ++s) {
        // counters carry on from what's in the file (a restarted tracker starts its own at 0)
        const fh_pinch_t &in = pinch.side(s);
        fh_pinch_t &o = out_->pinch[s];
        const uint32_t base_b = o.begins - last_begins_[s], base_e = o.ends - last_ends_[s];
        o = in;
        o.begins = base_b + in.begins;
        o.ends = base_e + in.ends;
        last_begins_[s] = in.begins, last_ends_[s] = in.ends;
    }
    out_->capture_ns = capture_ns;
    out_->publish_ns = mono_ns();
    __atomic_store_n(&out_->seq, 2 * seq_, __ATOMIC_RELEASE);
}
