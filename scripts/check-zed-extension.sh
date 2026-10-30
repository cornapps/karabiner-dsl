#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXTENSION="$ROOT/editors/zed/karabiner-dsl"
GRAMMAR_SOURCE="$EXTENSION/grammars/tree-sitter-karabiner-dsl"
GRAMMAR_REMOTE="$ROOT/.build/tree-sitter-karabiner-dsl.git"
GRAMMAR_WORK="$ROOT/.build/tree-sitter-karabiner-dsl-work"
GRAMMAR_CHECKOUT="$EXTENSION/grammars/karabiner_dsl"
GRAMMAR_WASM="$EXTENSION/grammars/karabiner_dsl.wasm"
TREE_SITTER_CONFIG="$ROOT/.build/zed-tree-sitter-config.json"
HIGHLIGHTS="$EXTENSION/languages/karabiner-dsl/highlights.scm"
CLANG="$HOME/Library/Application Support/Zed/extensions/build/wasi-sdk/bin/clang"

mkdir -p "$ROOT/.build"
printf '{"parser-directories":["%s/editors/zed/karabiner-dsl/grammars"],"theme":{}}\n' "$ROOT" > "$TREE_SITTER_CONFIG"

cd "$GRAMMAR_SOURCE"
npx --yes tree-sitter-cli generate

rm -rf "$GRAMMAR_WORK" "$GRAMMAR_REMOTE"
mkdir -p "$GRAMMAR_WORK"
cp "$GRAMMAR_SOURCE/grammar.js" "$GRAMMAR_WORK/grammar.js"
cp "$GRAMMAR_SOURCE/package.json" "$GRAMMAR_WORK/package.json"
cp "$GRAMMAR_SOURCE/tree-sitter.json" "$GRAMMAR_WORK/tree-sitter.json"
cp -R "$GRAMMAR_SOURCE/src" "$GRAMMAR_WORK/src"
git -C "$GRAMMAR_WORK" init -b main >/dev/null
git -C "$GRAMMAR_WORK" add .
git -C "$GRAMMAR_WORK" \
  -c user.name="Karabiner DSL" \
  -c user.email="karabiner-dsl@local" \
  commit -m "Update karabiner DSL grammar" >/dev/null
git clone --bare "$GRAMMAR_WORK" "$GRAMMAR_REMOTE" >/dev/null

cd "$ROOT"
for file in *.kb; do
  echo "zed parse $file"
  npx --yes tree-sitter-cli parse --config-path "$TREE_SITTER_CONFIG" --quiet "$file" >/dev/null
  npx --yes tree-sitter-cli query --config-path "$TREE_SITTER_CONFIG" --quiet "$HIGHLIGHTS" "$file" >/dev/null
done

if [ -x "$CLANG" ]; then
  rm -rf "$GRAMMAR_CHECKOUT" "$GRAMMAR_WASM"
  git init "$GRAMMAR_CHECKOUT" >/dev/null
  git -C "$GRAMMAR_CHECKOUT" remote add origin "file://$GRAMMAR_REMOTE"
  git -C "$GRAMMAR_CHECKOUT" fetch --depth 1 origin main
  git -C "$GRAMMAR_CHECKOUT" checkout main
  "$CLANG" -fPIC -shared -Os \
    -Wl,--export=tree_sitter_karabiner_dsl \
    -o "$GRAMMAR_WASM" \
    -I "$GRAMMAR_CHECKOUT/src" \
    "$GRAMMAR_CHECKOUT/src/parser.c"
  echo "zed compiled $(basename "$GRAMMAR_WASM")"
  rm -rf "$GRAMMAR_CHECKOUT" "$GRAMMAR_WASM"
else
  echo "zed wasi clang not found; skipped local wasm compile simulation"
fi
