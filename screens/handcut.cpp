// Hand cutouts (see handcut.h).
#include "handcut.h"

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <drm_fourcc.h>
#include <fcntl.h>
#include <gbm.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace handcut {
namespace {

// The hands file (frame-hands/include/fh_hands.h).
constexpr char kMagic[8] = {'F', 'H', 'H', 'A', 'N', 'D', 'S', '1'};
constexpr size_t kHeader = 64, kHand = 272, kCapsule = 32, kMaxHands = 2, kMaxCapsules = 64;
constexpr size_t kFileSize = kHeader + kMaxHands * kHand + kMaxCapsules * kCapsule;
constexpr int64_t kStaleNs = 300'000'000;   // hands older than this are gone
constexpr int64_t kHistoryNs = 1'000'000'000;

int64_t MonoNs() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return int64_t(ts.tv_sec) * 1'000'000'000 + ts.tv_nsec;
}

void Apply(const Mat &m, const float p[3], float out[3]) {
    for (int i = 0; i < 3; ++i) out[i] = m.m[i][0] * p[0] + m.m[i][1] * p[1] + m.m[i][2] * p[2] + m.m[i][3];
}

// Room -> panel-local: R^T (p - t).
void ToLocal(const Mat &m, const double p[3], double out[3]) {
    const double d[3] = {p[0] - m.m[0][3], p[1] - m.m[1][3], p[2] - m.m[2][3]};
    for (int i = 0; i < 3; ++i) out[i] = m.m[0][i] * d[0] + m.m[1][i] * d[1] + m.m[2][i] * d[2];
}

}  // namespace

// ------------------------------------------------------------------------------- hands

bool Hands::Read() {
    if (!map_) {
        const int64_t now = MonoNs();
        if (now - lastOpenTry_ < 1'000'000'000) return false;
        lastOpenTry_ = now;
        const char *run = std::getenv("XDG_RUNTIME_DIR");
        const std::string path = std::string(run ? run : "/run/user/" + std::to_string(getuid())) + "/frame-hands/hands";
        fd_ = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd_ < 0) return false;
        struct stat st;
        if (fstat(fd_, &st) < 0 || st.st_uid != getuid() || size_t(st.st_size) < kFileSize) {
            close(fd_), fd_ = -1;
            return false;
        }
        void *m = mmap(nullptr, kFileSize, PROT_READ, MAP_SHARED, fd_, 0);
        if (m == MAP_FAILED) {
            close(fd_), fd_ = -1;
            return false;
        }
        map_ = m;
    }
    const auto *p = static_cast<const volatile uint8_t *>(map_);
    auto u64 = [&](size_t off) { uint64_t v; std::memcpy(&v, const_cast<const uint8_t *>(p) + off, 8); return v; };
    const uint64_t s1 = __atomic_load_n(reinterpret_cast<const uint64_t *>(const_cast<const uint8_t *>(p) + 16), __ATOMIC_ACQUIRE);
    if ((s1 & 1) || s1 == seq_) return false;
    uint8_t copy[kFileSize];
    std::memcpy(copy, const_cast<const uint8_t *>(p), kFileSize);
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    if (u64(16) != s1 || std::memcmp(copy, kMagic, 8) != 0) return false;
    seq_ = s1;
    uint64_t capture, publish;
    uint32_t ncaps;
    std::memcpy(&capture, copy + 24, 8);
    std::memcpy(&publish, copy + 32, 8);
    std::memcpy(&ncaps, copy + 44, 4);
    captureNs_ = int64_t(capture), publishNs_ = int64_t(publish);
    const Mat head = HeadAt(captureNs_);
    world_.clear();
    for (uint32_t k = 0; k < std::min<uint32_t>(ncaps, kMaxCapsules); ++k) {
        float f[8];
        std::memcpy(f, copy + kHeader + kMaxHands * kHand + k * kCapsule, sizeof f);
        bool ok = true;
        for (float v : f) ok = ok && std::isfinite(v) && std::fabs(v) < 10;
        if (!ok || f[6] <= 0 || f[7] <= 0) continue;
        Capsule c;
        Apply(head, f, c.a);
        Apply(head, f + 3, c.b);
        c.ra = f[6], c.rb = f[7];
        world_.push_back(c);
    }
    return true;
}

