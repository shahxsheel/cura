/*
 * cura_ui.c — LVGL v9 UI for the Cura T5AI board.
 *
 * Main screen layout (320 × 480 portrait):
 *
 *   ┌────────────────────────┐
 *   │  CURA  (header)  [DEV] │  h=56
 *   ├────────────────────────┤
 *   │                        │
 *   │  [  state_label  ]     │  h=140  large centred text
 *   │                        │
 *   ├────────────────────────┤
 *   │  [ ▶  START       ]    │  h=90   green button
 *   ├────────────────────────┤
 *   │  [ ■  STOP        ]    │  h=90   red button
 *   ├────────────────────────┤
 *   │  (status bar)          │  h=54
 *   └────────────────────────┘
 *
 * Tapping [DEV] loads the dev screen:
 *   - WiFi SSID (compile-time), board IP (runtime), server address
 *   - Start/Stop camera preview (GC2145 via cura_camera.c)
 */

#include <string.h>
#include <stdio.h>
#include "lvgl.h"
#include "cura_config.h"
#include "cura_http.h"
#include "cura_camera.h"
#include "cura_ui.h"
#include "tkl_wifi.h"

/* ---------------------------------------------------------------------- */
/* Widget handles — main screen                                             */
/* ---------------------------------------------------------------------- */

static lv_obj_t *g_label_state  = NULL;
static lv_obj_t *g_label_status = NULL;

static char g_pending_label[CURA_LABEL_MAX] = "Connecting...";
static bool g_label_dirty = false;

/* Screen handles for navigation */
static lv_obj_t *g_main_screen = NULL;
static lv_obj_t *g_dev_screen  = NULL;

/* Dev screen widget references (valid only while dev screen is active) */
static lv_obj_t *g_dev_cam_btn_label = NULL;
static lv_obj_t *g_dev_cam_canvas    = NULL;
static lv_obj_t *g_dev_no_cam_label  = NULL;
static bool      g_dev_cam_running   = false;

/* ---------------------------------------------------------------------- */
/* Main screen button callbacks                                             */
/* ---------------------------------------------------------------------- */

static void _cb_start(lv_event_t *e)
{
    (void)e;
    bool ok = cura_http_post_command(
        CURA_SERVER_HOST, CURA_SERVER_PORT, "start_feeding"
    );
    lv_label_set_text(g_label_status, ok ? "Sent: start" : "Send failed");
}

static void _cb_estop(lv_event_t *e)
{
    (void)e;
    bool ok = cura_http_post_command(
        CURA_SERVER_HOST, CURA_SERVER_PORT, "emergency_stop"
    );
    lv_label_set_text(g_label_status, ok ? "Sent: STOP" : "Send failed");
}

/* ---------------------------------------------------------------------- */
/* lv_async_call target — marshals label update onto LVGL thread           */
/* ---------------------------------------------------------------------- */

static void _apply_pending_label(void *arg)
{
    (void)arg;
    if (g_label_dirty && g_label_state != NULL) {
        lv_label_set_text(g_label_state, g_pending_label);
        g_label_dirty = false;
    }
}

/* ---------------------------------------------------------------------- */
/* Helper: full-width button with centred text                             */
/* ---------------------------------------------------------------------- */

