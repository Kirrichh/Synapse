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
Runtime acceptance on Windows and actual adapter descendant paths is still pending.

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

The literal plugin-shell instruction in TR-2 remains a recorded implementation
discrepancy: see F-06 in [open conditions](OPEN_CONDITIONS.md). Native raw pipes
preserve bytes and allow suspended Windows creation, but this does not silently
amend the specification or close TR-2.
