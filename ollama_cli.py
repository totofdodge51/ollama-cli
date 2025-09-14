#!/usr/bin/env python3
"""
Ollama CLI v12 - Assistant LLM interactif amélioré avec création de projet multi-fichiers.
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Generator
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import ANSI
import subprocess
import difflib
import re
import shutil
from bs4 import BeautifulSoup
from datetime import datetime

# Importations de la bibliothèque Rich pour une interface utilisateur riche
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.text import Text
from pygments.styles import get_all_styles

# Initialisation de la console Rich pour un affichage esthétique
console = Console()

# Fichiers pour l'historique et le contexte
HISTORY_FILE = Path.home() / ".ollama_cli_history"
CONTEXT_FILE = Path.home() / ".ollama_cli_context.json"
CONFIG_FILE = Path.home() / ".ollama_cli_config.json"
CONVO_DIR = Path.home() / ".ollama_cli_conversations"
PROJECTS_DIR = Path.home() / ".ollama_cli_projects"

ASCII_LOGO = r"""
  ██████╗  ██╗      ██╗      ██╗       █████╗  ███╗   ███╗  █████╗      ██████╗██╗     ██╗
 ██╔═══██╗ ██║      ██║      ██║      ██╔══██╗ ████╗ ████║ ██╔══██╗    ██╔════╝██║     ██║
 ██║   ██║ ██║      ██║      ██║      ███████║ ██╔████╔██║ ███████║    ██║     ██║     ██║
 ██║   ██║ ██║      ██║      ██║      ██╔══██║ ██║╚██╔╝██║ ██╔══██║    ██║     ██║     ██║
 ╚██████╔╝ ███████╗ ███████╗ ███████╗ ██║  ██║ ██║ ╚═╝ ██║ ██║  ██║    ╚██████╗███████╗██║
  ╚═════╝  ╚══════╝ ╚══════╝ ╚══════╝ ╚═╝  ╚═╝ ╚═╝     ╚═╝ ╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝
