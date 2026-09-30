/*
 * fh-camd - publish the headset's IR camera frames to unprivileged trackers.
 *
 * Start it with sudo. As root it:
 *   1. finds the mono tracking cameras and XRService's buffer queues (xrcams.c),
 *   2. borrows those buffers read-only with pidfd_getfd,
 *   3. opens the v4l2_dqbuf tracepoint (tp.c),
 *   4. creates the frame ring in /run/frame-hands (fhring.h), owned by the user.
 * Then it drops to that user for good. From then on it only learns which
 * buffer holds which V4L2 index (as fh-camprobe does), and copies each
 * complete bright frame into the ring. It exits when XRService exits or
 * reallocates its buffers; start it again (or let systemd) to re-attach.
 *
 * XRService itself runs as the same user; root is needed only because
 * ptrace_scope=1 blocks pidfd_getfd and the tracepoints are root-only.
 * Nothing is read from the ring's readers.
 *
 * Build: make
 */

#define _GNU_SOURCE

#include "fhring.h"
#include "tp.h"
#include "xrcams.h"

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/dma-buf.h>
#include <math.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif

#ifndef SYS_pidfd_getfd
#define SYS_pidfd_getfd 438
#endif

#define MAX_CAMS    FH_RING_MAX_CAMS
#define MAX_SLOTS   64
#define MAX_INDEX   32
#define NSAMP      512
#define STALE_RELEARN 30    /* consecutive unchanged frames: the mapping changed      */
#define MAX_RELEARNS  5     /* then assume XRService has new buffers, and exit        */
#define RING_DIR   FH_RING_DIR
#define RING_FILE  FH_RING_PATH

typedef struct {
    xr_camera_t    *cam;
    xr_layout_t     lay;
    size_t          need;               /* bytes of plane 0 holding the image */
    char            slug[32];

    /* candidate buffers, in XRService allocation order */
    int             nslots;
    int             slot_group[MAX_SLOTS];
    int             slot_pos[MAX_SLOTS];
    int             fd[MAX_SLOTS];      /* kept for DMA_BUF_IOCTL_SYNC       */
    const uint8_t  *map[MAX_SLOTS];
    uint64_t        samp[MAX_SLOTS][NSAMP];

    /* index -> buffer learning */
    int             votes[MAX_INDEX][MAX_SLOTS];
    int             nobs[MAX_INDEX];
    int             maxindex;
    int             learn_events;
    bool            mapped;
    int             slot_of[MAX_INDEX]; /* buffer holding each V4L2 index     */
    int             relearns;
    bool            shared;             /* its buffers sit among another camera's  */

    double          recent[8];          /* means of the last frames, for dark detection */
    int             nrecent;
    int             stale_run;
    uint64_t        hist[2][NSAMP];     /* samples of its last two frames (bright and dark) */

    fh_ring_cam_t  *rc;
    uint8_t        *ring_slots;
    uint64_t        frame_no;
    fh_ring_cam_t  *rc_dark;            /* --with-dark: its near-black frames */
    uint8_t        *ring_dark;
    uint64_t        dark_no;

    uint64_t        events, bright, dark, stale, torn, repairs;
    uint64_t        sync_ns, copy_ns, nsync;    /* cost of cache sync and copy */
    uint64_t        last_bright;        /* for the status line */
} cam_t;

static cam_t       cams[MAX_CAMS];
static uint64_t    filled[XR_MAX_GROUPS][XR_MAX_RUNBUFS];   /* when each buffer last held a frame */
static uint64_t    nfilled;
static int         ncams;
static xr_state_t  xr;

static tp_event_t  ev_dqbuf;
static int         f_dq_minor, f_dq_index, f_dq_ts, f_dq_seq;

static const char *opt_sensor = "";
static const char *opt_user   = NULL;
static double      opt_dark   = 0.4;
static bool        opt_with_dark;       /* also publish the near-black frames */
static double      opt_status = 10.0;

