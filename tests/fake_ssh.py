#!/usr/bin/env python3
"""A stand-in for ssh for the host tests: the "host" is this machine with
$HOME at FAKE_SSH_HOME. ssh's options are taken and ignored, the command runs
with `sh -c` from that home (rsync's `-e` works through it too), and nothing
of the caller's BLENDERKC_ settings reaches it, as on a real host.

FAKE_SSH_FAIL names a file holding a number: while it is above 0, each call
takes one off, prints Tailscale SSH's check banner with a login URL, and
exits 255, as ssh did when the homeserver wanted a browser login.
FAKE_SSH_LOG, when set, gets one line per call: the command."""

import os
import subprocess
import sys

TAKES_VALUE = {"-o", "-p", "-i", "-l", "-F", "-J", "-b", "-c", "-m", "-E", "-S", "-W"}


def main(argv):
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in TAKES_VALUE else 1
    if i >= len(argv):
        print("fake_ssh: no host", file=sys.stderr)
        return 255
    command = " ".join(argv[i + 1:])
    if os.environ.get("FAKE_SSH_LOG"):
        with open(os.environ["FAKE_SSH_LOG"], "a") as fh:
            fh.write(command.replace("\n", " ") + "\n")
    fail = os.environ.get("FAKE_SSH_FAIL")
    if fail and os.path.exists(fail):
        n = int(open(fail).read().strip() or 0)
        if n > 0:
            open(fail, "w").write(str(n - 1))
            sys.stderr.write("# Tailscale SSH requires an additional check.\n"
                             f"# To authenticate, visit: https://login.tailscale.com/a/l{n:04d}fake\n")
            sys.stderr.write("Connection to 100.64.0.1 port 22 timed out\n")
            return 255
    home = os.environ["FAKE_SSH_HOME"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BLENDERKC_", "FAKE_SSH_"))}
    env["HOME"] = home
    return subprocess.run(["sh", "-c", command], cwd=home, env=env).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
