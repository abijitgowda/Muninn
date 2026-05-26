"""`muninn init` — interactive first-time setup."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_VAULT = PROJECT_ROOT / "Muninn-Demo"
EXAMPLE_ENV = PROJECT_ROOT / ".env.example"


def run_init(install_launchd: bool, console: Console) -> None:
    console.print("[bold]Muninn init[/bold]\n")

    _check_python(console)
    ollama_ok, models = _check_ollama(console)

    # ---- Vault setup ----
    vault_name = typer.prompt("Vault name", default="Muninn-Vault")
    default_vault_path = str(PROJECT_ROOT / vault_name)
    vault_path = Path(typer.prompt("Vault path", default=default_vault_path)).expanduser().resolve()

    if vault_path.exists():
        console.print(f"  vault exists: [cyan]{vault_path}[/cyan]")
    else:
        _scaffold_vault(vault_path, console)

    (vault_path / ".muninn").mkdir(exist_ok=True)

    # ---- Model selection ----
    if ollama_ok and models:
        _embed_names = {"mxbai-embed", "nomic-embed", "all-minilm", "snowflake-arctic-embed", "embed"}
        chat_models = [m for m in models if not any(e in m.lower() for e in _embed_names)]
        console.print(f"\n  Available models: {', '.join(chat_models[:10])}")
        default_model = (
            "gemma4:e4b" if "gemma4:e4b" in chat_models else (chat_models[0] if chat_models else "gemma4:e4b")
        )
        ingest_model = typer.prompt("Ingest model", default=default_model)
        query_model = typer.prompt("Query model", default=ingest_model)
    else:
        ingest_model = typer.prompt("Ingest model", default="gemma4:e4b")
        query_model = typer.prompt("Query model", default=ingest_model)

    # ---- Write .env early so _store_secret can append to it ----
    _write_env(console)

    # ---- Source configuration ----
    console.print("\n[bold]Configure sources[/bold]")
    sources = []

    if typer.confirm("Enable browser history?", default=True):
        browsers = _detect_browser_paths()
        _browser_builders = {
            "chrome": lambda p: _browser_source("chrome-history", "chrome", p),
            "edge": lambda p: _browser_source("edge-history", "edge", p),
            "firefox": lambda p: _firefox_source(p),
            "safari": lambda p: _safari_source(p),
        }
        for name, path in browsers.items():
            console.print(f"  {name.title()}: [cyan]{path}[/cyan]")
            sources.append(_browser_builders[name](path))
        if not browsers:
            console.print("  [yellow]No browser history found[/yellow]")

    if typer.confirm("Enable X (Twitter)?", default=False):
        bearer = typer.prompt("X Bearer Token", hide_input=True)
        user_id = typer.prompt("X User ID (numeric)")
        handle = typer.prompt("X Handle (without @)")
        _store_secret("X_BEARER_TOKEN", bearer, console)
        sources.append(_x_source(user_id, handle))

    if typer.confirm("Enable git project ingestion?", default=True):
        git_root = typer.prompt("Git root", default=str(Path.home() / "git"))
        sources.append(_git_source(git_root, vault_name))

    if typer.confirm("Enable personal documents?", default=True):
        docs_path = typer.prompt("Documents folder")
        sources.append(_docs_source(docs_path))

    if typer.confirm("Enable Claude Code history?", default=True):
        sources.append(_claude_source())

    if typer.confirm("Enable YouTube transcripts?", default=True):
        sources.append(_youtube_source(vault_name))

    # Inbox is built-in — vault/Inbox/ is always watched, no config needed

    # ---- Write configs ----
    _write_wiki_yaml(vault_path, ingest_model, query_model, sources, console)

    # ---- Log directory ----
    (Path.home() / "Library" / "Logs" / "Muninn").mkdir(parents=True, exist_ok=True)

    # ---- Skill symlinks ----
    agents_skills = vault_path / ".agents" / "skills"
    if agents_skills.exists():
        _ensure_skill_symlinks(agents_skills, vault_path / ".claude" / "skills", console)

    # ---- LaunchAgents ----
    if install_launchd and platform.system() == "Darwin":
        if typer.confirm("Install LaunchAgents for scheduled ingestion?", default=True):
            _install_launchd(console)

    console.print("\n[bold green]Done.[/bold green]")
    console.print(f"  Vault: [cyan]{vault_path}[/cyan]")
    if not ollama_ok:
        console.print("  Install Ollama: [cyan]brew install ollama && ollama serve[/cyan]")
        console.print(f"  Pull model: [cyan]ollama pull {ingest_model}[/cyan]")
    console.print("  First ingest: [cyan]muninn ingest --all --limit 5 -v[/cyan]")
    console.print(f"  Open [cyan]{vault_path}[/cyan] in Obsidian")


# ---- System checks ----


def _check_python(console: Console) -> None:
    import sys

    v = sys.version_info
    if v < (3, 11):
        console.print(f"  [red]Python {v.major}.{v.minor} — requires 3.11+[/red]")
        raise typer.Exit(1)
    console.print(f"  Python {v.major}.{v.minor} ✓")


def _check_ollama(console: Console) -> tuple[bool, list[str]]:
    try:
        import httpx

        r = httpx.get("http://localhost:11434/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            console.print(f"  Ollama ✓ ({len(models)} models)")
            return True, models
    except Exception:
        pass
    console.print("  [yellow]Ollama not reachable[/yellow]")
    return False, []


def _detect_browser_paths() -> dict[str, Path]:
    home = Path.home()
    found: dict[str, Path] = {}
    chrome = home / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"
    if chrome.exists():
        found["chrome"] = chrome
    edge = home / "Library" / "Application Support" / "Microsoft Edge" / "Default" / "History"
    if edge.exists():
        found["edge"] = edge
    safari = home / "Library" / "Safari" / "History.db"
    if safari.exists():
        found["safari"] = safari
    ff_profiles = home / "Library" / "Application Support" / "Firefox" / "Profiles"
    if ff_profiles.exists():
        for profile in sorted(ff_profiles.iterdir(), reverse=True):
            db = profile / "places.sqlite"
            if db.exists():
                found["firefox"] = db
                break
    return found


def _scaffold_vault(vault_path: Path, console: Console) -> None:
    if DEMO_VAULT.exists():
        shutil.copytree(DEMO_VAULT, vault_path, symlinks=False)
        console.print("  [green]created vault from demo template[/green]")
    else:
        vault_path.mkdir(parents=True)
        for sub in (
            "Wiki/Concepts",
            "Wiki/Entities/People",
            "Wiki/Entities/Orgs",
            "Wiki/Topics",
            "Wiki/Projects",
            "Wiki/Sources",
            "Raw/Sources",
            "Journal",
            "Inbox",
        ):
            (vault_path / sub).mkdir(parents=True, exist_ok=True)
        console.print("  [green]created vault skeleton[/green]")

    if not (vault_path / ".obsidian").exists():
        _setup_obsidian(vault_path, console)

    # Always copy skills from repo root (single source of truth)
    repo_skills = PROJECT_ROOT / ".agents" / "skills"
    vault_skills = vault_path / ".agents" / "skills"
    if repo_skills.exists():
        if vault_skills.exists():
            shutil.rmtree(vault_skills)
        shutil.copytree(repo_skills, vault_skills)
        console.print(f"  [green]copied {len(list(repo_skills.iterdir()))} skills[/green]")


OBSIDIAN_TEMPLATE = PROJECT_ROOT / ".obsidian-template"


def _setup_obsidian(vault_path: Path, console: Console) -> None:
    """Copy .obsidian-template/ into vault as .obsidian/ and install Copilot plugin."""
    obs = vault_path / ".obsidian"
    if not OBSIDIAN_TEMPLATE.exists():
        console.print("  [yellow].obsidian-template/ not found, skipping[/yellow]")
        return
    if obs.exists():
        shutil.rmtree(obs)
    shutil.copytree(OBSIDIAN_TEMPLATE, obs)
    _install_copilot_plugin(obs / "plugins" / "copilot", console)
    console.print("  [green]Obsidian configured — Copilot model: muninn, history: .copilot/[/green]")


_COPILOT_REPO = "logancyang/obsidian-copilot"
_COPILOT_FILES = ["main.js", "manifest.json", "styles.css"]


def _install_copilot_plugin(plugin_dir: Path, console: Console) -> None:
    """Download Copilot plugin from GitHub releases if not already present."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    if (plugin_dir / "main.js").exists():
        return

    try:
        import httpx

        console.print("  downloading Copilot plugin...")
        for fname in _COPILOT_FILES:
            url = f"https://github.com/{_COPILOT_REPO}/releases/latest/download/{fname}"
            r = httpx.get(url, follow_redirects=True, timeout=30)
            r.raise_for_status()
            (plugin_dir / fname).write_bytes(r.content)
        console.print("  [green]Copilot plugin installed[/green]")
    except Exception as e:
        console.print(f"  [yellow]Copilot download failed: {e}[/yellow]")
        console.print('  Install manually: Obsidian → Settings → Community plugins → search "Copilot"')