Mat Hands::HeadAt(int64_t ns) const {
    const Past *best = nullptr;
    for (const Past &p : history_)
        if (!best || std::llabs(p.ns - ns) < std::llabs(best->ns - ns)) best = &p;
    return best ? best->head : Mat{};
}

bool Hands::Update(const Mat &head, int64_t nowNs) {
    history_.push_back({nowNs, head});
    while (!history_.empty() && nowNs - history_.front().ns > kHistoryNs) history_.erase(history_.begin());
    Read();
    if (nowNs - publishNs_ > kStaleNs) world_.clear();
    return !world_.empty();
}

void EyePositions(const Mat &head, double out[2][3]) {
    const vr::EVREye eyes[2] = {vr::Eye_Left, vr::Eye_Right};
    for (int e = 0; e < 2; ++e) {
        const Mat t = vr::VRSystem()->GetEyeToHeadTransform(eyes[e]);
        for (int i = 0; i < 3; ++i)
            out[e][i] = head.m[i][0] * t.m[0][3] + head.m[i][1] * t.m[1][3] + head.m[i][2] * t.m[2][3] + head.m[i][3];
    }
}

// ----------------------------------------------------------------------------- project

namespace {

// Where the line from eye e through point q (both panel-local) meets the panel, as texture
// pixels, and how much a size at q grows there. False if q isn't between the eye and it.
bool OnPanel(const Panel &p, const double e[3], const double q[3], double *x, double *y, double *grow) {
    const double d[3] = {q[0] - e[0], q[1] - e[1], q[2] - e[2]};
    double s, u, v;
    if (p.curve <= 0) {
        if (e[2] <= q[2] || q[2] <= 0) return false;
        s = e[2] / (e[2] - q[2]);
        u = e[0] + s * d[0];
        v = e[1] + s * d[1];
    } else {
        // OpenVR bends a curved panel into a cylinder around (0, *, r), toward its front.
        const double r = p.curve, ez = e[2] - r;
        const double A = d[0] * d[0] + d[2] * d[2], B = 2 * (e[0] * d[0] + ez * d[2]), C = e[0] * e[0] + ez * ez - r * r;
        const double disc = B * B - 4 * A * C;
        if (A < 1e-12 || disc < 0) return false;
        s = (-B + std::sqrt(disc)) / (2 * A);   // the far side: the panel, seen from inside
        const double px = e[0] + s * d[0], pz = e[2] + s * d[2];
        if (pz > r) return false;
        u = r * std::atan2(px, r - pz);
        v = e[1] + s * d[1];
    }
    if (s <= 1) return false;  // the hand is behind the panel
    *x = (u / p.width + 0.5) * p.pxWidth;
    *y = (0.5 - v / p.height) * p.pxHeight;
    *grow = s;
    return true;
}

}  // namespace

bool Project(const Panel &p, const std::vector<Capsule> &caps, const double eyes[2][3], std::vector<Capsule2D> out[2]) {
    const double pxPerM = p.pxWidth / p.width;
    bool any = false;
    for (int e = 0; e < 2; ++e) {
        out[e].clear();
        double eye[3];
        ToLocal(p.pose, eyes[e], eye);
        if (eye[2] <= 0.01) continue;  // behind the panel
        for (const Capsule &c : caps) {
            double a[3], b[3];
            const double wa[3] = {c.a[0], c.a[1], c.a[2]}, wb[3] = {c.b[0], c.b[1], c.b[2]};
            ToLocal(p.pose, wa, a);
            ToLocal(p.pose, wb, b);
            // a hand pushed through the panel: keep the part in front
            const double eps = 0.002;
            if (a[2] < eps && b[2] < eps) continue;
            if (a[2] < eps || b[2] < eps) {
                double *in = a[2] < eps ? b : a, *out3 = a[2] < eps ? a : b;
                const double t = (in[2] - eps) / (in[2] - out3[2]);
                for (int i = 0; i < 3; ++i) out3[i] = in[i] + t * (out3[i] - in[i]);
            }
            double ax, ay, ga, bx, by, gb;
            if (!OnPanel(p, eye, a, &ax, &ay, &ga) || !OnPanel(p, eye, b, &bx, &by, &gb)) continue;
            const float ra = float(c.ra * ga * pxPerM), rb = float(c.rb * gb * pxPerM);
            const float r = std::max(ra, rb);
            if (std::max(ax, bx) + r < 0 || std::min(ax, bx) - r > p.pxWidth || std::max(ay, by) + r < 0 ||
                std::min(ay, by) - r > p.pxHeight)
                continue;
            out[e].push_back({float(ax), float(ay), float(bx), float(by), ra, rb});
            any = true;
        }
    }
    return any;
}

