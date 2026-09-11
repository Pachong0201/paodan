#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
ICON="$ROOT/assets/paodan_workbench.png"
DESKTOP="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
mkdir -p "$DESKTOP"
TARGET="$DESKTOP/paodan-workbench.desktop"
cat > "$TARGET" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Paodan 工作台
Comment=Paodan V5.0.2 Local Workbench
Exec=$ROOT/open_workbench.sh
Icon=$ICON
Terminal=true
Categories=Utility;
EOF
chmod +x "$TARGET"
echo "已创建：$TARGET"
echo "以后双击或从桌面启动即可。"
