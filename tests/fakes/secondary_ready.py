"""Supply a ready startup peer before existing runtime transcript fixtures.

Startup failure/timing is tested separately against the real owner in
test_secondary_startup.py. Runtime fixtures retain their exact command scripts.
"""


class ReadyMarlinTransport:
    def __init__(self, runtime):
        self.runtime = runtime
        self.startup_writes = []
        self.replies = []
        self.starting = True

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def synchronize_input(self):
        if not self.starting:
            self.runtime.synchronize_input()

    def write_line(self, line):
        if self.starting:
            assert line == "M115"
            self.startup_writes.append(line)
            self.replies = ["FIRMWARE_NAME:Marlin 2.0 MACHINE_TYPE:Ender-3 S1 Pro", "ok"]
        else:
            self.runtime.write_line(line)

    def read_line(self, timeout=1.0):
        if self.starting:
            reply = self.replies.pop(0)
            if reply == "ok":
                self.starting = False
            return reply
        return self.runtime.read_line(timeout)