// ---------------------------------------------------------------------------- renderer

namespace {

PFNEGLGETPLATFORMDISPLAYEXTPROC pGetPlatformDisplay;
PFNEGLCREATEIMAGEKHRPROC pCreateImage;
PFNEGLDESTROYIMAGEKHRPROC pDestroyImage;
PFNGLEGLIMAGETARGETTEXTURE2DOESPROC pImageTargetTexture;
PFNGLEGLIMAGETARGETRENDERBUFFERSTORAGEOESPROC pImageTargetRenderbuffer;

const char *kVertex = R"(
attribute vec2 pos;          // the unit square
uniform vec4 rect;           // where it goes, in pixels of the eye's half: x0 y0 x1 y1
uniform vec2 size;           // the half's size in pixels
varying vec2 px;
varying vec2 uv;
void main() {
    px = mix(rect.xy, rect.zw, pos);
    uv = px / size;
    gl_Position = vec4(uv * 2.0 - 1.0, 0.0, 1.0);
})";

// The client's pixels, opaque (its alpha is ignored, as IgnoreTextureAlpha did).
const char *kCopy = R"(
#extension GL_OES_EGL_image_external : require
precision mediump float;
uniform samplerExternalOES tex;
varying vec2 uv;
void main() { gl_FragColor = vec4(texture2D(tex, uv).rgb, 1.0); })";

// Coverage of one tapered capsule; blended to take that much alpha away.
const char *kCut = R"(
precision highp float;
uniform vec2 a, b, r;
uniform float feather;
varying vec2 px;
void main() {
    vec2 ab = b - a;
    float t = clamp(dot(px - a, ab) / max(dot(ab, ab), 1e-6), 0.0, 1.0);
    float d = length(px - (a + t * ab));
    float rad = mix(r.x, r.y, t);
    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0 - smoothstep(rad - feather, rad + feather, d));
})";

unsigned Shader(GLenum type, const char *src) {
    const GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, nullptr);
    glCompileShader(s);
    GLint ok = 0;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024] = "";
        glGetShaderInfoLog(s, sizeof log, nullptr, log);
        std::fprintf(stderr, "handcut: shader: %s\n", log);
    }
    return s;
}

unsigned Program(const char *fs) {
    const GLuint p = glCreateProgram();
    glAttachShader(p, Shader(GL_VERTEX_SHADER, kVertex));
    glAttachShader(p, Shader(GL_FRAGMENT_SHADER, fs));
    glBindAttribLocation(p, 0, "pos");
    glLinkProgram(p);
    GLint ok = 0;
    glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[1024] = "";
        glGetProgramInfoLog(p, sizeof log, nullptr, log);
        std::fprintf(stderr, "handcut: program: %s\n", log);
        return 0;
    }
    return p;
}

