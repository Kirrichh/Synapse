# Desktop development and source boundaries

Version 1.0. Current implementation: P1 control bootstrap, still in progress.

## Product layout

All product source stays under the existing `synapse/` directory:

```text
synapse/
  machine/                 Canonical Python machine endpoint and envelope schema
  ui/
    contracts/             Native bootstrap schema
    src/
      components/          UI components
      connection/          Channel observation and connection state
      contracts/           Strict input decoder and generated validators/types
      styles/              Theme and layout
    src-tauri/
      src/
        desktop/           Tauri caller, command, and Channel bindings
        lifecycle/         Windows and Linux containment
      packaging/linux/     User-service and desktop launcher
    tooling/               Product contract generation
```

The native modules separate byte transport, negotiation, the session controller,
bundle verification, diagnostic redaction, caller checks, and renderer publication.
The controller's host interface joins platform launch/exit with protocol ownership;
the production Tauri host always verifies the bundled inventory and containment.
There is no product test mode, alternative Gold entry, or renderer-supplied host.

Renderer delivery has its own lifecycle, separate from the native process session.
Malformed snapshots or failed ACKs close that observer. **Reconnect display**
creates a fresh subscription and accepts its validated initial snapshot even when
the native sequence has not advanced. The native publication atomically replaces
its single subscriber/ACK slot; the renderer discards callbacks and late failures
from the previous observer. The subscribe response does not occupy the ACK slot;
only channel deliveries are acknowledged, including a duplicate initial delivery.

Reconnecting never sends `desktop_start`. Initial mounting may start only a shell
whose validated native state is `native`; remounting a running, failed, or stopped
session does not request startup. A prelaunch retry remains a separate explicit
action. While delivery is unavailable, core controls are hidden until a fresh
snapshot arrives. Stop follows native `ready`; Force close requires native `fail`
and the existing native caller/state checks remain authoritative.

External acceptance, peers, fixtures, mutation seeds, and evidence live exclusively
in `acceptance/ui_bridge`. Existing `tests/` is the legacy acceptance layer.
Build tooling lives in `scripts/ui`. No production UI source, navigation, resource,
or status feed references the acceptance tree.

## Local build

Observed tools: Python 3.12.14, Node 24.19.0, npm 11.9.0, Rust 1.99.1.
Use the committed npm and Cargo lockfiles. The native crate declares Rust 1.99;
an older minimum toolchain has not been accepted. Build on the target OS.

On Linux, install the Tauri 2 build prerequisites: a C/C++ toolchain, pkg-config,
GTK 3, WebKitGTK 4.1, librsvg, patchelf, and the distribution's required native
runtime libraries. A functioning graphical session, systemd user manager
(supporting `ExitType=main`, systemd 250+), and cgroup v2 are required for execution.
On Windows, use the Rust MSVC toolchain, Microsoft C++ build tools, and WebView2.

From the repository root:

```bash
python -m pip install build setuptools
python scripts/ui/build_core_bundle.py --output synapse/ui/src-tauri/resources/core
cd synapse/ui
npm ci
npm run build
npm run bundle -- --debug --no-bundle
```

The bundle builder must run with the release interpreter for that OS. It copies
CPython and production dependencies, excludes distribution test packages, checks
the staged canonical entry, and writes a content inventory. It refuses to overwrite
an existing output; move an old build aside explicitly before rebuilding.
Keep the OS temporary directory outside the source checkout. Raw pip output can
contain local installation provenance and interpreter-bound console launchers;
the builder removes these and their RECORD entries before publishing resources.
Only sanitized files enter the destination's atomic staging directory. Production
launch continues to use the bundled interpreter's canonical `-m synapse` entry.
The observed Linux interpreter uses a relative `$ORIGIN/../lib` runtime search path.
Do not assume an arbitrary Python installation is relocatable: another interpreter
distribution needs clean-machine dependency and canonical-entry acceptance.

