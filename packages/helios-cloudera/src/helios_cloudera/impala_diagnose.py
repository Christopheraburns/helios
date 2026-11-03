"""Safe command-line diagnosis for CDW workload and proxy authentication."""
from __future__ import annotations

import argparse
from dataclasses import replace

from helios_core.caller import Caller, IdentityMode
from helios_core.engines import (
    ImpalaAuthenticationError,
    ImpalaEngine,
    ImpalaProxyDelegationError,
    impala_config,
)
from helios_core.settings import Settings


def main() -> None:
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

    configuration = impala_config(Settings())
    if configuration is None:
        raise SystemExit(
            "configuration: failed (set IMPALA_HOST, WORKLOAD_USER, and "
            "WORKLOAD_PASSWORD)"
        )
    engine = ImpalaEngine(configuration)
    try:
        if not engine.ping():
            raise SystemExit("service-account authentication: unexpected result")
    except ImpalaAuthenticationError as exc:
        raise SystemExit(f"service-account authentication: failed ({exc})")
    except Exception as exc:
        raise SystemExit(
            "service-account connectivity: failed "
            f"({type(exc).__name__}: {exc})"
        )
    print("service-account authentication: ok")

    if not args.delegated_user:
        return
    try:
        # Impersonation is what is being diagnosed, whatever mode the connection is set to.
        ImpalaEngine(replace(configuration, identity=IdentityMode.IMPERSONATE)).query(
            "SELECT 1",
            Caller(args.delegated_user),
            limit=1,
        )
    except ImpalaAuthenticationError as exc:
        raise SystemExit(f"proxy delegation: rejected by CDW ({exc})")
    except ImpalaProxyDelegationError as exc:
        raise SystemExit(f"proxy delegation: identity check failed ({exc})")
    except Exception as exc:
        raise SystemExit(
            f"proxy delegation: failed ({type(exc).__name__}: {exc})"
        )
    print(f"proxy delegation: ok for {args.delegated_user}")


if __name__ == "__main__":
    main()
