"""Phase 3 renderers (C-01 PDF, C-02 email, C-03 chat): determinism, validity,
exact mention locators and cross-artifact consistency."""

import email
import email.policy
import io
import json

import pytest
from pypdf import PdfReader

from helios_ds.config import ArtifactConfig, DatasetConfig, ScenarioConfig
from helios_ds.render import render_artifact
from helios_ds.render.base import parse_date
from helios_ds.scenarios import ScenarioPlanner

DATASET = "23a5b752-0b04-5ef7-afad-ca7205a1c999"


@pytest.fixture(scope="module")
def plan(tmp_path_factory):
    from helios_ds.templates import TemplateRegistry
    from helios_ds.tpcds import DuckDbTpcds

    repo = DuckDbTpcds.generate(0.01)
    templates = TemplateRegistry.load()
    config = DatasetConfig(
        artifacts={t: ArtifactConfig(target_count=25) for t in ("pdf", "email", "chat", "image")},
        scenarios={"product_return_damage": ScenarioConfig(weight=1.0)},
    )
    return templates, ScenarioPlanner(config, templates).plan(repo, DATASET)


def _rendered(plan, artifact_type):
    templates, generation = plan
    for scenario in generation.scenarios:
        for artifact in scenario.artifacts:
            if artifact.artifact_type == artifact_type:
                yield scenario, artifact, render_artifact(templates, scenario, artifact)


@pytest.mark.parametrize("artifact_type", ["pdf", "email", "chat"])
def test_rendering_is_deterministic_and_varied(plan, artifact_type):
    templates, _ = plan
    outputs = []
    for scenario, artifact, out in _rendered(plan, artifact_type):
        assert render_artifact(templates, scenario, artifact).data == out.data
        outputs.append(out.data)
    assert len(outputs) == 25
    assert len(set(outputs)) == 25


def test_email_is_valid_and_locators_are_exact(plan):
    for scenario, artifact, out in _rendered(plan, "email"):
        msg = email.message_from_bytes(out.data, policy=email.policy.default)
        assert msg["Message-ID"] == f"<{artifact.artifact_id}@mail.helios-retail.example>"
        assert msg["To"].addresses[0].domain == "helios-retail.example"
        assert not msg.is_multipart()
        body = msg.get_content().replace("\r\n", "\n")
        subject = str(msg["Subject"])
        for m in out.mentions:
            part = m.locator.get("part")
            if part in ("body", "subject"):
                text = body if part == "body" else subject
                assert text[m.locator["start"] : m.locator["end"]] == m.surface_form
        sent = email.utils.parsedate_to_datetime(msg["Date"]).date()
        assert sent >= parse_date(scenario.facts["return_date"])


def test_chat_matches_the_neutral_schema(plan):
    for _scenario, _artifact, out in _rendered(plan, "chat"):
        thread = json.loads(out.data)
        assert thread["schema"] == "helios-ds/chat-thread/1.0"
        assert thread["channel"] == "support-tier2"
        messages = {m["message_id"]: m for m in thread["messages"]}
        assert 4 <= len(messages) <= 8
        stamps = [m["timestamp"] for m in thread["messages"]]
        assert stamps == sorted(stamps)
        senders = {m["sender"] for m in thread["messages"]}
        assert senders <= {p["sender"] for p in thread["participants"]}
        assert len({p["name"] for p in thread["participants"]}) == len(thread["participants"])
        for m in out.mentions:
            text = messages[m.locator["message_id"]]["text"]
            assert text[m.locator["start"] : m.locator["end"]] == m.surface_form


def test_pdf_parses_to_one_page_containing_every_mention(plan):
    for _scenario, _artifact, out in _rendered(plan, "pdf"):
        reader = PdfReader(io.BytesIO(out.data))
        assert len(reader.pages) == 1
        text = reader.pages[0].extract_text()
        assert out.mentions
        for m in out.mentions:
            assert m.locator["page"] == 1
            assert m.surface_form in text
        assert reader.metadata["/CreationDate"] == "D:20000101000000+00'00'"  # invariant mode


def test_one_story_is_consistent_across_its_artifacts(plan):
    templates, generation = plan
    for scenario in generation.scenarios:
        texts = {}
        for artifact in scenario.artifacts:
            out = render_artifact(templates, scenario, artifact)
            if out is None:
                continue
            if artifact.artifact_type == "pdf":
                texts["pdf"] = PdfReader(io.BytesIO(out.data)).pages[0].extract_text()
            elif artifact.artifact_type == "email":
                texts["email"] = out.data.decode("utf-8", "replace")
                email_date = email.message_from_bytes(out.data, policy=email.policy.default)["Date"]
            else:
                texts[artifact.artifact_type] = out.data.decode()
                chat_first = json.loads(out.data)["messages"][0]["timestamp"]
        if {"pdf", "email", "chat"} <= set(texts):
            rma = [m for m in (texts["pdf"].split()) if m.startswith("RMA-")][0]
            assert all(rma in t.replace("=\r\n", "") for t in texts.values())
            email_day = email.utils.parsedate_to_datetime(email_date).date().isoformat()
            assert chat_first[:10] > email_day  # the chat happens after the email


def test_types_without_a_renderer_are_skipped(plan):
    assert all(out is None for _, _, out in _rendered(plan, "image"))


def test_articles_match_item_names(plan):
    import re

    for _scenario, _artifact, out in _rendered(plan, "email"):
        body = email.message_from_bytes(out.data, policy=email.policy.default).get_content()
        assert not re.search(r"\ba [aeiouAEIOU]", body), body
