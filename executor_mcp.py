#!/usr/bin/env python3
"""
Executor MCP Server

An MCP server for managing interactive CLI binary processes.
Enables AI to launch persistent binaries, send stdin commands, and read stdout responses.

Note: This MCP server uses stdio transport (for communication with MCP clients).
Don't confuse this with the stdin/stdout of the managed binary processes.
"""

import asyncio
import json
import logging
import os
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
    log_file: Optional[Path] = None
    started_at: datetime = field(default_factory=datetime.now)
    stdout_task: Optional[asyncio.Task] = None
    stderr_task: Optional[asyncio.Task] = None

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
    stream: asyncio.StreamReader, buffer: Deque[str], log_file: Path, prefix: str
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

    except asyncio.CancelledError:
        logger.info(f"Stream reader task cancelled for {prefix}")
    except Exception as e:
        logger.error(f"Error reading {prefix}: {e}")


def _format_process_info(proc_info: ProcessInfo, include_output: bool = False) -> Dict:
    """Format process information as a dictionary"""
    info = {
        "process_id": proc_info.process_id,
        "command": proc_info.command,
        "args": proc_info.args,
        "started_at": proc_info.started_at.isoformat(),
        "is_running": proc_info.is_running,
        "pid": proc_info.process.pid,
        "return_code": proc_info.process.returncode,
        "log_file": str(proc_info.log_file) if proc_info.log_file else None,
        "stdout_lines_buffered": len(proc_info.stdout_buffer),
        "stderr_lines_buffered": len(proc_info.stderr_buffer),
    }

    if include_output:
        info["recent_stdout"] = list(proc_info.stdout_buffer)[-10:]  # Last 10 lines
        info["recent_stderr"] = list(proc_info.stderr_buffer)[-10:]

    return info


def _handle_error(error: Exception, context: str) -> str:
    """Format error message with actionable guidance as JSON"""
    if isinstance(error, FileNotFoundError):
        message = f"Binary not found. {context}"
        suggestions = [
            "Verify the binary path is correct",
            "Check if the binary is in PATH",
            "Try using absolute path",
            "Ensure the binary is executable",
        ]
    elif isinstance(error, PermissionError):
        message = f"Permission denied. {context}"
        suggestions = [
            "Check file permissions",
            "Verify execute bit is set (chmod +x)",
            "Check if user has necessary privileges",
        ]
    elif isinstance(error, ProcessLookupError):
        message = f"Process not found. {context}"
        suggestions = ["The process may have already terminated."]
    else:
        message = f"{type(error).__name__}: {str(error)}"
        suggestions = [context]

    error_response = {
        "success": False,
        "error": message,
        "error_type": type(error).__name__,
        "suggestions": suggestions,
    }

    return json.dumps(error_response, indent=2)


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


