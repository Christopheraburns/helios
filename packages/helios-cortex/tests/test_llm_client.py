"""helios_cortex.llm (the `llm` extra): the provider a deployment's settings choose, and one
tool turn against each wire format (OpenAI-compatible and Bedrock Converse), with the
provider's HTTP replaced; a provider that does not answer in time is a timeout error."""
import pytest

httpx = pytest.importorskip("httpx", reason="the LLM client needs helios-cortex[llm]")

from helios_cortex.llm import LLMClient, LLMTimeoutError, ToolCall, llm_from_settings  # noqa: E402
from helios_cortex.settings import Settings  # noqa: E402

SEARCH = {"name": "search_semantics", "description": "Search",
          "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}}}


def answering(monkeypatch, body):
    """Replace the provider: every POST is recorded and answered with ``body``."""
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return body

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("helios_cortex.llm.client.httpx.post", post)
    return captured


def test_a_mistral_key_alone_chooses_mistral_on_its_openai_compatible_api(monkeypatch):
    for name in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "MISTRAL_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "server-side-mistral-secret")
    client = llm_from_settings(Settings().llm)
    assert (client.provider, client.model, client.base_url, client.api_key) == (
        "mistral", "mistral-small-latest", "https://api.mistral.ai/v1", "server-side-mistral-secret")


def test_an_openai_compatible_tool_turn(monkeypatch):
    captured = answering(monkeypatch, {
        "choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "call-1", "function": {"name": "search_semantics", "arguments": '{"question":"orders"}'}}]}}],
        "usage": {"prompt_tokens": 21, "completion_tokens": 7},
    })
    client = LLMClient("mistral", "mistral-small-latest", "server-side-secret", "https://api.mistral.ai/v1")
    turn = client.tool_turn("Use the tools.", [{"role": "user", "content": "Find orders"}], [SEARCH])

    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer server-side-secret"
    assert captured["json"]["model"] == "mistral-small-latest"
    assert turn.tool_calls == (ToolCall("call-1", "search_semantics", {"question": "orders"}),)
    assert (turn.stop_reason, turn.tokens_in, turn.tokens_out) == ("tool_calls", 21, 7)
    assert turn.latency_ms is not None
    assert turn.raw_tool_calls[0]["function"]["name"] == "search_semantics"


def test_a_bedrock_tool_turn_uses_a_bearer_key_and_the_converse_api(monkeypatch):
    captured = answering(monkeypatch, {
        "output": {"message": {"content": [
            {"text": "Checking."},
            {"toolUse": {"toolUseId": "call-1", "name": "describe_model", "input": {"model": "customer360"}}}]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 34, "outputTokens": 9},
    })
    client = LLMClient("bedrock", "amazon.nova-pro-v1:0", "bedrock-session-key",
                       "https://bedrock-runtime.us-east-1.amazonaws.com")
    turn = client.tool_turn("Use tools.", [{"role": "user", "content": "Describe the model."}],
                            [{"name": "describe_model", "description": "Describe a model", "inputSchema": {"type": "object"}}])

    assert captured["url"].endswith("/model/amazon.nova-pro-v1%3A0/converse")
    assert captured["headers"]["Authorization"] == "Bearer bedrock-session-key"
    assert captured["json"]["toolConfig"]["tools"][0]["toolSpec"]["name"] == "describe_model"
    assert turn.tool_calls == (ToolCall("call-1", "describe_model", {"model": "customer360"}),)
    assert (turn.stop_reason, turn.tokens_in, turn.tokens_out) == ("tool_use", 34, 9)
    assert turn.latency_ms is not None
    assert turn.raw_tool_calls[0]["name"] == "describe_model"


def test_a_provider_that_does_not_answer_in_time_is_a_timeout(monkeypatch):
    def post(*_args, **_kwargs):
        raise httpx.ConnectTimeout("connection timed out")

    monkeypatch.setattr("helios_cortex.llm.client.httpx.post", post)
    client = LLMClient("openai", "slow-model", "secret", "https://provider.example/v1", timeout=0.25)
    with pytest.raises(LLMTimeoutError, match=r"provider timed out \(response limit: 0.25 seconds\)"):
        client.tool_turn("Use the tools.", [], [])
