# RFC: LLM / Prompt CVM Bridge

**Status:** Draft  
**Track:** Alpha3e Track A  
**Target version:** 2.2.0-alpha3e  
**Closes fallbacks:** LLMCall (18), PromptExpr (11) → total 29 nodes  
**Expected HOST_ABI bump:** `2.2.0-alpha3e-llm`

---

## 1. Motivation

`LLMCall` and `PromptExpr` are the two largest fallback clusters in the
alpha3e-p0 corpus report (29 nodes, ~22% of remaining fallbacks). They
currently execute entirely in the tree-walker interpreter, bypassing:

- gas metering
- VMSnapshot serialisation
- deterministic replay
- capability enforcement at the CVM level

This RFC specifies how both node types are compiled into CVM bytecode
while preserving the architectural boundary: **CVM handles sequencing and
state; Host/Bridge handles external I/O and caching**.

---

## 2. Opcodes

Three new first-class opcodes are added to the CVM instruction set.

### 2.1 `PROMPT_BUILD`

```
PROMPT_BUILD  template_hash: str  variable_names: list[str]
```

**Stack contract:**
- Pops `len(variable_names)` values from the stack (evaluated variable bindings).
- Pushes a `PromptEnvelope` dict onto the stack:

```python
{
    "type":            "prompt_envelope",
    "template_hash":   str,           # SHA-256 of template source text
    "variables":       dict,          # {name: value} — already evaluated
    "variables_hash":  str,           # SHA-256(json_canonical(variables))
}
```

**Gas cost:** 3 (deterministic, no host call).  
**CVM effect:** Pure computation. No pause, no host call, no history event.

### 2.2 `LLM_REQUEST`

```
LLM_REQUEST  schema_hash: str  engine_params: dict  cache_policy: str
```

**Stack contract:**
- Pops one `PromptEnvelope` from the stack (output of `PROMPT_BUILD`).
- Computes `content_key`:

```python
content_key = SHA256(
    envelope["template_hash"] +
    envelope["variables_hash"] +
    schema_hash +
    SHA256(json_canonical(engine_params)) +
    engine_params.get("model_version", "")
)
```

- Creates `pending_host_call` envelope with `symbol = "llm.request"`.
- Transitions VM to `STATUS_PAUSED_HOST_CALL`.
- Does **not** push any value — result arrives via `LLM_RESUME`.

**Gas cost:** 25 (matches existing `LLM_EVAL` budget).  
**History event written by Bridge (not CVM):** `llm_request_dispatched`.

### 2.3 `LLM_RESUME`

```
LLM_RESUME  (no operands)
```

**Stack contract:**
- Called by `VMBridge.resume_host_call()` after result is available.
- Pushes resolved LLM result onto the stack as a plain dict/str.
- Transitions VM from `STATUS_PAUSED_HOST_CALL` back to `STATUS_RUNNING`.

**Gas cost:** 2 (resume bookkeeping only).  
**History event written by Bridge:** `llm_call_resolved` or `llm_call_failed`.

---

## 3. Content-Addressable Cache

The cache lives entirely in `VMBridge`. CVM never touches it.

### 3.1 Cache key

```python
content_key = SHA256(
    template_hash       # SHA-256 of raw template string
    || variables_hash   # SHA-256 of canonical JSON of bound variables
    || schema_hash      # SHA-256 of expected output schema, "" if none
    || engine_params_hash  # SHA-256 of canonical JSON of {model, temperature, max_tokens}
    || model_version    # explicit version string e.g. "gpt-4o-2024-08"
)
```

`model_version` is mandatory in `engine_params`. If absent, Bridge raises
`VMHostError(code="LLM_MISSING_MODEL_VERSION")` before the host call.

### 3.2 Cache invalidation policy

Configured per-agent in Synapse source, overridable by governance policy:

```synapse
agent Analyst {
    model "gpt-4o"
    llm_cache "model_change"   // never | model_change | policy_guard
}
```

