# Helios UI

Minimal React, TypeScript, and Vite application shell for the standalone
Helios UI Application.

## Local development

Node.js 20 or newer is required for frontend development.

```bash
cd apps/ui
npm install
npm run dev
```

Vite prints the local development URL, normally `http://localhost:5173`.
Copy `.env.example` to `.env.local` and set `VITE_HELIOS_API_URL` to the
locally running API origin. The API must allow the Vite origin through
`HELIOS_UI_ORIGINS`.

## Production build

```bash
cd apps/ui
npm install
npm run typecheck
npm run build
```

Vite writes the static production application to `apps/ui/dist`. Build output
is intentionally ignored by git and should be produced by the deployment
pipeline or copied into the Cloudera AI project workspace as a build artifact.
Each build displays a UTC timestamp-based build number beneath the Helios
wordmark. CI can supply its own identifier:

```bash
HELIOS_UI_BUILD_NUMBER="$CI_BUILD_NUMBER" npm run build
```

`npm run preview` is available for checking a production build locally. It is
not the Cloudera AI production server.

## Cloudera AI Application

Create a separate Cloudera AI Application with:

- **Script:** `helios/apps/ui/app.py` relative to the Workbench project root
- **Subdomain:** for example, `helios-ui`
- **Runtime:** a standard PBJ Workbench Python 3.12 runtime
- **Environment:** set `HELIOS_ROOT` only if the checkout is not at
  `$CDSW_PROJECT_DIR/helios`; set `HELIOS_API_URL` to the API Application's
  stable HTTPS URL

Build the UI before starting the Application and ensure `apps/ui/dist` exists
in the project workspace. The Application entry point uses only Python's
standard library, so Node.js and a custom Cloudera runtime are not required to
serve the production build.

The server binds to `127.0.0.1:$CDSW_APP_PORT`, which is the address and port
used by the Cloudera AI Application proxy. It serves the Vite build, supports
single-page-application route fallback, and exposes `/healthz`.

See `docs/ui-api-networking.md` for API CORS, transparent authentication, and
Cloudera AI configuration.

## Workspace context links

The selected workspace is represented with `organization` and `model` query
parameters. Primary navigation preserves both values, so routes such as
`/canvas?organization=<id>&model=<id>` can be shared or reloaded. Values are
accepted only when they are present in the authenticated API collections;
inaccessible or stale values are removed or replaced with an authorized
selection.
