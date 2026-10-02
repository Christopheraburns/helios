"""Compatibility: the connector classes now live in ``connectors``."""

from .connectors.base import Fetched, SourceAsset, plan_incremental
from .connectors.helios_ds import HeliosDsConnector, object_reader


def s3_client_from_connection(name: str):
    """The boto3 S3 client behind a Cloudera AI Workbench data connection."""
    import cml.data_v1 as cmldata

    return cmldata.get_connection(name).get_base_connection()


__all__ = [
    "Fetched",
    "HeliosDsConnector",
    "SourceAsset",
    "object_reader",
    "plan_incremental",
    "s3_client_from_connection",
]
