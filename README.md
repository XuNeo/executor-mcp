# CLI Runner MCP Server

An MCP (Model Context Protocol) server for managing interactive CLI binary processes. This server enables AI assistants to launch persistent binary processes, send stdin commands, and read stdout/stderr responses over time.

## Features

- **Persistent Process Management**: Launch binaries that run in the background
- **Interactive I/O**: Send commands to stdin and read from stdout/stderr
- **Output Buffering**: Keeps last 1000 lines in memory per stream
- **Comprehensive Logging**: All I/O is logged to timestamped files
- **Multiple Process Support**: Manage multiple binaries simultaneously
- **Real-time Output**: Background tasks continuously capture output

## Important Note

This MCP server uses **stdio transport** for communication with MCP clients (like Claude Desktop). This is separate from the stdin/stdout of the binaries being managed. The server manages the stdin/stdout of your target binaries independently.

## Installation

### From Source

```bash
# Clone or navigate to the directory
cd skills/mcp-builder/runner

# Install in development mode
pip install -e .

# Or install from the directory
pip install .
```

### Dependencies

- Python >= 3.10
- mcp >= 1.1.0
- pydantic >= 2.0.0

## Usage

### Starting the MCP Server

The server runs using stdio transport (default for MCP):

```bash
cli-runner-mcp
```

Or run directly:

```bash
python cli_runner_mcp.py
```

### Configuration

Configure via environment variable:

```bash
export CLI_RUNNER_LOG_DIR="$HOME/.cli_runner/logs"
cli-runner-mcp
```

Default log directory: `./cli_runner_logs/`

### Configuring with Claude Desktop

Add to your Claude Desktop configuration:

**MacOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

**Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "cli-runner": {
      "command": "python",
      "args": ["/absolute/path/to/cli_runner_mcp.py"],
      "env": {
        "CLI_RUNNER_LOG_DIR": "/path/to/logs"
      }
    }
  }
}
```

Or if installed via pip:

```json
{
  "mcpServers": {
    "cli-runner": {
      "command": "cli-runner-mcp"
    }
  }
}
```

## Available Tools

### 1. `cli_runner_start`

Launch a new interactive binary process.

**Parameters:**
- `command` (string, required): Path to the binary to execute
- `args` (list[string], optional): Command-line arguments
- `working_dir` (string, optional): Working directory for the process

**Example:**
```json
{
  "command": "/usr/bin/python3",
  "args": ["-i"],
  "working_dir": "/home/user/project"
}
```

**Returns:**
```json
{
  "success": true,
  "process_id": "a3f5d2b1",
  "command": "/usr/bin/python3",
  "args": ["-i"],
  "pid": 12345,
  "log_file": "./cli_runner_logs/a3f5d2b1_20260107_120000_python3.log",
  "message": "Process started successfully. Use process_id 'a3f5d2b1' for subsequent operations."
}
```

### 2. `cli_runner_send`

Send text to a process's stdin.

**Parameters:**
- `process_id` (string, required): Process ID from `cli_runner_start`
- `text` (string, required): Text to send
- `add_newline` (boolean, optional): Append newline (default: true)

**Example:**
```json
{
  "process_id": "a3f5d2b1",
  "text": "print('Hello, World!')",
  "add_newline": true
}
```

**Returns:**
```json
{
  "success": true,
  "process_id": "a3f5d2b1",
  "bytes_sent": 23,
  "message": "Input sent successfully"
}
```

### 3. `cli_runner_read_output`

Read output from a process's stdout/stderr buffer.

**Parameters:**
- `process_id` (string, required): Process ID to read from
- `tail_lines` (integer, optional): Number of recent lines (default: all buffered)
- `stream` (string, optional): Which stream - "stdout", "stderr", or "both" (default: "stdout")

**Example:**
```json
{
  "process_id": "a3f5d2b1",
  "tail_lines": 10,
  "stream": "stdout"
}
```

**Returns:**
```json
{
  "success": true,
  "process_id": "a3f5d2b1",
  "is_running": true,
  "return_code": null,
  "lines_returned": 10,
  "output": ["Hello, World!\n", ">>> "],
  "log_file": "./cli_runner_logs/a3f5d2b1_20260107_120000_python3.log",
  "message": "Retrieved 10 lines from stdout"
}
```

### 4. `cli_runner_stop`

Stop a running process.

**Parameters:**
- `process_id` (string, required): Process ID to stop
- `force` (boolean, optional): Use SIGKILL instead of SIGTERM (default: false)

**Example:**
```json
{
  "process_id": "a3f5d2b1",
  "force": false
}
```

**Returns:**
```json
{
  "success": true,
  "process_id": "a3f5d2b1",
  "pid": 12345,
  "return_code": 0,
  "termination_method": "SIGTERM (graceful)",
  "log_file": "./cli_runner_logs/a3f5d2b1_20260107_120000_python3.log",
  "message": "Process terminated successfully using SIGTERM (graceful)"
}
```

### 5. `cli_runner_list`

List all active processes.

**Parameters:** None

**Returns:**
```json
{
  "success": true,
  "count": 2,
  "processes": [
    {
      "process_id": "a3f5d2b1",
      "command": "/usr/bin/python3",
      "args": ["-i"],
      "started_at": "2026-01-07T12:00:00",
      "is_running": true,
      "pid": 12345,
      "return_code": null,
      "log_file": "./cli_runner_logs/a3f5d2b1_20260107_120000_python3.log",
      "stdout_lines_buffered": 42,
      "stderr_lines_buffered": 0
    }
  ],
  "message": "Found 2 active process(es)"
}
```

### 6. `cli_runner_get_info`

Get detailed information about a specific process.

**Parameters:**
- `process_id` (string, required): Process ID

**Returns:**
```json
{
  "success": true,
  "process_id": "a3f5d2b1",
  "command": "/usr/bin/python3",
  "args": ["-i"],
  "started_at": "2026-01-07T12:00:00",
  "is_running": true,
  "pid": 12345,
  "return_code": null,
  "log_file": "./cli_runner_logs/a3f5d2b1_20260107_120000_python3.log",
  "stdout_lines_buffered": 42,
  "stderr_lines_buffered": 0,
  "recent_stdout": [">>> ", "Hello, World!\n", ">>> "],
  "recent_stderr": []
}
```

## Typical Workflow

1. **Start a binary**:
   ```
   Use cli_runner_start to launch your interactive CLI tool
   → Returns a process_id
   ```

2. **Send commands**:
   ```
   Use cli_runner_send to write to stdin
   → Process executes command
   ```

3. **Read responses**:
   ```
   Use cli_runner_read_output to retrieve stdout/stderr
   → Get the command's output
   ```

4. **Repeat steps 2-3** as needed for ongoing interaction

5. **Stop when done**:
   ```
   Use cli_runner_stop to terminate the process
   ```

## Example Use Cases

### Interactive Python REPL

```python
# Start Python
cli_runner_start(command="python3", args=["-i"])
# → process_id: "abc123"

# Send Python code
cli_runner_send(process_id="abc123", text="x = 42")
cli_runner_send(process_id="abc123", text="print(x * 2)")

# Read output
cli_runner_read_output(process_id="abc123", tail_lines=5)
# → [">>> x = 42\n", ">>> print(x * 2)\n", "84\n", ">>> "]

# Stop Python
cli_runner_stop(process_id="abc123")
```

### Database CLI Tool

```python
# Start PostgreSQL CLI
cli_runner_start(command="psql", args=["mydb"])
# → process_id: "def456"

# Execute queries
cli_runner_send(process_id="def456", text="SELECT * FROM users LIMIT 5;")
cli_runner_read_output(process_id="def456")

# Continue querying
cli_runner_send(process_id="def456", text="\\dt")  # List tables
cli_runner_read_output(process_id="def456")