static volatile sig_atomic_t stop;

/* ------------------------------------------------------------- utilities */

static void die(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    exit(1);
}

static uint64_t mono_ns(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);

    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void sample_words(const uint8_t *p, size_t len, uint64_t *out)
{
    size_t step = (len / NSAMP) & ~(size_t)7;

    if (!step)
        step = 8;

    for (int i = 0; i < NSAMP; i++) {

        size_t off = (size_t)i * step;

        if (off + 8 > len)
            off = len - 8;

        memcpy(&out[i], p + off, 8);
    }
}

/* Mean of an 8-bit image on a sparse grid. */
static double luma_mean(const cam_t *c, const uint8_t *p)
{
    const xr_layout_t *l = &c->lay;
    uint64_t sum = 0, n = 0;

    for (unsigned y = 0; y < l->height; y += 8)
        for (unsigned x = 0; x < l->width; x += 8, n++)
            sum += p[(size_t)y * l->pitch + x];

    return n ? (double)sum / (double)n : 0.0;
}

/* Make the CPU's view of a camera buffer current; harmless if it already is. */
static void buf_sync(int fd, uint64_t flags)
{
    struct dma_buf_sync s = { .flags = flags | DMA_BUF_SYNC_READ };

    if (fd >= 0)
        ioctl(fd, DMA_BUF_IOCTL_SYNC, &s);
}

static cam_t *cam_by_minor(int64_t minor)
{
    for (int i = 0; i < ncams; i++)
        if ((int64_t)cams[i].cam->minor == minor)
            return &cams[i];

    return NULL;
}

static void model_of(const char *sensor, char *out, size_t n)
{
    char buf[64];

    if (sscanf(sensor, "%63s", buf) != 1)
        buf[0] = 0;

    snprintf(out, n, "%s", buf);
}

/* ---------------------------------------------------------------- setup */

static bool group_fits(const xr_group_t *g, const xr_camera_t *cam, size_t need)
{
    return g->planesize[1] == cam->planesize[1] && g->planesize[0] >= need;
}

/*
 * Candidate buffers for a camera: the queue bound to it. The two upper cameras
 * share one 32-buffer run, bound to only one of them, so a camera without its
 * own run also gets runs bound to cameras of the same model.
 */
static void setup_camera(cam_t *c, xr_camera_t *cam, int pidfd)
{
    memset(c, 0, sizeof(*c));
    c->cam  = cam;
    xr_camera_layout(cam, &c->lay);
    c->need = (size_t)c->lay.pitch * c->lay.rows;

    char sensor[XR_SENSOR_LEN];
    xr_slugify(cam->sensor, sensor, sizeof(sensor));
    snprintf(c->slug, sizeof(c->slug), "%.20s_video%d", sensor, cam->node);

    bool own = false;

    for (int g = 0; g < xr.ngroups; g++)
        if (xr.groups[g].cam == cam && group_fits(&xr.groups[g], cam, c->need))
            own = true;

    char model[16];
    model_of(cam->sensor, model, sizeof(model));

    for (int g = 0; g < xr.ngroups; g++) {

        xr_group_t *grp = &xr.groups[g];

        if (!group_fits(grp, cam, c->need))
            continue;

        if (own && grp->cam != cam)
            continue;

        if (!own) {
            char gm[16];
            model_of(grp->cam ? grp->cam->sensor : grp->sensor, gm, sizeof(gm));
            if (strcmp(gm, model))
                continue;
        }

        for (int b = 0; b < grp->nbufs && c->nslots < MAX_SLOTS; b++) {

            int fd = (int)syscall(SYS_pidfd_getfd, pidfd, grp->buf[b].xfd, 0u);

            if (fd < 0) {
                fprintf(stderr, "pidfd_getfd(%d): %s\n", grp->buf[b].xfd, strerror(errno));
                continue;
            }

            void *m = mmap(NULL, grp->buf[b].size, PROT_READ, MAP_SHARED, fd, 0);

            if (m == MAP_FAILED) {
                fprintf(stderr, "mmap(xfd %d): %s\n", grp->buf[b].xfd, strerror(errno));
                close(fd);
                continue;
            }

            int s = c->nslots++;
            c->slot_group[s] = g;
            c->slot_pos[s]   = b;
            c->fd[s]         = fd;
            c->map[s]        = m;

            sample_words(c->map[s], c->need, c->samp[s]);
        }
    }

    printf("  %-24s %-12s %ux%u pitch %u, %d candidate buffers%s\n", c->slug, cam->path,
           c->lay.width, c->lay.height, c->lay.pitch, c->nslots, own ? "" : " (shared run)");
}

