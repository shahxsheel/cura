#pragma once

#include "lvgl.h"
#include <stdbool.h>

/*
 * cura_camera.h — camera preview for the DEV mode screen.
 *
 * Usage:
 *   1. Create an lv_canvas widget and pass it to cura_camera_start().
 *   2. The canvas buffer is allocated internally from PSRAM.
 *   3. Call cura_camera_stop() before deleting the canvas or leaving the screen.
 */

/*
 * Open the GC2145 camera and stream YUV422 frames into canvas as RGB565.
 * canvas: lv_canvas object to draw into (must already be added to a screen).
 * w, h:   output resolution (e.g. 320, 240).
 * Returns true on success, false if the camera device is not found or fails to open.
 */
bool cura_camera_start(lv_obj_t *canvas, int w, int h);

/* Stop streaming and free the frame buffer. Safe to call if not started. */
void cura_camera_stop(void);

/* Returns a short string describing why the last cura_camera_start() failed.
   Valid until the next call to cura_camera_start(). Never NULL. */
const char *cura_camera_last_err(void);
