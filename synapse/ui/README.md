# Synapse desktop source

The desktop application lives inside the existing `synapse/` product root.
It is a Tauri 2 application with a React/TypeScript renderer. The adjacent
`synapse/machine` Python package owns the canonical machine control endpoint.

| Directory | Responsibility |
|---|---|
| `src/components` | Connection and session presentation |
| `src/connection` | Validated desktop observation and renderer state |
| `src/contracts` | Strict incoming JSON and generated contract bindings |
| `src/styles` | Theme and layout |
| `contracts` | Native-to-renderer bootstrap schema |
| `src-tauri/src` | Native controller, framing, validation, diagnostics, publication |
| `src-tauri/src/lifecycle` | Windows Job and Linux user-service process ownership |
| `src-tauri/src/desktop` | Trusted Tauri caller/Channels bindings and platform host |
| `src-tauri/packaging` | Platform launch integration |
| `tooling` | Build-time schema/type generation |

Product source never imports external acceptance code. Generated contracts,
native permissions, build products, and the bundled interpreter are build outputs.
The desktop subtree is excluded from Python package discovery to avoid recursively
embedding the frontend and its dependencies in the bundled Python distribution.

The current application implements connection bootstrap and control-only
ping/status/shutdown. Task execution, projects, editing, memory, agents, and
approval workflows remain unimplemented. A successful control handshake grants
no domain execution authority.

See the [development guide](../../docs/ui/DESKTOP_DEVELOPMENT.md) and
[current delivery status](../../docs/ui/IMPLEMENTATION_STATUS.md) for setup,
verified behavior, and the remaining platform work.