/* ------------------------------------------------------ index -> buffer */

/*
 * Every buffer that changed (about as much as the most-changed one) since this
 * camera's previous frame gets a vote for the V4L2 index just completed.
 *
 * XRService queues each index with the same buffer every time, but which
 * buffer depends on how it (re)started streaming. Right after allocation it is
 * allocation order, so a camera's buffers are a contiguous block (slot = base +
 * index, bases at multiples of the queue depth); that is the only way to tell
 * apart two cameras sharing one run, since their frames land at the same time.
 * After a restart the order is shuffled; a camera with its own run is then
 * mapped index by index.
 */
static bool resolve_block(cam_t *c)
{
    int depth = c->maxindex + 1;
    int best = -1;
    double s1 = -1, s2 = -1;

    for (int b = 0; b < c->nslots; b++) {

        if (c->slot_pos[b] % depth)
            continue;

        if (b + depth > c->nslots || c->slot_group[b + depth - 1] != c->slot_group[b])
            continue;

        int hit = 0, obs = 0;

        for (int i = 0; i < depth; i++) {
            hit += c->votes[i][b + i] < c->nobs[i] ? c->votes[i][b + i] : c->nobs[i];
            obs += c->nobs[i];
        }

        double score = obs ? (double)hit / obs : 0;

        if (score > s1) {
            s2 = s1; s1 = score; best = b;
        } else if (score > s2) {
            s2 = score;
        }
    }

    if (s2 < 0)
        s2 = 0;

    if (best < 0 || s1 < 0.8 || s1 - s2 < 0.3)
        return false;

    for (int i = 0; i < depth; i++)
        c->slot_of[i] = best + i;

    printf("%s: queue depth %d, buffers %d-%d in order (score %.2f vs %.2f)\n", c->slug, depth, best,
           best + depth - 1, s1, s2);
    return true;
}

static bool resolve_each(cam_t *c)
{
    int depth = c->maxindex + 1;
    bool taken[MAX_SLOTS] = { false };

    for (int i = 0; i < depth; i++) {

        int best = -1, v1 = 0, v2 = 0;

        for (int s = 0; s < c->nslots; s++) {
            if (c->votes[i][s] > v1) {
                v2 = v1; v1 = c->votes[i][s]; best = s;
            } else if (c->votes[i][s] > v2) {
                v2 = c->votes[i][s];
            }
        }

        if (c->nobs[i] < 3 || best < 0 || taken[best] || v1 < 0.8 * c->nobs[i] || v2 > 0.3 * c->nobs[i])
            return false;

        taken[best] = true;
        c->slot_of[i] = best;
    }

    printf("%s: queue depth %d, buffers mapped one by one:", c->slug, depth);
    for (int i = 0; i < depth; i++)
        printf(" %d", c->slot_of[i]);
    printf("\n");
    return true;
}

static void reset_learning(cam_t *c)
{
    memset(c->votes, 0, sizeof(c->votes));
    memset(c->nobs, 0, sizeof(c->nobs));
    c->maxindex     = 0;
    c->learn_events = 0;
    c->mapped       = false;
}

