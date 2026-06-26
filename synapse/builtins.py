"""
Synapse Builtins - Встроенные функции и LLM backend
"""
import random
import time
import uuid
from typing import Any, List, Dict, Optional, Mapping

from .llm import LLMGateway, LLMProviderStatus, LLMResult, LLMTokenStatus, LLMUsage, PrivacyContext
from .llm.gateway import config_from_env

class LLMBackend:
    """Product-facing LLM adapter with mock-compatible default behavior."""

    def __init__(
        self,
        default_model="mock",
        *,
        provider: Optional[str] = None,
        mode: Optional[str] = None,
        api_key: Optional[str] = None,
        tier: Optional[str] = None,
        user_region: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
        gateway: Optional[LLMGateway] = None,
    ):
        self.default_model = default_model
        self.call_count = 0
        self.history = []
        self.gateway_config = config_from_env(
            default_model=default_model,
            provider=provider,
            mode=mode,
            api_key=api_key,
            tier=tier,
            user_region=user_region,
            environ=environ,
        )
        self.gateway = gateway or (LLMGateway(self.gateway_config) if self.gateway_config.real_provider_enabled else None)
        self.last_result: Optional[LLMResult] = None

    def _mock_complete_result(self, prompt: str, model: str) -> LLMResult:
        responses = {
            "hello": "Hello! I am an AI assistant ready to help.",
            "translate": "[Translated text would appear here via real LLM]",
            "summarize": "[Summary would be generated here]",
            "code": "```python\n# Generated code would appear here\n```",
            "analyze": "Based on my analysis, I found several key patterns...",
        }
        prompt_lower = prompt.lower()
        for key, response in responses.items():
            if key in prompt_lower:
                text = f"[{model}] {response}"
                break
        else:
            text = f"[{model}] Processing: {prompt[:50]}..."
        return LLMResult(
            status=LLMProviderStatus.COMPLETED,
            provider="mock",
            model=model,
            response_text=text,
            usage=LLMUsage(
                token_status=LLMTokenStatus.UNAVAILABLE,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                thinking_included=False,
                diagnostics={},
            ),
        )

    def complete_result(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 100,
        privacy_context: Optional[PrivacyContext] = None,
    ) -> LLMResult:
        """Return a typed product result while preserving mock-compatible defaults."""
        self.call_count += 1
        selected_model = model or self.default_model
        if self.gateway is not None:
            result = self.gateway.complete(
                prompt,
                model=selected_model if selected_model != "mock" else None,
                temperature=temperature,
                max_tokens=max_tokens,
                privacy_context=privacy_context,
            )
        else:
            result = self._mock_complete_result(prompt, selected_model)
        self.last_result = result
        self.history.append({
            "prompt": prompt[:50],
            "model": result.model,
            "provider": result.provider,
            "status": result.status.value,
            "result": result.response_text[:50],
            "usage": result.usage.to_dict(),
        })
        return result

    def complete(self, prompt: str, model: Optional[str] = None,
                 temperature: float = 0.7, max_tokens: int = 100,
                 privacy_context: Optional[PrivacyContext] = None) -> str:
        """Backward-compatible string API over the product LLM boundary."""
        result = self.complete_result(
            prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            privacy_context=privacy_context,
        )
        if result.status is not LLMProviderStatus.COMPLETED:
            raise RuntimeError(f"LLM provider call failed: {result.status.value}: {result.error_message}")
        return result.response_text

    def thought_chain(self, steps: List[str], aggregator: str = "chain") -> str:
        """Цепочка рассуждений."""
        results = []
        context = ""
        for i, step in enumerate(steps):
            prompt = f"Step {i+1}: {step}\nContext: {context}"
            result = self.complete(prompt, max_tokens=200)
            results.append(result)
            context += f"\n- {result}"

        if aggregator == "chain":
            return "\n".join(results)
        elif aggregator == "best":
            return max(results, key=len)
        elif aggregator == "consensus":
            return f"Consensus of {len(results)} steps: {results[-1]}"
        return "\n".join(results)

    def superpose(self, branches: Dict[str, str], selector: str = "first") -> str:
        """Параллельное выполнение ветвей (симуляция)."""
        if selector == "first":
            return list(branches.values())[0]
        elif selector == "best":
            return max(branches.values(), key=len)
        elif selector == "consensus":
            return f"Merged: {list(branches.values())[-1]}"
        elif selector == "all":
            return "\n---\n".join(f"[{k}]: {v}" for k, v in branches.items())
        return list(branches.values())[0]

