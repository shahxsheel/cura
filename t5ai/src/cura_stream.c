#include "cura_stream.h"
#include "cura_config.h"
#include "tal_api.h"
#include "tal_network.h"
#include "tal_mutex.h"
#include "tal_semaphore.h"
#include <string.h>
#include <stdio.h>

#define FRAME_BUF_SIZE  (35 * 1024)   /* max JPEG bytes per frame */
#define FRAME_TIMEOUT_MS 3000

static uint8_t       s_jpeg[FRAME_BUF_SIZE];
static uint32_t      s_jpeg_len = 0;
static SEM_HANDLE    s_sem    = NULL;
static MUTEX_HANDLE  s_mtx    = NULL;
static THREAD_HANDLE s_thread = NULL;

void cura_stream_push_jpeg(const uint8_t *data, uint32_t len)
{
    if (!data || len == 0 || len > FRAME_BUF_SIZE || !s_mtx || !s_sem) return;
    tal_mutex_lock(s_mtx);
    memcpy(s_jpeg, data, len);
    s_jpeg_len = len;
    tal_mutex_unlock(s_mtx);
    tal_semaphore_post(s_sem);  /* ignores if already at max — drops oldest, keeps newest */
}

static bool _send_all(int fd, const void *buf, int len)
{
    const char *p = (const char *)buf;
    while (len > 0) {
        int n = tal_net_send(fd, p, (uint32_t)len);
        if (n <= 0) return false;
        p += n; len -= n;
    }
    return true;
}

static void _serve(int client_fd)
{
    static const char http_hdr[] =
        "HTTP/1.0 200 OK\r\n"
        "Content-Type: multipart/x-mixed-replace; boundary=--FRAME\r\n"
        "Cache-Control: no-cache\r\n"
        "\r\n";
    if (!_send_all(client_fd, http_hdr, (int)(sizeof(http_hdr) - 1))) return;

    uint8_t *tmp = (uint8_t *)tal_malloc(FRAME_BUF_SIZE);
    if (!tmp) {
        PR_ERR("cura_stream: _serve malloc failed");
        return;
    }

    char part_hdr[128];
    for (;;) {
        /* Wait for a new JPEG frame; timeout keeps the loop alive */
        if (tal_semaphore_wait(s_sem, FRAME_TIMEOUT_MS) != OPRT_OK) continue;

        uint32_t len;
        tal_mutex_lock(s_mtx);
        len = s_jpeg_len;
        if (len) memcpy(tmp, s_jpeg, len);
        tal_mutex_unlock(s_mtx);
        if (!len) continue;

        int ph_len = snprintf(part_hdr, sizeof(part_hdr),
            "--FRAME\r\n"
            "Content-Type: image/jpeg\r\n"
            "Content-Length: %u\r\n"
            "\r\n",
            (unsigned)len);

        if (!_send_all(client_fd, part_hdr, ph_len)) break;
        if (!_send_all(client_fd, tmp, (int)len))    break;
        if (!_send_all(client_fd, "\r\n", 2))        break;
    }

    tal_free(tmp);
}

static void _server_task(void *arg)
{
    (void)arg;

    tal_semaphore_create_init(&s_sem, 0, 1);
    tal_mutex_create_init(&s_mtx);

    int srv = tal_net_socket_create(PROTOCOL_TCP);
    if (srv < 0) {
        PR_ERR("cura_stream: socket_create failed %d", srv);
        return;
    }

    if (tal_net_bind(srv, TY_IPADDR_ANY, CURA_STREAM_PORT) < 0) {
        PR_ERR("cura_stream: bind failed");
        tal_net_close(srv);
        return;
    }

    tal_net_listen(srv, 1);
    PR_NOTICE("cura_stream: MJPEG server ready — http://<board-ip>:%d/", CURA_STREAM_PORT);

    for (;;) {
        TUYA_IP_ADDR_T cli_addr = 0;
        uint16_t       cli_port = 0;
        int client = tal_net_accept(srv, &cli_addr, &cli_port);
        if (client < 0) {
            tal_system_sleep(100);
            continue;
        }
        PR_DEBUG("cura_stream: client connected");
        tal_net_set_timeout(client, 5000, TRANS_SEND);
        _serve(client);
        tal_net_close(client);
        PR_DEBUG("cura_stream: client disconnected");
    }
}

void cura_stream_start(void)
{
    if (s_thread) return;
    THREAD_CFG_T cfg = {0};
    cfg.priority   = THREAD_PRIO_3;
    cfg.stackDepth = 6 * 1024;
    cfg.thrdname   = "cura_stream";
    tal_thread_create_and_start(&s_thread, NULL, NULL, _server_task, NULL, &cfg);
}
