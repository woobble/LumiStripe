#define _GNU_SOURCE
#include <Python.h>
#include <numpy/arrayobject.h>
#include <string.h>
#include <math.h>
#include <stdlib.h>
#include "kiss_fftr.h"

#define FFT_SIZE 1024
#define FFT_HOP_SIZE (FFT_SIZE / 2)
#define NUM_BANDS 8
#define RMS_HISTORY_SIZE 200
#define ONSET_HISTORY_SIZE 512
#define MAX_IOI_BUFFER 12
#define WINDOW_SCALE (FFT_SIZE / 2.0f)
#define ONSET_THRESHOLD 0.12f
#define BPM_INTERVAL_MIN 0.2
#define BPM_INTERVAL_MAX 2.0

typedef struct {
    float noise_floor;
    float rms_attack, rms_release;
    float band_attack, band_release, beat_release;
    int smoothing_enabled;
    float target_level, min_gain, max_gain;
    float adapt_attack, adapt_release;
    float music_threshold, music_max_gain, silence_floor;
    int normalization_enabled, dc_block_enabled;
    float sample_rate;

    float prev_bass_energy;
    float smoothed_rms;
    float smoothed_bands[NUM_BANDS];
    float beat_envelope;
    float level_estimate;
    float normalization_gain;
    int feed_count;
    long long samples_seen;
    float sample_sum;

    float buffer[FFT_SIZE];
    int buffer_pos;

    float rms_history[RMS_HISTORY_SIZE];
    int rms_idx;
    float onset_history[ONSET_HISTORY_SIZE];
    int onset_idx;
    float prev_rms;
    float prev_feature_bands[NUM_BANDS];
    float prev_onset;
    float smooth_onset;
    int last_onset_frame;
    float ioi_buffer[MAX_IOI_BUFFER];
    int ioi_buffer_len;
    float bpm;
    float brightness;
    float dynamic_range;
    int fft_call_count;

    float window[FFT_SIZE];
    float frequencies[FFT_SIZE / 2];
    int band_slices[NUM_BANDS][2];

    float frame_rms;
    float frame_bands[NUM_BANDS];
    int frame_beat;
    float frame_beat_strength;

    float features_bpm;
    float features_energy;
    float features_bass;
    float features_brightness;
    float features_onset_strength;
    float features_dynamic_range;
    int features_beat;
    float features_beat_strength;
    float features_bands[NUM_BANDS];
    float features_bass_energy;
    float features_mid_energy;
    float features_treble_energy;
    float features_spectral_centroid;
    float features_spectral_flux;
    float features_beat_confidence;
    float features_rolling_loudness;
    int features_silence;
    int features_drop_detected;
    int features_section_change;
    int silence_frames;
    int drop_cooldown_frames;
    int section_cooldown_frames;
    float prev_drop_bass;
    float section_energy;
    float section_mid;
    float section_treble;
    float section_brightness;
} AudioProcessorState;

typedef struct {
    PyObject_HEAD
    AudioProcessorState s;
    kiss_fftr_cfg fft_cfg;
} AudioProcessor;

static int _cmp_float(const void *a, const void *b) {
    float fa = *(const float*)a;
    float fb = *(const float*)b;
    return (fa > fb) - (fa < fb);
}

static float _smooth(float current, float target, float attack, float release) {
    float factor = (target >= current) ? attack : release;
    if (factor < 0.0f) factor = 0.0f;
    if (factor > 1.0f) factor = 1.0f;
    return current + (target - current) * factor;
}

static float _clamp01f(float value) {
    if (value < 0.0f) return 0.0f;
    if (value > 1.0f) return 1.0f;
    return value;
}

static float _apply_noise_floor(float value, float floor) {
    float clamped = floor;
    if (clamped < 0.0f) clamped = 0.0f;
    if (clamped > 0.99f) clamped = 0.99f;
    if (value <= clamped) return 0.0f;
    float scaled = (value - clamped) / (1.0f - clamped);
    if (scaled < 0.0f) scaled = 0.0f;
    if (scaled > 1.0f) scaled = 1.0f;
    return scaled;
}

static float _median(float *arr, int n) {
    if (n <= 0) return 0.0f;
    float *tmp = malloc(n * sizeof(float));
    if (!tmp) return 0.0f;
    memcpy(tmp, arr, n * sizeof(float));
    qsort(tmp, n, sizeof(float), _cmp_float);
    float result = tmp[n / 2];
    free(tmp);
    return result;
}

