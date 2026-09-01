#!/usr/bin/env bash
# 把 dist/Print Bridge.app 包成 dist/Print Bridge.dmg
# 用 mac 自带 hdiutil,无需安装第三方工具
#
# 用法:
#   bash scripts/make_dmg.sh
set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/Print Bridge.app"
OUT_DIR="dist"
DMG="$OUT_DIR/Print Bridge.dmg"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if [[ ! -d "$APP" ]]; then
  echo "找不到 $APP,先跑 pyinstaller build"; exit 1
fi

# 1. 拷贝 .app 到临时 stage
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"

# 2. 拖一个 /Applications 软链接到 DMG 窗口里,方便用户拖拽
ln -s /Applications "$STAGE/Applications"

# 3. 用 UDRW(可读写)先建一个 dmg
TMP_DMG="$(mktemp -d)/stage.dmg"
hdiutil create -ov -volname "Print Bridge" -fs HFS+ -srcfolder "$STAGE" "$TMP_DMG"

# 4. 转成只读压缩版(UDBZ 压缩高,UDZO 兼容性好,默认 UDZO)
rm -f "$DMG"
hdiutil convert "$TMP_DMG" -format UDZO -o "$DMG"

echo
echo "✓ 已生成: $DMG"
du -sh "$DMG"