def _store_secret(key: str, value: str, console: Console) -> None:
    env_path = PROJECT_ROOT / ".env"
    with env_path.open("a") as f:
        f.write(f"\n{key}={value}\n")
    console.print(f"  [green]stored {key} in .env[/green]")


# ---- Config writers ----


def _write_wiki_yaml(
    vault_path: Path, ingest_model: str, query_model: str, sources: list[dict], console: Console
) -> None:
    import yaml

    config = {
        "settings": {
            "vault_path": str(vault_path),
            "ollama_host": "http://localhost:11434",
            "llm_model": ingest_model,
            "llm_model_ingest": ingest_model,
            "llm_model_query": query_model,
            "llm_timeout": 900,
            "log_level": "WARNING",
            "num_ctx_ingest": 32768,
            "num_ctx_query": 32768,
            "max_body_ingest": 48000,
            "max_body_query": 8000,
            "retrieval_mode": "adaptive",
            "max_retrieval_pages": 5,
            "two_pass_ingest": False,
            "consolidation_enabled": True,
            "decay_rate": 0.95,
            "abstraction_threshold": 5,
            "archive_threshold": 0.1,
        },
        "sources": sources,
    }
    path = PROJECT_ROOT / "wiki.yaml"
    if path.exists():
        if not typer.confirm(
            "wiki.yaml exists (possibly from setup-obsidian). Overwrite with your new config?", default=True
        ):
            console.print("  keeping existing wiki.yaml")
            return
    path.write_text(
        "# Muninn — config (generated by `muninn init`)\n\n"
        + yaml.safe_dump(config, sort_keys=False, default_flow_style=False)
    )
    console.print("  [green]wrote wiki.yaml[/green]")


