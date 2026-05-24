/*
 * main.c — Cura T5AI firmware entry point.
 *
 * TuyaOpen dual-core T5AI entry chain:
 *   tuya_app_main()  ← called by AP core (bk7258_ap) — spawns a thread
 *   └─ user_main()   ← runs our app: TAL init, LVGL, Wi-Fi, poll task
 *
 * Startup sequence:
 *   1. Logging init
 *   2. TAL subsystems: kv, sw_timer, workq  (required by netmgr)
 *   3. Board hardware + LVGL display init
 *   4. lv_vendor_start() — LVGL event loop in its own thread
 *   5. Wi-Fi connect via event subscription (blocks until IP obtained)
 *   6. Spawn poll_task — loops every CURA_POLL_INTERVAL_MS
 */

#include <string.h>
#include "tuya_cloud_types.h"
#include "tal_api.h"
#include "tkl_output.h"
#include "netmgr.h"
#include "netconn_wifi.h"
#include "lvgl.h"
#include "lv_vendor.h"
#include "board_com_api.h"

#include "cura_config.h"
#include "cura_http.h"
#include "cura_stream.h"
#include "cura_ui.h"
#include "tdl_camera_manage.h"

/* ---------------------------------------------------------------------- */
/* Poll task                                                                */
/* ---------------------------------------------------------------------- */

static void _poll_task(void *arg)
{
    (void)arg;
    char label[CURA_LABEL_MAX];

    for (;;) {
        bool ok = cura_http_get_state_label(
            CURA_SERVER_HOST, CURA_SERVER_PORT,
            label, sizeof(label)
        );
        cura_ui_set_label(ok ? label : "No Server");
        tal_system_sleep(CURA_POLL_INTERVAL_MS);
    }
}

/* ---------------------------------------------------------------------- */
/* Wi-Fi — event-based, blocks until IP obtained                           */
/* ---------------------------------------------------------------------- */

static SEM_HANDLE g_wifi_sem = NULL;

static OPERATE_RET _wifi_status_cb(void *data)
{
    netmgr_status_e status = *((netmgr_status_e *)data);
    if (status == NETMGR_LINK_UP && g_wifi_sem != NULL) {
        tal_semaphore_post(g_wifi_sem);
    }
    return OPRT_OK;
}

static void _wifi_connect(void)
{
    PR_DEBUG("Cura: connecting to Wi-Fi '%s'", CURA_WIFI_SSID);

    tal_semaphore_create_init(&g_wifi_sem, 0, 1);
    tal_event_subscribe(EVENT_LINK_STATUS_CHG, "cura_wifi",
                        _wifi_status_cb, SUBSCRIBE_TYPE_NORMAL);

    netmgr_type_e type = NETCONN_WIFI;
    netmgr_init(type);

    netconn_wifi_info_t wifi_info = {0};
    strncpy(wifi_info.ssid, CURA_WIFI_SSID,     sizeof(wifi_info.ssid) - 1);
    strncpy(wifi_info.pswd, CURA_WIFI_PASSWORD, sizeof(wifi_info.pswd) - 1);
    netmgr_conn_set(NETCONN_WIFI, NETCONN_CMD_SSID_PSWD, &wifi_info);

    /* Block here — _wifi_status_cb posts when NETMGR_LINK_UP fires */
    tal_semaphore_wait_forever(g_wifi_sem);
    tal_semaphore_release(g_wifi_sem);
    g_wifi_sem = NULL;

    PR_DEBUG("Cura: Wi-Fi connected");
}

/* ---------------------------------------------------------------------- */
/* Entry point                                                              */
/* ---------------------------------------------------------------------- */

static void user_main(void);   /* forward declaration */

/* ---------------------------------------------------------------------- */
/* TuyaOpen AP-core entry — wraps user_main in a task thread               */
/* ---------------------------------------------------------------------- */

static THREAD_HANDLE g_app_thread = NULL;

static void _app_thread(void *arg)
{
    (void)arg;
    user_main();
    tal_thread_delete(g_app_thread);
    g_app_thread = NULL;
}

void tuya_app_main(void)
{
    THREAD_CFG_T tp = {0};
    tp.stackDepth = 1024 * 16;   /* 16 KB — LVGL + HTTP + netmgr need headroom */
    tp.priority   = THREAD_PRIO_1;
    tp.thrdname   = "cura_main";
    tal_thread_create_and_start(&g_app_thread, NULL, NULL, _app_thread, NULL, &tp);
}

/* ---------------------------------------------------------------------- */
/* Application logic                                                        */
/* ---------------------------------------------------------------------- */

static void user_main(void)
{
    /* 1. Logging */
    tal_log_init(TAL_LOG_LEVEL_DEBUG, 4096, (TAL_LOG_OUTPUT_CB)tkl_log_output);
    PR_NOTICE("Cura T5AI firmware starting");

    /* 2. TAL subsystems — netmgr requires these before netmgr_init() */
    tal_kv_init(&(tal_kv_cfg_t){
        .seed = "vmlkasdh93dlvlcy",
        .key  = "dflfuap134ddlduq",
    });
    tal_sw_timer_init();
    tal_workq_init();

    /* 3. Board hardware + LVGL — must happen before any cura_ui_* calls */
    PR_DEBUG("Cura: board_register_hardware start");
    board_register_hardware();
    PR_DEBUG("Cura: board_register_hardware done");

    /* Camera probe: runs right after hardware init — visible in boot log */
    {
        TDL_CAMERA_HANDLE_T cam = tdl_camera_find_dev("camera");
        if (cam) {
            PR_NOTICE("CAMERA PROBE: found 'camera' handle=%p", cam);
        } else {
            PR_ERR("CAMERA PROBE: 'camera' NOT in device list after board_register_hardware");
        }
    }

    PR_DEBUG("Cura: lv_vendor_init start");
    lv_vendor_init(DISPLAY_NAME);
    PR_DEBUG("Cura: lv_vendor_init done");

    lv_vendor_disp_lock();
    cura_ui_init();
    lv_vendor_disp_unlock();
    PR_DEBUG("Cura: cura_ui_init done");

    /* 4. Start LVGL task (non-blocking — spawns its own thread) */
    lv_vendor_start(5, 1024 * 8);
    PR_DEBUG("Cura: lv_vendor_start done");

    /* 5. Now safe to update UI — LVGL thread is running */
    cura_ui_set_label("Connecting...");

    /* 6. Wi-Fi (blocks until IP obtained) */
    _wifi_connect();

    /* 7. MJPEG stream server (needs IP — start after WiFi up) */
    cura_stream_start();

    /* 8. Poll task */
    THREAD_HANDLE poll_thread = NULL;
    THREAD_CFG_T cfg = {0};
    cfg.priority   = THREAD_PRIO_2;
    cfg.stackDepth = 4096;
    cfg.thrdname   = "cura_poll";
    tal_thread_create_and_start(&poll_thread, NULL, NULL, _poll_task, NULL, &cfg);
    /* user_main returns; poll_thread and LVGL thread keep running */
}