EGLImageKHR ImageFor(EGLDisplay dpy, const ft_dmabuf &b) {
    static const EGLint fd[4] = {EGL_DMA_BUF_PLANE0_FD_EXT, EGL_DMA_BUF_PLANE1_FD_EXT, EGL_DMA_BUF_PLANE2_FD_EXT,
                                 EGL_DMA_BUF_PLANE3_FD_EXT};
    static const EGLint off[4] = {EGL_DMA_BUF_PLANE0_OFFSET_EXT, EGL_DMA_BUF_PLANE1_OFFSET_EXT,
                                  EGL_DMA_BUF_PLANE2_OFFSET_EXT, EGL_DMA_BUF_PLANE3_OFFSET_EXT};
    static const EGLint pitch[4] = {EGL_DMA_BUF_PLANE0_PITCH_EXT, EGL_DMA_BUF_PLANE1_PITCH_EXT,
                                    EGL_DMA_BUF_PLANE2_PITCH_EXT, EGL_DMA_BUF_PLANE3_PITCH_EXT};
    static const EGLint lo[4] = {EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_LO_EXT,
                                 EGL_DMA_BUF_PLANE2_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_LO_EXT};
    static const EGLint hi[4] = {EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_HI_EXT,
                                 EGL_DMA_BUF_PLANE2_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_HI_EXT};
    EGLint a[64];
    int n = 0;
    a[n++] = EGL_WIDTH, a[n++] = b.width, a[n++] = EGL_HEIGHT, a[n++] = b.height;
    a[n++] = EGL_LINUX_DRM_FOURCC_EXT, a[n++] = EGLint(b.format);
    for (int i = 0; i < b.n_planes && i < 4; ++i) {
        a[n++] = fd[i], a[n++] = b.fd[i], a[n++] = off[i], a[n++] = EGLint(b.offset[i]);
        a[n++] = pitch[i], a[n++] = EGLint(b.stride[i]);
        if (b.modifier != DRM_FORMAT_MOD_INVALID) {
            a[n++] = lo[i], a[n++] = EGLint(b.modifier & 0xffffffff);
            a[n++] = hi[i], a[n++] = EGLint(b.modifier >> 32);
        }
    }
    a[n++] = EGL_NONE;
    return pCreateImage(dpy, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, nullptr, a);
}

}  // namespace

Renderer::~Renderer() {
    for (auto &[k, r] : rings_)
        for (Output &o : r.out) FreeOutput(o);
    for (auto &[k, im] : imported_) {
        glDeleteTextures(1, &im.tex);
        pDestroyImage(EGLDisplay(dpy_), EGLImageKHR(im.image));
    }
    if (ctx_) eglDestroyContext(EGLDisplay(dpy_), EGLContext(ctx_));
    if (dpy_) eglTerminate(EGLDisplay(dpy_));
    if (gbm_) gbm_device_destroy(static_cast<gbm_device *>(gbm_));
    if (drm_ >= 0) close(drm_);
}

bool Renderer::Init(const std::vector<uint64_t> &modifiers, std::function<void(const Output *)> released) {
    if (ready_) return true;
    modifiers_ = modifiers;
    released_ = std::move(released);
    drm_ = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    if (drm_ < 0) return std::perror("handcut: /dev/dri/renderD128"), false;
    gbm_ = gbm_create_device(drm_);
    pGetPlatformDisplay = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(eglGetProcAddress("eglGetPlatformDisplayEXT"));
    pCreateImage = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
    pDestroyImage = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
    pImageTargetTexture = reinterpret_cast<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(eglGetProcAddress("glEGLImageTargetTexture2DOES"));
    pImageTargetRenderbuffer = reinterpret_cast<PFNGLEGLIMAGETARGETRENDERBUFFERSTORAGEOESPROC>(
        eglGetProcAddress("glEGLImageTargetRenderbufferStorageOES"));
    if (!gbm_ || !pGetPlatformDisplay || !pCreateImage || !pImageTargetTexture || !pImageTargetRenderbuffer) {
        std::fprintf(stderr, "handcut: GBM or EGL extensions missing\n");
        return false;
    }
    EGLDisplay dpy = pGetPlatformDisplay(EGL_PLATFORM_GBM_KHR, gbm_, nullptr);
    if (dpy == EGL_NO_DISPLAY || !eglInitialize(dpy, nullptr, nullptr)) return std::fprintf(stderr, "handcut: no EGL display\n"), false;
    dpy_ = dpy;
    eglBindAPI(EGL_OPENGL_ES_API);
    const EGLint attrs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    EGLContext ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attrs);
    if (ctx == EGL_NO_CONTEXT || !eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx))
        return std::fprintf(stderr, "handcut: no surfaceless GLES context\n"), false;
    ctx_ = ctx;
    copyProg_ = Program(kCopy);
    cutProg_ = Program(kCut);
    if (!copyProg_ || !cutProg_) return false;
    const float quad[] = {0, 0, 1, 0, 0, 1, 1, 1};
    glGenBuffers(1, &vbo_);
    glBindBuffer(GL_ARRAY_BUFFER, vbo_);
    glBufferData(GL_ARRAY_BUFFER, sizeof quad, quad, GL_STATIC_DRAW);
    ready_ = true;
    return true;
}