"""

THEMES = {
    "dark": {
        "logo": "bold cyan",
        "header_subtitle": "dim",
        "header_border": "cyan",
        "user_prompt": "bold green",
        "command_panel_border": "yellow",
        "user_panel_border": "green",
        "assistant_panel_border": "cyan",
        "info_panel_border": "green",
        "warn_panel_border": "yellow",
        "error_panel_border": "red",
        "success": "green",
        "warning": "yellow",
        "error": "red",
        "info": "dim",
        "table_title": "cyan",
        "table_header": "green",
        "table_index": "cyan",
    },
    "light": {
        "logo": "bold blue",
        "header_subtitle": "dim",
        "header_border": "blue",
        "user_prompt": "bold blue",
        "command_panel_border": "dark_orange",
        "user_panel_border": "blue",
        "assistant_panel_border": "black",
        "info_panel_border": "dark_green",
        "warn_panel_border": "dark_orange",
        "error_panel_border": "red",
        "success": "dark_green",
        "warning": "dark_orange",
        "error": "red",
        "info": "dim",
        "table_title": "blue",
        "table_header": "dark_green",
        "table_index": "blue",
    }
}

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
        self.system_prompt_template = '''Tu es un assistant de terminal expert en développement de logiciels.

INSTRUCTIONS GÉNÉRALES:
- **Privilégie les réponses directes:** Pour les questions simples, les salutations ou les discussions, réponds naturellement sans utiliser d'outils.
- **Utilise les outils pour les actions:** N'utilise les outils ci-dessous que lorsque l'utilisateur te demande explicitement d'effectuer une action (créer/modifier un fichier, exécuter une commande, etc.).
- **Ne donne jamais d'instructions à l'utilisateur.** Si une action est nécessaire, fais-la toi-même en utilisant les outils.
- **Utilise `{python_command}` pour exécuter du code Python.**

INSTRUCTIONS POUR LES OUTILS:
- Pour toute demande de **création** d'un ou plusieurs fichiers, TU DOIS utiliser le format `<project_creation>`.
- Pour toute demande de **modification** d'un ou plusieurs fichiers, TU DOIS utiliser le format `<file_modifications>`.
- Pour toute demande d'**exécution** d'une commande, TU DOIS utiliser le format `<shell>`.
- Pour les **commandes de longue durée** (serveurs, etc.), TU DOIS les lancer dans un nouveau terminal: `<shell>{terminal_launcher} bash -c 'suite de commandes'</shell>`.

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
   - Commande Python: `<shell>{python_command} mon_script.py</shell>`
   - Serveur ou commande longue: `<shell>{terminal_launcher} bash -c 'cd mon_dossier && ma_commande_longue'</shell>`

Fichiers actuellement chargés en contexte (lisibles pour toi): {loaded_files}
'''

    def get_system_prompt(self, loaded_files: List[str], terminal_launcher: str, python_command: str) -> str:
        files_str = ", ".join(loaded_files) if loaded_files else "aucun"
        return self.system_prompt_template.format(
            loaded_files=files_str,
            terminal_launcher=terminal_launcher,
            python_command=python_command
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

    def generate(self, prompt: str, system_prompt: str, context: Optional[List] = None) -> Generator[str, None, None]:
        payload = {"model": self.model, "prompt": prompt, "system": system_prompt, "stream": True, "context": context or []}
        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, stream=True, timeout=(5, 300))
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        token = data.get("response", "")
                        if token:
                            yield token
                        if data.get("done") and "context" in data:
                            self.last_context = data.get("context", [])
                    except json.JSONDecodeError:
                        continue
        except requests.exceptions.RequestException as e:
            console.print(f"[red]Erreur API Ollama : {e}[/red]")

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
        self.python_command = "python3"  # Ajout de la commande python
        self.syntax_theme = "monokai"
        self.ui_theme_name = "dark"
        self.refresh_rate = 20  # Default refresh rate
        self.theme = THEMES[self.ui_theme_name]
        self.load_config()
        CONVO_DIR.mkdir(exist_ok=True)
        PROJECTS_DIR.mkdir(exist_ok=True)

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.terminal_launcher = config.get("terminal_launcher", self.terminal_launcher)
                    self.python_command = config.get("python_command", self.python_command)
                    self.api.web_enabled = config.get("web_enabled", True)
                    self.syntax_theme = config.get("syntax_theme", self.syntax_theme)
                    self.ui_theme_name = config.get("ui_theme_name", self.ui_theme_name)
                    self.refresh_rate = config.get("refresh_rate", self.refresh_rate)
                    if self.ui_theme_name not in THEMES:
                        self.ui_theme_name = "dark"
                    self.theme = THEMES[self.ui_theme_name]
            except (json.JSONDecodeError, IOError):
                self.theme = THEMES[self.ui_theme_name] # Ensure theme is set on failure

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config_data = {
                    "terminal_launcher": self.terminal_launcher,
                    "python_command": self.python_command,
                    "web_enabled": self.api.web_enabled,
                    "syntax_theme": self.syntax_theme,
                    "ui_theme_name": self.ui_theme_name,
                    "refresh_rate": self.refresh_rate
                }
                json.dump(config_data, f, indent=2)
        except IOError:
            pass

    def _get_header_panel(self):
        web_status = f"[{self.theme['success']}]Activé[/]" if self.api.web_enabled else f"[{self.theme['error']}]Désactivé[/]"
        subtitle = f"[{self.theme['header_subtitle']}]Modèle: [bold yellow]{self.api.model}[/] | Web: {web_status} | [yellow]/help[/] pour les commandes." 
        return Panel(Text(ASCII_LOGO, style=self.theme["logo"], justify="center"), title="Ollama CLI v12", subtitle=subtitle, border_style=self.theme["header_border"])

    def _update_display(self):
        max_history_items = 30
        self.chat_renderables = self.chat_renderables[-max_history_items:]
        console.clear()
        console.print(self._get_header_panel())
        for renderable in self.chat_renderables:
            console.print(renderable)

    def handle_config_command(self):
        current_web = f"[{self.theme['success']}]Activé[/]" if self.api.web_enabled else f"[{self.theme['error']}]Désactivé[/]"
        config_panel = Panel(
            f"Lanceur de terminal: `[cyan]{self.terminal_launcher}[/]`\n"
            f"Commande Python: `[cyan]{self.python_command}[/]`\n"
            f"Accès Web: {current_web}\n"
            f"Taux de rafraîchissement: `[cyan]{self.refresh_rate}[/]` img/sec",
            title="Configuration Actuelle",
            border_style=self.theme["info_panel_border"]
        )
        self.chat_renderables.append(config_panel)
        self._update_display()

        if Confirm.ask("\n[bold]Modifier l\'accès web ?[/bold]"):
            self.api.web_enabled = not self.api.web_enabled
            new_status = f"[{self.theme['success']}]Activé[/]" if self.api.web_enabled else f"[{self.theme['error']}]Désactivé[/]"
            self.chat_renderables.append(Panel(f"Accès web mis à jour: {new_status}", border_style=self.theme["success"]))

        if Confirm.ask("\n[bold]Modifier le lanceur de terminal ?[/bold]"):
            new_launcher = Prompt.ask("Entrez la nouvelle commande de lancement", default=self.terminal_launcher)
            self.terminal_launcher = new_launcher
            self.chat_renderables.append(Panel(f"Lanceur de terminal mis à jour: `[cyan]{self.terminal_launcher}[/]`", border_style=self.theme["success"]))

        if Confirm.ask("\n[bold]Modifier la commande Python ?[/bold]"):
            new_cmd = Prompt.ask("Entrez la nouvelle commande Python", default=self.python_command)
            self.python_command = new_cmd
            self.chat_renderables.append(Panel(f"Commande Python mise à jour: `[cyan]{self.python_command}[/]`", border_style=self.theme["success"]))

        if Confirm.ask("\n[bold]Modifier le taux de rafraîchissement ?[/bold]"):
            new_rate = IntPrompt.ask(
                "Entrez le nouveau taux (images par seconde)",
                default=self.refresh_rate
            )
            if new_rate > 0:
                self.refresh_rate = new_rate
                self.chat_renderables.append(Panel(f"Taux de rafraîchissement mis à jour: `[cyan]{self.refresh_rate}[/]`", border_style=self.theme["success"]))
            else:
                self.chat_renderables.append(Panel("[red]Le taux doit être un nombre positif.[/red]", border_style=self.theme["error"]))

        self.save_config()
        self.chat_renderables.append(Panel(f"[{self.theme['success']}]Configuration sauvegardée.[/{self.theme['success']}]"))
        self._update_display()

    def handle_theme_command(self):
        themes = sorted(list(THEMES.keys()))
        
        table = Table(title="🎨 Thèmes d\'interface disponibles")
        table.add_column("Index", style=self.theme["table_index"])
        table.add_column("Nom du Thème", style=self.theme["table_header"])
        for i, theme_name in enumerate(themes, 1):
            table.add_row(str(i), theme_name)
        
        self.chat_renderables.append(table)
        self._update_display()

        try:
            choice = Prompt.ask(
                f"\nSélectionnez un thème (actuel: [bold {self.theme['warning']}] {self.ui_theme_name}[/bold {self.theme['warning']}])",
                choices=[str(i) for i in range(1, len(themes) + 1)]
            )
            self.ui_theme_name = themes[int(choice) - 1]
            self.theme = THEMES[self.ui_theme_name]
            self.save_config()
            self.chat_renderables.append(Panel(f"[{self.theme['success']}]Thème changé en: {self.ui_theme_name}.[/{self.theme['success']}]"))
            self._update_display()
        except (KeyboardInterrupt, EOFError):
            self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Sélection annulée.[/{self.theme['warning']}]"))
            self._update_display()

    def handle_project_command(self, args: List[str]):
        if not args:
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /project [save|load|list] [nom_projet][/{self.theme['error']}]"))
            self._update_display()
            return

        subcommand = args[0].lower()
        project_name = args[1] if len(args) > 1 else None

        if subcommand == 'list':
            self.list_projects()
        elif subcommand == 'save':
            if project_name: self.save_project(project_name)
            else: self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /project save <nom_projet>[/{self.theme['error']}]"))
        elif subcommand == 'load':
            if project_name: self.load_project(project_name)
            else: self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /project load <nom_projet>[/{self.theme['error']}]"))
        elif subcommand == 'delete':
            if project_name: self.delete_project(project_name)
            else: self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /project delete <nom_projet>[/{self.theme['error']}]"))
        else:
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Sous-commande de projet inconnue: {subcommand}[/{self.theme['error']}]"))
        
        self._update_display()

    def handle_web_command(self, query: str):
        if not query:
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /web <recherche>[/{self.theme['error']}]"))
            self._update_display()
            return

        # 1. Query Refinement
        refined_query = query
        with console.status(f"[bold {self.theme['warning']}]Optimisation de la requête...[/bold {self.theme['warning']}]"):
            refinement_prompt = f"Compte tenu de la question de l'utilisateur, crée une requête de moteur de recherche concise et efficace pour trouver la réponse la plus pertinente. Ne renvoie que la requête, sans aucune autre explication. Question de l'utilisateur : \"{query}\". Requête de recherche :"
            refinement_system_prompt = "Tu es un expert en optimisation de requêtes de recherche."
            
            try:
                payload = {"model": self.api.model, "prompt": refinement_prompt, "system": refinement_system_prompt, "stream": False}
                response = requests.post(f"{self.api.base_url}/api/generate", json=payload, timeout=30)
                response.raise_for_status()
                data = response.json()
                refined_query = data.get("response", query).strip().replace('"', '')
            except (requests.exceptions.RequestException, json.JSONDecodeError):
                pass  # If refinement fails, just use the original query

        with console.status(f"[bold {self.theme['warning']}]Recherche web en cours pour: {refined_query}...[/bold {self.theme['warning']}]"):
            results = self.api.search_web(refined_query, num_results=5)

        if not results:
            self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Aucun résultat trouvé pour: {refined_query}[/{self.theme['warning']}]"))
            self._update_display()
            return

        search_context = f"Requête de l'utilisateur: {query}\nRequête de recherche optimisée: {refined_query}\n\nRésultats de recherche web:\n"
        
        with console.status(f"[bold {self.theme['warning']}]Analyse des pages web...[/bold {self.theme['warning']}]") as status:
            for i, result in enumerate(results[:3], 1):
                title = result.get('title', 'Sans titre')
                snippet = result.get('snippet', 'Pas de description.')
                url = result.get('url', '')
                
                status.update(f"[bold {self.theme['warning']}]Analyse de : {url}[/bold {self.theme['warning']}]")
                
                search_context += f"--- Source [{i}] ---\n"
                search_context += f"Titre: {title}\n"
                search_context += f"URL: {url}\n"
                search_context += f"Snippet: {snippet}\n"

                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'}
                    page_response = requests.get(url, headers=headers, timeout=15)
                    page_response.raise_for_status()
                    
                    soup = BeautifulSoup(page_response.content, 'html.parser')
                    
                    for script_or_style in soup(["script", "style"]):
                        script_or_style.decompose()

                    text = soup.get_text()
                    lines = (line.strip() for line in text.splitlines())
                    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                    text = '\n'.join(chunk for chunk in chunks if chunk)
                    
                    max_length = 4000
                    if len(text) > max_length:
                        text = text[:max_length] + "\n[...]"

                    search_context += f"Contenu de la page (extrait):\n{text}\n"

                except requests.exceptions.RequestException as e:
                    search_context += "Contenu de la page: [Erreur: Le contenu complet de la page n'a pas pu être chargé. L'analyse doit se baser sur le titre et le snippet.]\n"
                except Exception as e:
                    search_context += "Contenu de la page: [Erreur: Le contenu de la page est invalide ou n'a pas pu être analysé. L'analyse doit se baser sur le titre et le snippet.]\n"
                
                search_context += f"--- Fin de la Source [{i}] ---\n\n"

        synthesis_prompt = f"""Tu es un assistant de recherche. Ton but est de répondre à la question de l'utilisateur en te basant sur les sources web fournies.

