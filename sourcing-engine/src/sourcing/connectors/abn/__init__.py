"""ABN Lookup API connector (APIConnector — live JSONP, the resolution bridge)."""
from .lookup import SOURCE_ID, ABNLookupAPIConnector
from .parser import ABRException, calc_years_operating, normalize_to_company_record, parse_response

# Backwards-compatible alias for the pre-refactor name.
ABNLookupConnector = ABNLookupAPIConnector

__all__ = [
    "ABNLookupAPIConnector",
    "ABNLookupConnector",
    "SOURCE_ID",
    "ABRException",
    "normalize_to_company_record",
    "parse_response",
    "calc_years_operating",
]