static lv_obj_t *_make_button(
    lv_obj_t      *parent,
    const char    *text,
    lv_color_t     bg_color,
    lv_event_cb_t  cb
) {
    lv_obj_t *btn = lv_button_create(parent);
    lv_obj_set_size(btn, CURA_DISPLAY_W - 32, 90);
    lv_obj_set_style_bg_color(btn, bg_color, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(btn, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(btn, 12, LV_PART_MAIN);

    lv_obj_t *lbl = lv_label_create(btn);
    lv_label_set_text(lbl, text);
    lv_obj_set_style_text_font(lbl, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_set_style_text_color(lbl, lv_color_white(), LV_PART_MAIN);
    lv_obj_center(lbl);

    lv_obj_add_event_cb(btn, cb, LV_EVENT_CLICKED, NULL);
    return btn;
}

/* ---------------------------------------------------------------------- */
/* Dev screen callbacks                                                     */
/* ---------------------------------------------------------------------- */

static void _cb_back(lv_event_t *e)
{
    (void)e;
    cura_camera_stop();
    g_dev_cam_running   = false;
    g_dev_cam_btn_label = NULL;
    g_dev_cam_canvas    = NULL;
    g_dev_no_cam_label  = NULL;
    lv_screen_load(g_main_screen);
    /* g_dev_screen deleted on next DEV tap to avoid deleting from within
       its own event handler */
}

static void _cb_camera(lv_event_t *e)
{
    (void)e;
    if (!g_dev_cam_running) {
        bool ok = cura_camera_start(g_dev_cam_canvas, 320, 240);
        if (ok) {
            g_dev_cam_running = true;
            lv_label_set_text(g_dev_cam_btn_label, LV_SYMBOL_STOP "  Stop Camera");
            lv_obj_clear_flag(g_dev_cam_canvas, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(g_dev_no_cam_label, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_label_set_text(g_dev_no_cam_label, cura_camera_last_err());
        }
    } else {
        cura_camera_stop();
        g_dev_cam_running = false;
        lv_label_set_text(g_dev_cam_btn_label, LV_SYMBOL_PLAY "  Start Camera");
        lv_obj_add_flag(g_dev_cam_canvas, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(g_dev_no_cam_label, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(g_dev_no_cam_label, "Press Start Camera");
    }
}

static void _create_dev_screen(void)
{
    lv_obj_t *scr = lv_obj_create(NULL);
    lv_obj_set_size(scr, CURA_DISPLAY_W, CURA_DISPLAY_H);
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x0D1117), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, LV_PART_MAIN);

    /* ── Dev header ── */
    lv_obj_t *header = lv_obj_create(scr);
    lv_obj_set_size(header, CURA_DISPLAY_W, 56);
    lv_obj_align(header, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(header, lv_color_hex(0x1E293B), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(header, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(header, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(header, 0, LV_PART_MAIN);

    lv_obj_t *btn_back = lv_button_create(header);
    lv_obj_set_size(btn_back, 48, 34);
    lv_obj_align(btn_back, LV_ALIGN_LEFT_MID, 8, 0);
    lv_obj_set_style_bg_color(btn_back, lv_color_hex(0x374151), LV_PART_MAIN);
    lv_obj_set_style_radius(btn_back, 6, LV_PART_MAIN);
    lv_obj_t *back_lbl = lv_label_create(btn_back);
    lv_label_set_text(back_lbl, LV_SYMBOL_LEFT);
    lv_obj_set_style_text_color(back_lbl, lv_color_white(), LV_PART_MAIN);
    lv_obj_center(back_lbl);
    lv_obj_add_event_cb(btn_back, _cb_back, LV_EVENT_CLICKED, NULL);

    lv_obj_t *title = lv_label_create(header);
    lv_label_set_text(title, "DEV MODE");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_set_style_text_color(title, lv_color_white(), LV_PART_MAIN);
    lv_obj_center(title);

    /* ── Info panel ── */
    lv_obj_t *info = lv_obj_create(scr);
    lv_obj_set_size(info, CURA_DISPLAY_W, 110);
    lv_obj_align(info, LV_ALIGN_TOP_MID, 0, 56);
    lv_obj_set_style_bg_color(info, lv_color_hex(0x111827), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(info, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(info, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(info, 12, LV_PART_MAIN);
    lv_obj_set_style_radius(info, 0, LV_PART_MAIN);

    /* WiFi SSID — compile-time from cura_config.h */
    lv_obj_t *lbl_ssid = lv_label_create(info);
    lv_label_set_text(lbl_ssid, "WiFi:   " CURA_WIFI_SSID);
    lv_obj_set_style_text_font(lbl_ssid, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(lbl_ssid, lv_color_hex(0xD1D5DB), LV_PART_MAIN);
    lv_obj_align(lbl_ssid, LV_ALIGN_TOP_LEFT, 0, 0);

    /* IP address — read at screen creation time */
    NW_IP_S ip = {0};
    tkl_wifi_get_ip(WF_STATION, &ip);
    char ip_buf[48];
    snprintf(ip_buf, sizeof(ip_buf), "IP:     %s",
             ((char *)ip.ip)[0] ? (char *)ip.ip : "---");
    lv_obj_t *lbl_ip = lv_label_create(info);
    lv_label_set_text(lbl_ip, ip_buf);
    lv_obj_set_style_text_font(lbl_ip, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(lbl_ip, lv_color_hex(0xD1D5DB), LV_PART_MAIN);
    lv_obj_align(lbl_ip, LV_ALIGN_TOP_LEFT, 0, 30);

    /* Server address */
    char srv_buf[64];
    snprintf(srv_buf, sizeof(srv_buf), "Server: %s:%d",
             CURA_SERVER_HOST, CURA_SERVER_PORT);
    lv_obj_t *lbl_srv = lv_label_create(info);
    lv_label_set_text(lbl_srv, srv_buf);
    lv_obj_set_style_text_font(lbl_srv, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(lbl_srv, lv_color_hex(0x6B7280), LV_PART_MAIN);
    lv_obj_align(lbl_srv, LV_ALIGN_TOP_LEFT, 0, 60);

    /* ── Camera toggle button ── */
    lv_obj_t *btn_cam = lv_button_create(scr);
    lv_obj_set_size(btn_cam, CURA_DISPLAY_W - 32, 48);
    lv_obj_align(btn_cam, LV_ALIGN_TOP_MID, 0, 178);
    lv_obj_set_style_bg_color(btn_cam, lv_color_hex(0x1D4ED8), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(btn_cam, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(btn_cam, 10, LV_PART_MAIN);
    lv_obj_add_event_cb(btn_cam, _cb_camera, LV_EVENT_CLICKED, NULL);

    g_dev_cam_btn_label = lv_label_create(btn_cam);
    lv_label_set_text(g_dev_cam_btn_label, LV_SYMBOL_PLAY "  Start Camera");
    lv_obj_set_style_text_font(g_dev_cam_btn_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(g_dev_cam_btn_label, lv_color_white(), LV_PART_MAIN);
    lv_obj_center(g_dev_cam_btn_label);

    /* ── Camera preview area ── */
    lv_obj_t *cam_bg = lv_obj_create(scr);
    lv_obj_set_size(cam_bg, CURA_DISPLAY_W, 246);
    lv_obj_align(cam_bg, LV_ALIGN_TOP_MID, 0, 234);
    lv_obj_set_style_bg_color(cam_bg, lv_color_hex(0x1F2937), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(cam_bg, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(cam_bg, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(cam_bg, 0, LV_PART_MAIN);

    g_dev_no_cam_label = lv_label_create(cam_bg);
    lv_label_set_text(g_dev_no_cam_label, "Press Start Camera");
    lv_obj_set_style_text_font(g_dev_no_cam_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(g_dev_no_cam_label, lv_color_hex(0x4B5563), LV_PART_MAIN);
    lv_obj_center(g_dev_no_cam_label);

    /* Canvas hidden until cura_camera_start() sets a buffer and unhides it */
    g_dev_cam_canvas = lv_canvas_create(scr);
    lv_obj_set_size(g_dev_cam_canvas, 320, 240);
    lv_obj_align(g_dev_cam_canvas, LV_ALIGN_TOP_LEFT, 0, 237);
    lv_obj_add_flag(g_dev_cam_canvas, LV_OBJ_FLAG_HIDDEN);

    g_dev_screen = scr;
    lv_screen_load(scr);
}

static void _cb_dev(lv_event_t *e)
{
    (void)e;
    /* Clean up any previous dev screen before creating a fresh one.
       The old screen is not the active screen at this point (we are on
       the main screen), so deleting it is safe. */
    if (g_dev_screen) {
        lv_obj_delete(g_dev_screen);
        g_dev_screen = NULL;
    }
    g_dev_cam_running = false;
    _create_dev_screen();
}

/* ---------------------------------------------------------------------- */
/* Public API                                                               */
/* ---------------------------------------------------------------------- */

void cura_ui_init(void)
{
    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x111827), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, LV_PART_MAIN);

    /* ── Header ── */
    lv_obj_t *header = lv_obj_create(scr);
    lv_obj_set_size(header, CURA_DISPLAY_W, 56);
    lv_obj_align(header, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(header, lv_color_hex(0x1E40AF), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(header, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(header, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(header, 0, LV_PART_MAIN);

    lv_obj_t *title = lv_label_create(header);
    lv_label_set_text(title, "CURA");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_set_style_text_color(title, lv_color_white(), LV_PART_MAIN);
    lv_obj_center(title);

    /* DEV button — top-right of header */
    lv_obj_t *btn_dev = lv_button_create(header);
    lv_obj_set_size(btn_dev, 56, 32);
    lv_obj_align(btn_dev, LV_ALIGN_RIGHT_MID, -8, 0);
    lv_obj_set_style_bg_color(btn_dev, lv_color_hex(0x374151), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(btn_dev, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(btn_dev, 6, LV_PART_MAIN);
    lv_obj_t *dev_lbl = lv_label_create(btn_dev);
    lv_label_set_text(dev_lbl, "DEV");
    lv_obj_set_style_text_font(dev_lbl, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(dev_lbl, lv_color_white(), LV_PART_MAIN);
    lv_obj_center(dev_lbl);
    lv_obj_add_event_cb(btn_dev, _cb_dev, LV_EVENT_CLICKED, NULL);

    /* ── State label (centre panel) ── */
    lv_obj_t *state_panel = lv_obj_create(scr);
    lv_obj_set_size(state_panel, CURA_DISPLAY_W, 140);
    lv_obj_align(state_panel, LV_ALIGN_TOP_MID, 0, 56);
    lv_obj_set_style_bg_color(state_panel, lv_color_hex(0x111827), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(state_panel, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(state_panel, 0, LV_PART_MAIN);

    g_label_state = lv_label_create(state_panel);
    lv_label_set_text(g_label_state, "Connecting...");
    lv_label_set_long_mode(g_label_state, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(g_label_state, CURA_DISPLAY_W - 20);
    lv_obj_set_style_text_font(g_label_state, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_set_style_text_color(g_label_state, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_text_align(g_label_state, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_center(g_label_state);

    /* ── START button (green) ── */
    lv_obj_t *btn_start = _make_button(
        scr, LV_SYMBOL_PLAY "  START",
        lv_color_hex(0x16A34A),
        _cb_start
    );
    lv_obj_align(btn_start, LV_ALIGN_TOP_MID, 0, 210);

    /* ── EMERGENCY STOP button (red) ── */
    lv_obj_t *btn_stop = _make_button(
        scr, LV_SYMBOL_STOP "  EMERGENCY STOP",
        lv_color_hex(0xDC2626),
        _cb_estop
    );
    lv_obj_align(btn_stop, LV_ALIGN_TOP_MID, 0, 316);

    /* ── Status bar (bottom) ── */
    lv_obj_t *status_bar = lv_obj_create(scr);
    lv_obj_set_size(status_bar, CURA_DISPLAY_W, 54);
    lv_obj_align(status_bar, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_color(status_bar, lv_color_hex(0x1F2937), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(status_bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(status_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(status_bar, 0, LV_PART_MAIN);

    g_label_status = lv_label_create(status_bar);
    lv_label_set_text(g_label_status, "Polling server...");
    lv_obj_set_style_text_font(g_label_status, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(g_label_status, lv_color_hex(0x9CA3AF), LV_PART_MAIN);
    lv_obj_center(g_label_status);

    g_main_screen = lv_screen_active();
}

void cura_ui_set_label(const char *label)
{
    strncpy(g_pending_label, label, sizeof(g_pending_label) - 1);
    g_pending_label[sizeof(g_pending_label) - 1] = '\0';
    g_label_dirty = true;
    lv_async_call(_apply_pending_label, NULL);
}
