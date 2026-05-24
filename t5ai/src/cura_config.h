#pragma once

/*
 * cura_config.h — edit these values before flashing the T5AI board.
 *
 * Both the laptop and T5AI must be on the same Wi-Fi network.
 * Find the laptop IP with:  ifconfig | grep "inet " | grep -v 127
 */

/* Wi-Fi credentials */
#define CURA_WIFI_SSID      "Verizon-RC400L-90"
#define CURA_WIFI_PASSWORD  "3cd7e2cd"

/* Laptop FastAPI server */
#define CURA_SERVER_HOST    "192.168.1.110"
#define CURA_SERVER_PORT    8000

/* Polling interval in milliseconds */
#define CURA_POLL_INTERVAL_MS  500

/* HTTP request timeout in milliseconds */
#define CURA_HTTP_TIMEOUT_MS   3000

/* Display dimensions (T5AI 3.5" portrait) */
#define CURA_DISPLAY_W  320
#define CURA_DISPLAY_H  480

/* MJPEG HTTP stream port (http://<board-ip>:8081/) */
#define CURA_STREAM_PORT    8081
