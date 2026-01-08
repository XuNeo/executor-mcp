# Executor MCP Server - Project Context

## Project Overview

**Executor MCP Server** is a Model Context Protocol (MCP) server that enables AI assistants to manage interactive CLI binary processes. The server allows launching persistent binary processes, sending stdin commands, and reading stdout/stderr responses over time through a structured API.

**Key Technologies:**
- Python 3.10+
- MCP (Model Context Protocol) SDK (FastMCP)
- Pydantic 2.0+ for input validation
- asyncio for asynchronous process management

**Architecture:**
- Uses stdio transport for MCP client communication
- Manages multiple concurrent binary processes independently
- Implements circular buffers (1000 lines per stream) for in-memory output
- Provides comprehensive logging to timestamped files
- Background asyncio tasks continuously capture stdout/stderr

**Core Features:**
- Persistent process management with unique process IDs
- Interactive stdin/stdout communication
- Multiple concurrent process support
- Real-time output buffering and logging
- Graceful (SIGTERM) and forced (SIGKILL) termination
- Comprehensive error handling with actionable suggestions

## Building and Running

### Installation

```bash
# Install in development mode
pip install -e .

# Or install from directory
pip install .
```

### Running the Server

```bash
# Using installed command
executor-mcp

# Or run directly
python executor_mcp.py
```

### Configuration

Set log directory via environment variable:
```bash
export EXECUTOR_LOG_DIR="$HOME/.executor-mcp/logs"
executor-mcp
```

Default log directory: `.executor-mcp/`

### Testing

```bash
# Run test suite
python test_executor_mcp.py

# Syntax check
python -m py_compile executor_mcp.py

# Manual testing with MCP Inspector
npx @modelcontextprotocol/inspector python executor_mcp.py
```

### Building Distribution

```bash
pip install build
python -m build
```

Creates:
- `dist/executor_mcp-0.1.0.tar.gz`
- `dist/executor_mcp-0.1.0-py3-none-any.whl`

## Development Conventions

### Code Structure

**Single-file architecture:** All server logic is contained in `executor_mcp.py` (main module)

**Key components:**
- `ProcessInfo` dataclass: Manages process state, buffers, and metadata
- Global `_processes` registry: Dictionary mapping process IDs to ProcessInfo objects
- Pydantic models: Input validation for all tool parameters
- MCP tools: Six async functions decorated with `@mcp.tool()`

**Tool naming convention:** All tools use `executor_` prefix for clarity

### MCP Tools

1. **executor_start**: Launch new binary process
2. **executor_send**: Send text to process stdin
3. **executor_read_output**: Read from stdout/stderr buffers
4. **executor_stop**: Terminate running process
5. **executor_list**: List all active processes
6. **executor_get_info**: Get detailed process information

### Error Handling

All tools return structured JSON responses:
```json
{
  "success": boolean,
  "error": string (if failed),
  "error_type": string,
  "suggestions": [string]  // Actionable guidance
}
```

Common error types:
- `FileNotFoundError`: Binary not found
- `PermissionError`: Execute permission denied
- `ProcessLookupError`: Process not found
- `BrokenPipeError`: Process stdin closed

### Logging

**Two-tier logging system:**
1. Python logging module: Server-level events (INFO level)
2. Per-process log files: Complete I/O history with timestamps

**Log file format:**
```
=== Executor MCP Process Log ===
Process ID: {process_id}
Command: {command}
Started: {timestamp}
==================================================

[timestamp] COMMAND: {full command}
[timestamp] STDOUT: {output line}
[timestamp] STDIN: {input text}
[timestamp] STDERR: {error line}
[timestamp] TERMINATED: Method: {method}, Return code: {code}
```

### Memory Management

- Circular buffers: `deque(maxlen=1000)` per stream (stdout/stderr)
- Automatic dropping of old lines from memory
- Complete history preserved in log files
- Configurable via `DEFAULT_BUFFER_SIZE` constant

### Async/Await Patterns

- All I/O operations use async/await
- Background tasks for continuous stream reading
- Proper task cancellation on process termination
- Timeout handling (5 seconds) for graceful shutdown

### Input Validation

- Pydantic models for all tool inputs
- Field validators for constraints (e.g., stream values)
- Type hints throughout codebase
- Descriptive field descriptions for MCP schema generation

### Tool Annotations

