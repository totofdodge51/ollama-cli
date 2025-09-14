#!/usr/bin/env python3
"""
Ollama CLI v2 - Assistant LLM interactif amélioré avec des capacités avancées de gestion de fichiers.
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from typing import List, Optional, Tuple
import readline
import atexit
import subprocess
import difflib
import xml.etree.ElementTree as ET
import re

# Importations de la bibliothèque Rich pour une interface utilisateur riche
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner

# Initialisation de la console Rich pour un affichage esthétique
console = Console()

# Fichiers pour l'historique et le contexte
HISTORY_FILE = Path.home() / ".ollama_cli_history"
CONTEXT_FILE = Path.home() / ".ollama_cli_context.json"

class OllamaAPI:
    """Wrapper pour les appels à l'API d'Ollama"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.model = "llama3"  # Modèle par défaut
        # --- AMÉLIORATION : Prompt Système plus robuste ---
        # Ce nouveau prompt guide l'IA pour créer ET modifier des fichiers
        # en utilisant un format XML structuré, ce qui fiabilise le parsing.
        self.system_prompt_template = """Vous êtes un assistant de développement expert dans un terminal de ligne de commande.

Votre but est d'aider l'utilisateur avec son code. Les fichiers suivants sont actuellement chargés en contexte : {loaded_files}.

- Pour une conversation générale, répondez de manière claire et concise.
- Pour **créer un nouveau fichier**, utilisez le format <plan> et <code>.
- Pour **modifier un ou plusieurs fichiers existants**, répondez en utilisant **uniquement** le format XML suivant. Ne mettez AUCUN texte avant ou après le bloc <file_modifications>.

<file_modifications>
  <file path="chemin/vers/fichier1.py">
  <![CDATA[
Le contenu INTEGRAL et MODIFIÉ du fichier1.py va ici.
]]>
  </file>
  <file path="chemin/vers/fichier2.js">
  <![CDATA[
Le contenu INTEGRAL et MODIFIÉ du fichier2.js va ici.
]]>
  </file>
</file_modifications>

- Fournissez toujours le contenu complet du fichier, pas seulement un extrait ou un diff.
- L'utilisateur verra un diff des changements et devra confirmer avant d'enregistrer."""

    def get_system_prompt(self, loaded_files: List[str]) -> str:
        """Formate le prompt système avec les fichiers actuellement chargés."""
        files_str = ", ".join(loaded_files) if loaded_files else "aucun"
        return self.system_prompt_template.format(loaded_files=files_str)

    def list_models(self) -> List[str]:
        """Liste les modèles Ollama disponibles."""
        try:
            response = requests.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            return [model["name"] for model in models]
        except requests.exceptions.RequestException as e:
            console.print(f"[red]Erreur de connexion à l'API Ollama : {e}[/red]")
            return []

    def generate(self, prompt: str, system_prompt: str, context: Optional[List] = None, stream: bool = True) -> str:
        """Génère une réponse depuis Ollama, en stream par défaut."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system_prompt,
            "stream": stream,
            "context": context or []
        }

        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, stream=stream)
            response.raise_for_status()

            full_response = ""
            if stream:
                # --- AMÉLIORATION : Affichage en streaming avec Live et Spinner ---
                spinner = Spinner("dots", " L'assistant réfléchit...")
                with Live(spinner, refresh_per_second=10, transient=True) as live:
                    for line in response.iter_lines():
                        if line:
                            data = json.loads(line)
                            chunk = data.get("response", "")
                            full_response += chunk
                            # Mettre à jour le Live avec le texte généré
                            live.update(Markdown(full_response), refresh=True)

                            if data.get("done", False):
                                self.last_context = data.get("context", [])
                                break
                console.print(Markdown(full_response))
                return full_response
            else:
                # Mode non-stream (pourrait être utilisé pour des tâches de fond)
                data = response.json()
                full_response = data.get("response", "")
                self.last_context = data.get("context", [])
                return full_response

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Erreur lors de l'appel à l'API Ollama : {e}[/red]")
            return ""
        except json.JSONDecodeError:
            console.print("[red]Erreur: Réponse invalide de l'API Ollama.[/red]")
            return ""

class FileHandler:
    """Gère les opérations sur les fichiers et l'affichage des modifications."""

    @staticmethod
    def read_file(filepath: Path) -> Tuple[bool, str]:
        """Lit le contenu d'un fichier."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return True, f.read()
        except Exception as e:
            return False, str(e)

    @staticmethod
    def write_file(filepath: Path, content: str) -> Tuple[bool, str]:
        """Écrit du contenu dans un fichier."""
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, f"Fichier sauvegardé : {filepath}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def show_diff(original: str, modified: str, filepath: str) -> None:
        """Affiche un diff coloré entre deux versions de contenu."""
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"{filepath} (original)",
            tofile=f"{filepath} (modifié)"
        )

        console.print(Panel(f"Changements proposés pour [bold cyan]{filepath}[/bold cyan]", border_style="yellow"))
        diff_lines = list(diff)
        if not diff_lines:
            console.print("[dim]Aucun changement détecté.[/dim]")
            return

        for line in diff_lines:
            if line.startswith('+'):
                console.print(f"[green]{line}[/green]", end="")
            elif line.startswith('-'):
                console.print(f"[red]{line}[/red]", end="")
            elif line.startswith('@@'):
                console.print(f"[yellow]{line}[/yellow]", end="")
            else:
                console.print(line, end="")

class OllamaCLI:
    """Application CLI principale."""

    def __init__(self):
        self.api = OllamaAPI()
        self.file_handler = FileHandler()
        self.context = []
        self.conversation_history = []
        self.working_directory = Path.cwd()
        self.loaded_files = {}  # Dict pour stocker le contenu des fichiers chargés
        self.setup_readline()

    def setup_readline(self):
        """Configure readline pour l'historique des commandes."""
        if HISTORY_FILE.exists():
            readline.read_history_file(HISTORY_FILE)
        readline.set_history_length(1000)
        atexit.register(readline.write_history_file, HISTORY_FILE)

    def load_context(self):
        """Charge le contexte de la conversation précédente depuis un fichier."""
        if CONTEXT_FILE.exists():
            try:
                with open(CONTEXT_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.context = data.get("context", [])
                    self.conversation_history = data.get("history", [])
                    console.print("[dim]Contexte de la conversation précédente chargé.[/dim]")
            except (json.JSONDecodeError, IOError) as e:
                console.print(f"[yellow]Avertissement : Impossible de charger le contexte : {e}[/yellow]")
                self.context = []
                self.conversation_history = []

    def save_context(self):
        """Sauvegarde le contexte de la conversation actuelle dans un fichier."""
        try:
            with open(CONTEXT_FILE, 'w', encoding='utf-8') as f:
                context_to_save = {
                    "context": self.context,
                    "history": self.conversation_history[-20:]  # Garder les 20 derniers échanges
                }
                json.dump(context_to_save, f, indent=2)
        except IOError as e:
            console.print(f"[yellow]Avertissement : Impossible de sauvegarder le contexte : {e}[/yellow]")

    def select_model(self) -> bool:
        """Permet à l'utilisateur de sélectionner un modèle Ollama."""
        console.print(Panel("🤖 Modèles Disponibles", border_style="blue"))
        models = self.api.list_models()
        if not models:
            console.print("[red]Aucun modèle Ollama trouvé. Assurez-vous qu'Ollama est en cours d'exécution et que des modèles sont installés (ex: `ollama pull llama3`).[/red]")
            return False

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Index", style="cyan")
        table.add_column("Nom du Modèle", style="green")

        for i, model in enumerate(models, 1):
            table.add_row(str(i), model)

        console.print(table)

        choices = [str(i) for i in range(1, len(models) + 1)]
        choice = Prompt.ask("Sélectionnez un modèle", choices=choices, default="1")

        idx = int(choice) - 1
        self.api.model = models[idx]
        console.print(f"[green]✓ Modèle sélectionné : [bold]{self.api.model}[/bold][/green]")
        return True

    def handle_command(self, command: str) -> Tuple[bool, Optional[str]]:
        """Gère les commandes spéciales commençant par '/'.

        Returns:
            Tuple[bool, Optional[str]]: (continue_loop, processed_input)
        """
        parts = command.split()
        cmd = parts[0].lower()

        # --- AMÉLIORATION : Remplacement du dictionnaire par des if/elif pour plus de flexibilité ---
        if cmd in ["/quit", "/exit", "/q"]:
            console.print("Au revoir !")
            return False, None

        elif cmd == "/help":
            self.show_help()
            return True, None

        elif cmd == "/clear":
            self.clear_context()
            console.print("[green]Contexte effacé.[/green]")
            return True, None

        elif cmd == "/model":
            self.select_model()
            return True, None

        elif cmd == "/load":
            if len(parts) > 1:
                self.load_file(Path(" ".join(parts[1:])))
            else:
                console.print("[red]Usage: /load <filepath>[/red]")
            return True, None

        elif cmd == "/edit":
            if len(parts) > 1:
                self.edit_file(Path(" ".join(parts[1:])))
            else:
                console.print("[red]Usage: /edit <filepath>[/red]")
            return True, None

        # --- NOUVEAUTÉ : Commande /paste pour la saisie multiligne ---
        elif cmd == "/paste":
            pasted_text = self.get_pasted_input()
            # On retourne le texte collé pour qu'il soit traité par la boucle de chat
            return True, pasted_text

        elif cmd == "/run":
            if len(parts) > 1:
                self.run_command(" ".join(parts[1:]))
            else:
                console.print("[red]Usage: /run <command>[/red]")
            return True, None

        elif cmd == "/files":
            self.list_loaded_files()
            return True, None

        elif cmd == "/pwd":
            console.print(f"[cyan]Répertoire actuel : {self.working_directory}[/cyan]")
            return True, None

        elif cmd == "/cd":
            if len(parts) > 1:
                self.change_directory(Path(" ".join(parts[1:])))
            else:
                console.print("[red]Usage: /cd <directory>[/red]")
            return True, None

        else:
            console.print(f"[red]Commande inconnue : {cmd}. Tapez /help pour la liste.[/red]")
            return True, None

    def clear_context(self):
        self.context = []
        self.conversation_history = []
        self.loaded_files = {}

    def show_help(self):
        help_text = """
#  Aide de Ollama CLI v2

## Commandes de Chat
- **`/quit`, `/exit`, `/q`**: Quitter l'application.
- **`/clear`**: Effacer l'historique de la conversation et les fichiers chargés.
- **`/model`**: Sélectionner un autre modèle Ollama.

## Commandes de Fichiers
- **`/load <fichier>`**: Charger un fichier dans le contexte.
- **`/paste`**: Activer le mode collage pour envoyer du code ou du texte multiligne.
- **`/edit <fichier>`**: Lancer une session d'édition assistée par l'IA sur un fichier.
- **`/files`**: Lister les fichiers actuellement chargés en mémoire.
- **`/pwd`**: Afficher le répertoire de travail actuel.
- **`/cd <dossier>`**: Changer de répertoire de travail.

## Commandes Système
- **`/run <commande>`**: Exécuter une commande shell.
- **`/help`**: Afficher ce message d'aide.
        """
        console.print(Panel(Markdown(help_text), title=" Aide ", border_style="green"))

    def load_file(self, filepath: Path):
        full_path = self.working_directory / filepath
        success, content = self.file_handler.read_file(full_path)

        if success:
            self.loaded_files[str(filepath)] = content
            lexer = self.guess_lexer(filepath)
            preview = Syntax(content[:1000] + ("..." if len(content) > 1000 else ""), lexer, theme="monokai", line_numbers=True)
            console.print(Panel(preview, title=f"📂 Fichier chargé : {filepath}", subtitle=f"{len(content)} caractères", border_style="blue"))
        else:
            console.print(f"[red]Erreur de chargement : {content}[/red]")

    def edit_file(self, filepath: Path):
        """Session d'édition interactive d'un fichier."""
        full_path = self.working_directory / filepath
        success, original_content = self.file_handler.read_file(full_path)

        if not success:
            console.print(f"[red]Impossible de lire le fichier : {original_content}[/red]")
            return

        console.print(f"[cyan]Édition de : {filepath}[/cyan]")
        edit_request = Prompt.ask("[bold]Décrivez les changements souhaités[/bold]")

        prompt = f"""L'utilisateur souhaite modifier le fichier '{filepath}'.
Voici le contenu original :
```
{original_content}
```
Voici la demande de l'utilisateur : "{edit_request}".

Veuillez fournir la réponse en utilisant le format <file_modifications> avec le contenu complet et mis à jour du fichier."""

        system_prompt = self.api.get_system_prompt(list(self.loaded_files.keys()))
        response = self.api.generate(prompt, system_prompt, self.context)
        self.process_response(response)

    def run_command(self, command: str):
        console.print(f"[bold cyan]Exécution : $ {command}[/bold cyan]")
        try:
            with subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.working_directory) as proc:
                if proc.stdout:
                    for line in proc.stdout:
                        console.print(f"[dim]{line.strip()}[/dim]")
                if proc.stderr:
                    for line in proc.stderr:
                        console.print(f"[red]{line.strip()}[/red]")
        except Exception as e:
            console.print(f"[red]Erreur d'exécution : {e}[/red]")

    def list_loaded_files(self):
        if not self.loaded_files:
            console.print("[dim]Aucun fichier chargé.[/dim]")
            return

        table = Table(title=" फाइल्स en Contexte", border_style="purple")
        table.add_column("Chemin", style="cyan")
        table.add_column("Taille (caractères)", style="green")

        for filepath, content in self.loaded_files.items():
            table.add_row(filepath, str(len(content)))
        console.print(table)

    def change_directory(self, new_dir: Path):
        """Change le répertoire de travail."""
        try:
            target_path = (self.working_directory / new_dir).resolve()
            if target_path.is_dir():
                os.chdir(target_path)
                self.working_directory = target_path
                console.print(f"[green]Nouveau répertoire : {self.working_directory}[/green]")
            else:
                console.print(f"[red]Répertoire non trouvé : {target_path}[/red]")
        except Exception as e:
            console.print(f"[red]Erreur : {e}[/red]")


    def guess_lexer(self, filepath: Path, code: str = "") -> str:
        # Utilise le nom de fichier d'abord, puis le contenu si pas de nom
        return Syntax.guess_lexer(str(filepath), code=code)

    # --- NOUVEAUTÉ : Méthode pour gérer la saisie multiligne ---
    def get_pasted_input(self) -> str:
        """Récupère une saisie multiligne de l'utilisateur."""
        console.print("[dim]Collez votre texte. Appuyez sur [bold]Ctrl+D[/bold] (ou Ctrl+Z sous Windows) pour terminer.[/dim]")
        try:
            pasted_text = sys.stdin.read().strip()
            if pasted_text:
                # Devine le langage pour une jolie coloration syntaxique
                lexer = self.guess_lexer(Path("pasted.txt"), code=pasted_text)
                console.print(Panel(
                    Syntax(pasted_text, lexer, theme="monokai", line_numbers=True),
                    title="Texte collé",
                    border_style="green"
                ))
            else:
                console.print("[yellow]Aucune saisie détectée.[/yellow]")
            return pasted_text
        except Exception as e:
            console.print(f"[red]Erreur lors de la lecture de la saisie : {e}[/red]")
            return ""


    # --- AMÉLIORATION : Processeur de réponse intelligent ---
    # Cette fonction est au cœur de la nouvelle logique. Elle détecte si l'IA
    # veut créer, modifier ou simplement discuter.
    def process_response(self, response: str):
        """Analyse la réponse de l'IA pour des actions spéciales (création/modification de fichiers)."""
        try:
            if '<file_modifications>' in response:
                self.handle_file_modifications(response)
            elif '<plan>' in response and '<code>' in response:
                self.handle_file_creation(response)
            else:
                # Si aucune action spéciale n'est détectée, on affiche la réponse telle quelle.
                # La réponse est déjà affichée par le stream, cette partie peut être vide.
                pass
        except Exception as e:
            console.print(f"[bold red]Erreur lors de l'analyse de la réponse de l'IA :[/bold red] {e}")
            console.print("[dim]Affichage de la réponse brute :[/dim]")
            console.print(response)

    def handle_file_modifications(self, response: str):
        """Gère la logique de modification de fichiers proposée par l'IA."""
        console.print("[bold yellow]L'assistant propose des modifications de fichiers...[/bold yellow]")
        # Envelopper dans une balise racine pour un parsing XML valide
        xml_response = f"<root>{response}</root>"
        root = ET.fromstring(xml_response)

        modifications = []
        for file_elem in root.findall('.//file'):
            filepath_str = file_elem.get('path')
            content = (file_elem.text or "").strip()
            if filepath_str and content:
                modifications.append({'path': Path(filepath_str), 'content': content})

        if not modifications:
            console.print("[red]L'IA a tenté de modifier un fichier mais le format était incorrect.[/red]")
            return

        for mod in modifications:
            full_path = self.working_directory / mod['path']
            original_content = ""
            if full_path.exists():
                _, original_content = self.file_handler.read_file(full_path)
            self.file_handler.show_diff(original_content, mod['content'], str(mod['path']))

        if Confirm.ask("\n[bold]Appliquer ces modifications ?[/bold]", default=True):
            for mod in modifications:
                success, msg = self.file_handler.write_file(self.working_directory / mod['path'], mod['content'])
                if success:
                    console.print(f"[green]✓ {msg}[/green]")
                    self.loaded_files[str(mod['path'])] = mod['content'] # Mettre à jour le contexte
                else:
                    console.print(f"[red]✗ Erreur sur {mod['path']}: {msg}[/red]")
        else:
            console.print("[yellow]Modifications annulées.[/yellow]")


    def handle_file_creation(self, response: str):
        """Gère la logique de création de fichier."""
        plan_match = re.search(r'<plan>(.*?)</plan>', response, re.DOTALL)
        code_match = re.search(r'<code>(.*?)</code>', response, re.DOTALL)

        if not (plan_match and code_match): return

        plan = plan_match.group(1).strip()
        code = code_match.group(1).strip()

        console.print(Panel(Markdown(plan), title="Plan de Création Proposé", border_style="blue"))

        if Confirm.ask("\n[bold]Procéder à la création de ce fichier ?[/bold]"):
            filename_match = re.search(r'filename:\s*(\S+)', plan, re.IGNORECASE)
            default_filename = filename_match.group(1) if filename_match else "nouveau_fichier.py"

            filename_str = Prompt.ask("Nom du fichier", default=default_filename)
            filepath = self.working_directory / filename_str

            success, msg = self.file_handler.write_file(filepath, code)
            if success:
                console.print(f"[green]✓ {msg}[/green]")
                if Confirm.ask("Charger ce nouveau fichier en contexte ?", default=True):
                    self.load_file(Path(filename_str))
            else:
                console.print(f"[red]✗ {msg}[/red]")
        else:
            console.print("[yellow]Création annulée.[/yellow]")

    def chat_loop(self):
        """Boucle principale de chat."""
        console.print(Panel(
            "[bold cyan]Bienvenue dans Ollama CLI v2[/bold cyan]\n"
            "[dim]Votre assistant de code interactif. Tapez [yellow]/help[/yellow] pour les commandes.[/dim]",
            border_style="cyan",
            expand=False
        ))

        if not self.select_model():
            return

        self.load_context()

        while True:
            try:
                prompt = self.build_full_prompt()
                user_input = console.input(prompt)

                if not user_input.strip():
                    continue

                # --- AMÉLIORATION : Logique de boucle de chat pour gérer /paste ---
                if user_input.startswith('/'):
                    continue_loop, processed_input = self.handle_command(user_input)

                    if not continue_loop:
                        self.save_context()
                        break  # Quitte la boucle principale

                    if processed_input is not None:
                        # La commande /paste a retourné du texte, on l'utilise comme saisie
                        user_input = processed_input
                        if not user_input: # Si le collage est vide, on reboucle
                            continue
                    else:
                        # C'était une autre commande (ex: /help), on ne l'envoie pas à l'IA
                        continue

                self.conversation_history.append(("user", user_input))

                # Le prompt complet envoyé à l'IA inclut le contenu des fichiers
                full_content_prompt = self.get_files_content_for_prompt() + user_input
                system_prompt = self.api.get_system_prompt(list(self.loaded_files.keys()))

                response = self.api.generate(full_content_prompt, system_prompt, self.context)

                # Le post-traitement se fait après avoir reçu la réponse complète
                self.process_response(response)

                self.conversation_history.append(("assistant", response))
                self.save_context()

            except (KeyboardInterrupt, EOFError):
                self.save_context()
                console.print("\n[yellow]Au revoir ![/yellow]")
                break

    def build_full_prompt(self) -> str:
        """Construit le prompt affiché à l'utilisateur, incluant le contexte."""
        # Pourrait être étendu pour afficher le statut (ex: fichiers chargés)
        return "\n[bold green]Vous[/bold green] > "

    def get_files_content_for_prompt(self) -> str:
        """Concatène le contenu des fichiers chargés pour l'injecter dans le prompt."""
        if not self.loaded_files:
            return ""

        context_str = "CONTEXTE :\n"
        for path, content in self.loaded_files.items():
            context_str += f"--- Contenu de {path} ---\n{content}\n--- Fin de {path} ---\n\n"
        return context_str


def main():
    parser = argparse.ArgumentParser(description="Ollama CLI v2 - Assistant de code interactif.")
    parser.add_argument("--api-url", default="http://localhost:11434", help="URL de l'API Ollama.")
    parser.add_argument("--model", help="Spécifier un modèle à utiliser directement.")
    parser.add_argument("files", nargs="*", help="Fichiers à charger au démarrage.")
    args = parser.parse_args()

    cli = OllamaCLI()
    cli.api.base_url = args.api_url
    if args.model:
        cli.api.model = args.model

    # Charger les fichiers passés en argument
    if args.files:
        for f in args.files:
            cli.load_file(Path(f))

    cli.chat_loop()

if __name__ == "__main__":
    main()





