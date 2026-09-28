"""把使用者手册从 Markdown 转成随包发出去的 HTML。

    .venv/bin/python scripts/build_manuals.py            # 输出到 build/manuals/
    .venv/bin/python scripts/build_manuals.py 输出目录

使用者的电脑上多半没有能看 .md 的东西，双击 .html 谁都能打开。
仓库里只维护 Markdown（docs/使用说明.md、docs/User Guide.md），
HTML 是打包时现生成的产物，不进仓库。打包脚本（spec / make_dmg.sh）会调用这里。
依赖 `markdown` 包：pip install markdown
"""
import re
import sys
from pathlib import Path

import markdown

# Windows 的控制台默认 cp1252，打印 ✓ 和中文标题会直接抛 UnicodeEncodeError 把打包搞挂
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
MANUALS = ["使用说明.md", "User Guide.md", "高级配置说明.md", "Advanced Guide.md"]   # 互相链接，后缀在下面一起改成 .html

CSS = """
:root { color-scheme: light dark; --fg:#1f2328; --bg:#ffffff; --muted:#59636e; --line:#d1d9e0;
        --code-bg:#f6f8fa; --quote:#0969da; --link:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e6edf3; --bg:#0d1117; --muted:#9198a1; --line:#3d444d; --code-bg:#161b22;
          --quote:#4493f8; --link:#4493f8; }
}
html { background: var(--bg); }
body { margin: 0 auto; padding: 32px 24px 80px; max-width: 880px; color: var(--fg); background: var(--bg);
       font: 16px/1.7 -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC", Helvetica, Arial, sans-serif; }
h1 { font-size: 2em; border-bottom: 1px solid var(--line); padding-bottom: .3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--line); padding-bottom: .3em; margin-top: 2em; }
h3 { font-size: 1.2em; margin-top: 1.6em; }
a { color: var(--link); text-decoration: none; } a:hover { text-decoration: underline; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; font-size: .9em; }
code { background: var(--code-bg); padding: .15em .4em; border-radius: 6px; }
pre { background: var(--code-bg); padding: 14px 16px; border-radius: 8px; overflow-x: auto; line-height: 1.5; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: 8px 12px; text-align: left; vertical-align: top; }
th { background: var(--code-bg); }
blockquote { margin: 1em 0; padding: .2em 1em; border-left: 4px solid var(--quote); color: var(--muted); background: var(--code-bg); border-radius: 0 6px 6px 0; }
blockquote p { margin: .5em 0; }
hr { border: 0; border-top: 1px solid var(--line); margin: 2.5em 0; }
img { max-width: 100%; }
.toc { background: var(--code-bg); border-radius: 8px; padding: 12px 20px; margin: 1.5em 0 2em; }
.toc ul { margin: 0; padding-left: 1.2em; } .toc > ul { padding-left: 0; list-style: none; }
.toc > ul > li { margin: .3em 0; }
@media print { body { max-width: none; padding: 0; } .toc { display: none; } }
"""

TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def convert(md_path):
    text = md_path.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc"],
                           extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(text)
    title = re.search(r"^# (.+)$", text, re.M).group(1).strip()

    # 两份手册之间的链接改指 .html；指向仓库其他地方（../README.md 之类）的链接在包里点不开，退成纯文字
    for name in MANUALS:
        stem = name[:-3]
        for form in (name, name.replace(" ", "%20")):
            body = body.replace(f'href="{form}"', f'href="{form[:-3]}.html"')
    body = re.sub(r'<a href="\.\./[^"]*">(.*?)</a>', r"\1", body)

    # 目录插在第一个 h1 后面
    toc = f'<nav class="toc">{md.toc}</nav>' if md.toc.strip() else ""
    body = re.sub(r"(</h1>)", r"\1" + toc.replace("\\", "\\\\"), body, count=1)

    lang = "zh-CN" if re.search(r"[一-鿿]", title) else "en"
    return title, TEMPLATE.format(lang=lang, title=title, css=CSS, body=body)


def build(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in MANUALS:
        src = ROOT / "docs" / name
        if not src.is_file():
            print(f"跳过：找不到 {src}")
            continue
        title, html = convert(src)
        dst = out_dir / (name[:-3] + ".html")
        dst.write_text(html, encoding="utf-8")
        written.append(dst)
        print(f"✓ {dst.relative_to(ROOT) if dst.is_relative_to(ROOT) else dst}  ← {title}")
    return written


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "manuals")
