# Third-party software

## Softing OPC Classic SDK

- Official repository: <https://github.com/SoftingIndustrial/OPC-Classic-SDK>
- Pinned commit: `08f4b3e968acfce4b775299741cef1819e24fda1`
- License: MIT (retain the upstream `LICENSE` and all copyright/license notices with any redistributed source or binary).
- Purpose: reference and SDK base for the isolated Windows OPC DA Mirror server.
- Retrieval: run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/fetch_softing_sdk.ps1` from a Windows checkout. The script clones only the official HTTPS URL into `.vendor/softing-opc-classic-sdk/`, checks out the immutable commit detached, verifies `HEAD`, and refuses to change an existing checkout at a different revision. `.vendor/` is ignored by Git.

The project is discontinued and has no official support. Retrieval does not install, build, patch, register or execute the SDK. No SDK files are tracked in this project.

Updates require a deliberate review: confirm the official repository and license, inspect the proposed commit and its server examples, update the pinned SHA and this document together, and review the resulting dependency diff. Never edit vendor files in place. Any necessary fix belongs in a minimal, separately documented patch under `patches/`, with rationale and reproducible application instructions. Preserve upstream license and attribution notices.
