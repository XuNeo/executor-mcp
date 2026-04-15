#!/usr/bin/env python3
"""
executor-attach: Attach to a process managed by Claude Code's executor-mcp.

Finds the target process by PID or executor log, then bridges the current
terminal's stdin/stdout directly:
  - INPUT:  writes to the executor-mcp's pipe fd for the child's stdin
  - OUTPUT: tails the executor log file in real-time

This does NOT spawn a new executor-mcp instance. Claude Code's existing
executor session is fully preserved — you're just "tapping in" temporarily.

Usage:
    executor-attach                        # auto-discover from .executorlog/
    executor-attach <pid>                  # attach by child PID
    executor-attach -l <logfile>           # attach using log file directly

Press Ctrl-] to detach.
"""

import argparse
import fcntl
import glob
import os
import re
import select
import sys
import termios
import threading
import time
import tty


# ---------------------------------------------------------------------------
# Process discovery via /proc
# ---------------------------------------------------------------------------

def _scan_procs():
    """Single /proc scan — returns (executor_pids, all_procs).

    executor_pids: set of PIDs whose cmdline contains 'executor-mcp'
    all_procs:     {pid: (ppid, cmdline)}
    """
    executor_pids = set()
    all_procs = {}

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
            ppid = None
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split(":")[1].strip())
                        break
            all_procs[pid] = (ppid, cmdline)
            if "executor-mcp" in cmdline:
                executor_pids.add(pid)
        except OSError:
            continue

    return executor_pids, all_procs


def find_executor_children(executor_pids=None, all_procs=None):
    """Find all child processes of executor-mcp instances.

    Returns: {child_pid: (parent_pid, cmdline)}
    """
    if executor_pids is None or all_procs is None:
        executor_pids, all_procs = _scan_procs()

    children = {}
    for pid, (ppid, cmdline) in all_procs.items():
        if ppid in executor_pids and cmdline:
            children[pid] = (ppid, cmdline)
    return children


def find_log_dirs(executor_pids):
    """Discover all .executorlog/ directories from executor-mcp CWDs.

    Reads /proc/<pid>/cwd for each executor-mcp instance, collects unique
    .executorlog/ paths that exist on disk.
    """
    dirs = set()
    for pid in executor_pids:
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
            log_dir = os.path.join(cwd, ".executorlog")
            if os.path.isdir(log_dir):
                dirs.add(log_dir)
        except OSError:
            continue
    return sorted(dirs)


