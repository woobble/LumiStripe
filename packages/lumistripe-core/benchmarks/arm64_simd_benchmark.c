#define _POSIX_C_SOURCE 200809L
#include "../src/lumistripe/render/native_pixel.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

typedef void (*correction_kernel)(const uint8_t *, uint8_t *, size_t,
                                  const uint16_t[3]);
typedef void (*screen_composite_kernel)(const uint8_t *, uint8_t *,
                                        const uint8_t *, const uint8_t *,
                                        size_t, float, float, float);

static double wall_seconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0)
        return 0.0;
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}

static double process_cpu_seconds(void) {
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage) != 0)
        return 0.0;
    return (double)usage.ru_utime.tv_sec +
           (double)usage.ru_utime.tv_usec / 1000000.0 +
           (double)usage.ru_stime.tv_sec +
           (double)usage.ru_stime.tv_usec / 1000000.0;
}

static uint64_t checksum(const uint8_t *bytes, size_t byte_count) {
    uint64_t result = 0;
    for (size_t index = 0; index < byte_count; index++)
        result = (result * 131u) + bytes[index];
    return result;
}

static unsigned int max_byte_delta(const uint8_t *left, const uint8_t *right,
                                   size_t byte_count) {
    unsigned int maximum = 0u;
    for (size_t index = 0; index < byte_count; index++) {
        const unsigned int a = left[index];
        const unsigned int b = right[index];
        const unsigned int delta = a > b ? a - b : b - a;
        if (delta > maximum)
            maximum = delta;
    }
    return maximum;
}

static int verify_byte_domain(void) {
    uint8_t source[256u * 4u];
    uint8_t scalar[256u * 4u];
    uint8_t dispatched[256u * 4u];
    for (size_t index = 0; index < 256u; index++) {
        const uint8_t value = (uint8_t)index;
        source[index * 4u] = value;
        source[index * 4u + 1u] = (uint8_t)(index * 13u + 7u);
        source[index * 4u + 2u] = (uint8_t)(index * 29u + 11u);
        source[index * 4u + 3u] = (uint8_t)(index * 17u + 19u);
    }
    for (unsigned int gain = 0; gain <= 255u; gain++) {
        const uint16_t correction[3] = {
            (uint16_t)gain,
            (uint16_t)((gain * 13u) & 255u),
            (uint16_t)((gain * 29u) & 255u),
        };
        lumistripe_correct_rgba_scalar(source, scalar, 256u, correction);
        lumistripe_correct_rgba(source, dispatched, 256u, correction);
        if (memcmp(scalar, dispatched, sizeof(scalar)) != 0) {
            fprintf(stderr, "scalar/dispatch mismatch at correction gain %u\n", gain);
            return 1;
        }
    }
    puts("exhaustive byte-domain parity: 256 gains x 256 source values passed");
    return 0;
}

static void measure(const char *name, correction_kernel kernel,
                    const uint8_t *source, uint8_t *destination,
                    size_t pixels, size_t iterations,
                    const uint16_t correction[3], double *elapsed_out) {
    const double cpu_start = process_cpu_seconds();
    const double wall_start = wall_seconds();
    for (size_t index = 0; index < iterations; index++)
        kernel(source, destination, pixels, correction);
    const double elapsed = wall_seconds() - wall_start;
    const double cpu_elapsed = process_cpu_seconds() - cpu_start;
    *elapsed_out = elapsed;
    printf("  %-10s %8.3f ms  CPU %5.1f%%  checksum=%" PRIu64 "\n",
           name, elapsed * 1000.0,
           elapsed > 0.0 ? cpu_elapsed * 100.0 / elapsed : 0.0,
           checksum(destination, pixels * 4u));
}

static double measure_composite(const char *name, screen_composite_kernel kernel,
                                const uint8_t *base, uint8_t *destination,
                                const uint8_t *overlay_1,
                                const uint8_t *overlay_2, size_t pixels,
                                size_t iterations, float base_intensity,
                                float strength_1, float strength_2) {
    const double cpu_start = process_cpu_seconds();
    const double wall_start = wall_seconds();
    for (size_t index = 0; index < iterations; index++)
        kernel(base, destination, overlay_1, overlay_2, pixels,
               base_intensity, strength_1, strength_2);
    const double elapsed = wall_seconds() - wall_start;
    const double cpu_elapsed = process_cpu_seconds() - cpu_start;
    printf("  %-10s %8.3f ms  CPU %5.1f%%  checksum=%" PRIu64 "\n",
           name, elapsed * 1000.0,
           elapsed > 0.0 ? cpu_elapsed * 100.0 / elapsed : 0.0,
           checksum(destination, pixels * 4u));
    return elapsed;
}