Question: "{query}"

--- SOURCES WEB ---
{search_context}
--- FIN DES SOURCES ---

INSTRUCTIONS:
1.  Analyse attentivement les sources pour trouver la réponse la plus précise possible.
2.  Si tu trouves une réponse directe (par exemple, une température, une date, un nom), extrais-la et présente-la clairement. Cite tes sources avec `[Source X]`.
3.  Si les sources ne contiennent pas de réponse directe mais donnent des informations connexes (par exemple, des liens vers des prévisions météo, des articles de contexte), synthétise ces informations et explique comment l'utilisateur pourrait trouver la réponse. Par exemple: "Les sources ne donnent pas la température exacte, mais le site Météo France [Source X] semble avoir les prévisions détaillées."
4.  Si les sources sont totalement hors sujet ou inutilisables, admets que tu n'as pas pu trouver de réponse.
"""
        
        synthesis_system_prompt = "Tu es un assistant de recherche expert. Tu suis les instructions de l'utilisateur à la lettre pour analyser les sources fournies et construire la meilleure synthèse possible pour répondre à la question posée."

        summary_text = ""
        with console.status(f"[bold {self.theme['warning']}]Synthèse des résultats en cours...[/bold {self.theme['warning']}]"):
            summary_generator = self.api.generate(synthesis_prompt, synthesis_system_prompt, context=None)
            if summary_generator:
                summary_text = "".join(list(summary_generator))

        if summary_text:
            summary = summary_text
            for i, result in enumerate(results, 1):
                placeholder = f"[Source {i}]"
                url = result.get('url', '')
                if url:
                    replacement = f"[Source {i}]({url})"
                    summary = summary.replace(placeholder, replacement)
            
            stray_link_pattern = re.compile(r'\s*\(\s*//duckduckgo\.com/l/.*\)\s*', re.MULTILINE)
            summary = stray_link_pattern.sub('', summary)

            summary_panel = Panel(Markdown(summary), title=f"Synthèse Web pour '{query}'", border_style=self.theme["assistant_panel_border"])
            self.chat_renderables.append(summary_panel)
            
            history_entry = f"J'ai effectué une recherche web pour '{query}' et voici la synthèse que j'ai générée :\n{summary}"
            self.conversation_history.append({"role": "assistant", "content": history_entry})
        else:
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Impossible de synthétiser les résultats.[/{self.theme['error']}]"))

        self._update_display()

    def list_projects(self):
        if not PROJECTS_DIR.exists() or not any(PROJECTS_DIR.iterdir()):
            self.chat_renderables.append(Panel(f"[{self.theme['info']}]Aucun projet sauvegardé.[/{self.theme['info']}]"))
            return

        table = Table(title="💾 Projets Sauvegardés", title_style=self.theme["table_title"])
        table.add_column("Nom du Projet", style=self.theme["table_index"])
        for project_path in PROJECTS_DIR.iterdir():
            if project_path.is_dir():
                table.add_row(project_path.name)
        self.chat_renderables.append(table)

    def delete_project(self, name: str):
        project_path = PROJECTS_DIR / name
        if not project_path.is_dir():
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Projet '{name}' non trouvé.[/{self.theme['error']}]"))
            return

        if Confirm.ask(f"\n[bold {self.theme['warning']}]Êtes-vous sûr de vouloir supprimer le projet '{name}' ? Cette action est irréversible.[/bold {self.theme['warning']}]"):
            try:
                shutil.rmtree(project_path)
                self.chat_renderables.append(Panel(f"[{self.theme['success']}]Projet '{name}' supprimé avec succès.[/{self.theme['success']}]"))
            except Exception as e:
                self.chat_renderables.append(Panel(f"[{self.theme['error']}]Erreur lors de la suppression du projet '{name}': {e}[/{self.theme['error']}]"))
        else:
            self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Suppression annulée.[/{self.theme['warning']}]"))
        self._update_display()

    def save_project(self, name: str):
        project_path = PROJECTS_DIR / name
        files_path = project_path / 'files'
        
        try:
            project_path.mkdir(exist_ok=True)
            files_path.mkdir(exist_ok=True)

            # Sauvegarder les métadonnées
            metadata = {
                'model': self.api.model,
                'files': list(self.loaded_files.keys()),
                'timestamp': datetime.now().isoformat()
            }
            with open(project_path / 'project.json', 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)

            # Sauvegarder l'historique
            with open(project_path / 'history.json', 'w', encoding='utf-8') as f:
                json.dump(self.conversation_history, f, indent=2)

            # Sauvegarder les fichiers
            for file_path_str, content in self.loaded_files.items():
                target_file = files_path / file_path_str
                target_file.parent.mkdir(parents=True, exist_ok=True)
                with open(target_file, 'w', encoding='utf-8') as f:
                    f.write(content)

            self.chat_renderables.append(Panel(f"[{self.theme['success']}]Projet '{name}' sauvegardé avec succès.[/{self.theme['success']}]"))
        except Exception as e:
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Erreur lors de la sauvegarde du projet '{name}': {e}[/{self.theme['error']}]"))

    def load_project(self, name: str):
        project_path = PROJECTS_DIR / name
        if not project_path.is_dir():
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Projet '{name}' non trouvé.[/{self.theme['error']}]"))
            return

        try:
            self.clear_context()

            # Charger les métadonnées
            with open(project_path / 'project.json', 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            self.api.model = metadata.get('model', self.api.model)

            # Charger l'historique
            with open(project_path / 'history.json', 'r', encoding='utf-8') as f:
                self.conversation_history = json.load(f)

            # Charger les fichiers
            files_to_load = metadata.get('files', [])
            for file_path_str in files_to_load:
                file_path = project_path / 'files' / file_path_str
                if file_path.exists():
                    _, content = self.file_handler.read_file(file_path)
                    self.loaded_files[file_path_str] = content
            
            self.chat_renderables.append(Panel(f"[{self.theme['success']}]Projet '{name}' chargé avec succès.[/{self.theme['success']}]"))
            # Recréer l'affichage avec l'historique chargé
            for message in self.conversation_history:
                if message['role'] == 'user':
                    self.chat_renderables.append(Panel(message['content'], title="Vous", border_style=self.theme["user_panel_border"]))
                else:
                    # Simplification: on ne re-traite pas la réponse, on l'affiche
                    self.chat_renderables.append(Panel(Markdown(message['content']), title="Assistant", border_style=self.theme["assistant_panel_border"]))

        except Exception as e:
            self.clear_context()
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Erreur lors du chargement du projet '{name}': {e}[/{self.theme['error']}]"))

    def handle_command(self, command: str) -> Tuple[bool, Optional[str]]:
        parts = command.split()
        cmd = parts[0].lower()
        if cmd in ["/quit", "/exit", "/q"]:
            return False, None
        
        self.chat_renderables.append(Panel(command, title="Commande", title_align="left", border_style=self.theme["command_panel_border"]))

        if cmd == "/help":
            self.chat_renderables.append(self._get_help_content())
        elif cmd == "/clear":
            self.clear_context()
            self.chat_renderables.append(Panel(f"[{self.theme['success']}]Contexte de la conversation effacé.[/{self.theme['success']}]"))
        elif cmd == "/model":
            self.select_model()
            return True, None
        elif cmd == "/theme":
            self.handle_theme_command()
            return True, None
        elif cmd == "/config":
            self.handle_config_command()
            return True, None
        elif cmd == "/project":
            self.handle_project_command(parts[1:])
            return True, None
        elif cmd == "/web":
            if len(parts) > 1: self.handle_web_command(" ".join(parts[1:]))
            else: self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /web <recherche>[/{self.theme['error']}]"))
            return True, None
        elif cmd == "/load":
            if len(parts) > 1: self.load_file(" ".join(parts[1:]))
            else: self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /load <filepath>[/{self.theme['error']}]"))
        elif cmd == "/files":
            self.chat_renderables.append(self._get_files_table())
        elif cmd == "/run":
            if len(parts) > 1: self.run_command(" ".join(parts[1:]))
            else: self.chat_renderables.append(Panel(f"[{self.theme['error']}]Usage: /run <command>[/{self.theme['error']}]"))
        else:
            self.chat_renderables.append(Panel(f"[{self.theme['error']}]Commande inconnue : {cmd}. Tapez /help.[/{self.theme['error']}]"))

        self._update_display()
        return True, None

    def _get_help_content(self):
        help_text = """# Aide de Ollama CLI v12