unsigned Renderer::Texture(const void *key, const ft_dmabuf &src) {
    auto it = imported_.find(key);
    if (it != imported_.end()) return it->second.tex;
    EGLImageKHR image = ImageFor(EGLDisplay(dpy_), src);
    if (image == EGL_NO_IMAGE_KHR) {
        std::fprintf(stderr, "handcut: can't import a %dx%d client buffer (format 0x%x modifier 0x%llx)\n", src.width,
                     src.height, src.format, (unsigned long long)src.modifier);
        return 0;
    }
    GLuint tex;
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, tex);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    pImageTargetTexture(GL_TEXTURE_EXTERNAL_OES, image);
    imported_[key] = {image, tex};
    return tex;
}

void Renderer::Forget(const void *key) {
    auto it = imported_.find(key);
    if (it == imported_.end()) return;
    glDeleteTextures(1, &it->second.tex);
    pDestroyImage(EGLDisplay(dpy_), EGLImageKHR(it->second.image));
    imported_.erase(it);
}

bool Renderer::MakeOutput(Output &o, int w, int h) {
    auto *gbm = static_cast<gbm_device *>(gbm_);
    std::vector<uint64_t> mods;
    for (uint64_t m : modifiers_)
        if (m != DRM_FORMAT_MOD_INVALID) mods.push_back(m);
    gbm_bo *bo = mods.empty() ? gbm_bo_create(gbm, w, h, GBM_FORMAT_ABGR8888, GBM_BO_USE_RENDERING | GBM_BO_USE_LINEAR)
                              : gbm_bo_create_with_modifiers2(gbm, w, h, GBM_FORMAT_ABGR8888, mods.data(),
                                                             unsigned(mods.size()), GBM_BO_USE_RENDERING);
    if (!bo) return std::fprintf(stderr, "handcut: can't allocate a %dx%d output\n", w, h), false;
    o.bo = bo;
    o.buf = {};
    o.buf.width = w, o.buf.height = h;
    o.buf.format = DRM_FORMAT_ABGR8888;
    o.buf.modifier = mods.empty() ? DRM_FORMAT_MOD_LINEAR : gbm_bo_get_modifier(bo);
    o.buf.n_planes = gbm_bo_get_plane_count(bo);
    for (int i = 0; i < o.buf.n_planes && i < 4; ++i) {
        o.buf.fd[i] = gbm_bo_get_fd_for_plane(bo, i);
        o.buf.offset[i] = gbm_bo_get_offset(bo, i);
        o.buf.stride[i] = gbm_bo_get_stride_for_plane(bo, i);
    }
    EGLImageKHR image = ImageFor(EGLDisplay(dpy_), o.buf);
    if (image == EGL_NO_IMAGE_KHR) return std::fprintf(stderr, "handcut: can't render to the output\n"), FreeOutput(o), false;
    o.image = image;
    glGenRenderbuffers(1, &o.rb);
    glBindRenderbuffer(GL_RENDERBUFFER, o.rb);
    pImageTargetRenderbuffer(GL_RENDERBUFFER, image);
    glGenFramebuffers(1, &o.fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, o.fbo);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, o.rb);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
        return std::fprintf(stderr, "handcut: output framebuffer incomplete\n"), FreeOutput(o), false;
    return true;
}