static float _percentile(float *arr, int n, float p) {
    if (n <= 0) return 0.0f;
    float *tmp = malloc(n * sizeof(float));
    if (!tmp) return 0.0f;
    memcpy(tmp, arr, n * sizeof(float));
    qsort(tmp, n, sizeof(float), _cmp_float);
    float idx = (p / 100.0f) * (float)(n - 1);
    int lo = (int)idx;
    float frac = idx - (float)lo;
    float result;
    if (lo + 1 < n) {
        result = tmp[lo] + frac * (tmp[lo + 1] - tmp[lo]);
    } else {
        result = tmp[lo];
    }
    free(tmp);
    return result;
}

static int _search_left(float *arr, int n, float val) {
    int lo = 0, hi = n;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (arr[mid] < val) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static int _search_right(float *arr, int n, float val) {
    int lo = 0, hi = n;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (arr[mid] <= val) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static void _compute_band_slices(AudioProcessor *self) {
    float limits[8][2] = {
        {20.0f, 60.0f}, {60.0f, 120.0f}, {120.0f, 250.0f},
        {250.0f, 500.0f}, {500.0f, 1000.0f}, {1000.0f, 2500.0f},
        {2500.0f, 6000.0f}, {6000.0f, 16000.0f},
    };
    int half = FFT_SIZE / 2;
    for (int b = 0; b < NUM_BANDS; b++) {
        int start = _search_left(self->s.frequencies, half, limits[b][0]);
        int end = _search_right(self->s.frequencies, half, limits[b][1]);
        if (start >= half) start = half - 1;
        if (end < start + 1) end = start + 1;
        if (end > half) end = half;
        self->s.band_slices[b][0] = start;
        self->s.band_slices[b][1] = end;
    }
}

static void _compute_features(AudioProcessor *self, float rms, float *bands,
                              float *magnitudes, int beat, float beat_strength) {
    AudioProcessorState *s = &self->s;
    int half = FFT_SIZE / 2;

    s->rms_history[s->rms_idx % RMS_HISTORY_SIZE] = rms;
    s->rms_idx++;

    float band_weights[8] = {0.2f, 0.3f, 0.8f, 1.0f, 1.15f, 1.35f, 1.5f, 1.6f};
    float weighted_band_energy = 0.0f;
    float raw_flux = 0.0f;
    for (int b = 0; b < NUM_BANDS; b++) {
        float change = bands[b] - s->prev_feature_bands[b];
        if (change < 0.0f) change = 0.0f;
        weighted_band_energy += bands[b] * band_weights[b];
        raw_flux += change * band_weights[b];
        s->prev_feature_bands[b] = bands[b];
    }
    float spectral_flux = raw_flux / fmaxf(weighted_band_energy, 0.08f);
    if (spectral_flux > 1.0f) spectral_flux = 1.0f;

    float onset_raw = fmaxf(0.0f, rms - s->prev_rms) * 0.55f + spectral_flux * 0.85f;
    if (onset_raw > 1.0f) onset_raw = 1.0f;
    s->prev_rms = rms;
    s->smooth_onset = _smooth(s->smooth_onset, onset_raw, 0.3f, 0.08f);
    s->onset_history[s->onset_idx % ONSET_HISTORY_SIZE] = s->smooth_onset;
    s->onset_idx++;

    if (s->smooth_onset > ONSET_THRESHOLD && s->smooth_onset > s->prev_onset * 1.4f) {
        int interval = s->fft_call_count - s->last_onset_frame;
        float interval_sec = (float)interval * (float)FFT_SIZE / s->sample_rate;
        if (interval_sec > BPM_INTERVAL_MIN && interval_sec < BPM_INTERVAL_MAX) {
            if (s->ioi_buffer_len < MAX_IOI_BUFFER) {
                s->ioi_buffer[s->ioi_buffer_len++] = interval_sec;
            } else {
                memmove(s->ioi_buffer, s->ioi_buffer + 1, (MAX_IOI_BUFFER - 1) * sizeof(float));
                s->ioi_buffer[MAX_IOI_BUFFER - 1] = interval_sec;
            }
            if (s->ioi_buffer_len > 0) {
                s->bpm = 60.0f / _median(s->ioi_buffer, s->ioi_buffer_len);
            }
        }
        s->last_onset_frame = s->fft_call_count;
    }
    s->prev_onset = s->smooth_onset;

    float total_mag = 0.0f;
    for (int i = 0; i < half; i++) total_mag += magnitudes[i];
    float centroid_norm = 0.0f;
    if (total_mag > 1e-9f) {
        float weighted_sum = 0.0f;
        for (int i = 0; i < half; i++) weighted_sum += s->frequencies[i] * magnitudes[i];
        float centroid = weighted_sum / total_mag;
        centroid_norm = centroid / (s->sample_rate / 2.0f);
    }

    float total_band_energy = 0.0f;
    for (int b = 0; b < NUM_BANDS; b++) total_band_energy += bands[b];
    float off_bass_share = 0.0f, high_share = 0.0f;
    if (total_band_energy > 1e-9f) {
        float off_bass_sum = 0.0f, high_sum = 0.0f;
        for (int b = 2; b < NUM_BANDS; b++) off_bass_sum += bands[b];
        for (int b = 5; b < NUM_BANDS; b++) high_sum += bands[b];
        off_bass_share = off_bass_sum / total_band_energy;
        high_share = high_sum / total_band_energy;
    }
    s->brightness = centroid_norm * 0.35f + off_bass_share * 0.75f + high_share * 0.35f;
    if (s->brightness > 1.0f) s->brightness = 1.0f;

    int window = (s->rms_idx < RMS_HISTORY_SIZE) ? s->rms_idx : RMS_HISTORY_SIZE;
    if (window > 10) {
        float p95 = _percentile(s->rms_history, window, 95.0f);
        float p10 = _percentile(s->rms_history, window, 10.0f);
        s->dynamic_range = fmaxf(0.0f, p95 - p10);
    } else {
        s->dynamic_range = 0.0f;
    }

    float bass = (bands[0] + bands[1]) * 0.5f;
    float mid = (bands[2] + bands[3] + bands[4]) / 3.0f;
    float treble = (bands[5] + bands[6] + bands[7]) / 3.0f;
    float bass_delta = bass - s->prev_drop_bass;

    s->features_rolling_loudness = _smooth(s->features_rolling_loudness, rms, 0.08f, 0.025f);

    float silence_threshold = fmaxf(s->silence_floor * 3.0f, fmaxf(s->noise_floor * 0.8f, 0.012f));
    if (s->features_rolling_loudness <= silence_threshold && rms <= silence_threshold * 1.4f) {
        s->silence_frames++;
    } else {
        s->silence_frames = 0;
    }
    s->features_silence = s->silence_frames >= 8;

    if (s->drop_cooldown_frames > 0) s->drop_cooldown_frames--;
    s->features_drop_detected = 0;
    if (
        s->drop_cooldown_frames == 0
        && bass > 0.45f
        && bass_delta > 0.16f
        && s->smooth_onset > 0.12f
        && (beat || beat_strength > 0.08f || s->features_rolling_loudness < rms * 0.85f)
    ) {
        s->features_drop_detected = 1;
        s->drop_cooldown_frames = 24;
    }
    s->prev_drop_bass = _smooth(s->prev_drop_bass, bass, 0.18f, 0.06f);

    if (s->fft_call_count == 1) {
        s->section_energy = rms;
        s->section_mid = mid;
        s->section_treble = treble;
        s->section_brightness = s->brightness;
    }
    if (s->section_cooldown_frames > 0) s->section_cooldown_frames--;
    float section_delta =
        fabsf(rms - s->section_energy) * 0.9f
        + fabsf(mid - s->section_mid) * 0.45f
        + fabsf(treble - s->section_treble) * 0.45f
        + fabsf(s->brightness - s->section_brightness) * 0.35f;
    s->features_section_change = 0;
    if (s->fft_call_count > 20 && s->section_cooldown_frames == 0 && section_delta > 0.42f && s->smooth_onset > 0.08f) {
        s->features_section_change = 1;
        s->section_cooldown_frames = 60;
        s->section_energy = rms;
        s->section_mid = mid;
        s->section_treble = treble;
        s->section_brightness = s->brightness;
    } else {
        s->section_energy = _smooth(s->section_energy, rms, 0.018f, 0.018f);
        s->section_mid = _smooth(s->section_mid, mid, 0.018f, 0.018f);
        s->section_treble = _smooth(s->section_treble, treble, 0.018f, 0.018f);
        s->section_brightness = _smooth(s->section_brightness, s->brightness, 0.018f, 0.018f);
    }

    s->features_bpm = s->bpm;
    s->features_energy = rms;
    s->features_bass = bass;
    s->features_brightness = s->brightness;
    s->features_onset_strength = s->smooth_onset;
    s->features_dynamic_range = s->dynamic_range;
    s->features_beat = beat;
    s->features_beat_strength = beat_strength;
    memcpy(s->features_bands, bands, sizeof(float) * NUM_BANDS);
    s->features_bass_energy = bass;
    s->features_mid_energy = mid;
    s->features_treble_energy = treble;
    s->features_spectral_centroid = _clamp01f(centroid_norm);
    s->features_spectral_flux = _clamp01f(spectral_flux);
    s->features_beat_confidence = _clamp01f(fmaxf(beat_strength, s->smooth_onset * 0.55f));
}

static void _process_fft(AudioProcessor *self) {
    AudioProcessorState *s = &self->s;
    s->fft_call_count++;
    int half = FFT_SIZE / 2;

    float normalized[FFT_SIZE];
    if (s->dc_block_enabled) {
        float sum = 0.0f;
        for (int i = 0; i < FFT_SIZE; i++) sum += s->buffer[i];
        float mean = sum / (float)FFT_SIZE;
        for (int i = 0; i < FFT_SIZE; i++) normalized[i] = s->buffer[i] - mean;
    } else {
        memcpy(normalized, s->buffer, FFT_SIZE * sizeof(float));
    }

    if (s->normalization_enabled) {
        float sq_sum = 0.0f;
        for (int i = 0; i < FFT_SIZE; i++) sq_sum += normalized[i] * normalized[i];
        float level = (FFT_SIZE > 0) ? sqrtf(sq_sum / (float)FFT_SIZE) : 0.0f;

        float level_factor = (level > s->level_estimate) ? s->adapt_attack : s->adapt_release;
        s->level_estimate = _smooth(s->level_estimate, level, level_factor, level_factor);

        float effective_level = fmaxf(level, s->level_estimate);
        float target_gain;
        if (effective_level <= s->silence_floor) {
            target_gain = s->min_gain;
        } else {
            target_gain = s->target_level / effective_level;
            if (effective_level >= s->music_threshold) {
                target_gain = fminf(target_gain, s->music_max_gain);
            }
            target_gain = fmaxf(s->min_gain, fminf(s->max_gain, target_gain));
        }

        float gain_factor = (target_gain > s->normalization_gain) ? s->adapt_attack : s->adapt_release;
        s->normalization_gain = _smooth(s->normalization_gain, target_gain, gain_factor, gain_factor);

        for (int i = 0; i < FFT_SIZE; i++) {
            normalized[i] = normalized[i] * s->normalization_gain;
            if (normalized[i] > 1.0f) normalized[i] = 1.0f;
            else if (normalized[i] < -1.0f) normalized[i] = -1.0f;
        }
    }

    float sq_sum = 0.0f;
    for (int i = 0; i < FFT_SIZE; i++) sq_sum += normalized[i] * normalized[i];
    float raw_rms = sqrtf(sq_sum / (float)FFT_SIZE);
    if (raw_rms > 1.0f) raw_rms = 1.0f;

    kiss_fft_scalar fft_in[FFT_SIZE];
    kiss_fft_cpx fft_out[FFT_SIZE / 2 + 1];
    for (int i = 0; i < FFT_SIZE; i++) {
        fft_in[i] = normalized[i] * s->window[i];
    }
    kiss_fftr(self->fft_cfg, fft_in, fft_out);

    float mags[FFT_SIZE / 2];
    for (int i = 0; i < half; i++) {
        mags[i] = sqrtf(fft_out[i].r * fft_out[i].r + fft_out[i].i * fft_out[i].i) / WINDOW_SCALE;
    }

    float raw_bands[NUM_BANDS];
    for (int b = 0; b < NUM_BANDS; b++) {
        int start = s->band_slices[b][0];
        int end = s->band_slices[b][1];
        if (end > half) end = half;
        if (start >= end) { raw_bands[b] = 0.0f; continue; }
        float sum_sq = 0.0f;
        for (int i = start; i < end; i++) sum_sq += mags[i] * mags[i];
        float value = sqrtf(sum_sq / (float)(end - start));
        float compressed = (value > 0.0f) ? value / (value + 0.12f) : 0.0f;
        raw_bands[b] = compressed * 1.2f;
        if (raw_bands[b] > 1.0f) raw_bands[b] = 1.0f;
    }

    float gated_rms = _apply_noise_floor(raw_rms, s->noise_floor);
    float gated_bands[NUM_BANDS];
    for (int b = 0; b < NUM_BANDS; b++) {
        gated_bands[b] = _apply_noise_floor(raw_bands[b], s->noise_floor);
    }

    float bass = gated_bands[0];
    float bass_rise = bass - s->prev_bass_energy;
    float threshold = fmaxf(s->prev_bass_energy * 1.12f, s->noise_floor + 0.01f);
    int beat = (bass > threshold && bass_rise > 0.015f) ? 1 : 0;
    float raw_beat_strength = beat ? fminf(bass_rise * 1.6f, 1.0f) : 0.0f;
    s->prev_bass_energy = s->prev_bass_energy * 0.9f + bass * 0.1f;

    float rms, beat_strength;
    float bands[NUM_BANDS];
    if (s->smoothing_enabled) {
        s->smoothed_rms = _smooth(s->smoothed_rms, gated_rms, s->rms_attack, s->rms_release);
        for (int b = 0; b < NUM_BANDS; b++) {
            s->smoothed_bands[b] = _smooth(s->smoothed_bands[b], gated_bands[b], s->band_attack, s->band_release);
        }
        s->beat_envelope = _smooth(s->beat_envelope, raw_beat_strength, 1.0f, s->beat_release);
        rms = s->smoothed_rms;
        memcpy(bands, s->smoothed_bands, sizeof(float) * NUM_BANDS);
        beat_strength = s->beat_envelope;
    } else {
        s->smoothed_rms = gated_rms;
        memcpy(s->smoothed_bands, gated_bands, sizeof(float) * NUM_BANDS);
        s->beat_envelope = raw_beat_strength;
        rms = gated_rms;
        memcpy(bands, gated_bands, sizeof(float) * NUM_BANDS);
        beat_strength = raw_beat_strength;
    }

    s->frame_rms = rms;
    memcpy(s->frame_bands, bands, sizeof(float) * NUM_BANDS);
    s->frame_beat = beat;
    s->frame_beat_strength = beat_strength;

    _compute_features(self, rms, bands, mags, beat, beat_strength);
}

static int AudioProcessor_init(AudioProcessor *self, PyObject *args, PyObject *kwds) {
    PyObject *config_dict = NULL;
    float sample_rate = 44100.0f;
    static char *kwlist[] = {"config", "sample_rate", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O!|f", kwlist, &PyDict_Type, &config_dict, &sample_rate))
        return -1;

    AudioProcessorState *s = &self->s;
    memset(s, 0, sizeof(AudioProcessorState));
    s->sample_rate = sample_rate;
    s->normalization_gain = 1.0f;
    s->bpm = 120.0f;
    s->last_onset_frame = 0;

    s->noise_floor = 0.015f;
    s->rms_attack = 0.45f;
    s->rms_release = 0.12f;
    s->band_attack = 0.4f;
    s->band_release = 0.1f;
    s->beat_release = 0.18f;
    s->smoothing_enabled = 1;
    s->target_level = 0.36f;
    s->min_gain = 0.35f;
    s->max_gain = 5.0f;
    s->adapt_attack = 0.18f;
    s->adapt_release = 0.18f;
    s->music_threshold = 0.015f;
    s->music_max_gain = 4.5f;
    s->silence_floor = 0.003f;
    s->normalization_enabled = 1;
    s->dc_block_enabled = 1;

    PyObject *key, *value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(config_dict, &pos, &key, &value)) {
        if (!PyUnicode_Check(key)) continue;
        const char *k = PyUnicode_AsUTF8(key);
        double v = PyFloat_AsDouble(value);
        if (PyErr_Occurred()) {
            if (PyBool_Check(value)) {
                PyErr_Clear();
                v = (value == Py_True) ? 1.0 : 0.0;
            } else {
                PyErr_Clear();
                continue;
            }
        }
        #define SET_FLOAT(field) else if (strcmp(k, #field) == 0) s->field = (float)v
        #define SET_INT(field) else if (strcmp(k, #field) == 0) s->field = (int)v

        if (0) {}
        SET_FLOAT(noise_floor);
        SET_FLOAT(rms_attack);
        SET_FLOAT(rms_release);
        SET_FLOAT(band_attack);
        SET_FLOAT(band_release);
        SET_FLOAT(beat_release);
        SET_INT(smoothing_enabled);
        SET_FLOAT(target_level);
        SET_FLOAT(min_gain);
        SET_FLOAT(max_gain);
        SET_FLOAT(adapt_attack);
        SET_FLOAT(adapt_release);
        SET_FLOAT(music_threshold);
        SET_FLOAT(music_max_gain);
        SET_FLOAT(silence_floor);
        SET_INT(normalization_enabled);
        SET_INT(dc_block_enabled);

        #undef SET_FLOAT
        #undef SET_INT
    }

    for (int i = 0; i < FFT_SIZE; i++) {
        s->window[i] = 0.5f * (1.0f - cosf(2.0f * (float)M_PI * (float)i / (float)(FFT_SIZE - 1)));
    }
    for (int i = 0; i < FFT_SIZE / 2; i++) {
        s->frequencies[i] = (float)i * sample_rate / (float)FFT_SIZE;
    }
    _compute_band_slices(self);

    self->fft_cfg = kiss_fftr_alloc(FFT_SIZE, 0, NULL, NULL);
    if (!self->fft_cfg) {
        PyErr_NoMemory();
        return -1;
    }

    return 0;
}

static void AudioProcessor_dealloc(AudioProcessor *self) {
    if (self->fft_cfg) {
        kiss_fftr_free(self->fft_cfg);
        self->fft_cfg = NULL;
    }
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject* AudioProcessor_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    AudioProcessor *self = (AudioProcessor *)type->tp_alloc(type, 0);
    if (self) {
        self->fft_cfg = NULL;
    }
    return (PyObject *)self;
}

static PyObject* AudioProcessor_feed_samples(AudioProcessor *self, PyObject *args) {
    PyObject *obj;
    if (!PyArg_ParseTuple(args, "O", &obj))
        return NULL;

    Py_buffer view;
    if (PyObject_GetBuffer(obj, &view, PyBUF_FORMAT | PyBUF_ND | PyBUF_C_CONTIGUOUS) != 0)
        return NULL;

    if (view.ndim != 1 || view.format[0] != 'f') {
        PyErr_SetString(PyExc_TypeError, "expected a 1-D float32 array");
        PyBuffer_Release(&view);
        return NULL;
    }

    float *samples = (float *)view.buf;
    Py_ssize_t size = view.len / sizeof(float);
    if (size <= 0) {
        PyBuffer_Release(&view);
        Py_RETURN_NONE;
    }

    self->s.feed_count++;
    self->s.samples_seen += (long long)size;

    float abs_sum = 0.0f;
    for (Py_ssize_t i = 0; i < size; i++) abs_sum += fabsf(samples[i]);
    self->s.sample_sum += abs_sum;

    Py_ssize_t offset = 0;
    while (offset < size) {
        int remaining = FFT_SIZE - self->s.buffer_pos;
        int chunk = (int)(size - offset);
        if (chunk > remaining) chunk = remaining;
        memcpy(self->s.buffer + self->s.buffer_pos, samples + offset, chunk * sizeof(float));
        self->s.buffer_pos += chunk;
        offset += chunk;
        if (self->s.buffer_pos >= FFT_SIZE) {
            _process_fft(self);
            memmove(self->s.buffer, self->s.buffer + FFT_HOP_SIZE, sizeof(float) * FFT_HOP_SIZE);
            self->s.buffer_pos = FFT_HOP_SIZE;
        }
    }

    PyBuffer_Release(&view);
    Py_RETURN_NONE;
}

static PyObject* _make_bands_tuple(const float *bands) {
    PyObject *t = PyTuple_New(NUM_BANDS);
    if (!t) return NULL;
    for (int b = 0; b < NUM_BANDS; b++) {
        PyObject *val = PyFloat_FromDouble((double)bands[b]);
        if (!val) { Py_DECREF(t); return NULL; }
        PyTuple_SET_ITEM(t, b, val);
    }
    return t;
}

static PyObject* AudioProcessor_frame(AudioProcessor *self, PyObject *Py_UNUSED(ignored)) {
    PyObject *bands_tuple = _make_bands_tuple(self->s.frame_bands);
    if (!bands_tuple) return NULL;

    PyObject *r = PyFloat_FromDouble((double)self->s.frame_rms);
    if (!r) { Py_DECREF(bands_tuple); return NULL; }
    PyObject *b = PyBool_FromLong((long)self->s.frame_beat);
    if (!b) { Py_DECREF(r); Py_DECREF(bands_tuple); return NULL; }
    PyObject *s = PyFloat_FromDouble((double)self->s.frame_beat_strength);
    if (!s) { Py_DECREF(r); Py_DECREF(bands_tuple); Py_DECREF(b); return NULL; }
    PyObject *seq = PyLong_FromLong((long)self->s.fft_call_count);
    if (!seq) { Py_DECREF(r); Py_DECREF(bands_tuple); Py_DECREF(b); Py_DECREF(s); return NULL; }

    PyObject *result = PyTuple_New(5);
    if (!result) {
        Py_DECREF(r); Py_DECREF(bands_tuple); Py_DECREF(b); Py_DECREF(s); Py_DECREF(seq);
        return NULL;
    }
    PyTuple_SET_ITEM(result, 0, r);
    PyTuple_SET_ITEM(result, 1, bands_tuple);
    PyTuple_SET_ITEM(result, 2, b);
    PyTuple_SET_ITEM(result, 3, s);
    PyTuple_SET_ITEM(result, 4, seq);
    return result;
}

static PyObject* AudioProcessor_features(AudioProcessor *self, PyObject *Py_UNUSED(ignored)) {
    PyObject *bands_tuple = _make_bands_tuple(self->s.features_bands);
    if (!bands_tuple) return NULL;

    PyObject *items[19];
    items[0] = PyFloat_FromDouble((double)self->s.features_bpm);
    items[1] = PyFloat_FromDouble((double)self->s.features_energy);
    items[2] = PyFloat_FromDouble((double)self->s.features_bass);
    items[3] = PyFloat_FromDouble((double)self->s.features_brightness);
    items[4] = PyFloat_FromDouble((double)self->s.features_onset_strength);
    items[5] = PyFloat_FromDouble((double)self->s.features_dynamic_range);
    items[6] = PyBool_FromLong((long)self->s.features_beat);
    items[7] = PyFloat_FromDouble((double)self->s.features_beat_strength);
    items[8] = bands_tuple;  // already created, no INCREF needed
    items[9] = PyFloat_FromDouble((double)self->s.features_bass_energy);
    items[10] = PyFloat_FromDouble((double)self->s.features_mid_energy);
    items[11] = PyFloat_FromDouble((double)self->s.features_treble_energy);
    items[12] = PyFloat_FromDouble((double)self->s.features_spectral_centroid);
    items[13] = PyFloat_FromDouble((double)self->s.features_spectral_flux);
    items[14] = PyFloat_FromDouble((double)self->s.features_beat_confidence);
    items[15] = PyFloat_FromDouble((double)self->s.features_rolling_loudness);
    items[16] = PyBool_FromLong((long)self->s.features_silence);
    items[17] = PyBool_FromLong((long)self->s.features_drop_detected);
    items[18] = PyBool_FromLong((long)self->s.features_section_change);

    for (int i = 0; i < 19; i++) {
        if (!items[i]) {
            for (int j = 0; j < 19; j++) {
                if (items[j]) Py_DECREF(items[j]);
            }
            return NULL;
        }
    }

    PyObject *result = PyTuple_New(19);
    if (!result) {
        for (int i = 0; i < 19; i++) Py_DECREF(items[i]);
        return NULL;
    }
    for (int i = 0; i < 19; i++) PyTuple_SET_ITEM(result, i, items[i]);
    return result;
}

static PyObject* AudioProcessor_state_copy(AudioProcessor *self, PyObject *Py_UNUSED(ignored)) {
    PyTypeObject *type = Py_TYPE(self);
    AudioProcessor *clone = (AudioProcessor *)type->tp_alloc(type, 0);
    if (!clone) return NULL;

    clone->s = self->s;

    clone->fft_cfg = kiss_fftr_alloc(FFT_SIZE, 0, NULL, NULL);
    if (!clone->fft_cfg) {
        Py_DECREF(clone);
        return PyErr_NoMemory();
    }

    return (PyObject *)clone;
}

static PyObject* AudioProcessor_reset(AudioProcessor *self, PyObject *Py_UNUSED(ignored)) {
    AudioProcessorState saved = self->s;
    memset(&self->s, 0, sizeof(AudioProcessorState));
    self->s.sample_rate = saved.sample_rate;
    self->s.normalization_gain = 1.0f;
    self->s.bpm = 120.0f;
    self->s.noise_floor = saved.noise_floor;
    self->s.rms_attack = saved.rms_attack;
    self->s.rms_release = saved.rms_release;
    self->s.band_attack = saved.band_attack;
    self->s.band_release = saved.band_release;
    self->s.beat_release = saved.beat_release;
    self->s.smoothing_enabled = saved.smoothing_enabled;
    self->s.target_level = saved.target_level;
    self->s.min_gain = saved.min_gain;
    self->s.max_gain = saved.max_gain;
    self->s.adapt_attack = saved.adapt_attack;
    self->s.adapt_release = saved.adapt_release;
    self->s.music_threshold = saved.music_threshold;
    self->s.music_max_gain = saved.music_max_gain;
    self->s.silence_floor = saved.silence_floor;
    self->s.normalization_enabled = saved.normalization_enabled;
    self->s.dc_block_enabled = saved.dc_block_enabled;
    memcpy(self->s.window, saved.window, sizeof(float) * FFT_SIZE);
    memcpy(self->s.frequencies, saved.frequencies, sizeof(float) * (FFT_SIZE / 2));
    memcpy(self->s.band_slices, saved.band_slices, sizeof(int) * NUM_BANDS * 2);
    Py_RETURN_NONE;
}

static PyObject* AudioProcessor_normalization_gain(AudioProcessor *self, PyObject *Py_UNUSED(ignored)) {
    return PyFloat_FromDouble((double)self->s.normalization_gain);
}

static PyObject* AudioProcessor_stats(AudioProcessor *self, PyObject *Py_UNUSED(ignored)) {
    return Py_BuildValue(
        "(iLifd)",
        self->s.feed_count,
        self->s.samples_seen,
        self->s.fft_call_count,
        (double)self->s.sample_sum,
        (double)self->s.normalization_gain
    );
}

static PyMethodDef AudioProcessor_methods[] = {
    {"feed_samples", (PyCFunction)AudioProcessor_feed_samples, METH_VARARGS, "Feed audio samples"},
    {"frame", (PyCFunction)AudioProcessor_frame, METH_NOARGS, "Get current AudioFrame as tuple"},
    {"features", (PyCFunction)AudioProcessor_features, METH_NOARGS, "Get current MusicFeatures as tuple"},
    {"state_copy", (PyCFunction)AudioProcessor_state_copy, METH_NOARGS, "Deep clone state"},
    {"reset", (PyCFunction)AudioProcessor_reset, METH_NOARGS, "Reset state to initial values"},
    {"normalization_gain", (PyCFunction)AudioProcessor_normalization_gain, METH_NOARGS, "Current AGC gain value"},
    {"stats", (PyCFunction)AudioProcessor_stats, METH_NOARGS, "Get processor counters and gain"},
    {NULL, NULL, 0, NULL},
};

static PyTypeObject AudioProcessorType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lumistripe._audio.AudioProcessor",
    .tp_basicsize = sizeof(AudioProcessor),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = AudioProcessor_new,
    .tp_init = (initproc)AudioProcessor_init,
    .tp_dealloc = (destructor)AudioProcessor_dealloc,
    .tp_methods = AudioProcessor_methods,
};

static PyMethodDef module_methods[] = {
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef audiomodule = {
    PyModuleDef_HEAD_INIT,
    "lumistripe._audio",
    NULL,
    -1,
    module_methods,
};

PyMODINIT_FUNC PyInit__audio(void) {
    import_array();
    if (PyErr_Occurred()) return NULL;

    if (PyType_Ready(&AudioProcessorType) < 0) return NULL;

    PyObject *m = PyModule_Create(&audiomodule);
    if (!m) return NULL;

    Py_INCREF(&AudioProcessorType);
    if (PyModule_AddObject(m, "AudioProcessor", (PyObject *)&AudioProcessorType) < 0) {
        Py_DECREF(&AudioProcessorType);
        Py_DECREF(m);
        return NULL;
    }

    return m;
}
