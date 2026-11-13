# Base images are pinned to an immutable digest (in addition to a human-readable
# tag) so every build resolves the exact same layers — reproducible builds and
# safe rollbacks (#563).

# Stage 1: Build frontend SPA
FROM node:22.23.0-slim@sha256:d9f850096136edbc402debdd8729579a288aac64574ada0ff4db26b6ae58b0b2 AS frontend
# Public Supabase anon key for the savings leaderboard; empty by default so
# the image's leaderboard stays disabled (#589). Pass --build-arg to enable.
ARG OPENJARVIS_LEADERBOARD_PUBLIC_ANON=

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY frontend/ .
RUN VITE_SUPABASE_ANON_KEY="${OPENJARVIS_LEADERBOARD_PUBLIC_ANON}" npm run build

# Stage 2: Build Python package
FROM python:3.12.13-slim-bookworm@sha256:76d4b7b6305788c6b4c6a19d6a22a3921bf802e9af4d5e1e5bd771208dba74bf AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.cargo/bin:${PATH}"

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
        sh -s -- -y --profile minimal --default-toolchain none && \
    rustup toolchain install 1.89 --profile minimal && \
    rustup default 1.89

WORKDIR /app

# Install dependencies from the committed lockfile (#567). `uv export --frozen`
# reads uv.lock as-is (no re-resolution) and emits a fully pinned, hash-verified
# requirements set; `--no-deps` then installs exactly that set. This is a
# separate layer from the source copy so dependency installs stay cached when
# only application code changes.
#
# EXTRAS is a comma separated list of pyproject optional-dependency groups. The
# default keeps the image lean (API server only); docker-compose.yml passes
# `server,channel-discord,speech,voice` for the local stack. If an extra pulls
# in PyTorch (for example `voice`, for Kokoro TTS), the CUDA wheels from the
# lockfile are dropped and the same torch version is installed from the
# PyTorch CPU index instead, which saves several GB in this CPU-only image.
ARG EXTRAS=server
COPY pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir uv && \
    extra_flags=$(echo "${EXTRAS}" | tr ',' '\n' | sed '/^$/d; s/^/--extra /' | tr '\n' ' ') && \
    uv export --frozen --no-dev ${extra_flags} --no-emit-project \
        --no-hashes --no-annotate --no-header > /tmp/all-requirements.txt && \
    torch_version=$(sed -n 's/^torch==\([^ ;]*\).*/\1/p' /tmp/all-requirements.txt) && \
    gpu_packages=$(grep -E '^(torch|triton|nvidia-|cuda-)' /tmp/all-requirements.txt \
        | grep -v '^nvidia-ml-py' | cut -d'=' -f1 | cut -d' ' -f1 | cut -d'[' -f1 \
        | sed 's/^/--no-emit-package /' | tr '\n' ' ') && \
    uv export --frozen --no-dev ${extra_flags} --no-emit-project ${gpu_packages} \
        > requirements.txt && \
    uv pip install --system --no-deps -r requirements.txt && \
    if [ -n "${torch_version}" ]; then \
        uv pip install --system --no-deps \
            --index-url https://download.pytorch.org/whl/cpu "torch==${torch_version}+cpu"; \
    fi && \
    rm -f /tmp/all-requirements.txt && \
    uv pip install --system --no-deps "maturin>=1.12.6,<2"

# Kokoro's English voices use spaCy's en_core_web_sm through misaki. Without it
# misaki tries to pip install the model at runtime into the (non-persistent)
# user site of the unprivileged user, so bake the version matching the locked
# spaCy 3.8 into the image.
ARG SPACY_EN_MODEL_URL=https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
RUN case ",${EXTRAS}," in \
        *,voice,*) uv pip install --system --no-deps "${SPACY_EN_MODEL_URL}" && \
            python3 -c "import spacy; spacy.load('en_core_web_sm')" ;; \
    esac

# Copy the source and the non-src force-include paths (see pyproject
# [tool.hatch.build.targets.wheel.force-include]) before building the project.
COPY src/ src/
COPY rust/ rust/
COPY scripts/install scripts/install
COPY deploy/windows deploy/windows

# Copy built frontend into the server static directory
COPY --from=frontend /src/openjarvis/server/static src/openjarvis/server/static/

# Install the project itself without re-resolving dependencies.
RUN uv pip install --system --no-deps . && \
    maturin build --release \
        --manifest-path rust/crates/openjarvis-python/Cargo.toml \
        --interpreter python3 \
        --out /tmp/openjarvis-rust-wheel && \
    uv pip install --system --no-deps /tmp/openjarvis-rust-wheel/*.whl && \
    python3 -c "import openjarvis_rust; print('openjarvis_rust ok')" && \
    python3 -m pip uninstall -y maturin && \
    rm -rf /tmp/openjarvis-rust-wheel rust

# Stage 3: Runtime
FROM python:3.12.13-slim-bookworm@sha256:76d4b7b6305788c6b4c6a19d6a22a3921bf802e9af4d5e1e5bd771208dba74bf

# Kokoro TTS (the `voice` extra) needs espeak-ng for out-of-dictionary words.
ARG EXTRAS=server
RUN case ",${EXTRAS}," in \
        *,voice,*) apt-get update && \
            apt-get install -y --no-install-recommends espeak-ng && \
            rm -rf /var/lib/apt/lists/* ;; \
    esac

COPY --from=builder /usr/local /usr/local
COPY --from=builder /app /app
WORKDIR /app

# Run as an unprivileged user — the server needs no root privileges, so dropping
# them limits the blast radius of a compromise (#565). The app writes only to
# $HOME (config/cache/state), which is owned by this user. The data and cache
# folders are created here, owned by that user, so a fresh named volume mounted
# on them inherits the ownership; otherwise Docker creates the mount point as
# root and the server fails with PermissionError on startup.
RUN groupadd --system --gid 10001 openjarvis && \
    useradd --system --uid 10001 --gid openjarvis \
        --create-home --home-dir /home/openjarvis openjarvis && \
    install -d -m 700 -o openjarvis -g openjarvis \
        /home/openjarvis/.openjarvis /home/openjarvis/.cache
ENV HOME=/home/openjarvis
USER openjarvis

EXPOSE 8000

# Keep `jarvis` as the image entrypoint so Docker and Compose command overrides
# remain subcommands (for example, `docker run IMAGE serve ...`). Platforms
# such as Render replace CMD while preserving this entrypoint.
ENTRYPOINT ["jarvis"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
