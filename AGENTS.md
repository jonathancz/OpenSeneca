# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- **Naming.** The product shown in the UI is "Seneca". Package names, the Python module `openjarvis`, import paths, env vars (`OPENJARVIS_*`), storage keys (`openjarvis-*`) and the backend keep the OpenJarvis names; do not rename them.
- **Frontend look.** Dark only, no theme setting. Design tokens and the shared `.sn-*` utilities (cards, figures, pills, keycaps, glass, the single hero notch) live in `frontend/src/index.css`; small React helpers in `frontend/src/components/ui/seneca.tsx`. Components read colours through `var(--color-*)`, so change tokens before touching components. One accent (ember orange) for things spending compute right now; sentence case everywhere.
- **Hero photo.** `src/openjarvis/server/hero_images.py` fetches an Unsplash photo every 6 hours with `UNSPLASH_ACCESS_KEY` (server side only), caches it under `<data dir>/hero/`, and falls back to bundled NASA photos in `hero_fallback/`. Tests mock HTTP with `httpx.MockTransport`; `tests/conftest.py` unsets the key so no test can reach the live API.
- **Backend tests need the native extension.** `openjarvis_rust` is built with maturin and Rust 1.89 (`make build`). On a host without Rust, a matching build can be copied out of a locally built Docker image (`deploy/docker`) into a venv with the same Python minor version.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