class Memory:
    """Система памяти для агентов."""

    def __init__(self, capacity: int = 100):
        self.short_term = []
        self.long_term = {}
        self.capacity = capacity

    def read(self, key: Optional[str] = None) -> Any:
        if key is None:
            return self.short_term[-5:] if self.short_term else []
        return self.long_term.get(key)

    def write(self, value: Any, key: Optional[str] = None):
        if key:
            self.long_term[key] = value
        else:
            self.short_term.append(value)
            if len(self.short_term) > self.capacity:
                self.short_term.pop(0)

    def clear(self):
        self.short_term = []

    def forget(self, key: Optional[str] = None):
        if key is None:
            removed = list(self.short_term)
            self.short_term = []
            return removed
        if key in self.long_term:
            return self.long_term.pop(key)
        before = len(self.short_term)
        self.short_term = [x for x in self.short_term if str(key) not in str(x)]
        return {"removed_from_short_term": before - len(self.short_term)}

    def recall(self, pattern: str) -> List[Any]:
        return [x for x in self.short_term if pattern in str(x)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "short_term": self.short_term,
            "long_term": self.long_term,
            "capacity": self.capacity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Memory":
        memory = cls(capacity=data.get("capacity", 100))
        memory.short_term = data.get("short_term", [])
        memory.long_term = data.get("long_term", {})
        return memory

class AgentRuntime:
    """Runtime для агента."""

    def __init__(self, name: str, model: str, memory_config: Optional[str] = None, trust_level: Optional[str] = None, trust_scope: Optional[List[str]] = None):
        self.name = name
        self.model = model
        self.trust_level = trust_level or "medium"
        self.trust_scope = trust_scope or []
        self.memory = Memory()
        if memory_config:
            try:
                cap = int(memory_config)
                self.memory.capacity = cap
            except:
                pass
        self.llm = LLMBackend(model)
        self.tools = {}
        self.env = None  # Will be set by interpreter

    def register_tool(self, name: str, fn):
        self.tools[name] = fn

    def call_tool(self, name: str, *args):
        if name in self.tools:
            return self.tools[name](*args)
        raise RuntimeError(f"Tool '{name}' not found")

    def think(self, prompt: str) -> str:
        result = self.llm.complete(prompt, model=self.model)
        self.memory.write({"prompt": prompt, "result": result})
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "trust_level": self.trust_level,
            "trust_scope": self.trust_scope,
            "memory": self.memory.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentRuntime":
        agent = cls(data["name"], data.get("model", "mock"), trust_level=data.get("trust_level", "medium"), trust_scope=data.get("trust_scope", []))
        agent.memory = Memory.from_dict(data.get("memory", {}))
        return agent


class DurableActorRef:
    """Serializable reference to a spawned durable actor process."""

    def __init__(self, actor_name: str, process_id: str, node: str = "local"):
        self.actor_name = actor_name
        self.process_id = process_id
        self.name = process_id
        self.node = node

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actor_name": self.actor_name,
            "process_id": self.process_id,
            "node": self.node,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DurableActorRef":
        return cls(data.get("actor_name", "actor"), data.get("process_id", "actor"), data.get("node", "local"))

    def __repr__(self):
        return f"<DurableActorRef {self.process_id}@{self.node}>"


class DurablePromise:
    """Serializable placeholder for an external durable completion."""

    def __init__(self, promise_id: str, reason: str, request: Any = None, status: str = "pending", result: Any = None):
        self.promise_id = promise_id
        self.reason = reason
        self.request = request
        self.status = status
        self.result = result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promise_id": self.promise_id,
            "reason": self.reason,
            "request": self.request,
            "status": self.status,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DurablePromise":
        return cls(
            data.get("promise_id"),
            data.get("reason", "external_signal"),
            data.get("request"),
            data.get("status", "pending"),
            data.get("result"),
        )

    def __repr__(self):
        return f"<DurablePromise {self.promise_id}:{self.status}>"

# Встроенные функции языка
BUILTINS = {
    "print": lambda *args: print(*args),
    "len": lambda x: len(x),
    "range": lambda *args: list(range(*args)),
    "time": lambda: time.time(),
    "random": lambda: random.random(),
    "uuid": lambda: str(uuid.uuid4()),
    "type": lambda x: type(x).__name__,
    "str": lambda x: str(x),
    "int": lambda x: int(x),
    "float": lambda x: float(x),
    "list": lambda x: list(x),
    "dict": lambda: {},
    "abs": lambda x: abs(x),
    "sum": lambda x: sum(x),
    "max": lambda *args: max(args) if len(args) > 1 else max(args[0]),
    "min": lambda *args: min(args) if len(args) > 1 else min(args[0]),
    "sorted": lambda x: sorted(x),
    "reversed": lambda x: list(reversed(x)),
    "enumerate": lambda x: list(enumerate(x)),
    "zip": lambda *args: list(zip(*args)),
    "map": lambda f, x: list(map(f, x)),
    "filter": lambda f, x: list(filter(f, x)),
    "any": lambda x: any(x),
    "all": lambda x: all(x),
}
