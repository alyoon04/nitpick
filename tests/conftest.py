"""Shared test fixtures and data."""

import pytest


# -- Sample diffs --

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


# -- Webhook payload builders --

def make_pr_payload(action="opened", merged=False):
    payload = {
        "action": action,
        "installation": {"id": 12345},
        "repository": {
            "full_name": "octocat/hello-world",
            "id": 99999,
        },
        "pull_request": {
            "number": 42,
            "merged": merged,
            "title": "Fix auth bug",
            "body": "Fixes the password hashing",
            "user": {"login": "octocat"},
            "base": {"sha": "abc123", "repo": {"id": 99999}},
            "head": {"sha": "def456"},
        },
    }
    if action == "closed" and merged:
        payload["pull_request"]["merged"] = True
    return payload


def make_comment_payload(in_reply_to_id=None):
    return {
        "action": "created",
        "installation": {"id": 12345},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"number": 42},
        "comment": {
            "id": 777,
            "body": "I disagree, this is fine",
            "user": {"login": "octocat"},
            "in_reply_to_id": in_reply_to_id,
        },
    }
