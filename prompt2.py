#!/usr/bin/env python3
"""
prompt.py – build a Markdown prompt out of a list of source files whose
paths and context live in a dotenv-style file.

Usage examples
--------------

# Use a custom env file
python3 prompt2.py -e prompt.env

"""
import argparse
import json
import os
import sys
from pathlib import Path
from textwrap import dedent

from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────────────
# Configuration helpers
# ──────────────────────────────────────────────────────────────────────────────
EXTENSION_LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".java": "java", ".c": "c",
    ".cpp": "cpp", ".cs": "csharp", ".php": "php", ".rb": "ruby",
    ".go": "go", ".ts": "typescript", ".swift": "swift", ".kt": "kotlin",
    ".rs": "rust", ".html": "html", ".css": "css", ".json": "json",
    ".xml": "xml", ".sh": "bash", ".sql": "sql", ".md": "markdown",
    ".csv": "csv",
}


def load_environment(dotenv_path: Path | None) -> None:
    """
    Load key-value pairs from a chosen dotenv file. If *dotenv_path* is None or
    doesn't exist, fall back to a .env sitting next to this script.
    """
    fallback = Path(__file__).with_name(".env")
    env_file = dotenv_path if dotenv_path and dotenv_path.exists() else fallback
    load_dotenv(env_file, override=True)
    print(f"[prompt.py] Using env file: {env_file}")


def get_language_identifier(file_path: str) -> str:
    """Return a code-block language from EXTENSION_LANGUAGE_MAP."""
    return EXTENSION_LANGUAGE_MAP.get(Path(file_path).suffix.lower(), "")


def get_route(file_path: str) -> str:
    """
    Display-friendly path:
    - If "app" exists in the path parts, show from that folder onward.
    - Otherwise, show the path relative to the current working directory.
    """
    rel_path = os.path.relpath(file_path, start=os.getcwd())
    if "app" in rel_path.split(os.sep):
        parts = rel_path.split(os.sep)
        try:
            rel_path = os.path.join(*parts[parts.index("app"):])
        except ValueError:
            pass
    return rel_path


def write_file_as_code_block(src_path: str, out_fp) -> None:
    """Write one file into the output as a fenced code block."""
    if not Path(src_path).is_file():
        sys.exit(f"Error: '{src_path}' does not exist or is not a file.")

    language = get_language_identifier(src_path)
    headline = f"**{get_route(src_path)}**\n```{language or ''}"

    with open(src_path, encoding="utf-8") as f:
        content = f.read()

    # Pretty-print JSON or truncate CSV to first 20 lines
    if language == "json":
        try:
            content = json.dumps(json.loads(content), indent=2)
        except Exception:
            pass
        if content.count("\n") > 20:
            content = "\n".join(content.splitlines()[:20] + ["..."])
    elif language == "csv":
        lines = content.splitlines()
        if len(lines) > 20:
            content = "\n".join(lines[:20] + ["..."])

    out_fp.write(dedent(f"""{headline}
{content}
```

"""))


def build_prompt(context: str, file_list: list[str], output_path: Path) -> None:
    """Create the final prompt file."""
    with output_path.open("w", encoding="utf-8") as out_fp:
        out_fp.write(f"**Context:** {context}\n\n")
        for i, src in enumerate(file_list):
            write_file_as_code_block(src, out_fp)
            if i < len(file_list) - 1:
                out_fp.write("=" * 40 + "\n\n")
    print(f"[prompt.py] Prompt written to {output_path}")


# ──────────────────────────────────────────────────────────────────────────────
# Main entry – parse CLI, load env, and run.
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate prompt.txt from files listed in a dotenv file."
    )
    parser.add_argument(
        "-e", "--env", dest="env_file", metavar="PATH",
        type=Path, help="Path to the dotenv file to use instead of .env"
    )
    parser.add_argument(
        "-o", "--output", dest="output", default="prompt.txt",
        help="Output filename (default: prompt.txt)"
    )
    args = parser.parse_args()

    load_environment(args.env_file)

    context = os.getenv("PROMPT_GENERATOR_CONTEXT")
    files = os.getenv("PROMPT_GENERATOR_FILES")
    if not (context and files):
        sys.exit("Error: PROMPT_GENERATOR_CONTEXT or PROMPT_GENERATOR_FILES missing.")

    build_prompt(
        context=context,
        file_list=[f.strip() for f in files.split(",")],
        output_path=Path(args.output)
    )


if __name__ == "__main__":
    main()