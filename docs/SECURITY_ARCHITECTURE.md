# Identity and authorization boundaries

Helios treats authentication, Helios resource authorization, and underlying data
authorization as three separate decisions.

## 1. Authentication: who is this principal?

Authentication adapters live at each application boundary. They validate the
credential appropriate to that workload and construct a
`helios_core.identity.Principal`.

- The Web UI/API can translate identity asserted by Workbench SSO.
- The MCP server can translate a validated token or future workload credential.
- Jobs can use an explicitly configured service principal.

`Principal` supports human, service, and agent identities. Its claims record
authentication context but grant no permissions by themselves. Core code does not
depend on FastAPI, HTTP headers, bearer tokens, or a specific identity provider.

Today the console relies on Workbench SSO and uses `x-forwarded-user` only for
review audit attribution. The MCP server compares a shared bearer token but does
not distinguish token users. Jobs and outbound Atlas/Impala connections run with
configured Workbench workload credentials. These mechanisms remain unchanged
until boundary adapters are introduced for their credential types.

## 2. Helios resource authorization: what may it do in Helios?

`helios_core.authorization.ResourceAuthorizer` evaluates a `Principal`, an action,
and a `HeliosResource`. This covers Helios-owned resources such as organizations,
data-source definitions, models, versions, and discovery runs.

The interface and denial exception are transport-neutral. A FastAPI route may map
a denial to HTTP 403, an MCP tool may return an MCP error, and a job may fail or
skip work without putting transport concerns into the policy layer.

The first policy implementation is `helios_core.rbac.RbacAuthorizer`. Each
`Grant` links one Principal to one Role on one explicit Resource. Grants are
organization-scoped for `org_admin` and model-scoped for `model_owner`,
`model_editor`, `model_viewer`, and `model_consumer`. A principal may therefore
hold different roles on different models. Organization-admin grants are inherited
only by resources whose `organization_id` matches the granted organization.

The centralized `ROLE_PERMISSIONS` map is the source of role-to-action grants;
applications continue to ask about actions and resources and never branch on role
names. Resource IDs are never sufficient by themselves: authorization also
matches organization scope, preventing an identically named resource in another
organization from receiving access. RBAC grants `query.execute` only as permission
to invoke that Helios operation. It does not grant access to the underlying data.

## 3. Data authorization: what lakehouse data may it access?

`helios_core.data_authorization.DataPolicy` is a distinct policy port for physical
data addressed through a DataSource. A grant to view, consume, or execute queries
through a Helios Model does **not** grant permission to query the tables
represented by that model.

DataPolicy composes two one-way decisions:

1. Helios restrictions may deny an operation.
2. A platform adapter must confirm both that the platform allows it and that the
   relevant identity will actually be enforced.

Helios cannot turn a platform denial into an allow. The default platform adapter
is unavailable and denies every operation. No Ranger rules, table permissions,
column permissions, or row filters are represented in Helios.

The required request flow is:

1. An application adapter authenticates credentials and creates a `Principal`.
2. The Helios resource authorizer checks the requested Helios operation.
3. If the operation touches physical data, data authorization is checked
   independently before the engine executes it.

### Identity propagation status

**Web users.** Workbench SSO supplies `x-forwarded-user`, which Helios translates
to a human Principal for Helios resource authorization. That browser identity is
not currently propagated to Impala. The model-scoped HTTP API does not currently
execute physical queries. Any future web query endpoint must remain denied until
an adapter can demonstrate caller-level platform enforcement or an explicitly
approved service-identity execution model is designed.

**MCP callers.** The current MCP bearer token is a shared application gate, not a
distinct workload identity. Impala would execute with server-configured
credentials, which could bypass the caller's Ranger permissions. Therefore the
MCP `run_query` tool now fails closed at DataPolicy before connecting to Impala.
It must not be enabled merely because the server service account can query.

**Background jobs and services.** Discovery jobs run as the Workbench/job owner
and connect using configured workload credentials. Ranger therefore evaluates
that service identity, not the user who initiated a job. This is suitable only
for deliberately scoped background processing. Jobs must be represented as
service Principals for Helios resource policy; they must not claim to preserve an
initiating user's physical-data permissions.

## Calling authorization from another component

`helios_core.authz` is the stable public interface. A component loads persisted
grants once, constructs a policy, and asks about domain actions and resources:

```python
from helios_core import authz

policy = authz.Policy(grants)
resource = authz.Resource(
    "model",
    model.id,
    organization_id=model.organization_id,
)

decision = policy.can(principal, authz.Action.MODEL_EDIT, resource)
if decision.allowed:
    ...

# Or fail with authz.AuthorizationDenied, a domain error that the application
# boundary may translate to HTTP 403, an MCP error, or a failed job.
policy.require(principal, authz.Action.MODEL_PUBLISH, resource)
```

The package-level `authz.can(..., grants=grants)` and
`authz.require(..., grants=grants)` helpers provide the same behavior for one-shot
checks. Callers do not import or inspect `ROLE_PERMISSIONS`, and authorization
core code does not raise FastAPI exceptions.

## FastAPI integration

The console mounts an authorized API under `/api/v1`. Dependencies in
`apps.console.api` translate the trusted Workbench user header into a Principal,
load organizations and models from the application resource store, construct
organization-aware Resource references, and call the shared Policy before route
business logic runs. Domain denials are translated to HTTP 403 only at this
boundary.

The application starts with an empty resource store and no grants, so the API
fails closed until persistence/bootstrap code supplies them. Tests and future
persistence adapters replace `app.state.resource_store` and
`app.state.authorization_policy`. Model metadata responses expose
`available_actions` computed by policy for UI affordances; routes still enforce
the corresponding action independently.

Existing server-rendered glossary, run, and review URLs remain available for
compatibility while their global artifacts are migrated to organization/model
ownership. New clients should use the model-scoped API.
