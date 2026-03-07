import os
import sys
import json
from dotenv import load_dotenv

# Mapping of file extensions to programming languages
EXTENSION_LANGUAGE_MAP = {
    '.py': 'python',
    '.js': 'javascript',
    '.java': 'java',
    '.c': 'c',
    '.cpp': 'cpp',
    '.cs': 'csharp',
    '.php': 'php',
    '.rb': 'ruby',
    '.go': 'go',
    '.ts': 'typescript',
    '.swift': 'swift',
    '.kt': 'kotlin',
    '.rs': 'rust',
    '.html': 'html',
    '.css': 'css',
    '.json': 'json',
    '.xml': 'xml',
    '.sh': 'bash',
    '.sql': 'sql',
    '.md': 'markdown',
    '.csv': 'csv',  # Added CSV mapping
}

def get_language_identifier(file_path):
    """
    Determines the programming language based on the file extension.
    
    Parameters:
      - file_path (str): The path to the file.
    
    Returns:
      - str: The language identifier for the code block.
    """
    _, ext = os.path.splitext(file_path.lower())
    return EXTENSION_LANGUAGE_MAP.get(ext, '')  # Return empty string if extension not found

def get_route(file_path):
    """
    Determines the route to display in the headline.
    
    If the file path contains an "app" directory, the route is taken
    from that directory onward; otherwise, the relative path from the
    current working directory is returned.
    
    Parameters:
      - file_path (str): The path to the file.
      
    Returns:
      - str: The formatted route for display.
    """
    # Get the relative path with respect to the current working directory
    rel_path = os.path.relpath(file_path, start=os.getcwd())
    
    # If "app" is in the path, show from that point on.
    if "app" in rel_path.split(os.sep):
        parts = rel_path.split(os.sep)
        try:
            app_index = parts.index("app")
            route = os.path.join(*parts[app_index:])
        except ValueError:
            route = rel_path
    else:
        route = rel_path
        
    return route

def print_prompt_content(file_path, output_file):
    """
    Writes the content of the specified file to the output file,
    preceded by a headline formatted with the complete file route and a code block
    with the appropriate language.
    
    If the file is a JSON file, its content is pretty printed and then trimmed
    to a maximum of 20 lines. Similarly, CSV files are trimmed to the first 20 lines.
    
    Parameters:
      - file_path (str): The full path to the file to be printed.
      - output_file (file object): The file object to write the output.
    """
    # Verify that the provided path exists and is a file
    if not os.path.isfile(file_path):
        print(f"Error: The file '{file_path}' does not exist or is not a file.", file=sys.stderr)
        sys.exit(1)
    
    # Get the complete route to display
    route = get_route(file_path)
    
    # Determine the programming language based on file extension
    language = get_language_identifier(file_path)
    
    # Format the headline with code block opening
    if language:
        headline = f"**{route}**\n```{language}"
    else:
        headline = f"**{route}**\n```"  # No language specified for unrecognized extensions
    
    # Read the content of the file
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
    except Exception as e:
        print(f"Error reading '{file_path}': {e}", file=sys.stderr)
        sys.exit(1)
    
    # If the file is a JSON file, pretty-print and then trim to 20 lines.
    if language.lower() == 'json':
        try:
            parsed_json = json.loads(content)
            pretty_content = json.dumps(parsed_json, indent=2)
        except Exception:
            # If parsing fails, fall back to the original content.
            pretty_content = content
        
        lines = pretty_content.splitlines()
        if len(lines) > 20:
            content = "\n".join(lines[:20]) + "\n..."
        else:
            content = pretty_content
    # If the file is a CSV file, trim its content to a maximum of 20 lines.
    elif language.lower() == 'csv':
        lines = content.splitlines()
        if len(lines) > 20:
            content = "\n".join(lines[:20]) + "\n..."
    
    # Write the headline, content, and code block closure to the output file
    output_file.write(headline + '\n')
    output_file.write(content + '\n```\n')

def main():
    # Load environment variables from the .env file
    load_dotenv()
    
    # Retrieve the context from the environment variable
    context = os.getenv('PROMPT_GENERATOR_CONTEXT')
    if not context:
        print("Error: The environment variable 'PROMPT_GENERATOR_CONTEXT' is not set.", file=sys.stderr)
        sys.exit(1)
    
    # Retrieve the list of files from the environment variable
    files = os.getenv('PROMPT_GENERATOR_FILES')
    if not files:
        print("Error: The environment variable 'PROMPT_GENERATOR_FILES' is not set.", file=sys.stderr)
        sys.exit(1)
    
    # Split the files by comma and strip any surrounding whitespace
    file_list = [f.strip() for f in files.split(',')]
    
    # Define the output file name
    output_file_name = 'prompt.txt'
    
    try:
        # Open the output file in write mode (this will overwrite existing content)
        with open(output_file_name, 'w', encoding='utf-8') as output_file:
            # Write the context at the beginning
            output_file.write(f"**Context:** {context}\n\n")
            
            for idx, file_path in enumerate(file_list):
                print_prompt_content(file_path, output_file)
                
                # Add a separator between files, except after the last file
                if idx < len(file_list) - 1:
                    output_file.write("\n" + "="*40 + "\n\n")
        
        print(f"Output successfully written to '{output_file_name}'.")
    
    except Exception as e:
        print(f"Error writing to '{output_file_name}': {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
