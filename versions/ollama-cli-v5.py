#!/usr/bin/env python3
"""
Ollama CLI v5 - Assistant LLM interactif amélioré avec header persistant et conversation déroulante.
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from typing import List, Optional, Tuple, Iterator, Dict
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import ANSI
import subprocess
import difflib
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
from urllib.parse import quote
import time

# Importations de la bibliothèque Rich pour une interface utilisateur riche
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.layout import Layout

# Initialisation de la console Rich pour un affichage esthétique
console = Console()

# Fichiers pour l'historique et le contexte
HISTORY_FILE = Path.home() / ".ollama_cli_history"
CONTEXT_FILE = Path.home() / ".ollama_cli_context.json"
CONFIG_FILE = Path.home() / ".ollama_cli_config.json"

ASCII_LOGO = r"""
 ██████╗ ██╗     ██╗     ██████╗ ███╗   ███╗ █████╗      ██████╗██╗     ██╗██╗
██╔═══██╗██║     ██║    ██╔══██╗████╗ ████║██╔══██╗    ██╔════╝██║     ██║██║
██║   ██║██║     ██║    ███████║██╔████╔██║███████║    ██║     ██║     ██║██║
██║   ██║██║     ██║    ██╔══██║██║╚██╔╝██║██╔══██║    ██║     ██║     ██║╚═╝
╚██████╔╝███████╗███████╗██║  ██║██║ ╚═╝ ██║██║  ██║    ╚██████╗███████╗██║██╗
 ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝     ╚═════╝╚══════╝╚═╝╚═╝
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
                params = {
                    'q': query,
                    'format': 'json',
                    'categories': 'general'
                }

                response = requests.get(
                    f"{instance}/search",
                    params=params,
                    timeout=10
                )

                if response.status_code == 200:
                    data = response.json()
                    results = []

                    for item in data.get('results', [])[:num_results]:
                        results.append({
                            'title': item.get('title', ''),
                            'url': item.get('url', ''),
                            'snippet': item.get('content', '')
                        })

                    return results

            except Exception:
                # Silently try the next instance
                continue

        return []

    def search_duckduckgo(self, query: str, num_results: int = 5) -> List[Dict]:
        """Recherche via DuckDuckGo (scraping simple)"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            }

            params = {
                'q': query,
                'kl': 'fr-fr'
            }

            response = requests.get(self.duckduckgo_base, params=params, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            results = []
            for result in soup.find_all('div', class_='result')[:num_results]:
                title_elem = result.find('a', class_='result__a')
                snippet_elem = result.find('a', class_='result__snippet')

                if title_elem:
                    results.append({
                        'title': title_elem.get_text().strip(),
                        'url': title_elem.get('href', ''),
                        'snippet': snippet_elem.get_text().strip() if snippet_elem else ''
                    })

            return results

        except Exception as e:
            console.print(f"[red]Erreur DuckDuckGo: {e}[/red]")
            return []

class OllamaAPI:
    """Wrapper pour les appels à l'API d'Ollama avec recherche web"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.model = "llama3"  # Modèle par défaut
        self.last_context = []
        self.web_enabled = True
        self.web_searcher = WebSearcher()

        self.system_prompt_template = """Tu es un assistant de terminal avec accès à la recherche web.

INSTRUCTIONS GÉNÉRALES:
- Si la demande est une question générale, réponds normalement.
- Si tu as besoin d'informations récentes ou spécifiques, utilise: <web_search>termes de recherche</web_search>
- Si la demande est de CRÉER un fichier (ex: "crée un script"), réponds UNIQUEMENT avec le format:
<plan>NOM_DU_FICHIER.EXT</plan>
<code>
CONTENU_DU_FICHIER
</code>
- Si la demande est de MODIFIER un fichier, réponds UNIQUEMENT avec le format:
<file_modifications>
<explanation>
EXPLIQUE ICI LES CHANGEMENTS EN QUELQUES LIGNES.
</explanation>
<file path="NOM_DU_FICHIER.EXT">
NOUVEAU_CONTENU_COMPLET
</file>
</file_modifications>
- Si la demande est d'EXÉCUTER une commande, réponds UNIQUEMENT avec le format:
<shell>COMMANDE</shell>

RECHERCHE WEB:
Utilise la recherche web pour:
- Actualités récentes
- Informations sur des événements actuels
- Cours de bourse, crypto
- Météo
- Informations techniques récentes

Fichiers en contexte: {loaded_files}
"""

    def get_system_prompt(self, loaded_files: List[str]) -> str:
        files_str = ", ".join(loaded_files) if loaded_files else "aucun"
        return self.system_prompt_template.format(loaded_files=files_str)

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
        """Recherche web via SearX puis DuckDuckGo en fallback"""
        if not self.web_enabled:
            return []

        # Essayer SearX en premier
        results = self.web_searcher.search_searx(query, num_results)

        # Si échec, essayer DuckDuckGo
        if not results:
            results = self.web_searcher.search_duckduckgo(query, num_results)

        return results

    def detect_web_search_need(self, prompt: str) -> Optional[str]:
        """Détecte si une recherche web est nécessaire."""
        # 1. Vérification directe des balises explicites
        search_match = re.search(r'<web_search>(.*?)</web_search>', prompt, re.IGNORECASE | re.DOTALL)
        if search_match:
            return search_match.group(1).strip()

        # 2. Mots-clés et patterns indiquant le besoin de recherche web
        web_keywords = [
            'actualité', 'actualités', 'nouvelles', 'news',
            'cours de', 'prix de', 'bitcoin', 'crypto',
            'météo', 'temps qu\'il fait', 'temperature',
            'aujourd\'hui', 'maintenant', 'récent',
            'événements', 'situation actuelle',
            'dernières informations', 'mise à jour'
        ]

        prompt_lower = prompt.lower()
        if any(keyword in prompt_lower for keyword in web_keywords):
            # Extraire les termes de recherche pertinents
            # Simple heuristique : prendre la phrase qui contient le mot-clé
            sentences = prompt.split('.')
            for sentence in sentences:
                if any(keyword in sentence.lower() for keyword in web_keywords):
                    return sentence.strip()

        # 3. Demander au LLM de décider (fallback)
        return self._llm_search_decision(prompt)

    def _llm_search_decision(self, prompt: str) -> Optional[str]:
        """Utilise le LLM pour décider si une recherche est nécessaire."""
        meta_prompt_template = '''Analyse la question de l'utilisateur suivante :
"""
{user_prompt}
"""
Dois-je effectuer une recherche sur le web pour y répondre ? Si oui, quels seraient les termes de recherche les plus efficaces ?
Réponds UNIQUEMENT au format JSON suivant, sans aucun autre texte avant ou après:
{{
  "search_needed": true/false,
  "search_query": "termes de recherche ici"
}}'''
        meta_prompt = meta_prompt_template.format(user_prompt=prompt)

        meta_system_prompt = "Tu es un expert en analyse de requêtes. Ton seul but est de déterminer si une recherche web est nécessaire et de fournir les termes de recherche optimaux au format JSON."

        try:
            with console.status("[dim]Analyse du besoin de recherche...[/dim]"):
                response_str = self.generate(
                    prompt=meta_prompt,
                    system_prompt=meta_system_prompt,
                    force_no_web_search=True
                )

            if not response_str:
                return None

            # Extraire le JSON de la réponse
            json_match = re.search(r'\{.*\}', response_str, re.DOTALL)
            if not json_match:
                return None

            decision = json.loads(json_match.group(0))

            if decision.get("search_needed") and decision.get("search_query"):
                return str(decision["search_query"])

        except (json.JSONDecodeError, KeyError, Exception):
            pass

        return None

    def generate(self, prompt: str, system_prompt: str, context: Optional[List] = None, force_no_web_search: bool = False) -> Iterator[Dict]:
        # Détecter si une recherche web est demandée ou nécessaire
        search_query = None
        if not force_no_web_search:
            search_query = self.detect_web_search_need(prompt)

        if search_query and self.web_enabled:
            console.print(f"[yellow]🔍 Recherche web: {search_query}[/yellow]")

            # Effectuer la recherche
            results = self.search_web(search_query)

            if results:
                # Ajouter les résultats au contexte du prompt
                web_context = f"\n\nRÉSULTATS DE RECHERCHE WEB pour '{search_query}':\n"
                for i, result in enumerate(results, 1):
                    web_context += f"{i}. {result['title']}\n"
                    web_context += f"   {result['snippet']}\n"
                    web_context += f"   Source: {result['url']}\n\n"

                # Si c'était une balise explicite, la remplacer
                search_match = re.search(r'<web_search>(.*?)</web_search>', prompt, re.IGNORECASE | re.DOTALL)
                if search_match:
                    prompt = prompt.replace(search_match.group(0), web_context)
                else:
                    # Sinon ajouter le contexte
                    prompt = web_context + "\n\nQUESTION UTILISATEUR: " + prompt
            else:
                # Si pas de résultats
                no_results_msg = f"\n[Aucun résultat de recherche trouvé pour: {search_query}]\n"
                search_match = re.search(r'<web_search>(.*?)</web_search>', prompt, re.IGNORECASE | re.DOTALL)
                if search_match:
                    prompt = prompt.replace(search_match.group(0), no_results_msg)
                else:
                    prompt = no_results_msg + "\n\nQUESTION UTILISATEUR: " + prompt

        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system_prompt,
            "stream": True,
            "context": context or []
        }

        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, stream=True, timeout=(5, 300))

            if response.status_code == 404:
                try:
                    error_data = response.json()
                    if "model '" in error_data.get("error", ""):
                        console.print(f"[red]Erreur : Le modèle '{self.model}' n'a pas été trouvé par le serveur Ollama.[/red]")
                        return
                except json.JSONDecodeError:
                    pass

            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        yield data
                        # The final context is in the last message where done is true
                        if data.get("done") and "context" in data:
                            self.last_context = data.get("context", [])
                    except json.JSONDecodeError:
                        # In case of streaming, just ignore invalid json lines
                        continue

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Erreur lors de l'appel à l'API Ollama : {e}[/red]")
        except json.JSONDecodeError:
            console.print("[red]Erreur: Réponse invalide de l'API Ollama.[/red]")


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

    @staticmethod
    def show_diff(original: str, modified: str, filepath: str):
        diff_lines = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"{filepath} (original)",
            tofile=f"{filepath} (modifié)"
        ))

        if not diff_lines or len(diff_lines) <= 2:
            panel_content = Text("Aucun changement détecté.", style="dim")
        else:
            text = Text()
            # On saute les deux premières lignes de l'en-tête du diff (--- et +++)
            for line in diff_lines[2:]:
                if line.startswith('+'):
                    text.append(line, style="green")
                elif line.startswith('-'):
                    text.append(line, style="red")
                elif line.startswith('@@'):
                    text.append(line, style="yellow")
                else:
                    text.append(line)
            panel_content = text

        console.print(Panel(panel_content, title=f"Changements proposés pour [bold cyan]{filepath}[/bold cyan]", border_style="yellow", expand=False))

