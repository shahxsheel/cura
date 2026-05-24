#pragma once

#include <stdint.h>

/*
 * cura_stream.h — MJPEG HTTP server for camera preview over WiFi.
 *
 * Usage:
 *   1. Call cura_stream_start() once after WiFi is connected.
 *   2. Call cura_stream_push_jpeg() from the camera JPEG frame callback.
 *   3. Open http://<board-ip>:8081/ in a browser or VLC.
 */

/* Spawn the TCP server thread (port from CURA_STREAM_PORT in cura_config.h). */
void cura_stream_start(void);

/* Push a hardware-encoded JPEG frame to any connected client.
   Called from the camera frame callback — must be fast (no blocking I/O). */
void cura_stream_push_jpeg(const uint8_t *data, uint32_t len);
