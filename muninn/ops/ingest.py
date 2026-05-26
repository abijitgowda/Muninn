"""`muninn ingest` — call the pipeline for one or all sources."""

from __future__ import annotations

from rich.console import Console

from ..config import Config
from ..pipeline import Pipeline
from ..sources.url import UrlSource


def run_ingest(
    config: Config,
    *,
    source_name: str | None,
    all_sources: bool,
    limit: int | None,
    dry_run: bool,
    reprocess: str | None,
    verbose: bool,
    console: Console,
) -> None:
    pipeline = Pipeline(config, console=console)

    if reprocess:
        _reprocess(pipeline, reprocess, console)
        return

    targets = []
    if all_sources:
        targets = config.enabled()
        # Always include inbox if the folder exists
        inbox_path = config.settings.vault_path / "Inbox"
        if inbox_path.exists() and not any(s.name == "vault-inbox" for s in targets):
            from ..config import SourceConfig

            targets.append(
                SourceConfig(
                    name="vault-inbox",
                    type="inbox",
                    enabled=True,
                    url=str(inbox_path),
                    tool="muninn.sources.inbox:InboxSource",
                    options={},
                )
            )
        # Always include Journal/ if the folder exists (files stay in place)
        notes_path = config.settings.vault_path / "Journal"
        if notes_path.exists() and not any(s.name == "vault-notes" for s in targets):
            from ..config import SourceConfig

            targets.append(
                SourceConfig(
                    name="vault-journal",
                    type="folder",
                    enabled=True,
                    url=str(notes_path),
                    tool="muninn.sources.folder:FolderSource",
                    options={
                        "recursive": True,
                        "max_depth": 10,
                        "include_extensions": [".md", ".txt", ".pdf"],
                        "subfolder_as_tag": True,
                    },
                )
            )
    elif source_name:
        targets = [config.source(source_name)]
    if not targets:
        console.print("[yellow]No enabled sources to run.[/yellow]")
        return

    console.print(f"[bold]Running ingest for {len(targets)} source(s)[/bold]")
    if not dry_run and not pipeline.ollama.ping():
        console.print(
            f"[red]Ollama not reachable at {config.settings.ollama_host}. "
            "Start it with `ollama serve` (or `brew services start ollama`).[/red]"
        )
        return

    total_touched = 0
    for src in targets:
        results = pipeline.run_source(src, limit=limit, dry_run=dry_run)
        total_touched += sum(len(r.pages_touched) for r in results)

    # Post-ingest: cross-link touched pages (not all pages — scales linearly with batch size)
    if total_touched > 0 and not dry_run:
        from .maintain import _cross_link

        vault = pipeline.vault
        # Only cross-link pages that were touched, plus their link targets
        all_p = vault.all_pages()
        console.print(f"\n  cross-linking across {len(all_p)} pages...")
        inserted = _cross_link(all_p, vault, dry_run=False, console=console)
        console.print(f"  cross-link insertions: {inserted}")

    console.print(f"\n[bold green]Done.[/bold green] Pages touched (cumulative): {total_touched}")


def _reprocess(pipeline: Pipeline, item_id: str, console: Console) -> None:
    # Find the item across all sources
    for src in pipeline.config.sources:
        item = pipeline.manifest.get_item(src.name, item_id)
        if not item:
            continue
        console.print(f"Reprocessing {item_id} from {src.name}")
        try:
            touched = pipeline.ingest_one(src, item.path, item.item_id)
            pipeline.manifest.mark_processed(src.name, item.item_id, touched)
            console.print(f"[green]✓ touched {len(touched)} pages[/green]")
        except Exception as e:  # noqa: BLE001
            pipeline.manifest.mark_failed(src.name, item.item_id, str(e))
            console.print(f"[red]✗ {e}[/red]")
        return
    console.print(f"[red]No such item id: {item_id}[/red]")


def run_ingest_url(
    config: Config,
    urls: list[str],
    *,
    dry_run: bool,
    verbose: bool,
    console: Console,
) -> None:
    """Ingest one or more URLs via the UrlSource."""
    pipeline = Pipeline(config, console=console)
    if not dry_run and not pipeline.ollama.ping():
        console.print(f"[red]Ollama not reachable at {config.settings.ollama_host}[/red]")
        return

    source = UrlSource(name="url", url="", secret=None, options={})

    for u in urls:
        console.print(f"\n[cyan]→ {u}[/cyan]")
        try:
            item = source.ingest_one(u)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]fetch failed: {e}[/red]")
            continue
        if not item:
            console.print("[yellow]no content extracted[/yellow]")
            continue
        md = source.to_markdown(item)
        raw_path = pipeline.vault.write_raw("url", item.id, md, date=item.timestamp)
        rel = raw_path.relative_to(pipeline.vault.root)
        pipeline.manifest.add_item("url", item.id, str(rel))
        if dry_run:
            console.print(f"[yellow]dry-run: wrote raw at {rel}, skipping LLM[/yellow]")
            continue
        # Synthesize a SourceConfig-like wrapper for the URL run
        from ..config import SourceConfig

        src_cfg = SourceConfig(
            name="url",
            type="url",
            url="",
            tool="muninn.sources.url:UrlSource",
            options={},
        )
        try:
            touched = pipeline.ingest_one(src_cfg, str(rel), item.id)
            pipeline.manifest.mark_processed("url", item.id, touched)
            console.print(f"[green]✓ touched {len(touched)} pages[/green]")
        except Exception as e:  # noqa: BLE001
            pipeline.manifest.mark_failed("url", item.id, str(e))
            console.print(f"[red]✗ {e}[/red]")
