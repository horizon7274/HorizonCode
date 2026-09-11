"""项目内文件路径的规范化与边界校验。"""

from __future__ import annotations

from pathlib import Path


class PathOutsideWorkspaceError(ValueError):
    """请求路径不位于项目根目录时抛出。"""


class WorkspacePathGuard:
    """确保所有工具文件访问都限制在固定项目根目录内。"""

    def __init__(self, root: Path) -> None:
        """固定并规范化项目根目录。"""
        self.root = root.resolve()

    def resolve_file(self, raw_path: object) -> Path:
        """解析相对路径，并拒绝绝对路径、NUL 字符和目录逃逸。"""
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("path 必须是非空字符串")
        if "\x00" in raw_path:
            raise ValueError("path 不能包含 NUL 字符")
        requested = Path(raw_path)
        if requested.is_absolute():
            raise PathOutsideWorkspaceError("只允许相对项目根目录的路径")
        candidate = (self.root / requested).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PathOutsideWorkspaceError("路径超出当前项目根目录") from exc
        return candidate

    def relative(self, path: Path) -> str:
        """将已验证路径转换为 POSIX 风格的项目相对路径。"""
        return path.resolve().relative_to(self.root).as_posix()

    def contains(self, path: Path) -> bool:
        """判断路径解析后是否仍位于项目根目录内。"""
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def validate_glob(self, pattern: object) -> str:
        """验证 glob 模式不含绝对路径或父目录逃逸。"""
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("pattern 必须是非空字符串")
        if "\x00" in pattern:
            raise ValueError("pattern 不能包含 NUL 字符")
        normalized = pattern.replace("\\", "/")
        if Path(normalized).is_absolute() or any(part == ".." for part in normalized.split("/")):
            raise PathOutsideWorkspaceError("glob 模式只能匹配项目根目录内的路径")
        return normalized