static void resolve_map(cam_t *c)
{
    int depth = c->maxindex + 1;
    bool shared = c->slot_group[0] != c->slot_group[c->nslots - 1] || c->nslots > depth;

    /* a shuffled shared run can't be split by change votes alone */
    c->shared = shared;
    c->mapped = resolve_block(c) || (!shared && resolve_each(c));

    if (c->mapped) {
        fflush(stdout);
    } else if (c->learn_events > 20 * depth) {
        fprintf(stderr, "%s: can't tell which buffers are this camera's yet; still learning\n", c->slug);
        reset_learning(c);
    }
}

static void learn(cam_t *c, int index)
{
    static uint64_t cur[NSAMP];
    int changed[MAX_SLOTS], maxc = 0;

    for (int s = 0; s < c->nslots; s++) {

        sample_words(c->map[s], c->need, cur);

        changed[s] = 0;

        for (int i = 0; i < NSAMP; i++)
            changed[s] += cur[i] != c->samp[s][i];

        memcpy(c->samp[s], cur, sizeof(cur));

        if (changed[s] > maxc)
            maxc = changed[s];
    }

    c->nobs[index]++;
    c->learn_events++;

    if (index > c->maxindex)
        c->maxindex = index;

    if (maxc >= NSAMP / 50)
        for (int s = 0; s < c->nslots; s++)
            if (changed[s] * 2 >= maxc)
                c->votes[index][s]++;

    if (c->learn_events >= 24 && c->learn_events >= 3 * (c->maxindex + 1))
        resolve_map(c);
}

/*
 * A frame is complete in a buffer when its samples changed since we last saw
 * that buffer, down to the bottom rows (a buffer still being written has an
 * unchanged tail).
 */
static bool fresh(const uint64_t *cur, const uint64_t *old, int *nchanged)
{
    int n = 0, tail = 0;

    for (int i = 0; i < NSAMP; i++) {
        bool ch = cur[i] != old[i];
        n += ch;
        tail += ch && i >= NSAMP - NSAMP / 8;
    }

    *nchanged = n;
    return tail > 0;
}

/* How different two sets of samples are: mean absolute difference per byte. */
static double sample_distance(const uint64_t *a, const uint64_t *b)
{
    const uint8_t *x = (const uint8_t *)a, *y = (const uint8_t *)b;
    uint64_t sum = 0;

    for (size_t i = 0; i < NSAMP * 8; i++)
        sum += (uint64_t)abs((int)x[i] - (int)y[i]);

    return (double)sum / (NSAMP * 8);
}

static uint64_t *filled_at(const cam_t *c, int s)
{
    return &filled[c->slot_group[s]][c->slot_pos[s]];
}

/*
 * XRService mostly queues each index with the same buffer, but not always, and
 * under load the order churns. When the mapped buffer holds no new frame, look
 * for the one that does, starting with the buffers filled longest ago (the
 * likeliest to be next), and remember it for this index. Each probe costs a
 * cache sync of the whole buffer (the camera's DMA isn't cache-coherent), so it
 * stops at the first fresh one. Two cameras sharing a run finish frames at the
 * same moment, so there it takes the fresher of the first two that looks most
 * like this camera's previous frames.
 */
static int find_fresh(cam_t *c, uint64_t *out)
{
    static uint64_t cur[NSAMP];
    int order[MAX_SLOTS], best = -1, nfresh = 0, n;
    double bestd = 1e18;

    for (int s = 0; s < c->nslots; s++)
        order[s] = s;

    for (int i = 1; i < c->nslots; i++)         /* insertion sort, oldest first */
        for (int j = i; j > 0 && *filled_at(c, order[j]) < *filled_at(c, order[j - 1]); j--) {
            int t = order[j]; order[j] = order[j - 1]; order[j - 1] = t;
        }

    for (int k = 0; k < c->nslots; k++) {

        int s = order[k];

        buf_sync(c->fd[s], DMA_BUF_SYNC_START);
        sample_words(c->map[s], c->need, cur);
        buf_sync(c->fd[s], DMA_BUF_SYNC_END);

        if (!fresh(cur, c->samp[s], &n))
            continue;

        double d = c->shared ? fmin(sample_distance(cur, c->hist[0]), sample_distance(cur, c->hist[1])) : 0;

        if (d < bestd) {
            bestd = d;
            best = s;
            memcpy(out, cur, sizeof(cur));
        }

        if (!c->shared || ++nfresh == 2)
            break;
    }

    return best;
}

