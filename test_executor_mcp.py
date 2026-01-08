#!/usr/bin/env python3
"""
Simple test script for Executor MCP Server.
Tests basic functionality without requiring full MCP inspector.
"""

import asyncio
import sys
import json
from pathlib import Path

# Add parent directory to path to import executor_mcp
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


async def test_basic_workflow():
    """Test basic workflow: start -> send -> read -> stop"""
    print("=" * 60)
    print("Test 1: Basic Workflow with Python REPL")
    print("=" * 60)

    # Test 1: Start Python
    print("\n1. Starting Python REPL...")
    start_params = StartProcessInput(
        command="python3", args=["-i", "-u"]  # -u for unbuffered output
    )
    result = await executor_start(start_params)
    result_json = json.loads(result)
    print(f"✓ Started: {result_json}")

    if not result_json.get("success"):
        print("✗ Failed to start process")
        return False

    process_id = result_json["process_id"]

    # Wait a moment for Python to start
    await asyncio.sleep(0.5)

    # Test 2: Read initial output
    print(f"\n2. Reading initial output from process {process_id}...")
    read_params = ReadOutputInput(process_id=process_id, stream="stdout")
    result = await executor_read_output(read_params)
    result_json = json.loads(result)
    print(f"✓ Output: {result_json.get('lines_returned')} lines")
    if result_json.get("output"):
        print(f"   First few lines: {result_json['output'][:3]}")

    # Test 3: Send a command
    print(f"\n3. Sending command to process {process_id}...")
    send_params = SendInputInput(
        process_id=process_id, text="print('Hello from Executor MCP!')", add_newline=True
    )
    result = await executor_send(send_params)
    result_json = json.loads(result)
    print(f"✓ Sent: {result_json}")

    # Wait for output
    await asyncio.sleep(0.3)

    # Test 4: Read the response
    print(f"\n4. Reading response...")
    read_params = ReadOutputInput(process_id=process_id, tail_lines=5, stream="stdout")
    result = await executor_read_output(read_params)
    result_json = json.loads(result)
    print(f"✓ Response: {result_json.get('lines_returned')} lines")
    if result_json.get("output"):
        print(f"   Output: {result_json['output']}")

    # Test 5: List processes
    print(f"\n5. Listing all processes...")
    result = await executor_list()
    result_json = json.loads(result)
    print(f"✓ Found {result_json.get('count')} process(es)")

    # Test 6: Get detailed info
    print(f"\n6. Getting detailed info for process {process_id}...")
    info_params = ProcessIdInput(process_id=process_id)
    result = await executor_get_info(info_params)
    result_json = json.loads(result)
    print(f"✓ Process running: {result_json.get('is_running')}")
    print(f"   PID: {result_json.get('pid')}")
    print(f"   Buffered lines (stdout): {result_json.get('stdout_lines_buffered')}")

    # Test 7: Stop the process
    print(f"\n7. Stopping process {process_id}...")
    stop_params = StopProcessInput(process_id=process_id, force=False)
    result = await executor_stop(stop_params)
    result_json = json.loads(result)
    print(f"✓ Stopped: {result_json}")

    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
    return True


async def test_echo_command():
    """Test with simple echo command"""
    print("\n" + "=" * 60)
    print("Test 2: Simple Echo Command")
    print("=" * 60)

    # Start cat (reads stdin and echoes to stdout)
    print("\n1. Starting cat command...")
    start_params = StartProcessInput(command="cat", args=[])
    result = await executor_start(start_params)
    result_json = json.loads(result)
    print(f"✓ Started: {result_json}")

    if not result_json.get("success"):
        print("✗ Failed to start process")
        return False

    process_id = result_json["process_id"]

    await asyncio.sleep(0.2)

    # Send some text
    print(f"\n2. Sending text to cat...")
    send_params = SendInputInput(
        process_id=process_id, text="Hello, World!", add_newline=True
    )
    result = await executor_send(send_params)
    print(f"✓ Sent: {json.loads(result)}")

    await asyncio.sleep(0.2)

    # Read output
    print(f"\n3. Reading echoed output...")
    read_params = ReadOutputInput(process_id=process_id, stream="stdout")
    result = await executor_read_output(read_params)
    result_json = json.loads(result)
    print(f"✓ Output: {result_json.get('output')}")

    # Stop
    print(f"\n4. Stopping cat...")
    stop_params = StopProcessInput(process_id=process_id, force=True)
    await executor_stop(stop_params)
    print(f"✓ Stopped")

    print("\n" + "=" * 60)
    print("✓ Echo test passed!")
    print("=" * 60)
    return True


async def test_error_handling():
    """Test error handling"""
    print("\n" + "=" * 60)
    print("Test 3: Error Handling")
    print("=" * 60)

    # Test 1: Invalid command
    print("\n1. Testing invalid command...")
    start_params = StartProcessInput(command="/nonexistent/binary", args=[])
    result = await executor_start(start_params)
    result_json = json.loads(result)
    print(f"✓ Error handled: Contains 'not found': {'not found' in result.lower()}")

    # Test 2: Invalid process ID
    print("\n2. Testing invalid process ID...")
    send_params = SendInputInput(process_id="invalid_id_123", text="test")
    result = await executor_send(send_params)
    result_json = json.loads(result)
    print(f"✓ Error handled: {result_json.get('success') == False}")
    print(f"   Message: {result_json.get('error')}")

    print("\n" + "=" * 60)
    print("✓ Error handling tests passed!")
    print("=" * 60)
    return True


async def main():
    """Run all tests"""
    print("\n🧪 Executor MCP Server - Test Suite\n")

    try:
        # Run tests
        test1 = await test_basic_workflow()
        test2 = await test_echo_command()
        test3 = await test_error_handling()

        # Summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        print(f"Basic Workflow: {'✓ PASS' if test1 else '✗ FAIL'}")
        print(f"Echo Command: {'✓ PASS' if test2 else '✗ FAIL'}")
        print(f"Error Handling: {'✓ PASS' if test3 else '✗ FAIL'}")
        print("=" * 60)

        if all([test1, test2, test3]):
            print("\n🎉 All tests passed!\n")
            return 0
        else:
            print("\n❌ Some tests failed\n")
            return 1

    except Exception as e:
        print(f"\n❌ Test suite failed with error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
