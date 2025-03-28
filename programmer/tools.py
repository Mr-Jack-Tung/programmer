import base64
import json
import os
import subprocess
import weave
import contextlib
from contextvars import ContextVar

LENGTH_LIMIT = 30000


class ToolContext:
    def __init__(self, directory):
        self.directory = os.path.abspath(directory)

    def resolve_path(self, path):
        return os.path.join(self.directory, path)


# Create a ContextVar to store the current ToolContext
current_context: ContextVar[ToolContext | None] = ContextVar(
    "current_context", default=None
)


@contextlib.contextmanager
def tool_context(directory):
    context = ToolContext(directory)
    token = current_context.set(context)
    try:
        yield context
    finally:
        current_context.reset(token)


def get_current_context():
    context = current_context.get()
    if context is None:
        return ToolContext(".")
    return context


def read_image_as_base64(path: str):
    ext = os.path.splitext(path)[1]
    if ext not in [".jpg", ".jpeg", ".png"]:
        raise ValueError("Only .jpg, .jpeg, and .png files are supported.")
    if ext in [".jpg", ".jpeg"]:
        mime_type = "image/jpeg"
    else:
        mime_type = "image/png"
    # Read the image file in binary mode
    with open(path, "rb") as image_file:
        # Encode the image to base64
        base64_bytes = base64.b64encode(image_file.read())
        # Convert the base64 bytes to string
        base64_string = base64_bytes.decode("utf-8")
        # Format the string as required
        formatted_base64_string = f"data:{mime_type};base64,{base64_string}"
        return formatted_base64_string


@weave.op()
def view_image(path: str):
    """View a png or jpg image file.

    Args:
        path: The path to the image file.

    Returns:
        A message indicating that the image was displayed successfully.
    """
    context = get_current_context()
    full_path = context.resolve_path(path)
    base64_image = read_image_as_base64(full_path)

    return f"Image {full_path} displayed in next message.", {
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": base64_image, "detail": "high"},
            },
        ],
    }


@weave.op()
def list_files(directory: str) -> str:
    """List names of all files in a directory.

    Args:
        directory: The directory to list.

    Returns:
        The list of files in the directory.
    """
    context = get_current_context()
    full_path = context.resolve_path(directory)
    result = json.dumps(os.listdir(full_path))
    if len(result) > LENGTH_LIMIT:
        result = result[:LENGTH_LIMIT]
        result += "\n... (truncated)"
    return result


@weave.op()
def write_to_file(path: str, content: str) -> str:
    """Write text to a file at the given path.

    Args:
        path: The path to the file.
        content: The content to write to the file.

    Returns:
        A message indicating whether the file was written successfully.
    """
    context = get_current_context()
    full_path = context.resolve_path(path)
    with open(full_path, "w") as f:
        f.write(content)
    return "File written successfully."


@weave.op()
def read_from_file(path: str) -> str:
    """Read text from a file at the given path.

    Args:
        path: The path to the file.

    Returns:
        The content of the file.
    """
    context = get_current_context()
    full_path = context.resolve_path(path)
    with open(full_path, "r") as f:
        result = f.read()
        if len(result) > LENGTH_LIMIT:
            result = result[:LENGTH_LIMIT]
            result += "\n... (truncated)"
        return result


@weave.op()
def run_command(command: str) -> str:
    """Run a shell command and return its output.

    Args:
        command: The command to run.

    Returns:
        The output of the command.
    """
    context = get_current_context()
    completed_process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
        cwd=context.directory,  # Set the working directory for the command
    )
    exit_code = completed_process.returncode
    stdout = completed_process.stdout.strip()
    stderr = completed_process.stderr.strip()

    if len(stdout) > LENGTH_LIMIT:
        stdout = stdout[:LENGTH_LIMIT]
        stdout += "\n... (truncated)"
    if len(stderr) > LENGTH_LIMIT:
        stderr = stderr[:LENGTH_LIMIT]
        stderr += "\n... (truncated)"

    result = f"Exit code: {exit_code}\n"
    if stderr:
        result += f"STDERR\n{stderr}\n"
    if stdout:
        result += f"STDOUT\n{stdout}\n"
    return result


@weave.op
def read_lines_from_file(file_path: str, start_line: int) -> str:
    """Read up to 500 lines from a file starting at a specific line number.

    Args:
        file_path: The path to the file.
        start_line: The line number to start reading from (1-indexed).

    Returns:
        A string with each line prefixed by its line number.

    Raises:
        Exception: If the file does not exist or start_line is invalid.
    """
    context = get_current_context()
    full_path = context.resolve_path(file_path)
    if not os.path.exists(full_path):
        raise Exception(f"File '{full_path}' does not exist.")

    with open(full_path, "r") as file:
        lines = file.readlines()

    if start_line < 1 or start_line > len(lines):
        raise Exception("Invalid start_line number.")

    end_line = min(start_line + 500, len(lines) + 1)
    result = ""

    for i in range(start_line - 1, end_line - 1):
        result += f"{i + 1}:{lines[i]}"

    return result


@weave.op
def replace_lines_in_file(
    file_path: str,
    start_line: int,
    remove_line_count: int,
    previous_lines: str,
    new_lines: str,
) -> str:
    """Replace lines in a file from start_line to end_line with new_lines. Changes are committed to the file.

    Args:
        file_path: The path to the file.
        start_line: The starting line number for replacement (1-indexed).
        remove_line_count: The number of lines to remove, starting with start_line.
        previous_lines: The previous lines to replace, as a single string. This must match the existing lines, or an exception is raised.
        new_lines: The new lines to insert, as a single string.

    Returns:
        Success message, otherwise raises an exception.

    Raises:
        Exception: If the line range is invalid or file cannot be accessed.
    """
    context = get_current_context()
    full_path = context.resolve_path(file_path)
    lines = []
    if os.path.exists(full_path):
        with open(full_path, "r") as file:
            lines = file.readlines()

    end_line = start_line + remove_line_count

    if start_line < 1 or end_line < start_line or start_line > len(lines) + 1:
        raise Exception("Invalid line range.")

    prev_line_split = [l + "\n" for l in previous_lines.splitlines()]
    if not lines[start_line - 1 : end_line - 1] == prev_line_split:
        raise Exception("Previous lines do not match.")

    # Adjust end_line if it exceeds the current number of lines
    end_line = min(end_line, len(lines) + 1)

    if not new_lines.endswith("\n"):
        new_lines += "\n"

    # Convert new_lines string into a list of lines
    new_lines_list = new_lines.splitlines(keepends=True)

    # Replace the specified line range
    lines[start_line - 1 : end_line - 1] = new_lines_list

    # Write the modified lines back to the file
    with open(full_path, "w") as file:
        file.writelines(lines)

    # Determine the range for the output with a 5-line buffer
    output_start = max(start_line - 6, 0)
    output_end = min(
        start_line - 1 + len(new_lines_list) + 6, len(lines)
    )  # Calculate buffer correctly
    result = ""

    for i in range(output_start, output_end):
        result += f"{i + 1}:{lines[i]}"

    return result
