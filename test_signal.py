#!/usr/bin/env python3
"""Test if picocom propagates signals to parent process group.
Uses a pty pair to give picocom a real device to hold open."""
import asyncio, signal, os, pty

got = []
for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGPIPE):
    signal.signal(sig, lambda s, f: (got.append(s), print("PARENT GOT:", signal.Signals(s).name)))

async def test_picocom(new_session):
    label = "WITH" if new_session else "WITHOUT"
    print(f"\n=== {label} start_new_session ===")

    # Create a pty pair so picocom has a real tty to open
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    print(f"Using pty: {slave_name}")

    proc = await asyncio.create_subprocess_exec(
        "picocom", "-b", "115200", slave_name,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=new_session,
    )
    await asyncio.sleep(1)

    if proc.returncode is not None:
        print(f"picocom exited early with code {proc.returncode}")
        os.close(master_fd)
        os.close(slave_fd)
        return False

    print(f"Parent pid={os.getpid()} pgid={os.getpgid(0)}")
    print(f"Child  pid={proc.pid} pgid={os.getpgid(proc.pid)}")

    got.clear()
    print(f"Sending SIGTERM to child pid={proc.pid}...")
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
    await asyncio.sleep(0.3)
    names = [signal.Signals(s).name for s in got]
    none = "NONE"
    print(f"Child exit code: {proc.returncode}")
    print(f"Parent received signals: {names if names else none}")

    os.close(master_fd)
    os.close(slave_fd)
    return len(got) > 0

async def main():
    hit_without = await test_picocom(False)
    hit_with = await test_picocom(True)
    print(f"\nResult: without_session signal_hit={hit_without}, with_session signal_hit={hit_with}")

asyncio.run(main())
