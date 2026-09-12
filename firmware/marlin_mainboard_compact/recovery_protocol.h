// Restricted, allocation-free parser used only after native kill has latched.
#pragma once
#include <stdint.h>

class E3RecoveryParser {
public:
  enum Action { NONE, RESET };
  typedef bool (*Sender)(const char *);
  E3RecoveryParser(uint32_t seed, Sender output)
    : token(seed), send(output), used(0), discard(false), armed(false) {}

  Action receive(int byte) {
    if (byte == -1) return NONE;
    if (byte < 0 || byte > 126 || (byte < 32 && byte != '\n' && byte != '\r')) {
      discard = true;
      armed = false;
      return NONE;
    }
    if (byte != '\n') {
      if (!discard) {
        if (used == sizeof(line) - 1) { discard = true; armed = false; }
        else line[used++] = (char)byte;
      }
      return NONE;
    }
    if (used && line[used - 1] == '\r') --used;
    line[used] = 0;
    const Action action = discard ? reject() : command();
    used = 0;
    discard = false;
    return action;
  }

private:
  uint32_t token;
  Sender send;
  char line[32];
  unsigned used;
  bool discard, armed;

  bool same(const char *word) const {
    unsigned n = 0;
    while (word[n] && n < used && word[n] == line[n]) ++n;
    return n == used && !word[n];
  }
  Action reject() {
    armed = false;
    send("Error:E3RECOVERY:1 REJECTED\nok\n");
    return NONE;
  }
  Action command() {
    if (!used) return NONE;
    if (same("M115")) {
      // Addition is bijective until wrap: each query invalidates the previous
      // challenge in this latched session. This is stale-input protection,
      // not cryptographic authentication or a safety-rated control.
      token += UINT32_C(0x9E3779B9);
      char reply[] = "E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:00000000\nok\n";
      const unsigned offset = sizeof("E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:") - 1;
      const char digits[] = "0123456789ABCDEF";
      for (unsigned n = 0; n < 8; ++n) reply[offset + n] = digits[(token >> (28 - 4 * n)) & 15];
      armed = send(reply);
      return NONE;
    }
    const char prefix[] = "E3RECOVER ";
    if (used != sizeof(prefix) - 1 + 8 || !armed) return reject();
    for (unsigned n = 0; n < sizeof(prefix) - 1; ++n)
      if (line[n] != prefix[n]) return reject();
    uint32_t requested = 0;
    for (unsigned n = sizeof(prefix) - 1; n < used; ++n) {
      const char byte = line[n];
      if (!((byte >= '0' && byte <= '9') || (byte >= 'A' && byte <= 'F'))) return reject();
      requested = (requested << 4) | (uint32_t)(byte <= '9' ? byte - '0' : byte - 'A' + 10);
    }
    if (requested != token) return reject();
    armed = false; // Consume before acknowledgement; failed TX cannot reset.
    return send("E3RECOVERY:1 RESETTING\n") ? RESET : NONE;
  }
};
