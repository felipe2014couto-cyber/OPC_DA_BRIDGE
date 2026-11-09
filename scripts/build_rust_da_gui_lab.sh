#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
toolchain_root="${OPC_RUST_TOOLCHAIN_ROOT:-/tmp/opc-rust-toolchain}"
export RUSTUP_HOME="${toolchain_root}/rustup-home"
export CARGO_HOME="${toolchain_root}/cargo-home"
export PATH="${toolchain_root}/mingw-root/usr/bin:${CARGO_HOME}/bin:${PATH}"
export CARGO_TARGET_I686_WIN7_WINDOWS_GNU_LINKER="${toolchain_root}/mingw-root/usr/bin/i686-w64-mingw32-gcc-posix"
build_root="$(mktemp -d /tmp/opc-da-gui-build.XXXXXX)"
export CARGO_TARGET_DIR="${build_root}/target"
cargo +nightly-2026-11-03 test --locked --offline --manifest-path "${repo_root}/windows/rust_da_project/Cargo.toml"
cargo +nightly-2026-11-03 build --locked --offline --manifest-path "${repo_root}/windows/rust_da_gui/Cargo.toml" --target i686-win7-windows-gnu -Z build-std=std,panic_abort
cargo +nightly-2026-11-03 test --locked --offline --manifest-path "${repo_root}/windows/rust_da_project/Cargo.toml" --target i686-win7-windows-gnu -Z build-std=std,panic_abort --no-run
artifact="${CARGO_TARGET_DIR}/i686-win7-windows-gnu/debug/opc-bridge-config.exe"
echo "GUI artifact: ${artifact}"
file "${artifact}"
sha256sum "${artifact}"
