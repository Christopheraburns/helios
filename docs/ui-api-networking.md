# Helios UI-to-API networking

## Supported path

The supported deployment path uses the stable HTTPS URLs assigned to the two
Cloudera AI Applications:

```text
Browser
  -> https://helios-ui.<workbench-domain>
       loads the React application and runtime config
  -> https://helios-api.<workbench-domain>
       browser sends credentialed API requests directly
```

The second arrow is initiated by JavaScript loaded from the UI Application. It
is not a server-to-server proxy and does not use either workload's container
IP. Cloudera workload/container addresses are transient and must not be saved
as configuration.

Cloudera documents that Applications are exposed through their configured
subdomains and are authenticated by default. It also documents transparent
authentication using the injected `REMOTE-USER` and `REMOTE-USER-PERM`
headers. Cloudera AI on premises 1.5.5 SP2 CHF1 explicitly describes
cross-origin inter-Application communication and unauthenticated `OPTIONS`
preflight handling. References:

- [Securing Applications](https://docs.cloudera.com/machine-learning/cloud/applications/topics/ml-securing-applications.html)
- [Application limitations](https://docs.cloudera.com/machine-learning/cloud/applications/topics/ml-applications-limitations.html)
- [1.5.5 SP2 CHF1 fixed issues](https://docs.cloudera.com/machine-learning/1.5.5/release-notes-privatecloud/topics/ml-fixed-issues-cloudera-ai-1-5-5-sp2-chf1.html)

The global Cloudera AI CORS switch is not used: Cloudera documents that it
emits `Access-Control-Allow-Origin: *`, which is inappropriate for
credentialed requests. Helios instead returns CORS headers for only explicitly
configured UI origins.

## Authentication and authorization

Both Applications must remain authenticated. Do not select **Enable
Unauthenticated Access** for either Application.

The frontend uses `fetch` with `credentials: "include"`. It does not send an
identity header or application service credential. The Cloudera authentication
gateway authenticates the browser request to the API Application and injects
`REMOTE-USER`. The API translates that value to the existing
`cloudera-workbench:<username>` Helios principal and loads that principal's
resource grants.

The API still accepts the legacy `x-forwarded-user` header for compatibility
with existing deployments and tests, but `REMOTE-USER` takes precedence.
`HELIOS_DEV_USER` is an explicit local-development fallback that is active
only when `HELIOS_DEV=1`; neither variable should be set on the deployed API
Application.

An authenticated Cloudera project user with no Helios grants receives an empty
authorized organization collection. A request without transparent identity
receives HTTP 401. The UI does not infer permissions or replace either result
with the UI Application's workload identity.

## Configuration

Configure the UI Application:

```text
HELIOS_API_URL=https://helios-api.<workbench-domain>
```

`apps/ui/app.py` exposes this value to the built frontend through a generated,
non-cached `/config.js`. This is runtime configuration, so the API URL is not
compiled into the production bundle.

Configure the API Application:

```text
HELIOS_UI_ORIGINS=https://helios-ui.<workbench-domain>
```

Multiple exact origins can be comma-separated. Wildcard origins are rejected.
The configured value must be an origin, without a path. TLS verification stays
enabled and both URLs must use the Cloudera-provided HTTPS endpoints.

Create the Applications with distinct stable subdomains, for example
`helios-ui` and `helios-api`. Both processes continue to bind internally to
`127.0.0.1:$CDSW_APP_PORT`; the public subdomain URLs are the cross-Application
contract.

## Local development

Run the API with an explicit local UI origin and development principal:

```bash
HELIOS_UI_ORIGINS=http://localhost:5173 \
HELIOS_DEV=1 \
HELIOS_DEV_USER=<principal-subject> \
uvicorn apps.console.main:app --host 127.0.0.1 --port 8000
```

The selected subject must have grants in the configured Helios metadata
database. Do not use `HELIOS_DEV_USER` in Cloudera AI.

Create `apps/ui/.env.local`:

```text
VITE_HELIOS_API_URL=http://127.0.0.1:8000
```

Then run `npm run dev` in `apps/ui`. The hostname is local configuration, not
application source.

## Diagnostic endpoints and page

- `GET /api/v1/healthz` returns `{"status":"ok"}` when the API process is
  ready.
- `GET /api/v1/diagnostics` requires transparent identity. It returns the
  principal identity and distinct accessible organization count, including
  zero for an authenticated principal without grants.

The initial UI calls both endpoints and displays Connecting, Connected,
Authentication failure, Authorization failure, or API unavailable.

## Deployment verification gate

Repository tests verify exact-origin credentialed CORS, transparent
`REMOTE-USER` handling, response classification, and both diagnostic
endpoints. Local smoke tests verify the browser request path.

The final Cloudera-specific check must be performed on the target Workbench
release because this repository has no access to its authentication gateway:

1. Open the authenticated UI Application URL.
2. Confirm `/config.js` contains the API Application's HTTPS URL.
3. Confirm the diagnostic page reaches Connected and shows the signed-in
   Cloudera username.
4. Inspect the API response and confirm
   `Access-Control-Allow-Origin` exactly equals the UI origin and
   `Access-Control-Allow-Credentials` is `true`.

If the target release blocks cross-origin Application requests, does not pass
the browser's transparent identity to the API Application, or requires
wildcard CORS, stop deployment. Do not substitute a workload token, UI service
identity, disabled authentication, disabled TLS verification, or a transient
container address.
