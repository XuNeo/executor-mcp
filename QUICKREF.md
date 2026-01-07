# CLI Runner MCP - Quick Reference

## Installation

```bash
cd skills/mcp-builder/runner
pip install -e .
```

## Running

```bash
cli-runner-mcp
# Or
python cli_runner_mcp.py
```

## Available Tools

### 1. cli_runner_start
Start a binary process
```json
{
  "command": "/path/to/binary",
  "args": ["arg1", "arg2"],
  "working_dir": "/optional/path"
}
```
Returns: `process_id`

### 2. cli_runner_send
Send stdin to process
```json
{
  "process_id": "abc123",
  "text": "command to send",
  "add_newline": true
}
```

### 3. cli_runner_read_output
Read stdout/stderr
```json
{
  "process_id": "abc123",
  "tail_lines": 10,
  "stream": "stdout"
}
```

### 4. cli_runner_stop
Stop process
```json
{
  "process_id": "abc123",
  "force": false
}
```

### 5. cli_runner_list
List all processes (no params)

### 6. cli_runner_get_info
Get detailed process info
```json
{
  "process_id": "abc123"
}
```

## Configuration

Environment variable:
```bash
export CLI_RUNNER_LOG_DIR="$HOME/.cli_runner/logs"
```

## Testing

```bash
python test_cli_runner.py
```

## Key Features

- ✅ Interactive stdin/stdout communication
- ✅ Multiple concurrent processes
- ✅ Circular buffer (1000 lines/stream)
- ✅ Full I/O logging to files
- ✅ JSON responses
- ✅ Comprehensive error handling
- ✅ Background process management

## Example Workflow

```python
# 1. Start Python REPL
start → process_id: "abc123"

# 2. Send commands
send(process_id="abc123", text="x = 42")
send(process_id="abc123", text="print(x)")

# 3. Read output
read_output(process_id="abc123") → ["42\n", ">>> "]

# 4. Stop when done
stop(process_id="abc123")
```

## Log Files

Location: `./cli_runner_logs/` (or `$CLI_RUNNER_LOG_DIR`)

Format: `{process_id}_{timestamp}_{command}.log`

Example:
```
[2026-01-07 22:31:51.920] COMMAND: python3 -i -u
[2026-01-07 22:31:51.945] STDOUT: Python 3.10.14...
[2026-01-07 22:31:52.423] STDIN: print('Hello!')
[2026-01-07 22:31:52.423] STDOUT: Hello!
[2026-01-07 22:31:52.725] TERMINATED: Return code: -15
```
