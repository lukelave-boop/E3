"""Execute the production live-Z publisher in Cortex-M4 with fake I/O.

No controller is accessed. The harness checks the actual overlay, not a Python
copy, and deliberately models targets differently from executed step counters.
"""
from __future__ import annotations

from pathlib import Path

from run_recovery_tests import check_parser, compile_test

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

PREAMBLE = r'''
#include <stdint.h>
#include <stdarg.h>
#include <stddef.h>
extern "C" int strcmp(const char *a,const char *b) {
  while (*a && *a==*b) { ++a; ++b; } return *a-*b;
}
extern "C" void *memset(void *p,int v,size_t n) {
  for(size_t i=0;i<n;++i) ((char*)p)[i]=v; return p;
}
extern "C" void *memcpy(void *p,const void *s,size_t n) {
  for(size_t i=0;i<n;++i) ((char*)p)[i]=((const char*)s)[i]; return p;
}
static unsigned now, errors;
static bool trusted, moving;
static float executed_z, projected_z, received_z;
static uint32_t millis() { return now; }
enum {X_AXIS,Y_AXIS,Z_AXIS};
struct xyze_pos_t { float x,y,z,e; };
static struct {
  float get_axis_position_mm(int axis) { return axis==Z_AXIS ? executed_z : 0; }
  void unapply_modifiers(xyze_pos_t &p,bool) { p.z-=2; }
  bool has_blocks_queued() { return moving; }
} planner;
#define HAS_POSITION_MODIFIERS 1
#define TERN_(feature, expression) expression
#define LOGICAL_Z_POSITION(z) ((z)+1)
#define isfinite(v) __builtin_isfinite(v)
static bool axis_is_trusted(int) { return trusted; }
static struct { const char *command_ptr; } parser;
#define SERIAL_ERROR_MSG(s) (++errors)
struct GcodeSuite { static void M154(); };
static char captured[80];
static unsigned writes, capacity=63, emitted_known,emitted_homing,emitted_moving;
static unsigned long emitted_sequence;
static struct {
  int availableForWrite() { return capacity; }
  void write(const uint8_t *p,unsigned n) {
    if(n>capacity || n>=sizeof(captured)) { errors+=100; return; }
    for(unsigned i=0;i<n;++i) captured[i]=p[i]; captured[n]=0; ++writes;
  }
} MSerial1;
static void dtostrf(float value,int,unsigned,char *out) {
  received_z=value; out[0]='1';out[1]='.';out[2]='0';out[3]='0';out[4]='0';out[5]=0;
}
static int snprintf(char *out,unsigned count,const char *format,...) {
  if(strcmp(format,"E3Z:1 N:%lu Z:%s K:%u H:%u M:%u\n")) { errors+=1000; return -1; }
  va_list args; va_start(args,format);
  emitted_sequence=va_arg(args,unsigned long); (void)va_arg(args,const char*);
  emitted_known=va_arg(args,unsigned); emitted_homing=va_arg(args,unsigned);
  emitted_moving=va_arg(args,unsigned); va_end(args);
  // Exercise the production whole-frame TX capacity check with a 48-byte line.
  if(count<49) return -1;
  for(unsigned i=0;i<47;++i) out[i]='x';out[47]='\n';out[48]=0;return 48;
}
'''

TESTS = r'''
#define CHECK(v) do { if(!(v)) return __LINE__; } while(0)
extern "C" int test_main() {
  using namespace e3_live_z;
  reporter=Reporter(); now=100; writes=errors=0; trusted=true; moving=true;
  executed_z=12; projected_z=30; capacity=63;
  tick(); CHECK(writes==0); // disabled by default
  static const char *const bad[]={"M154","M154 S2","M154 S-1","M154 S1.0",
    "M154 S1 X0","M154 S01","M154 S1 ","m154 S1","M154 S0 S1"};
  for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);++i) {
    parser.command_ptr=bad[i]; GcodeSuite::M154(); CHECK(!reporter.enabled);
  }
  CHECK(errors==sizeof(bad)/sizeof(bad[0])); errors=0;
  parser.command_ptr="M154 S1"; GcodeSuite::M154(); CHECK(reporter.enabled);
  tick(); CHECK(writes==1 && received_z==11 && received_z!=projected_z);
  CHECK(emitted_known==1 && emitted_homing==0 && emitted_moving==1 && emitted_sequence==0);
  now=299; tick(); CHECK(writes==1);
  now=300; executed_z=13; tick(); CHECK(writes==2 && received_z==12 && emitted_sequence==1);
  { const HomingScope outer; now=500; executed_z=-4; tick();
    CHECK(emitted_homing==1 && emitted_known==0 && received_z==-5);
    { const HomingScope nested; CHECK(reporter.homing_depth==2); }
    CHECK(reporter.homing_depth==1);
  }
  CHECK(reporter.homing_depth==0);
  now=700; trusted=false; moving=false; tick(); CHECK(emitted_known==0 && emitted_homing==0 && emitted_moving==0);
  const unsigned final_idle=writes;
  now=900; tick(); CHECK(writes==final_idle); // final report followed by silence
  now=1100; executed_z=14; tick(); CHECK(writes==final_idle+1); // changed idle Z
  now=1300; trusted=true; tick(); CHECK(writes==final_idle+2 && emitted_known==1);
  now=1500; tick(); CHECK(writes==final_idle+2); // stable trusted idle is also quiet
  const unsigned previous=writes; const uint32_t seq=reporter.sequence;
  now=1700; moving=true; capacity=47; tick(); CHECK(writes==previous && reporter.sequence==seq);
  now=1701; capacity=63; tick(); CHECK(writes==previous); // no busy-loop retry
  now=1900; tick(); CHECK(writes==previous+1 && emitted_sequence==seq);
  parser.command_ptr="M154 S0"; GcodeSuite::M154(); now=2100; tick(); CHECK(writes==previous+1);
  CHECK(!reporter.published);
  moving=false; parser.command_ptr="M154 S1"; GcodeSuite::M154(); tick();
  CHECK(writes==previous+2); // re-enable reports unchanged idle once
  now=2300; tick(); CHECK(writes==previous+2);
  moving=true;
  parser.command_ptr="M154 S1"; now=0xFFFFFF80UL; GcodeSuite::M154(); tick();
  const unsigned wrapped=writes;
  now=0x47; tick(); CHECK(writes==wrapped);
  now=0x48; tick(); CHECK(writes==wrapped+1); // millis wrap
  reporter.sequence=0xFFFFFFFFUL; now+=200; tick(); CHECK(emitted_sequence==0xFFFFFFFFUL && reporter.sequence==0);
  now+=200; tick(); CHECK(emitted_sequence==0);
  executed_z=__builtin_nanf(""); now+=200; const unsigned finite=writes; tick(); CHECK(writes==finite);
  executed_z=20000000; now+=200; tick(); CHECK(writes==finite);
  CHECK(errors==0);
  return 0;
}
'''


def main() -> None:
    out = ROOT / "build/marlin-live-z-tests"
    out.mkdir(parents=True, exist_ok=True)
    production = (HERE / "e3_live_z.inc").read_text(encoding="utf-8")
    check_parser(compile_test(out, "live_z", PREAMBLE + production + TESTS))
    print("Production live-Z publisher: Cortex-M4 default-off, opcode rejection, executed steps, "
          "homing validity, 5 Hz/wrap, finite values and nonblocking TX tests passed; fake I/O only.")


if __name__ == "__main__":
    main()
