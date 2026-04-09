import sys
import logging
from typing import Optional
from mcp.server.fastmcp import FastMCP
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Initialize FastMCP Server
mcp = FastMCP("Repository Server")

@mcp.tool()
async def list_directory(directory_path: str) -> str:
    """List contents of a given directory within a repository clone path."""
    try:
        p = Path(directory_path)
        if not p.exists() or not p.is_dir():
            return f"Error: Directory {directory_path} does not exist or is not a directory."
        
        items = []
        for x in p.iterdir():
            if x.name.startswith(".git"):
                continue
            item_type = "DIR" if x.is_dir() else "FILE"
            items.append(f"{item_type}\t{x.name}")
            
        return "\n".join(items)
    except Exception as e:
        return f"Error listing directory: {str(e)}"

@mcp.tool()
async def read_source_file(filepath: str, start_line: Optional[int] = None, max_lines: Optional[int] = 200) -> str:
    """
    Read the contents of a source code file.
    If start_line is provided, reads up to max_lines starting from that line.
    """
    try:
        p = Path(filepath)
        if not p.exists() or not p.is_file():
            return f"Error: File {filepath} does not exist."
            
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        
        if start_line is None:
            # Read full or up to max
            subset = lines[:max_lines] if max_lines else lines
            return "\n".join(subset)
            
        # 1-indexed start line
        start_idx = max(0, start_line - 1)
        end_idx = start_idx + (max_lines or len(lines))
        subset = lines[start_idx:end_idx]
        
        numbered = [f"{i+start_idx+1}: {line}" for i, line in enumerate(subset)]
        return "\n".join(numbered)
    except Exception as e:
        return f"Error reading file: {str(e)}"

@mcp.tool()
async def apply_code_patch(filepath: str, original_snippet: str, new_snippet: str) -> str:
    """
    Apply a fix patching a file by replacing an original snippet with a new snippet.
    The original snippet MUST exactly match the file content, including whitespace.
    """
    try:
        p = Path(filepath)
        if not p.exists() or not p.is_file():
            return f"Error: File {filepath} does not exist."
            
        content = p.read_text(encoding="utf-8", errors="replace")
        
        if original_snippet not in content:
            return "Error: original_snippet not found in file. Ensure exact match including whitespace."
            
        patched_content = content.replace(original_snippet, new_snippet, 1)
        p.write_text(patched_content, encoding="utf-8")
        
        return "Patch applied successfully."
    except Exception as e:
        return f"Error applying patch: {str(e)}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run()
