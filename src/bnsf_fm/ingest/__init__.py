"""Ingestion sources. The only layer that knows where data came from."""

from bnsf_fm.ingest.anonymize import Identity, Roster, normalize_identity, surrogate_id
from bnsf_fm.ingest.base import Batch, IngestionSource, load
from bnsf_fm.ingest.corrigo_api import (
    CorrigoApiSource,
    CorrigoAuthError,
    CorrigoClient,
    CorrigoCredentials,
)
from bnsf_fm.ingest.csv_source import CsvSource, HeaderMap, MappingError
from bnsf_fm.ingest.fixtures import CAMPUS_EDGES, FixtureSource

__all__ = [
    "CAMPUS_EDGES",
    "Batch",
    "CorrigoApiSource",
    "CorrigoAuthError",
    "CorrigoClient",
    "CorrigoCredentials",
    "CsvSource",
    "Identity",
    "FixtureSource",
    "HeaderMap",
    "IngestionSource",
    "MappingError",
    "Roster",
    "load",
    "normalize_identity",
    "surrogate_id",
]