- `/quit, /exit, /q`: Quitter.
- `/clear`: Effacer l\'historique et les fichiers.
- `/model`: Sélectionner un autre modèle.
- `/theme`: Changer le thème de l\'interface utilisateur.
- `/config`: Modifier la configuration (terminal, accès web).
- `/project [list|save|load|delete]`: Gérer les projets.
- `/web <recherche>`: Effectuer une recherche web.
- `/load <fichier>`: Charger un fichier en contexte.
- `/files`: Lister les fichiers chargés.
- `/run <commande>`: Exécuter une commande shell.
"""
        return Panel(Markdown(help_text), title="Aide", border_style=self.theme["info_panel_border"])

    def select_model(self) -> bool:
        models = self.api.list_models()
        if not models: return False
        table = Table(title="🤖 Modèles Disponibles", title_style=self.theme["table_title"])
        table.add_column("Index", style=self.theme["table_index"])
        table.add_column("Nom du Modèle", style=self.theme["table_header"])
        for i, model in enumerate(models, 1): table.add_row(str(i), model)
        
        self.chat_renderables.append(table)
        self._update_display()

        try:
            choice = Prompt.ask("Sélectionnez un modèle", choices=[str(i) for i in range(1, len(models) + 1)])
            self.api.model = models[int(choice) - 1]
            self.clear_context()
            self.chat_renderables.append(Panel(f"[{self.theme['success']}]Modèle changé en: {self.api.model}. Contexte effacé.[/{self.theme['success']}]"))
            self._update_display()
            return True
        except (KeyboardInterrupt, EOFError):
            self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Sélection annulée.[/{self.theme['warning']}]"))
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
                self.chat_renderables.append(Panel(f"[{self.theme['error']}]Erreur : Le chemin {path_obj} n'existe pas.[/{self.theme['error']}]"))
                self._update_display()
                return
            if path_obj.is_dir():
                files_to_load = [f for f in path_obj.rglob('*') if f.is_file()]
            else:
                files_to_load = [path_obj]

        if not files_to_load:
            self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Aucun fichier trouvé pour : {path_str}[/{self.theme['warning']}]"))
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
            self.chat_renderables.append(Panel(f"[{self.theme['success']}]{loaded_count} fichier(s) chargé(s) depuis : {path_str}[/{self.theme['success']}]"))
        if error_count > 0:
            self.chat_renderables.append(Panel(f"[{self.theme['warning']}]{error_count} fichier(s) n'ont pas pu être chargés (ex: binaires).[/{self.theme['warning']}]"))
        
        self._update_display()

    def _get_files_table(self):
        if not self.loaded_files:
            return Panel(f"[{self.theme['info']}]Aucun fichier chargé.[/{self.theme['info']}]", title="📁 Fichiers en Contexte")
        table = Table(title="📁 Fichiers en Contexte", title_style=self.theme["table_title"])
        table.add_column("Chemin", style=self.theme["table_index"])
        for filepath in self.loaded_files: table.add_row(filepath)
        return table

    def run_command(self, command: str):
        self.chat_renderables.append(Panel(f"[bold {self.theme['warning']}]L'assistant propose d'exécuter :[/bold {self.theme['warning']}] [{self.theme['logo']}]{command}[/{self.theme['logo']}]"))
        self._update_display()
        if Confirm.ask("\n[bold]Exécuter cette commande ?[/bold]"):
            try:
                if command.strip().startswith(self.terminal_launcher):
                    subprocess.Popen(command, shell=True, cwd=self.working_directory)
                    self.chat_renderables.append(Panel(f"[{self.theme['success']}]Commande lancée dans un nouveau terminal.[/{self.theme['success']}]"))
                else:
                    result = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=self.working_directory)
                    output = result.stdout.strip()
                    error = result.stderr.strip()

                    # --- NEW LOGIC ---
                    if not output and not error:
                        if result.returncode == 0:
                            msg = f"[{self.theme['success']}]Commande exécutée avec succès (aucune sortie).[/]"
                            style = self.theme['success']
                        else:
                            msg = f"[{self.theme['error']}]Commande terminée avec le code d'erreur {result.returncode} (aucune sortie).[/]"
                            style = self.theme['error']
                        res_panel = Panel(msg, title="Résultat", border_style=style)
                    else:
                        renderables = []
                        if output:
                            renderables.append(Panel(output, title="Sortie", border_style=self.theme['info_panel_border']))
                        if error:
                            renderables.append(Panel(error, title="Erreur", border_style=self.theme['error_panel_border']))
                        
                        main_border_style = self.theme['error_panel_border'] if result.returncode != 0 else self.theme['success']
                        res_panel = Panel(Group(*renderables), title="Résultat", border_style=main_border_style)
                    
                    self.chat_renderables.append(res_panel)
            except Exception as e:
                self.chat_renderables.append(Panel(f"[{self.theme['error']}]Erreur d'exécution: {e}[/{self.theme['error']}]"))
        else:
            self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Exécution annulée.[/{self.theme['warning']}]"))
        self._update_display()

    def is_valid_python(self, code: str) -> Tuple[bool, Optional[str]]:
        """Vérifie la syntaxe du code Python."""
        try:
            compile(code, '<string>', 'exec')
            return True, None
        except SyntaxError as e:
            return False, str(e)

    def process_response(self, response: str, is_correction_attempt: bool = False):
        response = response.strip()
        if not response:
            return

        self.chat_renderables.append(Panel(Text(response), title="Assistant (Réponse Brute)", border_style=self.theme["assistant_panel_border"]))
        self._update_display()

        if '<project_creation>' in response:
            self.handle_project_creation(response, is_correction_attempt)
        elif '<file_modifications>' in response:
            self.handle_file_modifications(response, is_correction_attempt)
        elif '<shell>' in response:
            self.handle_shell_execution(response)
        elif self.handle_fallback_code_block(response, is_correction_attempt):
            pass

    def _attempt_self_correction(self, file_path: str, invalid_code: str, error_message: str):
        """Tente de demander au modèle de corriger son propre code invalide."""
        self.chat_renderables.append(
            Panel(f"Le code proposé pour `[bold]{file_path}[/bold]` est invalide. Tentative d'auto-correction...",
                  title="⚠️ Validation Échouée", border_style=self.theme["warn_panel_border"])
        )
        self._update_display()

        correction_prompt = f"""
