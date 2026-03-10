"""Build and upload to PyPI.

Prerequisites:
    pip install build twine

Usage:
    python scripts/publish_pypi.py          # upload to PyPI
    python scripts/publish_pypi.py --test   # upload to TestPyPI first
"""
import subprocess
import sys
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    test_mode = "--test" in sys.argv

    # clean previous builds
    for d in ["dist", "build"]:
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            shutil.rmtree(p)

    for d in os.listdir(ROOT):
        if d.endswith(".egg-info"):
            shutil.rmtree(os.path.join(ROOT, d))

    # build
    print("[publish] Building sdist and wheel...")
    subprocess.run([sys.executable, "-m", "build"], cwd=ROOT, check=True)

    # upload
    if test_mode:
        print("\n[publish] Uploading to TestPyPI...")
        subprocess.run([
            sys.executable, "-m", "twine", "upload",
            "--repository", "testpypi",
            os.path.join(ROOT, "dist", "*"),
        ], cwd=ROOT, check=True)
        print("\n[publish] Done! Install with:")
        print("  pip install --index-url https://test.pypi.org/simple/ namemasker")
    else:
        print("\n[publish] Uploading to PyPI...")
        subprocess.run([
            sys.executable, "-m", "twine", "upload",
            os.path.join(ROOT, "dist", "*"),
        ], cwd=ROOT, check=True)
        print("\n[publish] Done! Install with:")
        print("  pip install namemasker")


if __name__ == "__main__":
    main()