void Renderer::FreeOutput(Output &o) {
    if (o.bo && released_) released_(&o);
    if (o.fbo) glDeleteFramebuffers(1, &o.fbo);
    if (o.rb) glDeleteRenderbuffers(1, &o.rb);
    if (o.image) pDestroyImage(EGLDisplay(dpy_), EGLImageKHR(o.image));
    for (int i = 0; i < o.buf.n_planes && i < 4; ++i)
        if (o.buf.fd[i] >= 0) close(o.buf.fd[i]);
    if (o.bo) gbm_bo_destroy(static_cast<gbm_bo *>(o.bo));
    o = Output{};
}

void Renderer::DropPanel(int panel) {
    auto it = rings_.find(panel);
    if (it == rings_.end()) return;
    for (Output &o : it->second.out) FreeOutput(o);
    rings_.erase(it);
}

const Output *Renderer::Composite(int panel, const void *key, const ft_dmabuf &src, const std::vector<Capsule2D> eyes[2]) {
    if (!ready_) return nullptr;
    const auto t0 = std::chrono::steady_clock::now();
    const int w = src.width, h = src.height;
    Ring &ring = rings_[panel];
    if (ring.w != w || ring.h != h) {
        for (Output &old : ring.out) FreeOutput(old);
        ring.w = w, ring.h = h, ring.next = 0;
    }
    Output &o = ring.out[ring.next];
    if (!o.bo && !MakeOutput(o, 2 * w, h)) return nullptr;
    const GLuint tex = Texture(key, src);
    if (!tex) return nullptr;
    ring.next = (ring.next + 1) % 3;

    glBindFramebuffer(GL_FRAMEBUFFER, o.fbo);
    glBindBuffer(GL_ARRAY_BUFFER, vbo_);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, nullptr);
    for (int e = 0; e < 2; ++e) {
        glViewport(e * w, 0, w, h);
        glDisable(GL_BLEND);
        glUseProgram(copyProg_);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, tex);
        glUniform1i(glGetUniformLocation(copyProg_, "tex"), 0);
        glUniform4f(glGetUniformLocation(copyProg_, "rect"), 0, 0, float(w), float(h));
        glUniform2f(glGetUniformLocation(copyProg_, "size"), float(w), float(h));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        // take alpha away where the hand is; the colour stays (straight alpha)
        glEnable(GL_BLEND);
        glBlendFuncSeparate(GL_ZERO, GL_ONE, GL_ZERO, GL_ONE_MINUS_SRC_ALPHA);
        glUseProgram(cutProg_);
        glUniform2f(glGetUniformLocation(cutProg_, "size"), float(w), float(h));
        const GLint uRect = glGetUniformLocation(cutProg_, "rect"), uA = glGetUniformLocation(cutProg_, "a"),
                    uB = glGetUniformLocation(cutProg_, "b"), uR = glGetUniformLocation(cutProg_, "r"),
                    uF = glGetUniformLocation(cutProg_, "feather");
        for (const Capsule2D &c : eyes[e]) {
            const float feather = std::max(1.5f, 0.15f * std::min(c.ra, c.rb));
            const float r = std::max(c.ra, c.rb) + feather;
            glUniform4f(uRect, std::min(c.ax, c.bx) - r, std::min(c.ay, c.by) - r, std::max(c.ax, c.bx) + r,
                        std::max(c.ay, c.by) + r);
            glUniform2f(uA, c.ax, c.ay);
            glUniform2f(uB, c.bx, c.by);
            glUniform2f(uR, c.ra, c.rb);
            glUniform1f(uF, feather);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        }
    }
    glDisable(GL_BLEND);
    // SteamVR reads the buffer from another process and GPU queue; make sure it's done.
    glFinish();
    lastMs_ = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    return &o;
}

}  // namespace handcut
