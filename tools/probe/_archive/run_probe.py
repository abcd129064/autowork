import subprocess, sys
p = subprocess.run(
    [sys.executable, "tools/_probe_mysql_migrate.py"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=".")
with open("tools/_run_out.txt", "w", encoding="utf-8") as f:
    f.write("=== rc=%d ===\n" % p.returncode)
    f.write("--- stdout ---\n" + (p.stdout or "") + "\n")
    f.write("--- stderr ---\n" + (p.stderr or "") + "\n")
