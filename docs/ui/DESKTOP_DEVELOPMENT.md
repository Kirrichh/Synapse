# Desktop development and source boundaries

Version 1.0. Current implementation: control bootstrap and scoped Windows invocation application; full P1 remains in progress.

## Product layout

All product source stays under the existing `synapse/` directory:

```text
synapse/
  access/                  Core delegation authority and platform registry/resource custody
  commands/                Command bindings, JCS and owner-facing contracts
  machine/                 Canonical control/application endpoint and delivery routing
  ui/
    contracts/             Native bootstrap schema
    src/
      components/          UI components
      connection/          Control Channel observation and connection state
      application/         Authorized requests, intent recovery and scoped observations
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
`KILL_ON_JOB_CLOSE`, without breakaway flags. The core is created suspended with
`PROC_THREAD_ATTRIBUTE_JOB_LIST`, so its lifetime belongs to the Job when creation
returns. It inherits only its three pipe ends and is resumed after successful
preparation. A separate post-creation assignment would leave an orphan if the
owner died between creation and assignment (F-10).
Windows host acceptance now exercises nominal launch, overlapping-launch refusal,
ping, confirmed shutdown, owner death, synthetic descendant cleanup, an unrelated
inheritable-event sentinel and subsequent replacement. The debug NSIS artifact
has scoped installed-window/crash evidence. External debugger acceptance now
checks Job-list preparation failure, invalid Job rejection, resume failure and
owner death at the returned creation and pre-resume boundaries. Actual agent/MCP
launch owners and nested children pass owner-loss cleanup; the follow-up mutation
campaign detects removed controls. Linux execution is explicitly deferred debt;
the broader environment/release matrix remains open. See the
[Windows verification record](P1_VERIFICATION.md#windows-verification-follow-up).

Normal application close requests protocol shutdown and confirms process exit
within the deadline. A failed confirmation keeps the window open and exposes a
separate confirmed force-close action. Local cleanup does not establish remote
rollback or unknown operation outcomes. A launched session cannot silently restart;
pre-launch failures can be retried. A previously confirmed stop remains terminal
even if a subsequent restart request was refused.

Windows navigation cancellation is not a network request barrier. The native
WebView2 request guard is installed on an empty hidden WebView before packaged
content loads, and returns a local 403 for requests outside the configured asset
and IPC origins. Request-source-kind filtering is mandatory; setup failure refuses
startup. Keep Tauri dispatcher calls outside its native WebView callback to avoid
re-entering the held lock. See [F-11 acceptance](P1_VERIFICATION.md#windows-navigation-request-boundary-f-11).

## Renderer delivery

Rust owns a session identity, subscription identity, and increasing snapshot
sequence. Each subscription uses a Tauri Channel. At most one snapshot is awaiting
acknowledgment; further bootstrap updates coalesce to the latest complete snapshot.
Only the current subscription and its sent sequence can acknowledge delivery.
A blocked callback or missing acknowledgment cannot block the core controller.
This control mechanism remains separate from application delivery. The latter
uses an independent bounded Channel with exact acknowledgments, core-owned
subscriptions and source-class semantics described below.

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


### Windows release and isolated boundary probes

The [additional release evidence](P1_VERIFICATION.md#additional-windows-release-and-boundary-acceptance)
distinguishes the actual installed release from external native witnesses and
private foreign-context variants. Run all Windows lifecycle probes serially with
no other Synapse window: the production admission mutex and Job are session-wide.
Keep temporary paths and reports outside the repository.

```powershell
cargo build --release --locked --manifest-path synapse/ui/src-tauri/Cargo.toml
# From synapse/ui, with matching CARGO_TARGET_DIR and verified production assets:
npx tauri bundle --bundles nsis --ci
# Build external acceptance hosts separately:
cargo build --locked --manifest-path acceptance/ui_bridge/native/Cargo.toml --bins
windows_pipe_bytes.exe
```

For the installed control corpus, set the acceptance-only environment variable
`SYNAPSE_ACCEPTANCE_MACHINE_COMMAND` to a JSON argv array containing
`windows_installed_pipe.exe`, the verified installed core directory, and its
SHA-256 manifest digest. Run the machine CLI, handshake deadline, output
backpressure and pipe corpus pytest files. The relay uses the product launcher;
it does not replace the canonical Python entry or ship in the application.

`windows_installed_corpus_mutations.py` takes `--core`, a fresh external
`--workspace`, and `--output`. `windows_pipe_mutations.py` takes a fresh external
`--workspace`, `--cargo` and `--output`, with an external `CARGO_TARGET_DIR`.

Prepare foreign-context copies with `windows_caller_contexts.py --prepare`,
`--workspace`, `--cargo`, `--output` and an external `CARGO_TARGET_DIR`. Then run
the same driver with `--workspace`, `--output` and `--node` from an ordinary
interactive desktop. Its origin observer requires free loopback port 80 so the
independent non-default-port rule cannot mask the origin predicate. An occupied
port is an infrastructure failure; never stop another service to obtain it.
These copies deliberately grant capabilities and relax earlier navigation/request
admission solely to reach the actual native caller filter. Never install or ship
the private copies, their resources, or their acceptance instrumentation.


For installed GUI corruption, run `windows_installed_security.mjs` with
`--scenario transport`, the installed `--executable`, an external `--workspace`
and `--output`, and `--python` pointing to an acceptance interpreter with psutil.
Run `--fault zero`, `oversize`, `maximum`, `utf8`, `json`, `header_eof`, and
`body_eof` serially. `windows_core_fault.py` verifies the owned canonical child's
parent, executable and argv before duplicating its stdout pipe. This observer
uses Windows x64 process-parameter offsets and is not a portable product API.
EOF cases terminate only that owned child; the GUI may observe CORE_EXITED before
the framing reader sees partial EOF. Native byte witnesses establish the specific
partial-frame/no-resynchronization contract separately.

## Authorized invocation application

The [access contract](WORKSPACE_ACCESS_CONTRACT.md) defines the sole Python access
owner. The canonical `python -m synapse access --help` surface exposes enrollment,
application registration, exact grant issuance and revocation. Enrollment and
administrative changes require a real elevated administrative context; merely
launching the CLI grants nothing. The registry is machine-owned outside projects.
The ordinary application displays its registration public key and only grants
returned by its authenticated session. Folder paths and renderer IDs grant no
rights. Do not run the application elevated as a substitute for registration.

`access/` separates records/authentication/authority from Windows location,
registry and resource custody. The invocation owner retains command receipts and
observation truth. `machine/domain_session.py` routes the negotiated application
contract; `subscriptions.py` owns bounded source delivery and current authority.
Rust separates client identity, protected intent storage, command recovery,
application request handling and acknowledged publication. React separates the
session, fresh observations, recovery interaction and presentation. None is a
second execution owner or permission registry.

The UI supports explicit fresh invocation snapshots, retained facts, Live
connection observations, invocation cancellation and protected cold recovery.
It does not implement the complete P2 continuous multi-domain reducer. Linux
protected application identity/storage/access is unavailable; no permissive
substitute is selected. Control readiness alone never grants application access.

For installed application acceptance, run `windows_application_host.mjs` with
`--executable`, `--python`, external `--workspace` and `--run` directories, and
`--output`. Pair it with `windows_application_registry.py administer --run
<same-directory> --installed <installed-application-directory>` in a separately elevated
acceptance interpreter. The guardian invokes the installed canonical CLI.
The ordinary driver prepares the
synthetic owner; the elevated guardian registers/grants/revokes and cleans only
the temporary registry it created. It refuses an existing registry. Do not use
these checks against a configured personal installation or put runtime files
inside the checkout. Keep all application/Job checks serial on the same desktop.

The source-level subscription, domain-integration, application-channels and
windows-location mutation catalogues run through the external mutation driver.
Use a fresh workspace and external Cargo target for each campaign. The installed
sequence, foreign caller probes and transport corruption are separate host jobs;
successful component tests are not substitutes for them. See the
[final Windows record](P1_VERIFICATION.md#windows-application-and-delivery-integration).
