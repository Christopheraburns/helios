"""Safe command-line diagnosis for CDW workload and proxy authentication."""
from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import replace

from helios_cortex.caller import Caller, IdentityMode
from helios_cortex.settings import Settings

from helios_cloudera.impala import (
    ImpalaAuthenticationError,
    ImpalaEngine,
    ImpalaProxyDelegationError,
    impala_config,
)
from helios_cloudera.workbench import WorkbenchSettings


class DiagnosisFailed(Exception):
    """A check failed; the message says which and why, without credentials or SQL."""


def diagnose(settings: Settings, delegated_user: str | None = None, echo: Callable[[str], None] = print) -> None:
    """Check the ``default`` Impala connection's workload credential, then, with
    ``delegated_user``, that Impala runs a query as that user through doAs. ``echo``
    reports each check that passes; the first that fails raises DiagnosisFailed."""
    configuration = impala_config(settings)
    if configuration is None:
        raise DiagnosisFailed("configuration: failed (set IMPALA_HOST, WORKLOAD_USER, and WORKLOAD_PASSWORD)")
    engine = ImpalaEngine(configuration)
    try:
        if not engine.ping():
            raise DiagnosisFailed("service-account authentication: unexpected result")
    except ImpalaAuthenticationError as exc:
        raise DiagnosisFailed(f"service-account authentication: failed ({exc})") from exc
    except DiagnosisFailed:
        raise
    except Exception as exc:
        raise DiagnosisFailed(f"service-account connectivity: failed ({type(exc).__name__}: {exc})") from exc
    echo("service-account authentication: ok")

    if not delegated_user:
        return
    try:
        # Impersonation is what is being diagnosed, whatever mode the connection is set to.
        ImpalaEngine(replace(configuration, identity=IdentityMode.IMPERSONATE)).query(
            "SELECT 1", Caller(delegated_user), limit=1)
    except ImpalaAuthenticationError as exc:
        raise DiagnosisFailed(f"proxy delegation: rejected by CDW ({exc})") from exc
    except ImpalaProxyDelegationError as exc:
        raise DiagnosisFailed(f"proxy delegation: identity check failed ({exc})") from exc
    except Exception as exc:
        raise DiagnosisFailed(f"proxy delegation: failed ({type(exc).__name__}: {exc})") from exc
    echo(f"proxy delegation: ok for {delegated_user}")


def main() -> None:
    """``python -m helios_cloudera.impala_diagnose``; the same as
    ``helios --profile workbench impala diagnose``."""
    parser = argparse.ArgumentParser(
        description=(
            "Test the configured workload credential first, then an optional "
            "delegated SSO identity. No credential or SQL is printed."
        )
    )
    parser.add_argument(
        "--delegated-user",
        help="authenticated Cloudera SSO subject to verify through doAs",
    )
    args = parser.parse_args()
    try:
        diagnose(WorkbenchSettings(), args.delegated_user)
    except DiagnosisFailed as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
