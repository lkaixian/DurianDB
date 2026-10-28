"""Build the single-file executable: dist/durian-<os>-<arch>[.exe] and a .sha256 file beside it.

Usage: python -m pip install pyinstaller==6.22.3 && python tools/build_exe.py   (run from anywhere)

PyInstaller cannot run durian/__main__.py directly (its relative import needs the package), so a one-line
wrapper in build/ calls the same entry point, durian.cli.main. Output is a console app, not a windowed one.
"""
import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD, DIST = ROOT / "build", ROOT / "dist"
# Imported inside functions or by name, so listed explicitly: a later change to how they are loaded cannot
# silently leave them out of the bundle. The rest of the tree is found by PyInstaller reading the imports.
HIDDEN = ["durian.server", "durian.supervisor", "durian.reset", "durian.export", "durian.ingest", "durian.auth",
          "paho.mqtt.client", "pymysql", "serial"]


def main():
    BUILD.mkdir(exist_ok=True)
    entry = BUILD / "durian_entry.py"
    entry.write_text("import sys\nfrom durian.cli import main\n\nsys.exit(main())\n", encoding="utf-8")
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--name", "durian", "--noconfirm", "--clean",
           "--paths", str(ROOT), "--specpath", str(BUILD), "--workpath", str(BUILD / "pyinstaller"),
           "--distpath", str(DIST), "--add-data", f"{ROOT / 'web'}{os.pathsep}web"]
    for module in HIDDEN:
        cmd += ["--hidden-import", module]
    subprocess.run([*cmd, str(entry)], check=True, cwd=ROOT)

    windows = os.name == "nt"
    built = DIST / ("durian.exe" if windows else "durian")
    system = "windows" if windows else platform.system().lower()
    arch = platform.machine().lower().replace("amd64", "x86_64")
    exe = DIST / f"durian-{system}-{arch}{built.suffix}"
    os.replace(built, exe)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    sums = DIST / f"durian-{system}-{arch}.sha256"
    sums.write_text(f"{digest}  {exe.name}\n", encoding="utf-8", newline="\n")  # LF, so sha256sum -c accepts it on Linux
    print(f"Built {exe} ({exe.stat().st_size / 1_000_000:.1f} MB)\nsha256 {digest}\nWrote {sums}")


if __name__ == "__main__":
    main()