def _write_env(console: Console) -> None:
    path = PROJECT_ROOT / ".env"
    if path.exists():
        if not typer.confirm(".env exists (may contain secrets). Overwrite?", default=False):
            console.print("  keeping existing .env")
            return
    if EXAMPLE_ENV.exists():
        shutil.copy(EXAMPLE_ENV, path)
        console.print("  [green]wrote .env[/green]")


# ---- Source builders ----


def _browser_source(name: str, browser: str, path: Path) -> dict:
    return {
        "name": name,
        "type": "browser_history",
        "enabled": True,
        "url": f"file://{path}",
        "tool": "muninn.sources.browser_history:BrowserHistorySource",
        "options": {
            "browser": browser,
            "min_visit_count": 1,
            "max_items": 500,
            "fetch_body": True,
            "exclude_domains": [
                "google.com",
                "github.com",
                "duckduckgo.com",
                "bing.com",
                "localhost",
                "127.0.0.1",
            ],
        },
    }


def _firefox_source(path: Path) -> dict:
    return {
        "name": "firefox-history",
        "type": "browser_history",
        "enabled": True,
        "url": f"file://{path}",
        "tool": "muninn.sources.firefox_history:FirefoxHistorySource",
        "options": {
            "min_visit_count": 1,
            "max_items": 500,
            "fetch_body": True,
            "exclude_domains": [
                "google.com",
                "github.com",
                "duckduckgo.com",
                "bing.com",
                "localhost",
                "127.0.0.1",
            ],
        },
    }


def _safari_source(path: Path) -> dict:
    return {
        "name": "safari-history",
        "type": "browser_history",
        "enabled": True,
        "url": f"file://{path}",
        "tool": "muninn.sources.safari_history:SafariHistorySource",
        "options": {
            "min_visit_count": 1,
            "max_items": 500,
            "fetch_body": True,
            "exclude_domains": [
                "google.com",
                "github.com",
                "duckduckgo.com",
                "bing.com",
                "localhost",
                "127.0.0.1",
            ],
        },
    }


