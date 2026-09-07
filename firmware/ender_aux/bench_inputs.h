#ifndef E3_ENDER_AUX_BENCH_INPUTS_H
#define E3_ENDER_AUX_BENCH_INPUTS_H

#include <stdint.h>

/* Configure PC14 only: input with weak pull-up. No output or probe pulses. */
void bench_inputs_init(void);

/* Raw electrical GPIO level, 0 or 1. This does not identify a CR Touch state. */
uint32_t bench_probe_raw(void);

#endif
