#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
toolchain_root="${OPC_RUST_TOOLCHAIN_ROOT:-/tmp/opc-rust-toolchain}"
binding_checkout="${OPC_RUST_BINDINGS_CHECKOUT:-/tmp/opc-rust-audit-da44a992}"
expected_revision="da44a99265f9706618bbe506c98f714f2aa46f0b"

if [[ ! -x "${toolchain_root}/cargo-home/bin/cargo" ]]; then
  echo "Isolated Cargo missing at ${toolchain_root}/cargo-home/bin/cargo" >&2
  exit 2
fi
if [[ "$(git -C "${binding_checkout}" rev-parse HEAD)" != "${expected_revision}" ]]; then
  echo "Bindings checkout must be exactly ${expected_revision}" >&2
  exit 2
fi

build_root="$(mktemp -d "${OPC_RUST_BUILD_PARENT:-/tmp}/opc-da-com-lab-build.XXXXXX")"
patched_workspace="${build_root}/opc-rust-browse-patched"
export RUSTUP_HOME="${toolchain_root}/rustup-home"
export CARGO_HOME="${toolchain_root}/cargo-home"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${build_root}/target}"
export CARGO_TARGET_I686_WIN7_WINDOWS_GNU_LINKER="${toolchain_root}/mingw-root/usr/bin/i686-w64-mingw32-gcc-posix"
export PATH="${toolchain_root}/mingw-root/usr/bin:${CARGO_HOME}/bin:${PATH}"

python3 "${repo_root}/scripts/patch_rust_opc_da_browse.py" \
  "${binding_checkout}" "${patched_workspace}"

python3 - "${repo_root}" "${patched_workspace}" "${build_root}/Cargo.toml" <<'PY'
from pathlib import Path
import os
import shutil
import sys

repo, upstream, destination = map(Path, sys.argv[1:])
manifest = (repo / "windows/rust_da_com_lab/Cargo.toml").read_text()
mapping = {
    'opc_da_bindings = { git = "https://github.com/Ronbb/rust_opc", rev = "da44a99265f9706618bbe506c98f714f2aa46f0b" }': f'opc_da_bindings = {{ path = "{upstream / "opc_da_bindings"}" }}',
    'opc_classic_types = { git = "https://github.com/Ronbb/rust_opc", rev = "da44a99265f9706618bbe506c98f714f2aa46f0b" }': f'opc_classic_types = {{ path = "{upstream / "opc_classic_types"}" }}',
    'opc_classic_utils = { git = "https://github.com/Ronbb/rust_opc", rev = "da44a99265f9706618bbe506c98f714f2aa46f0b" }': f'opc_classic_utils = {{ path = "{upstream / "opc_classic_utils"}" }}',
    'opc-da-lab-model = { path = "../rust_da_model" }': f'opc-da-lab-model = {{ path = "{repo / "windows/rust_da_model"}" }}',
    'opc-da-project = { path = "../rust_da_project" }': f'opc-da-project = {{ path = "{repo / "windows/rust_da_project"}" }}',
    'path = "src/main.rs"': f'path = "{repo / "windows/rust_da_com_lab/src/main.rs"}"',
}
for old, new in mapping.items():
    if old not in manifest:
        raise SystemExit(f"manifest entry not found: {old}")
    manifest = manifest.replace(old, new)

registry_roots = sorted((Path(os.environ["CARGO_HOME"]) / "registry/src").glob("*"))
def registry_source(crate: str) -> Path:
    matches = [root / crate for root in registry_roots if (root / crate / "Cargo.toml").is_file()]
    if len(matches) != 1:
        raise SystemExit(f"expected one cached source for {crate}; found {len(matches)}")
    return matches[0]

patched = {}
for crate in ("windows-0.62.2", "windows-core-0.62.2"):
    source = registry_source(crate)
    destination_tree = destination.parent / crate
    shutil.copytree(source, destination_tree)
    patched[crate] = destination_tree

def replace_one(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one patch point in {path}; found {count}: {old}")
    path.write_text(text.replace(old, new))

windows_com = patched["windows-0.62.2"] / "src/Windows/Win32/System/Com/mod.rs"
replace_one(windows_com,
    'windows_core::link!("combase.dll" "system" fn CoTaskMemAlloc',
    'windows_core::link!("ole32.dll" "system" fn CoTaskMemAlloc')
replace_one(windows_com,
    'windows_core::link!("combase.dll" "system" fn CoTaskMemFree',
    'windows_core::link!("ole32.dll" "system" fn CoTaskMemFree')

core_bindings = patched["windows-core-0.62.2"] / "src/imp/bindings.rs"
replace_one(core_bindings,
    'windows_link::link!("combase.dll" "system" fn CoTaskMemAlloc',
    'windows_link::link!("ole32.dll" "system" fn CoTaskMemAlloc')
replace_one(core_bindings,
    'windows_link::link!("combase.dll" "system" fn CoTaskMemFree',
    'windows_link::link!("ole32.dll" "system" fn CoTaskMemFree')
marshaler = patched["windows-core-0.62.2"] / "src/imp/marshaler.rs"
replace_one(marshaler,
    'windows_link::link!("combase.dll" "system" fn CoCreateFreeThreadedMarshaler',
    'windows_link::link!("ole32.dll" "system" fn CoCreateFreeThreadedMarshaler')

manifest += (
    "\n[patch.crates-io]\n"
    f'windows = {{ path = "{patched["windows-0.62.2"]}" }}\n'
    f'windows-core = {{ path = "{patched["windows-core-0.62.2"]}" }}\n'
)
destination.write_text(manifest)
PY

export RUSTFLAGS="${RUSTFLAGS:+${RUSTFLAGS} }--cfg=windows_slim_errors"
cargo +nightly-2026-11-03 build --offline \
  --manifest-path "${build_root}/Cargo.toml" \
  --target i686-win7-windows-gnu \
  -Z build-std=std,panic_abort

artifact="${CARGO_TARGET_DIR}/i686-win7-windows-gnu/debug/rust-da-com-lab.exe"
echo "Artifact: ${artifact}"
file "${artifact}"
sha256sum "${artifact}"
