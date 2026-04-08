#!/usr/bin/env python3
"""
Executor MCP Server

An MCP server for managing interactive CLI binary processes.
Enables AI to launch persistent binaries, send stdin commands, and read stdout responses.

Note: This MCP server uses stdio transport (for communication with MCP clients).
Don't confuse this with the stdin/stdout of the managed binary processes.
"""

import asyncio
import logging
import os
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
logger = logging.getLogger("executor_mcp")

# Initialize FastMCP server
mcp = FastMCP("executor_mcp")

# Global configuration
DEFAULT_BUFFER_SIZE = 1000  # Lines to keep in memory
LOG_DIR = Path(os.getenv("EXECUTOR_LOG_DIR", ".executorlog"))


@dataclass
class ProcessInfo:
    """Information about a managed process"""

    process_id: str
    command: str
    args: List[str]
    process: asyncio.subprocess.Process
    stdout_buffer: Deque[str] = field(
        default_factory=lambda: deque(maxlen=DEFAULT_BUFFER_SIZE)
    )
    stderr_buffer: Deque[str] = field(
        default_factory=lambda: deque(maxlen=DEFAULT_BUFFER_SIZE)
    )
    merged_buffer: Deque[str] = field(
        default_factory=lambda: deque(maxlen=DEFAULT_BUFFER_SIZE)
    )
    log_file: Optional[Path] = None
    started_at: datetime = field(default_factory=datetime.now)
    stdout_task: Optional[asyncio.Task] = None
    stderr_task: Optional[asyncio.Task] = None
    total_lines_seen: int = 0

    @property
    def is_running(self) -> bool:
        """Check if process is still running"""
        return self.process.returncode is None


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
        f.write(f"=== Executor MCP Process Log ===\n")
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


