"""Create a standalone asset ZIP without project services, caches, or environments."""

import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "PizzaOps_Cozy_Isometric_Asset_Pack_v1"


def package(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{NAME}.zip"
    paths = [path for path in (ROOT / "assets" / "pizzaops").rglob("*") if path.is_file()]
    paths.extend(ROOT / relative for relative in (
        "README.md", "ASSET_LICENSE.md", "CODE_LICENSE.md", "requirements.txt",
        "viewer/main.py", "viewer/assets.py", "viewer/asset_demo.py", "tests/test_assets.py",
        "tools/build_assets.py", "tools/validate_assets.py", "tools/package_assets.py",
    ))
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for path in sorted(paths):
            if "__pycache__" in path.parts or path.name == ".DS_Store":
                continue
            zipped.write(path, f"{NAME}/{path.relative_to(ROOT).as_posix()}")
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert len(zipped.namelist()) == len(set(zipped.namelist()))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"Created {archive.name}: {archive.stat().st_size:,} bytes; SHA256 {digest}")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    package(args.output)
