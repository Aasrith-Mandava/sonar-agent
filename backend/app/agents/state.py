from typing import Annotated, Sequence, TypedDict, List, Dict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """The shared state for the SonarQube Multi-Agent LangGraph."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    scan_run_id: str
    repo_id: str
    clone_path: str
    issues_queue: List[Dict[str, Any]]
    fixes_queue: List[Dict[str, Any]]
    
    # Track the active agent to handle cyclic returns
    current_agent: str
    
    # Keep track of iteration caps for loops
    revision_count: int
