# Docker Deployment

OpenJarvis ships Docker images for CPU-only and GPU-accelerated deployments, and a Docker Compose stack that runs the API server (with the built web UI) next to an Ollama inference backend. The Compose stack is private by default: both ports are published on `127.0.0.1` only, secrets come from an env file outside the repository, and the server's data lives on named volumes that survive `docker compose down`.

## Quick Start

All commands run from the repository root.

**1. Create the secrets file** outside the repository. The container binds `0.0.0.0` inside its own network, so an **API key is required**: the server refuses to start without one.

```bash
mkdir -p ~/.config/openseneca
install -m 600 deploy/docker/secrets.env.example ~/.config/openseneca/secrets.env
# Generate a key and put it on the OPENJARVIS_API_KEY= line:
python3 -c "import secrets; print('oj_sk_' + secrets.token_urlsafe(32))"
```

Compose loads `~/.config/openseneca/secrets.env` by default and fails if it is missing. Point `OPENSENECA_SECRETS_FILE` at another path to use a different file. Never commit real values; `secrets.env.example` holds placeholders only.

**2. Build and start the stack:**

```bash
docker compose -f deploy/docker/docker-compose.yml up -d --build
```

To run Ollama on an NVIDIA GPU (requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)), add the Ollama GPU override:

```bash
docker compose -f deploy/docker/docker-compose.yml \
  -f deploy/docker/docker-compose.ollama-nvidia.yml up -d --build
```

**3. Pull the model** the server uses (`qwen3.5:4b` unless `OPENSENECA_MODEL` says otherwise). Models persist on the `ollama-models` volume, so this is a one time step:

```bash
docker compose -f deploy/docker/docker-compose.yml exec ollama ollama pull qwen3.5:4b
```

**4. Check it:**

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}

curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $OPENJARVIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "Hello"}]}'
```

Clients must send `Authorization: Bearer <key>` on `/v1/*` and `/api/*` requests. The web UI is served on <http://127.0.0.1:8000/>.

Stop the stack with `docker compose -f deploy/docker/docker-compose.yml down`. Add `-v` only if you want to delete the data and model volumes too.

The stack has two services:

| Service  | Host port         | Description                                     |
|----------|-------------------|-------------------------------------------------|
| `jarvis` | `127.0.0.1:8000`  | OpenJarvis API server and web UI                |
| `ollama` | `127.0.0.1:11434` | Ollama inference engine (no authentication)     |

## Configuration

### Engine and model

The `jarvis` service starts `jarvis serve --engine ollama --model ${OPENSENECA_MODEL:-qwen3.5:4b}`, so the engine and model are always explicit instead of auto-detected from the container's hardware. `OLLAMA_HOST=http://ollama:11434` points the Ollama engine at the `ollama` service.

### Settings

Non-secret settings are Compose variables. Export them, or put them in `deploy/docker/.env` (git-ignored; see `deploy/docker/.env.example`):

| Variable                  | Description                                                   | Default                                   |
|---------------------------|---------------------------------------------------------------|-------------------------------------------|
| `OPENSENECA_MODEL`        | Ollama model the server uses (pull it first)                  | `qwen3.5:4b`                              |
| `OPENSENECA_SECRETS_FILE` | Env file with the secrets below                               | `~/.config/openseneca/secrets.env`        |
| `OPENSENECA_EXTRAS`       | `pyproject.toml` extras baked into the image (comma separated) | `server,channel-discord,speech,voice`     |
| `OPENSENECA_PORT`         | Host port for the server, on `127.0.0.1`                       | `8000`                                    |
| `OLLAMA_PORT`             | Host port for Ollama, on `127.0.0.1`                           | `11434`                                   |

### Secrets

The secrets file is passed to the `jarvis` container as environment variables:

| Variable             | Description                                         |
|----------------------|-----------------------------------------------------|
| `OPENJARVIS_API_KEY` | Required. Bearer token for `/v1/*` and `/api/*`     |
| `DISCORD_BOT_TOKEN`  | Optional. Token for the Discord channel             |
| `DISCORD_ALLOWED_USER_IDS` | Optional. Discord user ids the bot answers (comma separated); the first is the owner |
| `UNSPLASH_ACCESS_KEY` | Optional. Unsplash Access Key for the rotating hero photo (see below) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, ... | Optional. Cloud providers, used only when set |

### Hero photo

The web UI shows one large photo behind the dashboard hero and the empty chat, darkened into the palette. With `UNSPLASH_ACCESS_KEY` set, the server fetches one photo from Unsplash every 6 hours for the theme chosen in Settings, caches the file and its metadata under `<data dir>/hero/` (the `jarvis-data` volume) and serves it at `/v1/hero/image`. The key stays on the server: it is never logged, cached or sent to the browser. The UI credits the photographer and Unsplash with referral links, and the server triggers the photo's download endpoint once per photo, as the Unsplash API guidelines ask. Without a key, or while offline, a few bundled public domain NASA photos rotate on the same clock. Settings > Appearance turns rotation off or changes the theme.

### Image extras

The Compose build passes `EXTRAS=server,channel-discord,speech,voice` to `deploy/docker/Dockerfile`, which adds the Discord channel, faster-whisper speech to text and Kokoro text to speech (with `espeak-ng`). Everything runs on CPU: when an extra pulls in PyTorch, the image installs the CPU wheel of the locked torch version instead of the CUDA wheels. Set `OPENSENECA_EXTRAS=server` for a lean API-only image. A plain `docker build` without `--build-arg EXTRAS=...` also builds the API-only image.

### Volumes

| Volume          | Mounted at                     | Holds                                                                 |
|-----------------|--------------------------------|-----------------------------------------------------------------------|
| `jarvis-data`   | `/home/openjarvis/.openjarvis` | `config.toml`, sessions, memory, traces, telemetry, stored credentials |
| `jarvis-cache`  | `/home/openjarvis/.cache`      | Downloaded speech model weights (faster-whisper, Kokoro)             |
| `ollama-models` | `/root/.ollama`                | Pulled Ollama models                                                 |

The images create both `jarvis` folders owned by the unprivileged `openjarvis` user (uid 10001), so a fresh volume inherits that ownership.

### Custom `config.toml`

The server reads `~/.openjarvis/config.toml`, which is on the `jarvis-data` volume. To manage it from the host, mount a file and point `OPENJARVIS_CONFIG` at it in a Compose override:

```yaml
services:
  jarvis:
    environment:
      - OPENJARVIS_CONFIG=/home/openjarvis/config.toml
    volumes:
      - ./my-config.toml:/home/openjarvis/config.toml:ro
```

The `--engine` and `--model` flags in the `command` take precedence over the engine and model in `config.toml`.

### Private Discord assistant

`docker-compose.discord.yml` turns the stack into a DM only Discord assistant for one person, with voice messages in both directions. It mounts `config.discord.toml` (channel enabled, `dm_only = true`, `voice_replies = "mirror"`) caps the `jarvis` container at 4 CPUs (`OPENSENECA_JARVIS_CPUS`), since Kokoro and faster-whisper run there on CPU, and keeps the chat model loaded in Ollama for 30 minutes between messages (`OPENSENECA_OLLAMA_KEEP_ALIVE`) so a reply after a pause does not wait for a reload.

1. Create the bot in the [Discord Developer Portal](https://discord.com/developers/applications): turn **Public Bot** off; no privileged gateway intents are needed.
2. Make a private server, invite the bot with the `bot` scope and the Send Messages, Attach Files, Read Message History and Send Voice Messages permissions. A bot can only DM users it shares a server with.
3. Enable Developer Mode in Discord, copy your user id, and add both values to the secrets file:

    ```bash
    DISCORD_BOT_TOKEN=...
    DISCORD_ALLOWED_USER_IDS=123456789012345678
    ```

4. Start the stack with the override:

    ```bash
    docker compose -f deploy/docker/docker-compose.yml \
      -f deploy/docker/docker-compose.discord.yml up -d --build
    ```

The first voice message downloads the faster-whisper `small` and Kokoro weights into the `jarvis-cache` volume. See the [channels guide](../user-guide/channels.md#private-discord-assistant) for the behaviour.

## GPU Overrides

| Override file                      | Effect                                                                                     |
|------------------------------------|--------------------------------------------------------------------------------------------|
| `docker-compose.ollama-nvidia.yml` | Gives Ollama the NVIDIA GPU; the server keeps the CPU image with the Discord and speech extras |
| `docker-compose.gpu.nvidia.yml`    | Also rebuilds `jarvis` from `Dockerfile.gpu` (CUDA) for GPU engines inside the server container |
| `docker-compose.gpu.rocm.yml`      | Rebuilds `jarvis` from `Dockerfile.gpu.rocm` (AMD ROCm)                                     |

`Dockerfile.gpu` and `Dockerfile.gpu.rocm` install the `server` extra only.

## Docker Images

All images are multi-stage builds: a Node stage builds the web UI, a builder stage installs the locked dependencies (`uv export --frozen`) and compiles the Rust extension (Rust 1.89, matching `rust/rust-toolchain.toml`), and the runtime stage runs as the unprivileged `openjarvis` user. Base images are pinned by digest. The repository's `.dockerignore` keeps virtualenvs, `node_modules`, `rust/target`, local data and secrets out of the build context.

| Dockerfile           | Base                     | Use                                      |
|----------------------|--------------------------|------------------------------------------|
| `Dockerfile`         | `python:3.12-slim`       | CPU server (default for Compose)         |
| `Dockerfile.gpu`     | `nvidia/cuda:12.4` runtime | GPU engines inside the server container |
| `Dockerfile.gpu.rocm`| `rocm/dev-ubuntu-22.04`  | AMD GPUs                                 |
| `Dockerfile.sandbox` | `python:3.12-slim` + Node | Sandboxed agent execution               |

Build and run the CPU image by hand:

```bash
docker build -f deploy/docker/Dockerfile \
  --build-arg EXTRAS=server,channel-discord -t openjarvis:latest .
docker run -d -p 127.0.0.1:8000:8000 \
  --env-file ~/.config/openseneca/secrets.env \
  -v openjarvis-data:/home/openjarvis/.openjarvis \
  openjarvis:latest serve --host 0.0.0.0 --port 8000 --engine ollama --model qwen3.5:4b
```

The entrypoint is `jarvis`, so the command is a `jarvis` subcommand.

### Available CLI Options for `jarvis serve`

| Option               | Description                                         |
|----------------------|-----------------------------------------------------|
| `--host`             | Bind address (default: from config)                 |
| `--port`             | Port number (default: from config)                  |
| `-e` / `--engine`    | Engine backend (`ollama`, `vllm`, `llamacpp`, `sglang`, `cloud`) |
| `-m` / `--model`     | Default model name                                  |
| `-a` / `--agent`     | Agent for chat requests (`simple`, `orchestrator`, `react`, `openhands`) |

## Health Check

`GET /health` checks whether the inference engine is responsive. A healthy server returns HTTP 200 with `{"status": "ok"}`; an unhealthy engine returns HTTP 503 with `{"detail": "Engine unhealthy"}`.
