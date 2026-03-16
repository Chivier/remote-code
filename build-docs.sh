#!/usr/bin/env bash
# Build mdBook documentation (Chinese + English)
#
# Output layout:
#   book/          <- English (default)
#   book/en/       <- English (explicit, copy of root)
#   book/zh/       <- Chinese
#
# Usage: ./build-docs.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

rm -rf book

# ── 1. Build English → book/ (default root, must be first) ──
echo "==> Building default (English) at root..."
cat > book.toml <<'EOF'
[book]
title = "Remote Code"
description = "Remote Code CLI control via Discord/Telegram bots"
authors = ["Remote Code Contributors"]
language = "en"
src = "docs/en"

[build]
build-dir = "book"
create-missing = false

[output.html]
default-theme = "light"
preferred-dark-theme = "navy"
additional-css = ["theme/lang-selector.css"]
additional-js  = ["theme/lang-selector.js"]
EOF
mdbook build

# ── 2. Build Chinese → book/zh/ ──
echo "==> Building Chinese documentation..."
cat > book.toml <<'EOF'
[book]
title = "Remote Code"
description = "通过 Discord/Telegram Bot 远程控制 Claude CLI"
authors = ["Remote Code Contributors"]
language = "zh"
src = "docs/zh"

[build]
build-dir = "book/zh"
create-missing = false

[output.html]
default-theme = "light"
preferred-dark-theme = "navy"
additional-css = ["theme/lang-selector.css"]
additional-js  = ["theme/lang-selector.js"]
EOF
mdbook build

# ── 3. Copy root English → book/en/ ──
echo "==> Creating book/en/ (copy from root)..."
mkdir -p book/en
for item in book/*; do
    base="$(basename "$item")"
    [ "$base" = "zh" ] && continue
    [ "$base" = "en" ] && continue
    cp -r "$item" "book/en/$base"
done

# ── 4. Restore book.toml ──
cat > book.toml <<'EOF'
[book]
title = "Remote Code"
description = "Remote Code CLI control via Discord/Telegram bots"
authors = ["Remote Code Contributors"]
language = "en"
src = "docs/en"

[build]
build-dir = "book"
create-missing = false

[output.html]
default-theme = "light"
preferred-dark-theme = "navy"
additional-css = ["theme/lang-selector.css"]
additional-js  = ["theme/lang-selector.js"]
EOF

echo "==> Done!"
echo "    book/        English (default)"
echo "    book/en/     English"
echo "    book/zh/     Chinese"
