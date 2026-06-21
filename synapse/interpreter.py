"""
Synapse Interpreter - Интерпретатор языка Synapse
"""
from typing import Any, Dict, List, Optional, Set
from collections.abc import Mapping
from dataclasses import replace
from enum import Enum, auto
import re
import uuid
import hashlib
import copy
import json
from .ast import *
from .builtins import BUILTINS, LLMBackend, Memory, AgentRuntime, DurableActorRef, DurablePromise
from .metrics import SynapseMetrics
from .hardening import hash_event_chain, verify_event_chain, canonical_json
from .memory import MemoryPalace
from .intention import IntentionCascade, weave_plan
from .habit import form_habit, EnergyPool, ContextTracker, AgentMode, ContextStackError, HabitRegistry, HabitEvaluator, HabitRuntimeRecord, HabitActivationEngine, HabitState, HabitRecursionError
from .affective import AffectiveState, modulation_from_state, affective_bridge, clamp
from .somatic import compute_gut_feeling
from .bytecode import CognitiveCompiler, BytecodeProgram
from .cvm import CognitiveVM, VMState, OutOfEnergy, VMSnapshot, VMSnapshotFormatError, VMConflictingSourceError, VMMultipleCheckpointError, VMResumeSyncError, VMTamperDetectedError, UnknownOpcodeError
from .threshold import ThresholdRegistry, ThresholdPurityViolation
from .runtime import ReplayEngine, GovernanceEngine, AffectiveRuntime, HabitEngine, ActorRuntime, VMBridge
from .runtime.consensus_engine import (
    ConsensusEngine,
    ConsensusRequest,
    ConsensusValidationError,
    ExplicitVoteSource,
    NULL_VOTE_SOURCE,
)
from .runtime.consensus_proposal_view import FrozenDict, FrozenList, ProposalViewMutationError
from .runtime.consensus_vote_sources import ActorMethodVoteSource
from .runtime.vm_routing import classify_ast_node_v22, fallback_reason_for
from .version import RUNTIME_VERSION
from .canonical_path import make_env_path
from .state_overlay import (
    ALPHA3G_LOCAL_JSON_PROFILE,
    STABLE_CANONICAL_PROFILE,
    StateOverlay,
    StateOverlayError,
    WriteSet,
    WriteSetEntry,
    canonical_value_hash,
)

class ReturnException(Exception):
    def __init__(self, value):
        self.value = value

class RuntimeError(Exception):
    pass


class ConsensusVoteSideEffectError(RuntimeError):
    """A vote method attempted an operation outside P3b-0's pure query contract."""


class ConsensusVoteRegistryMutationError(ConsensusVoteSideEffectError):
    """A vote method tried to alter VoteSource selection while collecting votes."""

class PolicyViolationException(Exception):
    pass

class PolicyCompilationError(Exception):
    pass

class ReplayIntegrityError(RuntimeError):
    """Strict replay detected a corrupted or mismatched recorded event."""
    pass


class ConsensusReplayIntegrityError(ReplayIntegrityError):
    """Recorded consensus history cannot be safely replayed."""

class IdentityCrisisError(RuntimeError):
    """Raised when code attempts to rewrite protected identity outside evolve."""
    pass

class DreamIsolationViolation(RuntimeError):
    """Raised when dream sandbox attempts an external side-effect."""
    pass

class DreamSandboxIsolationError(DreamIsolationViolation):
    """Raised when dream sandbox cannot safely isolate a parent-scope value."""
    pass

class NondeterminismBarrierViolation(RuntimeError):
    """RFC-INTEGRATE barrier violation for nondeterministic or external effects."""
    pass

class IntegrateIsolationViolation(NondeterminismBarrierViolation):
    """Raised when integrate transaction attempts forbidden inference/external side-effects."""
    pass

class IntegrateAssertionFailed(RuntimeError):
    """Raised by assert inside integrate to trigger transactional failure handling."""
    pass

class EvolutionTicketExpired(RuntimeError):
    """Raised when a deferred evolution ticket expires without satisfying its condition."""
    pass

class FracturePanicException(RuntimeError):
    """Unrecoverable error during fracture — base agent must decide how to proceed."""
    pass

class OrphanedIdentityException(RuntimeError):
    """Sub-agent attempted a forbidden external/stateful operation."""
    pass

class NestedFractureException(RuntimeError):
    """Maximum fracture depth exceeded or nested integrate attempted."""
    pass

class ResonancePrivacyException(RuntimeError):
    """Cross-agent resonance attempted without explicit readable policy."""
    pass

class CollectiveConsensusTimeout(RuntimeError):
    """Collective intelligence primitive timed out before quorum/consensus."""
    pass

class AffectiveIsolationViolation(RuntimeError):
    """Affective runtime was invoked from an incompatible context."""
    pass

class GuardMutationError(RuntimeError):
    """Policy guard attempted to mutate a frozen mood snapshot."""
    pass

class ConsensusBiasMissingError(RuntimeError):
    """affective_weighted consensus lacks an explicit branch bias and no Default was provided."""
    pass

class FrozenMoodSnapshot:
    """Read-only PAD snapshot exposed to policy guards and affective consensus."""
    __slots__ = ("_values", "_frozen")
    def __init__(self, values: Optional[Dict[str, Any]] = None):
        object.__setattr__(self, "_frozen", False)
        vals = values or {}
        object.__setattr__(self, "_values", {
            "valence": float(vals.get("valence", 0.0) or 0.0),
            "arousal": float(vals.get("arousal", 0.0) or 0.0),
            "dominance": float(vals.get("dominance", 0.0) or 0.0),
        })
        object.__setattr__(self, "_frozen", True)
    def __getattr__(self, name: str) -> float:
        if name in {"pleasure", "valence"}:
            return self._values["valence"]
        if name in {"energy", "arousal"}:
            return self._values["arousal"]
        if name in {"control", "dominance"}:
            return self._values["dominance"]
        raise AttributeError(name)
    def __setattr__(self, name: str, value: Any):
        if getattr(self, "_frozen", False):
            raise GuardMutationError("mood snapshot is read-only inside policy guard")
        object.__setattr__(self, name, value)
    def __getitem__(self, key: str) -> float:
        return getattr(self, key)
    def __setitem__(self, key: str, value: Any):
        raise GuardMutationError("mood snapshot is read-only inside policy guard")
    def to_dict(self) -> Dict[str, float]:
        return dict(self._values)

class EvolutionCooldownException(RuntimeError):
    """Reserved for hard cooldown failures; v1.5.1 uses deferred tickets instead."""
    pass

class RejectException(Exception):
    def __init__(self, message: str):
        self.message = message

class RuntimeMode(Enum):
    LIVE = auto()
    REPLAY = auto()

class Suspension:
    """Durable suspension point emitted by the coroutine interpreter.

    A Suspension is intentionally JSON-safe: it records the current node kind,
    environment snapshot and a reason. It does not pretend to serialize the
    Python generator frame itself; a production continuation layer must map this
    metadata back to an execution cursor/bytecode offset.
    """

    def __init__(self, node: Node, env: "Environment", reason: str, payload: Any = None):
        self.node = node
        self.env = env
        self.reason = reason
        self.payload = payload

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason": self.reason,
            "node_type": type(self.node).__name__,
            "line": getattr(self.node, "line", 0),
            "column": getattr(self.node, "column", 0),
            "payload": self.payload,
            "env": self.env.to_dict(),
        }

class DebateContext:
    """Read-only meta-context exposed inside debate branches."""

    def __init__(self, current_round: int, store: Dict[str, List[Any]]):
        self._current_round = current_round
        self._store = store

    def round(self) -> int:
        return self._current_round

    def history(self, branch_name: Optional[str] = None) -> Any:
        if branch_name is None:
            return {k: list(v) for k, v in self._store.items()}
        return list(self._store.get(str(branch_name), []))

    def transcript(self) -> str:
        lines = []
        max_rounds = max((len(v) for v in self._store.values()), default=0)
        for idx in range(max_rounds):
            lines.append(f"[Round {idx + 1}]")
            for branch, values in self._store.items():
                if idx < len(values):
                    lines.append(f"{branch}: {values[idx]}")
        return "\n".join(lines)


