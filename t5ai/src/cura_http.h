#pragma once
#include <stdbool.h>

/*
 * cura_http.h — HTTP helpers for polling the Cura laptop server.
 *
 * All functions are blocking with a timeout defined in cura_config.h.
 */

/* Maximum length of the state_label string from the server */
#define CURA_LABEL_MAX 64

/*
 * cura_http_get_state_label
 *
 * Sends GET http://<host>:<port>/status, parses "state_label" from the JSON
 * response, and copies at most (CURA_LABEL_MAX-1) bytes into out_label.
 *
 * Returns true on success, false on network or parse error.
 */
bool cura_http_get_state_label(
    const char *host,
    uint16_t    port,
    char       *out_label,
    size_t      label_max
);

/*
 * cura_http_post_command
 *
 * Sends POST http://<host>:<port>/command with body:
 *   {"action": "<action>", "source": "t5ai"}
 *
 * Returns true if the server responded with HTTP 200.
 */
bool cura_http_post_command(
    const char *host,
    uint16_t    port,
    const char *action
);
