#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
toolchain_root="${OPC_RUST_TOOLCHAIN_ROOT:-/tmp/opc-rust-toolchain}"

export RUSTUP_HOME="${RUSTUP_HOME:-${toolchain_root}/rustup-home}"
export CARGO_HOME="${CARGO_HOME:-${toolchain_root}/cargo-home}"
export PATH="${CARGO_HOME}/bin:${PATH}"

if [[ ! -x "${CARGO_HOME}/bin/cargo" ]]; then
  echo "Isolated Cargo not found under ${CARGO_HOME}; see docs/RUST_DA_POC_FEASIBILITY.md" >&2
  exit 2
fi

cargo +nightly-2026-11-03 test --offline \
  --manifest-path "${repo_root}/windows/rust_da_model/Cargo.toml"

if [[ -x "${toolchain_root}/mingw-root/usr/bin/i686-w64-mingw32-gcc-posix" ]]; then
  export CARGO_TARGET_I686_WIN7_WINDOWS_GNU_LINKER="${toolchain_root}/mingw-root/usr/bin/i686-w64-mingw32-gcc-posix"
  cargo +nightly-2026-11-03 build --offline \
    --manifest-path "${repo_root}/windows/rust_da_model/Cargo.toml" \
    --target i686-win7-windows-gnu \
    -Z build-std=std,panic_abort
  cargo +nightly-2026-11-03 test --offline \
    --manifest-path "${repo_root}/windows/rust_da_model/Cargo.toml" \
    --target i686-win7-windows-gnu \
    -Z build-std=std,panic_abort --no-run
fi