static int run_case(size_t pixels, size_t iterations) {
    uint8_t *source = (uint8_t *)malloc(pixels * 4u);
    uint8_t *scalar = (uint8_t *)malloc(pixels * 4u);
    uint8_t *dispatched = (uint8_t *)malloc(pixels * 4u);
    if (source == NULL || scalar == NULL || dispatched == NULL) {
        fprintf(stderr, "allocation failed for %zu pixels\n", pixels);
        free(source);
        free(scalar);
        free(dispatched);
        return 1;
    }
    for (size_t index = 0; index < pixels * 4u; index++)
        source[index] = (uint8_t)(index * 73u + index / 7u + 19u);
    const uint16_t correction[3] = {37u, 128u, 254u};

    lumistripe_correct_rgba_scalar(source, scalar, pixels, correction);
    lumistripe_correct_rgba(source, dispatched, pixels, correction);
    if (memcmp(scalar, dispatched, pixels * 4u) != 0) {
        fprintf(stderr, "scalar/dispatch mismatch for %zu pixels\n", pixels);
        free(source);
        free(scalar);
        free(dispatched);
        return 1;
    }

    printf("%zu pixels x %zu iterations\n", pixels, iterations);
    double scalar_seconds;
    double dispatched_seconds;
    measure("scalar", lumistripe_correct_rgba_scalar, source, scalar,
            pixels, iterations, correction, &scalar_seconds);
    measure("dispatch", lumistripe_correct_rgba, source, dispatched,
            pixels, iterations, correction, &dispatched_seconds);
    printf("  dispatch/scalar speed ratio: %.3fx\n",
           dispatched_seconds > 0.0 ? scalar_seconds / dispatched_seconds : 0.0);

    free(source);
    free(scalar);
    free(dispatched);
    return 0;
}

static int run_composite_case(size_t pixels, size_t iterations) {
    const size_t bytes = pixels * 4u;
    uint8_t *base = (uint8_t *)malloc(bytes);
    uint8_t *overlay_1 = (uint8_t *)malloc(bytes);
    uint8_t *overlay_2 = (uint8_t *)malloc(bytes);
    uint8_t *scalar = (uint8_t *)malloc(bytes);
    uint8_t *dispatched = (uint8_t *)malloc(bytes);
    if (base == NULL || overlay_1 == NULL || overlay_2 == NULL ||
        scalar == NULL || dispatched == NULL) {
        fprintf(stderr, "composite allocation failed for %zu pixels\n", pixels);
        free(base);
        free(overlay_1);
        free(overlay_2);
        free(scalar);
        free(dispatched);
        return 1;
    }
    for (size_t index = 0; index < bytes; index++) {
        base[index] = (uint8_t)(index * 43u + index / 9u + 7u);
        overlay_1[index] = (uint8_t)(index * 17u + index / 5u + 53u);
        overlay_2[index] = (uint8_t)(index * 61u + index / 13u + 101u);
    }
    const float base_intensity = 0.82f;
    const float strength_1 = 0.35f;
    const float strength_2 = 0.4f;
    lumistripe_composite_screen_rgba_scalar(
        base, scalar, overlay_1, overlay_2, pixels,
        base_intensity, strength_1, strength_2);
    lumistripe_composite_screen_rgba(
        base, dispatched, overlay_1, overlay_2, pixels,
        base_intensity, strength_1, strength_2);
    const unsigned int delta = max_byte_delta(scalar, dispatched, bytes);
    if (delta > 1u) {
        fprintf(stderr, "composite scalar/dispatch differs by %u for %zu pixels\n",
                delta, pixels);
        free(base);
        free(overlay_1);
        free(overlay_2);
        free(scalar);
        free(dispatched);
        return 1;
    }
    printf("screen composite: %zu pixels x %zu iterations (max delta %u)\n",
           pixels, iterations, delta);
    const double scalar_seconds = measure_composite(
        "scalar", lumistripe_composite_screen_rgba_scalar,
        base, scalar, overlay_1, overlay_2, pixels, iterations,
        base_intensity, strength_1, strength_2);
    const double dispatch_seconds = measure_composite(
        "dispatch", lumistripe_composite_screen_rgba,
        base, dispatched, overlay_1, overlay_2, pixels, iterations,
        base_intensity, strength_1, strength_2);
    printf("  dispatch/scalar speed ratio: %.3fx\n",
           dispatch_seconds > 0.0 ? scalar_seconds / dispatch_seconds : 0.0);
    free(base);
    free(overlay_1);
    free(overlay_2);
    free(scalar);
    free(dispatched);
    return 0;
}

int main(int argc, char **argv) {
    size_t iterations = 2000u;
    if (argc > 2) {
        fprintf(stderr, "usage: %s [iterations]\n", argv[0]);
        return 2;
    }
    if (argc == 2) {
        char *end = NULL;
        const unsigned long parsed = strtoul(argv[1], &end, 10);
        if (end == argv[1] || *end != '\0' || parsed == 0u) {
            fprintf(stderr, "iterations must be a positive integer\n");
            return 2;
        }
        iterations = (size_t)parsed;
    }
    if (verify_byte_domain() != 0)
        return 1;
    const size_t sizes[] = {600u, 4096u, 16384u};
    for (size_t index = 0; index < sizeof(sizes) / sizeof(sizes[0]); index++) {
        if (run_case(sizes[index], iterations) != 0)
            return 1;
        if (run_composite_case(sizes[index], iterations) != 0)
            return 1;
    }
    return 0;
}
