"""muninn CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import DEFAULT_CONFIG, load_config

app = typer.Typer(
    name="muninn",
    help="Local self-maintaining LLM wiki.",
    no_args_is_help=True,
    add_completion=False,
)
sources_app = typer.Typer(help="Manage source connectors.")
app.add_typer(sources_app, name="sources")
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"muninn {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    import logging

    try:
        cfg = load_config()
        level = getattr(logging, cfg.settings.log_level.upper(), logging.WARNING)
    except FileNotFoundError:
        level = logging.WARNING
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    handler.setLevel(level)
    muninn_logger = logging.getLogger("muninn")
    muninn_logger.addHandler(handler)
    muninn_logger.setLevel(level)
    muninn_logger.propagate = False


@app.command()
def init(
    no_launchd: bool = typer.Option(False, "--no-launchd", help="Skip installing LaunchAgents."),
) -> None:
    """One-time setup: copy example config + .env, install LaunchAgents."""
    from .init_op import run_init

    run_init(install_launchd=not no_launchd, console=console)


@app.command(name="setup-obsidian")
def setup_obsidian(
    vault: str = typer.Argument(None, help="Vault path. Defaults to configured vault, or Muninn-Demo/."),
) -> None:
    """Copy .obsidian-template/ into a vault so it opens correctly in Obsidian."""
    from .init_op import DEMO_VAULT, _setup_obsidian

    if vault:
        target = Path(vault).expanduser().resolve()
        if not target.exists():
            # Try as configured vault path from wiki.yaml
            try:
                target = load_config().settings.vault_path
            except FileNotFoundError:
                pass
    else:
        try:
            target = load_config().settings.vault_path
        except FileNotFoundError:
            target = DEMO_VAULT

    if not target.exists():
        console.print(f"[red]{target} not found[/red]")
        raise typer.Exit(1)

    # Bootstrap wiki.yaml and .env if missing
    import shutil

    from .init_op import EXAMPLE_ENV, PROJECT_ROOT

    wiki_yaml = PROJECT_ROOT / "wiki.yaml"
    if not wiki_yaml.exists():
        example = PROJECT_ROOT / "wiki.example.yaml"
        if example.exists():
            text = example.read_text().replace("./Muninn-Vault", f"./{target.name}")
            wiki_yaml.write_text(text)
            console.print(f"  [green]created wiki.yaml → {target.name}[/green]")
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists() and EXAMPLE_ENV.exists():
        shutil.copy(EXAMPLE_ENV, env_file)
        console.print("  [green]created .env[/green]")

    _setup_obsidian(target, console)
    console.print(f"\n[green]Done.[/green] Open [cyan]{target}[/cyan] in Obsidian.")
    console.print("  If prompted, turn off Restricted mode to enable Copilot.")
    console.print("  Run [cyan]muninn serve[/cyan] and start chatting.")


@app.command()
def ingest(
    source: str | None = typer.Option(None, "--source", help="Run only this source by name."),
    all_sources: bool = typer.Option(False, "--all", help="Run all enabled sources."),
    limit: int | None = typer.Option(None, "--limit", help="Cap items per source."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch raw but don't run LLM."),
    reprocess: str | None = typer.Option(None, "--reprocess", help="Re-run a raw item by ID."),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Run the ingest pipeline."""
    import logging

    from .ops.ingest import run_ingest

    if verbose:
        logging.getLogger("muninn").setLevel(logging.INFO)

    if not source and not all_sources and not reprocess:
        console.print("[yellow]Specify --all, --source NAME, or --reprocess ID[/yellow]")
        raise typer.Exit(1)
    run_ingest(
        load_config(),
        source_name=source,
        all_sources=all_sources,
        limit=limit,
        dry_run=dry_run,
        reprocess=reprocess,
        verbose=verbose,
        console=console,
    )