/* ------------------------------------------------------------- publishing */

/* Copy a frame into ring camera rc (the camera's own, or its dark twin). */
static void publish(cam_t *c, fh_ring_cam_t *rc, uint8_t *slots, uint64_t *frame_no,
                    const uint8_t *src, uint32_t seq, uint64_t ts, uint64_t evtime, double mean)
{
    uint64_t n = ++*frame_no;
    fh_ring_slot_t *s = (fh_ring_slot_t *)(slots + (n % rc->nslots) * rc->slot_bytes);
    uint8_t *dst = (uint8_t *)(s + 1);
    static uint64_t before[NSAMP], after[NSAMP];

    __atomic_store_n(&s->seq, 2 * n + 1, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);

    sample_words(src, c->need, before);

    for (unsigned y = 0; y < c->lay.height; y++)
        memcpy(dst + (size_t)y * rc->stride, src + (size_t)y * c->lay.pitch, c->lay.width);

    sample_words(src, c->need, after);

    if (memcmp(before, after, sizeof(before))) {
        /* XRService re-queued the buffer and the camera overwrote it mid-copy */
        __atomic_store_n(&s->seq, 0, __ATOMIC_RELEASE);
        c->torn++;
        rc->dropped++;
        return;
    }

    s->frame      = n;
    s->capture_ns = ts;
    s->dqbuf_ns   = evtime;
    s->publish_ns = mono_ns();
    s->v4l2_seq   = seq;
    s->mean       = (float)mean;

    __atomic_store_n(&s->seq, 2 * n + 2, __ATOMIC_RELEASE);
    __atomic_store_n(&rc->latest, n, __ATOMIC_RELEASE);
    rc->published++;
}

static void on_frame(cam_t *c, int64_t index, uint32_t seq, uint64_t ts, uint64_t evtime)
{
    c->events++;

    if (index < 0 || index >= MAX_INDEX || c->nslots == 0)
        return;

    if (!c->mapped) {
        learn(c, (int)index);
        return;
    }

    if (index > c->maxindex) {
        reset_learning(c);     /* the queue grew */
        return;
    }

    int slot = c->slot_of[index];

    static uint64_t cur[NSAMP];
    uint64_t t0 = mono_ns();

    buf_sync(c->fd[slot], DMA_BUF_SYNC_START);
    c->sync_ns += mono_ns() - t0;
    c->nsync++;
    sample_words(c->map[slot], c->need, cur);

    int nchanged;
    int found = -1;

    if (!fresh(cur, c->samp[slot], &nchanged)) {
        buf_sync(c->fd[slot], DMA_BUF_SYNC_END);
        found = find_fresh(c, cur);
        if (found >= 0) {
            c->slot_of[index] = slot = found;
            c->repairs++;
            buf_sync(c->fd[slot], DMA_BUF_SYNC_START);
        }
    }

    if (found < 0 && !fresh(cur, c->samp[slot], &nchanged)) {
        buf_sync(c->fd[slot], DMA_BUF_SYNC_END);
        c->stale++;
        c->rc->dropped++;
        if (++c->stale_run >= STALE_RELEARN) {
            if (++c->relearns > MAX_RELEARNS) {
                fprintf(stderr, "%s: its buffers keep going stale; XRService must have new ones\n", c->slug);
                exit(3);
            }
            fprintf(stderr, "%s: %d frames in a row unchanged; relearning its buffers\n", c->slug, c->stale_run);
            c->stale_run = 0;
            reset_learning(c);
        }
        return;
    }

    c->stale_run = 0;
    memcpy(c->samp[slot], cur, sizeof(cur));
    memcpy(c->hist[1], c->hist[0], sizeof(cur));
    memcpy(c->hist[0], cur, sizeof(cur));
    *filled_at(c, slot) = ++nfilled;

    /*
     * The cameras alternate a normal exposure with a near-black one, so judge
     * each frame against this camera's recent brightest.
     */
    double mean = luma_mean(c, c->map[slot]);
    double peak = mean;

    c->recent[c->nrecent++ % 8] = mean;

    for (int i = 0; i < 8 && i < c->nrecent; i++)
        if (c->recent[i] > peak)
            peak = c->recent[i];

    if (mean < opt_dark * peak) {
        c->dark++;
        c->rc->dropped++;
        if (c->rc_dark)
            publish(c, c->rc_dark, c->ring_dark, &c->dark_no, c->map[slot], seq, ts, evtime, mean);
    } else {
        c->bright++;
        uint64_t t1 = mono_ns();
        publish(c, c->rc, c->ring_slots, &c->frame_no, c->map[slot], seq, ts, evtime, mean);
        c->copy_ns += mono_ns() - t1;
    }

    buf_sync(c->fd[slot], DMA_BUF_SYNC_END);
}

