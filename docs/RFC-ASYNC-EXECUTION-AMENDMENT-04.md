# RFC-ASYNC-EXECUTION-AMENDMENT-04

**Title:** Named pre-execution refusals on stderr  
**Requirement ID:** `REQ-ASYNC-CLI-01`  
**Parent RFC:** `docs/RFC-ASYNC-EXECUTION.md`  
**Prior amendments:** `docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md`, `docs/RFC-ASYNC-EXECUTION-AMENDMENT-02.md`, `docs/RFC-ASYNC-EXECUTION-AMENDMENT-03.md`  
**Document status:** `DRAFT — TEAM REVIEW AND PRODUCT OWNER APPROVAL REQUIRED`

This amendment documents an implementation for review. It is not an approval
and does not by itself authorize a merge.

---

# 1. Amendment rule

This is an additive amendment.

- The parent RFC places diagnostics on stderr (§11) and keeps exactly one JSON
  document on stdout (AC-P2-22). This amendment fixes what a refusal before
  execution writes to stderr.
- The public JSON is unchanged (§11.3): it keeps the same codes and the same
  generic messages.
- The secrets boundary of AC-P2-23 is unchanged.

Before this amendment, a refused program printed only "Unsupported durable
operation". The reason the validator had established (for example
`durable CallExpr rejects non-allowlisted callee: Planner`) was lost.

# 2. Refusals that name themselves

1. Every refusal of the durable-safety validator (§27.8) and of the durable
   cognitive profile (Amendment 03, §2). These exit with code `25`.
2. An initial binding the program cannot receive (§9). This exits with code
   `2`.

Every other refusal writes nothing to stderr, as before:

- source parse errors;
- `run_id`, state-directory, input-file and signal-file refusals;
- every refusal of `resume`.

Their texts have not been audited against the allowlist of section 4.

A run that completes or suspends writes nothing to stderr.

# 3. The line

A refusal writes exactly one line:

```text
synapse: <program path as given>:<line>: UNSUPPORTED_DURABLE_OPERATION_OR_REASON: <reason>
synapse: INVALID_CLI_INPUT: initial binding '<name>' <reason>
```

- **Error code.** The public error code (§12) is the only stable part of the
  line. The reason that follows it is not a contract and may change wording.
- **Line number.** `<line>` is the source line of the innermost construct the
  refusal was raised for. Validation places a refusal at the first construct
  that reports it on the way out of the recursion: a call inside an `if` body
  is reported at its own line, not at the line of the `if`.
- **No line number.** The line number is omitted when a refusal belongs to no
  construct, such as the node inventory gate (§27.9).
- **No column.** Columns are not reported, because the lexer records the
  column after a token, not where it starts.
- **Path.** The program path is the path the operator passed, not a resolved
  one.

# 4. Allowlist

A line contains only:

- the public error code;
- AST class names and the classification constraints of the validator's
  registry (§27.5–§27.7);
- identifiers from the program source (callee, function and binding names);
- a source line number;
- the program path as given;
- the name of a refused initial binding.

A line never contains:

- a literal of the source (a secret can be written into the source as well);
- a rendered expression;
- the value of an initial binding;
- a signal, a request or a prompt;
- an object representation or a traceback.

# 5. Encoding

- A character that is not printable is escaped (`\n`, `\x1b`, …), never
  emitted. The line is one line whatever the input contains.
- A binding name is quoted and cut at 64 characters.
- The whole line is cut at 512 characters.

# 6. Rationale

- **Channel.** ISO C (N1570 §7.21.3) defines standard error as the stream "for
  writing diagnostic output". POSIX (XCU §1.4) sends diagnostics to standard
  error "only when the exit status indicates that an error occurred". The line
  is therefore written only for a refusal.
- **Format.** The GNU Coding Standards give the format
  `program:sourcefile:lineno: message`.
- **Prior art.** Durable workflow engines name the refused construct and never
  its inputs. Examples: Temporal's `workflowcheck` and workflow sandbox, the
  Azure Durable Task analyzers, DBOS.
- **Secrets.** CWE-209 and CWE-532 keep secrets out of error messages and logs.
  stderr often ends up in CI logs.
- **Injection.** CWE-117 is why control characters are escaped.

# 7. Evidence

The evidence is in `tests/test_durable_execution.py`:

- `test_validator_rejects_unsupported_operations_before_artifact` and
  `test_assert_statement_rejects_suspension_descendants_before_artifact`: the
  exact line, with the public JSON unchanged.
- `test_a_refusal_inside_a_block_names_the_innermost_line_and_never_a_value`:
  the innermost line is reported, and neither a source literal nor an
  initial-binding value reaches stdout or stderr.
- `test_initial_bindings_refuse_reserved_and_source_owned_names_before_any_effect`:
  the binding is named and its value is not printed.
- `test_a_binding_name_with_control_characters_is_named_on_one_escaped_line`.
- `test_a_refusal_line_escapes_the_program_path_and_bounds_its_reason`: a
  line separator (U+2028) in the program path is escaped, and a reason built
  from a 600-character identifier is cut to the bound.
- `test_a_refusal_outside_the_allowlist_names_nothing_on_stderr`: a malformed
  input file is refused with an empty stderr, and its content never appears.
- `test_resume_refusals_never_echo_the_signal`.
- `test_a_cognitive_profile_refusal_names_the_innermost_statement_line`, in
  `tests/test_durable_cognitive_diagnostics.py`: the cognitive profile takes
  a named run lock (`filelock`), so it runs with the package's dependencies,
  outside the dependency-free P2a suite.

The existing tests for completed and suspended runs keep `stderr == ""`.