class ReadOutputInput(BaseModel):
    """Input schema for reading process output"""

    process_id: str = Field(..., description="The process ID to read output from")
    tail_lines: Optional[int] = Field(
        default=None,
        ge=1,
        le=10000,
        description="Number of recent lines to return (default: all buffered lines)",
    )


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

        # Start the process
        full_command = [params.command] + params.args
        _log_to_file(log_file, "COMMAND", " ".join(full_command))

        process = await asyncio.create_subprocess_exec(
            *full_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
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
            _read_stream(process.stdout, proc_info.stdout_buffer, log_file, "STDOUT")
        )
        proc_info.stderr_task = asyncio.create_task(
            _read_stream(process.stderr, proc_info.stderr_buffer, log_file, "STDERR")
        )

        # Register process
        _processes[process_id] = proc_info

        logger.info(
            f"Started process {process_id}: {params.command} (PID: {process.pid})"
        )

        result = {
            "success": True,
            "process_id": process_id,
            "command": params.command,
            "args": params.args,
            "pid": process.pid,
            "log_file": str(log_file),
            "message": f"Process started successfully. Use process_id '{process_id}' for subsequent operations.",
        }

        return json.dumps(result, indent=2)

    except FileNotFoundError:
        return _handle_error(
            FileNotFoundError(), f"Command not found: {params.command}"
        )
    except Exception as e:
        return _handle_error(e, f"Failed to start process: {params.command}")


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

    By default, waits 0.1 seconds and returns recent output (last 20 lines).
    Set wait_time=0 to send without waiting for response.

    Returns: Plain text output if wait_time > 0, or "Success" if wait_time = 0.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return f"Error: Process not found: {params.process_id}"

        if not proc_info.is_running:
            return f"Error: Process {params.process_id} has terminated (return code: {proc_info.process.returncode})"

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

        # If wait_time is 0, return immediately
        if params.wait_time == 0:
            return "Success"

        # Wait for output
        await asyncio.sleep(params.wait_time)

        # Read output from both stdout and stderr
        output_lines = []

        stdout_lines = list(proc_info.stdout_buffer)
        if params.tail_lines:
            stdout_lines = stdout_lines[-params.tail_lines :]
        output_lines.extend(stdout_lines)

        stderr_lines = list(proc_info.stderr_buffer)
        if params.tail_lines:
            stderr_lines = stderr_lines[-params.tail_lines :]
        output_lines.extend(stderr_lines)

        # Return plain text output
        return "".join(output_lines)

    except BrokenPipeError:
        return f"Error: Process {params.process_id} stdin is closed"
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)}"


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def executor_read_output(params: ReadOutputInput) -> str:
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
            return json.dumps(
                {
                    "success": False,
                    "error": f"Process not found: {params.process_id}",
                    "suggestion": "Use executor_list to see active processes",
                },
                indent=2,
            )

        # Collect all output (both stdout and stderr)
        output_lines = []

        stdout_lines = list(proc_info.stdout_buffer)
        if params.tail_lines:
            stdout_lines = stdout_lines[-params.tail_lines :]
        output_lines.extend(stdout_lines)

        stderr_lines = list(proc_info.stderr_buffer)
        if params.tail_lines:
            stderr_lines = stderr_lines[-params.tail_lines :]
        output_lines.extend(stderr_lines)

        result = {
            "success": True,
            "process_id": params.process_id,
            "is_running": proc_info.is_running,
            "return_code": proc_info.process.returncode,
            "lines_returned": len(output_lines),
            "output": output_lines,
            "log_file": str(proc_info.log_file),
            "message": f"Retrieved {len(output_lines)} lines",
        }

        return json.dumps(result, indent=2)

    except Exception as e:
        return _handle_error(
            e, f"Failed to read output from process {params.process_id}"
        )


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

    Returns: JSON with termination status and final output summary.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Process not found: {params.process_id}",
                    "suggestion": "Use executor_list to see active processes",
                },
                indent=2,
            )

        if not proc_info.is_running:
            return json.dumps(
                {
                    "success": True,
                    "process_id": params.process_id,
                    "message": "Process already terminated",
                    "return_code": proc_info.process.returncode,
                },
                indent=2,
            )

        # Terminate the process
        if params.force:
            proc_info.process.kill()
            method = "SIGKILL (forced)"
        else:
            proc_info.process.terminate()
            method = "SIGTERM (graceful)"

        # Wait for process to finish
        try:
            await asyncio.wait_for(proc_info.process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc_info.process.kill()
            await proc_info.process.wait()
            method += " -> SIGKILL (timeout)"

        # Cancel background tasks
        if proc_info.stdout_task:
            proc_info.stdout_task.cancel()
        if proc_info.stderr_task:
            proc_info.stderr_task.cancel()

        # Log termination
        _log_to_file(
            proc_info.log_file,
            "TERMINATED",
            f"Method: {method}, Return code: {proc_info.process.returncode}",
        )

        logger.info(
            f"Stopped process {params.process_id} (PID: {proc_info.process.pid})"
        )

        result = {
            "success": True,
            "process_id": params.process_id,
            "pid": proc_info.process.pid,
            "return_code": proc_info.process.returncode,
            "termination_method": method,
            "log_file": str(proc_info.log_file),
            "message": f"Process terminated successfully using {method}",
        }

        # Remove from registry
        del _processes[params.process_id]

        return json.dumps(result, indent=2)

    except ProcessLookupError:
        return _handle_error(
            ProcessLookupError(), f"Process {params.process_id} not found in system"
        )
    except Exception as e:
        return _handle_error(e, f"Failed to stop process {params.process_id}")


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def executor_list() -> str:
    """
    List all active processes.

    Returns information about all currently managed processes, including their
    status, PIDs, and buffered output line counts.

    Returns: JSON array of process information.
    """
    try:
        if not _processes:
            return json.dumps(
                {
                    "success": True,
                    "count": 0,
                    "processes": [],
                    "message": "No active processes",
                },
                indent=2,
            )

        processes_info = []
        for proc_info in _processes.values():
            processes_info.append(_format_process_info(proc_info, include_output=False))

        result = {
            "success": True,
            "count": len(processes_info),
            "processes": processes_info,
            "message": f"Found {len(processes_info)} active process(es)",
        }

        return json.dumps(result, indent=2)

    except Exception as e:
        return _handle_error(e, "Failed to list processes")


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def executor_get_info(params: ProcessIdInput) -> str:
    """
    Get detailed information about a specific process.

    Returns comprehensive information including recent output, buffer status,
    and log file location.

    Returns: JSON with detailed process information.
    """
    try:
        proc_info = _processes.get(params.process_id)

        if not proc_info:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Process not found: {params.process_id}",
                    "suggestion": "Use executor_list to see active processes",
                },
                indent=2,
            )

        info = _format_process_info(proc_info, include_output=True)
        info["success"] = True

        return json.dumps(info, indent=2)

    except Exception as e:
        return _handle_error(e, f"Failed to get info for process {params.process_id}")


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
