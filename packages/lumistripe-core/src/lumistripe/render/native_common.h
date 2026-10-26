#ifndef LUMISTRIPE_RENDER_NATIVE_COMMON_H
#define LUMISTRIPE_RENDER_NATIVE_COMMON_H
#define PY_ARRAY_UNIQUE_SYMBOL LUMISTRIPE_RENDER_ARRAY_API
#ifndef LUMISTRIPE_RENDER_IMPORT_NUMPY
#define NO_IMPORT_ARRAY
#endif
#include <Python.h>
#include <numpy/arrayobject.h>
#include <math.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
int validate_rgba(PyArrayObject *array, const char *name, int writable);
int arrays_overlap(const PyArrayObject *left, const PyArrayObject *right);
int reject_partial_overlap(const PyArrayObject *left,
                                  const PyArrayObject *right,
                                  const char *left_name,
                                  const char *right_name);
PyObject *correct_rgba_into(PyObject *module, PyObject *args);
PyObject *apply_alpha_scale_into(PyObject *module, PyObject *args);
PyObject *render_solid_rgba_into(PyObject *module, PyObject *args);
PyObject *render_hsl_solid_into(PyObject *module, PyObject *args);
PyObject *render_halves_rgba_into(PyObject *module, PyObject *args);
PyObject *render_red_sweep_into(PyObject *module, PyObject *args);
PyObject *render_red_chase_into(PyObject *module, PyObject *args);
PyObject *render_drop_explosion_into(PyObject *module, PyObject *args);
PyObject *render_fire_into(PyObject *module, PyObject *args);
PyObject *render_color_wipe_into(PyObject *module, PyObject *args);
PyObject *composite_screen_rgba_into(PyObject *module, PyObject *args);
int validate_fixed_array(PyArrayObject *array, int type, int dimensions,
                                npy_intp first, npy_intp second,
                                const char *name);
int validate_array(PyArrayObject *array, int type, int dimensions,
                   const npy_intp *shape, int writable, double minimum,
                   double maximum, const char *name);
int validate_int16_array_range(PyArrayObject *array, int dimensions,
                               const npy_intp *shape, int writable,
                               int16_t minimum, int16_t maximum,
                               const char *name);
int validate_array_layout(PyArrayObject *array, int type, int dimensions,
                          const npy_intp *shape, int writable,
                          const char *name);
int validate_finite_prefix(PyArrayObject *array, npy_intp elements,
                           const char *name);
int reject_any_overlap(const PyArrayObject *left, const PyArrayObject *right,
                       const char *left_name, const char *right_name);
PyObject *render_tunnel_rings_into(PyObject *module, PyObject *args);
void hsl_to_rgb(double hue, double saturation, double lightness,
                       uint8_t *red, uint8_t *green, uint8_t *blue);
void map_palette(uint8_t *red, uint8_t *green, uint8_t *blue, int palette);
void palette_hsl_to_rgb(double hue, double saturation, double lightness,
                               int palette, unsigned int custom_rgb,
                               uint8_t *red, uint8_t *green, uint8_t *blue);
void catalog_palette_hsl_to_rgb(double hue, double saturation,
                                       double lightness, int palette,
                                       unsigned int custom_rgb, uint8_t *red,
                                       uint8_t *green, uint8_t *blue);
PyObject *render_rainbow_pattern_into(PyObject *module, PyObject *args);
PyObject *render_theater_chase_into(PyObject *module, PyObject *args);
PyObject *render_laser_sweep_into(PyObject *module, PyObject *args);
PyObject *render_sinelon_into(PyObject *module, PyObject *args);
PyObject *render_peak_mirror_into(PyObject *module, PyObject *args);
PyObject *render_bouncing_ball_into(PyObject *module, PyObject *args);
PyObject *render_wave_into(PyObject *module, PyObject *args);
PyObject *render_juggle_into(PyObject *module, PyObject *args);
PyObject *render_confetti_into(PyObject *module, PyObject *args);
PyObject *render_glow_rush_into(PyObject *module, PyObject *args);
PyObject *render_dual_comet_into(PyObject *module, PyObject *args);
PyObject *render_shockwave_into(PyObject *module, PyObject *args);
PyObject *render_aurora_into(PyObject *module, PyObject *args);
PyObject *render_comet_storm_into(PyObject *module, PyObject *args);
PyObject *render_twinkle_into(PyObject *module, PyObject *args);
PyObject *render_spectrum_flash_into(PyObject *module, PyObject *args);
PyObject *render_pulse_into(PyObject *module, PyObject *args);
PyObject *render_comet_into(PyObject *module, PyObject *args);
PyObject *render_dance_floor_into(PyObject *module, PyObject *args);
PyObject *render_disco_sparkle_into(PyObject *module, PyObject *args);
PyObject *render_club_flash_into(PyObject *module, PyObject *args);
PyObject *render_beat_wave_into(PyObject *module, PyObject *args);
PyObject *render_beat_explosion_into(PyObject *module, PyObject *args);
void write_transparent_palette_pixel(uint8_t *pixels, size_t offset,
                                            int palette,
                                            unsigned int custom_rgb);
PyObject *render_firework_burst_into(PyObject *module, PyObject *args);
PyObject *render_color_burst_into(PyObject *module, PyObject *args);
PyObject *render_plasma_rave_into(PyObject *module, PyObject *args);
PyObject *render_beat_ripple_into(PyObject *module, PyObject *args);
PyObject *render_neon_confetti_into(PyObject *module, PyObject *args);
PyObject *render_strobe_chase_into(PyObject *module, PyObject *args);
PyObject *render_rave_scanner_into(PyObject *module, PyObject *args);
PyObject *render_center_burst_into(PyObject *module, PyObject *args);
PyObject *render_dual_laser_into(PyObject *module, PyObject *args);
PyObject *render_mirror_flash_into(PyObject *module, PyObject *args);
PyObject *render_drop_wave_into(PyObject *module, PyObject *args);
PyObject *render_disco_comet_into(PyObject *module, PyObject *args);
PyObject *render_pixel_explosion_into(PyObject *module, PyObject *args);
PyObject *render_lightning_strike_into(PyObject *module, PyObject *args);
PyObject *render_neon_storm_into(PyObject *module, PyObject *args);
PyObject *render_electric_storm_into(PyObject *module, PyObject *args);
PyMethodDef *native_composite_methods(void);
PyMethodDef *native_patterns_methods(void);
PyMethodDef *native_particles_methods(void);
PyMethodDef *native_reactive_methods(void);
#endif
