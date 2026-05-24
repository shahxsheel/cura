#pragma once

/*
 * cura_ui.h — LVGL v9 screen layout for the Cura T5AI patient UI.
 *
 * Call cura_ui_init() once after lv_init().
 * Call cura_ui_set_label() from the poll thread to update the display.
 * Button callbacks are registered internally and POST to the laptop server.
 */

/* Initialise all LVGL widgets.  Must be called on the LVGL task thread. */
void cura_ui_init(void);

/*
 * Update the state label shown in the centre of the screen.
 * Safe to call from any thread — internally schedules an lv_async_call.
 * label: null-terminated string, e.g. "Ready", "Enjoy!", "Error — Please Help"
 */
void cura_ui_set_label(const char *label);
