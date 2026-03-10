"""Build standalone Windows exe using PyInstaller."""
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "namemasker", "assets", "icon.ico")


def main():
    EXCLUDES = [
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "matplotlib", "numpy", "pandas", "scipy", "IPython",
        "notebook", "nbformat", "sphinx", "docutils", "babel",
        "jedi", "black", "yapf", "zmq", "tornado",
    ]
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "NameMasker",
        "--icon", ICON,
        "--add-data", f"{os.path.join(ROOT, 'namemasker', 'assets')};namemasker/assets",
        "--hidden-import", "openai",
        "--hidden-import", "namemasker",
        "--hidden-import", "namemasker.app",
        "--hidden-import", "namemasker.masker",
        "--hidden-import", "namemasker.api_detect",
        "--hidden-import", "namemasker.doc_dialog",
        "--hidden-import", "namemasker.doc_process",
        "--hidden-import", "namemasker.pdf_process",
        "--hidden-import", "docx",
        "--hidden-import", "fitz",
    ]
    for ex in EXCLUDES:
        cmd.extend(["--exclude-module", ex])
    cmd.append(os.path.join(ROOT, "namemasker", "__main__.py"))

    # tkinterdnd2 is optional; include if available
    try:
        import tkinterdnd2
        dnd_dir = os.path.dirname(tkinterdnd2.__file__)
        cmd.extend(["--add-data", f"{dnd_dir};tkinterdnd2"])
        cmd.extend(["--hidden-import", "tkinterdnd2"])
        print("[build] tkinterdnd2 found, including in build")
    except ImportError:
        print("[build] tkinterdnd2 not found, skipping drag-and-drop support")

    print(f"[build] Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"\n[build] Done! Exe at: {os.path.join(ROOT, 'dist', 'NameMasker.exe')}")


if __name__ == "__main__":
    main()
