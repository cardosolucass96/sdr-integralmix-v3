from langgraph.graph import END, START, StateGraph

from app.agent.context import AgentRunContext
from app.agent.nodes import (
    delegate_specialist,
    execute_supervisor_handoff,
    interpret_turn,
    respond,
    update_qualification,
)
from app.agent.routing import (
    DELEGATE_SPECIALIST_NODE,
    INTERPRET_TURN_NODE,
    RESPOND_NODE,
    SUPERVISOR_HANDOFF_NODE,
    UPDATE_QUALIFICATION_NODE,
    route_after_interpretation,
    route_after_qualification,
)
from app.agent.state import AgentState


def build_graph(*, checkpointer=None):
    builder = StateGraph(AgentState, context_schema=AgentRunContext)
    builder.add_node(INTERPRET_TURN_NODE, interpret_turn)
    builder.add_node(DELEGATE_SPECIALIST_NODE, delegate_specialist)
    builder.add_node(UPDATE_QUALIFICATION_NODE, update_qualification)
    builder.add_node(SUPERVISOR_HANDOFF_NODE, execute_supervisor_handoff)
    builder.add_node(RESPOND_NODE, respond)
    builder.add_edge(START, INTERPRET_TURN_NODE)
    builder.add_conditional_edges(
        INTERPRET_TURN_NODE,
        route_after_interpretation,
        {
            DELEGATE_SPECIALIST_NODE: DELEGATE_SPECIALIST_NODE,
            UPDATE_QUALIFICATION_NODE: UPDATE_QUALIFICATION_NODE,
        },
    )
    builder.add_edge(DELEGATE_SPECIALIST_NODE, UPDATE_QUALIFICATION_NODE)
    builder.add_conditional_edges(
        UPDATE_QUALIFICATION_NODE,
        route_after_qualification,
        {
            SUPERVISOR_HANDOFF_NODE: SUPERVISOR_HANDOFF_NODE,
            RESPOND_NODE: RESPOND_NODE,
        },
    )
    builder.add_edge(SUPERVISOR_HANDOFF_NODE, END)
    builder.add_edge(RESPOND_NODE, END)
    compile_kwargs = {"checkpointer": checkpointer} if checkpointer is not None else {}
    return builder.compile(**compile_kwargs)


graph = build_graph()
