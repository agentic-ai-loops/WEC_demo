import os
import subprocess
import sys
import threading

from intelliw.pidfile import PidFile, terminate


def test_write_read_remove(tmp_path):
    pf = PidFile(tmp_path, "graphql")
    assert pf.read() is None
    pf.write()
    assert pf.read() == os.getpid()
    pf.remove_if_mine()
    assert not pf.path.exists()


def test_stale_pid_is_cleared(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    pf = PidFile(tmp_path, "mcp")
    pf.write(proc.pid)
    assert pf.read() is None
    assert not pf.path.exists()


def test_remove_if_mine_keeps_other_pid(tmp_path):
    pf = PidFile(tmp_path, "mcp")
    pf.write(1)
    pf.remove_if_mine()
    assert pf.path.exists()


def test_terminate(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    # reap in the background so the child doesn't linger as a zombie (which looks alive)
    threading.Thread(target=proc.wait, daemon=True).start()
    assert terminate(proc.pid, timeout=5)