static void on_sample(void *ctx, const tp_sample_t *s)
{
    (void)ctx;

    cam_t *c = cam_by_minor(tp_get(s->ev, f_dq_minor, s->raw, s->rawlen));

    if (c)
        on_frame(c, tp_get(s->ev, f_dq_index, s->raw, s->rawlen),
                 (uint32_t)tp_get(s->ev, f_dq_seq, s->raw, s->rawlen),
                 (uint64_t)tp_get(s->ev, f_dq_ts, s->raw, s->rawlen), s->time);
}

/* ------------------------------------------------------------ ring + user */

/* The ring lives in a root-owned directory, so nobody can plant a file or link there. */
static uint8_t *ring_create(uid_t uid, gid_t gid, size_t *len_out)
{
    if (mkdir(RING_DIR, 0755) < 0 && errno != EEXIST)
        die("mkdir %s: %s", RING_DIR, strerror(errno));

    struct stat st;

    if (lstat(RING_DIR, &st) < 0 || !S_ISDIR(st.st_mode) || st.st_uid != 0 || (st.st_mode & 022))
        die("%s must be a directory owned by root and writable only by root", RING_DIR);

    size_t len = sizeof(fh_ring_hdr_t);
    int nring = opt_with_dark ? 2 * ncams : ncams;

    for (int i = 0; i < nring; i++) {
        cam_t *c = &cams[i % ncams];
        size_t slot = sizeof(fh_ring_slot_t) + (size_t)c->lay.width * c->lay.height;
        len += FH_RING_SLOTS * ((slot + 63) & ~(size_t)63);
    }

    if (unlink(RING_FILE) < 0 && errno != ENOENT)
        die("unlink %s: %s", RING_FILE, strerror(errno));

    int fd = open(RING_FILE, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);

    if (fd < 0)
        die("create %s: %s", RING_FILE, strerror(errno));

    if (fchown(fd, uid, gid) < 0 || ftruncate(fd, (off_t)len) < 0)
        die("prepare %s: %s", RING_FILE, strerror(errno));

    uint8_t *m = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);

    if (m == MAP_FAILED)
        die("mmap %s: %s", RING_FILE, strerror(errno));

    fh_ring_hdr_t *h = (fh_ring_hdr_t *)m;
    size_t off = sizeof(*h);

    for (int i = 0; i < nring; i++) {

        cam_t *c = &cams[i % ncams];
        fh_ring_cam_t *rc = &h->cams[i];
        bool dark = i >= ncams;

        snprintf(rc->sensor, sizeof(rc->sensor), "%s", c->cam->sensor);
        snprintf(rc->name, sizeof(rc->name), "%.26s%s", c->slug, dark ? "-dark" : "");
        rc->flags       = dark ? FH_CAM_DARK : 0;
        rc->node        = c->cam->node;
        rc->format      = FH_FMT_GREY8;
        rc->width       = c->lay.width;
        rc->height      = c->lay.height;
        rc->stride      = c->lay.width;
        rc->nslots      = FH_RING_SLOTS;
        rc->slot_offset = off;
        rc->slot_bytes  = (sizeof(fh_ring_slot_t) + (size_t)rc->stride * rc->height + 63) & ~(size_t)63;
        off += rc->nslots * rc->slot_bytes;

        if (dark) {
            c->rc_dark   = rc;
            c->ring_dark = m + rc->slot_offset;
        } else {
            c->rc         = rc;
            c->ring_slots = m + rc->slot_offset;
        }
    }

    memcpy(h->magic, FH_RING_MAGIC, 8);
    h->version      = FH_RING_VERSION;
    h->header_bytes = sizeof(*h);
    h->ncams        = (uint32_t)nring;
    h->file_bytes   = len;
    h->writer_pid   = getpid();
    h->heartbeat_ns = mono_ns();

    *len_out = len;
    return m;
}

