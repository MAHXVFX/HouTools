"""Pack the HouTools release zip (necessary files only) and verify it.

Usage:
    python pack_zip.py <version> [--out <path>] [--root <repo>]

<version>  release number with or without the leading "v" (v1.4.0 / 1.4.0).
--out      output path; defaults to HouTools.zip in the user's REAL Downloads
           folder (resolved via SHGetKnownFolderPath -- user folders may be
           redirected, do not assume C:\\Users\\<name>\\Downloads). Use a temp
           path here for dry runs so the real zip is not clobbered.
--root     repo root; auto-detected by walking up from this script looking
           for HouTools.json + MainMenuCommon.xml when omitted.

Behavior:
1. Assert the working tree already carries <version> in houtools/__init__.py
   and docs/index.html (prevents packing a stale version).
2. Stage ONLY runtime-necessary files with a two-entry top level: the package
   manifest HouTools.json at the zip ROOT and everything else under a
   HouTools/ folder -- extraction yields exactly `HouTools.json` + `HouTools/`
   side by side, so unzipping straight into Documents/houdiniXX.X/packages/
   completes the install (the manifest points at $HOUDINI_PACKAGE_PATH/HouTools).
   Inside HouTools/: README.md, MainMenuCommon.xml, NetworkViewMenu.xml,
   OPmenu.xml, python3.13libs/, python_panels/, scripts/, docs/
   Excluded on purpose: tests/, LICENSE, AGENTS.md, settings/,
   __pycache__, *.pyc. (LICENSE stays out per the "necessary files only"
   decision; the About footer's ../LICENSE link won't resolve in zip installs
   -- accepted trade-off.)
3. Zip with Python zipfile (forward-slash entry names; PowerShell 5.1
   Compress-Archive writes backslash entries -- do not use it).
4. Verify the finished zip: integrity, packaged index.html carries
   v<version>, no excluded paths, no pycache. Prints path/entries/size.
"""

import ctypes
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

FILES = ["README.md", "MainMenuCommon.xml", "NetworkViewMenu.xml", "OPmenu.xml"]
DIRS = ["python3.13libs", "python_panels", "scripts", "docs"]
MANIFEST = "HouTools.json"  # zip top level, next to PACKAGE_DIR
PACKAGE_DIR = "HouTools"

_DOWNLOADS_GUID = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}")  # FOLDERID_Downloads


def downloads_dir() -> Path:
    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    g = _GUID(_DOWNLOADS_GUID.time_low, _DOWNLOADS_GUID.time_mid,
              _DOWNLOADS_GUID.time_hi_version,
              (ctypes.c_ubyte * 8)(*_DOWNLOADS_GUID.bytes[8:]))
    buf = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(g), 0, None, ctypes.byref(buf)) == 0:
        return Path(buf.value)
    return Path.home() / "Downloads"


def find_repo_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit)
        if not (root / "HouTools.json").is_file():
            raise SystemExit("--root has no HouTools.json: %s" % root)
        return root
    for p in Path(__file__).resolve().parents:
        if (p / "HouTools.json").is_file() and (p / "MainMenuCommon.xml").is_file():
            return p
    raise SystemExit("repo root not found (no HouTools.json above the skill)")


def main(argv: list[str]) -> None:
    args = [a for a in argv[1:]]
    if not args:
        raise SystemExit(__doc__)
    version = args[0].lstrip("vV")
    out = Path(args[args.index("--out") + 1]) if "--out" in args else None
    root = find_repo_root(args[args.index("--root") + 1] if "--root" in args else None)
    out = out or downloads_dir() / "HouTools.zip"

    init_py = (root / "python3.13libs" / "houtools" / "__init__.py").read_text(encoding="utf-8")
    html = (root / "docs" / "index.html").read_text(encoding="utf-8")
    assert '__version__ = "%s"' % version in init_py, \
        "python3.13libs/houtools/__init__.py not bumped to %s" % version
    assert '<span class="v">v%s</span>' % version in html, \
        "docs/index.html hero version not bumped to v%s" % version

    tmp = Path(tempfile.mkdtemp(prefix="houtools_pkg_"))
    try:
        stage = tmp / PACKAGE_DIR
        stage.mkdir()
        for f in FILES:
            shutil.copy2(root / f, stage / f)
        for d in DIRS:
            shutil.copytree(root / d, stage / d,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(root / MANIFEST, tmp / MANIFEST)

        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in sorted(tmp.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None, "corrupt zip entry"
        names = z.namelist()
        assert not any("\\" in n for n in names), "backslash entry name"
        assert MANIFEST in names, "manifest not at zip top level"
        assert not any(n.startswith("%s/%s" % (PACKAGE_DIR, MANIFEST))
                       for n in names), "manifest leaked into package dir"
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names), "pycache leaked"
        banned = ("%s/tests/" % PACKAGE_DIR,
                  "%s/LICENSE" % PACKAGE_DIR, "%s/AGENTS" % PACKAGE_DIR,
                  "%s/settings/" % PACKAGE_DIR)
        assert not any(n.startswith(banned) for n in names), "dev file leaked"
        packaged_html = z.read("%s/docs/index.html" % PACKAGE_DIR).decode("utf-8")
        assert "v%s" % version in packaged_html, "packaged index.html missing v%s" % version

    print("zip:", out)
    print("entries:", len(names))
    print("size: %.2f MB" % (out.stat().st_size / 1048576))
    print("top level:", sorted(set(n.split("/")[0] for n in names)))
    print("PACK OK")


if __name__ == "__main__":
    main(sys.argv)