def find_stdin_write_fd(child_pid):
    """Find the pipe write-end fd in the parent that connects to child's stdin.

    Returns: (parent_pid, fd_number, error_msg)
    """
    try:
        stdin_link = os.readlink(f"/proc/{child_pid}/fd/0")
        if not stdin_link.startswith("pipe:"):
            return None, None, f"child stdin is not a pipe: {stdin_link}"
        pipe_inode = stdin_link

        ppid = None
        with open(f"/proc/{child_pid}/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    ppid = int(line.split(":")[1].strip())
                    break
        if not ppid:
            return None, None, "could not find parent PID"

        fd_dir = f"/proc/{ppid}/fd"
        for fd_name in os.listdir(fd_dir):
            try:
                if os.readlink(f"{fd_dir}/{fd_name}") == pipe_inode:
                    return ppid, int(fd_name), None
            except OSError:
                continue

        return None, None, f"pipe {pipe_inode} not found in parent {ppid}'s fds"
    except OSError as e:
        return None, None, str(e)


def parse_log_header(log_path):
    """Parse executor log header → {process_id, command, log}."""
    info = {"log": log_path, "process_id": None, "command": None}
    try:
        with open(log_path) as f:
            for line in f:
                if line.startswith("Process ID:"):
                    info["process_id"] = line.split(":", 1)[1].strip()
                elif line.startswith("Command:"):
                    info["command"] = line.split(":", 1)[1].strip()
                elif line.startswith("===") and info["command"]:
                    break
    except OSError:
        pass
    return info


def discover_processes(log_dirs):
    """Discover active executor-managed processes, deduped by PID.

    Single /proc scan, then match children against log files across all
    discovered .executorlog/ directories.

    log_dirs: list of directory paths to search for log files.
    """
    executor_pids, all_procs = _scan_procs()
    children = find_executor_children(executor_pids, all_procs)
    if not children:
        return []

    # Auto-discover additional log dirs from executor-mcp CWDs
    auto_dirs = find_log_dirs(executor_pids)
    all_dirs = list(dict.fromkeys(list(log_dirs) + auto_dirs))  # dedup, preserve order

    # Collect all log files across directories, sorted by mtime (newest first)
    all_logs = []
    for d in all_dirs:
        all_logs.extend(glob.glob(os.path.join(d, "*.log")))
    all_logs.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    # Match children to most recent log file
    pid_to_entry = {}
    for log_path in all_logs:
        info = parse_log_header(log_path)
        if not info["command"]:
            continue
        cmd_base = os.path.basename(info["command"].split()[0])
        for pid, (ppid, cmdline) in children.items():
            if pid in pid_to_entry:
                continue
            if cmd_base and cmd_base in cmdline:
                pid_to_entry[pid] = {
                    "pid": pid, "ppid": ppid, "cmdline": cmdline,
                    "log": log_path, "process_id": info["process_id"],
                    "command": info["command"],
                }

    return list(pid_to_entry.values())


def find_log_for_pid(child_pid, log_dirs=None):
    """Find the most recent executor log matching a child PID.

    Searches across all provided log_dirs plus auto-discovered ones.
    """
    try:
        with open(f"/proc/{child_pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
    except OSError:
        return None

    cmd_base = os.path.basename(cmdline.split()[0]) if cmdline else ""
    if not cmd_base:
        return None

    # Collect search dirs
    search_dirs = list(log_dirs or [])
    executor_pids, _ = _scan_procs()
    search_dirs.extend(find_log_dirs(executor_pids))
    search_dirs = list(dict.fromkeys(search_dirs))  # dedup

    all_logs = []
    for d in search_dirs:
        if os.path.isdir(d):
            all_logs.extend(glob.glob(os.path.join(d, "*.log")))
    all_logs.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    for log_path in all_logs:
        info = parse_log_header(log_path)
        if info["command"] and cmd_base in info["command"]:
            return log_path
    return None


# ---------------------------------------------------------------------------
# Interactive attach
# ---------------------------------------------------------------------------

_LOG_LINE_RE = re.compile(
    r"^\[[\d\-: .]+\] (STDOUT|STDERR|STDIN|TERMINATED|COMMAND)([>:])\s?(.*)$"
)


class Attacher:
    """Bridge terminal stdin/stdout to a running executor-managed process.

    - Input:  terminal raw stdin → os.write() to parent's pipe fd
    - Output: tail -f on executor log file, filtering STDOUT/STDERR lines
    """

    def __init__(self, child_pid, parent_pid, write_fd_num, log_path):
        self.child_pid = child_pid
        self.parent_pid = parent_pid
        self.write_fd_num = write_fd_num
        self.log_path = log_path
        self.running = True
        self._old_termios = None
        self._raw_mode = False
        self._pipe_fd = None

    def attach(self):
        fd_path = f"/proc/{self.parent_pid}/fd/{self.write_fd_num}"
        try:
            self._pipe_fd = os.open(fd_path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            print(f"Error: cannot open {fd_path}: {e}", file=sys.stderr)
            return

        flags = fcntl.fcntl(self._pipe_fd, fcntl.F_GETFL)
        fcntl.fcntl(self._pipe_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

        print(f"\033[32m[attached to PID {self.child_pid}] "
              f"log={self.log_path}\033[0m")
        print(f"\033[32m[press Ctrl-] to detach]\033[0m")

        stdin_fd = sys.stdin.fileno()
        try:
            self._old_termios = termios.tcgetattr(stdin_fd)
        except termios.error:
            self._old_termios = None

        if self._old_termios:
            tty.setraw(stdin_fd)
            self._raw_mode = True

        tailer = threading.Thread(target=self._tail_log, daemon=True)
        tailer.start()

        try:
            self._input_loop(stdin_fd)
        finally:
            self.running = False
            if self._old_termios:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, self._old_termios)
                self._raw_mode = False
            if self._pipe_fd is not None:
                os.close(self._pipe_fd)
            print(f"\n\033[32m[detached from PID {self.child_pid}]\033[0m")

    def _tail_log(self):
        """Background: tail executor log, showing STDOUT/STDERR lines."""
        try:
            with open(self.log_path, "r") as f:
                # Show last ~20 output lines for context
                f.seek(0, 2)
                start = max(0, f.tell() - 8192)
                f.seek(start)
                if start > 0:
                    f.readline()  # skip partial
                recent = f.readlines()
                output_lines = [l for l in recent if self._is_output(l)]
                for line in output_lines[-20:]:
                    self._show(line)

                # Live tail
                while self.running:
                    line = f.readline()
                    if line:
                        if self._is_output(line):
                            self._show(line)
                        elif "TERMINATED" in line:
                            self._write(f"\r\n\033[33m[process terminated]\033[0m\r\n")
                            self.running = False
                            break
                    else:
                        try:
                            os.kill(self.child_pid, 0)
                        except ProcessLookupError:
                            self._write(f"\r\n\033[33m[process exited]\033[0m\r\n")
                            self.running = False
                            break
                        time.sleep(0.05)
        except Exception as e:
            self._write(f"\r\n\033[31m[log error: {e}]\033[0m\r\n")

    def _is_output(self, line):
        m = _LOG_LINE_RE.match(line)
        return m and m.group(1) in ("STDOUT", "STDERR")

    def _show(self, line):
        m = _LOG_LINE_RE.match(line)
        if m:
            sep = m.group(2)      # ':' = complete line, '>' = partial (prompt)
            content = m.group(3)
            if sep == ":":
                # Complete line — ensure newline
                if not content.endswith("\n"):
                    content += "\n"
            # Partial ('>') — no added newline, show as-is (e.g. ">>> ")
            self._write(content)

    def _write(self, text):
        if not text:
            return
        if self._raw_mode:
            text = text.replace('\n', '\r\n')
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:
            pass

    def _input_loop(self, stdin_fd):
        """Line-buffered input with local echo.

        Keystrokes are echoed locally and buffered until Enter,
        then the complete line is sent to the process.
        """
        line_buf = bytearray()

        while self.running:
            readable, _, _ = select.select([stdin_fd], [], [], 0.1)
            if not readable:
                try:
                    os.kill(self.child_pid, 0)
                except ProcessLookupError:
                    self.running = False
                    break
                continue

            data = os.read(stdin_fd, 4096)
            if not data:
                self.running = False
                break

            for byte in data:
                if byte == 0x1d:  # Ctrl-]
                    self.running = False
                    return
                elif byte in (0x0d, 0x0a):  # Enter
                    self._echo("\r\n")
                    line = line_buf.decode("utf-8", errors="replace") + "\n"
                    try:
                        os.write(self._pipe_fd, line.encode())
                    except OSError as e:
                        self._echo(f"\033[31m[write error: {e}]\033[0m\r\n")
                        self.running = False
                        return
                    line_buf.clear()
                elif byte in (0x7f, 0x08):  # Backspace / DEL
                    if line_buf:
                        line_buf.pop()
                        self._echo("\b \b")
                elif byte == 0x03:  # Ctrl-C
                    line_buf.clear()
                    self._echo("^C\r\n")
                elif byte == 0x04:  # Ctrl-D
                    if not line_buf:
                        self._echo("^D\r\n")
                        # Send empty (EOF) — but don't close pipe
                    else:
                        # Send current buffer as-is
                        line = line_buf.decode("utf-8", errors="replace") + "\n"
                        try:
                            os.write(self._pipe_fd, line.encode())
                        except OSError:
                            pass
                        line_buf.clear()
                        self._echo("\r\n")
                elif byte == 0x15:  # Ctrl-U: clear line
                    if line_buf:
                        self._echo("\r\033[K")  # move to start, clear line
                        line_buf.clear()
                elif byte >= 0x20:  # Printable
                    line_buf.append(byte)
                    self._echo(chr(byte))

    def _echo(self, text):
        """Write directly to terminal (no newline conversion)."""
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def attach_by_pid(pid, log_path=None, log_dirs=None):
    """Attach to a process by PID."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        print(f"Error: PID {pid} not found", file=sys.stderr)
        return False

    ppid, write_fd, err = find_stdin_write_fd(pid)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return False

    if not log_path:
        log_path = find_log_for_pid(pid, log_dirs)
    if not log_path:
        print(f"Error: no log file found for PID {pid}", file=sys.stderr)
        print(f"Use -l <logfile> to specify", file=sys.stderr)
        return False

    print(f"PID: {pid}  Parent: {ppid}  Write FD: {write_fd}")
    print(f"Log: {log_path}")
    Attacher(pid, ppid, write_fd, log_path).attach()
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Attach to a process managed by Claude Code's executor-mcp",
        epilog="Press Ctrl-] to detach.\n\n"
               "Examples:\n"
               "  executor-attach                    # auto-discover\n"
               "  executor-attach 12345              # attach by PID\n"
               "  executor-attach -l .executorlog/xxx.log\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pid", nargs="?", type=int, help="Child process PID")
    parser.add_argument("-l", "--log", metavar="FILE", help="Executor log file")
    parser.add_argument("-d", "--logdir", action="append", default=[],
                        metavar="DIR", help="Extra log directory to search (repeatable)")
    args = parser.parse_args()

    log_dirs = args.logdir  # explicit dirs; auto-discovery adds more

    if args.pid:
        attach_by_pid(args.pid, args.log, log_dirs)
        return

    if args.log:
        info = parse_log_header(args.log)
        children = find_executor_children()
        cmd_base = os.path.basename(info["command"].split()[0]) if info.get("command") else ""
        matches = [(pid, ppid) for pid, (ppid, cmdline) in children.items()
                   if cmd_base and cmd_base in cmdline]
        if not matches:
            print(f"Error: no alive process for {info.get('command')}", file=sys.stderr)
            sys.exit(1)
        if len(matches) == 1:
            attach_by_pid(matches[0][0], args.log, log_dirs)
        else:
            print("Multiple matches:")
            for i, (pid, ppid) in enumerate(matches, 1):
                print(f"  [{i}] PID={pid}")
            try:
                idx = int(input("Select: ").strip()) - 1
                if 0 <= idx < len(matches):
                    attach_by_pid(matches[idx][0], args.log, log_dirs)
            except (ValueError, EOFError, KeyboardInterrupt):
                pass
        return

    # Auto-discover across all executor-mcp CWDs + explicit dirs
    processes = discover_processes(log_dirs)
    if not processes:
        print("No active executor-managed processes found.")
        sys.exit(0)

    if len(processes) == 1:
        p = processes[0]
        attach_by_pid(p["pid"], p["log"], log_dirs)
        return

    print("Active executor-managed processes:")
    for i, p in enumerate(processes, 1):
        print(f"  [{i}] PID={p['pid']}  {p['cmdline']}")
        print(f"      log: {p['log']}")

    try:
        choice = input("\nSelect number (Enter to quit): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not choice:
        return
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(processes):
            p = processes[idx]
            attach_by_pid(p["pid"], p["log"], log_dirs)
        else:
            print("Invalid selection")
    except ValueError:
        # Might be a PID directly
        try:
            attach_by_pid(int(choice), log_dirs=log_dirs)
        except ValueError:
            print("Invalid input")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted")
