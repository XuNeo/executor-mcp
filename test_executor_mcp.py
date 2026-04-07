#!/usr/bin/env python3
"""
Simple test script for Executor MCP Server.
Tests basic functionality without requiring full MCP inspector.
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from executor_mcp import (
    executor_start,
    executor_send,
    executor_read_output,
    executor_list,
    executor_get_info,
    executor_stop,
    StartProcessInput,
    SendInputInput,
    ReadOutputInput,
    StopProcessInput,
    ProcessIdInput,
)


def parse_kv(text):
    """Parse 'key: value' lines into dict"""
    return dict(re.findall(r"^(\w+):\s*(.+)$", text, re.MULTILINE))


async def test_basic_workflow():
    print("=" * 60)
    print("Test 1: Basic Workflow with Python REPL")
    print("=" * 60)

    # Start Python
    print("\n1. Starting Python REPL...")
    result = await executor_start(StartProcessInput(command="python3", args=["-i", "-u"]))
    print(f"✓ {result}")
    kv = parse_kv(result)
    assert "process_id" in kv, f"Missing process_id: {result}"
    process_id = kv["process_id"]

    await asyncio.sleep(0.5)

    # Read initial output
    print(f"\n2. Reading initial output...")
    result = await executor_read_output(ReadOutputInput(process_id=process_id))
    print(f"✓ Output: {repr(result[:80])}")

    # Send command with auto-wait
    print(f"\n3. Sending print command...")
    result = await executor_send(SendInputInput(
        process_id=process_id, text="print('Hello from Executor MCP!')", wait_time=0.3
    ))
    print(f"✓ Output: {repr(result)}")
    assert "Hello from Executor MCP!" in result

    # Send without wait
    print(f"\n4. Sending without wait...")
    result = await executor_send(SendInputInput(
        process_id=process_id, text="x = 42", wait_time=0
    ))
    print(f"✓ Result: {result}")
    assert result == "ok"

    await asyncio.sleep(0.2)

    # Read output
    print(f"\n5. Reading output...")
    result = await executor_read_output(ReadOutputInput(process_id=process_id, tail_lines=5))
    print(f"✓ Output: {repr(result[:80])}")

    # List processes
    print(f"\n6. Listing processes...")
    result = await executor_list()
    print(f"✓ {result}")
    assert process_id in result

    # Get info
    print(f"\n7. Getting info...")
    result = await executor_get_info(ProcessIdInput(process_id=process_id))
    print(f"✓ {result[:120]}")
    assert "running" in result

    # Stop
    print(f"\n8. Stopping...")
    result = await executor_stop(StopProcessInput(process_id=process_id))
    print(f"✓ {result}")
    assert "stopped" in result

    print("\n✓ Basic workflow passed!")
    return True


async def test_echo_command():
    print("\n" + "=" * 60)
    print("Test 2: Echo Command")
    print("=" * 60)

    result = await executor_start(StartProcessInput(command="cat"))
    kv = parse_kv(result)
    process_id = kv["process_id"]

    await asyncio.sleep(0.2)

    result = await executor_send(SendInputInput(
        process_id=process_id, text="Hello, World!", wait_time=0.2
    ))
    print(f"✓ Echoed: {repr(result)}")
    assert "Hello, World!" in result

    await executor_stop(StopProcessInput(process_id=process_id, force=True))
    print("✓ Echo test passed!")
    return True


async def test_error_handling():
    print("\n" + "=" * 60)
    print("Test 3: Error Handling")
    print("=" * 60)

    result = await executor_start(StartProcessInput(command="/nonexistent/binary"))
    print(f"✓ Invalid cmd: {result}")
    assert "error:" in result

    result = await executor_send(SendInputInput(process_id="invalid_id", text="test", wait_time=0))
    print(f"✓ Invalid pid: {result}")
    assert "error:" in result or "not found" in result

    print("✓ Error handling passed!")
    return True


async def main():
    print("\n🧪 Executor MCP Server - Test Suite\n")
    try:
        t1 = await test_basic_workflow()
        t2 = await test_echo_command()
        t3 = await test_error_handling()

        print("\n" + "=" * 60)
        print(f"Basic: {'PASS' if t1 else 'FAIL'} | Echo: {'PASS' if t2 else 'FAIL'} | Errors: {'PASS' if t3 else 'FAIL'}")
        print("=" * 60)
        return 0 if all([t1, t2, t3]) else 1

    except Exception as e:
        print(f"\n❌ Failed: {e}")
        import traceback; traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
