#include "platform.h"
#include "protocol.h"
#include "bench_inputs.h"

/* All coordinates, fans and probe actions below are software state only.
 * There is no GPIO output-enable, PWM, step pulse or servo implementation. */
enum motion_kind { IDLE, MOVE, PROBE };
enum result_kind { NONE, DONE, CONTACT, NO_TRIGGER, STOPPED, ERROR };
struct token { const char *text; unsigned length; };

static char line[E3_LINE_CAPACITY];
static unsigned length;
static int discard;
static uint32_t fan1, fan2, z, target, origin, travel, speed, started, duration;
static int deployed, simulated_trigger, switch_source, downward;
static enum motion_kind motion;
static enum result_kind result;

static void text(const char *value) {
    while (*value) platform_putchar(*value++);
}

static void send(const char *value) {
    text(value);
    platform_putchar('\n');
}

static void number(uint32_t value) {
    char buffer[10];
    unsigned count = 0;
    do {
        buffer[count++] = (char)('0' + value % 10);
        value /= 10;
    } while (value);
    while (count) platform_putchar(buffer[--count]);
}

static int same(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

static int is(const struct token *item, const char *value) {
    unsigned i = 0;
    while (i < item->length && value[i] && item->text[i] == value[i]) ++i;
    return i == item->length && value[i] == 0;
}

static void physical_off(void) {
    platform_safe_outputs();
    /* safe_outputs also removes PC14's pull-up; restore only this input. */
    bench_inputs_init();
}

static int triggered(void) {
    return switch_source ? bench_probe_raw() == 0 : simulated_trigger;
}

static void stop(enum result_kind reason) {
    motion = IDLE;
    target = z;
    fan1 = fan2 = 0;
    deployed = 0;
    result = reason;
    physical_off();
}

static void defaults(void) {
    fan1 = fan2 = 0;
    z = target = 10000;
    origin = travel = speed = started = duration = 0;
    deployed = simulated_trigger = switch_source = downward = 0;
    motion = IDLE;
    result = NONE;
    physical_off();
}

static void reject(const char *reason) {
    stop(ERROR);
    send(reason);
}

void application_tick(void) {
    if (motion == IDLE) return;
    uint32_t elapsed = platform_millis() - started;
    uint32_t distance = elapsed >= duration ? travel : elapsed * speed / 1000;
    if (distance > travel) distance = travel;
    z = downward ? origin - distance : origin + distance;
    /* A switch first sampled at/after the deadline cannot establish an earlier
     * contact time. Completion is NO_TRIGGER unless contact was observed in time. */
    if (motion == PROBE && elapsed < duration && triggered()) {
        motion = IDLE;
        target = z;
        result = CONTACT;
    } else if (elapsed >= duration) {
        result = motion == PROBE ? NO_TRIGGER : DONE;
        motion = IDLE;
        z = target;
    }
}

static void status(void) {
    static const char *const motions[] = {"IDLE", "MOVE", "PROBE"};
    static const char *const results[] = {"NONE", "DONE", "CONTACT", "NO_TRIGGER", "STOPPED", "ERROR"};
    text("BENCH FAN1="); number(fan1);
    text(" FAN2="); number(fan2);
    text(" PROBE="); text(deployed ? "DEPLOYED" : "STOWED");
    text(" TRIGGER="); number((uint32_t)triggered());
    text(" SOURCE="); text(switch_source ? "SWITCH" : "SIM");
    text(" Z_UM="); number(z);
    text(" TARGET_UM="); number(target);
    text(" MOTION="); text(motions[motion]);
    text(" RESULT="); text(results[result]);
    send(" OUTPUTS=DISABLED");
}

/* Exact single-space-separated tokens: reject omitted/trailing/extra tokens. */
static unsigned tokenize(struct token *items) {
    unsigned position = 0, count = 0;
    while (position < length) {
        if (line[position] == ' ' || count == 5) return 0;
        unsigned start = position;
        while (position < length && line[position] != ' ') ++position;
        items[count].text = line + start;
        items[count++].length = position - start;
        if (position < length && ++position == length) return 0;
    }
    return count;
}

static int unsigned_value(const struct token *item, uint32_t limit, uint32_t *out) {
    if (!item->length) return 0;
    uint32_t value = 0;
    for (unsigned i = 0; i < item->length; ++i) {
        char c = item->text[i];
        if (c < '0' || c > '9') return 0;
        uint32_t digit = (uint32_t)(c - '0');
        if (digit > limit || value > (limit - digit) / 10) return 0;
        value = value * 10 + digit;
    }
    *out = value;
    return 1;
}

static int delta_value(const struct token *item, uint32_t *distance, int *negative) {
    struct token magnitude = *item;
    *negative = 0;
    if (magnitude.length && (magnitude.text[0] == '-' || magnitude.text[0] == '+')) {
        *negative = magnitude.text[0] == '-';
        ++magnitude.text;
        --magnitude.length;
    }
    return unsigned_value(&magnitude, 10000, distance) && *distance != 0;
}

static int start_motion(const struct token *distance_token, const struct token *speed_token,
                        int probing) {
    uint32_t distance, velocity;
    int negative = 1;
    if (motion != IDLE) { reject("ERR BUSY"); return 0; }
    if ((probing && !deployed) || (!probing && deployed)) {
        reject("ERR PROBE_STATE"); return 0;
    }
    if (probing && triggered()) { reject("ERR TRIGGERED"); return 0; }
    if ((probing && (!unsigned_value(distance_token, 10000, &distance) || !distance)) ||
        (!probing && !delta_value(distance_token, &distance, &negative)) ||
        !unsigned_value(speed_token, 10000, &velocity) || velocity < 100) {
        reject("ERR RANGE"); return 0;
    }
    if ((negative && distance > z) || (!negative && distance > 200000 - z)) {
        reject("ERR BOUNDS"); return 0;
    }
    uint32_t time = (distance * 1000 + velocity - 1) / velocity;
    if (time > 30000) { reject("ERR DURATION"); return 0; }
    origin = z;
    target = negative ? z - distance : z + distance;
    travel = distance;
    speed = velocity;
    downward = negative;
    started = platform_millis();
    duration = time;
    motion = probing ? PROBE : MOVE;
    result = NONE;
    return 1;
}

static void sim_command(void) {
    struct token words[5];
    unsigned count = tokenize(words);
    uint32_t value, channel;
    if (count < 2 || !is(words, "SIM")) { reject("ERR UNSUPPORTED"); return; }
    if (count == 2 && is(words + 1, "RESET")) {
        if (motion != IDLE) { reject("ERR BUSY"); return; }
        defaults();
    } else if (count == 4 && is(words + 1, "FAN")) {
        if (!unsigned_value(words + 2, 2, &channel) || !channel ||
            !unsigned_value(words + 3, 100, &value)) { reject("ERR RANGE"); return; }
        if (channel == 1) fan1 = value; else fan2 = value;
    } else if (count == 3 && is(words + 1, "PROBE") &&
               (is(words + 2, "DEPLOY") || is(words + 2, "STOW"))) {
        if (motion != IDLE) { reject("ERR BUSY"); return; }
        deployed = is(words + 2, "DEPLOY");
    } else if (count == 3 && is(words + 1, "SOURCE") &&
               (is(words + 2, "SIM") || is(words + 2, "SWITCH"))) {
        if (motion != IDLE) { reject("ERR BUSY"); return; }
        switch_source = is(words + 2, "SWITCH");
    } else if (count == 3 && is(words + 1, "TRIGGER")) {
        if (switch_source) { reject("ERR SOURCE"); return; }
        if (!unsigned_value(words + 2, 1, &value)) { reject("ERR RANGE"); return; }
        simulated_trigger = (int)value;
        application_tick();
    } else if (count == 4 && is(words + 1, "Z") && is(words + 2, "SET")) {
        if (motion != IDLE) { reject("ERR BUSY"); return; }
        if (!unsigned_value(words + 3, 200000, &value)) { reject("ERR RANGE"); return; }
        z = target = value;
        result = NONE;
    } else if (count == 5 && is(words + 1, "Z") &&
               (is(words + 2, "MOVE") || is(words + 2, "PROBE"))) {
        if (!start_motion(words + 3, words + 4, is(words + 2, "PROBE"))) return;
    } else { reject("ERR UNSUPPORTED"); return; }
    text("OK ");
    send(line);
}

static void command(void) {
    if (same(line, "INFO") || same(line, "M115")) send(E3_APPLICATION_ID);
    else if (same(line, "STATUS")) status();
    else if (same(line, "INPUTS")) {
        text("INPUTS PROBE_PC14=");
        number(bench_probe_raw() ? 1U : 0U);
        platform_putchar('\n');
    } else if (same(line, "STOP") || same(line, "M5")) {
        stop(STOPPED);
        send(same(line, "STOP") ? "OK STOP" : "OK M5 OUTPUTS_OFF");
    } else if (same(line, "UPDATE")) {
        stop(STOPPED);
        send("OK UPDATE");
        platform_reset();
    } else sim_command();
}

void application_init(void) {
    length = 0;
    discard = 0;
    defaults();
}

void application_receive(int byte) {
    application_tick();
    if (byte == -1) return;
    if (byte < 0 || byte > 255) {
        stop(ERROR);
        discard = 1;
        length = 0;
        send("ERR UART");
        return;
    }
    if (byte == '\n') {
        if (discard) send("ERR LINE");
        else {
            if (length && line[length - 1] == '\r') --length;
            line[length] = 0;
            command();
        }
        discard = 0;
        length = 0;
    } else if (!discard) {
        if (length == E3_LINE_CAPACITY - 1 ||
            ((byte < 32 || byte > 126) && byte != '\r')) {
            stop(ERROR);
            discard = 1;
        } else line[length++] = (char)byte;
    }
}

#ifndef E3_CORE_TEST
int main(void) {
    platform_init();
    application_init();
    if (!platform_board_supported()) {
        send("ERR MCU_MISMATCH");
        for (;;) platform_service();
    }
    for (;;) {
        platform_service();
        application_receive(platform_getchar());
    }
}
#endif
