"""
Synapse AST - Абстрактное синтаксическое дерево
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class Node:
    line: int = 0
    column: int = 0

# --- Программа ---
@dataclass
class Program(Node):
    statements: List[Node] = field(default_factory=list)

# --- Определения ---
@dataclass
class AgentDef(Node):
    name: str = ""
    model: Optional[str] = None
    memory: Optional[str] = None
    energy_pool: Optional[Node] = None
    trust_level: Optional[str] = None
    trust_scope: List[str] = field(default_factory=list)
    soulprint: Optional[Node] = None
    methods: List[Node] = field(default_factory=list)

@dataclass
class FnDef(Node):
    name: str = ""
    params: List[str] = field(default_factory=list)
    body: List[Node] = field(default_factory=list)
    is_async: bool = False
    closure: any = None  # Environment captured at definition time

@dataclass
class FlowDef(Node):
    name: str = ""
    body: List[Node] = field(default_factory=list)

# --- Операторы ---
@dataclass
class LetStmt(Node):
    name: str = ""
    value: Node = None

@dataclass
class IfStmt(Node):
    condition: Node = None
    then_body: List[Node] = field(default_factory=list)
    else_body: List[Node] = field(default_factory=list)

@dataclass
class WhileStmt(Node):
    condition: Node = None
    body: List[Node] = field(default_factory=list)

@dataclass
class ForStmt(Node):
    var: str = ""
    iterable: Node = None
    body: List[Node] = field(default_factory=list)

@dataclass
class ReturnStmt(Node):
    value: Node = None

@dataclass
class ExprStmt(Node):
    expr: Node = None

@dataclass
class TryCatchStmt(Node):
    """Local recovery block for checked guard violations.

    Track B.1 intentionally keeps this lexical: guarded side effects must be
    inside the try body of a local catch(GUARD_VIOLATION) block. Interprocedural
    throws/checked-effect propagation is deferred to a future RFC.
    """
    try_body: List[Node] = field(default_factory=list)
    catch_error: str = "GUARD_VIOLATION"
    catch_binding: Optional[str] = None
    catch_body: List[Node] = field(default_factory=list)


@dataclass
class AssignStmt(Node):
    target: str = ""
    value: Node = None

@dataclass
class MemberAssignStmt(Node):
    target: Node = None
    member: str = ""
    value: Node = None

# --- Выражения ---
@dataclass
class BinaryExpr(Node):
    left: Node = None
    op: str = ""
    right: Node = None

@dataclass
class UnaryExpr(Node):
    op: str = ""
    operand: Node = None

@dataclass
class Literal(Node):
    value: Any = None

@dataclass
class Variable(Node):
    name: str = ""

@dataclass
class CallExpr(Node):
    callee: Node = None
    args: List[Node] = field(default_factory=list)

@dataclass
class MemberAccess(Node):
    obj: Node = None
    member: str = ""

@dataclass
class ListExpr(Node):
    elements: List[Node] = field(default_factory=list)

@dataclass
class DictExpr(Node):
    pairs: List[tuple] = field(default_factory=list)

# --- Специфичные для ИИ ---
@dataclass
class PromptExpr(Node):
    template: str = ""
    args: Dict[str, Node] = field(default_factory=dict)

@dataclass
class LLMCall(Node):
    prompt: Node = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 100

@dataclass
class ThoughtBlock(Node):
    steps: List[Node] = field(default_factory=list)
    aggregator: str = "chain"  # chain, best, consensus

@dataclass
class SuperposeBlock(Node):
    branches: List[Node] = field(default_factory=list)
    selector: str = "first"  # first, best, all, consensus


# --- Cognitive primitives ---
@dataclass
class AffectiveBias(Node):
    mood_ref: str = "mood"

@dataclass
class DebateBlock(Node):
    branches: List[Node] = field(default_factory=list)
    judge: Optional[Node] = None
    rounds: Optional[Node] = None
    affective_bias: Optional[AffectiveBias] = None

@dataclass
class ReflectBlock(Node):
    target: Optional[str] = None  # self | memory | values | None
    last: Optional[Node] = None
    filter_condition: Optional[Node] = None

@dataclass
class BranchDef(Node):
    name: str = ""
    body: List[Node] = field(default_factory=list)


# --- Inner Life primitives ---
@dataclass
class SoulprintDef(Node):
    values: Dict[str, float] = field(default_factory=dict)
    memory_type: str = "long-term"
    style: str = ""
    version: str = "1.0"
    protected: bool = True

@dataclass
class DreamBlock(Node):
    scenario: Optional[Node] = None
    config: Dict[str, Any] = field(default_factory=dict)
    body: List[Node] = field(default_factory=list)
    # Legacy v1.3 inline integration. v1.4 prefers explicit IntegrateBlock.
    integration_clause: Optional[List[Node]] = None

@dataclass
class AssertStmt(Node):
    condition: Node = None
    message: Optional[Node] = None

@dataclass
class IntegrateBlock(Node):
    dream_result: Node = None
    body: List[Node] = field(default_factory=list)
    on_fail: str = "rollback"  # rollback | warn | halt
    reason: Optional[Node] = None

@dataclass
class EvolveStmt(Node):
    target: Node = None
    condition: Node = None
    delay: Optional[Node] = None
    delay_unit: str = "events"  # events | seconds | calls
    policy_ref: Optional[str] = None
    mutations: List[Node] = field(default_factory=list)
    safety_guard: Optional[Node] = None  # legacy v1.3 with <expr>
    trigger: Optional[Node] = None       # legacy alias for older callers



# --- Fracture / multi-self primitives ---
@dataclass
class SubAgentDef(Node):
    name: str = ""
    focus: Optional[str] = None
    soulprint_override: Dict[str, Any] = field(default_factory=dict)
    body: List[Node] = field(default_factory=list)

@dataclass
class AffectiveWeightedConsensus(Node):
    mood_ref: str = "mood"
    biases: Dict[str, Node] = field(default_factory=dict)

@dataclass
class FractureStmt(Node):
    target: Node = None
    subagents: List[SubAgentDef] = field(default_factory=list)
    fracture_id: Optional[str] = None
    consensus_strategy: str = "weighted"  # weighted | majority | unanimous | affective_weighted
    consensus_config: Optional[Node] = None
    integration_clause: Optional[List[Node]] = None

@dataclass
class FractureResult(Node):
    """Runtime-only: represents the ephemeral result of a fracture."""
    fracture_id: str = ""
    positions: Dict[str, Any] = field(default_factory=dict)
    deaths: Dict[str, str] = field(default_factory=dict)
    consensus_value: Any = None



# --- Resonance / inter-subjectivity primitives ---
@dataclass
class ResonanceStmt(Node):
    target: Optional[Node] = None
    depth: str = "deep"
    aspects: List[str] = field(default_factory=list)
    window: Optional[Node] = None
    binding: str = "resonance"

@dataclass
class ReflectOnFracturesStmt(Node):
    last: Optional[Node] = None
    filter_condition: Optional[Node] = None

@dataclass
class MeasureIdentityCoherenceStmt(Node):
    window: Optional[Node] = None
    metrics: List[str] = field(default_factory=list)
    binding: str = "coherence"



# --- Collective intelligence primitives ---
@dataclass
class CollectiveDreamStmt(Node):
    participants: List[Node] = field(default_factory=list)
    policy_ref: Optional[str] = None
    scenario: Optional[Node] = None
    converge_on: Optional[Node] = None
    depth: str = "deep"
    timeout: Optional[Node] = None
    binding: str = "collective_result"

@dataclass
class DistributedConsensusStmt(Node):
    participants: List[Node] = field(default_factory=list)
    topic: Optional[Node] = None
    quorum: Optional[Node] = None
    timeout: Optional[Node] = None
    policy_ref: Optional[str] = None
    binding: str = "vote"

@dataclass
class SwarmFractureStmt(Node):
    participants: List[Node] = field(default_factory=list)
    policy_ref: Optional[str] = None
    scenario: Optional[Node] = None
    roles: Dict[str, str] = field(default_factory=dict)
    consensus_strategy: str = "weighted"
    timeout: Optional[Node] = None
    binding: str = "swarm_result"



# --- v1.9 Cognitive Continuity / Memory Palace primitives ---
@dataclass
class MemoryPalaceDef(Node):
    name: str = ""
    rooms: List[str] = field(default_factory=list)
    decay_policy: Dict[str, Any] = field(default_factory=dict)
    backend: str = "sqlite"
    binding: str = "palace"
    consolidate_during_dream: bool = False

@dataclass
class ImprintStmt(Node):
    palace: Optional[Node] = None
    room: str = "episodic"
    fields: Dict[str, Node] = field(default_factory=dict)
    binding: Optional[str] = None

@dataclass
class RecallStmt(Node):
    palace: Optional[Node] = None
    room: str = "episodic"
    query: Optional[Node] = None
    threshold: Optional[Node] = None
    limit: Optional[Node] = None
    binding: str = "memories"
    affective_filter: Optional[Node] = None
    affective_sort: Optional[tuple] = None

@dataclass
class IntentionCascadeDef(Node):
    name: str = ""
    levels: Dict[str, Node] = field(default_factory=dict)
    binding: str = "plan"

@dataclass
class PlanWeaveStmt(Node):
    participants: List[Node] = field(default_factory=list)
    policy_ref: Optional[str] = None
    intention: Optional[Node] = None
    checkpoint_every: Optional[Node] = None
    rollback_on: str = "failure"
    timeout: Optional[Node] = None
    binding: str = "execution"

@dataclass
class InlineHabitCond(Node):
    pad_conditions: List[tuple] = field(default_factory=list)  # [(key, op, Node)]
    context: Optional[str] = None

@dataclass
class FatigueDef(Node):
    threshold: int = 5
    energy_cost_multiplier: float = 1.0
    require_rest: int = 0

@dataclass
class HabitStmt(Node):
    # Legacy v1.9 metadata map is preserved for compatibility. v2.1.3-B
    # adds structured fields for registry/activation without executing body.
    fields: Dict[str, Node] = field(default_factory=dict)
    name: Optional[str] = None
    frequency_op: Optional[str] = None
    frequency_val: Optional[Node] = None
    stability_op: Optional[str] = None
    stability_val: Optional[Node] = None
    promote_to: Optional[Node] = None
    energy_cost: Optional[Node] = None
    priority: str = "medium"
    body: List[Node] = field(default_factory=list)
    activate_when: List[Node] = field(default_factory=list)
    suppress_when: List[Node] = field(default_factory=list)
    fatigue: Optional[FatigueDef] = None
    binding: str = "habit_id"

@dataclass
class ConsolidateStmt(Node):
    palace: Optional[Node] = None
    rooms: List[str] = field(default_factory=list)
    binding: str = "consolidation"
    affective_routing: Optional[List[Node]] = None




# --- v2.1.0 Affective Memory Layer ---
@dataclass
class AffectivePadLiteral(Node):
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

@dataclass
class DecayExpr(Node):
    value: int = -1  # -1 = never
    unit: str = "never"  # days | events | never
    original: str = "never"

@dataclass
class AffectiveFilterExpr(Node):
    kind: str = "comparison"  # comparison | tagged | untagged | and
    left: Any = None
    op: Optional[str] = None
    right: Any = None

@dataclass
class RoutingRule(Node):
    condition: AffectiveFilterExpr = None
    actions: List[Node] = field(default_factory=list)

@dataclass
class RoutingAction(Node):
    kind: str = "keep"  # promote_to | keep | tag
    target: Optional[str] = None
    tag: Optional[str] = None




# --- v2.1.3 Living Habits Phase A ---
@dataclass
class EnergyPoolDecl(Node):
    max: Optional[Node] = None
    initial: Optional[Node] = None
    recharge_amount: Optional[Node] = None
    recharge_every: Optional[Node] = None
    rest_threshold: Optional[Node] = None
    hysteresis_margin: Optional[Node] = None

@dataclass
class ContextBlock(Node):
    label: str = ""
    body: List[Node] = field(default_factory=list)

# --- v2.1.2 Reactive Affective Layer ---
@dataclass
class AffectiveThresholdDef(Node):
    name: str = ""
    condition: Node = None
    for_events: Optional[int] = None
    cooldown: Optional[int] = None
    priority: str = "medium"
    action: List[Node] = field(default_factory=list)

@dataclass
class ThresholdRef(Node):
    name: str = ""

@dataclass
class MemoryAccess(Node):
    name: str = ""
    operation: str = "read"  # read, write, clear
    value: Node = None

@dataclass
class ImportStmt(Node):
    module: str = ""
    alias: Optional[str] = None

# --- Governance / AI safety primitives ---
@dataclass
class PolicyDef(Node):
    name: str = ""
    target: Optional[Node] = None
    rules: List[Node] = field(default_factory=list)
    guard_params: List[str] = field(default_factory=list)
    guard_body: List[Node] = field(default_factory=list)
    # v1.4 policy-as-code fields
    trigger: Optional[Node] = None
    cooldown: Optional[Node] = None
    max_delta: Optional[Node] = None
    guard_expr: Optional[Node] = None
    require_approval: bool = False
    fields: Dict[str, Node] = field(default_factory=dict)

@dataclass
class PolicyRule(Node):
    kind: str = ""  # require | forbid
    value: Node = None


@dataclass
class RejectStmt(Node):
    message: Optional[Node] = None

@dataclass
class VerifyBlock(Node):
    checks: List[Node] = field(default_factory=list)

@dataclass
class CheckStmt(Node):
    condition: Node = None
    message: Optional[Node] = None

@dataclass
class ClaimDef(Node):
    name: str = ""
    text: Optional[Node] = None
    evidence: Optional[Node] = None
    confidence: Optional[Node] = None

@dataclass
class ConsequenceDef(Node):
    name: str = ""
    fields: Dict[str, Node] = field(default_factory=dict)

@dataclass
class GovernedMemoryWrite(Node):
    value: Node = None
    fields: Dict[str, Node] = field(default_factory=dict)



@dataclass
class IntentDef(Node):
    name: str = ""
    fields: Dict[str, Node] = field(default_factory=dict)

@dataclass
class DeclareIntentStmt(Node):
    name: str = ""

@dataclass
class GovernedMemoryForget(Node):
    key: Node = None
    fields: Dict[str, Node] = field(default_factory=dict)

@dataclass
class ObserveHandler(Node):
    event_type: str = ""
    binding: str = "event"
    body: List[Node] = field(default_factory=list)

@dataclass
class ObserveBlock(Node):
    target: Optional[Node] = None
    handlers: List[ObserveHandler] = field(default_factory=list)

# --- Actor / durable execution primitives ---
@dataclass
class SendStmt(Node):
    receiver: Node = None
    method: str = ""
    args: List[Node] = field(default_factory=list)
    async_send: bool = False

@dataclass
class SpawnExpr(Node):
    callee: Node = None

@dataclass
class AwaitExpr(Node):
    expr: Node = None

@dataclass
class SuspendExpr(Node):
    request: Node = None

@dataclass
class ReceivePattern(Node):
    sender_var: str = ""
    target_var: str = ""
    body: List[Node] = field(default_factory=list)

@dataclass
class ReceiveBlock(Node):
    patterns: List[ReceivePattern] = field(default_factory=list)
    timeout: Optional[Node] = None
    else_body: List[Node] = field(default_factory=list)

@dataclass
class MigrateStmt(Node):
    target: Node = None


# --- v2.0 Affective Runtime & Cognitive VM primitives ---
@dataclass
class AffectiveStateDef(Node):
    name: str = ""
    dimensions: Dict[str, Any] = field(default_factory=dict)
    baseline: Dict[str, float] = field(default_factory=dict)
    decay: float = 0.0
    decay_unit: str = "minute"
    binding: str = "mood"

@dataclass
class AffectiveEventStmt(Node):
    name: str = ""
    fields: Dict[str, Node] = field(default_factory=dict)
    binding: str = "emotional_tag"

@dataclass
class AffectiveModulationStmt(Node):
    rules: List[Node] = field(default_factory=list)
    binding: str = "modulation_rules"

@dataclass
class AffectiveResonanceStmt(Node):
    target: Optional[Node] = None
    mirror: Optional[str] = None
    regulate: List[str] = field(default_factory=list)
    dampen: Dict[str, Node] = field(default_factory=dict)
    binding: str = "emotional_bridge"

@dataclass
class SomaticMarkerStmt(Node):
    name: str = ""
    fields: Dict[str, Node] = field(default_factory=dict)
    binding: str = "marker"

@dataclass
class CompileVmStmt(Node):
    source: Optional[Node] = None
    binding: str = "bytecode"

@dataclass
class AtIpTrigger(Node):
    ip: int = 0

@dataclass
class BeforeOpTrigger(Node):
    op: str = ""

@dataclass
class RunVmStmt(Node):
    # v2.0 compatibility: `program` is an alias for `source`.
    program: Optional[Node] = None
    source: Optional[Node] = None
    resume_from: Optional[Node] = None
    gas: Optional[Node] = None
    cognitive_budget: Optional[int] = None
    checkpoint_label: Optional[str] = None
    checkpoint_trigger: Optional[Node] = None
    binding: str = "vm_result"

    def __post_init__(self):
        if self.source is None and self.program is not None:
            self.source = self.program
        if self.program is None and self.source is not None:
            self.program = self.source
