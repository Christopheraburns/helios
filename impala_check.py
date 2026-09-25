diff --git a/helios_core/engines/impala.py b/helios_core/engines/impala.py
index 46b56f2..7062f3a 100644
--- a/helios_core/engines/impala.py
+++ b/helios_core/engines/impala.py
@@ -7,6 +7,15 @@ from ..config import ImpalaConfig
 from .base import Engine, QueryResult
 
 
+def _short_name(user: str) -> str:
+    """Kerberos-style short name: text before the first '/' or '@', lower-cased."""
+    return user.strip().split("/", 1)[0].split("@", 1)[0].lower()
+
+
+def _same_user(a: str, b: str) -> bool:
+    return bool(a and b) and _short_name(a) == _short_name(b)
+
+
 class ImpalaEngine(Engine):
     name = "impala"
     sqlglot_dialect = "hive"   # SQLGlot has no dedicated Impala dialect; Hive is the closest and is post-processed by the compiler
@@ -17,6 +26,10 @@ class ImpalaEngine(Engine):
     def _connect(self, delegated_user: str | None = None):
         from impala.dbapi import connect
         http_path = self.cfg.http_path
+        if delegated_user and _same_user(delegated_user, self.cfg.user):
+            # Already connected as this user. Impala rejects doAs-to-self unless the Virtual
+            # Warehouse has a proxy config, so skip it; query() still verifies EFFECTIVE_USER().
+            delegated_user = None
         if delegated_user:
             if not self.cfg.proxy_delegation:
                 raise PermissionError("Impala proxy-user delegation is disabled")
@@ -42,7 +55,7 @@ class ImpalaEngine(Engine):
             if delegated_user:
                 cur.execute("SELECT EFFECTIVE_USER()")
                 effective = cur.fetchone()
-                if not effective or effective[0] != delegated_user:
+                if not effective or not _same_user(str(effective[0]), delegated_user):
                     raise PermissionError(
                         "Impala did not enforce the delegated SSO identity"
                     )
diff --git a/scripts/impala_check.py b/scripts/impala_check.py
new file mode 100644
index 0000000..87829e3
--- /dev/null
+++ b/scripts/impala_check.py
@@ -0,0 +1,30 @@
+"""Isolate Impala connectivity and proxy delegation from the MCP server.
+
+Run in a Workbench Session (same project env vars as the MCP Application):
+  python helios/scripts/impala_check.py cburns
+Prints each step's result or Impala's full error message.
+"""
+import os
+import sys
+
+sys.path.insert(0, os.environ.get("HELIOS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+from helios_core.config import impala_config
+from helios_core.engines import ImpalaEngine
+
+delegate = sys.argv[1] if len(sys.argv) > 1 else None
+cfg = impala_config()
+if cfg is None:
+    sys.exit("impala_config() is None: set IMPALA_HOST, WORKLOAD_USER and WORKLOAD_PASSWORD")
+print(f"host={cfg.host}:{cfg.port} http_path={cfg.http_path} connect_as={cfg.user} "
+      f"proxy_delegation={cfg.proxy_delegation} delegate_to={delegate}")
+engine = ImpalaEngine(cfg)
+steps = [("1 connect, no delegation", "SELECT user(), effective_user()", None)]
+if delegate:
+    steps.append(("2 connect with delegation", "SELECT user(), effective_user()", delegate))
+    steps.append(("3 delegated read on tpcds", "SELECT count(*) FROM tpcds.date_dim", delegate))
+for label, sql, user in steps:
+    try:
+        print(f"[ok]   {label}: {engine.query(sql, 10, delegated_user=user).rows}")
+    except Exception as exc:  # noqa: BLE001
+        print(f"[fail] {label}: {type(exc).__name__}: {exc}")
+        break
diff --git a/tests/test_talk_to_data.py b/tests/test_talk_to_data.py
index 8a62f57..8204b6d 100644
--- a/tests/test_talk_to_data.py
+++ b/tests/test_talk_to_data.py
@@ -1106,3 +1106,23 @@ def test_conversation_endpoint_reports_missing_server_configuration(
         "MCP conversation connectivity is not configured"
         in response.json()["detail"]
     )
+
+
+def test_impala_skips_delegation_to_the_connected_user(monkeypatch):
+    captured = {}
+
+    def connect(**kwargs):
+        captured.update(kwargs)
+        return FakeConnection("cburns")
+
+    monkeypatch.setattr(impala_dbapi, "connect", connect)
+    engine = ImpalaEngine(
+        ImpalaConfig(
+            "warehouse.example", 443, "cburns", "secret",
+            http_path="cliservice", proxy_delegation=True,
+        )
+    )
+    result = engine.query("SELECT 7", delegated_user="CBurns")
+
+    assert result.rows == [(7,)]
+    assert "doAs" not in captured["http_path"]