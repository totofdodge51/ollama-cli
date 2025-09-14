# Ollama-CLI

Ollama-CLI est un assistant de code interactif en ligne de commande, puissant et personnalisable, qui s'exécute localement avec les modèles d'Ollama. Il est conçu pour aider les développeurs à écrire, modifier et gérer leur code directement depuis le terminal.

## Fonctionnalités Clés

- **Interface Riche** : Une expérience utilisateur moderne dans le terminal grâce à la bibliothèque Rich.
- **Gestion de Fichiers** : Chargez un ou plusieurs fichiers en contexte, créez de nouvelles arborescences de projet et modifiez le code existant avec l'aide de l'IA.
- **Recherche Web Intelligente** : L'assistant peut effectuer des recherches sur le web pour obtenir des informations à jour et analyser le contenu des pages pour fournir des synthèses pertinentes.
- **Validation et Auto-Correction** : Le script valide la syntaxe du code Python généré et peut demander à l'IA de corriger ses propres erreurs.
- **Gestion de Projets** : Sauvegardez et chargez des sessions de travail complètes, incluant les fichiers en contexte et l'historique de la conversation.
- **Personnalisation** : Changez le modèle de langue, le thème de l'interface et configurez le comportement de l'outil.
- **Exécution de Commandes** : Lancez des commandes shell directement depuis l'interface.

## Démarrage Rapide

### Prérequis

- Python 3.7+
- Le serveur Ollama doit être installé et en cours d'exécution. ([Voir le site d'Ollama](https://ollama.com/))
- Au moins un modèle installé (ex: `ollama pull llama3`)

> **Note sur le Modèle :** Le script a été principalement développé et testé avec `Qwen2.5-coder:14B`. Il est fortement recommandé d'utiliser ce modèle pour obtenir les meilleurs résultats, notamment pour la génération de code et l'utilisation des outils.

### Installation

1.  Clonez ce dépôt.
2.  Il est recommandé de créer un environnement virtuel :
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
3.  Installez les dépendances :
    ```bash
    pip install -r requirements.txt
    ```
    *(Note: Un fichier `requirements.txt` devra être généré à partir des imports du script)*

### Lancement

Exécutez le script principal :

```bash
./ollama-cli-v12.py
```

## Commandes

Une fois dans l'application, tapez `/help` pour voir la liste complète des commandes disponibles, incluant :

- `/model` : Changer de modèle LLM.
- `/load <fichier>` : Charger un fichier en contexte.
- `/run <commande>` : Exécuter une commande shell.
- `/project [save|load|list]` : Gérer vos projets.
- `/web <recherche>` : Lancer une recherche web manuelle.
- `/theme` : Changer le thème de l'interface.
- `/config` : Configurer l'application.
