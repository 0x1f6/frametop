/*
 * fh_hands - the tracked-hands file ft-hands publishes for ft-screens' hand cutouts
 * (/run/user/UID/frametop-hands/hands, directory mode 0700), rewritten in place
 * under a sequence lock: read seq, copy, read seq again; use the copy only if
 * both reads are the same even number.
 *
 * Positions are metres in the head frame at capture time, which is OpenVR's HMD
 * frame (+x right, +y up, -z forward). Turn them into the room with the HMD pose
 * at capture_ns (CLOCK_MONOTONIC). Writer: ft-hands (hands/track/io.cpp).
 */

#pragma once

#include <assert.h>
#include <stdint.h>

#define FH_HANDS_MAGIC        "FHHANDS1"
#define FH_HANDS_VERSION      1
#define FH_HANDS_MAX_HANDS    2
#define FH_HANDS_MAX_CAPSULES 64

enum {
    FH_HAND_RIGHT  = 1u << 0,   /* else the left hand                          */
    FH_HAND_STEREO = 1u << 1,   /* triangulated from two or more cameras       */
};

typedef struct {
    uint32_t id;                /* stays the same while the hand is tracked     */
    uint32_t flags;             /* FH_HAND_*                                    */
    float    confidence;
    float    reserved;
    float    pts[21][3];        /* MediaPipe hand landmarks                     */
    uint32_t ncapsules;         /* this hand's capsules, which follow the       */
                                /* previous hands' in capsules[]                */
} fh_hand_t;                    /* 272 bytes */

typedef struct {
    float a[3], b[3];           /* segment ends                                 */
    float ra, rb;               /* radius at each end                           */
} fh_capsule_t;                 /* 32 bytes: the hand's shape, to cut out       */

typedef struct {
    char              magic[8];
    uint32_t          version;
    uint32_t          size;
    volatile uint64_t seq;
    uint64_t          capture_ns;   /* CLOCK_MONOTONIC when the cameras took the frames */
    uint64_t          publish_ns;   /* CLOCK_MONOTONIC when this was written            */
    uint32_t          nhands;
    uint32_t          ncapsules;
    uint8_t           reserved[16];
    fh_hand_t         hands[FH_HANDS_MAX_HANDS];
    fh_capsule_t      capsules[FH_HANDS_MAX_CAPSULES];
} fh_hands_t;

static_assert(sizeof(fh_hand_t) == 272, "fh_hand_t layout");
static_assert(sizeof(fh_capsule_t) == 32, "fh_capsule_t layout");
static_assert(sizeof(fh_hands_t) == 64 + 2 * 272 + 64 * 32, "fh_hands_t layout");