| Policy | Behaviour |
|--------|-----------|
| `never` | Never use cache. Dev/test mode only. |
| `model_change` | Cache valid until `model_version` changes. **Default.** |
| `policy_guard` | Cache valid only if governance engine approves. Strict mode. |

Governance `policy` can override the per-agent setting:

```synapse
policy StrictLLM {
    guard(args) {
        require args.cache_policy != "never", "never-cache forbidden in prod"
    }
}
```

### 3.3 Cache lookup in Bridge

```
LLM_REQUEST received:
  1. Check capability gate → if denied: write LLM_REQUEST_DENIED + VMHostError
  2. Compute content_key
  3. If replay mode: look up LLM_RESPONSE_CACHED in execution_history by content_key
       found  → resolve_promise(call_id, cached_result)
       missing → VMHostError(code="REPLAY_CACHE_MISS")
  4. If live mode + cache_policy != "never": check Bridge cache
       hit  → resolve_promise(call_id, cached_result), write LLM_RESPONSE_CACHED_HIT
       miss → dispatch to LLM provider, on response: cache + write LLM_RESPONSE_CACHED + resolve
```

---

## 4. Failure Taxonomy

All failures are deterministic: a given failure always produces the same
history event and the same stack value. Replay reproduces them exactly.

| Failure | History event | Stack value |
|---------|--------------|-------------|
| Capability denied | `LLM_REQUEST_DENIED` + `LLM_CAPABILITY_MISSING` | `VMHostError(code="CAPABILITY_DENIED")` |
| Missing model version | `LLM_REQUEST_INVALID` | `VMHostError(code="LLM_MISSING_MODEL_VERSION")` |
| Provider error / 5xx | `LLM_HOST_FAILURE(retryable=true)` | `VMHostError(code="LLM_PROVIDER_ERROR", retryable=True)` |
| Timeout | `LLM_HOST_FAILURE(retryable=false)` | `VMHostError(code="LLM_TIMEOUT", retryable=False)` |
| Schema validation fail | `LLM_RESPONSE_SCHEMA_ERROR` | `VMHostError(code="SCHEMA_MISMATCH")` |
| Replay cache miss | `LLM_RESPONSE_MISSING` | `VMHostError(code="REPLAY_CACHE_MISS")` |

**Retry semantics:** retry is the responsibility of agent code, not the
runtime. `retryable=True` is a hint only:

```synapse
let result = llm("Analyse this")
if result.error and result.retryable {
    result = llm("Analyse this")   // explicit retry in agent logic
}
```

---

## 5. VMBridge Integration

### 5.1 Dispatch symbols added to HOST_ABI

```python
BRIDGE_DISPATCHED = {
    ...existing symbols...,
    "llm.request",    # LLM_REQUEST opcode dispatches here
    "prompt.build",   # reserved for future explicit prompt introspection
}
```

### 5.2 Schema validation — Bridge-side

Schema validation happens in Bridge before `resolve_promise()`. CVM never
sees the raw LLM response:

```
Bridge receives provider response:
  1. Parse response text
  2. If schema_hash != "": validate against schema
       invalid → write LLM_RESPONSE_SCHEMA_ERROR, VMHostError on stack
       valid   → continue
  3. Write LLM_RESPONSE_CACHED event to execution_history
  4. Call resolve_promise(call_id, result_dict)
  5. VM receives clean result dict via LLM_RESUME
```

CVM isolation invariant preserved: VM does not know about schema,
provider, or cache. It only knows `call_id → result`.

### 5.3 Capability enforcement

Two history events written on denial (security audit sees both layers):

```python
# event 1 — semantic intent
{"type": "LLM_REQUEST_DENIED", "call_id": ..., "capability_missing": "llm.request"}
# event 2 — VM error record  
{"type": "LLM_CAPABILITY_MISSING", "call_id": ..., "agent_id": ...}
```

### 5.4 HOST_ABI version bump

`HOST_ABI_VERSION` will be bumped to `"2.2.0-alpha3e-llm"` when this RFC
is implemented, because `llm.request` is a new VM-visible host-call symbol.

