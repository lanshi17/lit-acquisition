"""Tests for new providers: Semantic Scholar, ClinicalTrials.gov, Zenodo."""

from lit_acquisition import (
    ClinicalTrialsConfig,
    OnlineAcquisitionItem,
    SemanticScholarConfig,
    ZenodoConfig,
    configure,
    get_config,
    reload_config,
)
from lit_acquisition.normalize import (
    normalize_clinical_trials,
    normalize_semantic_scholar,
    normalize_zenodo,
)

# --- Config tests ---


def test_semantic_scholar_config_defaults():
    cfg = SemanticScholarConfig()
    assert cfg.base_url == "https://api.semanticscholar.org/graph/v1"
    assert cfg.api_key == ""


def test_clinical_trials_config_defaults():
    cfg = ClinicalTrialsConfig()
    assert cfg.base_url == "https://clinicaltrials.gov/api/v2"


def test_zenodo_config_defaults():
    cfg = ZenodoConfig()
    assert cfg.base_url == "https://zenodo.org/api"


def test_configure_semantic_scholar():
    reload_config()
    configure(semantic_scholar_api_key="ss-test")
    cfg = get_config()
    assert cfg.semantic_scholar.api_key == "ss-test"


def test_configure_clinical_trials():
    reload_config()
    configure(clinical_trials_base_url="https://custom.ct.gov/api/v2")
    cfg = get_config()
    assert cfg.clinical_trials.base_url == "https://custom.ct.gov/api/v2"


def test_configure_zenodo():
    reload_config()
    configure(zenodo_base_url="https://custom.zenodo.org/api")
    cfg = get_config()
    assert cfg.zenodo.base_url == "https://custom.zenodo.org/api"


def test_env_var_loading_new_providers(monkeypatch):
    monkeypatch.setenv("LIT_SEMANTIC_SCHOLAR_API_KEY", "ss-env-key")
    monkeypatch.setenv("LIT_CLINICAL_TRIALS_BASE_URL", "https://env.ct.gov/api/v2")
    monkeypatch.setenv("LIT_ZENODO_BASE_URL", "https://env.zenodo.org/api")
    cfg = reload_config()
    assert cfg.semantic_scholar.api_key == "ss-env-key"
    assert cfg.clinical_trials.base_url == "https://env.ct.gov/api/v2"
    assert cfg.zenodo.base_url == "https://env.zenodo.org/api"


# --- Normalizer tests ---


def test_normalize_semantic_scholar_basic():
    item = {
        "paperId": "abc123",
        "title": "MECP2 mutations in Rett syndrome",
        "authors": [
            {"authorId": "1", "name": "Jane Doe"},
            {"authorId": "2", "name": "John Smith"},
        ],
        "year": 2023,
        "externalIds": {"DOI": "10.1038/ng.1234", "PubMed": "12345678"},
        "openAccessPdf": {"url": "https://example.com/paper.pdf", "status": "GREEN"},
        "fieldsOfStudy": ["Medicine", "Biology"],
        "citationCount": 42,
        "publicationTypes": ["CaseReport"],
        "journal": {"name": "Nature Genetics"},
    }
    result = normalize_semantic_scholar(item)
    assert result.source == "semantic_scholar"
    assert result.title == "MECP2 mutations in Rett syndrome"
    assert result.authors == ["Jane Doe", "John Smith"]
    assert result.doi == "10.1038/ng.1234"
    assert result.year == "2023"
    assert result.url == "https://example.com/paper.pdf"
    assert result.journal == "Nature Genetics"
    assert result.license == "GREEN"
    assert result.literature_type == "case_report"
    assert "Medicine" in result.keywords
    assert "Biology" in result.keywords
    assert result.identifiers.get("paperId") == "abc123"
    assert result.identifiers.get("pmid") == "12345678"


def test_normalize_semantic_scholar_no_oa():
    item = {
        "paperId": "def456",
        "title": "Some paper without OA",
        "authors": [],
        "year": 2020,
        "externalIds": {"DOI": "10.1234/test"},
    }
    result = normalize_semantic_scholar(item)
    assert result.license is None
    assert result.url == "https://doi.org/10.1234/test"
    assert result.identifiers.get("paperId") == "def456"


def test_normalize_clinical_trials_basic():
    item = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT12345678",
                "briefTitle": "Phase 2 Trial of Gene Therapy for Rett Syndrome",
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "startDateStruct": {"date": "2024-01-15"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Test Institute", "class": "OTHER"},
            },
            "conditionsModule": {
                "conditions": ["Rett Syndrome"],
                "interventions": [{"name": "Gene Therapy"}],
            },
            "designModule": {
                "phases": ["PHASE2"],
                "studyType": "INTERVENTIONAL",
            },
        }
    }
    result = normalize_clinical_trials(item)
    assert result.source == "clinical_trials"
    assert "Phase 2 Trial" in (result.title or "")
    assert result.authors == ["Test Institute"]
    assert result.year == "2024"
    assert result.url == "https://clinicaltrials.gov/study/NCT12345678"
    assert result.license == "public_domain"
    assert "Rett Syndrome" in result.keywords
    assert "Gene Therapy" in result.keywords
    assert result.identifiers.get("nct_id") == "NCT12345678"


def test_normalize_zenodo_basic():
    item = {
        "id": 12345,
        "metadata": {
            "title": "Dataset of genetic variants in Rett syndrome",
            "creators": [
                {"name": "Doe, Jane"},
                {"name": "Smith, John"},
            ],
            "publication_date": "2023-06-15",
            "doi": "10.5281/zenodo.12345",
            "access_right": "open",
            "license": "CC-BY-4.0",
            "keywords": ["Rett syndrome", "MECP2", "genetics"],
            "language": "eng",
        },
        "links": {
            "self": "https://zenodo.org/api/records/12345",
            "latest_html": "https://zenodo.org/records/12345",
        },
    }
    result = normalize_zenodo(item)
    assert result.source == "zenodo"
    assert result.title == "Dataset of genetic variants in Rett syndrome"
    assert result.authors == ["Doe, Jane", "Smith, John"]
    assert result.year == "2023"
    assert result.doi == "10.5281/zenodo.12345"
    assert result.url == "https://zenodo.org/records/12345"
    assert result.license == "CC-BY-4.0"
    assert result.publisher == "Zenodo"
    assert "Rett syndrome" in result.keywords
    assert "MECP2" in result.keywords
    assert result.identifiers.get("record_id") == "12345"


def test_normalize_zenodo_no_doi():
    item = {
        "id": 67890,
        "metadata": {
            "title": "Some record",
            "creators": [],
            "publication_date": "2022-01-01",
            "access_right": "restricted",
        },
        "links": {},
    }
    result = normalize_zenodo(item)
    assert result.doi is None
    assert result.url == "https://zenodo.org/records/67890"
    assert result.license == "restricted"


def test_online_acquisition_item_has_license_field():
    item = OnlineAcquisitionItem(
        source="test",
        title="Test",
        license="CC-BY-4.0",
    )
    assert item.license == "CC-BY-4.0"
