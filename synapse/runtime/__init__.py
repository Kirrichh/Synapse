"""
Synapse Runtime Engines.

Domain engines extracted from the monolithic interpreter.  Engines are
orchestrated by Interpreter and must not own AST dispatch semantics.
"""
from synapse.runtime.replay_engine import ReplayEngine
from synapse.runtime.governance_engine import GovernanceEngine
from synapse.runtime.affective_runtime import AffectiveRuntime
from synapse.runtime.habit_engine import HabitEngine
from synapse.runtime.actor_runtime import ActorRuntime
from synapse.runtime.vm_bridge import VMBridge
from synapse.runtime.vm_routing import VMRoutingDecision, classify_ast_node, classify_host_opcode, coverage_ratio

__all__ = ["ReplayEngine", "GovernanceEngine", "AffectiveRuntime", "HabitEngine", "ActorRuntime", "VMBridge", "VMRoutingDecision", "classify_ast_node", "classify_host_opcode", "coverage_ratio"]
