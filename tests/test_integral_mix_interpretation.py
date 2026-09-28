from __future__ import annotations

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

import app.agent.interpretation as interpretation_service
from app.agent.chains.schemas import IntegralMixTurnInterpretation, QualificationFactUpdates
from app.core.config import RuntimeSettings


def test_interpret_turn_uses_history_and_known_facts(monkeypatch) -> None:
    expected = IntegralMixTurnInterpretation(
        intent="request",
        reason="Lead informou a cidade.",
        contact_profile_name="Maria Silva",
        qualification_updates=QualificationFactUpdates(city="Sobral"),
        routing_segment="ruminant_creator",
    )

    class FakeInterpreterChain:
        def invoke(self, payload, config=None):
            assert payload["latest_user_message"] == "Na verdade, moro em Sobral."
            assert payload["known_facts"] == '{"name":"Maria","city":"Fortaleza"}'
            assert payload["contact_profile_label"] == '"mARIA da SILVA"'
            assert payload["conversation_history"][-1] == {
                "role": "user",
                "content": "Na verdade, moro em Sobral.",
            }
            assert config == {"callbacks": ["trace"]}
            return expected

    monkeypatch.setattr(
        interpretation_service,
        "_build_interpreter_chain",
        lambda: FakeInterpreterChain(),
    )
    result = interpretation_service.interpret_turn(
        {
            "messages": [HumanMessage(content="Na verdade, moro em Sobral.")],
            "known_facts": {"name": "Maria", "city": "Fortaleza"},
        },
        config={"callbacks": ["trace"]},
        runtime=Runtime(
            context=interpretation_service.AgentRunContext(
                settings=RuntimeSettings(),
                lead_profile_label="mARIA da SILVA",
            )
        ),
    )

    assert result["intent"] == "request"
    assert result["last_interpretation"]["qualification_updates"]["city"] == "Sobral"
    assert result["last_interpretation"]["contact_profile_name"] == "Maria Silva"
    assert result["last_interpretation"]["routing_segment"] == "ruminant_creator"


def test_interpret_turn_without_user_message_uses_safe_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        interpretation_service,
        "_build_interpreter_chain",
        lambda: (_ for _ in ()).throw(AssertionError("LLM should not run without input")),
    )

    result = interpretation_service.interpret_turn({"messages": []})

    assert result["latest_user_message"] == ""
    assert result["intent"] == "fallback"
    assert result["last_interpretation"]["qualification_updates"] == {
        "name": None,
        "document": None,
        "activity": None,
        "city": None,
        "state": None,
        "species": None,
        "frequency": None,
        "consumption": None,
        "store_name": None,
        "works_with_nutrition": None,
        "product_category": None,
        "monthly_volume": None,
        "current_brands": None,
    }
