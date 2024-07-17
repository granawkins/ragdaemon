import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Set

from ragdaemon.get_paths import get_paths_for_directory


class LocalIO:
    def __init__(self, cwd: Path):
        self.cwd = cwd

    @contextmanager
    def open(self, path: Path, mode: str = "r"):
        with open(self.cwd / path, mode) as file:
            yield file

    def get_paths_for_directory(
        self, path: Optional[Path] = None, exclude_patterns: Set[Path] = set()
    ):
        path = self.cwd if path is None else self.cwd / path
        return get_paths_for_directory(path, exclude_patterns=exclude_patterns)

    def is_git_repo(self, path: Optional[Path] = None):
        args = ["git", "ls-files", "--error-unmatch"]
        if path:
            args.append(path.as_posix())
        try:
            output = subprocess.run(args, cwd=self.cwd)
            return output.returncode == 0
        except subprocess.CalledProcessError:
            return False

    def last_modified(self, path: Path) -> float:
        return (self.cwd / path).stat().st_mtime

    def get_git_diff(self, diff_args: str) -> str:
        args = ["git", "diff", "-U1"]
        if diff_args and diff_args != "DEFAULT":
            args += diff_args.split(" ")
        diff = subprocess.check_output(args, cwd=self.cwd, text=True)
        return diff