class OllamaCLI:
    def __init__(self):
        self.api = OllamaAPI()
        self.file_handler = FileHandler()
        self.context = []
        self.conversation_history = []
        # MODIFICATION: S'assurer que chat_renderables est bien initialisé.
        self.chat_renderables = []
        self.working_directory = Path.cwd()
        self.loaded_files = {}
        self.terminal_launcher = "konsole -e"
        self.file_to_edit = None
        self.load_config()

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    self.terminal_launcher = config_data.get("terminal_launcher", self.terminal_launcher)
                    self.api.web_enabled = config_data.get("web_enabled", True)
            except (json.JSONDecodeError, IOError):
                console.print(f"[yellow]Avertissement : Impossible de charger le fichier de configuration. Utilisation des valeurs par défaut.[/yellow]")
        self.save_config()

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config_to_save = {
                    "terminal_launcher": self.terminal_launcher,
                    "web_enabled": self.api.web_enabled
                }
                json.dump(config_to_save, f, indent=2)
        except IOError as e:
            console.print(f"[yellow]Avertissement : Impossible de sauvegarder la configuration : {e}[/yellow]")

    def save_context(self):
        try:
            with open(CONTEXT_FILE, 'w', encoding='utf-8') as f:
                context_to_save = {
                    "context": self.api.last_context,
                    "history": self.conversation_history[-20:]
                }
                json.dump(context_to_save, f, indent=2)
        except IOError as e:
            console.print(f"[yellow]Avertissement : Impossible de sauvegarder le contexte : {e}[/yellow]")

    def _get_header_panel(self):
        """Retourne le panel d'en-tête avec les informations actuelles"""
        web_status = f"Web: [bold green]Activé[/bold green]" if self.api.web_enabled else "Web: [bold red]Désactivé[/bold red]"
        subtitle = f"[dim]Modèle: [bold yellow]{self.api.model}[/bold yellow] | {web_status} | Tapez [yellow]/help[/yellow] pour les commandes.[/dim]"
        return Panel(
            Text(ASCII_LOGO, style="bold cyan", justify="center"),
            title="Ollama CLI v5",
            subtitle=subtitle,
            border_style="cyan"
        )

    # MODIFICATION: Nouvelle fonction centrale pour mettre à jour l'affichage
    def _update_display(self, new_renderable=None):
        """Efface l'écran, affiche l'en-tête et l'historique de la conversation."""
        if new_renderable:
            self.chat_renderables.append(new_renderable)

        # Limite l'historique affiché à l'écran pour garder les choses fluides
        max_history_items = 30
        if len(self.chat_renderables) > max_history_items:
            self.chat_renderables = self.chat_renderables[-max_history_items:]

        console.clear()
        console.print(self._get_header_panel())
        for renderable in self.chat_renderables:
            console.print(renderable)

    # MODIFICATION: Toutes les anciennes fonctions d'affichage sont supprimées ou modifiées.
    # _print_with_header, _clear_and_print_header sont supprimées.
    # _print_header est conservée pour le tout premier affichage.
    def _print_header(self):
        console.clear()
        console.print(self._get_header_panel())

    def handle_command(self, command: str) -> Tuple[bool, Optional[str]]:
        parts = command.split()
        cmd = parts[0].lower()

        if cmd in ["/quit", "/exit", "/q"]:
            return False, None

        command_panel = Panel(command, title="Commande", title_align="left", border_style="yellow")

        if cmd == "/help":
            content = Group(command_panel, self._get_help_content())
            self._update_display(content)
        elif cmd == "/clear":
            self.clear_context()
            success_msg = Panel("[green]Contexte de la conversation effacé.[/green]", border_style="green")
            # Ne pas utiliser _update_display ici car clear_context s'en charge.
        elif cmd == "/model":
            self._update_display(command_panel)

            current_model = self.api.model
            if self.select_model():
                if self.api.model != current_model:
                    # Model has changed, reset context
                    self.api.last_context = []
                    self.conversation_history = []

                    success_msg = Panel(f"[green]Modèle changé en : {self.api.model}[/green]", border_style="green")
                    reset_msg = Panel("[yellow]Le contexte de la conversation a été réinitialisé pour le nouveau modèle.[/yellow]", border_style="yellow")
                    self._update_display(Group(success_msg, reset_msg))
                else:
                    # Model was not changed
                    no_change_msg = Panel(f"[dim]Le modèle est resté : {self.api.model}[/dim]", border_style="dim")
                    self._update_display(no_change_msg)
            else:
                # Selection was cancelled
                error_msg = Panel("[red]Sélection de modèle annulée ou échouée.[/red]", border_style="red")
                self._update_display(error_msg)

            return True, None
        elif cmd == "/config":
            self._update_display(command_panel)
            self.handle_config_command()
        elif cmd == "/load":
            content_items = [command_panel]
            if len(parts) > 1:
                self.load_file_with_header(Path(" ".join(parts[1:])), content_items)
            else:
                error_msg = Panel("[red]Usage: /load <filepath>[/red]", border_style="red")
                content_items.append(error_msg)
            self._update_display(Group(*content_items))
        elif cmd == "/edit":
            content_items = [command_panel]
            if len(parts) > 1:
                self.file_to_edit = self.working_directory / Path(" ".join(parts[1:]))
                edit_prompt = self.edit_file_with_header(self.file_to_edit, content_items)
                self._update_display(Group(*content_items))
                return True, edit_prompt
            else:
                error_msg = Panel("[red]Usage: /edit <filepath>[/red]", border_style="red")
                content_items.append(error_msg)
                self._update_display(Group(*content_items))
        elif cmd == "/paste":
            self._update_display(command_panel)
            return True, self.get_pasted_input()
        elif cmd == "/run":
            content_items = [command_panel]
            if len(parts) > 1:
                # On affiche la commande, puis on l'exécute
                self._update_display(Group(*content_items))
                self.run_command(" ".join(parts[1:]))
            else:
                error_msg = Panel("[red]Usage: /run <command>[/red]", border_style="red")
                content_items.append(error_msg)
                self._update_display(Group(*content_items))
        elif cmd == "/files":
            content_items = [command_panel, self._get_files_table()]
            self._update_display(Group(*content_items))
        elif cmd == "/pwd":
            pwd_msg = Panel(f"[cyan]Répertoire actuel : {self.working_directory}[/cyan]", border_style="cyan")
            self._update_display(Group(command_panel, pwd_msg))
        elif cmd == "/cd":
            content_items = [command_panel]
            if len(parts) > 1:
                self.change_directory_with_header(Path(" ".join(parts[1:])), content_items)
            else:
                error_msg = Panel("[red]Usage: /cd <directory>[/red]", border_style="red")
                content_items.append(error_msg)
            self._update_display(Group(*content_items))
        elif cmd == "/search":
            content_items = [command_panel]
            if len(parts) > 1:
                query = " ".join(parts[1:])
                search_results = self.perform_direct_search(query)
                content_items.append(search_results)
            else:
                error_msg = Panel("[red]Usage: /search <termes de recherche>[/red]", border_style="red")
                content_items.append(error_msg)
            self._update_display(Group(*content_items))
        elif cmd == "/web":
            self.api.web_enabled = not self.api.web_enabled
            status = "activée" if self.api.web_enabled else "désactivée"
            web_msg = Panel(f"[cyan]🌐 Recherche web {status}.[/cyan]", border_style="cyan")
            self.save_config()
            # On réaffiche tout pour mettre à jour l'en-tête
            self._update_display(Group(command_panel, web_msg))
        else:
            error_msg = Panel(f"[red]Commande inconnue : {cmd}. Tapez /help pour la liste.[/red]", border_style="red")
            self._update_display(Group(command_panel, error_msg))

        return True, None


    def _get_help_content(self):
        """Retourne le contenu d'aide formaté"""
        help_text = """# Aide de Ollama CLI v5 - Avec Recherche Web 🌐

## Commandes de Chat
- `/quit`, `/exit`, `/q`: Quitter l'application.
- `/clear`: Effacer l'historique de la conversation et les fichiers chargés.
- `/model`: Sélectionner un autre modèle Ollama.
- `/paste`: Activer le mode collage pour envoyer du code ou du texte multiligne.

## 🆕 Commandes Web
- `/search <termes>`: Effectuer une recherche web directe.
- `/web`: Activer/désactiver la recherche web automatique.

## Commandes de Fichiers
- `/load <fichier>`: Charger un fichier en contexte.
- `/edit <fichier>`: Lancer une session d'édition assistée par l'IA sur un fichier.
- `/files`: Lister les fichiers actuellement chargés en contexte.
- `/pwd`: Afficher le répertoire de travail actuel.
- `/cd <dossier>`: Changer de répertoire de travail.

## Commandes Système
- `/run <commande>`: Exécuter une commande shell dans un nouveau terminal.
- `/config`: Menu de configuration (terminal + recherche web).
- `/help`: Afficher ce message d'aide.

## 🌐 Utilisation de la Recherche Web
L'IA détecte automatiquement le besoin de recherche pour :
- Actualités récentes
- Cours de bourse, crypto
- Météo
- Événements actuels

**Exemples :**
- "Quelles sont les actualités en France ?"
- "Quel est le cours du Bitcoin ?"
- "Météo à Paris aujourd'hui"

**Recherche forcée :**
`<web_search>vos termes de recherche</web_search>`
"""
        return Panel(Markdown(help_text), title="🆕 Aide avec Recherche Web", border_style="green")

    def _get_files_table(self):
        """Retourne le tableau des fichiers chargés"""
        if not self.loaded_files:
            return Panel("[dim]Aucun fichier chargé.[/dim]", title="📁 Fichiers en Contexte", border_style="purple")

        table = Table(title="📁 Fichiers en Contexte", border_style="purple")
        table.add_column("Chemin", style="cyan")
        table.add_column("Taille (caractères)", style="green")
        for filepath, content in self.loaded_files.items():
            table.add_row(filepath, str(len(content)))
        return table

    def select_model(self) -> bool:
        """Sélection de modèle qui s'intègre au nouvel affichage"""
        models = self.api.list_models()
        if not models:
            return False

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Index", style="cyan")
        table.add_column("Nom du Modèle", style="green")
        for i, model in enumerate(models, 1):
            table.add_row(str(i), model)

        # Affiche le tableau dans le contexte de la conversation
        self._update_display(Panel(table, title="🤖 Modèles Disponibles", border_style="blue"))

        choices = [str(i) for i in range(1, len(models) + 1)]
        try:
            choice = Prompt.ask("Sélectionnez un modèle", choices=choices, default="1")
            idx = int(choice) - 1
            self.api.model = models[idx]
            return True
        except (KeyboardInterrupt, EOFError):
            return False

    def load_file_with_header(self, filepath: Path, content_items: list):
        """Charge un fichier et ajoute le résultat à content_items"""
        full_path = self.working_directory / filepath
        if not full_path.exists():
            error_msg = Panel(f"[red]Erreur : Le fichier {full_path} n'existe pas.[/red]", border_style="red")
            content_items.append(error_msg)
            return

        success, content = self.file_handler.read_file(full_path)
        if success:
            self.loaded_files[str(filepath)] = content
            lexer = self.guess_lexer(filepath, code=content)
            syntax = Syntax(content, lexer, theme="monokai", line_numbers=True)
            file_panel = Panel(syntax, title=f"Contenu de {filepath}", border_style="cyan")
            content_items.append(file_panel)
        else:
            error_msg = Panel(f"[red]Erreur de chargement : {content}[/red]", border_style="red")
            content_items.append(error_msg)

    def edit_file_with_header(self, filepath: Path, content_items: list) -> Optional[str]:
        """Lance l'édition d'un fichier avec en-tête"""
        if not filepath.exists():
            error_msg = Panel(f"[red]Erreur : Le fichier {filepath} n'existe pas.[/red]", border_style="red")
            content_items.append(error_msg)
            return None

        success, content = self.file_handler.read_file(filepath)
        if not success:
            error_msg = Panel(f"[red]Erreur de lecture du fichier : {content}[/red]", border_style="red")
            content_items.append(error_msg)
            return None

        lexer = self.guess_lexer(filepath, code=content)
        file_panel = Panel(
            Syntax(content, lexer, theme="monokai", line_numbers=True),
            title=f"Contenu actuel de {filepath.name}",
            border_style="cyan"
        )
        content_items.append(file_panel)

        try:
            edit_request = Prompt.ask(f"[bold]Décrivez les changements souhaités pour {filepath.name}[/bold]")
        except (KeyboardInterrupt, EOFError):
            cancel_msg = Panel("\n[yellow]Édition annulée.[/yellow]", border_style="yellow")
            content_items.append(cancel_msg)
            return None

        prompt = (
            f"En te basant sur le contenu du fichier '{filepath.name}' ci-dessous, applique la modification suivante : {edit_request}.\n\n"
            f"--- Contenu de {filepath.name} ---\n"
            f"{content}\n"
            f"--- Fin de {filepath.name} ---\n\n"
            "Réponds en utilisant le format <file_modifications>."
        )
        return prompt

    def change_directory_with_header(self, new_dir: Path, content_items: list):
        """Change de répertoire et ajoute le résultat à content_items"""
        try:
            target_path = (self.working_directory / new_dir).resolve()
            if target_path.is_dir():
                os.chdir(target_path)
                self.working_directory = target_path
                success_msg = Panel(f"[green]Nouveau répertoire : {self.working_directory}[/green]", border_style="green")
                content_items.append(success_msg)
            else:
                error_msg = Panel(f"[red]Répertoire non trouvé : {target_path}[/red]", border_style="red")
                content_items.append(error_msg)
        except Exception as e:
            error_msg = Panel(f"[red]Erreur : {e}[/red]", border_style="red")
            content_items.append(error_msg)

    def perform_direct_search(self, query: str):
        """Effectue une recherche web et retourne le résultat formaté"""
        results = self.api.search_web(query, 5)
        if results:
            table = Table(title=f"🔍 Résultats pour: {query}", border_style="blue")
            table.add_column("Titre", style="cyan", max_width=50)
            table.add_column("Extrait", style="green", max_width=60)
            table.add_column("URL", style="dim", max_width=40)

            for result in results:
                title = result['title'][:47] + "..." if len(result['title']) > 50 else result['title']
                snippet = result['snippet'][:57] + "..." if len(result['snippet']) > 60 else result['snippet']
                url = result['url'][:37] + "..." if len(result['url']) > 40 else result['url']
                table.add_row(title, snippet, url)
            return table
        else:
            return Panel("[red]Aucun résultat trouvé.[/red]", border_style="red")

    def handle_config_command(self):
        while True:
            console.print(Panel("🔧 Configuration", border_style="blue", title_align="left"))
            table = Table.grid(padding=(0, 2))
            table.add_row("1.", "Configurer le terminal")
            table.add_row("2.", "Configurer la recherche Web")
            table.add_row("q.", "Quitter la configuration")
            console.print(table)

            choice = Prompt.ask("Votre choix", choices=["1", "2", "q"], default="q")

            if choice == '1':
                console.print(f"Terminal actuel : [cyan]{self.terminal_launcher}[/cyan]")
                new_terminal = Prompt.ask("Entrez la nouvelle commande de terminal (laissez vide pour ne pas changer)")
                if new_terminal.strip():
                    self.terminal_launcher = new_terminal
                console.print("[green]Terminal mis à jour.[/green]\n")

            elif choice == '2':
                web_status = '[bold green]Activée[/bold green]' if self.api.web_enabled else '[bold red]Désactivée[/bold red]'
                console.print(f"Recherche Web : {web_status}")
                if Confirm.ask("Changer le statut ?"):
                    self.api.web_enabled = not self.api.web_enabled
                console.print("[green]Configuration Web mise à jour.[/green]\n")

            elif choice == 'q':
                break

        self.save_config()
        # On réaffiche tout pour prendre en compte les potentiels changements
        self._update_display(Panel("[green]Configuration sauvegardée.[/green]"))


    def clear_context(self):
        self.context = []
        self.conversation_history = []
        self.loaded_files = {}
        self.chat_renderables.clear()
        # MODIFICATION: Mettre à jour l'affichage après avoir vidé le contexte
        self._update_display()


    def run_command_inline(self, command: str):
        """Exécute une commande et affiche le résultat dans le chat"""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.working_directory
            )

            content_items = []
            exec_info = Panel(f"[bold green]Exécution de :[/bold green] [cyan]{command}[/cyan]", border_style="green")
            content_items.append(exec_info)

            if result.stdout:
                stdout_panel = Panel(result.stdout.strip(), title="Sortie", border_style="green")
                content_items.append(stdout_panel)
            if result.stderr:
                stderr_panel = Panel(result.stderr.strip(), title="Erreur", border_style="red")
                content_items.append(stderr_panel)
            if not result.stdout and not result.stderr:
                no_output = Panel("[dim]La commande n'a produit aucune sortie.[/dim]", border_style="dim")
                content_items.append(no_output)

            self._update_display(Group(*content_items))

        except Exception as e:
            error_content = Group(
                Panel(f"[bold green]Exécution de :[/bold green] [cyan]{command}[/cyan]", border_style="green"),
                Panel(f"[red]Erreur lors de l'exécution : {e}[/red]", border_style="red")
            )
            self._update_display(error_content)

    def run_command(self, command: str):
        console.print(f"[bold green]Lancement de la commande dans un nouveau terminal :[/bold green] [cyan]{command}[/cyan]")
        try:
            inner_command = f"bash -c \"cd '{self.working_directory}' && {command}; echo; read -p 'Appuyez sur Entrée pour fermer...'\""
            full_command = f"{self.terminal_launcher} {inner_command}"
            subprocess.Popen(full_command, shell=True)
        except Exception as e:
            console.print(f"[red]Erreur lors du lancement du nouveau terminal : {e}[/red]")

    def guess_lexer(self, filepath: Path, code: str = "") -> str:
        return Syntax.guess_lexer(str(filepath), code=code)

    def get_pasted_input(self) -> str:
        console.print("[dim]Mode collage activé. Collez votre texte. Ctrl+D (ou Ctrl+Z sur Windows) pour finir.[/dim]")
        lines = sys.stdin.read().strip()
        if lines:
            lexer = self.guess_lexer(Path("pasted.txt"), code=lines)
            pasted_panel = Panel(Syntax(lines, lexer, theme="monokai"), title="Texte collé", border_style="green")
            # MODIFICATION: Mettre à jour l'affichage avec le texte collé
            self._update_display(pasted_panel)
        return lines

    def process_response(self, response: str):
        response = response.strip()
        try:
            is_tool_call = '<shell>' in response or '<file_modifications>' in response or ('<plan>' in response and '<code>' in response)

            if is_tool_call:
                if '<shell>' in response:
                    self.handle_shell_execution(response)
                elif '<file_modifications>' in response:
                    if self.file_to_edit:
                        self.handle_file_modifications(response)
                    else:
                        warning_msg = Panel("[yellow]L'assistant a suggéré une modification de fichier, mais aucune session d'édition n'est active. Utilisez /edit <fichier>.[/yellow]", border_style="yellow")
                        self._update_display(warning_msg)
                elif '<plan>' in response and '<code>' in response:
                    self.handle_file_creation(response)
            else:
                if not response:
                    debug_msg = Panel("[dim]La réponse de l'assistant était vide.[/dim]", title="Debug", border_style="red")
                    self._update_display(debug_msg)
                else:
                    assistant_panel = Panel(Markdown(response), title="Assistant", title_align="left", border_style="cyan")
                    self._update_display(assistant_panel)
        except Exception as e:
            error_msg = Panel(f"[bold red]Erreur lors de l'analyse de la réponse de l'IA :[/bold red] {e}", border_style="red")
            self._update_display(error_msg)

    def handle_shell_execution(self, response: str):
        shell_match = re.search(r'<shell>(.*?)</shell>', response, re.DOTALL)
        if shell_match:
            command = shell_match.group(1).strip()

            SIMPLE_COMMANDS = ["ls", "cat", "pwd", "echo", "grep", "find", "rm", "mv", "cp", "mkdir", "touch", "head", "tail", "df", "du", "lsblk", "free", "uname", "w", "whoami", "id"]

            try:
                command_start = command.split()[0]
            except IndexError:
                return # Empty command

            command_proposal = Panel(f"[bold yellow]L'assistant propose d'exécuter :[/bold yellow] [cyan]{command}[/cyan]", border_style="yellow")
            self._update_display(command_proposal)

            if command_start in SIMPLE_COMMANDS:
                if Confirm.ask("\n[bold]Exécuter cette commande (en local) ?[/bold]", default=True):
                    self.run_command_inline(command)
                else:
                    self._update_display(Panel("[yellow]Exécution annulée.[/yellow]", border_style="yellow"))
            else:
                if Confirm.ask("\n[bold]Exécuter cette commande (nouveau terminal) ?[/bold]", default=True):
                    self.run_command(command)
                else:
                    self._update_display(Panel("[yellow]Exécution annulée.[/yellow]", border_style="yellow"))


    def handle_file_modifications(self, response: str):
        try:
            modifications_match = re.search(r'<file_modifications>(.*?)</file_modifications>', response, re.DOTALL)
            if not modifications_match:
                error_content = Group(
                    Panel("[red]Erreur: La réponse de l'IA ne contient pas de balise <file_modifications> valide.[/red]", border_style="red"),
                    Panel(Markdown(response), title="Assistant (Réponse Brute)", title_align="left", border_style="red")
                )
                self._update_display(error_content)
                return

            modifications_str = modifications_match.group(1)

            content_items = []

            explanation_match = re.search(r'<explanation>(.*?)</explanation>', modifications_str, re.DOTALL)
            if explanation_match:
                explanation = explanation_match.group(1).strip()
                explanation_panel = Panel(Markdown(explanation), title="Explication des changements", border_style="blue")
                content_items.append(explanation_panel)

            file_match = re.search(r'<file path=".*?">(.*?)</file>', modifications_str, re.DOTALL)
            if not file_match:
                error_msg = Panel("[red]Erreur: La réponse de l'IA ne contient pas de balise <file> valide à l'intérieur de <file_modifications>.[/red]", border_style="red")
                content_items.append(error_msg)
                self._update_display(Group(*content_items))
                return

            new_content = file_match.group(1).strip()

            fence_match = re.search(r'```(?:[a-zA-Z0-9]+)?\s*\n(.*?)\n```', new_content, re.DOTALL)
            if fence_match:
                new_content = fence_match.group(1).strip()

            target_file = self.file_to_edit
            proposal_panel = Panel(f"[bold yellow]Modifications proposées pour :[/bold yellow] [cyan]{target_file.name}[/cyan]", border_style="yellow")
            content_items.append(proposal_panel)

            success, original_content = self.file_handler.read_file(target_file)
            if not success:
                warning_panel = Panel(f"[red]Impossible de lire le fichier original {target_file}.[/red]", border_style="red")
                content_items.append(warning_panel)
                original_content = ""

            diff_lines = list(difflib.unified_diff(
                original_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"{target_file} (original)",
                tofile=f"{target_file} (modifié)"
            ))

            if not diff_lines or len(diff_lines) <= 2:
                diff_content = Text("Aucun changement détecté.", style="dim")
            else:
                text = Text()
                for line in diff_lines[2:]:
                    if line.startswith('+'): text.append(line, style="green")
                    elif line.startswith('-'): text.append(line, style="red")
                    elif line.startswith('@@'): text.append(line, style="yellow")
                    else: text.append(line)
                diff_content = text

            diff_panel = Panel(diff_content, title=f"Changements pour [bold cyan]{target_file}[/bold cyan]", border_style="yellow", expand=False)
            content_items.append(diff_panel)

            self._update_display(Group(*content_items))

            if Confirm.ask(f"\n[bold]Appliquer ces changements à {target_file.name} ?[/bold]"):
                success, msg = self.file_handler.write_file(target_file, new_content)
                if success:
                    success_msg = Panel(f"[green]✓ {msg}[/green]", border_style="green")
                    self._update_display(success_msg)
                    try:
                        relative_path = str(target_file.relative_to(self.working_directory))
                        if relative_path in self.loaded_files: self.loaded_files[relative_path] = new_content
                    except ValueError:
                        if str(target_file) in self.loaded_files: self.loaded_files[str(target_file)] = new_content
                else:
                    self._update_display(Panel(f"[red]✗ {msg}[/red]", border_style="red"))
            else:
                self._update_display(Panel("[yellow]Modifications annulées.[/yellow]", border_style="yellow"))

        except Exception as e:
            error_content = Panel(f"[bold red]Erreur inattendue :[/bold red] {e}", border_style="red")
            self._update_display(error_content)
        finally:
            self.file_to_edit = None

    def handle_file_creation(self, response: str):
        plan_match = re.search(r'<plan>(.*?)</plan>', response, re.DOTALL)
        code_match = re.search(r'<code>(.*?)</code>', response, re.DOTALL)

        if not (plan_match and code_match):
            return

        plan = plan_match.group(1).strip()
        raw_code_content = code_match.group(1)

        fence_match = re.search(r'```(?:[a-zA-Z0-9]+)?\s*\n(.*?)\n```', raw_code_content, re.DOTALL)
        if fence_match:
            code = fence_match.group(1).strip()
        else:
            code = raw_code_content.strip()

        plan_panel = Panel(Markdown(plan), title="Plan de Création Proposé", border_style="blue")
        self._update_display(plan_panel)

        if Confirm.ask("\n[bold]Procéder à la création de ce fichier ?[/bold]"):
            filename_match = re.search(
                r"(['`])(.+?)\1|\b(\w+\.(?:sh|py|js|ts|html|css|md|txt|json|yaml|yml|xml|ini|conf|cfg|log))\b",
                plan,
                re.IGNORECASE
            )

            default_filename = "nouveau_fichier.py"
            if filename_match:
                default_filename = filename_match.group(2) or filename_match.group(3)

            filename_str = Prompt.ask("Nom du fichier", default=default_filename)
            filepath = self.working_directory / filename_str

            success, msg = self.file_handler.write_file(filepath, code)
            if success:
                success_msg = Panel(f"[green]✓ {msg}[/green]", border_style="green")
                self._update_display(success_msg)
                if Confirm.ask("Charger ce nouveau fichier en contexte ?", default=True):
                    success, content = self.file_handler.read_file(filepath)
                    if success:
                        self.loaded_files[filename_str] = content
                        lexer = self.guess_lexer(Path(filename_str), code=content)
                        syntax = Syntax(content, lexer, theme="monokai", line_numbers=True)
                        file_panel = Panel(syntax, title=f"Contenu de {filename_str}", border_style="cyan")
                        self._update_display(file_panel)
            else:
                self._update_display(Panel(f"[red]✗ {msg}[/red]", border_style="red"))
        else:
            self._update_display(Panel("[yellow]Création annulée.[/yellow]", border_style="yellow"))

    def load_context(self):
        """Charge le contexte sauvegardé si disponible."""
        if CONTEXT_FILE.exists():
            try:
                with open(CONTEXT_FILE, 'r', encoding='utf-8') as f:
                    context_data = json.load(f)
                    self.api.last_context = context_data.get("context", [])
                    self.conversation_history = context_data.get("history", [])
                    console.print("[dim]Contexte précédent chargé.[/dim]")
            except (json.JSONDecodeError, IOError):
                console.print("[yellow]Impossible de charger le contexte précédent.[/yellow]")

    # MODIFICATION: Refonte de la boucle de chat pour utiliser le nouvel affichage
    def chat_loop(self):
        """Boucle principale de chat."""
        session = PromptSession(history=FileHistory(str(HISTORY_FILE)))

        # L'affichage du modèle est maintenant géré à l'intérieur de select_model
        if not self.select_model():
            return

        # Afficher l'en-tête et le message de succès de la sélection du modèle.
        self._update_display(Panel(f"[green]Modèle sélectionné : {self.api.model}[/green]", border_style="green"))

        while True:
            try:
                prompt_text = ANSI("\x1b[1;32mVous > \x1b[0m")
                user_input = session.prompt(prompt_text)

                if not user_input.strip():
                    continue

                if user_input.startswith('/'):
                    continue_loop, processed_input = self.handle_command(user_input)
                    if not continue_loop:
                        self.save_context()
                        break
                    if processed_input:
                        user_input = processed_input
                    else:
                        continue # La commande a déjà mis à jour l'affichage

                # Affiche le prompt de l'utilisateur dans le chat
                user_panel = Panel(user_input, title="Vous", title_align="left", border_style="green")
                self._update_display(user_panel)

                self.conversation_history.append(("user", user_input))

                if self.file_to_edit:
                    full_content_prompt = user_input
                else:
                    full_content_prompt = self.get_files_content_for_prompt() + user_input

                system_prompt = self.api.get_system_prompt(list(self.loaded_files.keys()))

                force_no_search = self.file_to_edit is not None
                response_generator = self.api.generate(
                    full_content_prompt,
                    system_prompt,
                    self.api.last_context,
                    force_no_web_search=force_no_search
                )

                # --- Streaming Logic ---
                layout = Layout()
                layout.split(
                    Layout(self._get_header_panel(), name="header", size=11),
                    Layout(Group(*self.chat_renderables), name="body")
                )

                # On ne met pas de renderable ici, il sera défini dans la boucle Live
                assistant_panel = Panel("", title="Assistant", title_align="left", border_style="cyan")
                self.chat_renderables.append(assistant_panel)
                # Keep history clean
                max_history_items = 30
                if len(self.chat_renderables) > max_history_items:
                    self.chat_renderables = self.chat_renderables[-max_history_items:]
                layout["body"].update(Group(*self.chat_renderables))

                full_response = ""
                is_tool_call = None

                with Live(layout, screen=True, redirect_stderr=False, transient=False, refresh_per_second=15) as live:
                    # 1. Show spinner
                    from rich.spinner import Spinner
                    spinner = Spinner("dots", text=Text(" L'assistant réfléchit...", style="yellow"))
                    assistant_panel.renderable = spinner
                    live.refresh()

                    # 2. Loop through generator
                    for chunk in response_generator:
                        token = chunk.get("response", "")
                        if not token:
                            continue

                        full_response += token

                        if is_tool_call is None:
                            # Check only once at the beginning
                            if full_response.lstrip().startswith(('<shell>', '<file_modifications>', '<plan>')):
                                is_tool_call = True
                                # It's a tool call, update the panel title and stop streaming markup
                                assistant_panel.title = "Assistant (préparation d'un outil...)"
                                assistant_panel.renderable = ""
                                live.refresh()
                            else:
                                is_tool_call = False

                        if not is_tool_call:
                            md = Markdown(full_response)
                            assistant_panel.renderable = md
                            live.refresh()

                # Live context has ended, the final screen is visible.
                if full_response:
                    self.conversation_history.append(("assistant", full_response))

                    # We need to remove the temporary panel used for streaming
                    self.chat_renderables.pop()

                    if is_tool_call:
                        # Process the tool call, which will clear the screen and redraw
                        self.process_response(full_response)
                    else:
                        # It was a normal message. Add the final, static panel.
                        final_panel = Panel(Markdown(full_response), title="Assistant", title_align="left", border_style="cyan")
                        self._update_display(final_panel)

                self.save_context()

            except (KeyboardInterrupt, EOFError):
                self.save_context()
                console.print("\nAu revoir !")
                break

    # Les méthodes "simples" restantes sont maintenant des wrappers ou non nécessaires
    def load_file(self, filepath: Path):
        """Charge un fichier et met à jour l'affichage."""
        content_items = []
        full_path = self.working_directory / filepath
        if not full_path.exists():
            content_items.append(Panel(f"[red]Fichier non trouvé: {filepath}[/red]", border_style="red"))
        else:
            success, content = self.file_handler.read_file(full_path)
            if success:
                self.loaded_files[str(filepath)] = content
                content_items.append(Panel(f"[green]Fichier chargé: {filepath}[/green]", border_style="green"))
            else:
                content_items.append(Panel(f"[red]Erreur de chargement: {content}[/red]", border_style="red"))
        self._update_display(Group(*content_items))

    def get_files_content_for_prompt(self) -> str:
        if not self.loaded_files:
            return ""
        context_str = "CONTEXTE :\n"
        for path, content in self.loaded_files.items():
            context_str += f"--- Contenu de {path} ---\n{content}\n--- Fin de {path} ---\n\n"
        return context_str


def main():
    parser = argparse.ArgumentParser(description="Ollama CLI v5 - Assistant de code interactif avec recherche web.")
    parser.add_argument("--api-url", default="http://localhost:11434", help="URL de l'API Ollama.")
    # CORRECTION: Remplacement de ".add." par ".add_"
    parser.add_argument("--model", help="Spécifier un modèle à utiliser directement.")
    parser.add_argument("--no-web", action="store_true", help="Désactiver la recherche web au démarrage.")
    parser.add_argument("files", nargs="*", help="Fichiers à charger au démarrage.")
    args = parser.parse_args()

    cli = OllamaCLI()
    cli.api.base_url = args.api_url

    if args.model:
        cli.api.model = args.model

    if args.no_web:
        cli.api.web_enabled = False

    # Affichage initial avant de charger les fichiers
    cli._print_header()

    if args.files:
        for f in args.files:
            # la méthode load_file gère maintenant son propre affichage
            cli.load_file(Path(f))

    cli.chat_loop()


if __name__ == "__main__":
    main()
