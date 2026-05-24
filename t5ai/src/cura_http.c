/*
 * cura_http.c — HTTP polling and command posting for the Cura T5AI firmware.
 *
 * Uses TuyaOpen's http_client_interface (same API as the http_client example).
 */

#include <stdio.h>
#include <string.h>

#include "tuya_cloud_types.h"
#include "http_client_interface.h"
#include "tal_api.h"

#include "cura_config.h"
#include "cura_http.h"

/* Inline JSON key extraction — avoids pulling in cJSON for a simple field */
static bool _extract_string_field(const char *json, const char *key, char *out, size_t out_max)
{
    /* Find  "key":"value"  in the JSON blob */
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char *p = strstr(json, search);
    if (!p) return false;
    p += strlen(search);
    while (*p == ' ') p++;          /* skip whitespace */
    if (*p != '"') return false;
    p++;                            /* skip opening quote */
    size_t i = 0;
    while (*p && *p != '"' && i < out_max - 1) out[i++] = *p++;
    out[i] = '\0';
    return (i > 0);
}

bool cura_http_get_state_label(const char *host, uint16_t port,
                                char *out_label, size_t label_max)
{
    http_client_response_t resp = {0};
    http_client_header_t hdrs[] = {
        {.key = "Accept", .value = "application/json"},
    };

    http_client_status_t rc = http_client_request(
        &(const http_client_request_t){
            .host          = host,
            .port          = port,
            .path          = "/status",
            .method        = "GET",
            .headers       = hdrs,
            .headers_count = 1,
            .body          = (const uint8_t *)"",
            .body_length   = 0,
            .timeout_ms    = CURA_HTTP_TIMEOUT_MS,
        },
        &resp
    );

    if (rc != HTTP_CLIENT_SUCCESS || resp.body == NULL) {
        http_client_free(&resp);
        return false;
    }

    bool ok = _extract_string_field((char *)resp.body, "state_label", out_label, label_max);
    http_client_free(&resp);
    return ok;
}

bool cura_http_post_command(const char *host, uint16_t port, const char *action)
{
    char body[128];
    snprintf(body, sizeof(body),
             "{\"action\":\"%s\",\"source\":\"t5ai\"}", action);

    http_client_header_t hdrs[] = {
        {.key = "Content-Type", .value = "application/json"},
    };

    http_client_response_t resp = {0};
    http_client_status_t rc = http_client_request(
        &(const http_client_request_t){
            .host          = host,
            .port          = port,
            .path          = "/command",
            .method        = "POST",
            .headers       = hdrs,
            .headers_count = 1,
            .body          = (const uint8_t *)body,
            .body_length   = strlen(body),
            .timeout_ms    = CURA_HTTP_TIMEOUT_MS,
        },
        &resp
    );

    http_client_free(&resp);
    return (rc == HTTP_CLIENT_SUCCESS);
}
