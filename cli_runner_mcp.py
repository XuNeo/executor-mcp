#!/usr/bin/env python3
"""
CLI Runner MCP Server

An MCP server for managing interactive CLI binary processes.
Enables AI to launch persistent binaries, send stdin commands, and read stdout responses.

Note: This MCP server uses stdio transport (for communication with MCP clients).
Don't confuse this with the stdin/stdout of the managed binary processes.
"""

import asyncio
import logging
import os
import pexpect
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Deque

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("cli_runner_mcp")

# Initialize FastMCP server
mcp = FastMCP("cli_runner_mcp")

# Global configuration
DEFAULT_BUFFER_SIZE = 1000  # Lines to keep in memory
LOG_DIR = Path(os.getenv("CLI_RUNNER_LOG_DIR", "./cli_runner_logs"))


@dataclass
class ProcessInfo:
    """Information about a managed process"""

    process_id: str
    command: str
    args: List[str]
    process: pexpect.spawn
    stdout_buffer: Deque[str] = field(
        default_factory=lambda: deque(maxlen=DEFAULT_BUFFER_SIZE)
    )
    log_file: Optional[Path] = None
    started_at: datetime = field(default_factory=datetime.now)
    reader_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        """Check if process is still running"""
        return self.process.isalive()


# Global process registry
_processes: Dict[str, ProcessInfo] = {}


# ============================================================================
# Shared Utilities
# ============================================================================