def _x_source(user_id: str, handle: str) -> dict:
    return {
        "name": "x-twitter",
        "type": "x",
        "enabled": True,
        "url": f"https://api.twitter.com/2/users/{user_id}/tweets",
        "secret_env": "X_BEARER_TOKEN",
        "tool": "muninn.sources.x:XSource",
        "options": {
            "user_id": user_id,
            "handle": handle,
            "interaction_types": ["timeline", "liked", "replies"],
            "max_results": 100,
            "include_retweets": False,
        },
    }


def _git_source(root: str, vault_name: str) -> dict:
    return {
        "name": "git-projects",
        "type": "folder",
        "enabled": True,
        "url": root,
        "tool": "muninn.sources.folder:FolderSource",
        "options": {
            "recursive": True,
            "max_depth": 3,
            "include_extensions": [".md"],
            "include_filenames": ["README.md", "DESIGN.md", "CHANGELOG.md"],
            "exclude_paths": [vault_name, "node_modules", ".venv", "__pycache__"],
            "subfolder_as_tag": True,
            "page_kind": "project",
        },
    }


def _docs_source(path: str) -> dict:
    return {
        "name": "personal-docs",
        "type": "folder",
        "enabled": True,
        "url": path,
        "tool": "muninn.sources.folder:FolderSource",
        "options": {
            "recursive": True,
            "max_depth": 10,
            "include_extensions": [".pdf", ".txt", ".md", ".docx", ".doc", ".xlsx", ".csv"],
            "skip_extensions": [".png", ".jpg", ".jpeg", ".heic", ".mp4", ".mov", ".zip"],
            "subfolder_as_tag": True,
        },
    }


def _claude_source() -> dict:
    return {
        "name": "claude-history",
        "type": "claude-history",
        "enabled": True,
        "url": "~/.claude",
        "tool": "muninn.sources.claude_history:ClaudeHistorySource",
        "options": {"max_conversations": 20, "min_messages": 3},
    }


def _youtube_source(vault_name: str) -> dict:
    return {
        "name": "youtube-transcripts",
        "type": "youtube",
        "enabled": True,
        "url": f"./{vault_name}",
        "tool": "muninn.sources.youtube:YouTubeTranscriptSource",
        "options": {"max_videos": 20, "languages": ["en"], "raw_sources": ["edge-history", "chrome-history"]},
    }


# ---- Skill symlinks ----


def _ensure_skill_symlinks(agents_skills: Path, claude_skills: Path, console: Console) -> None:
    claude_skills.mkdir(parents=True, exist_ok=True)
    count = 0
    for skill_dir in agents_skills.iterdir():
        if not skill_dir.is_dir():
            continue
        link = claude_skills / skill_dir.name
        if link.exists() or link.is_symlink():
            continue
        link.symlink_to(os.path.relpath(skill_dir, claude_skills))
        count += 1
    if count:
        console.print(f"  symlinked {count} skills into .claude/skills/")


# ---- LaunchAgents ----


def _install_launchd(console: Console) -> None:
    launchd_dir = PROJECT_ROOT / "scripts" / "launchd"
    target = Path.home() / "Library" / "LaunchAgents"
    target.mkdir(parents=True, exist_ok=True)
    if not launchd_dir.exists():
        return

    subs = {
        "{{PROJECT_ROOT}}": str(PROJECT_ROOT),
        "{{UV_PATH}}": shutil.which("uv") or "uv",
        "{{LOG_DIR}}": str(Path.home() / "Library" / "Logs" / "Muninn"),
        "{{HOME}}": str(Path.home()),
    }
    for tmpl in launchd_dir.glob("*.plist.template"):
        plist = target / tmpl.name.removesuffix(".template")
        body = tmpl.read_text()
        for k, v in subs.items():
            body = body.replace(k, v)
        plist.write_text(body)
        try:
            subprocess.run(["launchctl", "unload", str(plist)], check=False, capture_output=True)
            subprocess.run(["launchctl", "load", "-w", str(plist)], check=True, capture_output=True)
            console.print(f"  [green]loaded {plist.name}[/green]")
        except Exception as e:
            console.print(f"  [yellow]{plist.name}: {e}[/yellow]")