class Environment:
    def __init__(self, parent: Optional['Environment'] = None, env_id: Optional[str] = None):
        self.env_id = env_id or str(uuid.uuid4())
        self.variables: Dict[str, Any] = {}
        self.parent = parent
        self.agents: Dict[str, AgentRuntime] = {}
        self.functions: Dict[str, FnDef] = {}

    def define(self, name: str, value: Any):
        self.variables[name] = value

    def get(self, name: str) -> Any:
        if name in self.variables:
            return self.variables[name]
        if name in self.agents:
            return self.agents[name]
        if name in self.functions:
            return self.functions[name]
        if self.parent:
            return self.parent.get(name)
        if name in BUILTINS:
            return BUILTINS[name]
        raise RuntimeError(f"Undefined variable or function: '{name}'")

    def set(self, name: str, value: Any):
        if name in self.variables:
            self.variables[name] = value
            return
        if self.parent:
            self.parent.set(name, value)
            return
        raise RuntimeError(f"Undefined variable: '{name}'")

    def define_agent(self, name: str, agent: AgentRuntime):
        self.agents[name] = agent

    def get_agent(self, name: str) -> AgentRuntime:
        if name in self.agents:
            return self.agents[name]
        if self.parent:
            return self.parent.get_agent(name)
        raise RuntimeError(f"Undefined agent: '{name}'")

    def define_function(self, name: str, fn: FnDef):
        self.functions[name] = fn

    def get_function(self, name: str) -> FnDef:
        if name in self.functions:
            return self.functions[name]
        if self.parent:
            return self.parent.get_function(name)
        raise RuntimeError(f"Undefined function: '{name}'")

    def _json_safe(self, value: Any) -> Any:
        if value is None or isinstance(value, (int, float, str, bool)):
            return value
        if isinstance(value, list):
            return [self._json_safe(v) for v in value]
        if isinstance(value, tuple):
            return [self._json_safe(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._json_safe(v) for k, v in value.items()}
        if isinstance(value, AgentRuntime):
            return {"__type__": "agent", "data": value.to_dict()}
        if isinstance(value, DurableActorRef):
            return {"__type__": "durable_actor_ref", "data": value.to_dict()}
        if isinstance(value, DurablePromise):
            return {"__type__": "durable_promise", "data": value.to_dict()}
        # Functions, Python callables and AST closures are intentionally not serialized.
        return {"__type__": "opaque", "repr": repr(value)}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "env_id": self.env_id,
            "variables": {k: self._json_safe(v) for k, v in self.variables.items()},
            "agents": {k: v.to_dict() for k, v in self.agents.items()},
            "parent": self.parent.to_dict() if self.parent else None,
        }

    @classmethod
    def _restore_value(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [cls._restore_value(v) for v in value]
        if isinstance(value, dict):
            if value.get("__type__") == "agent":
                return AgentRuntime.from_dict(value["data"])
            if value.get("__type__") == "durable_actor_ref":
                return DurableActorRef.from_dict(value["data"])
            if value.get("__type__") == "durable_promise":
                return DurablePromise.from_dict(value["data"])
            if value.get("__type__") == "opaque":
                return value.get("repr")
            return {k: cls._restore_value(v) for k, v in value.items()}
        return value

    @classmethod
    def from_dict(cls, data: Dict[str, Any], parent_env: Optional['Environment'] = None) -> 'Environment':
        parent = cls.from_dict(data["parent"]) if data.get("parent") else parent_env
        env = cls(parent=parent, env_id=data["env_id"])
        env.variables = {k: cls._restore_value(v) for k, v in data.get("variables", {}).items()}
        env.agents = {k: AgentRuntime.from_dict(v) for k, v in data.get("agents", {}).items()}
        return env


def _is_supported_immutable(value: Any) -> bool:
    """Return True for scalar values that are safe to pass through dream sandbox."""
    return value is None or isinstance(value, (str, int, float, bool))


def canonical_deepcopy(value: Any) -> Any:
    """Deep-copy Synapse-canonical primitives for dream sandbox isolation.

    This deliberately rejects arbitrary Python/runtime objects. Alpha3g P0.1
    only guarantees isolation for primitive Synapse values and containers.
    """
    if isinstance(value, list):
        return [canonical_deepcopy(item) for item in value]
    if isinstance(value, dict):
        return {canonical_deepcopy(key): canonical_deepcopy(item) for key, item in value.items()}
    if isinstance(value, set):
        return {canonical_deepcopy(item) for item in value}
    if isinstance(value, tuple):
        return tuple(canonical_deepcopy(item) for item in value)
    if _is_supported_immutable(value):
        return value
    raise DreamSandboxIsolationError(
        f"Unsupported mutable value in dream sandbox: {type(value).__name__}. "
        "Only list, dict, set, tuple, and primitive values are supported."
    )


class DreamSandboxEnvironment(Environment):
    """Strict dream sandbox with local writes and clone-on-first-read.

    Parent-scope assignment is shadowed locally, and supported mutable values read
    from the parent are cloned once and cached so aliases preserve identity within
    the dream without mutating the parent environment.
    """

    def __init__(self, parent: Environment, env_id: Optional[str] = None):
        super().__init__(parent=parent, env_id=env_id)
        self._clone_cache: Dict[int, Any] = {}

    def set(self, name: str, value: Any):
        self.variables[name] = value

    def get(self, name: str) -> Any:
        if name in self.variables:
            return self.variables[name]
        if name in self.agents:
            return self.agents[name]
        if name in self.functions:
            return self.functions[name]
        if not self.parent:
            if name in BUILTINS:
                return BUILTINS[name]
            raise RuntimeError(f"Undefined variable or function: '{name}'")

        value = self.parent.get(name)
        if isinstance(value, (list, dict, set, tuple)):
            obj_id = id(value)
            if obj_id not in self._clone_cache:
                self._clone_cache[obj_id] = canonical_deepcopy(value)
            self.variables[name] = self._clone_cache[obj_id]
            return self._clone_cache[obj_id]
        if _is_supported_immutable(value):
            return value
        raise DreamSandboxIsolationError(
            f"Cannot access parent variable '{name}' of type {type(value).__name__} "
            "inside dream sandbox. Custom objects and runtime handles are not "
            "supported in Alpha3g."
        )


class IntegrateOverlayEnvironment(Environment):
    """Environment adapter that routes env variable writes through StateOverlay.

    Alpha3g I2 uses this only for the opt-in LIVE-mode integrate skeleton. It
    isolates ordinary ``/env/<name>`` variable mutations from the parent
    environment while delegating functions, agents, and builtins to the parent.
    Internal bindings such as ``dream_result`` are local-only and do not enter
    the draft write-set.
    """

    def __init__(self, parent: Environment, overlay: StateOverlay, env_id: Optional[str] = None):
        super().__init__(parent=parent, env_id=env_id)
        self.overlay = overlay
        self._locals: Dict[str, Any] = {}

    def define_local(self, name: str, value: Any) -> None:
        self._locals[name] = value

    def define(self, name: str, value: Any):
        self.overlay.set(make_env_path(name), value)

    def set(self, name: str, value: Any):
        self.overlay.set(make_env_path(name), value)

    def get(self, name: str) -> Any:
        if name in self._locals:
            return self._locals[name]

        path = make_env_path(name)
        try:
            return self.overlay.get(path)
        except KeyError:
            # Only missing overlay/base env bindings fall through to the parent.
            # Serialization, discarded-overlay, and path errors must remain
            # fail-closed instead of being masked as function/builtin lookup.
            if self.parent:
                return self.parent.get(name)
            if name in BUILTINS:
                return BUILTINS[name]
            raise RuntimeError(f"Undefined variable or function: '{name}'")

    def define_agent(self, name: str, agent: AgentRuntime):
        if self.parent:
            self.parent.define_agent(name, agent)
            return
        super().define_agent(name, agent)

    def get_agent(self, name: str) -> AgentRuntime:
        if self.parent:
            return self.parent.get_agent(name)
        return super().get_agent(name)

    def define_function(self, name: str, fn: FnDef):
        if self.parent:
            self.parent.define_function(name, fn)
            return
        super().define_function(name, fn)

    def get_function(self, name: str) -> FnDef:
        if self.parent:
            return self.parent.get_function(name)
        return super().get_function(name)

class Interpreter:
    """
    === FRACTURE RUNTIME CONTRACT v1.5 MVP ===

    Base agent enters SUSPENDED_FRACTURED, its mailbox is frozen, and each
    sub-agent runs in an isolated ephemeral environment with a shadow soulprint.

    Death contract:
      NATURAL -> returned position, full consensus weight.
      ABORTED -> local assert failure, reduced/excluded consensus weight.
      KILLED  -> PolicyViolationException/OrphanedIdentityException, blocking signal; base survives.
      PANIC   -> unexpected runtime corruption; entire fracture aborts with FracturePanicException.

    Isolation boundaries for sub-agents:
      llm allowed; memory.write/forget/clear, send, migrate, evolve, integrate, dream, nested fracture forbidden.

    Durable events:
      identity_fractured, subagent_terminated, identity_integrated, fracture_panic.

    Replay semantics in MVP favor debuggability: fracture events remain explicit.
    v1.5.1 may add skip optimization from identity_fractured to identity_integrated.
    """
    def __init__(self):
        self.global_env = Environment()
        self.output_buffer = []
        self.llm_backend = LLMBackend()
        self.policies: Dict[str, List[Dict[str, Any]]] = {}
        self.claims: Dict[str, Dict[str, Any]] = {}
        self.consequences: Dict[str, Dict[str, Any]] = {}
        self.verification_results: List[Dict[str, Any]] = []
        self.memory_audit: List[Dict[str, Any]] = []
        self.mailboxes: Dict[str, List[Dict[str, Any]]] = {"global": []}
        self.actor_log: List[Dict[str, Any]] = []
        self.execution_history: List[Dict[str, Any]] = []
        # Non-authoritative observability events.  These intentionally do not
        # participate in replay cursors or exact execution_history assertions.
        self.telemetry_events: List[Dict[str, Any]] = []
        self.runtime_mode = RuntimeMode.LIVE
        self.replay_cursor = 0
        self.deterministic_side_effects = {"time", "random", "uuid"}
        self.checkpoints: List[Dict[str, Any]] = []
        self.policy_guard_depth = 0
        self.node_id = "local"
        self.routing_table: Dict[str, str] = {}
        self.outbound_packets: List[Dict[str, Any]] = []
        self.source_code: Optional[str] = None
        self.spawned_actors: Dict[str, Dict[str, Any]] = {}
        self.promises: Dict[str, Dict[str, Any]] = {}
        self.promise_routes: Dict[str, str] = {}
        self.promise_tombstones: Dict[str, str] = {}
        self.llm_context_cache: Dict[str, Dict[str, Any]] = {}
        self.intents: Dict[str, Dict[str, Any]] = {}
        self.intent_audit: List[Dict[str, Any]] = []
        self.observers: List[Dict[str, Any]] = []
        self._observer_depth = 0
        self.dream_depth = 0
        self.integrate_depth = 0
        # Alpha3g I2 skeleton is opt-in so legacy v1.4/v1.4.1 integrate
        # tests keep their historical event-emission behavior until the
        # implementation plan explicitly flips the default.
        self.integrate_i2_skeleton_enabled = False
        # P0.4.10 / SI5: Integrate hash/event paths remain on the legacy
        # Alpha3g local JSON profile by default. ``stable-canonical.v1`` is
        # opt-in only so existing Category B artifacts and replay histories are
        # not silently migrated.
        self.integrate_hash_profile = ALPHA3G_LOCAL_JSON_PROFILE
        self.integrate_i2_skeleton_depth = 0
        self.last_integrate_write_set: Optional[WriteSet] = None
        self._applied_integrate_replay_indices: Set[int] = set()
        self.evolve_depth = 0
        self.dream_audit: List[Dict[str, Any]] = []
        self.soulprint_audit: List[Dict[str, Any]] = []
        self.evolution_tickets: Dict[str, Dict[str, Any]] = {}
        self.fracture_depth = 0
        self.max_fracture_depth = 2
        self.fracture_debug_trace = False
        self.active_fractures: Dict[str, Dict[str, Any]] = {}
        self.subagent_registry: Dict[str, Dict[str, Any]] = {}
        self._subagent_stack: List[str] = []
        self.events_since_last_evolution: Dict[str, int] = {}
        self.resonance_cache: Dict[str, Dict[str, Any]] = {}
        self.collective_sessions: Dict[str, Dict[str, Any]] = {}
        self.consensus_tickets: Dict[str, Dict[str, Any]] = {}
        self.memory_palaces: Dict[str, MemoryPalace] = {}
        self.intention_cascades: Dict[str, Dict[str, Any]] = {}
        self.habits: Dict[str, Dict[str, Any]] = {}
        self._consensus_engine = ConsensusEngine()
        self._consensus_vote_source = None
        self._consensus_actor_method_enabled = False
        self._consensus_vote_source_registry_version = 0
        self._consensus_vote_query_depth = 0
        self._last_actor_method_vote_source = None
        self.affective_states: Dict[str, AffectiveState] = {}
        self.affective_events: List[Dict[str, Any]] = []
        self._applied_affective_resonance_events: set = set()
        self.threshold_registry = ThresholdRegistry()
        self.threshold_audit: List[Dict[str, Any]] = []
        self._threshold_action_depth = 0
        self.somatic_markers: Dict[str, Dict[str, Any]] = {}
        self.vm_snapshots: List[Dict[str, Any]] = []
        self.vm_checkpoints: Dict[str, Dict[str, Any]] = {}
        self.current_context: Optional[str] = None
        self.context_tracker = ContextTracker()
        self.energy_pool: Optional[EnergyPool] = None
        self.habit_registry = HabitRegistry()
        self.habit_evaluator = HabitEvaluator(
            context_tracker=self.context_tracker,
            affective_state_getter=self._current_pad_for_habits,
            energy_pool_getter=lambda: self.energy_pool,
        )
        self.habit_engine = HabitActivationEngine(
            registry=self.habit_registry,
            emit_fn=self._emit_habit_event,
            energy_pool_getter=lambda: self.energy_pool,
            body_executor=self._execute_habit_body,
        )
        self._habit_event_depth = 0
        self._energy_event_depth = 0
        self.energy_budget_default = 1000
        self.estimated_events_per_day = 1000
        self.storage_backend = None
        self.run_id = f"run-{uuid.uuid4().hex[:12]}"
        self.history_chain_seed = "synapse-v1.7"
        self.runtime = type("RuntimeFacade", (), {})()
        self.runtime.replay = ReplayEngine(
            history_getter=lambda: self.execution_history,
            runtime_mode_getter=lambda: self.runtime_mode,
            runtime_mode_setter=lambda mode: setattr(self, "runtime_mode", mode),
            replay_cursor_getter=lambda: self.replay_cursor,
            replay_cursor_setter=lambda cursor: setattr(self, "replay_cursor", cursor),
            live_mode=RuntimeMode.LIVE,
            replay_mode=RuntimeMode.REPLAY,
            builtins_registry=BUILTINS,
            hash_event_chain_fn=hash_event_chain,
            verify_event_chain_fn=verify_event_chain,
            history_chain_seed_getter=lambda: self.history_chain_seed,
        )
        self.runtime.governance = GovernanceEngine(
            policies_getter=lambda: self.policies,
            runtime_mode_getter=lambda: self.runtime_mode,
            replay_cursor_getter=lambda: self.replay_cursor,
            replay_cursor_setter=lambda cursor: setattr(self, "replay_cursor", cursor),
            live_mode=RuntimeMode.LIVE,
            replay_mode=RuntimeMode.REPLAY,
            peek_history_event_fn=self.peek_history_event,
            execution_history_getter=lambda: self.execution_history,
            actor_log_getter=lambda: self.actor_log,
            mailboxes_getter=lambda: self.mailboxes,
            mailboxes_setter=lambda value: setattr(self, "mailboxes", value),
            memory_audit_getter=lambda: self.memory_audit,
            global_env_getter=lambda: self.global_env,
            environment_factory=lambda parent: Environment(parent),
            execute_block_fn=self.execute_block,
            emit_runtime_event_fn=self.emit_runtime_event,
            actor_trust_record_fn=self.actor_trust_record,
            trust_at_least_fn=self.trust_at_least,
            current_mood_snapshot_fn=self.current_mood_snapshot,
            policy_guard_depth_getter=lambda: self.policy_guard_depth,
            policy_guard_depth_setter=lambda value: setattr(self, "policy_guard_depth", value),
            policy_violation_exception=PolicyViolationException,
            reject_exception=RejectException,
            resonance_privacy_exception=ResonancePrivacyException,
        )
        self.make_environment = lambda parent=None: Environment(parent)
        self.runtime.affective = AffectiveRuntime(
            host_getter=lambda: self,
            live_mode=RuntimeMode.LIVE,
            replay_mode=RuntimeMode.REPLAY,
            affective_isolation_exception=AffectiveIsolationViolation,
            orphaned_identity_exception=OrphanedIdentityException,
            frozen_mood_factory=lambda values=None: FrozenMoodSnapshot(values),
        )
        self.runtime.habit = HabitEngine(
            host_getter=lambda: self,
            live_mode=RuntimeMode.LIVE,
        )
        self.runtime.actor = ActorRuntime(
            host_getter=lambda: self,
            live_mode=RuntimeMode.LIVE,
            replay_mode=RuntimeMode.REPLAY,
        )
        self.runtime.vm = VMBridge(
            host_getter=lambda: self,
            live_mode=RuntimeMode.LIVE,
            replay_mode=RuntimeMode.REPLAY,
        )
        # Alpha.3-C runtime routing audit guard.  When an unsupported
        # statement falls back to HOST_EVAL, its children are executed by the
        # legacy tree-walker and must not be counted again recursively.
        self._vm_routing_audit_suppression_depth = 0
        self.Suspension = Suspension
        self.common_aspect_analyzers = {
            "emotional_tone": self._analyze_emotional_tone,
            "knowledge_level": self._analyze_knowledge_level,
            "humor": self._analyze_humor,
            "urgency": self._analyze_urgency,
            "trust_level": self._analyze_trust_level,
            "formality": self._analyze_formality,
            "creativity": self._analyze_creativity,
            "cognitive_style": self._analyze_cognitive_style,
            "value_alignment": self._analyze_value_alignment,
        }
        # Override print to capture output
        self.global_env.define("print", self._print)
        self.global_env.define("trust_at_least", self.trust_at_least)
        # Common symbolic constants for governance-oriented DSL blocks.
        for symbol in [
            "untrusted", "low", "medium", "high", "critical",
            "short_term", "long_term", "session", "project",
            "user_controlled", "system_controlled",
            "reversible", "irreversible",
            "deep", "shallow", "exploratory", "conservative",
            "rollback", "warn", "halt", "events", "seconds", "calls", "minute", "days", "never", "tagged", "untagged", "asc", "desc", "policy_violation", "affective_history"
        ]:
            self.global_env.define(symbol, symbol)

    registry_mutation_error_type = ConsensusVoteRegistryMutationError
    side_effect_error_type = ConsensusVoteSideEffectError

    def set_consensus_vote_source(self, source):
        if self._consensus_vote_query_depth > 0:
            raise ConsensusVoteRegistryMutationError(
                "VoteSource registry mutation is forbidden during consensus vote query"
            )
        self._consensus_vote_source = source
        self._consensus_vote_source_registry_version += 1

    def enable_actor_method_vote_source(self):
        if self._consensus_vote_query_depth > 0:
            raise ConsensusVoteRegistryMutationError(
                "VoteSource registry mutation is forbidden during consensus vote query"
            )
        self._consensus_actor_method_enabled = True
        self._consensus_vote_source_registry_version += 1

    def disable_actor_method_vote_source(self):
        if self._consensus_vote_query_depth > 0:
            raise ConsensusVoteRegistryMutationError(
                "VoteSource registry mutation is forbidden during consensus vote query"
            )
        self._consensus_actor_method_enabled = False
        self._consensus_vote_source_registry_version += 1

    def _select_consensus_vote_source(self):
        if self._consensus_vote_source is not None:
            return self._consensus_vote_source
        if self._consensus_actor_method_enabled:
            source = ActorMethodVoteSource(self)
            self._last_actor_method_vote_source = source
            return source
        return NULL_VOTE_SOURCE

    def _forbid_consensus_vote_side_effect(self, operation: str) -> None:
        if self._consensus_vote_query_depth > 0:
            raise ConsensusVoteSideEffectError(
                f"{operation} is forbidden during consensus vote query"
            )

    @staticmethod
    def _consensus_vote_forbidden_operation(node: Node) -> Optional[str]:
        operations = {
            "AgentDef": "agent definition",
            "AffectiveEventStmt": "affective operation",
            "AffectiveModulationStmt": "affective operation",
            "AffectiveResonanceStmt": "affective operation",
            "AffectiveStateDef": "affective operation",
            "AwaitExpr": "await",
            "ClaimDef": "claim mutation",
            "CheckStmt": "verification mutation",
            "CollectiveDreamStmt": "collective dream",
            "ConsolidateStmt": "memory consolidation",
            "CompileVmStmt": "vm compilation",
            "ConsequenceDef": "consequence mutation",
            "ContextBlock": "context block",
            "DeclareIntentStmt": "intent declaration",
            "DistributedConsensusStmt": "distributed consensus",
            "DreamBlock": "dream",
            "EnergyPoolDecl": "energy pool mutation",
            "EvolveStmt": "evolve",
            "FlowDef": "flow definition",
            "FnDef": "function definition",
            "FractureStmt": "fracture",
            "GovernedMemoryForget": "memory.forget",
            "GovernedMemoryWrite": "memory.write",
            "HabitStmt": "habit activation",
            "ImportStmt": "import",
            "IntegrateBlock": "integrate",
            "IntentDef": "intent declaration",
            "ImprintStmt": "memory imprint",
            "LLMCall": "llm_call",
            "MemoryAccess": "memory operation",
            "MemoryPalaceDef": "memory palace mutation",
            "MeasureIdentityCoherenceStmt": "identity measurement",
            "MigrateStmt": "migrate",
            "ObserveBlock": "observe registration",
            "PolicyDef": "policy definition",
            "ReceiveBlock": "receive",
            "RecallStmt": "memory recall",
            "ReflectBlock": "reflect",
            "ReflectOnFracturesStmt": "reflect",
            "ResonanceStmt": "resonate",
            "RunVmStmt": "vm execution",
            "SendStmt": "send",
            "SomaticMarkerStmt": "somatic mutation",
            "SpawnExpr": "spawn",
            "SoulprintDef": "soulprint mutation",
            "SubAgentDef": "sub-agent definition",
            "SuperposeBlock": "superpose",
            "SuspendExpr": "suspend",
            "SwarmFractureStmt": "swarm fracture",
            "AffectiveThresholdDef": "affective threshold mutation",
            "ThoughtBlock": "thought",
            "DebateBlock": "debate",
            "IntentionCascadeDef": "intention cascade mutation",
            "PlanWeaveStmt": "plan weave mutation",
            "VerifyBlock": "verification mutation",
        }
        if type(node).__name__ in operations:
            return operations[type(node).__name__]
        if type(node).__name__ == "AssignStmt":
            return "environment assignment"
        return None

    @staticmethod
    def _consensus_vote_stable_value(value: Any) -> Any:
        """Capture primitive structure without invoking host-object hooks."""
        if value is None or type(value) in {str, bool, int, float}:
            return value
        if type(value) is list:
            return ("list", tuple(Interpreter._consensus_vote_stable_value(item) for item in value))
        if type(value) is dict:
            string_keys = [key for key in value if type(key) is str]
            if len(string_keys) == len(value):
                return (
                    "dict",
                    tuple(
                        (key, Interpreter._consensus_vote_stable_value(value[key]))
                        for key in sorted(string_keys)
                    ),
                )
            return ("dict", len(value))
        value_type = type(value)
        return ("opaque", value_type.__module__, value_type.__qualname__)

    def _consensus_vote_side_effect_snapshot(self, participant_values) -> Dict[str, Any]:
        def map_state(mapping: Any) -> Any:
            return self._consensus_vote_stable_value(mapping)

        def mailbox_state() -> tuple:
            if type(self.mailboxes) is not dict:
                return ("mailboxes", self._consensus_vote_stable_value(self.mailboxes))
            entries = []
            for key in sorted(key for key in self.mailboxes if type(key) is str):
                mailbox = self.mailboxes[key]
                entries.append((key, len(mailbox) if type(mailbox) is list else None))
            return tuple(entries)

        def soulprint_state() -> tuple:
            states = []
            for participant in participant_values:
                if isinstance(participant, AgentRuntime):
                    raw = participant.__dict__.get("soulprint")
                    states.append((participant.name, self._consensus_vote_stable_value(raw)))
            return tuple(sorted(states))

        def affective_state() -> tuple:
            states = []
            for name in sorted(key for key in self.affective_states if type(key) is str):
                state = self.affective_states[name]
                states.append((name, self._consensus_vote_stable_value(state.__dict__.get("current"))))
            return tuple(states)

        energy_pool = self.energy_pool
        energy_state = None
        if energy_pool is not None:
            energy_state = (
                energy_pool.__dict__.get("max"),
                energy_pool.__dict__.get("initial"),
                energy_pool.__dict__.get("current"),
                energy_pool.__dict__.get("events_counter"),
                self._consensus_vote_stable_value(energy_pool.__dict__.get("mode")),
            )

        return {
            "execution_history": len(self.execution_history),
            "actor_log": len(self.actor_log),
            "outbound_packets": len(self.outbound_packets),
            "mailboxes": mailbox_state(),
            "promises": map_state(self.promises),
            "output_buffer": len(self.output_buffer),
            "telemetry_events": len(self.telemetry_events),
            "consensus_tickets": map_state(self.consensus_tickets),
            "memory_audit": len(self.memory_audit),
            "verification_results": len(self.verification_results),
            "intents": map_state(self.intents),
            "habits": map_state(self.habits),
            "threshold_audit": len(self.threshold_audit),
            "context_tracker": self._consensus_vote_stable_value(self.context_tracker.__dict__.get("stack")),
            "observers": len(self.observers),
            "participant_soulprints": soulprint_state(),
            "affective_states": affective_state(),
            "energy_pool": energy_state,
            "llm_context_cache": map_state(self.llm_context_cache),
            "spawned_actors": map_state(self.spawned_actors),
            "promise_routes": map_state(self.promise_routes),
            "promise_tombstones": map_state(self.promise_tombstones),
            "routing_table": map_state(self.routing_table),
            "registry_version": self._consensus_vote_source_registry_version,
        }

    def begin_consensus_vote_query(self, participant_values):
        snapshot = self._consensus_vote_side_effect_snapshot(participant_values)
        self._consensus_vote_query_depth += 1
        return snapshot

    def _verify_consensus_vote_side_effect_snapshot(self, snapshot, participant_values) -> None:
        if self._consensus_vote_source_registry_version != snapshot["registry_version"]:
            raise ConsensusVoteRegistryMutationError(
                "VoteSource registry mutation is forbidden during consensus vote query"
            )
        if self._consensus_vote_side_effect_snapshot(participant_values) != snapshot:
            raise ConsensusVoteSideEffectError(
                "vote collection mutated interpreter state"
            )

    def end_consensus_vote_query(self, snapshot, participant_values) -> None:
        try:
            self._verify_consensus_vote_side_effect_snapshot(snapshot, participant_values)
        finally:
            self._consensus_vote_query_depth -= 1

    def invoke_actor_vote_method(self, agent: AgentRuntime, method: FnDef, proposal_view: Any) -> Any:
        return self.call_function(method, [proposal_view], agent.env, agent=agent)

    def _print(self, *args):
        self._forbid_consensus_vote_side_effect("print")
        output = " ".join(str(a) for a in args)
        self.output_buffer.append(output)
        # CLI prints the final output buffer; avoid duplicate stdout emission here.

    def log(self, msg: str):
        self._forbid_consensus_vote_side_effect("output mutation")
        self.output_buffer.append(str(msg))

    def get_output(self) -> str:
        return "\n".join(self.output_buffer)

    def _is_runtime_routing_statement(self, node: Node) -> bool:
        """Return True for statement-level AST nodes audited by routing metrics.

        This deliberately excludes expressions so fallback accounting remains
        statement-level and cannot recursively double-count children.
        """
        node_type = type(node).__name__
        if node_type.endswith(("Stmt", "Def", "Decl", "Block")):
            return True
        return node_type in {
            "CompileVmStmt", "RunVmStmt", "EnergyPoolDecl", "ContextBlock",
            "HabitStmt", "AgentDef", "FlowDef", "IntentDef",
            "MemoryPalaceDef", "IntentionCascadeDef",
        }

    def _source_sha256(self) -> Optional[str]:
        if self.source_code is None:
            return None
        return hashlib.sha256(self.source_code.encode("utf-8")).hexdigest()

    def _current_runtime_program_hash(self) -> Optional[str]:
        source_hash = self._source_sha256()
        return f"sha256:{source_hash}" if source_hash else None

    def _audit_runtime_routing_decision(self, node: Node) -> bool:
        """Record one runtime routing decision for an executed statement.

        Returns True when the statement is routed to HOST_EVAL.  For HOST_EVAL
        decisions the caller suppresses child audits while the legacy
        tree-walker executes the node body.
        """
        if self._vm_routing_audit_suppression_depth > 0:
            return False
        if not self._is_runtime_routing_statement(node):
            return False

        decision = classify_ast_node_v22(node)
        node_type = decision.node
        program_hash = self._current_runtime_program_hash()

        if decision.route == "HOST_EVAL":
            self.runtime.vm.log_fallback(
                node,
                reason=decision.reason,
                compiler_phase="runtime_routing",
            )
            # Normalize fields that are specific to pre-CVM runtime routing.
            event = (self.telemetry_events or self.execution_history)[-1]
            event["program_hash"] = program_hash
            event["ip_at_fallback"] = None
            event["source_sha256"] = self._source_sha256()
            return True

        self.telemetry_events.append({
            "type": "vm_routing_cvm",
            "ast_node_type": node_type,
            "node": node_type,
            "route": "CVM",
            "reason": decision.reason,
            "compiler_phase": "runtime_routing",
            "program_hash": program_hash,
            "source_sha256": self._source_sha256(),
            "ip_at_fallback": None,
            "trace_id": self.current_trace_id(),
        })
        return False

    def interpret(self, node: Node) -> Any:
        if isinstance(node, Program):
            return self.visit_program(node)
        return self.evaluate(node, self.global_env)

    def visit_program(self, node: Program) -> Any:
        result = None
        for stmt in node.statements:
            result = self.evaluate(stmt, self.global_env)
            if not isinstance(stmt, AffectiveThresholdDef):
                self.process_affective_thresholds(self.global_env)

        # Synapse v0.2 convention: if a zero-argument main() exists, execute it
        # after top-level declarations have been loaded. This keeps scripts simple
        # while preserving top-level execution for existing programs.
        try:
            main_fn = self.global_env.get_function("main")
            if isinstance(main_fn, FnDef) and len(main_fn.params) == 0:
                result = self.call_function(main_fn, [], self.global_env)
        except RuntimeError:
            pass
        return result

    _PROMPT_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def _interpolate_prompt(self, template: str, env: Environment) -> str:
        """Substitute {identifier} placeholders from the current environment.

        Unknown identifiers are left untouched so that templates intended
        for downstream rendering (e.g. CVM envelope variables) survive.
        Doubled braces {{...}} escape interpolation.
        """
        if "{" not in template:
            return template

        sentinel_open, sentinel_close = "\x00OB\x00", "\x00CB\x00"
        text = template.replace("{{", sentinel_open).replace("}}", sentinel_close)

        def _sub(match: "re.Match[str]") -> str:
            name = match.group(1)
            try:
                value = env.get(name)
            except RuntimeError:
                return match.group(0)
            return str(value)

        text = self._PROMPT_VAR_RE.sub(_sub, text)
        return text.replace(sentinel_open, "{").replace(sentinel_close, "}")

    def evaluate(self, node: Node, env: Environment) -> Any:
        operation = self._consensus_vote_forbidden_operation(node)
        if operation is not None:
            self._forbid_consensus_vote_side_effect(operation)
        if self._consensus_vote_query_depth > 0:
            return self._evaluate_impl(node, env)
        _audit_fallback = self._audit_runtime_routing_decision(node)
        if _audit_fallback:
            self._vm_routing_audit_suppression_depth += 1
            try:
                return self._evaluate_impl(node, env)
            finally:
                self._vm_routing_audit_suppression_depth -= 1
        return self._evaluate_impl(node, env)

    def _evaluate_impl(self, node: Node, env: Environment) -> Any:
        if isinstance(node, LetStmt):
            value = self.evaluate(node.value, env)
            env.define(node.name, value)
            return value

        if isinstance(node, AssignStmt):
            if self.is_in_subagent():
                raise OrphanedIdentityException("assignment is forbidden inside sub-agent")
            if node.target == "soulprint" and self.evolve_depth <= 0:
                raise IdentityCrisisError("Protected soulprint cannot be directly overwritten; use evolve")
            if self.policy_guard_depth > 0:
                if node.target == "mood":
                    raise GuardMutationError("mood snapshot is read-only inside policy guard")
                raise PolicyCompilationError("Policy guard cannot assign to an existing environment variable")
            value = self.evaluate(node.value, env)
            env.set(node.target, value)
            return value

        if isinstance(node, MemberAssignStmt):
            obj = self.evaluate(node.target, env)
            if self._consensus_vote_query_depth > 0 and not isinstance(obj, (FrozenDict, FrozenList)):
                self._forbid_consensus_vote_side_effect("member assignment")
            if self.policy_guard_depth > 0 and isinstance(obj, FrozenMoodSnapshot):
                raise GuardMutationError("mood snapshot is read-only inside policy guard")
            value = self.evaluate(node.value, env)
            if isinstance(obj, dict):
                obj[node.member] = value
                return value
            try:
                setattr(obj, node.member, value)
            except GuardMutationError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Cannot assign member {node.member}: {exc}")
            return value

        if isinstance(node, IfStmt):
            condition = self.evaluate(node.condition, env)
            if self.is_truthy(condition):
                return self.execute_block(node.then_body, Environment(env))
            elif node.else_body:
                return self.execute_block(node.else_body, Environment(env))
            return None

        if isinstance(node, WhileStmt):
            result = None
            while self.is_truthy(self.evaluate(node.condition, env)):
                result = self.execute_block(node.body, Environment(env))
            return result

        if isinstance(node, ForStmt):
            iterable = self.evaluate(node.iterable, env)
            result = None
            for item in iterable:
                loop_env = Environment(env)
                loop_env.define(node.var, item)
                result = self.execute_block(node.body, loop_env)
            return result

        if isinstance(node, ReturnStmt):
            value = self.evaluate(node.value, env) if node.value else None
            raise ReturnException(value)

        if isinstance(node, ExprStmt):
            return self.evaluate(node.expr, env)

        if isinstance(node, AgentDef):
            trust_level = self.resolve_trust_level(node.trust_level, env) if getattr(node, "trust_level", None) is not None else "medium"
            trust_scope = []
            for item in getattr(node, "trust_scope", []) or []:
                trust_scope.append(item if isinstance(item, str) else self.evaluate(item, env))
            agent = AgentRuntime(node.name, node.model or "mock", node.memory, trust_level=trust_level, trust_scope=trust_scope)
            if getattr(node, "energy_pool", None):
                pool = self.evaluate_energy_pool(node.energy_pool, env)
                agent.energy_pool = pool
                self.energy_pool = pool
                env.define("energy_pool", pool.snapshot())
            soulprint = self.evaluate(node.soulprint, env) if getattr(node, "soulprint", None) else None
            if soulprint is not None:
                agent.soulprint = soulprint
                agent.identity_version = soulprint.get("version", "1.0")
            agent_env = Environment(env)
            agent_env.define("self", agent)
            if soulprint is not None:
                agent_env.define("soulprint", soulprint)
            for method in node.methods:
                if isinstance(method, FnDef):
                    agent_env.define_function(method.name, method)
            agent.env = agent_env
            env.define_agent(node.name, agent)
            env.define(node.name, agent)
            return agent

        if isinstance(node, FnDef):
            node.closure = env  # Capture current environment
            env.define_function(node.name, node)
            return node

        if isinstance(node, FlowDef):
            # Define first; execution happens when the flow is called.
            env.define(node.name, node)
            return node

        if isinstance(node, IntentDef):
            record = {k: self.evaluate(v, env) for k, v in node.fields.items()}
            record["name"] = node.name
            self.intents[node.name] = record
            env.define(node.name, {"type": "intent", **record})
            return record

        if isinstance(node, DeclareIntentStmt):
            return self.declare_intent(node.name, env)

        if isinstance(node, ObserveBlock):
            target = self.observe_target_string(node.target, env) if node.target else "*"
            record = {"target": target, "handlers": node.handlers}
            self.observers.append(record)
            env.define(f"observe:{target}:{len(self.observers)}", record)
            return record

        if isinstance(node, PolicyDef):
            rules = []
            for rule in node.rules:
                rules.append(self.evaluate(rule, env))
            policy_record = {
                "name": node.name,
                "target": self.evaluate(node.target, env) if getattr(node, "target", None) else None,
                "rules": rules,
                "guard_params": list(getattr(node, "guard_params", []) or []),
                "guard_body": list(getattr(node, "guard_body", []) or []),
                # v1.4.1 policy-as-code fields are kept as AST nodes or literals
                # and evaluated at enforcement time in the relevant runtime context.
                "trigger": getattr(node, "trigger", None),
                "cooldown": getattr(node, "cooldown", None),
                "max_delta": getattr(node, "max_delta", None),
                "guard_expr": getattr(node, "guard_expr", None),
                "require_approval": bool(getattr(node, "require_approval", False)),
                "fields": dict(getattr(node, "fields", {}) or {}),
            }
            self.policies[node.name] = policy_record
            env.define(node.name, {"type": "policy", **policy_record})
            return policy_record

        if isinstance(node, PolicyRule):
            return {"kind": node.kind, "value": self.evaluate(node.value, env)}

        if isinstance(node, VerifyBlock):
            results = []
            for check in node.checks:
                results.append(self.evaluate(check, env))
            return results

        if isinstance(node, CheckStmt):
            passed = self.is_truthy(self.evaluate(node.condition, env))
            message = self.evaluate(node.message, env) if node.message else "verification check failed"
            record = {"passed": passed, "message": message}
            self.verification_results.append(record)
            if not passed:
                raise RuntimeError(f"Verification failed: {message}")
            return record

        if isinstance(node, AssertStmt):
            return self.evaluate_assert(node, env)

        if isinstance(node, IntegrateBlock):
            self.forbid_subagent_side_effect("integrate")
            return self.evaluate_integrate(node, env)

        if isinstance(node, EnergyPoolDecl):
            pool = self.evaluate_energy_pool(node, env)
            self.energy_pool = pool
            env.define("energy_pool", pool.snapshot())
            return pool.snapshot()

        if isinstance(node, ContextBlock):
            return self.evaluate_context_block(node, env)

        if isinstance(node, ClaimDef):
            record = {
                "text": self.evaluate(node.text, env) if node.text else None,
                "evidence": self.evaluate(node.evidence, env) if node.evidence else None,
                "confidence": self.evaluate(node.confidence, env) if node.confidence else None,
            }
            self.claims[node.name] = record
            env.define(node.name, record)
            return record

        if isinstance(node, ConsequenceDef):
            record = {k: self.evaluate(v, env) for k, v in node.fields.items()}
            self.consequences[node.name] = record
            env.define(node.name, record)
            return record

        if isinstance(node, GovernedMemoryWrite):
            self.forbid_subagent_side_effect("memory.write")
            if self.integrate_i2_skeleton_depth > 0:
                raise IntegrateIsolationViolation("memory.write is forbidden inside Alpha3g I2 integrate skeleton")
            if self.dream_depth > 0:
                raise DreamIsolationViolation("dream cannot write memory; use integrate")
            if self.policy_guard_depth > 0:
                raise PolicyCompilationError("Policy guard cannot write memory")
            value = self.evaluate(node.value, env) if node.value else None
            fields = {k: self.evaluate(v, env) for k, v in node.fields.items()}
            if not fields.get("reason"):
                raise RuntimeError("Governed memory.write requires a reason field")
            agent = self.find_agent(env)
            agent.memory.write({"value": value, "governance": fields})
            self.memory_audit.append({"agent": agent.name, "value": value, "governance": fields})
            return value

        if isinstance(node, GovernedMemoryForget):
            self.forbid_subagent_side_effect("memory.forget")
            if self.integrate_i2_skeleton_depth > 0:
                raise IntegrateIsolationViolation("memory.forget is forbidden inside Alpha3g I2 integrate skeleton")
            if self.dream_depth > 0:
                raise DreamIsolationViolation("dream cannot forget memory; use integrate")
            if self.policy_guard_depth > 0:
                raise PolicyCompilationError("Policy guard cannot forget memory")
            key = self.evaluate(node.key, env) if node.key else None
            fields = {k: self.evaluate(v, env) for k, v in node.fields.items()}
            if not fields.get("reason"):
                raise RuntimeError("Governed memory.forget requires a reason field")
            agent = self.find_agent(env)
            removed = agent.memory.forget(key)
            event = {"type": "memory_forgotten", "agent": agent.name, "key": key, "governance": fields, "removed": removed}
            self.memory_audit.append(event)
            self.execution_history.append(event)
            self.emit_runtime_event(event, env)
            return removed

        if isinstance(node, SpawnExpr):
            self.forbid_subagent_side_effect("spawn")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("spawn is forbidden inside integrate transaction")
            return self.spawn_actor(node, env)

        if isinstance(node, AwaitExpr):
            self.forbid_subagent_side_effect("await")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("await is forbidden inside integrate transaction")
            raise RuntimeError("await is a durable suspension point; use interpret_async()")

        if isinstance(node, SuspendExpr):
            self.forbid_subagent_side_effect("suspend")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("suspend is forbidden inside integrate transaction")
            raise RuntimeError("suspend is a durable suspension point; use interpret_async()")

        if isinstance(node, MigrateStmt):
            self.forbid_subagent_side_effect("migrate")
            if self.dream_depth > 0:
                raise DreamIsolationViolation("dream cannot migrate actors; use integrate")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("migrate is forbidden inside integrate transaction")
            target = self.evaluate(node.target, env)
            raise RuntimeError(f"migrate is a durable suspension point; use interpret_async() for migration to {target}")

        if isinstance(node, SendStmt):
            self.forbid_subagent_side_effect("send")
            if self.dream_depth > 0:
                raise DreamIsolationViolation("dream cannot send actor messages; use integrate")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("send is forbidden inside integrate transaction")
            if self.policy_guard_depth > 0:
                raise PolicyCompilationError("Policy guard cannot send actor messages")
            receiver = self.evaluate(node.receiver, env)
            receiver_name = self.receiver_name(receiver)
            args = [self.evaluate(arg, env) for arg in node.args]
            sender = self.current_actor_name(env)
            return self.send_message(sender, receiver_name, node.method, args)

        if isinstance(node, ReceiveBlock):
            return self.runtime.actor.evaluate_receive(node, env)

        if isinstance(node, RejectStmt):
            message = self.evaluate(node.message, env) if node.message else "Policy rejected action"
            raise RejectException(str(message))

        if isinstance(node, ImportStmt):
            # В MVP импорты — заглушка
            env.define(node.alias or node.module, f"<module {node.module}>")
            return None

        # Выражения
        if isinstance(node, Literal):
            return node.value

        if isinstance(node, AffectivePadLiteral):
            return {"valence": float(node.valence), "arousal": float(node.arousal), "dominance": float(node.dominance)}

        if isinstance(node, DecayExpr):
            return {"value": node.value, "unit": node.unit, "original": node.original}

        if isinstance(node, Variable):
            return env.get(node.name)

        if isinstance(node, BinaryExpr):
            left = self.evaluate(node.left, env)
            right = self.evaluate(node.right, env)
            return self.eval_binary(node.op, left, right)

        if isinstance(node, UnaryExpr):
            operand = self.evaluate(node.operand, env)
            return self.eval_unary(node.op, operand)

        if isinstance(node, CallExpr):
            return self.eval_call(node, env)

        if isinstance(node, MemberAccess):
            obj = self.evaluate(node.obj, env)
            if isinstance(obj, AgentRuntime):
                if self._consensus_vote_query_depth > 0 and node.member in {"think", "memory"}:
                    self._forbid_consensus_vote_side_effect(f"AgentRuntime.{node.member}")
                # Доступ к методам агента
                fn_def = None
                # Ищем в окружении агента
                for key, val in env.functions.items():
                    if key == node.member:
                        fn_def = val
                        break
                if fn_def:
                    return fn_def
                # Или к памяти
                if node.member in ["memory", "think", "model"]:
                    return getattr(obj, node.member)
                raise RuntimeError(f"Agent '{obj.name}' has no member or method '{node.member}'")
            elif isinstance(obj, dict):
                return obj.get(node.member)
            elif isinstance(obj, list):
                if node.member == "length" or node.member == "size":
                    return len(obj)
                if hasattr(obj, node.member):
                    return getattr(obj, node.member)
            elif hasattr(obj, node.member):
                return getattr(obj, node.member)
            raise RuntimeError(f"Object has no member '{node.member}'")

        if isinstance(node, ListExpr):
            return [self.evaluate(e, env) for e in node.elements]

        if isinstance(node, DictExpr):
            return {k: self.evaluate(v, env) for k, v in node.pairs}

        if isinstance(node, PromptExpr):
            return self._interpolate_prompt(node.template, env)

        if isinstance(node, AssertStmt):
            return self.evaluate_assert(node, env)

        if isinstance(node, IntegrateBlock):
            self.forbid_subagent_side_effect("integrate")
            return self.evaluate_integrate(node, env)

        if isinstance(node, EnergyPoolDecl):
            pool = self.evaluate_energy_pool(node, env)
            self.energy_pool = pool
            env.define("energy_pool", pool.snapshot())
            return pool.snapshot()

        if isinstance(node, ContextBlock):
            return self.evaluate_context_block(node, env)

        if isinstance(node, LLMCall):
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("llm is forbidden inside integrate transaction")
            prompt = self.evaluate(node.prompt, env)
            if isinstance(prompt, PromptExpr):
                prompt = prompt.template
            prompt_text = str(prompt)
            prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

            event = self.next_history_event("llm_call")
            if event is not None:
                return event.get("result")

            result = self.llm_backend.complete(
                prompt_text, 
                model=node.model,
                temperature=node.temperature,
                max_tokens=node.max_tokens
            )
            self.llm_context_cache[prompt_hash] = {"prompt_hash": prompt_hash, "model": node.model, "result": result}
            self.execution_history.append({
                "type": "llm_call",
                "prompt": prompt_text,
                "prompt_hash": prompt_hash,
                "model": node.model,
                "temperature": node.temperature,
                "max_tokens": node.max_tokens,
                "result": result,
            })
            return result

        if isinstance(node, ThoughtBlock):
            steps = [self.evaluate(s, env) for s in node.steps]
            return self.llm_backend.thought_chain([str(s) for s in steps], node.aggregator)

        if isinstance(node, SuperposeBlock):
            branches = {}
            for branch in node.branches:
                branch_env = Environment(env)
                try:
                    result = self.execute_block(branch.body, branch_env)
                    branches[branch.name] = str(result)
                except ReturnException as e:
                    branches[branch.name] = str(e.value)
            return self.llm_backend.superpose(branches, node.selector)

        if isinstance(node, DebateBlock):
            return self.evaluate_debate(node, env)

        if isinstance(node, ReflectBlock):
            return self.evaluate_reflect(node, env)

        if isinstance(node, ResonanceStmt):
            return self.evaluate_resonate(node, env)

        if isinstance(node, ReflectOnFracturesStmt):
            return self.evaluate_reflect_on_fractures(node, env)

        if isinstance(node, MeasureIdentityCoherenceStmt):
            return self.evaluate_measure_identity_coherence(node, env)

        if isinstance(node, MemoryPalaceDef):
            return self.evaluate_memory_palace(node, env)

        if isinstance(node, ImprintStmt):
            return self.evaluate_imprint(node, env)

        if isinstance(node, RecallStmt):
            return self.evaluate_recall(node, env)

        if isinstance(node, IntentionCascadeDef):
            return self.evaluate_intention_cascade(node, env)

        if isinstance(node, PlanWeaveStmt):
            return self.evaluate_plan_weave(node, env)

        if isinstance(node, HabitStmt):
            return self.evaluate_habit(node, env)

        if isinstance(node, ConsolidateStmt):
            return self.evaluate_consolidate(node, env)

        if isinstance(node, AffectiveThresholdDef):
            return self.evaluate_affective_threshold_def(node, env)

        if isinstance(node, AffectiveStateDef):
            return self.evaluate_affective_state(node, env)

        if isinstance(node, AffectiveEventStmt):
            return self.evaluate_affective_event(node, env)

        if isinstance(node, AffectiveModulationStmt):
            return self.evaluate_affective_modulation(node, env)

        if isinstance(node, AffectiveResonanceStmt):
            return self.evaluate_affective_resonance(node, env)

        if isinstance(node, SomaticMarkerStmt):
            return self.evaluate_somatic_marker(node, env)

        if isinstance(node, CompileVmStmt):
            return self.evaluate_compile_vm(node, env)

        if isinstance(node, RunVmStmt):
            return self.evaluate_run_vm(node, env)

        if isinstance(node, CollectiveDreamStmt):
            self.forbid_integrate_i2_effect("collective_dream")
            return self.evaluate_collective_dream(node, env)

        if isinstance(node, DistributedConsensusStmt):
            self.forbid_integrate_i2_effect("distributed_consensus")
            return self.evaluate_distributed_consensus(node, env)

        if isinstance(node, SwarmFractureStmt):
            self.forbid_integrate_i2_effect("swarm_fracture")
            return self.evaluate_swarm_fracture(node, env)

        if isinstance(node, SoulprintDef):
            return self.evaluate_soulprint(node, env)

        if isinstance(node, DreamBlock):
            self.forbid_subagent_side_effect("dream")
            if self.integrate_i2_skeleton_depth > 0:
                raise IntegrateIsolationViolation("dream is forbidden inside Alpha3g I2 integrate skeleton")
            return self.evaluate_dream(node, env)

        if isinstance(node, EvolveStmt):
            self.forbid_subagent_side_effect("evolve")
            if self.integrate_i2_skeleton_depth > 0:
                raise IntegrateIsolationViolation("evolve is forbidden inside Alpha3g I2 integrate skeleton")
            return self.evaluate_evolve(node, env)

        if isinstance(node, FractureStmt):
            self.forbid_integrate_i2_effect("fracture")
            return self.evaluate_fracture(node, env)

        if isinstance(node, SubAgentDef):
            return node

        if isinstance(node, MemoryAccess):
            if node.operation in {"write", "clear", "forget"}:
                self.forbid_subagent_side_effect(f"memory.{node.operation}")
            agent = self.find_agent(env)

            if node.operation == "read":
                key = self.evaluate(node.value, env) if node.value else None
                return agent.memory.read(key)
            elif node.operation == "write":
                if self.integrate_i2_skeleton_depth > 0:
                    raise IntegrateIsolationViolation("memory.write is forbidden inside Alpha3g I2 integrate skeleton")
                if self.dream_depth > 0:
                    raise DreamIsolationViolation("dream cannot write memory; use integrate")
                if self.policy_guard_depth > 0:
                    raise PolicyCompilationError("Policy guard cannot write memory")
                value = self.evaluate(node.value, env) if node.value else None
                agent.memory.write(value)
                return value
            elif node.operation == "clear":
                if self.integrate_i2_skeleton_depth > 0:
                    raise IntegrateIsolationViolation("memory.clear is forbidden inside Alpha3g I2 integrate skeleton")
                if self.dream_depth > 0:
                    raise DreamIsolationViolation("dream cannot clear memory; use integrate")
                if self.policy_guard_depth > 0:
                    raise PolicyCompilationError("Policy guard cannot clear memory")
                agent.memory.clear()
                return None
            elif node.operation == "forget":
                if self.integrate_i2_skeleton_depth > 0:
                    raise IntegrateIsolationViolation("memory.forget is forbidden inside Alpha3g I2 integrate skeleton")
                if self.dream_depth > 0:
                    raise DreamIsolationViolation("dream cannot forget memory; use integrate")
                if self.policy_guard_depth > 0:
                    raise PolicyCompilationError("Policy guard cannot forget memory")
                key = self.evaluate(node.value, env) if node.value else None
                return agent.memory.forget(key)
            elif node.operation == "recall":
                pattern = self.evaluate(node.value, env) if node.value else ""
                return agent.memory.recall(str(pattern))
            else:
                raise RuntimeError(f"Unknown memory operation: {node.operation}")

        raise RuntimeError(f"Unknown node type: {type(node).__name__}")


    def evaluate_soulprint(self, node: SoulprintDef, env: Environment) -> Dict[str, Any]:
        return {
            "values": dict(node.values or {}),
            "memory_type": node.memory_type,
            "style": node.style,
            "version": node.version,
            "protected": bool(node.protected),
        }

    def _canonical_hash(self, value: Any) -> str:
        """Hash JSON-safe canonical data used by replay identity checks."""
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    def _ast_to_canonical_data(self, value: Any) -> Any:
        """Return a stable, JSON-safe AST/value representation for identity hashes."""
        if isinstance(value, Node):
            data = {"node_type": type(value).__name__}
            for key, val in sorted(value.__dict__.items()):
                if key in {"line", "column"}:
                    continue
                data[key] = self._ast_to_canonical_data(val)
            return data
        if isinstance(value, list):
            return [self._ast_to_canonical_data(v) for v in value]
        if isinstance(value, tuple):
            return [self._ast_to_canonical_data(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._ast_to_canonical_data(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
        if value is None or isinstance(value, (int, float, str, bool)):
            return value
        return repr(value)

    def _history_prefix_hash(self, prefix_len: Optional[int] = None) -> str:
        """Return the hash of the current history prefix, preserving empty-prefix semantics."""
        if prefix_len is None:
            prefix_len = self.replay_cursor if self.runtime_mode == RuntimeMode.REPLAY else len(self.execution_history)
        prefix = self.execution_history[: max(0, prefix_len)]
        chain = hash_event_chain(prefix, seed=self.runtime.replay.get_history_chain_seed())
        return chain[-1]["hash"] if chain else ""

    def _collect_read_variables(self, value: Any, local_names: Optional[Set[str]] = None) -> Set[str]:
        """Conservatively collect variable names read by a node tree.

        This keeps bound_variables_hash focused on values visible to the DreamBlock
        rather than hashing the entire enclosing environment.
        """
        if local_names is None:
            local_names = set()
        reads: Set[str] = set()
        if value is None:
            return reads
        if isinstance(value, Variable):
            if value.name not in local_names:
                reads.add(value.name)
            return reads
        if isinstance(value, LetStmt):
            reads.update(self._collect_read_variables(value.value, set(local_names)))
            local_names.add(value.name)
            return reads
        if isinstance(value, AssignStmt):
            reads.update(self._collect_read_variables(value.value, set(local_names)))
            return reads
        if isinstance(value, ForStmt):
            reads.update(self._collect_read_variables(value.iterable, set(local_names)))
            body_locals = set(local_names)
            body_locals.add(value.var)
            for stmt in value.body:
                reads.update(self._collect_read_variables(stmt, body_locals))
            return reads
        if isinstance(value, FnDef):
            if value.name:
                local_names.add(value.name)
            fn_locals = set(local_names) | set(value.params or [])
            for stmt in value.body:
                reads.update(self._collect_read_variables(stmt, fn_locals))
            return reads
        if isinstance(value, list):
            scoped = set(local_names)
            for item in value:
                reads.update(self._collect_read_variables(item, scoped))
            return reads
        if isinstance(value, tuple):
            for item in value:
                reads.update(self._collect_read_variables(item, set(local_names)))
            return reads
        if isinstance(value, dict):
            for item in value.values():
                reads.update(self._collect_read_variables(item, set(local_names)))
            return reads
        if isinstance(value, Node):
            for key, val in value.__dict__.items():
                if key in {"line", "column", "name", "params"}:
                    continue
                reads.update(self._collect_read_variables(val, set(local_names)))
            return reads
        return reads

    def _bound_variables_snapshot(self, node: DreamBlock, env: Environment) -> Dict[str, Any]:
        names = self._collect_read_variables(node.body)
        captured: Dict[str, Any] = {}
        for name in sorted(names):
            if name in {"dream"} or name in BUILTINS:
                continue
            try:
                captured[name] = env._json_safe(env.get(name))
            except Exception:
                # Unresolved names may be local definitions, builtins, or deliberately
                # opaque runtime values; keep the key stable without materializing it.
                captured[name] = {"__unresolved__": True}
        return captured

    def _dream_identity(self, node: DreamBlock, env: Environment, scenario: Any, config: Dict[str, Any], parent_history_hash: str) -> Dict[str, Any]:
        body_data = self._ast_to_canonical_data(node.body)
        bound_snapshot = self._bound_variables_snapshot(node, env)
        scenario_hash = self._canonical_hash(env._json_safe(scenario))
        config_hash = self._canonical_hash(env._json_safe(config))
        body_hash = self._canonical_hash(body_data)
        bound_variables_hash = self._canonical_hash(bound_snapshot)
        return {
            "scenario_hash": scenario_hash,
            "config_hash": config_hash,
            "body_hash": body_hash,
            "bound_variables_hash": bound_variables_hash,
            "parent_history_hash": parent_history_hash,
            "runtime_version": RUNTIME_VERSION,
        }

    def evaluate_dream(self, node: DreamBlock, env: Environment) -> Any:
        """Execute a sandboxed simulation with Alpha3g replay verification.

        LIVE records dream_completed with a deterministic dream_key and result_hash.
        REPLAY follows RFC-01 v2 / A2: execute the body to consume nested replay
        events in linear order, then consume and verify dream_completed. The
        canonical result returned to user code is always the recorded event result.
        """
        scenario = self.evaluate(node.scenario, env) if node.scenario else None
        config = {k: self.evaluate(v, env) for k, v in (node.config or {}).items()}
        parent_history_hash = self._history_prefix_hash()
        dream_key = self._dream_identity(node, env, scenario, config, parent_history_hash)

        sandbox = DreamSandboxEnvironment(env)
        sandbox.define("dream", {"scenario": scenario, "config": config})
        self.dream_depth += 1
        computed_result = None
        try:
            try:
                computed_result = self.execute_block(node.body, sandbox)
            except ReturnException as e:
                computed_result = e.value
        finally:
            self.dream_depth -= 1

        computed_result_hash = self._canonical_hash(env._json_safe(computed_result))
        result = computed_result

        if self.runtime_mode == RuntimeMode.REPLAY:
            event = self.next_history_event("dream_completed")
            if event is None:
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: expected dream_completed event not found")
            if event.get("dream_key") != dream_key:
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: dream_key mismatch")
            recorded_result = event.get("result")
            recorded_result_hash = self._canonical_hash(env._json_safe(recorded_result))
            event_result_hash = event.get("result_hash")
            if recorded_result_hash != event_result_hash:
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: recorded dream result_hash mismatch")
            if computed_result_hash != event_result_hash:
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: computed dream result_hash mismatch")
            if event.get("nested_event_policy") != "execute_and_verify":
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: unsupported dream nested_event_policy")
            result = recorded_result
        elif self.runtime_mode == RuntimeMode.LIVE:
            event = {
                "type": "dream_completed",
                "scenario": scenario,
                "config": config,
                "result": computed_result,
                "dream_key": dream_key,
                "result_hash": computed_result_hash,
                "nested_event_policy": "execute_and_verify",
            }
            self.execution_history.append(event)
            self.dream_audit.append(event)
            self.emit_runtime_event(event, env)

        if node.integration_clause:
            integration_env = Environment(env)
            integration_env.define("dream_result", result)
            integration_env.define("dream_insights", result)
            integration_env.define("dream_context", {"scenario": scenario, "config": config})
            return self.execute_block(node.integration_clause, integration_env)
        return result

    def evaluate_evolve(self, node: EvolveStmt, env: Environment) -> Any:
        target = self.evaluate(node.target, env) if node.target else self.find_agent(env)
        condition_value = self.evaluate(node.condition, env) if node.condition else True
        delay_node = getattr(node, "delay", None) or getattr(node, "trigger", None)
        delay_value = self.evaluate(delay_node, env) if delay_node is not None else None
        policy_ref = getattr(node, "policy_ref", None)
        policy = self.policies.get(policy_ref) if policy_ref else None
        if policy_ref and policy is None:
            raise RuntimeError(f"Undefined policy: '{policy_ref}'")

        if delay_value is not None and not self.is_truthy(condition_value):
            ticket = self.create_evolution_ticket(target, condition_value, delay_value, getattr(node, "delay_unit", "events"), policy_ref)
            return {"evolved": False, "ticket_created": ticket["ticket_id"]}

        if not self.is_truthy(condition_value):
            return {"evolved": False, "delay": delay_value}

        if policy:
            cooldown_node = policy.get("cooldown")
            if cooldown_node is not None:
                cooldown_value = self.evaluate(cooldown_node, env) if isinstance(cooldown_node, Node) else cooldown_node
                try:
                    cooldown_events = int(cooldown_value)
                except Exception:
                    cooldown_events = 0
                target_name = getattr(target, "name", str(target))
                events_since = self.events_since_last_evolution.get(target_name, cooldown_events)
                if events_since < cooldown_events:
                    remaining = cooldown_events - events_since
                    ticket = self.create_evolution_ticket(target, condition_value, remaining, "events", policy_ref)
                    event = {
                        "type": "evolution_deferred",
                        "reason": "cooldown",
                        "policy": policy_ref,
                        "target": target_name,
                        "events_remaining": remaining,
                        "ticket_id": ticket["ticket_id"],
                    }
                    if self.runtime_mode == RuntimeMode.LIVE:
                        self.execution_history.append(event)
                        self.soulprint_audit.append(event)
                        self.emit_runtime_event(event, env)
                    return {"evolved": False, "deferred": True, "ticket_id": ticket["ticket_id"], "events_remaining": remaining}

            self.enforce_evolution_policy(target, policy, env)
            if policy.get("require_approval"):
                event = {"type": "evolution_approved", "policy": policy_ref, "approved": True}
                if self.runtime_mode == RuntimeMode.LIVE:
                    self.execution_history.append(event)

        safety = self.evaluate(node.safety_guard, env) if getattr(node, "safety_guard", None) else None
        mutation_env = Environment(env)
        mutation_env.define("evolve_target", target)
        mutation_env.define("safety_guard", safety)
        if isinstance(target, AgentRuntime):
            mutation_env.define("soulprint", copy.deepcopy(getattr(target, "soulprint", {}) or {}))
        if policy:
            mutation_env.define("evolve_policy", policy)

        before_soulprint = copy.deepcopy(getattr(target, "soulprint", None)) if isinstance(target, AgentRuntime) else None
        log_snapshot = self.capture_durable_log_state()
        self.evolve_depth += 1
        result = None
        try:
            try:
                result = self.execute_block(node.mutations, mutation_env)
            except ReturnException as e:
                result = e.value

            # Whole-soulprint assignment sugar inside evolve: `soulprint = {...}` mutates the target identity atomically.
            if isinstance(target, AgentRuntime):
                try:
                    evolved_soulprint = mutation_env.get("soulprint")
                    if evolved_soulprint is not None:
                        target.soulprint = copy.deepcopy(evolved_soulprint)
                        if isinstance(evolved_soulprint, dict):
                            target.identity_version = evolved_soulprint.get("version", getattr(target, "identity_version", "1.0"))
                except RuntimeError:
                    pass

            if policy:
                self.enforce_evolution_max_delta(target, policy, before_soulprint, env)
        except Exception:
            if isinstance(target, AgentRuntime):
                target.soulprint = copy.deepcopy(before_soulprint)
                if isinstance(before_soulprint, dict):
                    target.identity_version = before_soulprint.get("version", getattr(target, "identity_version", "1.0"))
            self.restore_durable_log_state(log_snapshot)
            raise
        finally:
            self.evolve_depth -= 1

        event = {
            "type": "soulprint_evolved",
            "target": getattr(target, "name", str(target)),
            "delay": delay_value,
            "delay_unit": getattr(node, "delay_unit", "events"),
            "policy": policy_ref,
            "safety_guard": str(safety) if safety is not None else None,
            "result": result,
        }
        if self.runtime_mode == RuntimeMode.LIVE:
            self.execution_history.append(event)
            self.soulprint_audit.append(event)
            self.emit_runtime_event(event, env)
        if isinstance(target, AgentRuntime):
            self.events_since_last_evolution[getattr(target, "name", str(target))] = 0
        return event

    def evaluate_assert(self, node: AssertStmt, env: Environment) -> bool:
        if self.dream_depth > 0:
            raise DreamIsolationViolation("assert has no transactional context inside dream")
        passed = self.is_truthy(self.evaluate(node.condition, env))
        if not passed:
            message = self.evaluate(node.message, env) if node.message else "assertion failed"
            if self.integrate_depth > 0:
                raise IntegrateAssertionFailed(str(message))
            raise RuntimeError(f"Assert failed: {message}")
        return True

    def evaluate_integrate(self, node: IntegrateBlock, env: Environment) -> Any:
        if self.integrate_i2_skeleton_enabled:
            return self.evaluate_integrate_i2_skeleton(node, env)

        if self.dream_depth > 0:
            raise DreamIsolationViolation("integrate cannot run from inside dream sandbox")

        dream_result = self.evaluate(node.dream_result, env) if node.dream_result else None
        reason_value = self.evaluate(node.reason, env) if node.reason else None

        # v1.4.1: transaction snapshot covers both mutable state and durable logs.
        env_snapshot = self.capture_env_state(env)
        agent_snapshots = self.capture_agent_state(env)
        log_snapshot = self.capture_durable_log_state()

        self.integrate_depth += 1
        tx_env = Environment(env)
        tx_env.define("dream_result", dream_result)
        tx_env.define("dream_insights", dream_result)
        result = None
        try:
            try:
                result = self.execute_block(node.body, tx_env)
            except ReturnException as e:
                result = e.value
            event = {
                "type": "integrate_committed",
                "on_fail": node.on_fail,
                "reason": reason_value,
                "state_diff": self.compute_state_diff(env_snapshot, env, agent_snapshots),
            }
            if self.runtime_mode == RuntimeMode.LIVE:
                self.execution_history.append(event)
                self.emit_runtime_event(event, env)
            return result
        except IntegrateAssertionFailed as exc:
            event = {
                "type": "integrate_rollback",
                "on_fail": node.on_fail,
                "cause": str(exc),
                "reason": reason_value,
            }
            if node.on_fail in {"rollback", "halt"}:
                self.restore_env_state(env, env_snapshot)
                self.restore_agent_state(env, agent_snapshots)
                self.restore_durable_log_state(log_snapshot)
            if self.runtime_mode == RuntimeMode.LIVE:
                self.execution_history.append(event)
                self.actor_log.append(dict(event))
                self.emit_runtime_event(event, env)
            if node.on_fail == "halt":
                raise RuntimeError(f"Integrate halted: {exc}")
            if node.on_fail == "warn":
                self.output_buffer.append(f"Integrate warning: {exc}")
            return None
        except IntegrateIsolationViolation:
            self.restore_env_state(env, env_snapshot)
            self.restore_agent_state(env, agent_snapshots)
            self.restore_durable_log_state(log_snapshot)
            raise
        finally:
            self.integrate_depth -= 1

    def integrate_i2_forbidden_builtins(self) -> Set[str]:
        """Builtins forbidden by the Alpha3g I2 runtime nondeterminism barrier."""
        return {"print", "time", "random", "uuid"}

    def forbid_integrate_i2_effect(self, operation: str) -> None:
        """Fail closed for operations forbidden inside the Alpha3g I2 skeleton.

        P0.3.2 keeps INT-04 in guard form: promise/actor-producing
        operations must not be reachable inside I2, so no orphaned promise can
        be created before I3+ resource-cleanup infrastructure exists.
        """
        if self.integrate_i2_skeleton_depth > 0:
            raise IntegrateIsolationViolation(
                f"{operation} is forbidden inside Alpha3g I2 integrate skeleton"
            )

    def flatten_env_variables(self, env: Environment) -> Dict[str, Any]:
        """Return parent-first canonical data bindings for top-level /env state.

        Interpreter helpers such as ``print`` and other callables live in the
        global env for legacy lookup, but they are not state values and must not
        participate in integrate pre/post state hashes. Values that cannot enter
        the Alpha3g v1 local canonical subset are therefore excluded from the
        overlay base and remain reachable through parent lookup as functions or
        builtins.
        """
        chain: List[Environment] = []
        cursor: Optional[Environment] = env
        while cursor is not None:
            chain.append(cursor)
            cursor = cursor.parent
        flattened: Dict[str, Any] = {}
        for item in reversed(chain):
            for name, value in item.variables.items():
                try:
                    canonical_value_hash(value)
                except StateOverlayError:
                    continue
                flattened[name] = value
        return flattened

    def evaluate_integrate_i2_skeleton(self, node: IntegrateBlock, env: Environment) -> Any:
        """Alpha3g opt-in LIVE integrate path through I3.

        P0.3.3 extends the I2 skeleton with LIVE event emission and base-env
        application. It still does not run the REPLAY applier, emit CVM opcodes,
        touch actor runtime internals, or update golden fixtures. The legacy
        integrate implementation remains the default until the feature flag is
        flipped by a later release gate.
        """
        if self.dream_depth > 0:
            raise DreamIsolationViolation("integrate cannot run from inside dream sandbox")
        if self.integrate_i2_skeleton_depth > 0:
            raise IntegrateIsolationViolation("nested integrate is forbidden inside Alpha3g I2 integrate skeleton")
        if self.runtime_mode == RuntimeMode.REPLAY:
            return self.replay_integrate_i4_event(node, env)
        if self.runtime_mode != RuntimeMode.LIVE:
            raise IntegrateIsolationViolation("Alpha3g I2/I3 integrate path supports only LIVE and REPLAY modes")

        dream_result = self.evaluate(node.dream_result, env) if node.dream_result else None
        reason_value = self.evaluate(node.reason, env) if node.reason else None
        self.last_integrate_write_set = None

        base_state = {"env": self.flatten_env_variables(env), "memory": {}}
        overlay = StateOverlay(base_state, profile=self.integrate_hash_profile)
        pre_state_hash = overlay.canonical_hash()
        tx_env = IntegrateOverlayEnvironment(env, overlay)
        tx_env.define_local("dream_result", dream_result)
        tx_env.define_local("dream_insights", dream_result)

        self.integrate_depth += 1
        self.integrate_i2_skeleton_depth += 1
        try:
            try:
                result = self.execute_block(node.body, tx_env)
            except ReturnException as e:
                result = e.value
            post_state_hash = overlay.canonical_hash()
            write_set = overlay.commit()
            self.apply_integrate_write_set_to_env(write_set, env)
            self.last_integrate_write_set = write_set
            event = self.build_integrate_committed_event(
                write_set=write_set,
                pre_state_hash=pre_state_hash,
                post_state_hash=post_state_hash,
                reason=reason_value,
            )
            self.execution_history.append(event)
            self.emit_runtime_event(event, env)
            return result
        except Exception as exc:
            if isinstance(exc, StateOverlayError):
                reason_code = "commit_error"
            elif isinstance(exc, NondeterminismBarrierViolation):
                reason_code = "barrier_violation"
            elif isinstance(exc, IntegrateAssertionFailed):
                reason_code = "guard_violation"
            else:
                reason_code = "exception"
            event = self.build_integrate_aborted_event(
                overlay=overlay,
                pre_state_hash=pre_state_hash,
                exc=exc,
                reason_code=reason_code,
                reason=reason_value,
            )
            try:
                overlay.discard()
            except StateOverlayError:
                pass
            self.last_integrate_write_set = None
            self.execution_history.append(event)
            self.emit_runtime_event(event, env)
            raise
        finally:
            self.integrate_i2_skeleton_depth -= 1
            self.integrate_depth -= 1

    def integrate_schema_version(self) -> str:
        return "alpha3g.integrate.v1"

    def validate_integrate_hash_profile(self, profile: str) -> str:
        """Return a supported Integrate hash profile or fail closed.

        The default profile is ``alpha3g.local-json.v1``.  The approved
        ``stable-canonical.v1`` profile is opt-in for P0.4.10 / SI5 and must be
        explicitly selected by tests or future migration gates.
        """
        if profile in (ALPHA3G_LOCAL_JSON_PROFILE, STABLE_CANONICAL_PROFILE):
            return profile
        raise IntegrateIsolationViolation(f"unsupported integrate hash profile: {profile}")

    def active_integrate_hash_profile(self) -> str:
        return self.validate_integrate_hash_profile(self.integrate_hash_profile)

    def hash_integrate_payload(self, payload: Any, *, profile: Optional[str] = None) -> str:
        """Hash an Integrate event payload under the selected profile."""
        active_profile = self.validate_integrate_hash_profile(profile or self.active_integrate_hash_profile())
        return canonical_value_hash(payload, profile=active_profile)

    def sanitize_integrate_abort_message(self, exc: Exception) -> Optional[str]:
        """Return a deterministic, host-safe abort message for history events."""
        msg = str(exc)
        if not msg:
            return None
        # Avoid recording tracebacks, object repr addresses, or arbitrarily long
        # host-specific details. I3 keeps a small stable prefix only.
        msg = msg.replace("\n", " ").strip()
        if " at 0x" in msg or "Traceback" in msg:
            return None
        return msg[:240]

    def integrate_abort_reason_code(self, exc: Exception) -> str:
        if isinstance(exc, NondeterminismBarrierViolation):
            return "barrier_violation"
        if isinstance(exc, IntegrateAssertionFailed):
            return "guard_violation"
        if isinstance(exc, StateOverlayError):
            return "commit_error"
        return "exception"

    def integrate_barrier_operation(self, exc: Exception) -> Optional[str]:
        if not isinstance(exc, NondeterminismBarrierViolation):
            return None
        text = str(exc)
        # Expected form: "<operation> is forbidden inside ...".
        marker = " is forbidden"
        if marker in text:
            return text.split(marker, 1)[0].strip()
        return None

    def build_integrate_committed_event(
        self,
        *,
        write_set: WriteSet,
        pre_state_hash: str,
        post_state_hash: str,
        reason: Any,
    ) -> Dict[str, Any]:
        write_set_payload = write_set.to_list()
        profile = self.active_integrate_hash_profile()
        event = {
            "type": "integrate_committed",
            "target": "integrate",
            "schema_version": self.integrate_schema_version(),
            "pre_state_hash": pre_state_hash,
            "post_state_hash": post_state_hash,
            "write_set": write_set_payload,
            "write_set_hash": self.hash_integrate_payload(write_set_payload, profile=profile),
            "nondeterminism_barrier_violated": False,
        }
        if profile != ALPHA3G_LOCAL_JSON_PROFILE:
            event["hash_profile"] = profile
        if reason is not None:
            event["reason"] = reason
        return event

    def build_integrate_aborted_event(
        self,
        *,
        overlay: StateOverlay,
        pre_state_hash: str,
        exc: Exception,
        reason_code: str,
        reason: Any,
    ) -> Dict[str, Any]:
        overlay_summary = overlay.overlay_summary()
        profile = self.active_integrate_hash_profile()
        event = {
            "type": "integrate_aborted",
            "target": "integrate",
            "schema_version": self.integrate_schema_version(),
            "pre_state_hash": pre_state_hash,
            "abort_reason": reason_code,
            "exception_type": exc.__class__.__name__,
            "message": self.sanitize_integrate_abort_message(exc),
            "barrier_op": self.integrate_barrier_operation(exc),
            "overlay_summary": overlay_summary,
        }
        if profile != ALPHA3G_LOCAL_JSON_PROFILE:
            event["hash_profile"] = profile
        if reason is not None:
            event["reason"] = reason
        return event

    def env_chain_for_apply(self, env: Environment) -> List[Environment]:
        chain: List[Environment] = []
        cursor: Optional[Environment] = env
        while cursor is not None:
            chain.append(cursor)
            cursor = cursor.parent
        return chain

    def find_env_for_variable(self, env: Environment, name: str) -> Optional[Environment]:
        for item in self.env_chain_for_apply(env):
            if name in item.variables:
                return item
        return None

    def apply_integrate_write_set_to_env(self, write_set: WriteSet, env: Environment) -> None:
        """Atomically apply /env write-set entries to an Environment chain.

        P0.3.3 supports only /env entries. /memory and other namespaces remain
        future implementation work. If application fails, original variable
        bindings are restored before the exception is re-raised.
        """
        backups: list[tuple[Environment, str, bool, Any]] = []
        touched: set[tuple[int, str]] = set()
        try:
            for entry in write_set:
                if not entry.path.startswith("/env/"):
                    raise IntegrateIsolationViolation(
                        f"{entry.path} cannot be applied by Alpha3g I3 env-only commit"
                    )
                name = entry.path[len("/env/"):]
                holder = self.find_env_for_variable(env, name) or env
                key = (id(holder), name)
                if key not in touched:
                    backups.append((holder, name, name in holder.variables, holder.variables.get(name)))
                    touched.add(key)
                if entry.op == "replace":
                    holder.variables[name] = copy.deepcopy(entry.new_value)
                elif entry.op == "delete":
                    holder.variables.pop(name, None)
                else:
                    raise IntegrateIsolationViolation(f"unsupported integrate write_set op: {entry.op}")
        except Exception:
            for holder, name, existed, value in reversed(backups):
                if existed:
                    holder.variables[name] = value
                else:
                    holder.variables.pop(name, None)
            raise

    def integrate_current_state_hash(self, env: Environment, *, profile: Optional[str] = None) -> str:
        """Return canonical Integrate state hash for current env view."""
        active_profile = self.validate_integrate_hash_profile(profile or self.active_integrate_hash_profile())
        return StateOverlay(
            {"env": self.flatten_env_variables(env), "memory": {}},
            profile=active_profile,
        ).canonical_hash()

    def integrate_event_write_set_to_writeset(self, payload: Any) -> WriteSet:
        """Parse recorded event write_set payload into immutable WriteSet."""
        if not isinstance(payload, list):
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: integrate write_set must be a list")
        entries: list[WriteSetEntry] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: integrate write_set entry must be an object")
            path = item.get("path")
            if not isinstance(path, str):
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: integrate write_set entry missing path")
            if path in seen:
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: duplicate integrate write_set path")
            seen.add(path)
            op = item.get("op")
            if op not in {"replace", "delete"}:
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: unsupported integrate write_set op")
            entries.append(
                WriteSetEntry(
                    path=path,
                    granularity=item.get("granularity", "top_level"),
                    op=op,
                    old_value_hash=item.get("old_value_hash"),
                    new_value=item.get("new_value"),
                    new_value_hash=item.get("new_value_hash"),
                    value_profile=item.get("value_profile"),
                )
            )
        try:
            return WriteSet(tuple(entries))
        except ValueError as exc:
            raise ReplayIntegrityError(f"REPLAY_INTEGRITY_ERROR: {exc}") from exc

    def integrate_env_value_hash_for_path(
        self,
        env: Environment,
        path: str,
        *,
        profile: Optional[str] = None,
    ) -> Optional[str]:
        """Return current /env value hash for a recorded write-set path."""
        if not path.startswith("/env/"):
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: Alpha3g I4 supports only /env write_set paths")
        name = path[len("/env/"):]
        holder = self.find_env_for_variable(env, name)
        if holder is None:
            return None
        return canonical_value_hash(
            holder.variables[name],
            profile=self.validate_integrate_hash_profile(profile or self.active_integrate_hash_profile()),
        )

    def verify_integrate_write_set_against_env(
        self,
        write_set: WriteSet,
        env: Environment,
        *,
        profile: Optional[str] = None,
    ) -> None:
        """Verify old/new hashes around a recorded integrate write-set."""
        active_profile = self.validate_integrate_hash_profile(profile or self.active_integrate_hash_profile())
        for entry in write_set:
            current_hash = self.integrate_env_value_hash_for_path(env, entry.path, profile=active_profile)
            if current_hash != entry.old_value_hash:
                raise ReplayIntegrityError(
                    "REPLAY_INTEGRITY_ERROR: integrate pre-entry old_value_hash mismatch"
                )
            if entry.op == "replace":
                if canonical_value_hash(entry.new_value, profile=active_profile) != entry.new_value_hash:
                    raise ReplayIntegrityError(
                        "REPLAY_INTEGRITY_ERROR: integrate new_value_hash mismatch"
                    )

    def replay_integrate_i4_event(self, node: IntegrateBlock, env: Environment) -> Any:
        """Alpha3g I4 replay applier for recorded integrate events.

        REPLAY consumes recorded integrate events without executing the integrate
        body. Committed events apply their recorded write-set and verify pre /
        write-set / post hashes. Aborted events leave state unchanged and
        reproduce a deterministic abort exception. This is an in-run applier;
        durable crash-resume checkpointing remains out of scope for v1.
        """
        event = self.peek_next_history_event()
        if event is None:
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: expected integrate event not found")
        event_type = event.get("type")
        if event_type not in {"integrate_committed", "integrate_aborted"}:
            raise ReplayIntegrityError(
                f"REPLAY_INTEGRITY_ERROR: expected integrate event, got {event_type}"
            )
        event_index = self.replay_cursor
        if event_index in self._applied_integrate_replay_indices:
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: integrate event already applied in this replay run")
        event = self.next_history_event(event_type)
        if event is None:
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: expected integrate event not found")
        if event.get("schema_version") != self.integrate_schema_version():
            raise ReplayIntegrityError("EVENT_SCHEMA_UNSUPPORTED: integrate event schema version")

        event_profile = self.validate_integrate_hash_profile(event.get("hash_profile", ALPHA3G_LOCAL_JSON_PROFILE))
        pre_state_hash = self.integrate_current_state_hash(env, profile=event_profile)
        if pre_state_hash != event.get("pre_state_hash"):
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: integrate pre_state_hash mismatch")

        if event_type == "integrate_aborted":
            self._applied_integrate_replay_indices.add(event_index)
            self.last_integrate_write_set = None
            reason = event.get("abort_reason", "aborted")
            exc_type = event.get("exception_type", "IntegrateIsolationViolation")
            message = event.get("message") or f"recorded integrate abort: {reason}"
            if exc_type == "IntegrateIsolationViolation" or reason == "barrier_violation":
                raise IntegrateIsolationViolation(message)
            raise RuntimeError(message)

        write_set_payload = event.get("write_set")
        if self.hash_integrate_payload(write_set_payload, profile=event_profile) != event.get("write_set_hash"):
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: integrate write_set_hash mismatch")
        write_set = self.integrate_event_write_set_to_writeset(write_set_payload)
        self.verify_integrate_write_set_against_env(write_set, env, profile=event_profile)
        self.apply_integrate_write_set_to_env(write_set, env)
        post_state_hash = self.integrate_current_state_hash(env, profile=event_profile)
        if post_state_hash != event.get("post_state_hash"):
            raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: integrate post_state_hash mismatch")
        self._applied_integrate_replay_indices.add(event_index)
        self.last_integrate_write_set = write_set
        return None

    def capture_durable_log_state(self) -> Dict[str, int]:
        """Capture append-only/audit buffers so failed integrate transactions leave no dead events."""
        return {
            "execution_history_len": len(self.execution_history),
            "actor_log_len": len(self.actor_log),
            "memory_audit_len": len(self.memory_audit),
            "verification_results_len": len(self.verification_results),
            "output_buffer_len": len(self.output_buffer),
            "intent_audit_len": len(getattr(self, "intent_audit", [])),
            "dream_audit_len": len(getattr(self, "dream_audit", [])),
            "soulprint_audit_len": len(getattr(self, "soulprint_audit", [])),
            "outbound_packets_len": len(getattr(self, "outbound_packets", [])),
        }

    def restore_durable_log_state(self, snapshot: Dict[str, int]):
        """Trim all durable/audit/output tails created inside a rolled-back integrate block."""
        del self.execution_history[snapshot.get("execution_history_len", len(self.execution_history)):]
        del self.actor_log[snapshot.get("actor_log_len", len(self.actor_log)):]
        del self.memory_audit[snapshot.get("memory_audit_len", len(self.memory_audit)):]
        del self.verification_results[snapshot.get("verification_results_len", len(self.verification_results)):]
        del self.output_buffer[snapshot.get("output_buffer_len", len(self.output_buffer)):]
        del self.intent_audit[snapshot.get("intent_audit_len", len(getattr(self, "intent_audit", []))):]
        del self.dream_audit[snapshot.get("dream_audit_len", len(getattr(self, "dream_audit", []))):]
        del self.soulprint_audit[snapshot.get("soulprint_audit_len", len(getattr(self, "soulprint_audit", []))):]
        del self.outbound_packets[snapshot.get("outbound_packets_len", len(getattr(self, "outbound_packets", []))):]

    def safe_clone_value(self, value: Any) -> Any:
        # Keep runtime objects/callables by identity; deep-copy plain data only.
        if callable(value) or isinstance(value, (AgentRuntime, DurableActorRef, DurablePromise, FnDef, FlowDef)):
            return value
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    def capture_env_state(self, env: Environment) -> Dict[str, Any]:
        return {"variables": {k: self.safe_clone_value(v) for k, v in env.variables.items()}, "agents": list(env.agents.keys())}

    def restore_env_state(self, env: Environment, snapshot: Dict[str, Any]):
        env.variables = {k: self.safe_clone_value(v) for k, v in snapshot.get("variables", {}).items()}

    def capture_agent_state(self, env: Environment) -> Dict[str, Dict[str, Any]]:
        snapshots = {}
        cursor = env
        while cursor:
            for name, agent in cursor.agents.items():
                snapshots[name] = {
                    "memory": copy.deepcopy(agent.memory.to_dict()),
                    "soulprint": copy.deepcopy(getattr(agent, "soulprint", None)),
                    "identity_version": copy.deepcopy(getattr(agent, "identity_version", None)),
                }
            cursor = cursor.parent
        return snapshots

    def restore_agent_state(self, env: Environment, snapshots: Dict[str, Dict[str, Any]]):
        cursor = env
        while cursor:
            for name, agent in cursor.agents.items():
                if name in snapshots:
                    snap = snapshots[name]
                    agent.memory = Memory.from_dict(copy.deepcopy(snap.get("memory", {})))
                    agent.soulprint = copy.deepcopy(snap.get("soulprint"))
                    agent.identity_version = copy.deepcopy(snap.get("identity_version"))
            cursor = cursor.parent

    def compute_state_diff(self, env_snapshot: Dict[str, Any], env: Environment, agent_snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        before_vars = env_snapshot.get("variables", {})
        after_vars = env.variables
        changed_vars = [k for k in set(before_vars) | set(after_vars) if before_vars.get(k) != after_vars.get(k)]
        changed_agents = []
        cursor = env
        while cursor:
            for name, agent in cursor.agents.items():
                snap = agent_snapshots.get(name, {})
                if snap.get("memory") != agent.memory.to_dict() or snap.get("soulprint") != getattr(agent, "soulprint", None):
                    changed_agents.append(name)
            cursor = cursor.parent
        return {"changed_variables": changed_vars, "changed_agents": changed_agents}

    def create_evolution_ticket(self, target: Any, condition: Any, delay: Any, unit: str, policy_ref: Optional[str]) -> Dict[str, Any]:
        ticket_id = f"evo-{uuid.uuid4().hex[:12]}"
        try:
            delay_int = int(delay)
        except Exception:
            delay_int = 0
        ticket = {
            "ticket_id": ticket_id,
            "target": getattr(target, "name", str(target)),
            "condition": str(condition),
            "delay": delay_int,
            "delay_unit": unit,
            "events_remaining": delay_int if unit == "events" else 0,
            "status": "pending",
            "policy": policy_ref,
        }
        self.evolution_tickets[ticket_id] = ticket
        if self.runtime_mode == RuntimeMode.LIVE:
            self.execution_history.append({"type": "evolution_ticket_created", **ticket})
        return ticket

    def enforce_evolution_policy(self, target: Any, policy: Dict[str, Any], env: Environment):
        guard_expr = policy.get("guard_expr")
        if guard_expr is not None:
            guard_env = Environment(env)
            guard_env.define("soulprint", copy.deepcopy(getattr(target, "soulprint", {})))
            guard_env.define("target", target)
            if not self.is_truthy(self.evaluate(guard_expr, guard_env)):
                raise PolicyViolationException("Evolution guard invariant violated")
        return True

    def policy_numeric_value(self, value: Any, env: Environment) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, Node):
            value = self.evaluate(value, env)
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        try:
            return float(value)
        except Exception:
            return None

    def enforce_evolution_max_delta(self, target: Any, policy: Dict[str, Any], before_soulprint: Any, env: Environment):
        """Enforce `max_delta` as a hard postcondition over soulprint.values.* changes."""
        max_delta = self.policy_numeric_value(policy.get("max_delta"), env)
        if max_delta is None:
            return True
        if not isinstance(target, AgentRuntime):
            return True

        before_values = {}
        if isinstance(before_soulprint, dict):
            before_values = copy.deepcopy(before_soulprint.get("values", {}) or {})
        after_soulprint = getattr(target, "soulprint", {}) or {}
        after_values = after_soulprint.get("values", {}) if isinstance(after_soulprint, dict) else {}

        for key, after_val in dict(after_values).items():
            try:
                after_num = float(after_val)
            except Exception:
                continue
            before_num = float(before_values.get(key, 0.0))
            delta = abs(after_num - before_num)
            if delta > max_delta:
                raise PolicyViolationException(
                    f"Evolution of '{key}' violates max_delta: {delta} > {max_delta}"
                )
        return True

    def is_in_subagent(self) -> bool:
        return self.fracture_depth > 0 and bool(self._subagent_stack)

    def classify_subagent_death(self, operation_or_message: str) -> str:
        msg = str(operation_or_message).lower()
        if "memory" in msg:
            return "KILLED_MEMORY"
        if "send" in msg or "migrate" in msg or "network" in msg:
            return "KILLED_NETWORK"
        if "nested" in msg or "fracture" in msg or "depth" in msg:
            return "KILLED_NESTED"
        if "evolve" in msg or "evolution" in msg:
            return "KILLED_EVOLUTION"
        if "integrate" in msg or "integration" in msg:
            return "KILLED_INTEGRATION"
        return "KILLED_ISOLATION"

    def forbid_subagent_side_effect(self, operation: str):
        if self.is_in_subagent():
            death_type = self.classify_subagent_death(operation)
            raise OrphanedIdentityException(f"{operation} is forbidden inside sub-agent: {death_type}")

    def derive_fracture_id(self, base_name: str, node: FractureStmt) -> str:
        if getattr(node, "fracture_id", None):
            return str(node.fracture_id)
        source = self.source_code or ""
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
        seed = f"{base_name}:{getattr(node, 'line', 0)}:{getattr(node, 'column', 0)}:{source_hash}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def evaluate_fracture(self, node: FractureStmt, env: Environment) -> Any:
        """Evaluate fracture self with v1.5.1 polish semantics.

        Top-level fractures may integrate. Nested fractures are allowed up to
        depth 2, but may only return a position to their parent sub-agent.
        """
        if self.dream_depth > 0:
            raise DreamIsolationViolation("fracture is forbidden inside dream sandbox")
        if self.integrate_depth > 0:
            raise IntegrateIsolationViolation("fracture is forbidden inside integrate transaction")
        if self.fracture_depth >= self.max_fracture_depth:
            raise NestedFractureException(f"Maximum fracture depth {self.max_fracture_depth} exceeded")
        if self.fracture_depth > 0 and node.integration_clause:
            raise NestedFractureException("nested fracture cannot integrate; return a position to parent fracture")

        base_agent = self.evaluate(node.target, env) if node.target else self.find_agent(env)
        if not isinstance(base_agent, AgentRuntime):
            base_agent = self.find_agent(env)
        base_name = base_agent.name
        fracture_id = self.derive_fracture_id(base_name, node)

        if self.runtime_mode == RuntimeMode.REPLAY:
            replayed = self.replay_fracture_result(fracture_id)
            if replayed is not None:
                return replayed

        self.fracture_depth += 1
        event = {
            "type": "identity_fractured",
            "fracture_id": fracture_id,
            "base_agent": base_name,
            "subagents": [s.name for s in node.subagents],
            "consensus": node.consensus_strategy,
            "depth": self.fracture_depth,
        }
        if self.runtime_mode == RuntimeMode.LIVE:
            self.execution_history.append(event)
            self.actor_log.append(dict(event))
            self.emit_runtime_event(event, env)

        self.active_fractures[fracture_id] = {"base": base_name, "status": "active", "depth": self.fracture_depth}
        mailbox_backup = list(self.mailboxes.get(base_name, []))
        self.mailboxes[base_name] = []

        positions: Dict[str, Any] = {}
        deaths: Dict[str, str] = {}
        try:
            for sub_def in node.subagents:
                sub_id = f"{base_name}:{fracture_id}:{sub_def.name}"
                position, death_type, reason, summary = self.execute_subagent_compacted(
                    base_agent, sub_def, sub_id, fracture_id, env
                )
                positions[sub_def.name] = position
                deaths[sub_def.name] = death_type
                self.log_subagent_termination(fracture_id, sub_def.name, death_type, reason, position, sub_id, summary)
                self.terminate_subagent(sub_id)

            self.mailboxes[base_name] = mailbox_backup
            consensus_value = self.compute_fracture_consensus(node.consensus_strategy, positions, deaths, node.subagents, getattr(node, "consensus_config", None))
            fracture_result = {
                "fracture_id": fracture_id,
                "positions": positions,
                "deaths": deaths,
                "consensus": consensus_value,
            }

            integration_result = None
            if node.integration_clause:
                integrate_env = Environment(env)
                integrate_env.define("fracture_result", fracture_result)
                integrate_env.define("positions", positions)
                integrate_env.define("deaths", deaths)
                integrate_env.define("consensus", consensus_value)
                integration_result = self.execute_block(node.integration_clause, integrate_env)

            integrated_event = {
                "type": "identity_integrated",
                "fracture_id": fracture_id,
                "consensus": node.consensus_strategy,
                "consensus_value": self.global_env._json_safe(consensus_value),
                "positions": self.global_env._json_safe(positions),
                "deaths": deaths,
                "result": self.global_env._json_safe(consensus_value),
                "resulting_delta": {},
                "depth": self.fracture_depth,
            }
            if self.runtime_mode == RuntimeMode.LIVE:
                self.execution_history.append(integrated_event)
                self.actor_log.append(dict(integrated_event))
                self.emit_runtime_event(integrated_event, env)
            self.active_fractures[fracture_id]["status"] = "integrated"
            return integration_result if integration_result is not None else consensus_value
        finally:
            if fracture_id in self.active_fractures and self.active_fractures[fracture_id].get("status") == "active":
                self.mailboxes[base_name] = mailbox_backup
            self.fracture_depth = max(0, self.fracture_depth - 1)

    def execute_subagent_compacted(self, base_agent: AgentRuntime, sub_def: SubAgentDef, sub_id: str, fracture_id: str, env: Environment):
        """Run a sub-agent in an isolated ephemeral history buffer.

        Main durable history receives one compact subagent_terminated event.
        Full ephemeral events may be retained in debug mode only.
        """
        sub_env, _sub_agent = self.create_subagent(base_agent, sub_def, sub_id, fracture_id, env)
        main_history = self.execution_history
        main_actor_log = self.actor_log
        ephemeral_history: List[Dict[str, Any]] = []
        ephemeral_actor_log: List[Dict[str, Any]] = []
        self.execution_history = ephemeral_history
        self.actor_log = ephemeral_actor_log

        death_type = "NATURAL"
        reason = None
        position = None
        try:
            self._subagent_stack.append(sub_id)
            try:
                position = self.execute_block(sub_def.body, sub_env)
            except ReturnException as ret:
                position = ret.value
        except NestedFractureException as exc:
            death_type, reason, position = "KILLED_NESTED", str(exc), None
        except PolicyViolationException as exc:
            death_type, reason, position = self.classify_subagent_death(str(exc)), str(exc), None
        except OrphanedIdentityException as exc:
            death_type, reason, position = self.classify_subagent_death(str(exc)), str(exc), None
        except RuntimeError as exc:
            msg = str(exc)
            if msg.startswith("Assert failed:"):
                death_type, reason, position = "ABORTED", msg, None
            else:
                panic_event = {"type": "fracture_panic", "fracture_id": fracture_id, "base_agent": base_agent.name, "subagent": sub_def.name, "reason": msg}
                main_history.append(panic_event)
                self.execution_history = main_history
                self.actor_log = main_actor_log
                if self._subagent_stack and self._subagent_stack[-1] == sub_id:
                    self._subagent_stack.pop()
                raise FracturePanicException(f"Subagent {sub_def.name} panicked: {msg}")
        except Exception as exc:
            panic_event = {"type": "fracture_panic", "fracture_id": fracture_id, "base_agent": base_agent.name, "subagent": sub_def.name, "reason": str(exc)}
            main_history.append(panic_event)
            self.execution_history = main_history
            self.actor_log = main_actor_log
            if self._subagent_stack and self._subagent_stack[-1] == sub_id:
                self._subagent_stack.pop()
            raise FracturePanicException(f"Subagent {sub_def.name} panicked: {exc}")
        finally:
            if self._subagent_stack and self._subagent_stack[-1] == sub_id:
                self._subagent_stack.pop()

        summary = self.compact_ephemeral_history(ephemeral_history, ephemeral_actor_log, position, death_type)
        if self.fracture_debug_trace:
            summary["debug_trace"] = self.global_env._json_safe(ephemeral_history)
        self.execution_history = main_history
        self.actor_log = main_actor_log
        return position, death_type, reason, summary

    def compact_ephemeral_history(self, ephemeral_history: List[Dict[str, Any]], ephemeral_actor_log: List[Dict[str, Any]], position: Any, death_type: str) -> Dict[str, Any]:
        return {
            "llm_calls": sum(1 for e in ephemeral_history if e.get("type") == "llm_call"),
            "assertions": sum(1 for e in ephemeral_history if e.get("type") in {"assert_failed", "assertion_failed"}),
            "reflections": sum(1 for e in ephemeral_actor_log if e.get("type") == "reflect_query"),
            "events_total": len(ephemeral_history),
            "actor_events_total": len(ephemeral_actor_log),
            "final_position": self.global_env._json_safe(position),
            "death_type": death_type,
        }

    def create_subagent(self, base_agent: AgentRuntime, sub_def: SubAgentDef, sub_id: str, fracture_id: str, parent_env: Environment):
        shadow = copy.deepcopy(getattr(base_agent, "soulprint", {}) or {})
        for key, value in (sub_def.soulprint_override or {}).items():
            if isinstance(value, Node):
                value = self.evaluate(value, parent_env)
            if key == "values" and isinstance(value, dict):
                shadow.setdefault("values", {}).update(value)
            else:
                shadow[key] = value
        sub_agent = AgentRuntime(sub_id, getattr(base_agent, "model", "mock"), trust_level=getattr(base_agent, "trust_level", "medium"), trust_scope=list(getattr(base_agent, "trust_scope", []) or []))
        sub_agent.soulprint = shadow
        sub_agent.identity_version = shadow.get("version", getattr(base_agent, "identity_version", "1.0")) if isinstance(shadow, dict) else getattr(base_agent, "identity_version", "1.0")
        sub_env = Environment(parent_env)
        sub_env.define("self", sub_agent)
        sub_env.define("parent", base_agent)
        sub_env.define("soulprint", copy.deepcopy(shadow))
        if sub_def.focus:
            sub_env.define("focus", sub_def.focus)
        sub_env.define_agent(sub_id, sub_agent)
        self.subagent_registry[sub_id] = {"fracture_id": fracture_id, "base": base_agent.name, "name": sub_def.name, "status": "alive"}
        return sub_env, sub_agent

    def terminate_subagent(self, sub_id: str):
        meta = self.subagent_registry.get(sub_id)
        if meta:
            meta["status"] = "terminated"
        for key in list(self.mailboxes.keys()):
            if key == sub_id or key.startswith(sub_id + ":"):
                del self.mailboxes[key]

    def cleanup_fracture(self, fracture_id: str, base_name: str, mailbox_backup: List[Dict[str, Any]]):
        self.mailboxes[base_name] = mailbox_backup
        for sub_id, meta in list(self.subagent_registry.items()):
            if meta.get("fracture_id") == fracture_id:
                self.terminate_subagent(sub_id)
        if fracture_id in self.active_fractures:
            self.active_fractures[fracture_id]["status"] = "panic"

    def log_subagent_termination(self, fracture_id: str, sub_name: str, death_type: str, reason: Optional[str], position: Any, sub_id: str, ephemeral_summary: Optional[Dict[str, Any]] = None):
        event = {
            "type": "subagent_terminated",
            "fracture_id": fracture_id,
            "subagent": sub_name,
            "sub_id": sub_id,
            "death_type": death_type,
            "reason": reason,
            "position": self.global_env._json_safe(position),
            "ephemeral_history_length": int((ephemeral_summary or {}).get("events_total", 0)),
            "ephemeral_summary": ephemeral_summary or {"events_total": 0, "llm_calls": 0, "assertions": 0, "reflections": 0},
        }
        if self.runtime_mode == RuntimeMode.LIVE:
            self.execution_history.append(event)
            self.actor_log.append(dict(event))

    def compute_fracture_consensus(self, strategy: str, positions: Dict[str, Any], deaths: Dict[str, str], subagents: List[SubAgentDef], config: Optional[Node] = None) -> Any:
        if strategy == "unanimous":
            if all(deaths.get(sub.name) == "NATURAL" for sub in subagents):
                return {k: v for k, v in positions.items()}
            raise FracturePanicException("Unanimous consensus failed")
        if strategy == "majority":
            return {k: v for k, v in positions.items() if deaths.get(k) in {"NATURAL", "ABORTED"}}
        if strategy == "affective_weighted":
            if not isinstance(config, AffectiveWeightedConsensus):
                raise ConsensusBiasMissingError("affective_weighted consensus requires explicit bias mapping")
            branch_names = [sub.name for sub in subagents]
            default_bias = config.biases.get("Default")
            missing = [name for name in branch_names if name not in config.biases and default_bias is None]
            if missing:
                raise ConsensusBiasMissingError(f"Missing affective bias for branches: {', '.join(missing)}")
            mood_snapshot = self.current_mood_snapshot()
            base = 1.0 / max(len(branch_names), 1)
            raw_weights: Dict[str, float] = {}
            bias_values: Dict[str, float] = {}
            for name in branch_names:
                expr = config.biases.get(name, default_bias)
                bias = self.evaluate_bias_expr(expr, mood_snapshot) if expr is not None else 0.0
                bias_values[name] = bias
                raw_weights[name] = base + bias
            weights = self.normalize_weights(raw_weights)
            event = {
                "type": "affective_consensus_computed",
                "strategy": "affective_weighted",
                "pad_snapshot": mood_snapshot.to_dict(),
                "weights": weights,
                "raw_weights": raw_weights,
                "bias_values": bias_values,
                "bias_mapping": {k: type(v).__name__ for k, v in config.biases.items()},
            }
            if self.runtime_mode == RuntimeMode.LIVE:
                self.execution_history.append(event)
                self.actor_log.append(dict(event))
            return {"positions": dict(positions), "deaths": dict(deaths), "weights": weights}
        # weighted MVP: preserve all positions plus death metadata; KILLED remains a blocking signal.
        return {"positions": dict(positions), "deaths": dict(deaths)}

    def replay_fracture_result(self, fracture_id: str) -> Optional[Any]:
        """Default REPLAY optimization: skip ephemeral fracture events.

        The cursor is advanced to just after the matching identity_integrated
        event. If the expected shape is unavailable, return None so callers can
        fall back to full reconstruction.
        """
        start_idx = self.replay_cursor if self.runtime_mode == RuntimeMode.REPLAY else 0
        if self.runtime_mode == RuntimeMode.REPLAY and start_idx < len(self.execution_history):
            first = self.execution_history[start_idx]
            if first.get("type") == "identity_fractured" and first.get("fracture_id") != fracture_id:
                return None
        for idx in range(start_idx, len(self.execution_history)):
            event = self.execution_history[idx]
            if event.get("type") == "identity_integrated" and event.get("fracture_id") == fracture_id:
                if self.runtime_mode == RuntimeMode.REPLAY:
                    self.replay_cursor = idx + 1
                return event.get("consensus_value", event.get("result"))
            if idx > start_idx and event.get("type") == "identity_fractured":
                return None
        return None

    def evaluate_debate(self, node: DebateBlock, env: Environment) -> Any:
        """Run a deterministic multi-branch debate and return the judge result."""
        rounds_value = self.evaluate(node.rounds, env) if node.rounds else 1
        try:
            total_rounds = int(rounds_value)
        except Exception:
            raise RuntimeError("debate rounds must evaluate to an integer")
        if total_rounds < 1:
            raise RuntimeError("debate rounds must be >= 1")

        judge = self.evaluate(node.judge, env) if node.judge else "neutral_judge"
        store: Dict[str, List[Any]] = {branch.name: [] for branch in node.branches}

        for round_no in range(1, total_rounds + 1):
            for branch in node.branches:
                branch_env = Environment(env)
                branch_env.define("debate", DebateContext(round_no, store))
                try:
                    result = self.execute_block(branch.body, branch_env)
                except ReturnException as e:
                    result = e.value
                store.setdefault(branch.name, []).append(result)

        transcript = DebateContext(total_rounds, store).transcript()
        affective_instruction = ""
        if getattr(node, "affective_bias", None):
            mood_snapshot = self.current_mood_snapshot()
            vals = mood_snapshot.to_dict()
            parts = []
            if vals["valence"] < -0.3:
                parts.append("Be extra cautious and risk-aware in your judgment")
            if vals["arousal"] > 0.7:
                parts.append("The agent is in a high-intensity state; weight urgency appropriately")
            if vals["dominance"] < 0.3:
                parts.append("The agent has low confidence; consider requesting more information")
            if vals["valence"] > 0.3:
                parts.append("Be open to opportunity in your judgment")
            affective_instruction = "\nAffective guidance: " + "; ".join(parts) if parts else "\nAffective guidance: Maintain neutral arbitration."
        judge_prompt = (
            f"You are {judge}. Analyze the debate transcript and produce a final verdict.{affective_instruction}\n"
            f"Transcript:\n{transcript}"
        )
        result = self.evaluate(
            LLMCall(prompt=Literal(value=judge_prompt), model=str(judge), line=node.line, column=node.column),
            env,
        )
        event = {
            "type": "debate_completed",
            "judge": str(judge),
            "rounds": total_rounds,
            "branches": {k: [str(x) for x in v] for k, v in store.items()},
            "result": result,
            "affective_bias": bool(getattr(node, "affective_bias", None)),
            "judge_prompt": judge_prompt,
        }
        if self.runtime_mode == RuntimeMode.LIVE:
            self.execution_history.append(event)
            self.emit_runtime_event(event, env)
        return result

    def evaluate_reflect(self, node: ReflectBlock, env: Environment) -> Any:
        if getattr(node, "target", None) == "memory":
            agent = self.find_agent(env)
            return agent.memory.to_dict()
        if getattr(node, "target", None) == "values":
            agent = self.find_agent(env)
            return dict(getattr(agent, "soulprint", {}).get("values", {}))
        if getattr(node, "target", None) == "self":
            agent = self.find_agent(env)
            return {
                "name": agent.name,
                "model": agent.model,
                "trust_level": agent.trust_level,
                "soulprint": copy.deepcopy(getattr(agent, "soulprint", None)),
            }
        last_value = self.evaluate(node.last, env) if node.last else len(self.execution_history)
        try:
            n = int(last_value)
        except Exception:
            raise RuntimeError("reflect last must evaluate to an integer")
        events = list(self.execution_history[-n:]) if n >= 0 else list(self.execution_history)
        if node.filter_condition is not None:
            filtered = []
            for event in events:
                event_env = Environment(env)
                event_env.define("event", event)
                for key, value in event.items():
                    event_env.define(str(key), value)
                if self.is_truthy(self.evaluate(node.filter_condition, event_env)):
                    filtered.append(event)
            events = filtered
        result = [dict(e) for e in events]
        audit = {"type": "reflect_query", "count": len(result)}
        # Reflection is intentionally audit-only: it does not feed replay decisions.
        self.actor_log.append(audit)
        return result



    # --- v1.6 Resonance & inter-subjectivity ---
    def evaluate_resonate(self, node: ResonanceStmt, env: Environment) -> Dict[str, Any]:
        """Read-only inter-subjective calibration against recent durable history."""
        if self.dream_depth > 0:
            raise RuntimeError("ResonanceViolation: resonate is forbidden inside dream")
        if self.fracture_depth > 0:
            raise OrphanedIdentityException("resonate external calibration forbidden inside sub-agent")

        target = self.evaluate(node.target, env) if node.target else "@user"
        target_str = self.actor_name_value(target)
        self.require_cross_agent_resonance_permission(target_str)
        window_value = self.evaluate(node.window, env) if node.window else 50
        try:
            window = int(window_value)
        except Exception:
            raise RuntimeError("ResonanceTypeError: window must evaluate to integer")
        if window < 1:
            window = 1
        aspects = list(node.aspects or ["emotional_tone", "knowledge_level"])

        history_slice = self.execution_history[-window:]
        history_hash = hashlib.sha256(json.dumps(history_slice, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        aspects_hash = hashlib.sha256("|".join(sorted(aspects)).encode("utf-8")).hexdigest()[:8]
        cache_key = f"{target_str}:{aspects_hash}:{window}:{history_hash}"
        if cache_key in self.resonance_cache:
            profile = copy.deepcopy(self.resonance_cache[cache_key])
        else:
            profile = self.compute_resonance_profile(target_str, aspects, window, env)
            self.resonance_cache[cache_key] = copy.deepcopy(profile)

        env.define(node.binding, profile)
        if self.runtime_mode == RuntimeMode.LIVE:
            event = {
                "type": "resonance_profile_computed",
                "target": target_str,
                "aspects": aspects,
                "profile": copy.deepcopy(profile),
                "cache_key": cache_key,
                "depth": node.depth,
            }
            self.execution_history.append(event)
            self.actor_log.append(dict(event))
            self.emit_runtime_event(event, env)
        return profile

    def compute_resonance_profile(self, target: str, aspects: List[str], window: int, env: Environment) -> Dict[str, Any]:
        relevant = self.get_relevant_history(target, window)
        aspect_results: Dict[str, Dict[str, Any]] = {}
        for aspect in aspects:
            analyzer = self.common_aspect_analyzers.get(aspect)
            if analyzer:
                aspect_results[aspect] = analyzer(relevant, env)
            else:
                aspect_results[aspect] = {"value": None, "confidence": 0.0, "error": "unknown_aspect"}
        drift_vector = self.compute_drift_vector(aspect_results, target)
        confidence_values = [r.get("confidence", 0.0) for r in aspect_results.values()]
        overall = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0
        return {
            "target": target,
            "aspects": aspect_results,
            "recommendation": self.generate_resonance_recommendation(aspect_results, drift_vector),
            "overall_confidence": overall,
            "drift_detected": any(abs(v) > 0.15 for v in drift_vector.values()),
            "drift_vector": drift_vector,
            "window": window,
            "event_count": len(relevant),
        }

    def get_relevant_history(self, target: str, window: int) -> List[Dict[str, Any]]:
        clean_target = target.replace("@", "")
        result = []
        for event in reversed(self.execution_history):
            if len(result) >= window:
                break
            blob = json.dumps(event, default=str)
            if target in blob or clean_target in blob or target == "@user":
                result.append(event)
        result.reverse()
        return result

    def _message_texts(self, events: List[Dict[str, Any]]) -> List[str]:
        texts = []
        for event in events:
            if event.get("type") in {"message_sent", "message_received"}:
                msg = event.get("message") or event
                texts.append(str(msg.get("payload") or msg.get("message") or msg)) if isinstance(msg, dict) else texts.append(str(msg))
            else:
                # allow synthetic test/user events to contribute
                for key in ("payload", "message", "text", "prompt"):
                    if key in event:
                        texts.append(str(event.get(key)))
                        break
        return texts

    def _analyze_emotional_tone(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        text = " ".join(self._message_texts(events)).lower()
        if any(x in text for x in ["срочно", "паника", "авария", "urgent", "!!!", "не работает"]):
            return {"value": "anxious", "confidence": 0.85}
        if "?" in text:
            return {"value": "curious", "confidence": 0.7}
        return {"value": "neutral", "confidence": 0.6 if text else 0.5}

    def _analyze_knowledge_level(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        text = " ".join(self._message_texts(events)).lower()
        terms = ["ast", "parser", "lexer", "runtime", "рантайм", "детерминизм", "replay", "event sourcing", "governance", "fracture"]
        score = sum(1 for term in terms if term in text)
        if score >= 3:
            return {"value": "expert", "confidence": 0.9}
        if score >= 1:
            return {"value": "intermediate", "confidence": 0.75}
        return {"value": "beginner" if text else "unknown", "confidence": 0.6 if text else 0.0}

    def _analyze_humor(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        text = " ".join(self._message_texts(events)).lower()
        markers = ["lol", "хаха", "шутка", "😂", "😄"]
        score = min(sum(1 for m in markers if m in text) / 3.0, 1.0)
        return {"value": round(score, 2), "confidence": 0.65}

    def _analyze_urgency(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        text = " ".join(self._message_texts(events)).lower()
        markers = ["срочно", "asap", "немедленно", "urgent", "!!!"]
        score = min(sum(1 for m in markers if m in text) / 2.0, 1.0)
        return {"value": round(score, 2), "confidence": 0.8}

    def _analyze_trust_level(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        violations = sum(1 for e in events if e.get("type") == "policy_violation")
        approvals = sum(1 for e in events if e.get("type") in {"evolution_approved", "policy_evaluated"})
        score = approvals / (approvals + violations + 1)
        return {"value": round(score, 2), "confidence": 0.7}

    def _analyze_formality(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        text = " ".join(self._message_texts(events))
        formal_markers = ["пожалуйста", "благодарю", "please", "therefore"]
        score = min(sum(1 for m in formal_markers if m in text.lower()) / 2.0 + 0.5, 1.0)
        return {"value": round(score, 2), "confidence": 0.55}

    def _analyze_creativity(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        text = " ".join(self._message_texts(events)).lower()
        markers = ["идея", "создадим", "новый", "dream", "fracture", "resonance", "архитектур"]
        score = min(sum(1 for m in markers if m in text) / 4.0, 1.0)
        return {"value": round(score, 2), "confidence": 0.6}

    def aspect_to_numeric(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        mapping = {
            "expert": 1.0, "intermediate": 0.5, "beginner": 0.0, "unknown": 0.25,
            "anxious": 0.85, "urgent": 0.9, "neutral": 0.5, "curious": 0.35,
        }
        return mapping.get(str(value), 0.5)


    def _analyze_cognitive_style(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        text = " ".join(self._message_texts(events)).lower()
        if any(x in text for x in ["архитект", "spec", "runtime", "рантайм", "инвариант", "contract"]):
            return {"value": "architectural", "confidence": 0.82}
        if any(x in text for x in ["пример", "demo", "быстро"]):
            return {"value": "pragmatic", "confidence": 0.7}
        return {"value": "balanced", "confidence": 0.55}

    def _analyze_value_alignment(self, events: List[Dict[str, Any]], env: Environment) -> Dict[str, Any]:
        violations = sum(1 for e in events if e.get("type") in {"policy_violation", "fracture_panic"})
        approvals = sum(1 for e in events if e.get("type") in {"policy_evaluated", "identity_integrated", "integrate_committed", "collective_dream_consensus_reached"})
        score = approvals / (approvals + violations + 1)
        return {"value": round(score, 3), "confidence": 0.72}

    def compute_drift_vector(self, current: Dict[str, Dict[str, Any]], target: str) -> Dict[str, float]:
        previous = None
        for event in reversed(self.execution_history):
            if event.get("type") == "resonance_profile_computed" and event.get("target") == target:
                previous = event.get("profile", {}).get("aspects", {})
                break
        drift: Dict[str, float] = {}
        for aspect, result in current.items():
            val = self.aspect_to_numeric(result.get("value"))
            if previous and aspect in previous:
                drift[aspect] = round(val - self.aspect_to_numeric(previous[aspect].get("value")), 3)
            else:
                drift[aspect] = 0.0
        return drift

    def generate_resonance_recommendation(self, aspects: Dict[str, Dict[str, Any]], drift: Dict[str, float]) -> str:
        rec = []
        if aspects.get("emotional_tone", {}).get("value") == "anxious":
            rec.append("increase reassurance and reduce ambiguity")
        if aspects.get("knowledge_level", {}).get("value") == "beginner":
            rec.append("reduce jargon and add examples")
        if aspects.get("urgency", {}).get("value", 0) > 0.75:
            rec.append("prioritize concrete next actions")
        if aspects.get("knowledge_level", {}).get("value") == "expert":
            rec.append("use precise systems terminology")
        return "; ".join(rec) if rec else "preserve baseline communication policy"

    def evaluate_reflect_on_fractures(self, node: ReflectOnFracturesStmt, env: Environment) -> List[Dict[str, Any]]:
        last = self.evaluate(node.last, env) if node.last else 10
        try:
            last = int(last)
        except Exception:
            raise RuntimeError("reflect on fractures last must be integer")
        events = [e for e in self.execution_history if e.get("type") in {"identity_fractured", "identity_integrated", "subagent_terminated", "fracture_panic"}]
        events = events[-last:]
        if node.filter_condition is not None:
            filtered = []
            for event in events:
                fenv = Environment(env)
                fenv.define("event", event)
                for k, v in event.items():
                    fenv.define(str(k), v)
                if self.is_truthy(self.evaluate(node.filter_condition, fenv)):
                    filtered.append(event)
            events = filtered
        return [copy.deepcopy(e) for e in events]

    def evaluate_measure_identity_coherence(self, node: MeasureIdentityCoherenceStmt, env: Environment) -> Dict[str, Any]:
        window = self.evaluate(node.window, env) if node.window else 100
        try:
            window = int(window)
        except Exception:
            raise RuntimeError("measure identity_coherence window must be integer")
        metrics = node.metrics or ["soulprint_stability", "fracture_consensus_rate"]
        results: Dict[str, Any] = {}
        for metric in metrics:
            if metric in {"soulprint_stability", "stability"}:
                results[metric] = self.measure_soulprint_stability(window)
            elif metric in {"fracture_consensus_rate", "consensus_rate"}:
                results[metric] = self.measure_fracture_consensus_rate(window)
            elif metric == "resonance_drift":
                results[metric] = self.measure_resonance_drift(window)
            else:
                results[metric] = {"error": "unknown_metric", "score": 0.5}
        scores = [v.get("score", 0.5) for v in results.values() if isinstance(v, dict)]
        profile = {"score": round(sum(scores) / len(scores), 3) if scores else 0.5, "metrics": results, "window": window}
        env.define(node.binding, profile)
        return profile

    def measure_soulprint_stability(self, window: int) -> Dict[str, Any]:
        events = [e for e in self.execution_history[-window:] if e.get("type") == "soulprint_evolved"]
        score = max(0.0, 1.0 - len(events) * 0.15)
        return {"score": round(score, 3), "mutation_events": len(events)}

    def measure_fracture_consensus_rate(self, window: int) -> Dict[str, Any]:
        integrations = [e for e in self.execution_history[-window:] if e.get("type") == "identity_integrated"]
        panics = [e for e in self.execution_history[-window:] if e.get("type") == "fracture_panic"]
        if not integrations:
            return {"score": 1.0, "fractures": 0, "panics": len(panics)}
        return {"score": round(max(0.0, 1.0 - len(panics) / len(integrations)), 3), "fractures": len(integrations), "panics": len(panics)}

    def measure_resonance_drift(self, window: int) -> Dict[str, Any]:
        events = [e for e in self.execution_history[-window:] if e.get("type") == "resonance_profile_computed"]
        if len(events) < 2:
            return {"score": 1.0, "profiles": len(events), "average_step_drift": 0.0}
        total = 0.0
        count = 0
        for ev in events[1:]:
            for val in ev.get("profile", {}).get("drift_vector", {}).values():
                total += abs(val)
                count += 1
        avg = total / count if count else 0.0
        return {"score": round(max(0.0, 1.0 - avg * 1.5), 3), "profiles": len(events), "average_step_drift": round(avg, 3)}

    def receiver_name(self, receiver: Any) -> str:
        return self.runtime.actor.receiver_name(receiver)

    def spawn_actor(self, node: SpawnExpr, env: Environment, async_mode: bool = False) -> DurableActorRef:
        return self.runtime.actor.spawn_actor(node, env, async_mode=async_mode)

    def create_durable_promise(self, reason: str, request: Any = None) -> DurablePromise:
        return self.runtime.actor.create_durable_promise(reason, request=request)

    def resolve_promise(self, promise_id: str, result: Any, source_node: Optional[str] = None):
        return self.runtime.actor.resolve_promise(promise_id, result, source_node=source_node)

    def register_promise_owner(self, promise_id: str, node_address: str):
        return self.runtime.actor.register_promise_owner(promise_id, node_address)

    def register_promise_tombstone(self, promise_id: str, node_address: str):
        return self.runtime.actor.register_promise_tombstone(promise_id, node_address)

    def resolve_promise_location(self, promise_id: str) -> str:
        return self.runtime.actor.resolve_promise_location(promise_id)

    def build_resolve_promise_packet(self, promise_id: str, result: Any, target_node: str) -> Dict[str, Any]:
        return self.runtime.actor.build_resolve_promise_packet(promise_id, result, target_node)

    def emit_or_apply_promise_resolution(self, promise_id: str, result: Any) -> Dict[str, Any]:
        return self.runtime.actor.emit_or_apply_promise_resolution(promise_id, result)

    def promise_id_from_await_target(self, expr: Node, env: Environment) -> str:
        return self.runtime.actor.promise_id_from_await_target(expr, env)


    def describe_request(self, node: Node, env: Environment) -> Any:
        """Create a JSON-safe descriptor for an external suspend request.

        Undefined external calls such as await_human_approval(plan) are treated
        as request descriptors instead of local function invocations.
        """
        if isinstance(node, CallExpr):
            if isinstance(node.callee, Variable):
                return {
                    "call": node.callee.name,
                    "args": [self.evaluate(arg, env) for arg in node.args],
                }
            if isinstance(node.callee, MemberAccess):
                target = self.evaluate(node.callee.obj, env)
                return {
                    "target": self.receiver_name(target),
                    "method": node.callee.member,
                    "args": [self.evaluate(arg, env) for arg in node.args],
                }
        try:
            return self.evaluate(node, env)
        except Exception:
            return {"expr": type(node).__name__}

    def await_expression(self, node: AwaitExpr, env: Environment):
        promise_id = self.promise_id_from_await_target(node.expr, env)
        record = self.promises.get(promise_id)
        if record and record.get("status") == "resolved":
            return record.get("result")
        event = self.next_history_event("promise_resolved")
        if event is not None and event.get("promise_id") == promise_id:
            return event.get("result")
        owner_node = self.resolve_promise_location(promise_id)
        injected = yield Suspension(
            node,
            env,
            reason="awaiting_promise",
            payload={"promise_id": promise_id, "owner_node": owner_node},
        )
        result = injected
        self.promises[promise_id] = {"promise_id": promise_id, "reason": "await", "status": "resolved", "result": result}
        self.execution_history.append({"type": "promise_resolved", "promise_id": promise_id, "result": result})
        return result

    def suspend_expression(self, node: SuspendExpr, env: Environment):
        request_value = self.describe_request(node.request, env)
        promise = self.create_durable_promise("suspend", request_value)
        event = self.next_history_event("promise_resolved")
        if event is not None and event.get("promise_id") == promise.promise_id:
            return event.get("result")
        injected = yield Suspension(
            node,
            env,
            reason="awaiting_external_signal",
            payload={"promise_id": promise.promise_id, "request": request_value},
        )
        self.resolve_promise(promise.promise_id, injected)
        return injected

    def current_actor_name(self, env: Environment) -> str:
        try:
            current = env.get("self")
            if isinstance(current, AgentRuntime):
                return current.name
        except RuntimeError:
            pass
        return "global"

    def resolve_trust_level(self, trust_node: Node, env: Environment) -> str:
        """Resolve trust level using DSL symbols, not user-shadowed locals."""
        allowed = {"untrusted", "low", "medium", "high", "critical"}
        if isinstance(trust_node, Variable) and trust_node.name in allowed:
            return trust_node.name
        value = self.evaluate(trust_node, env)
        if value not in allowed:
            raise RuntimeError(f"Invalid trust level: {value}")
        return value

    def find_agent(self, env: Environment) -> AgentRuntime:
        try:
            current = env.get("self")
            if isinstance(current, AgentRuntime):
                return current
        except RuntimeError:
            pass

        actor_name = self.current_actor_name(env)
        cursor = env
        while cursor:
            if actor_name in cursor.agents:
                return cursor.agents[actor_name]
            if len(cursor.agents) == 1:
                return next(iter(cursor.agents.values()))
            cursor = cursor.parent
        raise RuntimeError("No agent context for memory operation")


    def observe_target_string(self, target: Node, env: Environment) -> str:
        """Convert an observe target AST into a stable string such as Worker.process."""
        if isinstance(target, MemberAccess):
            return f"{self.observe_target_string(target.obj, env)}.{target.member}"
        if isinstance(target, Variable):
            return target.name
        if isinstance(target, Literal):
            return str(target.value)
        try:
            return str(self.evaluate(target, env))
        except Exception:
            return type(target).__name__

    def event_target(self, event: Dict[str, Any]) -> str:
        if event.get("target"):
            return str(event.get("target"))
        msg = event.get("message") or event
        receiver = msg.get("receiver")
        method = msg.get("method")
        if receiver and method:
            return f"{receiver}.{method}"
        if event.get("actor"):
            return str(event.get("actor"))
        return "*"


    def increment_evolution_cooldowns(self, event_type: Optional[str]):
        significant = {"message_sent", "llm_call", "policy_evaluated", "integrate_committed", "identity_integrated"}
        if event_type not in significant:
            return
        for key in list(self.events_since_last_evolution.keys()):
            self.events_since_last_evolution[key] = self.events_since_last_evolution.get(key, 0) + 1

    def observer_matches(self, observer_target: str, event_target: str) -> bool:
        return observer_target in {"*", event_target} or event_target.startswith(observer_target + ".")


    def evaluate_energy_pool(self, node: EnergyPoolDecl, env: Environment) -> EnergyPool:
        def num(expr, default):
            if expr is None:
                return default
            return int(self.evaluate(expr, env))
        max_value = num(node.max, 100)
        return EnergyPool(
            max=max_value,
            initial=num(node.initial, max_value),
            recharge_amount=num(node.recharge_amount, 0),
            recharge_every=max(1, num(node.recharge_every, 1)),
            rest_threshold=num(node.rest_threshold, 0),
            hysteresis_margin=num(node.hysteresis_margin, 5),
        )

    def evaluate_context_block(self, node: ContextBlock, env: Environment) -> Any:
        enter = self.context_tracker.enter_event(node.label)
        enter["event_id"] = self.next_event_id()
        self.current_context = self.context_tracker.current
        self.execution_history.append(enter)
        self.emit_runtime_event(enter, env)
        result = None
        try:
            result = self.execute_block(node.body, Environment(env))
        finally:
            exit_event = self.context_tracker.exit_event(node.label)
            exit_event["event_id"] = self.next_event_id()
            self.current_context = self.context_tracker.current
            self.execution_history.append(exit_event)
            self.emit_runtime_event(exit_event, env)
        return result

    def next_event_id(self) -> str:
        return f"evt-{len(self.execution_history):08d}"

    def process_energy_pool_event(self, event: Dict[str, Any]):
        if self.runtime_mode != RuntimeMode.LIVE or self.energy_pool is None:
            return
        if self._energy_event_depth > 0:
            return
        self._energy_event_depth += 1
        try:
            for generated in self.energy_pool.on_event():
                generated.setdefault("event_id", self.next_event_id())
                self.execution_history.append(generated)
                # Do not recursively recharge on generated energy events, but let observers see them.
                if self.runtime_mode == RuntimeMode.LIVE and self.policy_guard_depth == 0 and self._observer_depth == 0 and getattr(self, "_threshold_action_depth", 0) == 0:
                    target = self.event_target(generated)
                    event_type = generated.get("type")
                    self._observer_depth += 1
                    try:
                        for observer in list(self.observers):
                            if not self.observer_matches(observer.get("target", "*"), target):
                                continue
                            for handler in observer.get("handlers", []):
                                if handler.event_type != event_type and handler.event_type != "*":
                                    continue
                                obs_env = Environment(self.global_env)
                                obs_env.define(handler.binding, generated)
                                self.execute_block(handler.body, obs_env)
                    finally:
                        self._observer_depth -= 1
        finally:
            self._energy_event_depth -= 1

    def _current_pad_for_habits(self) -> Dict[str, float]:
        return self.runtime.habit.current_pad_for_habits()

    def _emit_habit_event(self, event: Dict[str, Any]):
        return self.runtime.habit.emit_habit_event(event)

    def _execute_habit_body(self, body: List[Node]):
        return self.runtime.habit.execute_habit_body(body)

    def process_habits_on_event(self, event: Dict[str, Any]):
        return self.runtime.habit.process_habits_on_event(event)

    def emit_runtime_event(self, event: Dict[str, Any], env: Optional[Environment] = None):
        """Run passive observers for LIVE audit events without mutating core flow."""
        self._forbid_consensus_vote_side_effect("runtime event emission")
        if self.runtime_mode == RuntimeMode.LIVE:
            self.increment_evolution_cooldowns(event.get("type"))
            self.process_energy_pool_event(event)
            self.process_habits_on_event(event)
        if self.runtime_mode != RuntimeMode.LIVE or self.policy_guard_depth > 0 or self._observer_depth > 0 or getattr(self, "_threshold_action_depth", 0) > 0 or getattr(getattr(self.runtime, "habit", None), "suppress_observers", False):
            return
        target = self.event_target(event)
        event_type = event.get("type")
        self._observer_depth += 1
        try:
            for observer in list(self.observers):
                if not self.observer_matches(observer.get("target", "*"), target):
                    continue
                for handler in observer.get("handlers", []):
                    if handler.event_type != event_type and handler.event_type != "*":
                        continue
                    obs_env = Environment(env or self.global_env)
                    obs_env.define(handler.binding, event)
                    self.execute_block(handler.body, obs_env)
        finally:
            self._observer_depth -= 1

    TRUST_ORDER = {"untrusted": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

    def actor_trust_record(self, actor_name: str) -> Dict[str, Any]:
        # Direct actor lookup by declaration name.
        try:
            actor = self.global_env.get_agent(actor_name)
            return {"name": actor.name, "trust": actor.trust_level, "trust_scope": actor.trust_scope}
        except Exception:
            pass
        # Spawned process id lookup.
        meta = self.spawned_actors.get(actor_name) or {}
        base_name = meta.get("actor_name", actor_name.split("#", 1)[0])
        try:
            actor = self.global_env.get_agent(base_name)
            return {"name": base_name, "trust": actor.trust_level, "trust_scope": actor.trust_scope}
        except Exception:
            return {"name": actor_name, "trust": "medium", "trust_scope": []}

    def trust_at_least(self, trust: str, minimum: str) -> bool:
        return self.TRUST_ORDER.get(str(trust), 0) >= self.TRUST_ORDER.get(str(minimum), 0)

    def declare_intent(self, name: str, env: Environment) -> Dict[str, Any]:
        if name not in self.intents:
            raise RuntimeError(f"Undefined intent: '{name}'")
        record = dict(self.intents[name])
        event = {"type": "intent_declared", "intent": name, "fields": record}
        self.check_intent_governance(name, record, env)
        self.intent_audit.append(event)
        self.execution_history.append(event)
        self.emit_runtime_event(event, env)
        return record

    def check_intent_governance(self, name: str, record: Dict[str, Any], env: Environment):
        return self.runtime.governance.check_intent_governance(name, record)

    def policy_target_matches(self, target: Optional[str], receiver: str, method: str) -> bool:
        return self.runtime.governance.policy_target_matches(target, receiver, method)

    def peek_history_event(self) -> Optional[Dict[str, Any]]:
        return self.runtime.replay.peek_history_event()

    def applicable_policies(self, receiver: str, method: str) -> List[Dict[str, Any]]:
        return self.runtime.governance.applicable_policies(receiver, method)

    def current_mood_snapshot(self) -> FrozenMoodSnapshot:
        return self.runtime.affective.current_mood_snapshot()

    def evaluate_bias_expr(self, expr: Node, mood_snapshot: FrozenMoodSnapshot) -> float:
        bias_env = Environment(self.global_env)
        vals = mood_snapshot.to_dict()
        for k, v in vals.items():
            bias_env.define(k, v)
        bias_env.define("pleasure", vals["valence"])
        bias_env.define("energy", vals["arousal"])
        bias_env.define("control", vals["dominance"])
        bias_env.define("mood", mood_snapshot)
        try:
            return float(self.evaluate(expr, bias_env))
        except Exception as exc:
            raise RuntimeError(f"Failed to evaluate affective bias expression: {exc}")

    def normalize_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        # Negative raw weights are clipped to zero before normalization.
        clipped = {k: max(0.0, float(v)) for k, v in weights.items()}
        total = sum(clipped.values())
        if total <= 0.0:
            n = max(len(clipped), 1)
            return {k: round(1.0 / n, 6) for k in clipped}
        return {k: round(v / total, 6) for k, v in clipped.items()}

    def execute_policy_guard(self, policy: Dict[str, Any], sender: str, receiver: str, method: str, args: List[Any]) -> Dict[str, Any]:
        return self.runtime.governance.execute_policy_guard(policy, sender, receiver, method, args)

    def check_send_governance(self, sender: str, receiver: str, method: str, args: List[Any]):
        return self.runtime.governance.check_send_governance(sender, receiver, method, args)

    def register_route(self, actor_name: str, node_address: str):
        return self.runtime.actor.register_route(actor_name, node_address)

    def resolve_actor_location(self, actor_name: str) -> str:
        return self.runtime.actor.resolve_actor_location(actor_name)

    def build_forward_packet(self, message: Dict[str, Any], node_address: str) -> Dict[str, Any]:
        return self.runtime.actor.build_forward_packet(message, node_address)

    def send_message(self, sender: str, receiver: str, method: str, args: List[Any]) -> Dict[str, Any]:
        self._forbid_consensus_vote_side_effect("send")
        return self.runtime.actor.send_message(sender, receiver, method, args)

    def apply_receive_patterns(self, node: ReceiveBlock, message: Dict[str, Any], env: Environment, async_mode: bool = False):
        return self.runtime.actor.apply_receive_patterns(node, message, env, async_mode=async_mode)

    def execute_side_effect(self, name: str, args: List[Any]) -> Any:
        self._forbid_consensus_vote_side_effect(name)
        return self.runtime.replay.execute_side_effect(name, args)

    def peek_next_history_event(self) -> Optional[Dict[str, Any]]:
        return self.runtime.replay.peek_next_history_event()

    def next_history_event(self, expected_type: str, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self.runtime.replay.next_history_event(expected_type, name=name)

    def execute_block(self, statements: List[Node], env: Environment) -> Any:
        result = None
        for stmt in statements:
            result = self.evaluate(stmt, env)
        return result

    def interpret_async(self, node: Node):
        """Coroutine-based execution entrypoint.

        Yields Suspension objects for durable wait points. The same Python
        generator can be resumed with .send(value) inside one process. For
        cross-process recovery, use snapshot()/restore_snapshot() and replay from
        a stable continuation layer in a future bytecode runtime.
        """
        if isinstance(node, Program):
            return (yield from self.execute_block_async(node.statements, self.global_env))
        return (yield from self.evaluate_async(node, self.global_env))

    def execute_block_async(self, statements: List[Node], env: Environment):
        result = None
        for stmt in statements:
            result = yield from self.evaluate_async(stmt, env)
        return result

    def evaluate_async(self, node: Node, env: Environment):
        if isinstance(node, LetStmt):
            value = yield from self.evaluate_async(node.value, env)
            env.define(node.name, value)
            return value

        if isinstance(node, AssignStmt):
            if self.is_in_subagent():
                raise OrphanedIdentityException("assignment is forbidden inside sub-agent")
            if node.target == "soulprint" and self.evolve_depth <= 0:
                raise IdentityCrisisError("Protected soulprint cannot be directly overwritten; use evolve")
            if self.policy_guard_depth > 0:
                raise PolicyCompilationError("Policy guard cannot assign to an existing environment variable")
            value = yield from self.evaluate_async(node.value, env)
            env.set(node.target, value)
            return value

        if isinstance(node, ExprStmt):
            return (yield from self.evaluate_async(node.expr, env))

        if isinstance(node, Literal):
            return node.value

        if isinstance(node, AffectivePadLiteral):
            return {"valence": float(node.valence), "arousal": float(node.arousal), "dominance": float(node.dominance)}

        if isinstance(node, DecayExpr):
            return {"value": node.value, "unit": node.unit, "original": node.original}

        if isinstance(node, Variable):
            return env.get(node.name)

        if isinstance(node, AssertStmt):
            return self.evaluate_assert(node, env)

        if isinstance(node, IntegrateBlock):
            self.forbid_subagent_side_effect("integrate")
            return self.evaluate_integrate(node, env)

        if isinstance(node, EnergyPoolDecl):
            pool = self.evaluate_energy_pool(node, env)
            self.energy_pool = pool
            env.define("energy_pool", pool.snapshot())
            return pool.snapshot()

        if isinstance(node, ContextBlock):
            return self.evaluate_context_block(node, env)

        if isinstance(node, LLMCall):
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("llm is forbidden inside integrate transaction")
            prompt = yield from self.evaluate_async(node.prompt, env)
            if isinstance(prompt, PromptExpr):
                prompt = prompt.template
            prompt_text = str(prompt)
            prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

            event = self.next_history_event("llm_call")
            if event is not None:
                return event.get("result")

            injected = yield Suspension(
                node,
                env,
                reason="awaiting_llm",
                payload={"prompt": prompt_text, "model": node.model},
            )
            if injected is not None:
                result = injected
            else:
                result = self.llm_backend.complete(
                    prompt_text,
                    model=node.model,
                    temperature=node.temperature,
                    max_tokens=node.max_tokens,
                )
            self.llm_context_cache[prompt_hash] = {"prompt_hash": prompt_hash, "model": node.model, "result": result}
            self.execution_history.append({
                "type": "llm_call",
                "prompt": prompt_text,
                "prompt_hash": prompt_hash,
                "model": node.model,
                "temperature": node.temperature,
                "max_tokens": node.max_tokens,
                "result": result,
            })
            return result

        if isinstance(node, GovernedMemoryForget):
            self.forbid_subagent_side_effect("memory.forget")
            if self.integrate_i2_skeleton_depth > 0:
                raise IntegrateIsolationViolation("memory.forget is forbidden inside Alpha3g I2 integrate skeleton")
            if self.dream_depth > 0:
                raise DreamIsolationViolation("dream cannot forget memory; use integrate")
            if self.policy_guard_depth > 0:
                raise PolicyCompilationError("Policy guard cannot forget memory")
            key = self.evaluate(node.key, env) if node.key else None
            fields = {k: self.evaluate(v, env) for k, v in node.fields.items()}
            if not fields.get("reason"):
                raise RuntimeError("Governed memory.forget requires a reason field")
            agent = self.find_agent(env)
            removed = agent.memory.forget(key)
            event = {"type": "memory_forgotten", "agent": agent.name, "key": key, "governance": fields, "removed": removed}
            self.memory_audit.append(event)
            self.execution_history.append(event)
            self.emit_runtime_event(event, env)
            return removed

        if isinstance(node, SpawnExpr):
            self.forbid_subagent_side_effect("spawn")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("spawn is forbidden inside integrate transaction")
            return self.spawn_actor(node, env, async_mode=True)

        if isinstance(node, AwaitExpr):
            self.forbid_subagent_side_effect("await")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("await is forbidden inside integrate transaction")
            return (yield from self.await_expression(node, env))

        if isinstance(node, SuspendExpr):
            self.forbid_subagent_side_effect("suspend")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("suspend is forbidden inside integrate transaction")
            return (yield from self.suspend_expression(node, env))

        if isinstance(node, MigrateStmt):
            self.forbid_subagent_side_effect("migrate")
            if self.dream_depth > 0:
                raise DreamIsolationViolation("dream cannot migrate actors; use integrate")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("migrate is forbidden inside integrate transaction")
            target = yield from self.evaluate_async(node.target, env)
            return (yield from self.runtime.actor.request_migration_async(node, env, target))

        if isinstance(node, SendStmt):
            self.forbid_subagent_side_effect("send")
            if self.dream_depth > 0:
                raise DreamIsolationViolation("dream cannot send actor messages; use integrate")
            if self.integrate_depth > 0:
                raise IntegrateIsolationViolation("send is forbidden inside integrate transaction")
            if self.policy_guard_depth > 0:
                raise PolicyCompilationError("Policy guard cannot send actor messages")
            receiver = yield from self.evaluate_async(node.receiver, env)
            receiver_name = self.receiver_name(receiver)
            args = []
            for arg in node.args:
                args.append((yield from self.evaluate_async(arg, env)))
            return self.send_message(self.current_actor_name(env), receiver_name, node.method, args)

        if isinstance(node, ReceiveBlock):
            actor_name = self.current_actor_name(env)

            replay_event = self.peek_next_history_event()
            if replay_event and replay_event.get("type") == "message_received":
                event = self.next_history_event("message_received")
                message = event.get("message")
                return (yield from self.apply_receive_patterns(node, message, env, async_mode=True))
            if replay_event and replay_event.get("type") == "receive_timeout":
                self.next_history_event("receive_timeout")
                return (yield from self.execute_block_async(node.else_body, Environment(env))) if node.else_body else None

            mailbox = self.mailboxes.setdefault(actor_name, [])
            timeout_value = None
            if node.timeout is not None:
                timeout_value = yield from self.evaluate_async(node.timeout, env)
            if not mailbox:
                reason = "awaiting_message_or_timeout" if node.timeout is not None else "awaiting_message"
                injected = yield Suspension(node, env, reason=reason, payload={"actor": actor_name, "timeout": timeout_value})
                if isinstance(injected, dict) and injected.get("timeout") is True:
                    event = {"type": "receive_timeout", "actor": actor_name, "timeout": timeout_value}
                    self.execution_history.append(event)
                    self.actor_log.append(event)
                    return (yield from self.execute_block_async(node.else_body, Environment(env))) if node.else_body else None
                if injected is not None:
                    mailbox.append(injected)
            if not mailbox:
                if node.timeout is not None:
                    event = {"type": "receive_timeout", "actor": actor_name, "timeout": timeout_value}
                    self.execution_history.append(event)
                    self.actor_log.append(event)
                    return (yield from self.execute_block_async(node.else_body, Environment(env))) if node.else_body else None
                return None
            message = mailbox.pop(0)
            self.execution_history.append({"type": "message_received", "actor": actor_name, "message": message})
            return (yield from self.apply_receive_patterns(node, message, env, async_mode=True))

        if isinstance(node, CallExpr):
            # MVP: synchronous function calls remain available from the coroutine
            # runner. Native async calls will be lowered to bytecode later.
            return self.evaluate(node, env)

        if isinstance(node, BinaryExpr):
            left = yield from self.evaluate_async(node.left, env)
            right = yield from self.evaluate_async(node.right, env)
            return self.eval_binary(node.op, left, right)

        if isinstance(node, UnaryExpr):
            operand = yield from self.evaluate_async(node.operand, env)
            return self.eval_unary(node.op, operand)

        if isinstance(node, ListExpr):
            values = []
            for item in node.elements:
                values.append((yield from self.evaluate_async(item, env)))
            return values

        if isinstance(node, DictExpr):
            result = {}
            for k, v in node.pairs:
                result[k] = yield from self.evaluate_async(v, env)
            return result

        if isinstance(node, IfStmt):
            condition = yield from self.evaluate_async(node.condition, env)
            if self.is_truthy(condition):
                return (yield from self.execute_block_async(node.then_body, Environment(env)))
            if node.else_body:
                return (yield from self.execute_block_async(node.else_body, Environment(env)))
            return None

        if isinstance(node, ReturnStmt):
            value = yield from self.evaluate_async(node.value, env) if node.value else None
            raise ReturnException(value)

        # Everything else falls back to the stable synchronous interpreter.
        return self.evaluate(node, env)


    # ------------------------------------------------------------------
    # v1.7 Production Hardening: persistence, metrics and provenance
    # ------------------------------------------------------------------
    def attach_storage(self, backend: Any, run_id: Optional[str] = None):
        """Attach a StorageBackend-compatible object to this interpreter."""
        self.storage_backend = backend
        if run_id:
            self.run_id = run_id
        return self

    def compute_history_hash(self) -> str:
        return self.runtime.replay.compute_history_hash()

    def history_hash_chain(self) -> List[Dict[str, Any]]:
        return self.runtime.replay.history_hash_chain()

    def verify_history_chain(self, chain: List[Dict[str, Any]]) -> bool:
        return self.runtime.replay.verify_history_chain(chain)




    # --- v1.8 Collective Intelligence ---
    def actor_name_value(self, value: Any) -> str:
        if isinstance(value, AgentRuntime):
            return value.name
        if isinstance(value, DurableActorRef):
            return value.actor_name
        return str(value)

    def eval_participants(self, participants: List[Node], env: Environment) -> List[str]:
        names = []
        for item in participants:
            try:
                value = self.evaluate(item, env)
            except Exception:
                value = getattr(item, "name", str(item))
            names.append(self.actor_name_value(value))
        return names

    def policy_allows(self, policy_name: Optional[str] = None, target: Optional[str] = None, field: str = "allow") -> bool:
        return self.runtime.governance.policy_allows(policy_name, target, field)

    def require_cross_agent_resonance_permission(self, target_name: str):
        return self.runtime.governance.require_cross_agent_resonance_permission(
            target_name,
            lambda: self.current_actor_name(self.global_env),
        )

    def _collective_trace(self, kind: str, payload: Dict[str, Any]) -> Dict[str, str]:
        base = json.dumps({"kind": kind, "payload": payload, "node": self.node_id}, sort_keys=True, default=str)
        trace_id = hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]
        span_id = hashlib.sha256((trace_id + kind).encode("utf-8")).hexdigest()[:16]
        return {"trace_id": trace_id, "span_id": span_id}

    def _event_signature(self, event: Dict[str, Any]) -> str:
        base = json.dumps(event, sort_keys=True, default=str)
        return hashlib.sha256((self.compute_history_hash() + base).encode("utf-8")).hexdigest()





    # --- v2.1.2/v2.0 Affective Runtime ---
    def evaluate_affective_threshold_def(self, node: AffectiveThresholdDef, env: Environment) -> Dict[str, Any]:
        return self.runtime.affective.evaluate_affective_threshold_def(node, env)

    def validate_threshold_action_purity(self, body: List[Node]):
        return self.runtime.affective.validate_threshold_action_purity(body)

    def _eval_affective_condition(self, condition: Node, pad_snapshot: Dict[str, float]) -> bool:
        return self.runtime.affective._eval_affective_condition(condition, pad_snapshot)

    def process_affective_thresholds(self, env: Optional[Environment] = None):
        return self.runtime.affective.process_affective_thresholds(env)

    def execute_threshold_action(self, rec, env: Environment):
        return self.runtime.affective.execute_threshold_action(rec, env)

    def evaluate_affective_state(self, node: AffectiveStateDef, env: Environment) -> Dict[str, Any]:
        return self.runtime.affective.evaluate_affective_state(node, env)

    def _current_affective_state(self, env: Environment) -> AffectiveState:
        return self.runtime.affective._current_affective_state(env)

    def evaluate_affective_event(self, node: AffectiveEventStmt, env: Environment) -> Dict[str, Any]:
        return self.runtime.affective.evaluate_affective_event(node, env)

    def evaluate_affective_modulation(self, node: AffectiveModulationStmt, env: Environment) -> Dict[str, Any]:
        return self.runtime.affective.evaluate_affective_modulation(node, env)

    def _lookup_resonance_profile_for_target(self, target: str, env: Environment) -> Dict[str, Any]:
        return self.runtime.affective._lookup_resonance_profile_for_target(target, env)

    def _compute_affective_resonance_deltas(self, node: AffectiveResonanceStmt, env: Environment, state: AffectiveState, target: str) -> List[Dict[str, Any]]:
        return self.runtime.affective._compute_affective_resonance_deltas(node, env, state, target)

    def _apply_affective_resonance_event(self, event: Dict[str, Any], env: Environment) -> Dict[str, Any]:
        return self.runtime.affective._apply_affective_resonance_event(event, env)

    def evaluate_affective_resonance(self, node: AffectiveResonanceStmt, env: Environment) -> Dict[str, Any]:
        return self.runtime.affective.evaluate_affective_resonance(node, env)

    def evaluate_somatic_marker(self, node: SomaticMarkerStmt, env: Environment) -> Dict[str, Any]:
        fields = {k: self.evaluate(v, env) for k, v in (node.fields or {}).items()}
        explicit = fields.get("gut_feeling")
        threshold = float(fields.get("threshold", 0.4) or 0.4)
        marker = compute_gut_feeling(node.name, self.affective_events, threshold=threshold, explicit=explicit)
        if fields.get("escalate_to"):
            marker["escalate_to"] = fields.get("escalate_to")
        self.somatic_markers[node.name] = marker
        env.define(node.binding, marker)
        event = {"type": "somatic_marker_evaluated", "name": node.name, "marker": marker, "trace_id": self.current_trace_id()}
        self.execution_history.append(event)
        return marker

    def evaluate_compile_vm(self, node: CompileVmStmt, env: Environment) -> Dict[str, Any]:
        return self.runtime.vm.evaluate_compile_vm(node, env)

    def _event_id_for_index(self, idx: int) -> str:
        return self.runtime.vm.event_id_for_index(idx)

    def _parse_event_id(self, event_id: str) -> int:
        return self.runtime.vm.parse_event_id(event_id)

    def _history_hash_until(self, event_id: Optional[str]) -> str:
        return self.runtime.vm.history_hash_until(event_id)

    def _current_mood_snapshot(self) -> Dict[str, float]:
        return self.runtime.vm.current_mood_snapshot()

    def _make_vm_snapshot(self, vm: CognitiveVM, label: str) -> Dict[str, Any]:
        return self.runtime.vm.make_vm_snapshot(vm, label)

    def _restore_vm_from_checkpoint(self, label: str, gas: Optional[int] = None, cognitive_budget: Optional[int] = None) -> CognitiveVM:
        return self.runtime.vm.restore_vm_from_checkpoint(label, gas=gas, cognitive_budget=cognitive_budget)

    def _vm_host_call(self, opcode: str, a: Any, b: Any) -> Dict[str, Any]:
        return self.runtime.vm.vm_host_call(opcode, a, b)

    def evaluate_run_vm(self, node: RunVmStmt, env: Environment) -> Dict[str, Any]:
        return self.runtime.vm.evaluate_run_vm(node, env)


    # --- v1.9 Cognitive Continuity: Memory Palace / Intention / Habits ---
    def evaluate_memory_palace(self, node: MemoryPalaceDef, env: Environment) -> Dict[str, Any]:
        palace = MemoryPalace(
            name=node.name,
            rooms=list(node.rooms or ["episodic", "semantic", "procedural"]),
            decay_policy=dict(node.decay_policy or {}),
            backend=node.backend or "sqlite",
            consolidate_during_dream=bool(node.consolidate_during_dream),
        )
        self.memory_palaces[node.name] = palace
        env.define(node.binding, palace.to_dict())
        env.define(node.name, palace.to_dict())
        event = {"type": "memory_palace_created", "name": node.name, "rooms": palace.rooms, "backend": palace.backend_name, "trace_id": self.current_trace_id()}
        self.execution_history.append(event)
        self.memory_audit.append(event)
        return palace.to_dict()

    def resolve_palace(self, palace_ref: Any, env: Environment) -> MemoryPalace:
        if isinstance(palace_ref, dict) and palace_ref.get("type") == "memory_palace":
            name = palace_ref.get("name")
            if name in self.memory_palaces:
                return self.memory_palaces[name]
            palace = MemoryPalace.from_dict(palace_ref)
            self.memory_palaces[name] = palace
            return palace
        if isinstance(palace_ref, str):
            if palace_ref in self.memory_palaces:
                return self.memory_palaces[palace_ref]
            try:
                return self.resolve_palace(env.get(palace_ref), env)
            except Exception:
                pass
        raise RuntimeError("Unknown memory palace reference")

    def _normalize_affective_tag_value(self, value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            if "delta" in value and isinstance(value.get("delta"), dict):
                snap = {k: float(value["delta"].get(k, 0.0) or 0.0) for k in ("valence", "arousal", "dominance")}
            else:
                snap = {k: float(value.get(k, 0.0) or 0.0) for k in ("valence", "arousal", "dominance")}
            return {"id": value.get("id") or value.get("tag_id") or value.get("name"), "snapshot": snap, "raw": value}
        return {"id": str(value), "snapshot": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}, "raw": value}

    def _decay_to_expiration(self, decay_value: Any) -> Dict[str, Any]:
        if not decay_value:
            return {}
        if isinstance(decay_value, dict):
            unit = decay_value.get("unit")
            value = int(decay_value.get("value", -1))
            original = decay_value.get("original") or ("never" if value < 0 else f"{value} {unit}")
        elif isinstance(decay_value, DecayExpr):
            unit = decay_value.unit; value = decay_value.value; original = decay_value.original
        else:
            return {}
        if unit == "never" or value < 0:
            return {"affective_expires_at_event": None, "affective_decay_original": "never"}
        events = value if unit == "events" else value * int(self.estimated_events_per_day)
        return {"affective_expires_at_event": len(self.execution_history) + int(events), "affective_decay_original": original}

    def evaluate_imprint(self, node: ImprintStmt, env: Environment) -> str:
        palace = self.resolve_palace(self.evaluate(node.palace, env), env)
        fields = {k: self.evaluate(v, env) for k, v in (node.fields or {}).items()}
        fields.setdefault("trace_id", self.current_trace_id())
        affective_tag = fields.get("affective_tag")
        affective_tag_info = self._normalize_affective_tag_value(affective_tag) if affective_tag is not None else {}
        if affective_tag_info:
            fields["affective_tag"] = affective_tag_info.get("raw")
            fields["affective_tag_id"] = affective_tag_info.get("id")
            fields["affective_tag_snapshot"] = affective_tag_info.get("snapshot")
        if "affective_decay" in fields:
            fields.update(self._decay_to_expiration(fields.get("affective_decay")))
        imprint_id = palace.imprint(node.room, fields)
        event = {"type": "memory_imprinted", "palace": palace.name, "room": node.room, "imprint_id": imprint_id, "fields": fields, "trace_id": fields.get("trace_id")}
        if affective_tag_info:
            event.update({"affective_tag_id": fields.get("affective_tag_id"), "affective_tag_snapshot": fields.get("affective_tag_snapshot"), "affective_expires_at_event": fields.get("affective_expires_at_event"), "affective_decay_original": fields.get("affective_decay_original")})
        self.execution_history.append(event)
        self.memory_audit.append(event)
        if node.binding:
            env.define(node.binding, imprint_id)
        return imprint_id

    def evaluate_recall(self, node: RecallStmt, env: Environment) -> List[Dict[str, Any]]:
        palace = self.resolve_palace(self.evaluate(node.palace, env), env)
        query = self.evaluate(node.query, env) if node.query else ""
        threshold = float(self.evaluate(node.threshold, env)) if node.threshold else 0.0
        limit = int(self.evaluate(node.limit, env)) if node.limit else 10
        memories = palace.recall(node.room, str(query), threshold, limit, affective_filter=getattr(node, "affective_filter", None), affective_sort=getattr(node, "affective_sort", None), current_event_index=len(self.execution_history))
        for m in memories:
            if m.get("affective_expired") and m.get("id"):
                expiry_event = {"type": "memory_affective_tag_expired", "imprint_id": m.get("id"), "expired_at_event": len(self.execution_history), "trace_id": self.current_trace_id()}
                if not any(e.get("type") == "memory_affective_tag_expired" and e.get("imprint_id") == m.get("id") for e in self.execution_history):
                    self.execution_history.append(expiry_event)
                    self.memory_audit.append(expiry_event)
        event = {"type": "memory_recalled", "palace": palace.name, "room": node.room, "query": query, "affective_filter": self._affective_filter_to_string(getattr(node, "affective_filter", None)), "count": len(memories), "results_count": len(memories), "trace_id": self.current_trace_id()}
        self.execution_history.append(event)
        env.define(node.binding, memories)
        return memories

    def _affective_filter_to_string(self, expr: Any) -> Optional[str]:
        if expr is None:
            return None
        kind = getattr(expr, "kind", None)
        if kind in {"tagged", "untagged"}:
            return kind
        if kind == "comparison":
            return f"{getattr(expr, 'left', '')} {getattr(expr, 'op', '')} {getattr(expr, 'right', '')}"
        if kind == "and":
            return f"({self._affective_filter_to_string(getattr(expr, 'left', None))}) and ({self._affective_filter_to_string(getattr(expr, 'right', None))})"
        return str(expr)

    def evaluate_intention_cascade(self, node: IntentionCascadeDef, env: Environment) -> Dict[str, Any]:
        levels = {k: self.evaluate(v, env) for k, v in (node.levels or {}).items()}
        cascade = IntentionCascade(node.name, levels).to_dict()
        self.intention_cascades[node.name] = cascade
        event = {"type": "intention_cascade_created", "name": node.name, "cascade_id": cascade["id"], "levels": levels, "trace_id": self.current_trace_id()}
        self.execution_history.append(event)
        env.define(node.binding, cascade)
        env.define(node.name, cascade)
        return cascade

    def evaluate_plan_weave(self, node: PlanWeaveStmt, env: Environment) -> Dict[str, Any]:
        participants = self.eval_participants(node.participants, env)
        intention = self.evaluate(node.intention, env) if node.intention else {}
        checkpoint_every = int(self.evaluate(node.checkpoint_every, env)) if node.checkpoint_every else 3
        timeout = int(self.evaluate(node.timeout, env)) if node.timeout else 120
        result = weave_plan(intention, participants, checkpoint_every=checkpoint_every)
        result.update({"policy": node.policy_ref, "timeout": timeout, "rollback_on": node.rollback_on, "trace_id": self.current_trace_id()})
        event = {"type": "plan_weave_completed", "participants": participants, "result": result, "trace_id": result["trace_id"]}
        self.execution_history.append(event)
        env.define(node.binding, result)
        # Successful distributed plans are episodic memory candidates.
        for palace in self.memory_palaces.values():
            palace.imprint("episodic", {"content": f"Plan weave completed: {result.get('status')}", "confidence": 0.9, "source": "plan_weave", "trace_id": result["trace_id"]})
            break
        return result

    def evaluate_habit(self, node: HabitStmt, env: Environment) -> Dict[str, Any]:
        """Register a Living Habit candidate rule (v2.1.3-B).

        The executable body remains in the runtime registry; palace.procedural keeps
        only declarative metadata. v2.1.3-C executes the body only when a
        candidate is triggered through the event-driven HabitRegistry.
        """
        def eval_num(expr, default=0.0):
            if expr is None:
                return default
            return float(self.evaluate(expr, env)) if isinstance(expr, Node) else float(expr)

        name = node.name or f"Habit-{uuid.uuid4().hex[:8]}"
        fatigue_threshold = 5
        fatigue_multiplier = 1.0
        fatigue_rest_events = 0
        if getattr(node, "fatigue", None):
            fatigue_threshold = int(node.fatigue.threshold)
            fatigue_multiplier = float(node.fatigue.energy_cost_multiplier)
            fatigue_rest_events = int(node.fatigue.require_rest)
        record = HabitRuntimeRecord(
            name=name,
            activate_when=list(getattr(node, "activate_when", []) or []),
            suppress_when=list(getattr(node, "suppress_when", []) or []),
            energy_cost=eval_num(getattr(node, "energy_cost", None), 0.0),
            fatigue_threshold=fatigue_threshold,
            fatigue_multiplier=fatigue_multiplier,
            fatigue_rest_events=fatigue_rest_events,
            priority=getattr(node, "priority", "medium") or "medium",
            body=list(getattr(node, "body", []) or []),
            promote_to=getattr(node, "promote_to", None),
        )
        self.habit_registry.register(record)
        habit_id = f"habit-{uuid.uuid4().hex[:12]}"
        metadata = {
            "id": habit_id,
            "name": name,
            "status": "registered",
            "phase": "v2.1.3-C",
            "energy_cost": record.energy_cost,
            "priority": record.priority,
            "subscriptions": sorted(record._subscribed_events),
            "body_registered": bool(record.body),
            "body_stored_in_palace": False,
        }
        self.habits[habit_id] = metadata
        event = {"type": "habit_registered", "habit_id": habit_id, "habit_name": name, "metadata": metadata, "trace_id": self.current_trace_id()}
        self.execution_history.append(event)
        # Backward-compatible v1.9 event name; body execution is still not performed in Phase B.
        legacy_event = {"type": "habit_formed", "habit_id": habit_id, "habit_name": name, "fields": metadata, "trace_id": event["trace_id"]}
        self.execution_history.append(legacy_event)
        # Declarative palace promotion only: no executable code is stored in procedural memory.
        if getattr(node, "promote_to", None) is not None:
            for palace in self.memory_palaces.values():
                palace.imprint("procedural", {"content": json.dumps({k: v for k, v in metadata.items() if k != "body"}, ensure_ascii=False, default=str), "confidence": 0.85, "source": "habit_registry", "trace_id": event["trace_id"]})
                break
        env.define(node.binding, habit_id)
        return metadata

    def evaluate_consolidate(self, node: ConsolidateStmt, env: Environment) -> Dict[str, Any]:
        palace = self.resolve_palace(self.evaluate(node.palace, env), env)
        result = palace.consolidate(node.rooms or None, affective_routing=getattr(node, "affective_routing", None), current_event_index=len(self.execution_history))
        result["trace_id"] = self.current_trace_id()
        event = {"type": "memory_consolidated", **result}
        self.execution_history.append(event)
        self.memory_audit.append(event)
        env.define(node.binding, result)
        return result

    def current_trace_id(self) -> str:
        for event in reversed(self.execution_history):
            if event.get("trace_id"):
                return str(event["trace_id"])
        return hashlib.sha256((self.run_id + str(len(self.execution_history))).encode()).hexdigest()[:16]

    def evaluate_collective_dream(self, node: CollectiveDreamStmt, env: Environment) -> Dict[str, Any]:
        if self.dream_depth > 0 or self.fracture_depth > 0 or self.integrate_depth > 0:
            raise RuntimeError("collective dream must start from base runtime context")
        participants = self.eval_participants(node.participants, env)
        if not self.policy_allows(policy_name=node.policy_ref, target="collective.*", field="collective_dream"):
            raise PolicyViolationException("collective dream requires explicit collective_dream: true policy")
        scenario = self.evaluate(node.scenario, env) if node.scenario else "collective scenario"
        converge_on = self.evaluate(node.converge_on, env) if node.converge_on else "shared_consensus"
        timeout = int(self.evaluate(node.timeout, env)) if node.timeout else 60
        session_id = hashlib.sha256(json.dumps({"p": participants, "s": scenario, "c": converge_on, "h": self.compute_history_hash()}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        trace = self._collective_trace("collective_dream", {"session_id": session_id, "participants": participants})
        initiated = {"type": "collective_dream_initiated", "session_id": session_id, "participants": participants, "scenario": scenario, "converge_on": converge_on, "depth": node.depth, **trace}
        self.execution_history.append(initiated)
        self.actor_log.append(dict(initiated))
        positions = {}
        for participant in participants:
            position = f"{participant} proposes {converge_on} for {scenario}"
            positions[participant] = position
            submitted = {"type": "collective_dream_position_submitted", "session_id": session_id, "participant": participant, "position": position, "trace_id": trace["trace_id"]}
            self.execution_history.append(submitted)
        consensus_doc = {"scenario": scenario, "converge_on": converge_on, "positions": positions, "mode": "asynchronous_mvp"}
        document_hash = hashlib.sha256(json.dumps(consensus_doc, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        signatures = {p: hashlib.sha256((p + document_hash).encode("utf-8")).hexdigest() for p in participants}
        status = "partial_consensus" if timeout <= 0 else "consensus_reached"
        result = {"session_id": session_id, "status": status, "document": consensus_doc, "document_hash": document_hash, "signatures": signatures, "participants": participants, "trace_id": trace["trace_id"]}
        final_type = "collective_dream_timeout" if timeout <= 0 else "collective_dream_consensus_reached"
        event = {"type": final_type, "session_id": session_id, "document_hash": document_hash, "signatures": signatures, "result": result, "trace_id": trace["trace_id"]}
        event["signature"] = self._event_signature(event)
        self.execution_history.append(event)
        self.collective_sessions[session_id] = result
        env.define(node.binding, result)
        return result

    def evaluate_distributed_consensus(self, node: DistributedConsensusStmt, env: Environment) -> Dict[str, Any]:
        self._forbid_consensus_vote_side_effect("distributed consensus")
        participants = []
        for participant in node.participants:
            try:
                participants.append(self.evaluate(participant, env))
            except RuntimeError:
                participants.append(None)
        topic = self.evaluate(node.topic, env) if node.topic else "decision"
        quorum = self.evaluate(node.quorum, env) if node.quorum else None
        timeout = self.evaluate(node.timeout, env) if node.timeout else None
        policy_ref = self._consensus_policy_ref(node.policy_ref, env)
        request = ConsensusRequest(
            topic=topic,
            participants=participants,
            quorum=quorum,
            timeout=timeout,
            policy_ref=policy_ref,
            coordinator=self.current_actor_name(env),
            statement_identity=f"source:{node.line}:{node.column}",
        )

        if self.runtime_mode == RuntimeMode.REPLAY:
            try:
                replay_event = self.peek_next_history_event()
            except (AttributeError, TypeError) as exc:
                raise ConsensusReplayIntegrityError(
                    "malformed consensus replay event before replay frontier"
                ) from exc
            if replay_event is not None:
                return self._consume_replayed_distributed_consensus(request, replay_event, node, env)

        request = replace(request, vote_source=self._select_consensus_vote_source())
        try:
            decision = self._consensus_engine.decide(request)
        except ProposalViewMutationError as exc:
            raise RuntimeError("invalid_request: proposal_view_mutated") from exc
        except ConsensusVoteRegistryMutationError as exc:
            raise RuntimeError("invalid_request: dynamic_votesource_registration") from exc
        except ConsensusVoteSideEffectError as exc:
            raise RuntimeError("invalid_request: vote_collection_side_effect") from exc
        except ConsensusValidationError as exc:
            raise RuntimeError(str(exc)) from exc
        event = dict(decision.event_payload)
        self.execution_history.append(event)
        env.define(node.binding, decision.result)
        return decision.result

    def _consume_replayed_distributed_consensus(
        self,
        request: ConsensusRequest,
        event: Any,
        node: DistributedConsensusStmt,
        env: Environment,
    ) -> Dict[str, Any]:
        """Validate and consume one recorded consensus decision without live voting."""
        if not isinstance(event, Mapping):
            raise ConsensusReplayIntegrityError("malformed consensus replay event before replay frontier")
        if event.get("type") != "distributed_consensus_decided":
            raise ConsensusReplayIntegrityError(
                "consensus replay integrity mismatch: expected distributed_consensus_decided "
                f"event, got {event.get('type')}"
            )
        if event.get("schema_version") != "consensus.event.v2":
            raise ConsensusReplayIntegrityError(
                "unsupported legacy consensus event or missing votes map"
            )

        try:
            prepared = self._consensus_engine._prepare_proposal(request)
        except ConsensusValidationError as exc:
            raise RuntimeError(str(exc)) from exc
        if prepared.proposal_id != event.get("proposal_id"):
            raise ConsensusReplayIntegrityError(
                "consensus proposal_id mismatch / non-determinism"
            )
        if prepared.statement_identity != event.get("statement_identity"):
            raise ConsensusReplayIntegrityError(
                "consensus statement_identity mismatch / non-determinism"
            )

        if "votes" not in event or not isinstance(event["votes"], Mapping):
            raise ConsensusReplayIntegrityError(
                "unsupported legacy consensus event or missing votes map"
            )
        recorded_votes = event["votes"]
        if not self._is_json_compatible_votes_map(recorded_votes):
            raise ConsensusReplayIntegrityError("malformed recorded votes: votes map is not JSON-compatible")

        try:
            replay_request = replace(request, vote_source=ExplicitVoteSource(recorded_votes))
            decision = self._consensus_engine.decide(replay_request)
        except ConsensusValidationError as exc:
            raise ConsensusReplayIntegrityError(f"malformed recorded votes: {exc}") from exc

        if decision.result["proposal_id"] != event.get("proposal_id"):
            raise ConsensusReplayIntegrityError("consensus proposal_id replay integrity mismatch")
        if decision.result["votes_hash"] != event.get("votes_hash"):
            raise ConsensusReplayIntegrityError("consensus votes_hash replay integrity mismatch")
        if decision.result["result_hash"] != event.get("result_hash"):
            raise ConsensusReplayIntegrityError("consensus result_hash replay integrity mismatch")
        if decision.event_payload["statement_identity"] != event.get("statement_identity"):
            raise ConsensusReplayIntegrityError("consensus statement_identity replay integrity mismatch")
        if decision.event_payload["type"] != event.get("type"):
            raise ConsensusReplayIntegrityError("consensus event type replay integrity mismatch")
        if decision.event_payload["schema_version"] != event.get("schema_version"):
            raise ConsensusReplayIntegrityError("consensus event schema replay integrity mismatch")

        cursor_before_consume = self.replay_cursor
        try:
            consumed_event = self.next_history_event("distributed_consensus_decided")
        except RuntimeError as exc:
            self.replay_cursor = cursor_before_consume
            raise ConsensusReplayIntegrityError(
                f"consensus replay integrity mismatch while consuming event: {exc}"
            ) from exc
        if consumed_event is None:
            self.replay_cursor = cursor_before_consume
            raise ConsensusReplayIntegrityError("consensus replay event disappeared before consumption")

        env.define(node.binding, decision.result)
        return decision.result

    @staticmethod
    def _is_json_compatible_votes_map(votes: Mapping[Any, Any]) -> bool:
        if not all(isinstance(participant, str) for participant in votes):
            return False
        try:
            json.dumps(dict(votes), allow_nan=False)
        except (TypeError, ValueError):
            return False
        return True

    def _consensus_policy_ref(self, policy_ref: Optional[str], env: Environment) -> Any:
        if policy_ref is None:
            return None
        if policy_ref in self.policies:
            return self.policies[policy_ref]
        return policy_ref

    def evaluate_swarm_fracture(self, node: SwarmFractureStmt, env: Environment) -> Dict[str, Any]:
        participants = self.eval_participants(node.participants, env)
        if not self.policy_allows(policy_name=node.policy_ref, target="swarm.*", field="swarm_fracture"):
            raise PolicyViolationException("swarm fracture requires explicit swarm_fracture: true policy")
        scenario = self.evaluate(node.scenario, env) if node.scenario else "swarm fracture"
        timeout = int(self.evaluate(node.timeout, env)) if node.timeout else 60
        trace = self._collective_trace("swarm_fracture", {"scenario": scenario, "participants": participants})
        roles = dict(node.roles or {})
        positions = {p: {"role": roles.get(p, "Participant"), "position": f"{p} handles {roles.get(p, 'Participant')} for {scenario}"} for p in participants}
        status = "aborted" if timeout < 0 else "consensus_reached"
        result = {"status": status, "scenario": scenario, "participants": participants, "roles": roles, "positions": positions, "consensus": node.consensus_strategy, "trace_id": trace["trace_id"]}
        event = {"type": "swarm_fracture_consensus_reached" if status != "aborted" else "swarm_fracture_aborted", "result": result, **trace}
        event["signature"] = self._event_signature(event)
        self.execution_history.append({"type": "swarm_fracture_initiated", "scenario": scenario, "participants": participants, "roles": roles, **trace})
        self.execution_history.append(event)
        env.define(node.binding, result)
        return result


    def metrics_snapshot(self) -> Dict[str, Any]:
        return SynapseMetrics(self).snapshot()

    def metrics_text(self) -> str:
        return SynapseMetrics(self).prometheus_text()

    def save_runtime_state(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        if self.storage_backend is None:
            raise RuntimeError("No storage backend attached; call attach_storage() first")
        rid = run_id or self.run_id
        state = self.snapshot()
        state["history_hash"] = self.compute_history_hash()
        state["history_chain"] = self.history_hash_chain()
        return self.storage_backend.save_state(rid, state)

    def load_runtime_state(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        if self.storage_backend is None:
            raise RuntimeError("No storage backend attached; call attach_storage() first")
        rid = run_id or self.run_id
        state = self.storage_backend.load_state(rid)
        if state is None:
            raise RuntimeError(f"No persisted Synapse state for run_id={rid}")
        restored = self.restore_snapshot(state)
        # Copy restored state into this instance while preserving attached storage.
        storage = self.storage_backend
        current_run_id = self.run_id
        self.__dict__.update(restored.__dict__)
        self.storage_backend = storage
        self.run_id = rid or current_run_id
        return state

    def append_runtime_events(self, events: Optional[List[Dict[str, Any]]] = None, run_id: Optional[str] = None) -> int:
        if self.storage_backend is None:
            raise RuntimeError("No storage backend attached; call attach_storage() first")
        rid = run_id or self.run_id
        payload = list(events if events is not None else self.execution_history)
        return self.storage_backend.append_events(rid, payload)

    def create_state_checkpoint(self, label: Optional[str] = None) -> Dict[str, Any]:
        """Create a JSON-safe state checkpoint artifact.

        This checkpoint records the current environment and the history offset.
        It is safe for storage and audit. It does not claim to replace a future
        continuation/bytecode cursor; for now it is a compaction primitive that
        can be paired with deterministic replay metadata.
        """
        checkpoint = {
            "type": "checkpoint",
            "version": "1.0.0",
            "label": label,
            "history_offset": len(self.execution_history),
            "global_env": self.global_env.to_dict(),
            "mailboxes": self.mailboxes,
            "actor_log_length": len(self.actor_log),
        }
        self.checkpoints.append(checkpoint)
        self.execution_history.append({
            "type": "checkpoint",
            "label": label,
            "history_offset": checkpoint["history_offset"],
        })
        return checkpoint

    def dump_state(
        self,
        source_code: Optional[str] = None,
        actor_name: str = "global",
        suspension: Optional[Suspension] = None,
        target_node: Optional[str] = None,
        reason: str = "mobility_dump",
    ) -> Dict[str, Any]:
        """Create a portable Swarm mobility envelope.

        This is the network-safe process-migration artifact. It deliberately
        contains source text + deterministic history, not Python frame state.
        A remote node restores by compiling source_code and replaying the
        execution_history until the durable cursor reaches LIVE mode.
        """
        return {
            "type": "synapse_mobility_envelope",
            "version": "1.0.0",
            "reason": reason,
            "source_node": self.node_id,
            "target_node": target_node,
            "actor_name": actor_name,
            "source_code": source_code if source_code is not None else self.source_code,
            "runtime": {
                "mailboxes": self.mailboxes,
                "actor_log": self.actor_log,
                "execution_history": self.execution_history,
                "routing_table": self.routing_table,
                "outbound_packets": self.outbound_packets,
                "runtime_mode": self.runtime_mode.name,
                "replay_cursor": self.replay_cursor,
                "policies": self.policies,
                "claims": self.claims,
                "consequences": self.consequences,
                "verification_results": self.verification_results,
                "memory_audit": self.memory_audit,
                "checkpoints": self.checkpoints,
                "spawned_actors": self.spawned_actors,
                "promises": self.promises,
                "promise_routes": self.promise_routes,
                "promise_tombstones": self.promise_tombstones,
                "llm_context_cache": self.llm_context_cache,
                "intents": self.intents,
                "intent_audit": self.intent_audit,
                "observers": [],
                "global_env": self.global_env.to_dict(),
                "evolution_tickets": self.evolution_tickets,
                "resonance_cache": self.resonance_cache,
                "history_hash": self.compute_history_hash(),
            },
            "suspension": suspension.to_dict() if suspension else None,
        }

    def load_mobility_envelope(self, envelope: Dict[str, Any]):
        if envelope.get("type") != "synapse_mobility_envelope":
            raise RuntimeError("Invalid Synapse mobility envelope")
        runtime = envelope.get("runtime", {})
        self.source_code = envelope.get("source_code")
        self.node_id = envelope.get("target_node") or self.node_id
        self.mailboxes = runtime.get("mailboxes", {"global": []})
        self.actor_log = runtime.get("actor_log", [])
        self.execution_history = runtime.get("execution_history", [])
        self.routing_table = runtime.get("routing_table", {})
        self.outbound_packets = runtime.get("outbound_packets", [])
        self.runtime_mode = RuntimeMode.REPLAY if self.execution_history else RuntimeMode.LIVE
        self.replay_cursor = 0
        self.policies = runtime.get("policies", {})
        self.claims = runtime.get("claims", {})
        self.consequences = runtime.get("consequences", {})
        self.verification_results = runtime.get("verification_results", [])
        self.memory_audit = runtime.get("memory_audit", [])
        self.checkpoints = runtime.get("checkpoints", [])
        self.spawned_actors = runtime.get("spawned_actors", {})
        self.promises = runtime.get("promises", {})
        self.promise_routes = runtime.get("promise_routes", {})
        self.promise_tombstones = runtime.get("promise_tombstones", {})
        self.llm_context_cache = runtime.get("llm_context_cache", {})
        self.intents = runtime.get("intents", {})
        self.intent_audit = runtime.get("intent_audit", [])
        self.evolution_tickets = runtime.get("evolution_tickets", {})
        self.resonance_cache = runtime.get("resonance_cache", {})
        self.memory_palaces = {k: MemoryPalace.from_dict(v) for k, v in runtime.get("memory_palaces", {}).items()}
        self.intention_cascades = runtime.get("intention_cascades", {})
        self.habits = runtime.get("habits", {})
        # Rebuild deterministic state from source + history rather than trusting
        # a Python frame. The serialized global_env is retained in the envelope
        # for diagnostics/checkpoint optimization, not as the primary resume path.
        self.global_env = Environment()
        self.bootstrap_global_env()
        return self

    def snapshot(self, suspension: Optional[Suspension] = None) -> Dict[str, Any]:
        return {
            "version": "1.0.0",
            "node_id": self.node_id,
            "source_code": self.source_code,
            "routing_table": self.routing_table,
            "outbound_packets": self.outbound_packets,
            "global_env": self.global_env.to_dict(),
            "mailboxes": self.mailboxes,
            "actor_log": self.actor_log,
            "execution_history": self.execution_history,
            "runtime_mode": self.runtime_mode.name,
            "replay_cursor": self.replay_cursor,
            "policies": self.policies,
            "claims": self.claims,
            "consequences": self.consequences,
            "verification_results": self.verification_results,
            "memory_audit": self.memory_audit,
            "checkpoints": self.checkpoints,
            "spawned_actors": self.spawned_actors,
            "promises": self.promises,
            "promise_routes": self.promise_routes,
            "promise_tombstones": self.promise_tombstones,
            "llm_context_cache": self.llm_context_cache,
            "intents": self.intents,
            "intent_audit": self.intent_audit,
            "evolution_tickets": self.evolution_tickets,
            "resonance_cache": self.resonance_cache,
            "memory_palaces": {k: v.to_dict() for k, v in self.memory_palaces.items()},
            "intention_cascades": self.intention_cascades,
            "habits": self.habits,
            "history_hash": self.compute_history_hash(),
            "history_chain": self.history_hash_chain(),
            "metrics": self.metrics_snapshot(),
            "suspension": suspension.to_dict() if suspension else None,
        }

    @classmethod
    def restore_snapshot(cls, snapshot: Dict[str, Any]) -> "Interpreter":
        interpreter = cls()
        interpreter.global_env = Environment.from_dict(snapshot["global_env"])
        interpreter.node_id = snapshot.get("node_id", "local")
        interpreter.source_code = snapshot.get("source_code")
        interpreter.routing_table = snapshot.get("routing_table", {})
        interpreter.outbound_packets = snapshot.get("outbound_packets", [])
        interpreter.mailboxes = snapshot.get("mailboxes", {"global": []})
        interpreter.actor_log = snapshot.get("actor_log", [])
        interpreter.execution_history = snapshot.get("execution_history", [])
        interpreter.runtime_mode = RuntimeMode.REPLAY if interpreter.execution_history else RuntimeMode.LIVE
        interpreter.replay_cursor = 0
        interpreter.policies = snapshot.get("policies", {})
        interpreter.claims = snapshot.get("claims", {})
        interpreter.consequences = snapshot.get("consequences", {})
        interpreter.verification_results = snapshot.get("verification_results", [])
        interpreter.memory_audit = snapshot.get("memory_audit", [])
        interpreter.checkpoints = snapshot.get("checkpoints", [])
        interpreter.spawned_actors = snapshot.get("spawned_actors", {})
        interpreter.promises = snapshot.get("promises", {})
        interpreter.promise_routes = snapshot.get("promise_routes", {})
        interpreter.promise_tombstones = snapshot.get("promise_tombstones", {})
        interpreter.llm_context_cache = snapshot.get("llm_context_cache", {})
        interpreter.intents = snapshot.get("intents", {})
        interpreter.intent_audit = snapshot.get("intent_audit", [])
        interpreter.evolution_tickets = snapshot.get("evolution_tickets", {})
        interpreter.resonance_cache = snapshot.get("resonance_cache", {})
        interpreter.memory_palaces = {k: MemoryPalace.from_dict(v) for k, v in snapshot.get("memory_palaces", {}).items()}
        interpreter.intention_cascades = snapshot.get("intention_cascades", {})
        interpreter.habits = snapshot.get("habits", {})
        interpreter.global_env.define("print", interpreter._print)
        interpreter.global_env.define("trust_at_least", interpreter.trust_at_least)
        return interpreter

    def bootstrap_global_env(self):
        self.global_env.define("print", self._print)
        self.global_env.define("trust_at_least", self.trust_at_least)
        for symbol in [
            "untrusted", "low", "medium", "high", "critical",
            "short_term", "long_term", "session", "project",
            "user_controlled", "system_controlled",
            "reversible", "irreversible",
            "deep", "shallow", "exploratory", "conservative",
            "rollback", "warn", "halt", "events", "seconds", "calls", "minute", "days", "never", "tagged", "untagged", "asc", "desc", "policy_violation", "affective_history"
        ]:
            self.global_env.define(symbol, symbol)

    def load_snapshot(self, snapshot: Dict[str, Any]):
        """Prepare this interpreter for deterministic replay from source + history.

        Unlike restore_snapshot(), this method intentionally resets the global
        environment. The source program is then re-executed from the beginning
        and nondeterministic operations are served from execution_history until
        the replay cursor reaches the end, after which the runtime switches to
        LIVE mode.
        """
        self.global_env = Environment()
        self.bootstrap_global_env()
        self.node_id = snapshot.get("node_id", self.node_id)
        self.source_code = snapshot.get("source_code", self.source_code)
        self.routing_table = snapshot.get("routing_table", {})
        self.outbound_packets = snapshot.get("outbound_packets", [])
        self.mailboxes = snapshot.get("mailboxes", {"global": []})
        self.actor_log = snapshot.get("actor_log", [])
        self.execution_history = snapshot.get("execution_history", [])
        self.runtime_mode = RuntimeMode.REPLAY if self.execution_history else RuntimeMode.LIVE
        self.replay_cursor = 0
        self.policies = snapshot.get("policies", {})
        self.claims = snapshot.get("claims", {})
        self.consequences = snapshot.get("consequences", {})
        self.verification_results = snapshot.get("verification_results", [])
        self.memory_audit = snapshot.get("memory_audit", [])
        self.checkpoints = snapshot.get("checkpoints", [])
        self.spawned_actors = snapshot.get("spawned_actors", {})
        self.promises = snapshot.get("promises", {})
        self.promise_routes = snapshot.get("promise_routes", {})
        self.promise_tombstones = snapshot.get("promise_tombstones", {})
        self.llm_context_cache = snapshot.get("llm_context_cache", {})
        self.intents = snapshot.get("intents", {})
        self.intent_audit = snapshot.get("intent_audit", [])
        return self

    def eval_binary(self, op: str, left: Any, right: Any) -> Any:
        if op == "+":
            if isinstance(left, str) or isinstance(right, str):
                return str(left) + str(right)
            return left + right
        if op == "-": return left - right
        if op == "*": return left * right
        if op == "/": 
            if right == 0:
                raise RuntimeError("Division by zero")
            return left / right
        if op == "%": return left % right
        if op == "==": return left == right
        if op == "eq": return left == right
        if op == "neq": return left != right
        if op == "gt": return left > right
        if op == "gte": return left >= right
        if op == "lt": return left < right
        if op == "lte": return left <= right
        if op == "and": return left and right
        if op == "or": return left or right
        raise RuntimeError(f"Unknown binary operator: {op}")

    def eval_unary(self, op: str, operand: Any) -> Any:
        if op == "-": return -operand
        if op == "not": return not self.is_truthy(operand)
        raise RuntimeError(f"Unknown unary operator: {op}")

    def is_truthy(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, (list, dict, str)):
            return len(value) > 0
        return True

    def eval_call(self, node: CallExpr, env: Environment) -> Any:
        callee = node.callee
        args = [self.evaluate(arg, env) for arg in node.args]

        # Прямой вызов функции по имени
        if isinstance(callee, Variable):
            fn_name = callee.name

            if self._consensus_vote_query_depth > 0 and fn_name in self.deterministic_side_effects:
                self._forbid_consensus_vote_side_effect(fn_name)

            if self.integrate_i2_skeleton_depth > 0 and fn_name in self.integrate_i2_forbidden_builtins():
                raise IntegrateIsolationViolation(f"{fn_name} is forbidden inside Alpha3g I2 integrate skeleton")

            if fn_name in self.deterministic_side_effects:
                return self.execute_side_effect(fn_name, args)

            # Сначала проверяем окружение (variables > functions > agents)
            try:
                val = env.get(fn_name)
                if callable(val) and not isinstance(val, FnDef):
                    self._forbid_consensus_vote_side_effect("Python callable")
                    return val(*args)
                if isinstance(val, FnDef):
                    return self.call_function(val, args, env)
                if isinstance(val, AgentRuntime):
                    if args:
                        self._forbid_consensus_vote_side_effect("AgentRuntime.think")
                        return val.think(str(args[0]))
                    return val
                if isinstance(val, FlowDef):
                    self._forbid_consensus_vote_side_effect("FlowDef call")
                    return self.execute_block(val.body, Environment(env))
            except ConsensusVoteSideEffectError:
                raise
            except RuntimeError:
                pass

            # Проверка встроенных
            if fn_name in BUILTINS:
                self._forbid_consensus_vote_side_effect("builtin")
                return BUILTINS[fn_name](*args)

            raise RuntimeError(f"Undefined function or agent: '{fn_name}'")

        # Вызов метода агента: Agent.method()
        if isinstance(callee, MemberAccess):
            obj = self.evaluate(callee.obj, env)
            member = callee.member

            if self._consensus_vote_query_depth > 0:
                if obj is self and member in {
                    "set_consensus_vote_source",
                    "enable_actor_method_vote_source",
                    "disable_actor_method_vote_source",
                }:
                    raise ConsensusVoteRegistryMutationError(
                        "VoteSource registry mutation is forbidden during consensus vote query"
                    )
                if isinstance(obj, AgentRuntime):
                    if member in {"think", "memory"}:
                        self._forbid_consensus_vote_side_effect(f"AgentRuntime.{member}")
                elif not isinstance(obj, (FrozenDict, FrozenList)):
                    self._forbid_consensus_vote_side_effect("Python callable")

            if isinstance(obj, AgentRuntime):
                # Ищем функцию агента в его окружении
                if obj.env:
                    try:
                        fn_def = obj.env.get_function(member)
                        return self.call_function(fn_def, args, obj.env, agent=obj)
                    except ConsensusVoteSideEffectError:
                        raise
                    except RuntimeError:
                        pass

                # Специальные методы
                if member == "think":
                    return obj.think(str(args[0]) if args else "")
                if member == "memory":
                    return obj.memory
                if member == "model":
                    return obj.model

                raise RuntimeError(f"Agent '{obj.name}' has no member or method '{member}'")

            # Вызов метода объекта Python / Synapse convenience methods
            if member == "contains":
                needle = args[0] if args else None
                return needle in obj
            if member == "__getitem__":
                if not args:
                    raise RuntimeError("__getitem__ requires an index")
                return obj[args[0]]
            if hasattr(obj, member):
                method = getattr(obj, member)
                return method(*args)

            raise RuntimeError(f"Object has no method '{member}'")

        # Вызов через переменную (first-class function)
        fn_value = self.evaluate(callee, env)
        if isinstance(fn_value, FnDef):
            return self.call_function(fn_value, args, env)
        if isinstance(fn_value, FlowDef):
            self._forbid_consensus_vote_side_effect("FlowDef call")
            return self.execute_block(fn_value.body, Environment(env))
        if callable(fn_value):
            self._forbid_consensus_vote_side_effect("Python callable")
            return fn_value(*args)

        raise RuntimeError(f"Uncallable object: {type(fn_value)}")

    def call_function(self, fn_def: FnDef, args: List[Any], env: Environment, agent: Optional[AgentRuntime] = None) -> Any:
        if len(args) != len(fn_def.params):
            raise RuntimeError(f"Expected {len(fn_def.params)} arguments, got {len(args)}")

        # Use closure environment if available, otherwise caller's environment
        parent_env = fn_def.closure if fn_def.closure else env
        func_env = Environment(parent_env)

        # Если агент — добавляем в окружение
        if agent:
            func_env.define("self", agent)
            func_env.define_agent(agent.name, agent)

        for param, arg in zip(fn_def.params, args):
            func_env.define(param, arg)

        try:
            result = self.execute_block(fn_def.body, func_env)
            return result
        except ReturnException as e:
            return e.value
