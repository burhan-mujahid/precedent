"""Use the rulebook as a LangChain / LangGraph tool."""
from langchain_core.tools import tool

from precedent import Precedent

kb = Precedent.open("./my-rulebook")


@tool
def consult_rulebook(situation: str, context: str = "") -> dict:
    """Check the company's approved operating rules before acting on a business situation.
    If outcome is not 'answer', do not act: get a human."""
    return kb.handle_tool_call({"situation": situation, "context": context})

# agent = create_react_agent(model, tools=[consult_rulebook, ...])
