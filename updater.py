"""检查更新与更新日志：比版本号、读 CHANGELOG.md、问 GitHub 有没有新版本。

更新源就是 GitHub Release：CI 在打 tag 时发 Release，正文是 CHANGELOG 里本版那一节，
附件是各平台的安装包（文件名里带 windows / macos / linux）。

不做自动替换：程序跑着的时候换不了自己的 exe / .app，只给出下载链接，由使用者自己装。

不依赖 Qt。界面在 ui/updates.py。
"""

import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path

from version import __version__

REPO = "BrianH-Wang/print_bridge"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

# CHANGELOG 里的版本标题：## v1.2.0 - 2026-11-17（日期可省）
_HEADING = re.compile(r"^##\s+v?(\d+(?:\.\d+)*)\b.*$", re.MULTILINE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def parse_version(text):
    """"v1.10.0" → (1, 10, 0)；认不出来返回 None。

    按数字元组比，不能按字符串比，否则 "1.10" 会排在 "1.9" 前面。
    """
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(text).strip())
    if not match:
        return None
    parts = tuple(int(x) for x in match.group(1).split("."))
    # 补齐到三段，1.2 和 1.2.0 算同一个版本
    return parts + (0,) * (3 - len(parts)) if len(parts) < 3 else parts


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


# --------------------------------------------------------------------------- #
# 本地的更新日志（随包带的 CHANGELOG.md）
# --------------------------------------------------------------------------- #

def changelog_path():
    """开发态在仓库根目录，打包后在 PyInstaller 解开的目录里（spec 的 datas 带进去的）。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "CHANGELOG.md"


def read_changelog():
    try:
        return changelog_path().read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_changelog(text):
    """切成 [(版本元组, 标题行, 正文)]，保持文件里的顺序（新的在上）。注释去掉。"""
    text = _COMMENT.sub("", text)
    heads = list(_HEADING.finditer(text))
    sections = []
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        body = text[head.end():end].strip()
        sections.append((parse_version(head.group(1)), head.group(0).strip(), body))
    return sections


def notes_between(text, after, upto):
    """after < 版本 <= upto 的那几节拼成一段 Markdown；after 为 None 时从头算起。

    跨了好几个版本升级的人，要看到中间每一版的改动，不只是最新那一版。
    """
    low = parse_version(after) if after else None
    high = parse_version(upto)
    picked = [
        f"{heading}\n\n{body}"
        for version, heading, body in parse_changelog(text)
        if version and (low is None or version > low) and (high is None or version <= high)
    ]
    return "\n\n".join(picked)


def section(text, version):
    """某一版那一节（含标题）；没写就是空串。"""
    wanted = parse_version(version)
    for found, heading, body in parse_changelog(text):
        if found == wanted:
            return f"{heading}\n\n{body}"
    return ""


def full_changelog_markdown(text):
    """给「更新日志」窗口看的：去掉文件开头的 # 大标题和写给维护者的注释。"""
    sections = parse_changelog(text)
    return "\n\n".join(f"{heading}\n\n{body}" for _, heading, body in sections)


# --------------------------------------------------------------------------- #
# 远端：GitHub Release
# --------------------------------------------------------------------------- #

def _ssl_context():
    # 打包后的 Python 不一定找得到系统证书（macOS 上尤其常见），优先用 certifi 带的那份
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch_releases(timeout=8):
    """正式发布的 Release，版本号从新到旧。草稿和预发布不算。"""
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"PrintBridge/{__version__}",
    })
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        releases = json.load(response)
    usable = [
        r for r in releases
        if not r.get("draft") and not r.get("prerelease") and parse_version(r.get("tag_name", ""))
    ]
    usable.sort(key=lambda r: parse_version(r["tag_name"]), reverse=True)
    return usable


def platform_key(platform=None):
    platform = platform or sys.platform
    if platform == "win32":
        return "windows"
    if platform == "darwin":
        return "macos"
    return "linux"


def pick_asset(release, platform=None):
    """本平台的安装包下载地址；找不到就退回 Release 页面，让人自己挑。"""
    key = platform_key(platform)
    for asset in release.get("assets", []):
        if key in asset.get("name", "").lower():
            return asset["browser_download_url"]
    return release.get("html_url") or RELEASES_PAGE


def find_update(releases, current=__version__, platform=None):
    """有比 current 新的版本就返回 dict，没有返回 None。

    notes 是 current 之后每一版 Release 正文拼起来的，从新到旧。
    """
    newer = [r for r in releases if is_newer(r["tag_name"], current)]
    if not newer:
        return None
    latest = newer[0]
    notes = "\n\n".join(
        f"## {r['tag_name']}\n\n{(r.get('body') or '（这一版没有写说明）').strip()}"
        for r in newer
    )
    return {
        "version": latest["tag_name"].lstrip("v"),
        "notes": notes,
        "download_url": pick_asset(latest, platform),
        "page_url": latest.get("html_url") or RELEASES_PAGE,
    }


def check_for_update(current=__version__, timeout=8):
    """联网问一次。网络错误照常抛出，由调用方决定要不要告诉使用者。"""
    return find_update(fetch_releases(timeout), current)
