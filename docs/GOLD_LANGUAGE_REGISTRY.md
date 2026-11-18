# Gold language profiles

Inspect installed profiles with `python -m synapse project languages --check`. It loads the pinned grammars, compiles each declaration query and returns a nonzero exit if a grammar is unavailable. Install the Synapse wheel with its `native-code` extra. The R profile is distributed as a second wheel.

The Python profile uses the existing revision-aware AST/import-root resolver. C++, Go and JavaScript preserve their published tree-sitter v1 binding bytes. Other profiles use queries over pinned offline grammars. The native-code extra also pins the C# package that the grammar pack loads separately. A binding includes the revision, file, qualified name, symbol kind, resolver identity and SHA-256 of the declaration's source span. For query profiles the resolver identity includes the profile, owning package/version, grammar pack version and parser version. A consumer re-reads the committed or retained bytes and reproduces that binding. File contents do not execute during discovery.

## Extending the registry

The installable example is in `language-packs/r`. Copy its wheel layout and declare the `synapse.gold.languages.v1` metadata entry point as `profile_id = "package:resource"`. It selects `package/resource.json` from the installed distribution. Gold checks the resource length and digest against the wheel RECORD. The metadata entry point is read as data. Install a regular built wheel and start a new Synapse process to discover it.

A query profile declares a unique ID and suffixes, a display label, the grammar name, optional per-suffix grammar variants, the query, declaration extent and driver. Use the R profile as a complete example. The pinned pack must provide the selected grammar. Each match captures one `@name` inside a declaration and one role such as `@function`, `@class`, `@method`, `@owner`, `@rule` or `@view`. Duplicate IDs or suffixes invalidate registry construction. Duplicate qualified symbols and invalid syntax refuse binding. Ambiguous `.h` headers have no default owner. Only declarations covered by a profile's query are named. Assembly, SQL, HTML, CSS and HCL profiles address labels, views/tables, elements with static quoted IDs, rules and local attributes respectively.

Compilation and behavioral verification use a project's declared tools, recipes and oracle. The registry supplies structural targets to the existing Gold commands. It does not install language toolchains or infer a project's build procedure.
