import importlib
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

import app.agent.interpretation as interpretation_nodes
import app.agent.nodes.response as response_nodes
from app.agent import run_agent
from app.agent.chains.schemas import IntegralMixTurnInterpretation, QualificationFactUpdates
from app.core.config import get_settings
from app.observability import reset_langfuse_clients
from app.outbound_media import OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT

graph_module = importlib.import_module("app.agent.graph")


@pytest.fixture(autouse=True)
def no_outbound_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT,
    )


def test_langgraph_config_points_to_graph() -> None:
    config = json.loads(Path("langgraph.json").read_text())

    assert config["graphs"]["sdr_pipefacil"] == "./src/app/agent/agent.py:graph"
    assert config["env"] == "./.env"


def test_langgraph_scaffold_graph_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()

    class FakeInterpreterChain:
        def invoke(
            self,
            payload: dict[str, object],
            config: object | None = None,
        ) -> IntegralMixTurnInterpretation:
            assert payload["latest_user_message"] == "teste de estrutura"
            return IntegralMixTurnInterpretation(
                intent="question",
                reason="Mensagem curta tratada como pergunta neutra.",
            )

    class FakeResponderChain:
        def invoke(self, payload: dict[str, object], config: object | None = None) -> AIMessage:
            assert payload["intent"] == "question"
            assert payload["conversation_history"][-1].content == "teste de estrutura"
            return AIMessage(content="Resposta de scaffold.")

    monkeypatch.setattr(
        interpretation_nodes,
        "_build_interpreter_chain",
        lambda: FakeInterpreterChain(),
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    result = run_agent({"messages": [HumanMessage(content="teste de estrutura")]})

    assert result["status"] == "responded"
    assert result["latest_user_message"] == "teste de estrutura"
    assert result["intent"] == "question"
    assert result["intent_reason"] == "Mensagem curta tratada como pergunta neutra."
    assert result["response_text"] == "Resposta de scaffold."
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == "Resposta de scaffold."
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_langgraph_can_route_through_specialist_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()

    class FakeInterpreterChain:
        def invoke(
            self,
            payload: dict[str, object],
            config: object | None = None,
        ) -> IntegralMixTurnInterpretation:
            return IntegralMixTurnInterpretation(
                intent="request",
                reason="Pedido explicito de especialista.",
                requires_specialist=True,
                specialist_name="test_specialist",
                specialist_reason="Rodar especialista de teste.",
            )

    class FakeResponderChain:
        def invoke(self, payload: dict[str, object], config: object | None = None) -> AIMessage:
            assert payload["specialist_result"]["summary"] == "Resumo especialista."
            return AIMessage(content="Resposta depois do especialista.")

    def fake_delegate_specialist(state, config=None):
        assert state["requires_specialist"] is True
        return {
            "specialist_status": "completed",
            "specialist_result": {
                "status": "completed",
                "summary": "Resumo especialista.",
                "response_guidance": "Responder com base no resumo.",
                "extracted_fields": {},
                "confidence": 0.8,
                "error_code": None,
            },
        }

    monkeypatch.setattr(
        interpretation_nodes,
        "_build_interpreter_chain",
        lambda: FakeInterpreterChain(),
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(graph_module, "delegate_specialist", fake_delegate_specialist)

    graph = graph_module.build_graph()
    result = run_agent(
        {"messages": [HumanMessage(content="preciso de um especialista aqui")]},
        graph=graph,
    )

    assert result["specialist_status"] == "completed"
    assert result["response_text"] == "Resposta depois do especialista."
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_run_agent_supports_protocol_dict_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()

    class FakeInterpreterChain:
        def invoke(
            self,
            payload: dict[str, object],
            config: object | None = None,
        ) -> IntegralMixTurnInterpretation:
            assert payload["latest_user_message"] == "Oi via protocolo"
            return IntegralMixTurnInterpretation(
                intent="greeting",
                reason="Mensagem vinda do protocolo local.",
            )

    class FakeResponderChain:
        def invoke(self, payload: dict[str, object], config: object | None = None) -> AIMessage:
            assert payload["conversation_history"][-1]["content"] == "Oi via protocolo"
            return AIMessage(content="Resposta compatível com protocolo.")

    monkeypatch.setattr(
        interpretation_nodes,
        "_build_interpreter_chain",
        lambda: FakeInterpreterChain(),
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = run_agent({"messages": [{"type": "human", "content": "Oi via protocolo"}]})

    assert result["status"] == "responded"
    assert result["intent"] == "greeting"
    assert result["response_text"] == "Resposta compatível com protocolo."
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_complete_qualification_routes_to_application_handoff_without_responder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()

    class FakeInterpreterChain:
        def invoke(self, payload: dict[str, object], config: object | None = None):
            del payload, config
            return IntegralMixTurnInterpretation(
                intent="request",
                reason="O lead concluiu a qualificação.",
                qualification_updates=QualificationFactUpdates(
                    name="Maria",
                    document="12345678901",
                    activity="criador",
                    city="Fortaleza",
                    state="CE",
                    species="Peixes",
                    frequency="Mensal",
                    consumption="R$ 3.000",
                ),
                routing_segment="aquaculture_creator",
            )

    monkeypatch.setattr(
        interpretation_nodes,
        "_build_interpreter_chain",
        lambda: FakeInterpreterChain(),
    )
    monkeypatch.setattr(
        response_nodes,
        "_build_responder_chain",
        lambda: (_ for _ in ()).throw(AssertionError("qualified leads skip the responder")),
    )

    result = run_agent(
        {"messages": [HumanMessage(content="Sou criadora de peixes em Fortaleza.")]},
        graph=graph_module.build_graph(),
    )

    assert result["status"] == "supervisor_handoff_ready"
    assert result["pending_goal"] is None
    assert result["supervisor_handoff_request"]["route_categories"] == ["AQUAMIX"]
    assert result.get("response_text", "") == ""
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_supervisor_action_node_delegates_to_application_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()
    captured: list[dict[str, object]] = []

    class FakeInterpreterChain:
        def invoke(self, payload: dict[str, object], config: object | None = None):
            del payload, config
            return IntegralMixTurnInterpretation(
                intent="request",
                reason="O lead concluiu a qualificação.",
                qualification_updates=QualificationFactUpdates(
                    name="Maria",
                    document="12345678901",
                    activity="criador",
                    city="Fortaleza",
                    state="CE",
                    species="Peixes",
                    frequency="Mensal",
                    consumption="R$ 3.000",
                ),
                routing_segment="aquaculture_creator",
            )

    def handoff_action(request: dict[str, object]) -> dict[str, object]:
        captured.append(request)
        return {
            "handled": True,
            "status": "supervisor_handoff_delivered",
            "handoff_delivery_status": "delivered",
            "supervisor_assignment": {
                "deal_seq": 100,
                "supervisor_id": 7,
                "supervisor_name": "Ana",
            },
        }

    monkeypatch.setattr(
        interpretation_nodes,
        "_build_interpreter_chain",
        lambda: FakeInterpreterChain(),
    )
    result = run_agent(
        {"messages": [HumanMessage(content="Concluí os dados.")]},
        graph=graph_module.build_graph(),
        integral_mix_handoff_action=handoff_action,
    )

    assert captured[0]["route_categories"] == ["AQUAMIX"]
    assert result["status"] == "supervisor_handoff_delivered"
    assert result["supervisor_assignment"]["supervisor_id"] == 7
    assert result["supervisor_handoff_request"] is None
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_follow_up_after_handoff_does_not_restart_qualification_or_specialist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()
    graph = graph_module.build_graph(checkpointer=InMemorySaver())
    interpreter_turns = 0
    handoff_calls: list[dict[str, object]] = []

    class FakeInterpreterChain:
        def invoke(self, payload: dict[str, object], config: object | None = None):
            nonlocal interpreter_turns
            del payload, config
            interpreter_turns += 1
            if interpreter_turns == 1:
                return IntegralMixTurnInterpretation(
                    intent="request",
                    reason="O lead concluiu a qualificação.",
                    qualification_updates=QualificationFactUpdates(
                        name="Lucas",
                        document="12345678901",
                        activity="revendedor",
                        city="Fortaleza",
                        state="CE",
                        store_name="Cardoso Rações",
                        works_with_nutrition="sim",
                        current_brands="Integral Mix",
                        product_category="Ração para cavalo",
                        monthly_volume="R$ 15.000",
                    ),
                    routing_segment="agro_reseller",
                )
            return IntegralMixTurnInterpretation(
                intent="request",
                reason="O lead enviou outro valor depois do encaminhamento.",
                requires_specialist=True,
                specialist_name="test_specialist",
                qualification_updates=QualificationFactUpdates(monthly_volume="R$ 20.000"),
            )

    def handoff_action(request: dict[str, object]) -> dict[str, object]:
        handoff_calls.append(request)
        return {
            "handled": True,
            "status": "supervisor_handoff_delivered",
            "handoff_delivery_status": "delivered",
            "supervisor_assignment": {"deal_seq": 100, "supervisor_id": 7},
        }

    monkeypatch.setattr(
        interpretation_nodes,
        "_build_interpreter_chain",
        lambda: FakeInterpreterChain(),
    )
    monkeypatch.setattr(
        response_nodes,
        "_build_responder_chain",
        lambda: (_ for _ in ()).throw(AssertionError("assigned lead must not get a bot reply")),
    )

    first = run_agent(
        {"messages": [HumanMessage(content="Sou revendedor. Cardoso Rações, Fortaleza CE.")]},
        graph=graph,
        session_id="handoff-follow-up",
        integral_mix_handoff_action=handoff_action,
    )
    second = run_agent(
        {"messages": [HumanMessage(content="20 mil")]},
        graph=graph,
        session_id="handoff-follow-up",
        integral_mix_handoff_action=handoff_action,
    )

    assert first["status"] == "supervisor_handoff_delivered"
    assert first["known_facts"]["monthly_volume"] == "R$ 15.000"
    assert second["known_facts"]["monthly_volume"] == "R$ 15.000"
    assert second["pending_goal"] is None
    assert second["supervisor_handoff_request"] is None
    assert second["status"] == "supervisor_handoff_delivered"
    assert second["response_text"] == ""
    assert len(handoff_calls) == 1
    reset_langfuse_clients()
    get_settings.cache_clear()