@app.command(name="ingest-url")
def ingest_url(
    url: str = typer.Argument(..., help="URL to fetch and ingest."),
    file: Path | None = typer.Option(None, "--file", help="File with one URL per line (batch mode)."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Ingest a single URL (or many via --file) into the wiki."""
    from .ops.ingest import run_ingest_url

    urls = [url]
    if file:
        urls = [line.strip() for line in file.read_text().splitlines() if line.strip()]
    run_ingest_url(load_config(), urls=urls, dry_run=dry_run, verbose=verbose, console=console)


@app.command()
def lint(
    consolidate: bool = typer.Option(False, "--consolidate", help="Interactive auto-fix."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Run wiki health checks."""
    from .ops.lint import run_lint

    code = run_lint(load_config(), consolidate=consolidate, json_out=json_out, console=console)
    raise typer.Exit(code)


@app.command()
def consolidate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be consolidated without writing."),
    page: str | None = typer.Option(None, "--page", help='Consolidate one page, e.g. "NVDA" or "LLM Wiki"'),
) -> None:
    """Nightly brain consolidation: replay, strengthen, abstract."""
    from .ops.consolidate import run_consolidate

    run_consolidate(
        load_config(),
        dry_run=dry_run,
        page_title=page,
        console=console,
    )


@app.command()
def maintain(
    dry_run: bool = typer.Option(False, "--dry-run"),
    cross_link_only: bool = typer.Option(False, "--cross-link-only"),
) -> None:
    """Weekly: decay strengths, cross-link, rebuild index + overview."""
    from .ops.maintain import run_maintain

    run_maintain(load_config(), dry_run=dry_run, cross_link_only=cross_link_only, console=console)


@app.command()
def refresh(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Fix all pages in place: frontmatter, tags, wikilinks, cross-links, vectorstore. No LLM."""
    from .ops.maintain import run_refresh

    run_refresh(load_config(), dry_run=dry_run, console=console)


@app.command()
def reindex(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Archive wiki, re-queue all items, and re-ingest. Wiki stays live."""
    from .ops.maintain import run_reindex

    run_reindex(load_config(), dry_run=dry_run, console=console)


@app.command(name="vectorstore-sync")
def vectorstore_sync() -> None:
    """Sync all wiki pages into the ChromaDB vector store."""
    from .vault import Vault
    from .vectorstore import VectorStore

    cfg = load_config()
    vault = Vault(cfg.settings.vault_path)
    vs = VectorStore(
        persist_dir=cfg.settings.state_dir / "chroma",
        ollama_host=cfg.settings.ollama_host,
    )
    pages = vault.all_pages()
    console.print(f"[bold]Syncing {len(pages)} pages to ChromaDB...[/bold]")
    upserted, deleted = vs.sync_vault(pages)
    em = vs.embed_metrics
    console.print(f"[green]Done.[/green] Upserted: {upserted}, Deleted: {deleted}, Total: {vs.count}")
    if em.total_texts:
        console.print(f"  Embed: {em}")


@app.command()
def query(
    question: str = typer.Argument(None, help="Your question."),
    stdin: bool = typer.Option(False, "--stdin", help="Read question from stdin."),
    quick: bool = typer.Option(False, "--quick"),
    deep: bool = typer.Option(False, "--deep"),
    cite_only: bool = typer.Option(False, "--cite-only"),
    json_out: bool = typer.Option(False, "--json"),
    explain: str | None = typer.Option(None, "--explain", help='Explain a page, e.g. "[[LLM Wiki]]"'),
) -> None:
    """Ask the wiki a question."""
    from .ops.query import run_query

    if stdin and not question:
        import sys

        question = sys.stdin.read().strip()
    if not question and not explain:
        console.print("[yellow]Provide a question or --explain[/yellow]")
        raise typer.Exit(1)
    run_query(
        load_config(),
        question=question,
        explain=explain,
        quick=quick,
        deep=deep,
        cite_only=cite_only,
        json_out=json_out,
        console=console,
    )


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Bind address. Stick to 127.0.0.1 unless you set MUNINN_API_TOKEN."
    ),
    port: int = typer.Option(19828, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev)."),
) -> None:
    """Run the local HTTP API (OpenAI-compat + native /query)."""
    from .ops.serve import run_serve

    run_serve(host=host, port=port, reload=reload, console=console)


