#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
toolchain_root="${OPC_RUST_TOOLCHAIN_ROOT:-${HOME}/.cache/opc-rust-toolchain}"
build_parent="${OPC_RUST_BUILD_PARENT:-${TMPDIR:-/tmp}}"
build_root="$(mktemp -d "${build_parent%/}/opc-telemetry-build.XXXXXX")"
export RUSTUP_HOME="${toolchain_root}/rustup-home"
export CARGO_HOME="${toolchain_root}/cargo-home"
export CARGO_TARGET_DIR="${build_root}/target"
export CARGO_TARGET_I686_WIN7_WINDOWS_GNU_LINKER="${toolchain_root}/mingw-root/usr/bin/i686-w64-mingw32-gcc-posix"
export PATH="${toolchain_root}/mingw-root/usr/bin:${CARGO_HOME}/bin:${PATH}"
if [[ ! -x "${CARGO_HOME}/bin/cargo" || ! -x "${CARGO_TARGET_I686_WIN7_WINDOWS_GNU_LINKER}" ]]; then
  echo "Isolated Rust/MinGW toolchain missing; see docs/RUST_DA_POC_FEASIBILITY.md" >&2
  exit 2
fi
cargo +nightly-2026-11-03 build --offline --locked \
  --manifest-path "${repo_root}/windows/rust_telemetry_agent/Cargo.toml" \
  --target i686-win7-windows-gnu -Z build-std=std,panic_abort
artifact="${CARGO_TARGET_DIR}/i686-win7-windows-gnu/debug/opc-telemetry-agent.exe"
file "${artifact}"
sha256sum "${artifact}"
"${toolchain_root}/mingw-root/usr/bin/i686-w64-mingw32-objdump" -p "${artifact}" | sed -n '/DLL Name:/p'
echo "Artifact: ${artifact}"
