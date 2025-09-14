#!/usr/bin/env python3
"""
Ollama CLI - Interactive LLM assistant with file handling capabilities
Similar to claude-code and gemini-cli but using local Ollama models
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import readline
import atexit
from datetime import datetime
import subprocess
import tempfile
import difflib
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
import re

# Initialize Rich console for beautiful output
console = Console()

# History file for readline
HISTORY_FILE = Path.home() / ".ollama_cli_history"
CONTEXT_FILE = Path.home() / ".ollama_cli_context.json"

class OllamaAPI:
    """Wrapper for Ollama API calls"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.model = "llama3.2"  # Default model
        self.system_prompt = """You are a helpful coding assistant in Ollama CLI.

For general questions, respond normally.

When the user requests to create a new file or script, respond using exactly this XML-like format:

<plan>
Describe the plan here.
Include how it works, assumptions, etc.
Suggested filename: weather.sh
</plan>

<code>
Put the complete code here.
No additional text.
</code>

Do not add any text outside these tags."""

    def list_models(self) -> List[str]:
        """List available Ollama models"""
        try:
            response = requests.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                return [model["name"] for model in models]
            return []
        except:
            return []

    def generate(self, prompt: str, context: Optional[List] = None, stream: bool = False) -> str:
        """Generate response from Ollama"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": self.system_prompt,
            "stream": stream
        }

        if context:
            payload["context"] = context

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                stream=stream
            )

            if stream:
                full_response = ""
                for line in response.iter_lines():
                    if line:
                        data = json.loads(line)
                        chunk = data.get("response", "")
                        console.print(chunk, end="")
                        full_response += chunk

                        if data.get("done", False):
                            self.last_context = data.get("context", [])
                            break
                console.print()  # New line after response
                return full_response
            else:
                data = response.json()
                full_response = data.get("response", "")
                self.last_context = data.get("context", [])
                return full_response
        except Exception as e:
            console.print(f"[red]Error calling Ollama API: {e}[/red]")
            return ""

class FileHandler:
    """Handle file operations and code editing"""

    @staticmethod
    def read_file(filepath: Path) -> Tuple[bool, str]:
        """Read file content"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return True, f.read()
        except Exception as e:
            return False, str(e)

    @staticmethod
    def write_file(filepath: Path, content: str) -> Tuple[bool, str]:
        """Write content to file"""
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, f"File written: {filepath}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def show_diff(original: str, modified: str, filepath: str) -> None:
        """Show diff between original and modified content"""
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"{filepath} (original)",
            tofile=f"{filepath} (modified)"
        )

        console.print("\n[yellow]Proposed changes:[/yellow]")
        for line in diff:
            if line.startswith('+'):
                console.print(f"[green]{line}[/green]", end="")
            elif line.startswith('-'):
                console.print(f"[red]{line}[/red]", end="")
            else:
                console.print(line, end="")

