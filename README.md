# GitHub → Drive Sync

Synchronise automatiquement **tous vos dépôts GitHub** sous forme de fichiers Markdown structurés dans un dossier `github` sur Google Drive. Chaque fichier contient l'arborescence du repo, le contenu des fichiers de code, et les issues ouvertes (hors tag `jules`).

**État** : v0.1 — MVP fonctionnel  
**Stack** : Python 3 · Google Drive API (OAuth2 `drive.file`) · GitHub REST API · systemd

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/hjamet/github-to-drive/main/install.sh | bash
```

Le script d'installation vous guidera pour :
1. Entrer votre **GitHub Personal Access Token** (scope `repo`)
2. Configurer l'accès **Google Drive** via OAuth2 (une seule fois, dans le navigateur)

Le service démarre automatiquement et survit aux redémarrages. En cas de mise à jour, relancez simplement la commande `curl` ci-dessus — le service existant sera arrêté proprement avant la réinstallation.

### Pré-requis

- Python 3.8+
- `python3-venv` (`sudo apt install python3-venv` sur Debian/Ubuntu)
- systemd (pour le service en arrière-plan)
- Un projet Google Cloud avec l'API Drive activée ([guide](https://console.cloud.google.com))

## Description détaillée

### Cœur du système

Le script `sync.py` tourne en boucle (polling toutes les heures) et effectue les opérations suivantes :

1. **Liste tous les repos** de l'utilisateur GitHub authentifié
2. **Détecte les nouveaux commits** en comparant les SHA avec un fichier d'état local
3. **Télécharge le tarball** de chaque repo modifié
4. **Génère un fichier Markdown** contenant :
   - L'arborescence complète du repo (tree)
   - Le contenu de chaque fichier de code (extensions : `.py`, `.js`, `.ts`, `.md`, `.tex`, `.html`, `.css`, `.sh`, `.yaml`, `.json`, `.toml`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, etc.)
   - Les issues ouvertes créées par l'utilisateur, exclues si taggées `jules`
5. **Upload le fichier** dans le dossier `github` sur Google Drive (créé automatiquement)

### Flux d'authentification

- **GitHub** : Token personnel stocké dans `~/.config/github-to-drive/config.json`
- **Google Drive** : OAuth2 avec scope `drive.file` (accès limité aux fichiers créés par l'app). Le refresh token est stocké de manière permanente dans `~/.config/github-to-drive/token.json`

### Architecture des fichiers de configuration

```
~/.config/github-to-drive/
├── config.json          # GitHub token
├── credentials.json     # OAuth2 client credentials (Google Cloud)
├── token.json           # Refresh token OAuth2 (permanent)
└── state.json           # Dernier SHA vu par repo
```

## Principaux résultats

*À venir — le projet est en phase initiale.*

## Documentation Index

| Titre | Description |
|-------|-------------|
| *Aucun document pour le moment* | — |

## Plan du repo

```
github-to-drive/
├── install.sh           # Script d'installation (curl)
├── sync.py              # Script principal de synchronisation
├── requirements.txt     # Dépendances Python
├── README.md            # Ce fichier
└── .gitignore           # Fichiers ignorés
```

## Scripts d'entrée principaux

| Script | Commande | Description |
|--------|----------|-------------|
| `install.sh` | `curl -fsSL <URL> \| bash` | Installation complète + démarrage du service |
| `sync.py` | `python3 sync.py` | Boucle de synchronisation (normalement géré par systemd) |
| `sync.py --setup` | `python3 sync.py --setup` | Flow OAuth2 interactif (utilisé par install.sh) |

## Scripts exécutables secondaires & Utilitaires

| Commande | Description |
|----------|-------------|
| `systemctl --user status github-to-drive` | Statut du service |
| `systemctl --user restart github-to-drive` | Redémarrer le service |
| `journalctl --user -u github-to-drive -f` | Consulter les logs en temps réel |

## Roadmap

| Tâche | Objectif | État |
|-------|----------|------|
| *Aucune tâche planifiée* | — | — |
