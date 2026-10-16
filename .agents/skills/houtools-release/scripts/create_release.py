"""Create the GitCode release for a pushed tag and upload HouTools.zip to it.

Usage:
    python create_release.py <version> [--notes <file>] [--zip <path>] [--root <repo>]

<version>  release tag with or without the leading "v" (v1.6.0 / 1.6.0). The
           tag must already exist locally AND on the remote (skill step 3
           pushes it); its annotated message doubles as the release notes.
--notes    notes file override; default reads the annotated tag message via
           `git tag -l --format=%(contents)` (skill step 3 writes the notes
           there, so the default path needs no extra file).
--zip      attachment override; defaults to HouTools.zip in the user's REAL
           Downloads folder (SHGetKnownFolderPath -- user folders may be
           redirected, same helper as pack_zip.py).
--root     repo root; auto-detected by walking up from this script looking
           for HouTools.json + MainMenuCommon.xml (same as pack_zip.py).

Token lookup (mirrors the zhuque-ai-detector skill's key handling):
1. config file ~/.gitcode_api_token -- one plain-text line; utf-8-sig and
   utf-16 are both tolerated so PowerShell redirects work too;
2. persistent user env var GITCODE_TOKEN read from registry HKCU\\Environment.
   Deliberately NOT os.environ: a process env is a snapshot taken at launch
   and may be stale; the registry value is what setx writes, so a fresh
   `setx GITCODE_TOKEN <value>` takes effect immediately without restarting.

GitCode OpenAPI v5 flow (verified 2026-10-17 shipping v1.6.0):
1. GET  /user                     token check; fails fast with guidance.
2. GET  /releases/tags/<tag>      reuse an existing release, never duplicate.
3. POST /releases                 create {tag_name, name, body,
                                  release_status: latest}. The tag already
                                  exists on the remote, so target_commitish
                                  is not needed. An existing release keeps
                                  its body UNLESS it is empty (then PATCH
                                  the notes in) -- a differing non-empty
                                  body is treated as user edits, kept as-is.
4. GET  .../upload_url            short-lived OBS signed URL plus a header
                                  set that MUST be forwarded verbatim --
                                  x-obs-callback is what links the asset to
                                  the release afterwards.
5. PUT  the zip to OBS            NO GitCode token on this request and NO
                                  redirect following (the signed URL is
                                  single-purpose). Use it immediately: the
                                  signature expires quickly.
6. poll GET /releases/tags/<tag>  until HouTools.zip shows up in assets
                                  (the OBS callback lags a few seconds).

Idempotent: re-running never duplicates the release or the attachment.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "https://api.gitcode.com/api/v5"
FILE_NAME = "HouTools.zip"
TOKEN_FILE = ".gitcode_api_token"
TOKEN_PORTAL = "https://gitcode.com/setting/token-classic"
POLL_TRIES, POLL_WAIT_S = 6, 3

_DOWNLOADS_GUID = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}")  # FOLDERID_Downloads


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


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


def read_token():
    """配置文件 ~/.gitcode_api_token → 注册表用户环境变量 GITCODE_TOKEN。

    故意不读 os.environ：进程环境是启动时的一次性快照可能过时；注册表
    HKCU\\Environment 是 setx 的持久落点，改完即时生效无需重开终端。
    """
    key_file = os.path.join(os.path.expanduser("~"), TOKEN_FILE)
    try:
        try:
            with open(key_file, "r", encoding="utf-8-sig") as f:
                v = f.read()
        except UnicodeDecodeError:  # PowerShell 重定向默认写出 UTF-16
            with open(key_file, "r", encoding="utf-16") as f:
                v = f.read()
        v = v.replace("\x00", "").strip().strip('"').strip("'")
        if v:
            return v, "配置文件 %s" % key_file
    except OSError:
        pass
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                raw, _ = winreg.QueryValueEx(k, "GITCODE_TOKEN")
        except OSError:
            raw = None
        v = str(raw or "").replace("\x00", "").strip().strip('"').strip("'")
        if v:
            return v, "用户环境变量 GITCODE_TOKEN（注册表）"
    return None, None


def token_help():
    print("未找到 gitcode 令牌。二选一配置：")
    print('  ① echo <令牌> > ~/%s   （bash；令牌在 %s 创建）' % (TOKEN_FILE, TOKEN_PORTAL))
    print("  ② setx GITCODE_TOKEN <令牌>   （用户环境变量，即时生效）")
    print("令牌需授予仓库 releases 相关权限。")


def git_output(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def parse_owner_repo(remote_url: str) -> tuple[str, str]:
    m = re.search(r"gitcode\.com[/:](.+?)/(.+?)\.git/?$", remote_url.strip())
    if not m:
        raise SystemExit("cannot parse gitcode owner/repo from origin: %s" % remote_url)
    return m.group(1), m.group(2)


def call(method, url, token=None, headers=None, data=None, timeout=120):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.data = data
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        return 0, str(e.reason).encode("utf-8", "replace")


def jtext(b: bytes) -> str:
    return b.decode("utf-8", "replace")


def release_url(owner: str, repo: str, tag: str) -> str:
    return "https://gitcode.com/%s/%s/releases/tag/%s" % (owner, repo, tag)


def asset_names(release: dict) -> list[str]:
    return [a.get("name") or a.get("file_name") or "" for a in release.get("assets") or []]


def main(argv: list[str]) -> None:
    args = list(argv[1:])
    if not args:
        raise SystemExit(__doc__)
    tag = "v" + args[0].lstrip("vV")
    root = find_repo_root(args[args.index("--root") + 1] if "--root" in args else None)

    notes = git_output(root, "tag", "-l", "--format=%(contents)", tag).strip()
    if "--notes" in args:
        notes = Path(args[args.index("--notes") + 1]).read_text(encoding="utf-8").strip()
    if not notes:
        raise SystemExit("tag %s has no annotated message - skill step 3 writes "
                         "the release notes into the tag first" % tag)
    zip_path = (Path(args[args.index("--zip") + 1]) if "--zip" in args
                else downloads_dir() / FILE_NAME)
    if not zip_path.is_file():
        raise SystemExit("attachment not found: %s (run pack_zip.py first)" % zip_path)
    owner, repo = parse_owner_repo(git_output(root, "remote", "get-url", "origin"))

    token, src = read_token()
    if not token:
        token_help()
        raise SystemExit(1)
    print("token source:", src)

    # 1. token check
    s, b = call("GET", BASE + "/user", token)
    print("[1] token check:", s)
    if s == 401:
        print("    令牌无效或已吊销——更新 ~/%s 或注册表 GITCODE_TOKEN 后重试" % TOKEN_FILE)
        raise SystemExit(1)
    if s != 200:
        print(jtext(b)[:300])
        raise SystemExit(1)
    me = json.loads(jtext(b))
    print("    user:", me.get("login") or me.get("username") or me.get("name"))

    # 2. existing release?
    s, b = call("GET", BASE + "/repos/%s/%s/releases/tags/%s" % (owner, repo, tag), token)
    release = json.loads(jtext(b)) if s == 200 else None
    print("[2] existing release:", "yes" if release else "no (http %s)" % s)

    # 3. create or patch
    if release is None:
        body = {"tag_name": tag, "name": tag, "body": notes, "release_status": "latest"}
        s, b = call("POST", BASE + "/repos/%s/%s/releases" % (owner, repo), token,
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(body).encode("utf-8"))
        print("[3] create release:", s)
        if s not in (200, 201):
            print(jtext(b)[:500])
            raise SystemExit(1)
        release = json.loads(jtext(b))
    else:
        cur = (release.get("body") or "").strip()
        if cur == notes:
            print("[3] body already matches tag notes, skip patch")
        elif not cur:
            s2, b2 = call("PATCH", BASE + "/repos/%s/%s/releases/%s" % (owner, repo, tag),
                          token, headers={"Content-Type": "application/json"},
                          data=json.dumps({"name": release.get("name") or tag,
                                           "body": notes}).encode("utf-8"))
            print("[3] patch empty body:", s2)
            if s2 not in (200, 201):
                print(jtext(b2)[:300])
        else:
            print("[3] existing body differs from tag notes, kept as-is")

    # 4-5. attachment
    if FILE_NAME in asset_names(release):
        print("[4] attachment already present")
        print("RELEASE OK", release_url(owner, repo, tag))
        return

    s, b = call("GET", BASE + "/repos/%s/%s/releases/%s/upload_url?file_name=%s"
                % (owner, repo, tag, urllib.parse.quote(FILE_NAME)), token, timeout=60)
    print("[4] upload_url:", s)
    if s != 200:
        print(jtext(b)[:500])
        raise SystemExit(1)
    up = json.loads(jtext(b))
    payload = zip_path.read_bytes()
    print("    zip bytes:", len(payload), "; obs headers:", sorted(up.get("headers", {})))

    s, b = call("PUT", up["url"], headers=up.get("headers", {}), data=payload, timeout=300)
    print("[5] PUT to OBS:", s)
    if s not in (200, 201):
        print(jtext(b)[:500])
        raise SystemExit(1)

    # 6. verify association (OBS callback may lag a few seconds)
    for _ in range(POLL_TRIES):
        time.sleep(POLL_WAIT_S)
        s, b = call("GET", BASE + "/repos/%s/%s/releases/tags/%s" % (owner, repo, tag), token)
        if s == 200:
            names = asset_names(json.loads(jtext(b)))
            if FILE_NAME in names:
                print("[6] verified assets:", names)
                print("RELEASE OK", release_url(owner, repo, tag))
                return
            print("    waiting for callback... assets=%s" % names)
    print("[6] asset not visible after %ds - check the release page shortly"
          % (POLL_TRIES * POLL_WAIT_S))
    raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv)
