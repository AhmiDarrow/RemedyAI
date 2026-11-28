#!/bin/sh
# Installs the Remedy runtime and its `remedy` command on Linux or macOS.
#
# For a machine without the desktop app (a server, a headless box, an ARM
# board). It downloads the runtime for this computer from the GitHub release,
# checks both files against the release's SHA-256 list, and installs them for
# the current user. Nothing is installed system-wide and nothing needs root.
#
#   sh install.sh              # latest release
#   sh install.sh 2.1.2        # a given version
#
# Environment:
#   REMEDY_INSTALL_DIR   where the runtime goes   (default ~/.local/lib/remedy)
#   REMEDY_BIN_DIR       where `remedy` goes      (default ~/.local/bin)
set -eu

REPO="AhmiDarrow/RemedyAI"
RELEASES="https://github.com/${REPO}/releases"
SUMS="remedy-native-SHA256SUMS.txt"

say() { printf '%s\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }

fetch() { # fetch <url> <file>
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --proto '=https' --tlsv1.2 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --https-only -O "$2" "$1"
  else
    die "curl or wget is needed to download Remedy"
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    die "sha256sum or shasum is needed to check the download"
  fi
}

case "$(uname -s)" in
  Linux)  os="unknown-linux-gnu"; lib_ext="so" ;;
  Darwin) os="apple-darwin";      lib_ext="dylib" ;;
  *) die "this script supports Linux and macOS. On Windows use install.ps1." ;;
esac
case "$(uname -m)" in
  x86_64|amd64)  arch="x86_64" ;;
  aarch64|arm64) arch="aarch64" ;;
  *) die "no Remedy runtime is published for $(uname -m)" ;;
esac
triple="${arch}-${os}"
runtime_asset="remedy-runtime-${triple}"
core_asset="libremedy_core-${triple}.${lib_ext}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

version="${1:-}"
if [ -z "$version" ]; then
  fetch "${RELEASES}/latest/download/latest.json" "$tmp/latest.json" ||
    die "could not read the latest release. Give a version: sh install.sh 2.1.2"
  version="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$tmp/latest.json" | head -n 1)"
fi
version="${version#v}"
case "$version" in
  ''|*[!0-9A-Za-z.+-]*) die "that does not look like a version: '$version'" ;;
esac

base="${RELEASES}/download/runtime-v${version}"
say "Remedy ${version} for ${triple}"
fetch "${base}/${SUMS}" "$tmp/$SUMS" || die "release runtime-v${version} has no checksum list"
for asset in "$runtime_asset" "$core_asset"; do
  fetch "${base}/${asset}" "$tmp/$asset" || die "release runtime-v${version} has no ${asset}"
  want="$(awk -v f="$asset" '$2 == f || $2 == "*" f { print $1 }' "$tmp/$SUMS" | head -n 1)"
  [ -n "$want" ] || die "${asset} is not in the release's checksum list; not installing it"
  got="$(sha256_of "$tmp/$asset")"
  [ "$want" = "$got" ] || die "${asset} does not match its checksum; not installing it"
done
say "Checksums match."

install_dir="${REMEDY_INSTALL_DIR:-$HOME/.local/lib/remedy}"
bin_dir="${REMEDY_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$install_dir" "$bin_dir"
# Written beside the old files and moved into place, so a running `remedy`
# is never left with half a binary.
cp "$tmp/$runtime_asset" "$install_dir/remedy-runtime.new"
cp "$tmp/$core_asset" "$install_dir/libremedy_core.${lib_ext}.new"
chmod 755 "$install_dir/remedy-runtime.new"
mv -f "$install_dir/remedy-runtime.new" "$install_dir/remedy-runtime"
mv -f "$install_dir/libremedy_core.${lib_ext}.new" "$install_dir/libremedy_core.${lib_ext}"

# A small launcher, not a link: the runtime looks for its core library beside
# its own file, and a link would point it at the wrong folder on macOS.
cat > "$bin_dir/remedy" <<EOF
#!/bin/sh
exec "$install_dir/remedy-runtime" "\$@"
EOF
chmod 755 "$bin_dir/remedy"

say "Installed: $bin_dir/remedy"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) say "Add it to your PATH:  export PATH=\"$bin_dir:\$PATH\"" ;;
esac
say "Start with:  remedy setup"
say "To update later, run this script again."
