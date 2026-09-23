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
The initial shell does not call the Helios API yet.

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

`npm run preview` is available for checking a production build locally. It is
not the Cloudera AI production server.

## Cloudera AI Application

Create a separate Cloudera AI Application with:

- **Script:** `helios/apps/ui/app.py` relative to the Workbench project root
- **Subdomain:** for example, `helios-ui`
- **Runtime:** a standard PBJ Workbench Python 3.12 runtime
- **Environment:** set `HELIOS_ROOT` only if the checkout is not at
  `$CDSW_PROJECT_DIR/helios`

Build the UI before starting the Application and ensure `apps/ui/dist` exists
in the project workspace. The Application entry point uses only Python's
standard library, so Node.js and a custom Cloudera runtime are not required to
serve the production build.

The server binds to `127.0.0.1:$CDSW_APP_PORT`, which is the address and port
used by the Cloudera AI Application proxy. It serves the Vite build, supports
single-page-application route fallback, and exposes `/healthz`.
