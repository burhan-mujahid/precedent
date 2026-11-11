"""Use the rulebook as LangChain / LangGraph tools."""
from langchain_core.tools import tool

from precedent import Precedent

kb = Precedent.open("./my-rulebook")


@tool
def consult_rulebook(situation: str, context: str = "", run_id: str = "") -> dict:
    """Check the company's approved operating rules before acting on a business situation.
    If outcome is not 'answer', do not act: get a human. Keep the decision_id for check_action."""
    return kb.handle_tool_call({"situation": situation, "context": context, "run_id": run_id})


@tool
def check_action(decision_id: str, step: str, stage: str = "during") -> dict:
    """Re-check a rulebook decision before each step that changes something (stage 'during') and
    once after you are done (stage 'after'). If outcome is 'halt', stop at once and tell the user."""
    return kb.handle_check_call({"decision_id": decision_id, "step": step, "stage": stage})


@tool
@kb.guarded(final=True)
def issue_refund(order: str, amount: float) -> str:
    """Refund a customer's order. Needs the decision_id from consult_rulebook; refused without a current one."""
    return f"refunded {amount} on {order}"      # your payment call goes here
    # A refused call raises precedent.boundary.Blocked: let it reach the agent as a tool error.

# agent = create_react_agent(model, tools=[consult_rulebook, check_action, issue_refund, ...])
