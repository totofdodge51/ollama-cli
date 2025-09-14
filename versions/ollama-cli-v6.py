#!/usr/bin/env python3
"""
Ollama CLI v6 - Assistant LLM interactif amélioré avec création de projet multi-fichiers.
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import ANSI
import subprocess
import difflib
import re
from bs4 import BeautifulSoup
from datetime import datetime

# Importations de la bibliothèque Rich pour une interface utilisateur riche
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text

# Initialisation de la console Rich pour un affichage esthétique
console = Console()

# Fichiers pour l'historique et le contexte
HISTORY_FILE = Path.home() / ".ollama_cli_history"
CONTEXT_FILE = Path.home() / ".ollama_cli_context.json"
CONFIG_FILE = Path.home() / ".ollama_cli_config.json"
CONVO_DIR = Path.home() / ".ollama_cli_conversations"

ASCII_LOGO = r"""
  ██████╗  ██╗      ██╗      ██╗       █████╗  ███╗   ███╗  █████╗      ██████╗██╗     ██╗
 ██╔═══██╗ ██║      ██║      ██║      ██╔══██╗ ████╗ ████║ ██╔══██╗    ██╔════╝██║     ██║
 ██║   ██║ ██║      ██║      ██║      ███████║ ██╔████╔██║ ███████║    ██║     ██║     ██║
 ██║   ██║ ██║      ██║      ██║      ██╔══██║ ██║╚██╔╝██║ ██╔══██║    ██║     ██║     ╚═╝
 ╚██████╔╝ ███████╗ ███████╗ ███████╗ ██║  ██║ ██║ ╚═╝ ██║ ██║  ██║    ╚██████╗███████╗██╗
  ╚═════╝  ╚══════╝ ╚══════╝ ╚══════╝ ╚═╝  ╚═╝ ╚═╝     ╚═╝ ╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝
"""

class WebSearcher:
    """Gestionnaire de recherche web avec plusieurs providers"""

    def __init__(self):
        self.searx_instances = [
            "https://search.privacyguides.net",
            "https://searx.be",
            "https://search.sapti.me",
            "https://searx.tiekoetter.com"
        ]
        self.duckduckgo_base = "https://html.duckduckgo.com/html/"

    def search_searx(self, query: str, num_results: int = 5) -> List[Dict]:
        """Recherche via SearX (plus fiable)"""
        for instance in self.searx_instances:
            try:
                params = {'q': query, 'format': 'json', 'categories': 'general'}
                response = requests.get(f"{instance}/search", params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    return [{'title': item.get('title', ''), 'url': item.get('url', ''), 'snippet': item.get('content', '')} for item in data.get('results', [])[:num_results]]
            except Exception:
                continue
        return []

    def search_duckduckgo(self, query: str, num_results: int = 5) -> List[Dict]:
        """Recherche via DuckDuckGo (scraping simple)"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
            params = {'q': query, 'kl': 'fr-fr'}
            response = requests.get(self.duckduckgo_base, params=params, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            for result in soup.find_all('div', class_='result')[:num_results]:
                title_elem = result.find('a', class_='result__a')
                snippet_elem = result.find('a', class_='result__snippet')
                if title_elem:
                    results.append({'title': title_elem.get_text(strip=True), 'url': title_elem.get('href', ''), 'snippet': snippet_elem.get_text(strip=True) if snippet_elem else ''})
            return results
        except Exception as e:
            console.print(f"[red]Erreur DuckDuckGo: {e}[/red]")
            return []

class OllamaAPI:
    """Wrapper pour les appels à l'API d'Ollama avec recherche web"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.model = "llama3"
        self.last_context = []
        self.web_enabled = True
        self.web_searcher = WebSearcher()
        self.system_prompt_template = '''Tu es un assistant de terminal expert en développement de logiciels. Tu dois utiliser les outils à ta disposition pour accomplir les tâches demandées par l'utilisateur.

INSTRUCTIONS OBLIGATOIRES:
- Pour toute demande de **création** d'un ou plusieurs fichiers, TU DOIS utiliser le format `<project_creation>`.
- Pour toute demande de **modification** d'un ou plusieurs fichiers, TU DOIS utiliser le format `<file_modifications>`.
- Pour toute demande d'**exécution** d'une commande, TU DOIS utiliser le format `<shell>`.
- Pour les **commandes de longue durée** (serveurs, etc.), TU DOIS les lancer dans un nouveau terminal. Le format est: `<shell>{terminal_launcher} bash -c 'suite de commandes'</shell>`.
- **Ne donne jamais d'instructions à l'utilisateur.** Fais-le toi-même en utilisant les outils.

OUTILS DISPONIBLES:

1. CRÉATION DE PROJET/FICHIERS:
   <project_creation>
     <explanation>Brève explication.</explanation>
     <file path="chemin/fichier.ext">CONTENU</file>
   </project_creation>

2. MODIFICATION DE FICHIERS:
   <file_modifications>
     <explanation>Brève explication.</explanation>
     <file path="chemin/fichier.ext">NOUVEAU CONTENU</file>
   </file_modifications>

3. EXÉCUTION DE COMMANDE SHELL:
   - Commande simple: `<shell>ma_commande</shell>`
   - Serveur ou commande longue: `<shell>{terminal_launcher} bash -c 'cd mon_dossier && ma_commande_longue'</shell>`

4. RECHERCHE WEB:
   `<web_search>termes de recherche</web_search>`

Fichiers actuellement chargés en contexte (lisibles pour toi): {loaded_files}
'''

    def get_system_prompt(self, loaded_files: List[str], terminal_launcher: str) -> str:
        files_str = ", ".join(loaded_files) if loaded_files else "aucun"
        return self.system_prompt_template.format(
            loaded_files=files_str,
            terminal_launcher=terminal_launcher
        )

    def list_models(self) -> List[str]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()
            models = response.json().get("models", [])
            return [model["name"] for model in models]
        except requests.exceptions.RequestException as e:
            console.print(f"[red]Erreur de connexion à l'API Ollama : {e}[/red]")
            console.print("[yellow]Veuillez vous assurer que le serveur Ollama est bien lancé.[/yellow]")
            return []

    def search_web(self, query: str, num_results: int = 3) -> List[Dict]:
        if not self.web_enabled:
            return []
        results = self.web_searcher.search_searx(query, num_results)
        if not results:
            results = self.web_searcher.search_duckduckgo(query, num_results)
        return results

    def generate(self, prompt: str, system_prompt: str, context: Optional[List] = None) -> str:
        payload = {"model": self.model, "prompt": prompt, "system": system_prompt, "stream": True, "context": context or []}
        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, stream=True, timeout=(5, 300))
            response.raise_for_status()
            full_response = ""
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        token = data.get("response", "")
                        if token:
                            full_response += token
                        if data.get("done") and "context" in data:
                            self.last_context = data.get("context", [])
                    except json.JSONDecodeError:
                        continue
            return full_response
        except requests.exceptions.RequestException as e:
            console.print(f"[red]Erreur API Ollama : {e}[/red]")
            return ""

class FileHandler:
    @staticmethod
    def read_file(filepath: Path) -> Tuple[bool, str]:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return True, f.read()
        except Exception as e:
            return False, str(e)

    @staticmethod
    def write_file(filepath: Path, content: str) -> Tuple[bool, str]:
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, f"Fichier sauvegardé : {filepath}"
        except Exception as e:
            return False, str(e)

class OllamaCLI:
    def __init__(self):
        self.api = OllamaAPI()
        self.file_handler = FileHandler()
        self.conversation_history = []
        self.chat_renderables = []
        self.working_directory = Path.cwd()
        self.loaded_files = {}
        self.terminal_launcher = "konsole -e"
        self.load_config()
        CONVO_DIR.mkdir(exist_ok=True)

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.terminal_launcher = config.get("terminal_launcher", self.terminal_launcher)
                    self.api.web_enabled = config.get("web_enabled", True)
            except (json.JSONDecodeError, IOError):
                pass

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({"terminal_launcher": self.terminal_launcher, "web_enabled": self.api.web_enabled}, f, indent=2)
        except IOError:
            pass

    def _get_header_panel(self):
        web_status = "[bold green]Activé[/]" if self.api.web_enabled else "[bold red]Désactivé[/]"
        subtitle = f"[dim]Modèle: [bold yellow]{self.api.model}[/] | Web: {web_status} | [yellow]/help[/] pour les commandes.[/dim]"
        return Panel(Text(ASCII_LOGO, style="bold cyan", justify="center"), title="Ollama CLI v6", subtitle=subtitle, border_style="cyan")

    def _update_display(self):
        max_history_items = 30
        self.chat_renderables = self.chat_renderables[-max_history_items:]
        console.clear()
        console.print(self._get_header_panel())
        for renderable in self.chat_renderables:
            console.print(renderable)

    def handle_config_command(self):
        current_web = "[bold green]Activé[/]" if self.api.web_enabled else "[bold red]Désactivé[/]"
        config_panel = Panel(f"Lanceur de terminal: `[cyan]{self.terminal_launcher}[/]`\nAccès Web: {current_web}", title="Configuration Actuelle")
        self.chat_renderables.append(config_panel)
        self._update_display()

        if Confirm.ask("\n[bold]Modifier l'accès web ?[/bold]"):
            self.api.web_enabled = not self.api.web_enabled
            new_status = "[bold green]Activé[/]" if self.api.web_enabled else "[bold red]Désactivé[/]"
            self.chat_renderables.append(Panel(f"Accès web mis à jour: {new_status}", border_style="green"))

        if Confirm.ask("\n[bold]Modifier le lanceur de terminal ?[/bold]"):
            new_launcher = Prompt.ask("Entrez la nouvelle commande de lancement", default=self.terminal_launcher)
            self.terminal_launcher = new_launcher
            self.chat_renderables.append(Panel(f"Lanceur de terminal mis à jour: `[cyan]{self.terminal_launcher}[/]`", border_style="green"))

        self.save_config()
        self.chat_renderables.append(Panel("[green]Configuration sauvegardée.[/green]"))
        self._update_display()

    def handle_command(self, command: str) -> Tuple[bool, Optional[str]]:
        parts = command.split()
        cmd = parts[0].lower()
        if cmd in ["/quit", "/exit", "/q"]: return False, None
        
        self.chat_renderables.append(Panel(command, title="Commande", title_align="left", border_style="yellow"))

        if cmd == "/help":
            self.chat_renderables.append(self._get_help_content())
        elif cmd == "/clear":
            self.clear_context()
            self.chat_renderables.append(Panel("[green]Contexte de la conversation effacé.[/green]"))
        elif cmd == "/model":
            self.select_model()
            return True, None
        elif cmd == "/config":
            self.handle_config_command()
            return True, None
        elif cmd == "/load":
            if len(parts) > 1: self.load_file(" ".join(parts[1:]))
            else: self.chat_renderables.append(Panel("[red]Usage: /load <filepath>[/red]"))
        elif cmd == "/files":
            self.chat_renderables.append(self._get_files_table())
        elif cmd == "/run":
            if len(parts) > 1: self.run_command(" ".join(parts[1:]))
            else: self.chat_renderables.append(Panel("[red]Usage: /run <command>[/red]"))
        else:
            self.chat_renderables.append(Panel(f"[red]Commande inconnue : {cmd}. Tapez /help.[/red]"))

        self._update_display()
        return True, None

    def _get_help_content(self):
        help_text = """# Aide de Ollama CLI v6
- `/quit, /exit, /q`: Quitter.
- `/clear`: Effacer l'historique et les fichiers.
- `/model`: Sélectionner un autre modèle.
- `/config`: Modifier la configuration (terminal, accès web).
- `/load <fichier>`: Charger un fichier en contexte.
- `/files`: Lister les fichiers chargés.
- `/run <commande>`: Exécuter une commande shell.
"""
        return Panel(Markdown(help_text), title="Aide", border_style="green")

    def select_model(self) -> bool:
        models = self.api.list_models()
        if not models: return False
        table = Table(title="🤖 Modèles Disponibles")
        table.add_column("Index", style="cyan")
        table.add_column("Nom du Modèle", style="green")
        for i, model in enumerate(models, 1): table.add_row(str(i), model)
        
        self.chat_renderables.append(table)
        self._update_display()

        try:
            choice = Prompt.ask("Sélectionnez un modèle", choices=[str(i) for i in range(1, len(models) + 1)])
            self.api.model = models[int(choice) - 1]
            self.clear_context()
            self.chat_renderables.append(Panel(f"[green]Modèle changé en: {self.api.model}. Contexte effacé.[/green]"))
            self._update_display()
            return True
        except (KeyboardInterrupt, EOFError):
            self.chat_renderables.append(Panel("[yellow]Sélection annulée.[/yellow]"))
            self._update_display()
            return False

    def load_file(self, path_str: str):
        # Use pathlib's glob for powerful matching
        base_path = self.working_directory
        # Handle cases where user provides a path like 'Web/*' vs 'Web'
        if '*' in path_str or '[' in path_str or '?' in path_str:
            files_to_load = list(base_path.glob(path_str))
        else:
            path_obj = base_path / path_str
            if not path_obj.exists():
                self.chat_renderables.append(Panel(f"[red]Erreur : Le chemin {path_obj} n'existe pas.[/red]"))
                self._update_display()
                return
            if path_obj.is_dir():
                files_to_load = [f for f in path_obj.rglob('*') if f.is_file()]
            else:
                files_to_load = [path_obj]

        if not files_to_load:
            self.chat_renderables.append(Panel(f"[yellow]Aucun fichier trouvé pour : {path_str}[/yellow]"))
            self._update_display()
            return

        loaded_count = 0
        error_count = 0
        for filepath in files_to_load:
            relative_path_str = str(filepath.relative_to(base_path))
            success, content = self.file_handler.read_file(filepath)
            if success:
                self.loaded_files[relative_path_str] = content
                loaded_count += 1
            else:
                error_count += 1
        
        if loaded_count > 0:
            self.chat_renderables.append(Panel(f"[green]{loaded_count} fichier(s) chargé(s) depuis : {path_str}[/green]"))
        if error_count > 0:
            self.chat_renderables.append(Panel(f"[yellow]{error_count} fichier(s) n'ont pas pu être chargés (ex: binaires).[/yellow]"))
        
        self._update_display()

    def _get_files_table(self):
        if not self.loaded_files:
            return Panel("[dim]Aucun fichier chargé.[/dim]", title="📁 Fichiers en Contexte")
        table = Table(title="📁 Fichiers en Contexte")
        table.add_column("Chemin", style="cyan")
        for filepath in self.loaded_files: table.add_row(filepath)
        return table

    def run_command(self, command: str):
        self.chat_renderables.append(Panel(f"[bold yellow]L'assistant propose d'exécuter :[/bold yellow] [cyan]{command}[/cyan]"))
        self._update_display()
        if Confirm.ask("\n[bold]Exécuter cette commande ?[/bold]"):
            try:
                if command.strip().startswith(self.terminal_launcher):
                    subprocess.Popen(command, shell=True, cwd=self.working_directory)
                    self.chat_renderables.append(Panel("[green]Commande lancée dans un nouveau terminal.[/green]"))
                else:
                    result = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=self.working_directory)
                    output = result.stdout.strip()
                    error = result.stderr.strip()
                    res_panel = Panel(Group(Panel(output, title="Sortie"), Panel(error, title="Erreur")), title="Résultat")
                    self.chat_renderables.append(res_panel)
            except Exception as e:
                self.chat_renderables.append(Panel(f"[red]Erreur d'exécution: {e}[/red]"))
        else:
            self.chat_renderables.append(Panel("[yellow]Exécution annulée.[/yellow]"))
        self._update_display()

    def process_response(self, response: str):
        response = response.strip()
        if '<project_creation>' in response:
            self.handle_project_creation(response)
        elif '<file_modifications>' in response:
            self.handle_file_modifications(response)
        elif '<shell>' in response:
            self.handle_shell_execution(response)
        elif response:
            self.chat_renderables.append(Panel(Markdown(response), title="Assistant", border_style="cyan"))
            self._update_display()

    def handle_shell_execution(self, response: str):
        command = re.search(r'<shell>(.*?)</shell>', response, re.DOTALL)
        if command:
            self.run_command(command.group(1).strip())

    def handle_project_creation(self, response: str):
        project_match = re.search(r'<project_creation>(.*?)</project_creation>', response, re.DOTALL)
        if not project_match: return
        
        content = project_match.group(1)
        explanation = re.search(r'<explanation>(.*?)</explanation>', content, re.DOTALL)
        files = re.findall(r'<file path="(.*?)">(.*?)</file>', content, re.DOTALL)

        if explanation:
            self.chat_renderables.append(Panel(Markdown(explanation.group(1).strip()), title="Plan de Création"))
        
        if not files: 
            self._update_display()
            return

        table = Table(title="Fichiers à créer")
        table.add_column("Chemin", style="cyan")
        for path, _ in files: table.add_row(path)
        self.chat_renderables.append(table)
        self._update_display()

        if Confirm.ask(f"\n[bold]Créer ces {len(files)} fichier(s) ?[/bold]"):
            created_paths = []
            for path, file_content in files:
                filepath = self.working_directory / path.strip()
                clean_content = file_content.strip()
                success, msg = self.file_handler.write_file(filepath, clean_content)
                console.print(f"[green]✓ {msg}[/green]" if success else f"[red]✗ {msg}[/red]")
                if success:
                    created_paths.append(path.strip())

            if created_paths:
                console.print("\n[bold green]Chargement automatique des fichiers créés en contexte...[/bold green]")
                for path_str in created_paths:
                    self.load_file(path_str)
            
            self._update_display()
        else:
            console.print("[yellow]Création annulée.[/yellow]")
            self._update_display()

    def handle_file_modifications(self, response: str):
        modifications_match = re.search(r'<file_modifications>(.*?)</file_modifications>', response, re.DOTALL)
        if not modifications_match: return

        content = modifications_match.group(1)
        explanation = re.search(r'<explanation>(.*?)</explanation>', content, re.DOTALL)
        files_to_modify = re.findall(r'<file path="(.*?)">(.*?)</file>', content, re.DOTALL)

        if explanation:
            self.chat_renderables.append(Panel(Markdown(explanation.group(1).strip()), title="Plan de Modification"))
        
        if not files_to_modify:
            self._update_display()
            return

        diff_panels = []
        for path, new_content in files_to_modify:
            path = path.strip()
            # The model might add trailing newlines, let's be gentle
            new_content = new_content.strip()
            
            original_content = self.loaded_files.get(path)
            if original_content is None:
                success, content_from_disk = self.file_handler.read_file(self.working_directory / path)
                if not success:
                    self.chat_renderables.append(Panel(f"[red]Erreur: Impossible de lire le fichier original {path} pour la modification.[/red]"))
                    self._update_display()
                    return
                original_content = content_from_disk
            
            diff = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
            diff_text = "".join(diff)
            diff_panels.append(Panel(Syntax(diff_text, "diff", theme="monokai", line_numbers=True), title=f"Changements pour {path}"))

        self.chat_renderables.extend(diff_panels)
        self._update_display()

        if Confirm.ask(f"\n[bold]Appliquer ces {len(files_to_modify)} modification(s) ?[/bold]"):
            for path, new_content in files_to_modify:
                path = path.strip()
                new_content = new_content.strip()
                filepath = self.working_directory / path
                success, msg = self.file_handler.write_file(filepath, new_content)
                console.print(f"[green]✓ {msg}[/green]" if success else f"[red]✗ {msg}[/red]")
                if success:
                    # Update context after successful write
                    self.loaded_files[path] = new_content
            self._update_display()
        else:
            console.print("[yellow]Modifications annulées.[/yellow]")
            self._update_display()

    def get_files_content_for_prompt(self) -> str:
        if not self.loaded_files: return ""
        context_str = "\nCONTEXTE FICHIERS:\n"
        for path, content in self.loaded_files.items():
            context_str += f"--- Contenu de {path} ---\n{content}\n--- Fin de {path}---\n\n"
        return context_str

    def clear_context(self):
        self.conversation_history = []
        self.loaded_files = {}
        self.chat_renderables = []
        self.api.last_context = []

    def chat_loop(self):
        self._update_display()
        if not self.api.list_models() or not self.select_model():
            return

        session = PromptSession(history=FileHistory(str(HISTORY_FILE)))
        while True:
            try:
                user_input = session.prompt(ANSI("\x1b[1;32mVous > \x1b[0m"))
                if not user_input.strip(): continue

                if user_input.startswith('/'):
                    continue_loop, _ = self.handle_command(user_input)
                    if not continue_loop: break
                    continue

                self.chat_renderables.append(Panel(user_input, title="Vous", border_style="green"))
                self._update_display()

                self.conversation_history.append({"role": "user", "content": user_input})
                
                prompt = self.get_files_content_for_prompt() + user_input
                system_prompt = self.api.get_system_prompt(
                    list(self.loaded_files.keys()),
                    self.terminal_launcher
                )
                
                with console.status("[bold yellow]L'assistant réfléchit...[/bold yellow]"):
                    response = self.api.generate(prompt, system_prompt, self.api.last_context)
                
                if response:
                    self.conversation_history.append({"role": "assistant", "content": response})
                    self.process_response(response)

            except (KeyboardInterrupt, EOFError):
                break
        console.print("\nAu revoir !")

def main():
    parser = argparse.ArgumentParser(description="Ollama CLI v6 - Assistant de code interactif avec création de projet.")
    parser.add_argument("--model", help="Spécifier un modèle à utiliser directement.")
    args = parser.parse_args()

    cli = OllamaCLI()
    if args.model:
        cli.api.model = args.model
    
    cli.chat_loop()

if __name__ == "__main__":
    main()
