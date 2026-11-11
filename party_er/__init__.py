"""Party entity resolution: assign a unique entity id to party records
(name, account, address, country code) and infer country from the address."""
from .country import CountryDetector, CountryResult, normalize_country_code, reconcile_country
from .matching import MatchConfig
from .pipeline import (
    ColumnMap,
    EntityResolver,
    ResolutionResult,
    ResolverConfig,
    load_previous_mapping,
    read_table,
)

__all__ = [
    "ColumnMap",
    "CountryDetector",
    "CountryResult",
    "EntityResolver",
    "MatchConfig",
    "ResolutionResult",
    "ResolverConfig",
    "load_previous_mapping",
    "normalize_country_code",
    "read_table",
    "reconcile_country",
]
__version__ = "0.1.0"