Ta proposition précédente pour le fichier '{file_path}' contient une erreur de syntaxe.
Voici le code invalide que tu as généré :
```python
{invalid_code}
```
Et voici le message d'erreur exact :
```
{error_message}
```
S'il te plaît, analyse l'erreur et fournis une nouvelle version qui corrige le problème.
La nouvelle version doit être complète et syntaxiquement valide.
Utilise le format <file_modifications> ou <project_creation> pour fournir le contenu corrigé et complet du fichier '{file_path}'.
"""
        
        system_prompt = self.api.get_system_prompt(
            list(self.loaded_files.keys()),
            self.terminal_launcher,
            self.python_command
        )
        
        full_response = ""
        from rich.live import Live
        from rich.text import Text

        response_text = Text("")
        panel = Panel(response_text, title="Assistant (Correction)", border_style=self.theme["assistant_panel_border"])

        try:
            with console.status("[bold yellow]Demande de correction envoyée au modèle...[/bold yellow]"):
                with Live(panel, vertical_overflow="visible", refresh_per_second=self.refresh_rate) as live:
                    for token in self.api.generate(correction_prompt, system_prompt, self.api.last_context):
                        full_response += token
                        response_text.append(token)
        except Exception as e:
            console.print(f"[red]Erreur durant la tentative de correction: {e}[/red]")
            return

        console.print()

        if full_response.strip():
            self.conversation_history.append({"role": "user", "content": "J'ai demandé une correction pour le code précédent."})
            self.conversation_history.append({"role": "assistant", "content": full_response})
            self.process_response(full_response, is_correction_attempt=True)

    def handle_shell_execution(self, response: str):
        commands = re.findall(r'<shell>(.*?)</shell>', response, re.DOTALL)
        if not commands:
            return

        if len(commands) > 1:
            explanation_panel = Panel(f"L'assistant a proposé d'exécuter les {len(commands)} commandes suivantes séquentiellement.", title="Proposition d'Exécution Multiple", border_style=self.theme["info_panel_border"])
            self.chat_renderables.append(explanation_panel)
            self._update_display()
        
        for command in commands:
            self.run_command(command.strip())

    def handle_fallback_code_block(self, response: str, is_correction_attempt: bool = False) -> bool:
        code_blocks = re.findall(r'```(\w*)?\n?(.*?)```', response, re.DOTALL)
        if not code_blocks:
            return False

        is_all_shell = True
        shell_commands = []
        for lang, content in code_blocks:
            content = content.strip()
            if not content:
                continue
            if lang.lower() in ['shell', 'bash', 'sh']:
                shell_commands.append(content)
            else:
                is_all_shell = False
                break

        if is_all_shell and shell_commands:
            title = "Proposition d'Exécution"
            if len(shell_commands) > 1:
                title = "Proposition d'Exécution Multiple"
            
            msg = f"L'assistant a proposé d'exécuter {len(shell_commands)} commande(s) (détection de secours)."
            explanation_panel = Panel(msg, title=title, border_style=self.theme["info_panel_border"])
            self.chat_renderables.append(explanation_panel)
            self._update_display()

            for command in shell_commands:
                self.run_command(command)
            return True

        lang, new_content = code_blocks[0]
        new_content = new_content.strip()
        if not new_content:
            return False

        if not self.loaded_files:
            explanation_panel = Panel("[bold yellow]L'assistant a fourni un bloc de code sans instructions précises (détection de secours).[/bold yellow]", title="Proposition de Création", border_style=self.theme["warn_panel_border"])
            self.chat_renderables.append(explanation_panel)
            
            code_panel = Panel(Syntax(new_content, (lang or "text"), theme=self.syntax_theme, line_numbers=True), title="Code Proposé")
            self.chat_renderables.append(code_panel)
            self._update_display()

            try:
                filename = Prompt.ask("\n[bold]Entrez un nom de fichier pour sauvegarder ce code (ou laissez vide pour annuler)[/bold]")
                if not filename:
                    self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Création annulée.[/{self.theme['warning']}]"))
                    self._update_display()
                    return True

                if filename.endswith('.py'):
                    is_valid, error_msg = self.is_valid_python(new_content)
                    if not is_valid:
                        if is_correction_attempt:
                            error_panel = Panel(f"La tentative d'auto-correction pour `[bold]{filename}[/bold]` a encore échoué.\n[bold]Détail :[/bold] {error_msg}", title="❌ Correction Échouée", border_style=self.theme["error_panel_border"])
                            self.chat_renderables.append(error_panel)
                        elif Confirm.ask(f"\n[bold yellow]La suggestion pour `{filename}` contient une erreur de syntaxe. Tenter une auto-correction ?[/bold yellow]"):
                            self._attempt_self_correction(filename, new_content, error_msg)
                            return True
                        else:
                            error_panel = Panel(f"La création du fichier `[bold]{filename}[/bold]` a été rejetée.\n[bold]Détail :[/bold] {error_msg}", title="❌ Validation Échouée", border_style=self.theme["error_panel_border"])
                            self.chat_renderables.append(error_panel)
                        self._update_display()
                        return True

                filepath = self.working_directory / filename
                if Confirm.ask(f"\n[bold]Confirmer la création du fichier `{filename}` ?[/bold]"):
                    success, msg = self.file_handler.write_file(filepath, new_content)
                    console.print(f"[{self.theme['success']}]✓ {msg}[/{self.theme['success']}]") if success else console.print(f"[{self.theme['error']}]✗ {msg}[/{self.theme['error']}]")
                    if success:
                        console.print(f"\n[bold {self.theme['success']}]Chargement automatique du fichier créé en contexte...[/bold {self.theme['success']}]")
                        self.load_file(filename)
                    self._update_display()
                else:
                    self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Création annulée.[/{self.theme['warning']}]"))
                    self._update_display()
            except (KeyboardInterrupt, EOFError):
                self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Création annulée.[/{self.theme['warning']}]"))
                self._update_display()
            return True
        else:
            path_to_modify = None
            if len(self.loaded_files) == 1:
                path_to_modify = list(self.loaded_files.keys())[0]
            else:
                self.chat_renderables.append(Panel("[bold yellow]L'assistant a suggéré une modification mais plusieurs fichiers sont ouverts. Lequel voulez-vous modifier ?[/bold yellow]", title="Précision Requise", border_style=self.theme["warn_panel_border"]))
                file_list = sorted(list(self.loaded_files.keys()))
                table = Table(title="Fichiers en Contexte", title_style=self.theme["table_title"])
                table.add_column("Index", style=self.theme["table_index"])
                table.add_column("Chemin", style=self.theme["table_header"])
                for i, filename in enumerate(file_list, 1):
                    table.add_row(str(i), filename)
                self.chat_renderables.append(table)
                self._update_display()
                try:
                    choice = Prompt.ask("\nSélectionnez le fichier à modifier (ou pressez Entrée pour annuler)", choices=[str(i) for i in range(1, len(file_list) + 1)] + [""], default="")
                    if not choice:
                        self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Modification annulée.[/{self.theme['warning']}]"))
                        self._update_display()
                        return True
                    path_to_modify = file_list[int(choice) - 1]
                except (KeyboardInterrupt, EOFError):
                    self.chat_renderables.append(Panel(f"[{self.theme['warning']}]Modification annulée.[/{self.theme['warning']}]"))
                    self._update_display()
                    return True

            if not path_to_modify:
                return False

            if path_to_modify.endswith('.py'):
                is_valid, error_msg = self.is_valid_python(new_content)
                if not is_valid:
                    if is_correction_attempt:
                        error_panel = Panel(f"La tentative d'auto-correction pour `[bold]{path_to_modify}[/bold]` a encore échoué.\n[bold]Détail :[/bold] {error_msg}", title="❌ Correction Échouée", border_style=self.theme["error_panel_border"])
                        self.chat_renderables.append(error_panel)
                    elif Confirm.ask(f"\n[bold yellow]La suggestion pour `{path_to_modify}` contient une erreur de syntaxe. Tenter une auto-correction ?[/bold yellow]"):
                        self._attempt_self_correction(path_to_modify, new_content, error_msg)
                        return True
                    else:
                        error_panel = Panel(f"La modification pour `[bold]{path_to_modify}[/bold]` a été rejetée.\n[bold]Détail :[/bold] {error_msg}", title="❌ Validation Échouée", border_style=self.theme["error_panel_border"])
                        self.chat_renderables.append(error_panel)
                    self._update_display()
                    return True

            original_content = self.loaded_files.get(path_to_modify, "")
            explanation_panel = Panel("[bold yellow]L'assistant a suggéré une modification (détection de secours).[/bold yellow]", title="Proposition de Modification", border_style=self.theme["warn_panel_border"])
            self.chat_renderables.append(explanation_panel)

            diff = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path_to_modify}",
                tofile=f"b/{path_to_modify}",
            )
            diff_text = "".join(diff)
            diff_panel = Panel(Syntax(diff_text, "diff", theme=self.syntax_theme, line_numbers=True), title=f"Changements proposés pour {path_to_modify}")
            self.chat_renderables.append(diff_panel)
            self._update_display()

            if Confirm.ask(f"\n[bold]Appliquer cette modification au fichier {path_to_modify} ?[/bold]"):
                filepath = self.working_directory / path_to_modify
                success, msg = self.file_handler.write_file(filepath, new_content)
                console.print(f"[{self.theme['success']}]✓ {msg}[/{self.theme['success']}]") if success else console.print(f"[{self.theme['error']}]✗ {msg}[/{self.theme['error']}]")
                if success:
                    self.loaded_files[path_to_modify] = new_content
                self._update_display()
            else:
                console.print(f"[{self.theme['warning']}]Modifications annulées.[/{self.theme['warning']}]")
                self._update_display()
            return True

    def handle_project_creation(self, response: str, is_correction_attempt: bool = False):
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

        processed_files = []
        for path, file_content in files:
            path = path.strip()
            file_content = file_content.strip()
            if path.endswith('/') and file_content and '\n' not in file_content:
                processed_path = path + file_content
                processed_content = ""
                processed_files.append((processed_path, processed_content))
            else:
                processed_files.append((path, file_content))

        table = Table(title="Fichiers à créer", title_style=self.theme["table_title"])
        table.add_column("Chemin", style=self.theme["table_index"])
        for path, _ in processed_files: table.add_row(path)
        self.chat_renderables.append(table)
        self._update_display()

        if Confirm.ask(f"\n[bold]Créer ces {len(processed_files)} élément(s) ?[/bold]"):
            created_paths = []
            for path, file_content in processed_files:
                filepath = self.working_directory / path

                if path.endswith('/'):
                    try:
                        filepath.mkdir(parents=True, exist_ok=True)
                        console.print(f"[{self.theme['success']}]✓ Répertoire créé : {filepath}[/{self.theme['success']}]")
                    except Exception as e:
                        console.print(f"[{self.theme['error']}]✗ Erreur création répertoire {filepath}: {e}[/{self.theme['error']}]")
                    continue

                content_to_write = file_content
                code_match = re.search(r'```(?:\w+)?\n?(.*)```', content_to_write, re.DOTALL)
                if code_match:
                    content_to_write = code_match.group(1).strip()

                if path.endswith('.py'):
                    is_valid, error_msg = self.is_valid_python(content_to_write)
                    if not is_valid:
                        if is_correction_attempt:
                            error_panel = Panel(f"La tentative d'auto-correction pour `[bold]{path}[/bold]` a encore échoué.\n[bold]Détail :[/bold] {error_msg}", title="❌ Correction Échouée", border_style=self.theme["error_panel_border"])
                            self.chat_renderables.append(error_panel)
                        elif Confirm.ask(f"\n[bold yellow]La suggestion pour `{path}` contient une erreur de syntaxe. Tenter une auto-correction ?[/bold yellow]"):
                            self._attempt_self_correction(path, content_to_write, error_msg)
                            return
                        else:
                            error_panel = Panel(f"La création du fichier `[bold]{path}[/bold]` a été rejetée.\n[bold]Détail :[/bold] {error_msg}", title="❌ Validation Échouée", border_style=self.theme["error_panel_border"])
                            self.chat_renderables.append(error_panel)
                        continue

                success, msg = self.file_handler.write_file(filepath, content_to_write)
                console.print(f"[{self.theme['success']}]✓ {msg}[/{self.theme['success']}]") if success else console.print(f"[{self.theme['error']}]✗ {msg}[/{self.theme['error']}]")
                if success:
                    created_paths.append(path)

            if created_paths:
                console.print(f"\n[bold {self.theme['success']}]Chargement automatique des fichiers créés en contexte...[/bold {self.theme['success']}]")
                for path_str in created_paths:
                    self.load_file(path_str)
            
            self._update_display()
        else:
            console.print(f"[{self.theme['warning']}]Création annulée.[/{self.theme['warning']}]")
            self._update_display()

    def handle_file_modifications(self, response: str, is_correction_attempt: bool = False):
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
        cleaned_files_content = {}
        valid_modifications_count = 0

        for path, new_content_raw in files_to_modify:
            path = path.strip()
            
            new_content = new_content_raw.strip()
            code_match = re.search(r'```(?:\w+)?\n?(.*)```', new_content, re.DOTALL)
            if code_match:
                new_content = code_match.group(1).strip()
            
            if path.endswith('.py'):
                is_valid, error_msg = self.is_valid_python(new_content)
                if not is_valid:
                    if is_correction_attempt:
                        error_panel = Panel(f"La tentative d'auto-correction pour `[bold]{path}[/bold]` a encore échoué.\n[bold]Détail :[/bold] {error_msg}", title="❌ Correction Échouée", border_style=self.theme["error_panel_border"])
                        self.chat_renderables.append(error_panel)
                    elif Confirm.ask(f"\n[bold yellow]La suggestion pour `{path}` contient une erreur de syntaxe. Tenter une auto-correction ?[/bold yellow]"):
                        self._attempt_self_correction(path, new_content, error_msg)
                        return
                    else:
                        error_panel = Panel(f"La modification pour `[bold]{path}[/bold]` a été rejetée.\n[bold]Détail :[/bold] {error_msg}", title="❌ Validation Échouée", border_style=self.theme["error_panel_border"])
                        self.chat_renderables.append(error_panel)
                    continue

            cleaned_files_content[path] = new_content
            valid_modifications_count += 1
            
            original_content = self.loaded_files.get(path)
            if original_content is None:
                success, content_from_disk = self.file_handler.read_file(self.working_directory / path)
                original_content = content_from_disk if success else ""
            
            diff = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
            diff_text = "".join(diff)
            diff_panels.append(Panel(Syntax(diff_text, "diff", theme=self.syntax_theme, line_numbers=True), title=f"Changements pour {path}"))

        if not diff_panels:
            self._update_display()
            return

        self.chat_renderables.extend(diff_panels)
        self._update_display()

        if Confirm.ask(f"\n[bold]Appliquer ces {valid_modifications_count} modification(s) valide(s) ?[/bold]"):
            for path, new_content in cleaned_files_content.items():
                filepath = self.working_directory / path
                success, msg = self.file_handler.write_file(filepath, new_content)
                console.print(f"[{self.theme['success']}]✓ {msg}[/{self.theme['success']}]") if success else console.print(f"[{self.theme['error']}]✗ {msg}[/{self.theme['error']}]")
                if success:
                    self.loaded_files[path] = new_content
            self._update_display()
        else:
            console.print(f"[{self.theme['warning']}]Modifications annulées.[/{self.theme['warning']}]")
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

                self.chat_renderables.append(Panel(user_input, title="Vous", border_style=self.theme["user_panel_border"]))
                self._update_display()

                self.conversation_history.append({"role": "user", "content": user_input})
                
                prompt = self.get_files_content_for_prompt() + user_input
                system_prompt = self.api.get_system_prompt(
                    list(self.loaded_files.keys()),
                    self.terminal_launcher,
                    self.python_command
                )
                
                full_response = ""
                from rich.live import Live
                from rich.text import Text

                # Create a Text object that will be updated in-place
                response_text = Text("")
                # Place it inside a Panel
                panel = Panel(response_text, title="Assistant", border_style=self.theme["assistant_panel_border"])

                try:
                    # Increase the refresh rate for a smoother animation
                    with Live(panel, vertical_overflow="visible", refresh_per_second=self.refresh_rate) as live:
                        for token in self.api.generate(prompt, system_prompt, self.api.last_context):
                            full_response += token
                            response_text.append(token) # Just update the Text object
                except Exception as e:
                    console.print(f"[red]Erreur durant la génération de la réponse: {e}[/red]")

                # Ajout d'un print pour stabiliser l'affichage après le Live
                console.print()

                # After the live display, process the response.
                if full_response.strip():
                    self.conversation_history.append({"role": "assistant", "content": full_response})
                    # process_response will create a new panel with Markdown for the final display
                    self.process_response(full_response)

            except (KeyboardInterrupt, EOFError):
                break
        console.print("\nAu revoir !")

def main():
    parser = argparse.ArgumentParser(description="Ollama CLI v9 - Assistant de code interactif avec création de projet.")
    parser.add_argument("--model", help="Spécifier un modèle à utiliser directement.")
    args = parser.parse_args()

    cli = OllamaCLI()
    if args.model:
        cli.api.model = args.model
    
    cli.chat_loop()

if __name__ == "__main__":
    main()
