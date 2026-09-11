"""HorizonCode 入口 —— 串联配置、Provider、TUI 和会话历史。

启动流程: 解析命令行参数 → 加载配置 → 创建 Provider → 初始化 TUI → 运行主循环。
退出时自动保存会话历史并释放 HTTP 连接资源。
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from horizoncode.config import load_config, get_active_profile, USER_CONFIG_DIR
from horizoncode.providers.base import get_provider
from horizoncode.history import HistoryManager
from horizoncode.tui.app import HorizonTUI
from horizoncode.tools.registry import create_default_registry

# ── 日志 ────────────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    """配置文件日志，避免错误信息污染 TUI 界面。"""
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


# ── CLI 参数 ──────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="horizoncode",
        description="HorizonCode —— 终端 AI 编程助手",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="项目级配置文件的路径（默认: ./horizoncode.yaml）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="启用调试日志，输出到 ~/.horizoncode/horizoncode.log",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="horizoncode 0.1.0",
    )
    return parser.parse_args()


# ── 主流程 ────────────────────────────────────────────────────────────────


async def _run_app(config_path: Path | None, verbose: bool) -> None:
    """核心异步流程: 加载配置 → 初始化 Provider → 运行 TUI → 保存历史。"""
    _setup_logging(verbose)
    logger = logging.getLogger(__name__)

    # 1. 加载配置
    try:
        config = load_config(config_path)
        profile = get_active_profile(config)
    except (ValueError, FileNotFoundError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        print(
            "请在 ~/.horizoncode/config.yaml 中创建配置文件。",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info(
        "已加载 profile '%s'（protocol=%s, model=%s）",
        profile.get("name", "?"),
        profile["protocol"],
        profile["model"],
    )

    # 2. 创建 Provider
    try:
        provider = get_provider(
            protocol=profile["protocol"],
            api_key=profile["api_key"],
            base_url=profile["base_url"],
        )
    except ValueError as exc:
        print(f"Provider 错误: {exc}", file=sys.stderr)
        sys.exit(1)

    # 3. 初始化历史管理器
    history = HistoryManager()
    tools = create_default_registry(Path.cwd())

    # 4. 运行 TUI
    tui = HorizonTUI(
        provider=provider,
        history_manager=history,
        model=profile["model"],
        tools=tools,
    )

    try:
        await tui.run()
    except Exception:
        logger.exception("TUI 崩溃")
        print("\nHorizonCode 遇到未预期错误。", file=sys.stderr)
        print(
            f"查看日志: {USER_CONFIG_DIR / 'horizoncode.log'}",
            file=sys.stderr,
        )
    finally:
        # 5. 保存历史
        saved_path = history.save()
        if saved_path:
            logger.info("会话已保存至 %s", saved_path)

        # 6. 关闭 Provider（释放 HTTP 连接）
        if hasattr(provider, "close"):
            await provider.close()


def main() -> None:
    """HorizonCode CLI 入口。"""
    args = _parse_args()

    try:
        asyncio.run(_run_app(args.config, args.verbose))
    except KeyboardInterrupt:
        # 用户强制退出（多次 Ctrl+C）
        print("\n已中断。", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
