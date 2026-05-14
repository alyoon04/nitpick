"""Tests for the diff parser — pure logic, no external deps."""

from nitpick.services.diff_parser import (
    FileDiff,
    DiffHunk,
    MAX_FILE_LINES,
    parse_diff,
    _should_skip,
)

# Inline test data (conftest fixtures aren't importable as modules)

SIMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index abc1234..def5678 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,8 @@ def main():
     config = load_config()
     app = create_app(config)
+    if not config.debug:
+        app.setup_logging()
     app.run()
"""

MULTI_FILE_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
index 1111111..2222222 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,4 +1,5 @@
 import hashlib
+import secrets

 def check_password(pw, hashed):
-    return hashlib.md5(pw.encode()).hexdigest() == hashed
+    return secrets.compare_digest(hashlib.sha256(pw.encode()).hexdigest(), hashed)
diff --git a/src/utils.py b/src/utils.py
index 3333333..4444444 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -5,3 +5,6 @@ def format_date(dt):
     return dt.strftime("%Y-%m-%d")
+
+def format_time(dt):
+    return dt.strftime("%H:%M:%S")
"""

LOCKFILE_DIFF = """\
diff --git a/package-lock.json b/package-lock.json
index aaa..bbb 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
-  "version": "1.0.0"
+  "version": "1.0.1"
diff --git a/src/real.py b/src/real.py
index ccc..ddd 100644
--- a/src/real.py
+++ b/src/real.py
@@ -1,2 +1,3 @@
 def hello():
+    print("hello")
     pass
"""


class TestShouldSkip:
    def test_lockfiles(self):
        assert _should_skip("package-lock.json")
        assert _should_skip("yarn.lock")
        assert _should_skip("poetry.lock")
        assert _should_skip("Cargo.lock")
        assert _should_skip("go.sum")

    def test_skip_extensions(self):
        assert _should_skip("bundle.min.js")
        assert _should_skip("styles.min.css")
        assert _should_skip("app.js.map")

    def test_skip_directories(self):
        assert _should_skip("node_modules/lodash/index.js")
        assert _should_skip("vendor/some-lib/foo.go")
        assert _should_skip(".git/config")
        assert _should_skip("__pycache__/module.cpython-311.pyc")
        assert _should_skip("dist/bundle.js")
        assert _should_skip("build/output.css")

    def test_normal_files_not_skipped(self):
        assert not _should_skip("src/app.py")
        assert not _should_skip("README.md")
        assert not _should_skip("tests/test_auth.py")
        assert not _should_skip("main.go")

    def test_nested_lockfile(self):
        assert _should_skip("subdir/package-lock.json")

    def test_lockfile_extension(self):
        assert _should_skip("something.lock")


class TestParseDiff:
    def test_simple_diff(self):
        files = parse_diff(SIMPLE_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.path == "src/app.py"
        assert f.added_lines == 2
        assert f.removed_lines == 0
        assert len(f.hunks) == 1

    def test_hunk_positions(self):
        files = parse_diff(SIMPLE_DIFF)
        hunk = files[0].hunks[0]
        assert hunk.old_start == 10
        assert hunk.old_count == 6
        assert hunk.new_start == 10
        assert hunk.new_count == 8
        assert hunk.start_position == 1  # first @@ line is position 1

    def test_multi_file_diff(self):
        files = parse_diff(MULTI_FILE_DIFF)
        assert len(files) == 2
        assert files[0].path == "src/auth.py"
        assert files[1].path == "src/utils.py"

    def test_multi_file_line_counts(self):
        files = parse_diff(MULTI_FILE_DIFF)
        auth = files[0]
        assert auth.added_lines == 2  # +secrets, +secrets.compare_digest line
        assert auth.removed_lines == 1  # -hashlib.md5 line

        utils = files[1]
        assert utils.added_lines == 3  # blank line + def + return
        assert utils.removed_lines == 0

    def test_lockfile_skipped(self):
        files = parse_diff(LOCKFILE_DIFF)
        assert len(files) == 1
        assert files[0].path == "src/real.py"

    def test_empty_diff(self):
        assert parse_diff("") == []

    def test_total_changed_property(self):
        fd = FileDiff(path="test.py", added_lines=5, removed_lines=3)
        assert fd.total_changed == 8

    def test_content_for_review(self):
        files = parse_diff(SIMPLE_DIFF)
        content = files[0].content_for_review()
        assert content.startswith("--- src/app.py")
        assert "@@ -10,6 +10,8 @@" in content
        assert "+    if not config.debug:" in content

    def test_large_file_filtered(self):
        """Files with more than MAX_FILE_LINES changed lines should be filtered out."""
        lines = []
        for i in range(MAX_FILE_LINES + 10):
            lines.append(f"+line {i}")
        hunk_content = "\n".join(lines)
        diff = (
            f"diff --git a/big.py b/big.py\n"
            f"--- a/big.py\n"
            f"+++ b/big.py\n"
            f"@@ -1,0 +1,{MAX_FILE_LINES + 10} @@\n"
            f"{hunk_content}"
        )
        files = parse_diff(diff)
        assert len(files) == 0

    def test_position_tracking_across_hunks(self):
        """Position counter should be continuous across hunks within a file."""
        diff = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -1,3 +1,4 @@
 line1
+added1
 line3
@@ -10,3 +11,4 @@
 line10
+added2
 line12"""
        files = parse_diff(diff)
        assert len(files) == 1
        assert len(files[0].hunks) == 2
        h1, h2 = files[0].hunks
        assert h1.start_position == 1  # first @@
        # h1 has 3 content lines after @@, so next @@ is at position 1+3+1=5
        assert h2.start_position == 5

    def test_diff_git_without_b_prefix(self):
        """Malformed diff line without ' b/' should not crash."""
        diff = "diff --git a/onlyA\n"
        files = parse_diff(diff)
        assert len(files) == 0

    def test_binary_or_no_hunks(self):
        """File with diff header but no hunks should still be parsed (0 changes)."""
        diff = """\
diff --git a/img.png b/img.png
Binary files differ"""
        files = parse_diff(diff)
        # File is created but has 0 changes, which is <= MAX_FILE_LINES
        assert len(files) == 1
        assert files[0].total_changed == 0