@app.command()
def status() -> None:
    """Show pending items and per-source cursors."""
    from .manifest import Manifest

    cfg = load_config()
    mf = Manifest(cfg.settings.manifest_path)
    table = Table(title="Source status")
    table.add_column("Source")
    table.add_column("Cursor")
    table.add_column("Pending", justify="right")
    table.add_column("Processed", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Avg tok/s", justify="right")
    table.add_column("Avg time", justify="right")
    # Configured sources + built-in sources
    source_names = [src.name for src in cfg.enabled()]
    vault_path = cfg.settings.vault_path
    if (vault_path / "Journal").exists() and "vault-journal" not in source_names:
        source_names.append("vault-journal")
    if (vault_path / "Inbox").exists() and "vault-inbox" not in source_names:
        source_names.append("vault-inbox")

    for name in source_names:
        st = mf.summary(name)
        tok_s = f"{st.avg_tok_s:.0f}" if st.avg_tok_s else "—"
        duration = f"{st.avg_duration_s:.1f}s" if st.avg_duration_s else "—"
        table.add_row(
            name,
            st.cursor or "—",
            str(st.pending),
            str(st.processed),
            str(st.failed),
            tok_s,
            duration,
        )
    console.print(table)

    # Vector store stats
    try:
        from .vectorstore import VectorStore

        vs = VectorStore(
            persist_dir=cfg.settings.state_dir / "chroma",
            ollama_host=cfg.settings.ollama_host,
        )
        console.print(f"  Vector store: [cyan]{vs.count}[/cyan] documents in ChromaDB")
    except Exception:  # noqa: BLE001
        console.print("  Vector store: [yellow]not initialized[/yellow] (run muninn vectorstore-sync)")

    # Query stats
    qs = mf.query_stats()
    if qs["total_queries"]:
        console.print(
            f"  Queries: [cyan]{qs['total_queries']}[/cyan] total, avg [cyan]{qs['avg_duration_s']}s[/cyan]"
        )

    console.print(
        f"  Models — ingest: [cyan]{cfg.settings.model_for_ingest}[/cyan]  query: [cyan]{cfg.settings.model_for_query}[/cyan]  embed: [cyan]mxbai-embed-large[/cyan]  retrieval: [cyan]{cfg.settings.retrieval_mode}[/cyan]"
    )


@sources_app.command("list")
def sources_list() -> None:
    """List configured sources."""
    cfg = load_config()
    table = Table(title="Configured sources")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Enabled")
    for s in cfg.sources:
        table.add_row(s.name, s.type, "✓" if s.enabled else "✗")
    console.print(table)


@sources_app.command("enable")
def sources_enable(name: str) -> None:
    """Mark a source enabled in wiki.yaml."""
    _toggle_source(name, enabled=True)


@sources_app.command("disable")
def sources_disable(name: str) -> None:
    """Mark a source disabled in wiki.yaml."""
    _toggle_source(name, enabled=False)


@sources_app.command("reset")
def sources_reset(
    name: str,
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation."),
) -> None:
    """Reset a source's cursor and clear its processed history."""
    if not yes:
        confirm = typer.confirm(f"Reset cursor for {name!r}? This will re-ingest from scratch.")
        if not confirm:
            raise typer.Exit(1)
    cfg = load_config()
    from .manifest import Manifest

    mf = Manifest(cfg.settings.manifest_path)
    mf.reset_source(name)
    console.print(f"[green]Reset cursor for {name!r}[/green]")


def _toggle_source(name: str, enabled: bool) -> None:
    import yaml

    raw = yaml.safe_load(DEFAULT_CONFIG.read_text())
    found = False
    for entry in raw["sources"]:
        if entry["name"] == name:
            entry["enabled"] = enabled
            found = True
            break
    if not found:
        console.print(f"[red]No source named {name!r}[/red]")
        raise typer.Exit(1)
    DEFAULT_CONFIG.write_text(yaml.safe_dump(raw, sort_keys=False))
    console.print(f"[green]{'Enabled' if enabled else 'Disabled'} {name!r}[/green]")


if __name__ == "__main__":
    app()