The Rust build binds the inventory SHA-256 into the shell. Startup checks platform,
architecture, declared files, executable identity, containment, symlinks, extra
startup files, and bounded content before launch. The inventory is an integrity
binding, not installer signing or protection against an administrator replacing
the application. Distribution signing and clean-machine installation remain open.

`npm run generate` derives standalone Ajv validators and TypeScript types from
product schemas. Ajv compilation runs only during the build. The renderer does
not compile schemas or require `unsafe-eval` at runtime. Native builds default
to Tauri's packaged custom protocol; no native development-server URL is allowed.

For renderer layout work, `npm run dev` opens a browser build. It reports
`DESKTOP_REQUIRED` and cannot start a core. This is intentional and must not be
replaced by simulated readiness. The current [browser preview](assets/bootstrap-browser.png)
shows this actual state; the [original demo](prototype/README.md) remains the
reference for the fuller workspace design.

## Platform launch and close

The Linux package includes `synapse-desktop.service`; its desktop launcher starts
that user service. After installation, launch with:

```bash
systemctl --user daemon-reload
systemctl --user start synapse-desktop.service
```

The graphical login must publish its display/session environment to the user
manager. Direct execution outside the required service is refused before core
creation. The native shell must be the unit's actual MainPID, with cgroup v2,
`ExitType=main`, `KillMode=control-group`, `Restart=no`, `SendSIGKILL=yes`, and a
five-second stop deadline. There is no one-PID fallback for core cleanup.

Windows creation uses a serialized admission guard and a Job with
`KILL_ON_JOB_CLOSE`, without breakaway flags. The core is created suspended,
inherits only its three pipe ends, is assigned to the Job, and is then resumed.
Windows host acceptance now exercises nominal launch, overlapping-launch refusal,
ping, confirmed shutdown, owner death, synthetic descendant cleanup, an unrelated
inheritable-event sentinel and subsequent replacement. The debug NSIS artifact
has scoped installed-window/crash evidence. Assignment/resume/interrupted-startup
faults, exhaustive handle checks and actual adapter/MCP descendants remain pending;
see the
[Windows verification record](P1_VERIFICATION.md#windows-verification-follow-up).

Normal application close requests protocol shutdown and confirms process exit
within the deadline. A failed confirmation keeps the window open and exposes a
separate confirmed force-close action. Local cleanup does not establish remote
rollback or unknown operation outcomes. A launched session cannot silently restart;
pre-launch failures can be retried. A previously confirmed stop remains terminal
even if a subsequent restart request was refused.

## Renderer delivery

Rust owns a session identity, subscription identity, and increasing snapshot
sequence. Each subscription uses a Tauri Channel. At most one snapshot is awaiting
acknowledgment; further bootstrap updates coalesce to the latest complete snapshot.
Only the current subscription and its sent sequence can acknowledge delivery.
A blocked callback or missing acknowledgment cannot block the core controller.
This mechanism does not implement domain StateSync coverage, durable command
results, Live gaps, or evidential replay; those retain their separate requirements.

## External verification

The [P1 verification record](P1_VERIFICATION.md) contains the executed checks,
mutation evidence, and explicit unverified platform boundaries. Heavy handshake,
output-backpressure, and wheel-building acceptance each have a separate file.
They can run as independent processes with isolated temporary directories; avoid
CPU oversubscription when checking deadlines.

The [approved TR-2 amendment](SOURCE_MANIFEST.md#approved-semantic-amendments)
resolves F-06: retain one native byte-pipe path per supported platform. Do not add
a shell-plugin launcher, a mechanism switch, or a fallback after containment
failure. `lifecycle/` owns launch, handles, containment, and process exit;
`transport.rs` owns byte framing and bounded I/O; `protocol.rs` validates payloads
and reply bindings; `publication.rs` delivers authorized projections to the
renderer. Tauri `invoke`, Channels, caller checks, and capabilities remain in use.

The mechanism decision does not close TR-2. Execute the
[transport and launch obligations](ACCEPTANCE_MATRIX.md#native-transport-and-launch-obligations)
at the specified component, desktop, and installed levels. Source inspection and
cross-compilation do not establish startup-fault cleanup or descendant containment.
