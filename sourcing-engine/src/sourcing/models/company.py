"""CompanyRecord — the unit that is acquired and ranked (spec §3.3).

Declared in Step 1, populated in later steps. Defined now so the schema and
downstream interfaces are stable.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Location(BaseModel):
    state: str | None = None
    postcode: str | None = None
    suburb: str | None = None
    lat: float | None = None
    lng: float | None = None
    km_to_capital: float | None = None


class Sector(BaseModel):
    anzsic: list[str] = Field(default_factory=list)
    anzsic_confidence: float | None = None  # set by the signal extractor
    category_text: list[str] = Field(default_factory=list)
    keyword_hits: list[str] = Field(default_factory=list)
    keyword_density: float | None = None
    exclude_hits: list[str] = Field(default_factory=list)


class Age(BaseModel):
    abn_registered: str | None = None
    asic_registered: str | None = None  # ASIC registration_date (merged by the resolver)
    years_operating: int | None = None


class Size(BaseModel):
    employee_count: int | None = None
    employee_source: str | None = None
    review_count: int | None = None  # proxy size signal from Maps/directories
    revenue_est_aud: float | None = None
    revenue_confidence: float | None = None
    ebitda_est_aud: float | None = None
    ebitda_confidence: float | None = None


class Ownership(BaseModel):
    structure_guess: str | None = None
    pe_vc_backed: bool | None = None
    listed_entity: bool | None = None
    institutional_on_register: bool | None = None


class MoatSignals(BaseModel):
    ip: bool | None = None
    gov_contracts: bool = False
    gov_contract_value_aud: int | None = None
    gov_contract_count: int | None = None            # number of AusTender releases
    gov_contract_agencies: list[str] = Field(default_factory=list)  # for concentration check
    regulatory_accreditation: bool | None = None
    physical_ops: bool | None = None
    hard_assets: bool | None = None                  # signal extractor
    recurring_revenue_hint: bool | None = None       # signal extractor
    award_finalist: bool | None = None


class AwardSignal(BaseModel):
    """A finalist/winner listing from an award register (set by an AgentConnector)."""

    program: str                       # e.g. "Telstra Best of Business"
    tier: int = 2                      # 1 = national/flagship, 2 = regional
    year: int | None = None
    category: str | None = None        # LLM-classified business category of the finalist
    state: str | None = None
    level: str = "finalist"            # finalist | winner


class Provenance(BaseModel):
    field: str
    source: str
    locator: str | None = None  # e.g. OCID, URL, or query that produced the value
    fetched_at: str | None = None
    confidence: float | None = None


class Screen(BaseModel):
    status: str | None = None
    score: float | None = None
    flags: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)
    missed: list[str] = Field(default_factory=list)


class CompanyRecord(BaseModel):
    # Defaults to "" so enrichment fragments (e.g. an AusTender release mapped on
    # its own) can be constructed without an id and merged onto a resolved record.
    entity_id: str = ""
    abn: str | None = None
    acn: str | None = None
    legal_name: str | None = None
    trading_names: list[str] = Field(default_factory=list)
    country: str = "Australia"
    location: Location = Field(default_factory=Location)
    sector: Sector = Field(default_factory=Sector)
    age: Age = Field(default_factory=Age)
    size: Size = Field(default_factory=Size)
    ownership: Ownership = Field(default_factory=Ownership)
    business_model: str | None = None
    moat_signals: MoatSignals = Field(default_factory=MoatSignals)
    award_signals: list[AwardSignal] = Field(default_factory=list)
    contacts_min: dict = Field(default_factory=dict)
    website_text_raw: str | None = None  # attached by the website connector / enrichment node
    deferred_assessment: dict = Field(default_factory=dict)
    provenance: list[Provenance] = Field(default_factory=list)
    screen: Screen = Field(default_factory=Screen)
    # Record-level flags (resolution / data-quality), distinct from screen.flags.
    flags: list[str] = Field(default_factory=list)
    resolution_confidence: float | None = None  # set by EntityResolver