All tools include MCP annotations:
- `readOnlyHint`: True for read-only operations (list, get_info, read_output)
- `destructiveHint`: True for stop operation
- `idempotentHint`: True for read-only operations

### Testing Conventions

**Test file:** `test_executor_mcp.py`

**Test structure:**
- Async test functions using `asyncio.run()`
- Direct import and invocation of tool functions
- JSON response parsing and validation
- Three test categories:
  1. Basic workflow (start → send → read → stop)
  2. Simple commands (echo/cat)
  3. Error handling (invalid inputs)

**Test execution:**
```bash
python test_executor_mcp.py
```

### Version Management

- Version defined in `pyproject.toml` under `[project]`
- Current version: `0.1.0`
- Follow semantic versioning (MAJOR.MINOR.PATCH)
- Update version before creating releases

### Publishing

**GitHub Actions workflow:** `.github/workflows/publish.yml`

**Publishing process:**
1. Update version in `pyproject.toml`
2. Commit and push changes
3. Create git tag: `git tag v0.2.0`
4. Create GitHub release
5. Workflow automatically builds and publishes to PyPI

**Trusted publishing:** Uses OIDC tokens (no API tokens needed)

### Code Style

- PEP 8 compliant Python
- Type hints for all function signatures
- Docstrings for all public functions
- Clear variable names with descriptive comments
- Section headers for code organization (using `# ====`)

### Dependencies

**Core dependencies:**
- `mcp>=1.1.0`: MCP SDK
- `pydantic>=2.0.0`: Data validation

**Dev dependencies:**
- `pytest>=7.0.0`: Testing framework
- `pytest-asyncio>=0.21.0`: Async test support

### Configuration Files

- `pyproject.toml`: Project metadata, dependencies, build config
- `requirements.txt`: Core dependencies (for reference)
- `.gitignore`: Python-specific ignores (pycache, egg-info, logs)
- `LICENSE.txt`: Apache License 2.0

### Documentation

- `README.md`: Comprehensive user guide with examples
- `QUICKREF.md`: Quick reference for common operations
- `AGENTS.md`: This file (development context)
- `.github/PUBLISHING.md`: Publishing instructions

## Typical Development Workflow

1. **Make changes** to `executor_mcp.py`
2. **Run syntax check:** `python -m py_compile executor_mcp.py`
3. **Run tests:** `python test_executor_mcp.py`
4. **Test manually** with MCP Inspector if needed
5. **Update version** in `pyproject.toml` if releasing
6. **Commit changes** with descriptive message
7. **Create release** to trigger PyPI publishing

## Key Design Principles

- **Clear separation:** MCP stdio transport vs. managed binary stdin/stdout
- **Process isolation:** Each binary runs independently with its own buffers
- **Non-blocking:** All operations are async to prevent blocking
- **Comprehensive logging:** Every I/O operation is logged with timestamps
- **User-friendly errors:** Error messages include actionable suggestions
- **Type safety:** Pydantic validation ensures correct input types
- **Resource management:** Proper cleanup of processes and tasks
- **Scalability:** Supports multiple concurrent processes efficiently

## Common Patterns

### Starting a Process
```python
start_params = StartProcessInput(
    command="python3",
    args=["-i"],
    working_dir="/path/to/project"
)
result = await executor_start(start_params)
process_id = json.loads(result)["process_id"]
```

### Sending Commands
```python
send_params = SendInputInput(
    process_id=process_id,
    text="print('Hello')",
    add_newline=True
)
await executor_send(send_params)
```

### Reading Output
```python
read_params = ReadOutputInput(
    process_id=process_id,
    tail_lines=10,
    stream="stdout"
)
result = await executor_read_output(read_params)
output = json.loads(result)["output"]
```

### Stopping a Process
```python
stop_params = StopProcessInput(
    process_id=process_id,
    force=False  # Use SIGTERM
)
await executor_stop(stop_params)
```

## Troubleshooting

**Process not starting:**
- Check binary path is correct
- Verify execute permissions
- Check log files for detailed errors

**No output appearing:**
- Ensure binary uses unbuffered output (e.g., `python -u`)
- Wait for background tasks to capture output
- Check log files for complete history

**Process won't stop:**
- Use `force=True` for SIGKILL
- Check if process is zombie state
- Verify process ID is correct

**Memory issues:**
- Reduce `DEFAULT_BUFFER_SIZE` if needed
- Monitor number of active processes
- Check log file sizes
