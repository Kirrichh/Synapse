"""
Synapse - Язык программирования для ИИ
"""
import sys as _sys
if _sys.version_info < (3, 10):
    raise RuntimeError(
        f"Synapse requires Python 3.10+, got {_sys.version_info.major}.{_sys.version_info.minor}. "
        "Please upgrade your Python interpreter."
    )

_LANGUAGE_VERSION = "2.2.0-alpha3e"

from .lexer import Lexer
from .parser import Parser
from .interpreter import Interpreter, Suspension, Environment, DreamSandboxEnvironment, DreamSandboxIsolationError, canonical_deepcopy, RuntimeMode, PolicyViolationException, PolicyCompilationError, IdentityCrisisError, DreamIsolationViolation, IntegrateIsolationViolation, IntegrateAssertionFailed, EvolutionTicketExpired, FracturePanicException, OrphanedIdentityException, NestedFractureException, EvolutionCooldownException, GuardMutationError, ConsensusBiasMissingError, ReplayIntegrityError, AffectiveIsolationViolation, ResonancePrivacyException, CollectiveConsensusTimeout
from .builtins import LLMBackend, AgentRuntime, Memory, DurableActorRef, DurablePromise
from .persistence import StorageBackend, InMemoryStorage, SQLiteStorage, StorageError
from .metrics import SynapseMetrics
from .hardening import RuntimeStressHarness, hash_event_chain, verify_event_chain
from .memory import MemoryPalace
from .storage_backends import CognitiveStorageBackend, InMemoryCognitiveStorage, SQLiteCognitiveStorage, PostgreSQLCognitiveStorage, RedisSpine
from .affective import AffectiveState, modulation_from_state, affective_bridge
from .somatic import compute_gut_feeling
from .bytecode import CognitiveCompiler, BytecodeProgram, Instruction
from .cvm import CognitiveVM, VMState, OutOfEnergy, VMSnapshot, VMSnapshotFormatError, VMConflictingSourceError, VMMultipleCheckpointError, VMResumeSyncError, VMTamperDetectedError, UnknownOpcodeError
from .threshold import ThresholdRegistry, ThresholdPurityViolation
from .habit import EnergyPool, ContextTracker, AgentMode, ContextStackError, HabitRegistry, HabitEvaluator, HabitRuntimeRecord, HabitActivationEngine, HabitState, HabitRecursionError
from synapse.version import __version__, LANGUAGE_VERSION, RUNTIME_VERSION, SPEC_VERSION

__all__ = ["Lexer", "Parser", "Interpreter", "Suspension", "Environment", "DreamSandboxEnvironment", "DreamSandboxIsolationError", "canonical_deepcopy", "LLMBackend", "AgentRuntime", "Memory", "RuntimeMode", "PolicyViolationException", "PolicyCompilationError", "IdentityCrisisError", "DreamIsolationViolation", "IntegrateIsolationViolation", "IntegrateAssertionFailed", "EvolutionTicketExpired", "FracturePanicException", "OrphanedIdentityException", "NestedFractureException", "EvolutionCooldownException", "GuardMutationError", "ConsensusBiasMissingError", "ReplayIntegrityError", "AffectiveIsolationViolation", "ResonancePrivacyException", "CollectiveConsensusTimeout", "DurableActorRef", "DurablePromise", "StorageBackend", "InMemoryStorage", "SQLiteStorage", "StorageError", "SynapseMetrics", "RuntimeStressHarness", "hash_event_chain", "verify_event_chain", "MemoryPalace", "CognitiveStorageBackend", "InMemoryCognitiveStorage", "SQLiteCognitiveStorage", "PostgreSQLCognitiveStorage", "RedisSpine", "AffectiveState", "modulation_from_state", "affective_bridge", "compute_gut_feeling", "CognitiveCompiler", "BytecodeProgram", "Instruction", "CognitiveVM", "VMState", "OutOfEnergy", "VMSnapshot", "VMSnapshotFormatError", "VMConflictingSourceError", "VMMultipleCheckpointError", "VMResumeSyncError", "VMTamperDetectedError", "UnknownOpcodeError", "ThresholdRegistry", "ThresholdPurityViolation", "EnergyPool", "ContextTracker", "AgentMode", "ContextStackError", "HabitRegistry", "HabitEvaluator", "HabitRuntimeRecord", "HabitActivationEngine", "HabitState", "HabitRecursionError", "__version__", "LANGUAGE_VERSION", "RUNTIME_VERSION", "SPEC_VERSION"]

def run(source: str, interpreter: Interpreter = None) -> str:
    """Выполнить код Synapse и вернуть вывод."""
    lexer = Lexer(source)
    tokens = lexer.scan_tokens()
    parser = Parser(tokens)
    ast = parser.parse()

    if interpreter is None:
        interpreter = Interpreter()

    interpreter.source_code = source
    interpreter.interpret(ast)
    return interpreter.get_output()

def compile_to_ast(source: str):
    """Скомпилировать код в AST."""
    lexer = Lexer(source)
    tokens = lexer.scan_tokens()
    parser = Parser(tokens)
    return parser.parse()


def run_until_suspension(source: str, interpreter: Interpreter = None):
    """Run Synapse in coroutine mode until completion or first durable suspension.

    Returns (interpreter, status), where status is either a Suspension or the
    final returned value.
    """
    ast = compile_to_ast(source)
    if interpreter is None:
        interpreter = Interpreter()
    interpreter.source_code = source
    flow = interpreter.interpret_async(ast)
    try:
        status = next(flow)
    except StopIteration as done:
        status = done.value
    return interpreter, status
