# Claude Managed Agents on Fly.io and Sprites

A reference control plane that runs [Claude self-hosted sandbox](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes) sessions as [Sprites](https://sprites.dev/).

Sprites are full computers for your agents. Unlike ephemeral sandboxes, Sprites are persistent, and sessions run in a Sprite stick around for as long as you need them. Connect with `sprite console` and pick up right where Claude left off.

In addition to persistence, Sprites offer:

- High speed, directly-attached NVMe storage
- Filesystem checkpoints and restores
- Fine-grained network egress controls
- Automatic web URLs to preview your creations
- Proxied API connectors to protect sensitive credentials
- Only pay for the CPU, memory, and storage you actually use

## Quick start

> [!NOTE]
> The [Sprites docs](https://docs.sprites.dev/integrations/claude-managed-agents/) contain a more detailed setup guide.

This project has two components. The [`dispatcher`](./dispatch) is a [Fly App](https://fly.io/docs/apps/overview/) which manages sessions and hands out work. The [`worker`](./worker) is a Sprite that receives tool calls from Claude and executes them in its sandbox. Every Claude session has a unique `worker` Sprite which can be cleaned up or archived to revisit later.

You'll need:

- [`flyctl`](https://fly.io/docs/flyctl/install/) installed (and optionally [`sprite`](https://docs.sprites.dev/quickstart/))
- Access to the [Claude Platform Console](https://platform.claude.com)

### Preparing an environment

Fork and/or clone the repository. Configure your Fly App with a unique name:

```sh
fly launch --no-deploy
```

> [!TIP]
> The [Agent Quickstart](https://platform.claude.com/workspaces/default/agent-quickstart) panel in the Claude Console walks you through setting up an agent and environment. Make sure the environment type is "self-hosted" and you've created a key.

In the Claude Console, open the [Agents](https://platform.claude.com/workspaces/default/agents) panel and create a new agent. If you already have one, you can skip this step. Next, open the [Environments](https://platform.claude.com/workspaces/default/environments) panel and create a new environment. For its hosting type, select "Self-hosted". From the environment's page, create a new environment key. Finally, open the [Webhooks](https://platform.claude.com/settings/workspaces/default/webhooks) panel and create a webhook. The endpoint URL will be `https://<your app name>.fly.dev` and the webhook must be subscribed to `session.status_run_started`.

In the [Sprites dashboard](https://sprites.dev), open the "Access Tokens" panel and create a new token. If you use your organization for other things, click on the gear icon and set the restricted prefix to `claude-agent-` before creating the token. This prevents the dispatcher from accessing any other Sprite.

### Deploying

Configure your app's secrets and deploy:

```sh
fly secrets set \
    ANTHROPIC_ENVIRONMENT_ID=env_01... \
    ANTHROPIC_ENVIRONMENT_KEY=sk-ant-oat01-... \
    ANTHROPIC_WEBHOOK_SECRET=whsec_... \
    SPRITE_TOKEN=...
fly deploy
```

Fly.io builds the Docker image and creates two dispatcher Machines. Both Machines will automatically stop when not in use.

In the Claude Console, create a new [session](https://platform.claude.com/workspaces/default/sessions) with the agent and environment you generated earlier. When the agent attempts to run its first command, the dispatcher will create a Sprite and hand it off to the worker. See your new Sprite in the dashboard, or with `sprite list`.

## Development

To build this project locally, you'll need `uv` and `docker`. The dispatcher is a `FastAPI` server which interacts with Sprites through the [Python SDK](https://github.com/superfly/sprites-py). Set up the [worker closure](#generating-a-worker-closure) and export `VENDOR_TAR_PATH`, then run the developent server:

```sh
export ANTHROPIC_ENVIRONMENT_ID=... ANTHROPIC_ENVIRONMENT_KEY=... \
    ANTHROPIC_WEBHOOK_SECRET=... SPRITE_TOKEN=...
uv run --directory dispatch fastapi dev --port 8080
```

Expose the port publicly (e.g. `ngrok`, `cloudflared tunnel`, running inside a Sprite) and register that as a new webhook for testing.

Run the CI checks with:

```sh
uv run ruff check && uv run ruff format --check
uv run pyrefly check
uv run pytest
```

### Generating a worker closure

The worker closure is the bundle sent to the Sprite. Normally this is built during the Docker build. To build it locally:

```sh
UV_PROJECT_ENVIRONMENT=/tmp/worker-venv uv sync --locked --no-dev \
    --no-editable --package sprites-claude-managed-agents-worker
tar -czf vendor.tar.gz -C /tmp/worker-venv/lib/python3.*/site-packages .
export VENDOR_TAR_PATH=$PWD/vendor.tar.gz
```

## License

Copyright 2026 Fly.io, Inc. Licensed under the Apache License, Version 2.0. See the [LICENSE](./LICENSE) file.
