"""HorizonCode entry point — wires config, provider, TUI, and history together."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from horizoncode.config import load_config, get_active_profile, USER_CONFIG_DIR
from horizoncode.providers.base import get_provider
from horizoncode.history import HistoryManager
from horizoncode.tui.app import HorizonTUI

# ── logging ────────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    """Configure file-based logging so errors don't pollute the TUI."""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = USER_CONFIG_DIR / "horizoncode.log"

    level = logging.DEBUG if verbose else logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="horizoncode",
        description="HorizonCode — A terminal AI coding assistant",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=None,
        help="Path to a project-level config file (default: ./horizoncode.yaml)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging to ~/.horizoncode/horizoncode.log",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="horizoncode 0.1.0",
    )
    return parser.parse_args()


# ── main ───────────────────────────────────────────────────────────────────


async def _run_app(config_path: Path | None, verbose: bool) -> None:
    """Core async flow: load config → init provider → run TUI → save history."""
    _setup_logging(verbose)
    logger = logging.getLogger(__name__)

    # 1. Load configuration
    try:
        config = load_config(config_path)
        profile = get_active_profile(config)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print(
            "Create ~/.horizoncode/config.yaml with your provider settings.",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info(
        "Loaded profile '%s' (protocol=%s, model=%s)",
        profile.get("name", "?"),
        profile["protocol"],
        profile["model"],
    )

    # 2. Create provider
    try:
        provider = get_provider(
            protocol=profile["protocol"],
            api_key=profile["api_key"],
            base_url=profile["base_url"],
        )
    except ValueError as exc:
        print(f"Provider error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 3. Initialize history
    history = HistoryManager()

    # 4. Run the TUI
    tui = HorizonTUI(
        provider=provider,
        history_manager=history,
        model=profile["model"],
    )

    try:
        await tui.run()
    except Exception:
        logger.exception("TUI crashed")
        print("\nHorizonCode encountered an unexpected error.", file=sys.stderr)
        print(
            f"Check logs at: {USER_CONFIG_DIR / 'horizoncode.log'}",
            file=sys.stderr,
        )
    finally:
        # 5. Save history
        saved_path = history.save()
        if saved_path:
            logger.info("Session saved to %s", saved_path)

        # 6. Close provider (releases HTTP connections)
        if hasattr(provider, "close"):
            await provider.close()


def main() -> None:
    """CLI entry point for HorizonCode."""
    args = _parse_args()

    try:
        asyncio.run(_run_app(args.config, args.verbose))
    except KeyboardInterrupt:
        # User forced exit (repeated Ctrl+C)
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
