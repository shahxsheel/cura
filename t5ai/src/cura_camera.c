#include "cura_camera.h"
#include "tal_api.h"
#include <string.h>
#include <stdint.h>

/* Camera preview requires ENABLE_CAMERA=1 and ENABLE_DVP=1 (from tuya_kconfig.h).
   When not configured, the functions compile to no-ops so the DEV screen still
   builds and shows "Camera not found". */
#if defined(ENABLE_CAMERA) && defined(ENABLE_DVP)

#include "tdl_camera_manage.h"

#ifndef CAMERA_NAME
#define CAMERA_NAME "gc2145"
#endif

static TDL_CAMERA_HANDLE_T s_hdl    = NULL;
static uint16_t           *s_buf    = NULL;
static lv_obj_t           *s_canvas = NULL;
static int                  s_w     = 0;
static int                  s_h     = 0;
static bool                 s_open  = false;
static char                 s_err[48] = "none";

static void _refresh(void *arg)
{
    (void)arg;
    if (s_canvas) {
        lv_obj_invalidate(s_canvas);
    }
}

/* GC2145 native: 480×480 (smallest square in PPI table, used by TuyaOpen example).
 * 640×480 needs ~1.84 MB internal buffers; 480×480 needs ~1.35 MB — fits in PSRAM.
 * We center-crop 320×240 out of the 480×480 frame (no scaling). */
#define CAM_NATIVE_W  480
#define CAM_NATIVE_H  480
#define CAM_X_START   ((CAM_NATIVE_W - 320) / 2)   /* 80 */
#define CAM_Y_START   ((CAM_NATIVE_H - 240) / 2)   /* 120 */
#define CAM_STRIDE    (CAM_NATIVE_W * 2)             /* UYVY: 2 bytes/pixel */

static OPERATE_RET _frame_cb(TDL_CAMERA_HANDLE_T hdl, TDL_CAMERA_FRAME_T *frame)
{
    (void)hdl;
    if (!frame || !frame->data || !s_buf) {
        return OPRT_OK;
    }

    /* Center-crop 320×240 from 480×480 UYVY frame.
     * DVP outputs UYVY: [U, Y0, V, Y1] per 4-byte group (TuyaOpen reference).
     * BT.601 limited-range coefficients match TuyaOpen tal_image_yuv422_to_rgb.c. */
    const uint8_t *base = frame->data;

    for (int yo = 0; yo < s_h; yo++) {
        const uint8_t *row = base + (size_t)(CAM_Y_START + yo) * CAM_STRIDE;
        uint16_t      *dst = s_buf + (size_t)yo * s_w;
        for (int xo = 0; xo < s_w; xo++) {
            int src_col = CAM_X_START + xo;
            const uint8_t *p = row + (src_col >> 1) * 4;  /* UYVY group */
            int32_t d = (int32_t)p[0] - 128;              /* U = Cb */
            int32_t c = (int32_t)(src_col & 1 ? p[3] : p[1]) - 16; /* Y0 or Y1 */
            int32_t e = (int32_t)p[2] - 128;              /* V = Cr */
            int32_t r = (298 * c + 409 * e + 128) >> 8;
            int32_t g = (298 * c - 100 * d - 208 * e + 128) >> 8;
            int32_t b = (298 * c + 516 * d + 128) >> 8;
            r = r < 0 ? 0 : r > 255 ? 255 : r;
            g = g < 0 ? 0 : g > 255 ? 255 : g;
            b = b < 0 ? 0 : b > 255 ? 255 : b;
            dst[xo] = ((uint16_t)(r >> 3) << 11) | ((uint16_t)(g >> 2) << 5) | (uint16_t)(b >> 3);
        }
    }

    lv_async_call(_refresh, NULL);
    return OPRT_OK;
}

const char *cura_camera_last_err(void) { return s_err; }

bool cura_camera_start(lv_obj_t *canvas, int w, int h)
{
    if (s_open) {
        return true;
    }

    snprintf(s_err, sizeof(s_err), "none");

    s_hdl = tdl_camera_find_dev(CAMERA_NAME);
    if (!s_hdl) {
        snprintf(s_err, sizeof(s_err), "find_dev NULL ('%s')", CAMERA_NAME);
        PR_ERR("cura_camera: %s", s_err);
        return false;
    }

    size_t bytes = (size_t)w * h * sizeof(uint16_t);
    s_buf = (uint16_t *)tal_malloc(bytes);
    if (!s_buf) {
        snprintf(s_err, sizeof(s_err), "malloc fail %u bytes", (unsigned)bytes);
        PR_ERR("cura_camera: %s", s_err);
        return false;
    }
    memset(s_buf, 0, bytes);

    s_canvas = canvas;
    s_w      = w;
    s_h      = h;

    lv_canvas_set_buffer(canvas, s_buf, w, h, LV_COLOR_FORMAT_RGB565);

    TDL_CAMERA_CFG_T cfg = {
        .fps          = 15,
        .width        = CAM_NATIVE_W,   /* 480 — in GC2145 PPI table */
        .height       = CAM_NATIVE_H,   /* 480 */
        .out_fmt      = TDL_CAMERA_FMT_YUV422,
        .get_frame_cb = _frame_cb,
    };

    OPERATE_RET rt = tdl_camera_dev_open(s_hdl, &cfg);
    if (rt != OPRT_OK) {
        snprintf(s_err, sizeof(s_err), "dev_open err %d", (int)rt);
        PR_ERR("cura_camera: %s", s_err);
        tal_free(s_buf);
        s_buf    = NULL;
        s_canvas = NULL;
        s_hdl    = NULL;
        return false;
    }

    s_open = true;
    return true;
}

void cura_camera_stop(void)
{
    if (s_hdl) {
        tdl_camera_dev_close(s_hdl);
        s_hdl  = NULL;
        s_open = false;
    }
    s_canvas = NULL;
    if (s_buf) {
        tal_free(s_buf);
        s_buf = NULL;
    }
}

#else  /* ENABLE_CAMERA/ENABLE_DVP not set — stub implementations */

bool cura_camera_start(lv_obj_t *canvas, int w, int h)
{
    (void)canvas; (void)w; (void)h;
    return false;
}

void cura_camera_stop(void) {}

const char *cura_camera_last_err(void) { return "ENABLE_CAMERA/DVP not set"; }

#endif /* ENABLE_CAMERA && ENABLE_DVP */