# Exit
cli_runner_stop(process_id="def456")
```

### Node.js REPL

```python
# Start Node.js
cli_runner_start(command="node")
# → process_id: "ghi789"

# Evaluate JavaScript
cli_runner_send(process_id="ghi789", text="const fs = require('fs')")
cli_runner_send(process_id="ghi789", text="fs.readdirSync('.')")
cli_runner_read_output(process_id="ghi789")

# Stop
cli_runner_stop(process_id="ghi789")
```

## Log Files

All process I/O is logged to timestamped files in the log directory:

```
cli_runner_logs/
├── abc123_20260107_120000_python3.log
├── def456_20260107_120530_psql.log
└── ghi789_20260107_121045_node.log
```

**Log Format:**
```
=== CLI Runner Process Log ===
Process ID: abc123
Command: python3
Started: 2026-01-07T12:00:00.123456
==================================================

[2026-01-07 12:00:00.123] COMMAND: python3 -i
[2026-01-07 12:00:00.456] STDOUT: Python 3.13.0 ...
[2026-01-07 12:00:01.789] STDIN: x = 42
[2026-01-07 12:00:01.890] STDOUT: >>> 
[2026-01-07 12:00:02.012] STDIN: print(x * 2)
[2026-01-07 12:00:02.123] STDOUT: 84
[2026-01-07 12:00:02.234] STDOUT: >>> 
[2026-01-07 12:00:05.567] TERMINATED: Method: SIGTERM (graceful), Return code: 0
```

## Architecture

### Process Lifecycle

1. **Start**: `asyncio.create_subprocess_exec` launches the binary with PIPE'd stdin/stdout/stderr
2. **Background Tasks**: Two asyncio tasks continuously read from stdout and stderr
3. **Buffering**: Output is stored in circular buffers (deque with maxlen=1000)
4. **Logging**: All I/O is written to log files with timestamps
5. **Interaction**: AI can send stdin and read buffered output at any time
6. **Termination**: Process is stopped with SIGTERM/SIGKILL, tasks are cancelled

### Memory Management

- Each process keeps **1000 lines** in memory per stream (configurable via `DEFAULT_BUFFER_SIZE`)
- Older lines are automatically dropped from buffers (circular buffer)
- Complete history is preserved in log files
- Multiple processes can run simultaneously

### Error Handling

All tools return structured JSON with:
- `success`: boolean indicating operation success
- `error`: error message (if failed)
- `suggestion`: actionable guidance for fixing the issue

## Development

### Testing

Test the server manually:

```bash
# Terminal 1: Start the server
python cli_runner_mcp.py

# Terminal 2: Use MCP Inspector
npx @modelcontextprotocol/inspector python cli_runner_mcp.py
```

### Syntax Check

```bash
python -m py_compile cli_runner_mcp.py
```

### Packaging

Build a distributable package:

```bash
pip install build
python -m build
```

This creates:
- `dist/cli_runner_mcp-0.1.0.tar.gz`
- `dist/cli_runner_mcp-0.1.0-py3-none-any.whl`

## License

Apache License 2.0 - See [LICENSE.txt](LICENSE.txt) for details.

## Contributing

This MCP server follows the [MCP Best Practices](../reference/mcp_best_practices.md) and [Python Implementation Guide](../reference/python_mcp_server.md).

Key design principles:
- ✅ Clear, descriptive tool names with `cli_runner_` prefix
- ✅ Comprehensive error messages with actionable suggestions
- ✅ Proper async/await for all I/O operations
- ✅ Type-safe Pydantic models for input validation
- ✅ JSON responses for structured data
- ✅ Tool annotations (readOnlyHint, destructiveHint, etc.)
- ✅ Comprehensive logging for debugging

## Support

For issues or questions:
1. Check the log files in `CLI_RUNNER_LOG_DIR`
2. Review error messages (they include suggestions)
3. Verify binary paths and permissions
4. Ensure Python >= 3.10 and dependencies are installed