async def _read_stream(
    stream: asyncio.StreamReader,
    buffer: Deque[str],
    log_file: Path,
    prefix: str,
    merged_buffer: Optional[Deque[str]] = None,
):
    """Background task to continuously read from a stream"""
    try:
        while True:
            line = await stream.readline()
            if not line:
                break

            decoded = line.decode("utf-8", errors="replace")
            buffer.append(decoded)
            _log_to_file(log_file, prefix, decoded)

            if merged_buffer is not None:
                merged_buffer.append(decoded)

    except asyncio.CancelledError:
        logger.info(f"Stream reader task cancelled for {prefix}")
    except Exception as e:
        logger.error(f"Error reading {prefix}: {e}")



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
        ..., description="The process ID returned from executor_start"
    )
    text: str = Field(..., description="Text to send to the process stdin")
    add_newline: bool = Field(
        default=True,
        description="Whether to append a newline character (default: true)",
    )
    wait_time: Optional[float] = Field(
        default=0.1,
        ge=0,
        le=10.0,
        description="Seconds to wait before reading output (0 = no wait, no output returned)",
    )
    tail_lines: Optional[int] = Field(
        default=20,
        ge=1,
        le=1000,
        description="Number of recent output lines to return when wait_time > 0",
    )
    full_buffer: bool = Field(
        default=False,
        description="If True, return last tail_lines from full buffer. If False (default), return only new output generated after this send.",
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
        default="both",
        description="Which stream to read from: 'stdout', 'stderr', or 'both' (default: 'both' merges stdout/stderr)",
    )

    @field_validator("stream")
    @classmethod
    def validate_stream(cls, v: str) -> str:
        valid_streams = ["stdout", "stderr", "both"]
        if v not in valid_streams:
            raise ValueError(f"stream must be one of {valid_streams}")
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
async def executor_start(params: StartProcessInput) -> str:
    """
    Start a new interactive binary process.

    Launches a binary in the background and returns a process_id for subsequent
    interactions. The process runs persistently, allowing multiple stdin/stdout
    exchanges over time.

    Returns: process_id, pid, and log_file path as plain text.
    """
    try:
        # Generate unique process ID
        process_id = str(uuid.uuid4())[:8]

        # Create log file
        log_file = _create_log_file(process_id, params.command)

        # Set up working directory
        cwd = Path(params.working_dir) if params.working_dir else None
        if cwd and not cwd.exists():
            return f"error: working directory does not exist: {cwd}"

        # Start the process
        full_command = [params.command] + params.args
        _log_to_file(log_file, "COMMAND", " ".join(full_command))

        process = await asyncio.create_subprocess_exec(
            *full_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )

        # Create process info
        proc_info = ProcessInfo(
            process_id=process_id,
            command=params.command,
            args=params.args,
            process=process,
            log_file=log_file,
        )

        # Start background tasks to read stdout/stderr
        proc_info.stdout_task = asyncio.create_task(
            _read_stream(
                process.stdout,
                proc_info.stdout_buffer,
                log_file,
                "STDOUT",
                proc_info.merged_buffer,
            )
        )
        proc_info.stderr_task = asyncio.create_task(
            _read_stream(
                process.stderr,
                proc_info.stderr_buffer,
                log_file,
                "STDERR",
                proc_info.merged_buffer,
            )
        )

        # Register process
        _processes[process_id] = proc_info

        logger.info(
            f"Started process {process_id}: {params.command} (PID: {process.pid})"
        )

        return f"process_id: {process_id}\npid: {process.pid}\nlog: {log_file}"

    except FileNotFoundError:
        return f"error: command not found: {params.command}"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    }
)
async def executor_send(params: SendInputInput) -> str:
    """
    Send text to a process's stdin and optionally wait for output.

    By default (wait_time > 0), waits specified seconds and returns ONLY NEW output generated after this send.
    For long-running commands, use wait_time=0 to send immediately, then call executor_read_output later.

    Parameters:
    - wait_time > 0: Wait and return only new output (default: 0.1 seconds)
    - wait_time = 0: Send immediately without waiting (use executor_read_output later)
    - full_buffer = True: Return last tail_lines from full buffer (old behavior)
    - full_buffer = False: Return only new output (default, recommended)

    Returns: plain text output if wait_time > 0, or "ok" if wait_time = 0.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"error: process not found: {params.process_id}"

        if not proc_info.is_running:
            return f"error: process {params.process_id} has terminated (exit_code: {proc_info.process.returncode})"

        # Prepare text to send
        text_to_send = params.text
        if params.add_newline and not text_to_send.endswith("\n"):
            text_to_send += "\n"

        # Write to stdin
        proc_info.process.stdin.write(text_to_send.encode("utf-8"))
        await proc_info.process.stdin.drain()

        # Log the input
        _log_to_file(proc_info.log_file, "STDIN", text_to_send)

        logger.info(
            f"Sent input to process {params.process_id}: {repr(params.text[:50])}"
        )

        if params.wait_time == 0:
            return "ok"

        lines_before = len(proc_info.merged_buffer)

        await asyncio.sleep(params.wait_time)

        lines_after = len(proc_info.merged_buffer)
        new_lines_count = lines_after - lines_before

        if params.full_buffer:
            tail_to_return = params.tail_lines or 20
            output_lines = list(proc_info.merged_buffer)[-tail_to_return:]
        else:
            output_lines = (
                list(proc_info.merged_buffer)[-new_lines_count:]
                if new_lines_count > 0
                else []
            )

        text = "".join(output_lines)
        if not proc_info.is_running:
            return f"{text}[exited: {proc_info.process.returncode}]" if text else f"[exited: {proc_info.process.returncode}]"
        return text if text else "(no output)"

    except BrokenPipeError:
        return "error: stdin closed (process may have exited)"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def executor_read_output(params: ReadOutputInput) -> str:
    """
    Read output from a process's stdout/stderr buffer.

    Retrieves recent output lines from the process. Output is continuously captured
    in memory buffers (max 1000 lines per stream). For complete history, check the
    log file.

    Returns: plain text output lines.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"error: process not found: {params.process_id}"

        if params.stream == "stdout":
            source_buffer = proc_info.stdout_buffer
        elif params.stream == "stderr":
            source_buffer = proc_info.stderr_buffer
        else:
            source_buffer = proc_info.merged_buffer

        output_lines = list(source_buffer)
        if params.tail_lines:
            output_lines = output_lines[-params.tail_lines :]

        text = "".join(output_lines)
        if not proc_info.is_running:
            return f"{text}[exited: {proc_info.process.returncode}]" if text else f"[exited: {proc_info.process.returncode}]"
        return text if text else "(no output)"

    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
    }
)
async def executor_stop(params: StopProcessInput) -> str:
    """
    Stop a running process.

    Terminates the process using SIGTERM (graceful) or SIGKILL (force).
    The process will be removed from the active process list.

    Returns: plain text termination status.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"error: process not found: {params.process_id}"

        if not proc_info.is_running:
            del _processes[params.process_id]
            return f"already terminated, exit_code: {proc_info.process.returncode}"

        # Cancel background reader tasks FIRST to avoid asyncio deadlock
        # (readers holding pipe transports can block process.wait())
        if proc_info.stdout_task:
            proc_info.stdout_task.cancel()
        if proc_info.stderr_task:
            proc_info.stderr_task.cancel()

        # Terminate the process
        if params.force:
            proc_info.process.kill()
            method = "SIGKILL"
        else:
            proc_info.process.terminate()
            method = "SIGTERM"

        # Wait for process to finish (always with timeout)
        try:
            await asyncio.wait_for(proc_info.process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc_info.process.kill()
            try:
                await asyncio.wait_for(proc_info.process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                method += "->SIGKILL(timeout, abandoned)"
            else:
                method += "->SIGKILL(timeout)"

        # Log termination
        _log_to_file(
            proc_info.log_file,
            "TERMINATED",
            f"Method: {method}, Return code: {proc_info.process.returncode}",
        )

        logger.info(
            f"Stopped process {params.process_id} (PID: {proc_info.process.pid})"
        )

        rc = proc_info.process.returncode
        exit_str = str(rc) if rc is not None else "unknown (abandoned)"

        # Remove from registry
        del _processes[params.process_id]

        return f"stopped ({method}), exit_code: {exit_str}"

    except ProcessLookupError:
        return f"error: process {params.process_id} not found in system"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def executor_list() -> str:
    """
    List all active processes.

    Returns information about all currently managed processes, including their
    status, PIDs, and buffered output line counts.

    Returns: plain text process list, one per line.
    """
    try:
        if not _processes:
            return "(no active processes)"

        lines = []
        for proc_info in _processes.values():
            status = "running" if proc_info.is_running else f"exited({proc_info.process.returncode})"
            cmd = proc_info.command
            if proc_info.args:
                cmd += " " + " ".join(proc_info.args[:3])
            lines.append(f"{proc_info.process_id} [{status}] pid={proc_info.process.pid} {cmd}")

        return "\n".join(lines)

    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def executor_get_info(params: ProcessIdInput) -> str:
    """
    Get detailed information about a specific process.

    Returns comprehensive information including recent output, buffer status,
    and log file location.

    Returns: plain text process details and recent output.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"error: process not found: {params.process_id}"

        status = "running" if proc_info.is_running else f"exited({proc_info.process.returncode})"
        recent = "".join(list(proc_info.merged_buffer)[-10:])
        cmd = proc_info.command + (" " + " ".join(proc_info.args) if proc_info.args else "")

        return f"pid: {proc_info.process.pid}\nstatus: {status}\ncmd: {cmd}\nbuf_lines: {len(proc_info.merged_buffer)}\nlog: {proc_info.log_file}\nrecent_output:\n{recent}"

    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


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
