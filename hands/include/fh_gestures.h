/*
 * fh_gestures - pinch state ft-hands publishes for input: look at something and pinch to
 * click it, pinch and move to drag (the Vision Pro model, with the eye tracker doing the
 * looking). /run/user/UID/frametop-hands/gestures, next to the hands
 * file, with the same sequence lock (read seq, copy, read seq again; use the copy only if
 * both reads are the same even number) and the same frame: metres in the head frame at
 * capture time, OpenVR's HMD frame (+x right, +y up, -z forward).
 *
 * One slot per side: pinch[0] is the left hand, pinch[1] the right. A pinch follows the
 * hand it began on until it ends. It begins when the thumb and index tips close within
 * begin_m and ends when they open past end_m (the gap between keeps it from flickering),
 * or when the hand stays lost too long (FH_PINCH_LOST).
 *
 * Don't miss short pinches: a reader that polls slower than a quick tap still sees it,
 * because begins and ends count every pinch. When begins changed, a pinch began at
 * begin_ns; when ends changed, one ended at end_ns. begins - ends is 1 while pinching.
 *
 * Drags: point is where the pinch is now, begin_point where it began. Turn each into the
 * room with the HMD pose at its capture time (capture_ns, begin_ns) before subtracting,
 * so turning your head doesn't drag.
 */

#pragma once

#include <assert.h>
#include <stdint.h>

#define FH_GESTURES_MAGIC   "FHGEST01"
#define FH_GESTURES_VERSION 1

enum {
    FH_PINCH_TRACKED = 1u << 0, /* the hand was tracked in this frame              */
    FH_PINCH_DOWN    = 1u << 1, /* pinching now                                     */
    FH_PINCH_LOST    = 1u << 2, /* the last pinch ended because the hand was lost   */
};

typedef struct {
    uint32_t flags;             /* FH_PINCH_*                                       */
    uint32_t hand_id;           /* fh_hand_t.id of the hand, 0 if none              */
    uint32_t begins;            /* pinches begun so far                             */
    uint32_t ends;              /* pinches ended so far                             */
    uint64_t begin_ns;          /* capture time (CLOCK_MONOTONIC) the current or    */
                                /* last pinch began                                 */
    uint64_t end_ns;            /* ... the last pinch ended                         */
    float    distance;          /* thumb tip to index tip, m, at this user's hand   */
                                /* size                                             */
    float    strength;          /* 0 open (end_m or more) .. 1 closed (begin_m)     */
    float    point[3];          /* between the thumb and index tips                 */
    float    begin_point[3];    /* point when the current or last pinch began       */
} fh_pinch_t;                   /* 64 bytes */

typedef struct {
    char              magic[8];
    uint32_t          version;
    uint32_t          size;
    volatile uint64_t seq;
    uint64_t          capture_ns;   /* CLOCK_MONOTONIC when the cameras took the frames */
    uint64_t          publish_ns;   /* CLOCK_MONOTONIC when this was written            */
    float             begin_m;      /* the thresholds in use                            */
    float             end_m;
    uint8_t           reserved[16];
    fh_pinch_t        pinch[2];     /* [0] left hand, [1] right hand                    */
} fh_gestures_t;

static_assert(sizeof(fh_pinch_t) == 64, "fh_pinch_t layout");
static_assert(sizeof(fh_gestures_t) == 64 + 2 * 64, "fh_gestures_t layout");