def _ensure_log_dir() -> Path:
    """Ensure log directory exists"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def _create_log_file(process_id: str, command: str) -> Path:
    """Create a new log file for a process"""
    log_dir = _ensure_log_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_command = command.replace("/", "_").replace(" ", "_")[:50]
    log_file = log_dir / f"{process_id}_{timestamp}_{safe_command}.log"

    # Write header
    with open(log_file, "w") as f:
        f.write(f"=== CLI Runner Process Log ===\n")
        f.write(f"Process ID: {process_id}\n")
        f.write(f"Command: {command}\n")
        f.write(f"Started: {datetime.now().isoformat()}\n")
        f.write(f"{'=' * 50}\n\n")

    return log_file


def _log_to_file(log_file: Path, prefix: str, content: str):
    """Append content to log file with timestamp and prefix"""
    try:
        with open(log_file, "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            f.write(f"[{timestamp}] {prefix}: {content}")
            if not content.endswith("\n"):
                f.write("\n")
    except Exception as e:
        logger.error(f"Failed to write to log file {log_file}: {e}")


async def _read_pexpect_output(
    process: pexpect.spawn, buffer: Deque[str], log_file: Path, prefix: str
):
    """Background task to continuously read from a pexpect process"""
    loop = asyncio.get_event_loop()

    def read_chunk():
        """Helper to read data from pexpect process"""
        try:
            # Since we used encoding='utf-8', this returns a string
            return process.read_nonblocking(size=4096, timeout=0.1)
        except pexpect.TIMEOUT:
            return None
        except pexpect.EOF:
            return ""  # Empty string signals EOF

    try:
        while process.isalive():
            try:
                # Read data from process
                data = await loop.run_in_executor(None, read_chunk)

                if data is None:
                    # Timeout, no data available
                    await asyncio.sleep(0.05)
                    continue

                if data == "":
                    # EOF
                    logger.info(f"Process {prefix} ended (EOF)")
                    break

                # Data is already a string due to encoding='utf-8'
                # Split into lines for buffering
                lines = data.split("\n")
                for i, line in enumerate(lines):
                    if i < len(lines) - 1:
                        # Not the last item, add newline
                        line_with_newline = line + "\n"
                        buffer.append(line_with_newline)
                        _log_to_file(log_file, prefix, line_with_newline)
                    elif line:
                        # Last item and not empty (partial line)
                        buffer.append(line)
                        _log_to_file(log_file, prefix, line)

            except Exception as e:
                logger.error(f"Error reading {prefix}: {e}")
                await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        logger.info(f"Reader task cancelled for {prefix}")
    except Exception as e:
        logger.error(f"Error in reader {prefix}: {e}")




# ============================================================================
# Pydantic Models for Tool Input Validation
# ============================================================================


class StartProcessInput(BaseModel):
    """Input schema for starting a process"""

    command: str = Field(
        ..., description="Path to the binary to execute", min_length=1, max_length=1000
    )
    args: List[str] = Field(
        default_factory=list, description="Command-line arguments for the binary"
    )
    working_dir: Optional[str] = Field(
        default=None,
        description="Working directory for the process (defaults to current directory)",
    )

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Command cannot be empty")
        return v.strip()


class SendInputInput(BaseModel):
    """Input schema for sending input to a process"""

    process_id: str = Field(
        ..., description="The process ID returned from cli_runner_start"
    )
    text: str = Field(..., description="Text to send to the process stdin")
    add_newline: bool = Field(
        default=True,
        description="Whether to append a newline character (default: true)",
    )


class ReadOutputInput(BaseModel):
    """Input schema for reading process output"""

    process_id: str = Field(..., description="The process ID to read output from")
    tail_lines: Optional[int] = Field(
        default=None,
        ge=1,
        le=10000,
        description="Number of recent lines to return (default: all buffered lines)",
    )
    stream: str = Field(
        default="stdout",
        description="Which stream to read: 'stdout', 'stderr', or 'both'",
    )

    @field_validator("stream")
    @classmethod
    def validate_stream(cls, v: str) -> str:
        if v not in ["stdout", "stderr", "both"]:
            raise ValueError("stream must be 'stdout', 'stderr', or 'both'")
        return v


class StopProcessInput(BaseModel):
    """Input schema for stopping a process"""

    process_id: str = Field(..., description="The process ID to stop")
    force: bool = Field(
        default=False, description="If true, use SIGKILL instead of SIGTERM"
    )


class ProcessIdInput(BaseModel):
    """Input schema for operations requiring only a process ID"""

    process_id: str = Field(..., description="The process ID")


# ============================================================================
# MCP Tools
# ============================================================================


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    }
)
async def cli_runner_start(params: StartProcessInput) -> str:
    """
    Start a new interactive binary process.

    Launches a binary in the background and returns a process_id for subsequent
    interactions. The process runs persistently, allowing multiple stdin/stdout
    exchanges over time.

    Returns: JSON with process_id, command, pid, and log_file path.
    """
    try:
        # Generate unique process ID
        process_id = str(uuid.uuid4())[:8]

        # Create log file
        log_file = _create_log_file(process_id, params.command)

        # Set up working directory
        cwd = Path(params.working_dir) if params.working_dir else None
        if cwd and not cwd.exists():
            return _handle_error(
                FileNotFoundError(), f"Working directory does not exist: {cwd}"
            )

        # Start the process with pexpect (automatically uses PTY)
        full_command = [params.command] + params.args
        command_str = " ".join(full_command)
        _log_to_file(log_file, "COMMAND", command_str)

        # Use pexpect.spawn to start the process with PTY support
        process = pexpect.spawn(
            params.command,
            args=params.args,
            cwd=str(cwd) if cwd else None,
            encoding='utf-8',
            codec_errors='replace',
            timeout=None,  # Don't timeout on reads
        )

        # Create process info
        proc_info = ProcessInfo(
            process_id=process_id,
            command=params.command,
            args=params.args,
            process=process,
            log_file=log_file,
        )

        # Start background task to read output
        proc_info.reader_task = asyncio.create_task(
            _read_pexpect_output(process, proc_info.stdout_buffer, log_file, "OUTPUT")
        )

        # Register process
        _processes[process_id] = proc_info

        logger.info(
            f"Started process {process_id}: {params.command} (PID: {process.pid})"
        )

        return f"Started process {process_id} (PID: {process.pid})"

    except FileNotFoundError:
        return f"Error: Command not found: {params.command}"
    except Exception as e:
        return f"Error: Failed to start process: {str(e)}"


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    }
)
async def cli_runner_send(params: SendInputInput) -> str:
    """
    Send text to a process's stdin.

    Writes the provided text to the process's standard input, optionally followed
    by a newline. This is how you interact with the running binary.

    Returns: JSON with success status and confirmation message.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"Error: Process not found: {params.process_id}"

        if not proc_info.is_running:
            return f"Error: Process {params.process_id} has terminated"

        # Prepare text to send
        text_to_send = params.text

        # Send to process (pexpect handles encoding)
        loop = asyncio.get_event_loop()
        if params.add_newline:
            await loop.run_in_executor(None, proc_info.process.sendline, text_to_send)
            text_logged = text_to_send + "\n"
        else:
            await loop.run_in_executor(None, proc_info.process.send, text_to_send)
            text_logged = text_to_send

        # Log the input
        _log_to_file(proc_info.log_file, "STDIN", text_logged)

        logger.info(
            f"Sent input to process {params.process_id}: {repr(params.text[:50])}"
        )

        return "OK"

    except BrokenPipeError:
        return f"Error: Process {params.process_id} stdin is closed"
    except Exception as e:
        return f"Error: Failed to send input: {str(e)}"


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def cli_runner_read_output(params: ReadOutputInput) -> str:
    """
    Read output from a process's stdout/stderr buffer.

    Retrieves recent output lines from the process. Output is continuously captured
    in memory buffers (max 1000 lines per stream). For complete history, check the
    log file.

    Returns: JSON with output lines and metadata.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"Error: Process not found: {params.process_id}"

        # Collect output (pexpect combines stdout/stderr via PTY)
        output_lines = list(proc_info.stdout_buffer)
        if params.tail_lines:
            output_lines = output_lines[-params.tail_lines :]

        # Clear the buffer after reading so next read gets only new output
        proc_info.stdout_buffer.clear()

        # Return plain text with real newlines
        if not output_lines:
            return "(no output)"

        # Join lines - they already have \r\n, so just concatenate
        return "".join(output_lines)

    except Exception as e:
        return f"Error: Failed to read output: {str(e)}"


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
    }
)
async def cli_runner_stop(params: StopProcessInput) -> str:
    """
    Stop a running process.

    Terminates the process using SIGTERM (graceful) or SIGKILL (force).
    The process will be removed from the active process list.

    Returns: JSON with termination status and final output summary.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"Error: Process not found: {params.process_id}"

        if not proc_info.is_running:
            return f"Process {params.process_id} already terminated"

        # Terminate the process
        loop = asyncio.get_event_loop()

        if params.force:
            # Force kill
            await loop.run_in_executor(None, proc_info.process.kill, 9)
            method = "SIGKILL (forced)"
        else:
            # Graceful terminate
            await loop.run_in_executor(None, proc_info.process.terminate)
            method = "SIGTERM (graceful)"

            # Wait for process to finish with timeout
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, proc_info.process.wait),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                # Force kill if timeout
                await loop.run_in_executor(None, proc_info.process.kill, 9)
                await loop.run_in_executor(None, proc_info.process.wait)
                method += " -> SIGKILL (timeout)"

        # Cancel background reader task
        if proc_info.reader_task:
            proc_info.reader_task.cancel()
            try:
                await proc_info.reader_task
            except asyncio.CancelledError:
                pass

        # Close the process
        await loop.run_in_executor(None, proc_info.process.close)

        # Log termination
        _log_to_file(
            proc_info.log_file,
            "TERMINATED",
            f"Method: {method}, Exit status: {proc_info.process.exitstatus}",
        )

        logger.info(
            f"Stopped process {params.process_id} (PID: {proc_info.process.pid})"
        )

        # Remove from registry
        del _processes[params.process_id]

        return f"Stopped process {params.process_id}"

    except ProcessLookupError:
        return f"Error: Process {params.process_id} not found in system"
    except Exception as e:
        return f"Error: Failed to stop process: {str(e)}"


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def cli_runner_list() -> str:
    """
    List all active processes.

    Returns information about all currently managed processes, including their
    status, PIDs, and buffered output line counts.

    Returns: JSON array of process information.
    """
    try:
        if not _processes:
            return "(no active processes)"

        # Format as plain text, one process per line
        lines = []
        for proc_info in _processes.values():
            pid = proc_info.process.pid
            cmd = proc_info.command
            status = "running" if proc_info.is_running else "stopped"
            lines.append(f"{proc_info.process_id}: {cmd} (PID: {pid}, {status})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: Failed to list processes: {str(e)}"


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def cli_runner_get_info(params: ProcessIdInput) -> str:
    """
    Get detailed information about a specific process.

    Returns comprehensive information including recent output, buffer status,
    and log file location.

    Returns: JSON with detailed process information.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"Error: Process not found: {params.process_id}"

        # Format as plain text info block
        lines = [
            f"Process ID: {proc_info.process_id}",
            f"Command: {proc_info.command} {' '.join(proc_info.args)}",
            f"PID: {proc_info.process.pid}",
            f"Status: {'running' if proc_info.is_running else 'stopped'}",
            f"Started: {proc_info.started_at.isoformat()}",
            f"Buffered lines: {len(proc_info.stdout_buffer)}",
            f"Log file: {proc_info.log_file}",
        ]

        # Add recent output if available
        if proc_info.stdout_buffer:
            recent = list(proc_info.stdout_buffer)[-10:]
            lines.append(f"Recent output (last {len(recent)} lines):")
            for line in recent:
                lines.append(f"  {line.rstrip()}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: Failed to get info: {str(e)}"


# ============================================================================
# Entry Point
# ============================================================================


def main():
    """Main entry point for the MCP server"""
    logger.info("Starting CLI Runner MCP Server")
    logger.info(f"Log directory: {LOG_DIR}")
    logger.info(f"Buffer size: {DEFAULT_BUFFER_SIZE} lines")

    # Run the MCP server with stdio transport
    mcp.run()


if __name__ == "__main__":
    main()
