// Pinch detection for input: look at something and pinch to click, pinch and move to drag.
// Per side, from the tracker's hands after each step; published as fh_gestures.h.
#pragma once

#include "tracker.h"

#include <string>
#include <vector>

extern "C" {
#include "../include/fh_gestures.h"
}

struct PinchParams {
    double begin_m = 0.020;   // thumb and index tips closer than this: the pinch begins
    double end_m = 0.035;     // further apart than this: it ends (the gap keeps it from flickering)
    int end_frames = 2;       // processed frames in a row past end_m before it ends, so one
                              // noisy frame doesn't drop a drag
    double grace_s = 0.25;    // a pinching hand lost this long ends its pinch (FH_PINCH_LOST)
    // Where the distance comes from: MediaPipe's world landmarks (the model's own 3D hand
    // pose, averaged over the hand's views, at the user's hand size), or the tracker's
    // triangulated tips. The model's pose should hold up better when the fingers hide each
    // other; tomorrow's recordings will tell.
    bool triangulated = false;
};

class Pinch {
public:
    explicit Pinch(const PinchParams &p = {}) : p_(p) {}
    const PinchParams &params() const { return p_; }
    // After each processed set: the hands out of Tracker::step, the tracker's views (for
    // the world landmarks) and the capture time.
    void update(const std::vector<const Hand *> &hands, const std::vector<Seen> &views, int64_t t_ns);
    // Ends any pinch that's down (as lost), e.g. when the tracker stops.
    void release(int64_t t_ns);
    const fh_pinch_t &side(int s) const { return side_[s]; }   // 0 left, 1 right
    // A pinch is down or closing: worth tracking at the full rate.
    bool engaged() const;

    // What changed in the last update, for logs.
    struct Event {
        int side;
        const char *what;   // "begin", "end", "lost"
        int64_t t_ns;
        double distance;
        V3 point;
    };
    std::vector<Event> events;
    // Both distance measures for the last update, per side (-1: no hand), for logs.
    double world_d[2] = {-1, -1}, tri_d[2] = {-1, -1};

private:
    void end(int s, int64_t t_ns, bool lost);
    PinchParams p_;
    fh_pinch_t side_[2]{};
    int follow_[2] = {0, 0};        // the hand id a pinch follows while down
    int open_frames_[2] = {0, 0};
    int64_t seen_ns_[2] = {0, 0};
};

// Writes /run/user/UID/frametop/gestures.
class GesturePublisher {
public:
    bool open(const Pinch &pinch, std::string &err);
    void write(const Pinch &pinch, uint64_t capture_ns);

private:
    fh_gestures_t *out_ = nullptr;
    uint64_t seq_ = 0;
    uint32_t last_begins_[2] = {0, 0}, last_ends_[2] = {0, 0};   // Pinch's counters last written
};
