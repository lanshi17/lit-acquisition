"""Tests for lit_acquisition configuration."""



from lit_acquisition import (
    LitAcquisitionConfig,
    LLMConfig,
    NetworkConfig,
    configure,
    get_config,
    reload_config,
)


def test_default_config():
    """Default config has empty strings and sensible defaults."""
    cfg = LitAcquisitionConfig()
    assert cfg.llm.base_url == ""
    assert cfg.llm.model == ""
    assert cfg.llm.api_key == ""
    assert cfg.llm.max_tokens == 8192
    assert cfg.network.proxy == ""
    assert cfg.pubmed.base_url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def test_configure_updates_llm():
    """configure() updates LLM config."""
    reload_config()
    configure(
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="sk-test",
        llm_model="gpt-4o",
    )
    cfg = get_config()
    assert cfg.llm.base_url == "https://api.openai.com/v1"
    assert cfg.llm.api_key == "sk-test"
    assert cfg.llm.model == "gpt-4o"


def test_configure_updates_web_search():
    """configure() updates web search config."""
    reload_config()
    configure(
        firecrawl_api_key="fc-test",
        tavily_api_key="tvly-test",
    )
    cfg = get_config()
    assert cfg.web_search.firecrawl_api_key == "fc-test"
    assert cfg.web_search.tavily_api_key == "tvly-test"


def test_configure_updates_network():
    """configure() updates network config."""
    reload_config()
    configure(proxy="http://127.0.0.1:7890")
    cfg = get_config()
    assert cfg.network.proxy == "http://127.0.0.1:7890"


def test_configure_partial_update():
    """configure() only updates provided arguments."""
    reload_config()
    configure(llm_model="gpt-4o")
    cfg = get_config()
    assert cfg.llm.model == "gpt-4o"
    assert cfg.llm.base_url == ""  # unchanged


def test_all_api_keys_dedup():
    """all_api_keys deduplicates and preserves order."""
    llm = LLMConfig(
        api_key="sk-1",
        api_keys=["sk-2", "sk-3"],
    )
    assert llm.all_api_keys == ["sk-2", "sk-3", "sk-1"]

    llm_with_dupes = LLMConfig(
        api_key="sk-1",
        api_keys=["sk-1", "sk-2"],
    )
    assert llm_with_dupes.all_api_keys == ["sk-1", "sk-2"]


def test_network_resolve_proxy():
    """NetworkConfig.resolve_proxy_for_url respects no_proxy."""
    net = NetworkConfig(
        proxy="http://127.0.0.1:7890",
        no_proxy="cn,localhost,127.0.0.1",
    )
    assert net.resolve_proxy_for_url("https://example.com/page") == "http://127.0.0.1:7890"
    assert net.resolve_proxy_for_url("https://www.example.cn/page") is None
    assert net.resolve_proxy_for_url("https://localhost:8080") is None


def test_env_var_loading(monkeypatch):
    """Config loads from environment variables."""
    monkeypatch.setenv("LIT_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LIT_LLM_API_KEY", "sk-env")
    monkeypatch.setenv("LIT_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LIT_PROXY", "socks5://127.0.0.1:1080")
    cfg = reload_config()
    assert cfg.llm.base_url == "https://api.deepseek.com/v1"
    assert cfg.llm.api_key == "sk-env"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.network.proxy == "socks5://127.0.0.1:1080"
