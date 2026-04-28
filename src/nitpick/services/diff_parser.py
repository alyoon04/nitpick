"""Parse unified diffs into structured file chunks for review."""

from dataclasses import dataclass, field
import re

# Files to skip reviewing
SKIP_PATTERNS = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Pipfile.lock",
    "poetry.lock", "Cargo.lock", "go.sum", "Gemfile.lock",
}
SKIP_EXTENSIONS = {".min.js", ".min.css", ".map", ".lock", ".sum"}
SKIP_DIRS = {"node_modules/", "vendor/", ".git/", "__pycache__/", "dist/", "build/"}

MAX_FILE_LINES = 500


@dataclass
class DiffHunk:
    """A single hunk within a file diff."""
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)
    # Position in the diff (1-indexed from first @@ line), used by GitHub Reviews API
    start_position: int = 0


@dataclass
class FileDiff:
    """Parsed diff for a single file."""
    path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0

    @property
    def total_changed(self) -> int:
        return self.added_lines + self.removed_lines

    def content_for_review(self) -> str:
        """Return the diff content formatted for LLM review."""
        parts = [f"--- {self.path}"]
        for hunk in self.hunks:
            parts.append(hunk.header)
            parts.extend(hunk.lines)
        return "\n".join(parts)


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff string into a list of FileDiff objects."""
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: DiffHunk | None = None
    position = 0  # GitHub diff position counter

    for line in diff_text.split("\n"):
        # New file
        if line.startswith("diff --git"):
            position = 0
            current_hunk = None
            # Extract path from "diff --git a/path b/path"
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                path = parts[1]
                if _should_skip(path):
                    current_file = None
                    continue
                current_file = FileDiff(path=path)
                files.append(current_file)
            continue

        if current_file is None:
            continue

        # Skip --- and +++ header lines
        if line.startswith("---") or line.startswith("+++"):
            continue

        # New hunk
        match = _HUNK_RE.match(line)
        if match:
            position += 1
            current_hunk = DiffHunk(
                header=line,
                old_start=int(match.group(1)),
                old_count=int(match.group(2) or 1),
                new_start=int(match.group(3)),
                new_count=int(match.group(4) or 1),
                start_position=position,
            )
            current_file.hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        # Diff content lines
        position += 1
        current_hunk.lines.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            current_file.added_lines += 1
        elif line.startswith("-") and not line.startswith("---"):
            current_file.removed_lines += 1

    # Filter out files that are too large
    return [f for f in files if f.total_changed <= MAX_FILE_LINES]


def _should_skip(path: str) -> bool:
    """Check if a file should be skipped based on path patterns."""
    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    if basename in SKIP_PATTERNS:
        return True
    if any(path.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    if any(d in path for d in SKIP_DIRS):
        return True
    return False