---

## 6. CI Golden Replay Package

LLM responses are stored as `LLM_RESPONSE_CACHED` events embedded in
`execution_history`. This is the single source of truth — no separate
cache files.

### 6.1 Event format

```json
{
  "type":         "LLM_RESPONSE_CACHED",
  "call_id":      "sha256:...",
  "content_key":  "sha256:...",
  "result":       { ... },
  "model_version": "gpt-4o-2024-08",
  "schema_hash":  "sha256:...",
  "history_hash": "sha256:..."
}
```

### 6.2 Replay-without-tokens workflow

```bash
# Record a golden run (requires live LLM):
synapse run agent.syn --record reports/golden_llm_alpha3e.json

# Replay without tokens (CI mode):
synapse replay reports/golden_llm_alpha3e.json
```

In replay mode Bridge checks `execution_history` for `LLM_RESPONSE_CACHED`
by `content_key` before any provider call. Provider is never contacted.

### 6.3 CI integration

```yaml
# .github/workflows/ci.yml addition (Track A)
- name: Run LLM golden replays (no tokens)
  run: python -m pytest tests/test_cvm_llm_bridge_golden.py -v
```

---

## 7. Compiler changes

### 7.1 `CognitiveCompiler` additions

```python
def compile_prompt_expr(self, node: PromptExpr):
    # evaluate template variables onto stack
    for var_name, var_node in node.args.items():
        self.compile_expr(var_node)
    self._emit("PROMPT_BUILD",
               hashlib.sha256(node.template.encode()).hexdigest(),
               list(node.args.keys()))

def compile_llm_call(self, node: LLMCall):
    # compile the prompt argument (may be PromptExpr or string literal)
    self.compile_expr(node.prompt)
    if not isinstance(node.prompt, PromptExpr):
        # bare string — wrap in minimal envelope via PROMPT_BUILD with no vars
        self._emit("PROMPT_BUILD",
                   "sha256:inline",
                   [])
    schema_hash = ""  # extended in future when schema annotation added to LLMCall AST
    engine_params = {
        "model":        node.model or "default",
        "temperature":  node.temperature,
        "max_tokens":   node.max_tokens,
        "model_version": node.model or "default",
    }
    cache_policy = "model_change"
    self._emit("LLM_REQUEST", schema_hash, engine_params, cache_policy)
```

### 7.2 `vm_routing.py` additions

```python
CVM_AST_NODE_TYPES_V22 = {
    ...existing...,
    "PromptExpr",   # PROMPT_BUILD
    "LLMCall",      # PROMPT_BUILD + LLM_REQUEST + LLM_RESUME
}
```

---

## 8. Acceptance criteria

```
[ ] PromptExpr compiles to PROMPT_BUILD without HOST_EVAL fallback.
[ ] LLMCall compiles to PROMPT_BUILD + LLM_REQUEST without HOST_EVAL fallback.
[ ] LLM_REQUEST creates pending_host_call and pauses VM.
[ ] resume_host_call(call_id, result) resumes VM via LLM_RESUME, result on stack.
[ ] Replay uses LLM_RESPONSE_CACHED events; no provider call made.
[ ] CAPABILITY_DENIED writes two history events.
[ ] All five failure types produce deterministic history events + VMHostError.
[ ] content_key includes model_version; absent model_version raises LLM_MISSING_MODEL_VERSION.
[ ] Schema validation happens Bridge-side before resolve_promise().
[ ] corpus fallback count decreases by >= 29 nodes.
[ ] corpus_coverage_ratio >= 0.938 after Track A.
[ ] HOST_ABI_VERSION bumped to 2.2.0-alpha3e-llm.
[ ] tests/test_cvm_llm_bridge_alpha3e.py: all new tests pass.
[ ] tests/test_cvm_llm_bridge_golden.py: golden replays pass without tokens.
[ ] 401+ existing tests continue to pass (zero regression).
```