class OllamaCLI:
    """Main CLI application"""

    def __init__(self):
        self.api = OllamaAPI()
        self.file_handler = FileHandler()
        self.context = []
        self.conversation_history = []
        self.working_directory = Path.cwd()
        self.loaded_files = {}

        # Setup readline for better input handling
        self.setup_readline()

    def setup_readline(self):
        """Setup readline with history"""
        if HISTORY_FILE.exists():
            readline.read_history_file(HISTORY_FILE)
        readline.set_history_length(1000)
        atexit.register(readline.write_history_file, HISTORY_FILE)

    def load_context(self):
        """Load saved context from file"""
        if CONTEXT_FILE.exists():
            try:
                with open(CONTEXT_FILE, 'r') as f:
                    data = json.load(f)
                    self.context = data.get("context", [])
                    self.conversation_history = data.get("history", [])
                    console.print("[dim]Previous context loaded[/dim]")
            except:
                pass

    def save_context(self):
        """Save context to file"""
        try:
            with open(CONTEXT_FILE, 'w') as f:
                json.dump({
                    "context": self.context,
                    "history": self.conversation_history[-20:]  # Keep last 20 exchanges
                }, f)
        except:
            pass

    def select_model(self):
        """Let user select an Ollama model"""
        models = self.api.list_models()
        if not models:
            console.print("[red]No Ollama models found. Please install models first.[/red]")
            console.print("[dim]Run: ollama pull llama3.2[/dim]")
            return False

        table = Table(title="Available Models")
        table.add_column("Index", style="cyan")
        table.add_column("Model Name", style="green")

        for i, model in enumerate(models, 1):
            table.add_row(str(i), model)

        console.print(table)

        choice = Prompt.ask("Select model", default="1")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                self.api.model = models[idx]
                console.print(f"[green]Using model: {self.api.model}[/green]")
                return True
        except:
            pass

        console.print("[red]Invalid selection[/red]")
        return False

    def handle_command(self, command: str) -> bool:
        """Handle special commands"""
        parts = command.split()
        cmd = parts[0].lower()

        if cmd in ["/quit", "/exit", "/q"]:
            self.save_context()
            console.print("[yellow]Goodbye![/yellow]")
            return False

        elif cmd == "/help":
            self.show_help()

        elif cmd == "/clear":
            self.context = []
            self.conversation_history = []
            console.print("[green]Context cleared[/green]")

        elif cmd == "/model":
            self.select_model()

        elif cmd == "/load":
            if len(parts) > 1:
                filepath = Path(" ".join(parts[1:]))
                self.load_file(filepath)
            else:
                console.print("[red]Usage: /load <filepath>[/red]")

        elif cmd == "/save":
            if len(parts) > 2:
                filepath = Path(parts[1])
                content = " ".join(parts[2:])
                self.save_file(filepath, content)
            else:
                console.print("[red]Usage: /save <filepath> <content>[/red]")

        elif cmd == "/edit":
            if len(parts) > 1:
                filepath = Path(" ".join(parts[1:]))
                self.edit_file(filepath)
            else:
                console.print("[red]Usage: /edit <filepath>[/red]")

        elif cmd == "/run":
            if len(parts) > 1:
                self.run_command(" ".join(parts[1:]))
            else:
                console.print("[red]Usage: /run <command>[/red]")

        elif cmd == "/files":
            self.list_loaded_files()

        elif cmd == "/pwd":
            console.print(f"[cyan]Working directory: {self.working_directory}[/cyan]")

        elif cmd == "/cd":
            if len(parts) > 1:
                self.change_directory(Path(" ".join(parts[1:])))
            else:
                console.print("[red]Usage: /cd <directory>[/red]")

        else:
            console.print(f"[red]Unknown command: {cmd}[/red]")
            console.print("[dim]Type /help for available commands[/dim]")

        return True

    def show_help(self):
        """Display help information"""
        help_text = """
# Ollama CLI Commands

## Chat Commands
- **Just type** - Send message to AI
- **/quit, /exit, /q** - Exit the application
- **/clear** - Clear conversation context
- **/model** - Select a different Ollama model

## File Commands
- **/load <file>** - Load file into context
- **/save <file> <content>** - Save content to file
- **/edit <file>** - Edit file with AI assistance
- **/files** - List loaded files
- **/pwd** - Show working directory
- **/cd <dir>** - Change working directory

## System Commands
- **/run <command>** - Execute shell command
- **/help** - Show this help message

## Tips
- The AI remembers your conversation context
- Load files to give the AI context about your code
- Use /edit for interactive file editing with AI assistance
- For creating new files/scripts via chat, the AI will propose a plan and code, and the CLI will handle confirmation and saving
        """
        console.print(Markdown(help_text))

    def load_file(self, filepath: Path):
        """Load a file into context"""
        full_path = self.working_directory / filepath
        success, content = self.file_handler.read_file(full_path)

        if success:
            self.loaded_files[str(filepath)] = content
            # Add to prompt context
            file_context = f"\n--- File: {filepath} ---\n{content}\n--- End of {filepath} ---\n"
            self.conversation_history.append(("system", file_context))

            # Show preview
            console.print(Panel(
                Syntax(content[:500] + ("..." if len(content) > 500 else ""),
                       lexer=self.guess_lexer(filepath),
                       theme="monokai"),
                title=f"Loaded: {filepath}",
                expand=False
            ))
        else:
            console.print(f"[red]Error loading file: {content}[/red]")

    def save_file(self, filepath: Path, content: str):
        """Save content to a file"""
        full_path = self.working_directory / filepath
        success, message = self.file_handler.write_file(full_path, content)

        if success:
            console.print(f"[green]{message}[/green]")
        else:
            console.print(f"[red]Error: {message}[/red]")

    def edit_file(self, filepath: Path):
        """Interactive file editing with AI"""
        full_path = self.working_directory / filepath
        success, original_content = self.file_handler.read_file(full_path)

        if not success:
            console.print(f"[red]Cannot read file: {original_content}[/red]")
            return

        console.print(f"[cyan]Editing: {filepath}[/cyan]")
        console.print("[dim]Describe the changes you want to make:[/dim]")

        edit_request = Prompt.ask(">")

        # Build prompt for AI
        prompt = f"""You are editing the file: {filepath}

Original content:
```
{original_content}
```


User request: {edit_request}

Please provide the complete modified file content. Include ONLY the code, no explanations."""

        console.print("[dim]Generating changes...[/dim]")
        modified_content = self.api.generate(prompt, self.context)

        # Clean up response (remove markdown code blocks if present)
        lines = modified_content.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines[-1].startswith('```'):
            lines = lines[:-1]
        modified_content = '\n'.join(lines)

        # Show diff
        self.file_handler.show_diff(original_content, modified_content, str(filepath))

        # Ask for confirmation
        if Confirm.ask("\nApply these changes?"):
            success, message = self.file_handler.write_file(full_path, modified_content)
            if success:
                console.print(f"[green]✓ {message}[/green]")
                self.loaded_files[str(filepath)] = modified_content
            else:
                console.print(f"[red]Error: {message}[/red]")
        else:
            console.print("[yellow]Changes discarded[/yellow]")

    def run_command(self, command: str):
        """Execute shell command"""
        console.print(f"[cyan]Running: {command}[/cyan]")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.working_directory,
                timeout=30
            )

            if result.stdout:
                console.print("[green]Output:[/green]")
                console.print(result.stdout)
            if result.stderr:
                console.print("[red]Error:[/red]")
                console.print(result.stderr)

            console.print(f"[dim]Exit code: {result.returncode}[/dim]")
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out after 30 seconds[/red]")
        except Exception as e:
            console.print(f"[red]Error running command: {e}[/red]")

    def list_loaded_files(self):
        """List all loaded files"""
        if not self.loaded_files:
            console.print("[dim]No files loaded[/dim]")
            return

        table = Table(title="Loaded Files")
        table.add_column("File", style="cyan")
        table.add_column("Size", style="green")

        for filepath, content in self.loaded_files.items():
            size = f"{len(content)} chars"
            table.add_row(filepath, size)

        console.print(table)

    def change_directory(self, new_dir: Path):
        """Change working directory"""
        try:
            full_path = self.working_directory / new_dir
            full_path = full_path.resolve()
            if full_path.exists() and full_path.is_dir():
                self.working_directory = full_path
                console.print(f"[green]Changed to: {self.working_directory}[/green]")
            else:
                console.print(f"[red]Directory not found: {new_dir}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    def guess_lexer(self, filepath: Path) -> str:
        """Guess syntax lexer from file extension"""
        ext_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'jsx',
            '.tsx': 'tsx',
            '.java': 'java',
            '.c': 'c',
            '.cpp': 'cpp',
            '.h': 'c',
            '.hpp': 'cpp',
            '.cs': 'csharp',
            '.rb': 'ruby',
            '.go': 'go',
            '.rs': 'rust',
            '.php': 'php',
            '.html': 'html',
            '.css': 'css',
            '.scss': 'scss',
            '.json': 'json',
            '.xml': 'xml',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.md': 'markdown',
            '.sh': 'bash',
            '.bash': 'bash',
            '.zsh': 'bash',
            '.sql': 'sql',
            '.r': 'r',
            '.R': 'r',
        }
        return ext_map.get(filepath.suffix.lower(), 'text')

    def process_response(self, response: str):
        """Process AI response for special formats"""
        plan_match = re.search(r'<plan>(.*?)</plan>', response, re.DOTALL | re.IGNORECASE)
        code_match = re.search(r'<code>(.*?)</code>', response, re.DOTALL | re.IGNORECASE)

        if plan_match and code_match:
            plan = plan_match.group(1).strip()
            code = code_match.group(1).strip()

            filename_match = re.search(r'Suggested filename:\s*(.*?)\s*$', plan, re.MULTILINE | re.IGNORECASE)
            suggested_filename = filename_match.group(1).strip() if filename_match else "new_file.txt"

            console.print("\n[bold yellow]Proposed Plan:[/bold yellow]")
            console.print(Markdown(plan))

            if Confirm.ask("\nDoes this plan suit you? Proceed to create the file?"):
                filename = Prompt.ask("Enter filename", default=suggested_filename)
                full_path = self.working_directory / filename
                success, msg = self.file_handler.write_file(full_path, code)
                if success:
                    console.print(f"[green]{msg}[/green]")
                    # Optionally load the file
                    self.load_file(Path(filename))
                else:
                    console.print(f"[red]{msg}[/red]")
            else:
                console.print("[yellow]File creation cancelled.[/yellow]")
        else:
            console.print(response)

    def chat_loop(self):
        """Main chat interaction loop"""
        # Welcome message
        console.print(Panel.fit(
            "[bold cyan]Ollama CLI[/bold cyan]\n"
            "[dim]Interactive LLM assistant with file handling[/dim]\n"
            "Type [yellow]/help[/yellow] for commands",
            border_style="cyan"
        ))

        # Select model
        if not self.select_model():
            return

        # Load previous context if exists
        self.load_context()

        # Main loop
        while True:
            try:
                # Get user input
                user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")

                if not user_input.strip():
                    continue

                # Check for commands
                if user_input.startswith('/'):
                    if not self.handle_command(user_input):
                        break
                    continue

                # Build context from loaded files
                context_prompt = ""
                if self.loaded_files:
                    context_prompt = "Context files:\n"
                    for filepath, content in self.loaded_files.items():
                        # Include first 1000 chars of each file
                        preview = content[:1000]
                        context_prompt += f"\n--- {filepath} ---\n{preview}\n"
                        if len(content) > 1000:
                            context_prompt += f"... (truncated, {len(content)} total chars)\n"
                    context_prompt += "\n"

                # Add user message to history
                self.conversation_history.append(("user", user_input))

                # Build full prompt
                full_prompt = context_prompt + user_input

                # Get AI response (non-streaming for parsing)
                console.print("\n[bold green]Assistant[/bold green]")
                response = self.api.generate(full_prompt, self.context, stream=False)

                # Process the response
                self.process_response(response)

                # Add assistant response to history
                self.conversation_history.append(("assistant", response))

                # Save context periodically
                if len(self.conversation_history) % 5 == 0:
                    self.save_context()

            except KeyboardInterrupt:
                console.print("\n[yellow]Use /quit to exit[/yellow]")
                continue
            except EOFError:
                break
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                continue

        # Save context before exit
        self.save_context()

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Ollama CLI - Interactive LLM assistant with file handling"
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:11434",
        help="Ollama API URL (default: http://localhost:11434)"
    )
    parser.add_argument(
        "--model",
        help="Specify model to use directly"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to load on startup"
    )

    args = parser.parse_args()

    # Initialize CLI
    cli = OllamaCLI()

    if args.api_url:
        cli.api.base_url = args.api_url

    if args.model:
        cli.api.model = args.model
        console.print(f"[green]Using model: {cli.api.model}[/green]")

    # Load any specified files
    for filepath in args.files:
        cli.load_file(Path(filepath))

    # Start chat loop
    try:
        cli.chat_loop()
    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
