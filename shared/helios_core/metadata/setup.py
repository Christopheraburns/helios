"""Create or upgrade the operational metadata database.

Run with: ``python -m helios_core.metadata.setup``.
"""
from .sqlite import SQLiteMetadataRepository


def main() -> None:
    repository = SQLiteMetadataRepository()
    repository.migrate()
    print(
        f"helios metadata schema {repository.schema_version()} "
        f"ready at {repository.path}"
    )


if __name__ == "__main__":
    main()