static void target_user(uid_t *uid, gid_t *gid)
{
    if (opt_user) {
        struct passwd *pw = getpwnam(opt_user);
        if (!pw)
            die("unknown user %s", opt_user);
        *uid = pw->pw_uid;
        *gid = pw->pw_gid;
    } else if (getenv("SUDO_UID") && getenv("SUDO_GID")) {
        *uid = (uid_t)atoi(getenv("SUDO_UID"));
        *gid = (gid_t)atoi(getenv("SUDO_GID"));
    } else {
        die("run through sudo or pass --user NAME: fh-camd drops root once set up");
    }

    if (*uid == 0)
        die("refusing to keep running as root; pass --user NAME");
}

static void drop_root(uid_t uid, gid_t gid)
{
    if (setgroups(0, NULL) < 0 || setresgid(gid, gid, gid) < 0 || setresuid(uid, uid, uid) < 0)
        die("dropping root: %s", strerror(errno));

    if (setuid(0) == 0 || geteuid() == 0)
        die("dropping root failed");

    prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
}

/* ------------------------------------------------------------------- main */

static void status(double secs)
{
    printf("[%.0f s]", secs);

    for (int i = 0; i < ncams; i++) {
        cam_t *c = &cams[i];
        printf("  %s %s%.1f fps (dark %llu stale %llu torn %llu remapped %llu, sync %.2f ms copy %.2f ms)",
               c->slug, c->mapped ? "" : "learning ", (double)(c->bright - c->last_bright) / opt_status,
               (unsigned long long)c->dark, (unsigned long long)c->stale, (unsigned long long)c->torn,
               (unsigned long long)c->repairs,
               c->nsync ? c->sync_ns / 1e6 / c->nsync : 0.0, c->bright ? c->copy_ns / 1e6 / c->bright : 0.0);
        c->last_bright = c->bright;
    }

    printf("\n");
    fflush(stdout);
}

static void on_signal(int sig)
{
    (void)sig;
    stop = 1;
}

static void usage(const char *argv0)
{
    printf("Usage: sudo %s [options]\n"
           "  --user NAME    user to run as after setup and to own the ring (default: $SUDO_USER)\n"
           "  --sensor S     only cameras whose sensor name contains S (default: all mono cameras)\n"
           "  --dark R       a frame dimmer than R x the camera's recent brightest is dark (default 0.4)\n"
           "  --with-dark    also publish the dark frames, as extra ring cameras flagged FH_CAM_DARK\n"
           "  --status S     print a status line every S seconds, 0 for never (default 10)\n"
           "Frames go to " RING_FILE " (layout in fhring.h).\n", argv0);
}

