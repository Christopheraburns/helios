"""Create or upgrade the operational metadata database.

Run with: ``python -m helios_core.metadata.setup``.
"""

from helios_core.settings import Settings

from .sqlite import SQLiteMetadataRepository


def main() -> None:
    repository = SQLiteMetadataRepository.from_settings(Settings())
    repository.migrate()
    print(f"helios metadata schema {repository.schema_version()} ready at {repository.path}")


if __name__ == "__main__":
    main()
