# Taskroom — free local frontend

No paid hosting, subscription, model API or OpenAI credential is required. The UI
source is in this public repository on the Taskroom pull request branch. Clone
that branch to run the UI locally. There is no public hosted application.

## Start

Requires Node.js 24 and pnpm 11.25.0:

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Open http://127.0.0.1:3000. Sample preview works without Docker or credentials.
Its three tasks, reports and accounting values are clearly labelled examples,
not backend execution. Actual provider spend is zero. For a production build
locally, run `pnpm build && pnpm start`. Both servers bind only loopback.

## Optional Local API

Start the existing backend on 127.0.0.1:8000 following the root README. This Mac
uses `DOCKER_CONTEXT=colima-ai-task-platform docker-compose` (standalone Compose).
An existing dedicated OWNER API key may be configured in the ignored
`frontend/.env.local` as `ATP_API_KEY=...`. Never use an administrator/OpenAI key,
commit that file, or paste the key in the browser. Set permissions to 0600 and
restart Next.js after changing configuration. Provisioning new access is a
separate operator action requiring approval; starting the UI creates no identity.

Local API reads the owner's first 50 tasks, selected snapshot, and first 50 steps.
It polls every five seconds while visible. SSE and further pagination are not
implemented in V1. Only simulated analysis:1 creation is allowed, enforced by the
server gateway before upstream HTTP; paid workflow versions are rejected.
Admission must already be open. The UI cannot enable dispatch or start a worker.
Queued work remains queued without a separately configured compatible test-profile
worker. For actual simulated execution use the existing isolated `make test-e2e`;
this UI is not a new synthetic provider deployment or a real-model qualification.

Cancellation uses the observed task version. Ambiguous commands retain their
exact body/key in this tab's session storage, including input text. Keep the tab
until resolved: closing it or clearing its storage loses that retained identity.
Only explicit Retry resends; no automatic regeneration occurs. Definitive 4xx
rejections, except ambiguous 408, release the form for a fresh command. Confirmed,
estimated and uncertain accounting are separate. Missing usage reads Unknown.

The gateway uses a fixed loopback destination, route/method allowlists, exact-origin
writes, size/time bounds and no redirects/automatic retries. No administrator route
is exposed. This is a single-owner local app, not public multi-user authentication.

## Verify

```sh
pnpm test
pnpm typecheck
pnpm lint
pnpm exec prettier --check app lib tests '*.json' '*.mjs' '*.ts'
pnpm build
```

See ../docs/frontend-plan.md and ../reviews/frontend-verification.md for evidence,
review findings and limits. Never point destructive backend integration tests at
your development database. No OpenAI smoke or paid deployment is part of delivery.