int main(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {

        if (!strcmp(argv[i], "--user") && i + 1 < argc)
            opt_user = argv[++i];
        else if (!strcmp(argv[i], "--sensor") && i + 1 < argc)
            opt_sensor = argv[++i];
        else if (!strcmp(argv[i], "--dark") && i + 1 < argc)
            opt_dark = atof(argv[++i]);
        else if (!strcmp(argv[i], "--with-dark"))
            opt_with_dark = true;
        else if (!strcmp(argv[i], "--status") && i + 1 < argc)
            opt_status = atof(argv[++i]);
        else {
            usage(argv[0]);
            return strcmp(argv[i], "--help") ? 1 : 0;
        }
    }

    if (geteuid() != 0)
        die("run with sudo: borrowing XRService's buffers and reading tracepoints need root");

    uid_t uid;
    gid_t gid;
    target_user(&uid, &gid);

    char err[512];

    if (!xr_discover(&xr, "XRService", err, sizeof(err)))
        die("%s", err);

    if (!tp_event_load(&ev_dqbuf, "v4l2", "v4l2_dqbuf", err, sizeof(err)))
        die("tracepoint v4l2:v4l2_dqbuf: %s", err);

    f_dq_minor = tp_field(&ev_dqbuf, "minor");
    f_dq_index = tp_field(&ev_dqbuf, "index");
    f_dq_ts    = tp_field(&ev_dqbuf, "timestamp");
    f_dq_seq   = tp_field(&ev_dqbuf, "sequence");

    if (f_dq_minor < 0 || f_dq_index < 0 || f_dq_ts < 0 || f_dq_seq < 0)
        die("v4l2_dqbuf lacks the minor/index/timestamp/sequence fields");

    int pidfd = (int)syscall(SYS_pidfd_open, xr.pid, 0u);

    if (pidfd < 0)
        die("pidfd_open(%d): %s", xr.pid, strerror(errno));

    printf("XRService pid %d\ncameras:\n", xr.pid);

    for (int i = 0; i < xr.ncameras && ncams < MAX_CAMS; i++) {

        xr_camera_t *cam = &xr.cameras[i];
        xr_layout_t lay;

        xr_camera_layout(cam, &lay);

        if (lay.fmt == XR_FMT_GREY8 && strstr(cam->sensor, opt_sensor))
            setup_camera(&cams[ncams++], cam, pidfd);
    }

    if (!ncams)
        die("no mono camera matches '%s'", opt_sensor);

    if (opt_with_dark && 2 * ncams > FH_RING_MAX_CAMS)
        die("--with-dark needs a ring camera per dark stream too: pick at most %d cameras with --sensor",
            FH_RING_MAX_CAMS / 2);

    static tp_t tp;     /* large: pending-sample pool */
    tp_event_t *evs[1] = { &ev_dqbuf };

    if (!tp_open(&tp, evs, 1, err, sizeof(err)))
        die("%s", err);

    size_t ring_len;
    fh_ring_hdr_t *ring = (fh_ring_hdr_t *)ring_create(uid, gid, &ring_len);

    drop_root(uid, gid);
    printf("ring %s (%.1f MB), running as uid %d\n", RING_FILE, ring_len / 1e6, (int)getuid());
    fflush(stdout);

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    uint64_t start = mono_ns(), last_status = start;
    int rc = 0;

    while (!stop) {

        if (tp_poll(&tp, 100, on_sample, NULL) < 0) {
            rc = 1;
            break;
        }

        uint64_t now = mono_ns();
        __atomic_store_n(&ring->heartbeat_ns, now, __ATOMIC_RELEASE);

        struct pollfd pf = { .fd = pidfd, .events = POLLIN };

        if (poll(&pf, 1, 0) > 0) {
            fprintf(stderr, "XRService exited\n");
            rc = 2;
            break;
        }

        if (opt_status > 0 && (double)(now - last_status) / 1e9 >= opt_status) {
            status((double)(now - start) / 1e9);
            last_status = now;
        }
    }

    tp_close(&tp);
    ring->heartbeat_ns = 0;     /* tells readers the writer is gone */

    return rc;
}
