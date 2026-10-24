# Workspace access ownership

Status: owner-approved contract, 2026-10-19; implementation in progress.
This adds the source of trusted context required by CMD-21. It does not replace
domain ownership, Gold admission, approval, or the renderer-compromise limits in
[the security design](UI_SECURITY_DESIGN.md#2-threat-model-and-trust-boundaries).

## One authority

The Python core's workspace access authority owns registration, grants, changes,
revocation and current authorization. Its authoritative registry resides outside
managed workspaces and agent working directories. Project files, imports and
plugins cannot register or expand rights. `CommandScope` is descriptive data.
Neither that value, the CLI executable, Rust, nor React is an authority issuer.

The local installation administrator deliberately enrolls before granting access
to external executors. Issuance and expansion require a separate protected
administrative context, unavailable to the renderer, agents, MCP servers and the
ordinary machine session. An operator-name argument, terminal, selected path or
OS account name is insufficient. OS identity may protect the registry; it never
implicitly grants application access to that account's files. Actual platform
protection must be verified, including refusal from an ordinary agent process.
Do not claim isolation if an untrusted process can read the administrative
credentials or modify the authoritative registry.

The canonical CLI is the initial administrative surface. The finished graphical
workflow uses trusted confirmation over the same owner and issuance contract.
It must not maintain a second Rust permissions database or invoke a CLI with an
automatically supplied affirmative answer. A folder picker only selects a
candidate resource; it grants no execution, tool or memory privileges.

## Exact, revocable delegation

Each durable grant records its identity/version, registered application instance,
workspace and resource identity, exact allowed methods and applicable target
constraints, object scope, explicit expiry or until-revoked lifetime,
administrative basis, and revocation state. A preset is expanded to a closed
method set when issued; future methods are not implicitly authorized. Initial
read access requires explicit confirmation. Task execution, file changes,
memory-policy changes, connection administration and approval responses are
separate rights.

The application proves its registered instance using protected client material
and the connection context. An asserted client ID is insufficient. There is no
secret shared by every installation. Renderer-visible handles describe permitted
objects; they do not expose administrative credentials or select the principal.
The authority builds the verified authorization context from the authenticated
session and current registry state.

Every access intersects authenticated session, active grant, workspace/object
membership, exact operation, domain prerequisites and any action-specific
consent. Invocation membership comes from its owner's retained records, not
matching two client-supplied IDs. Resource opening verifies the actual object,
including links/reparse points and replacement at a formerly approved path.
Access to `approval.respond` is not consent to an arbitrary proposed change.

## Revocation and recovery

Revocation takes effect without restarting the application. New requests are
denied, queued-but-unaccepted commands are rechecked before owner acceptance,
and further protected delivery stops. Checks also cover cached results, status,
snapshots, subscriptions and recovered intents. A prior idempotency receipt is
not permission to disclose it after revocation. The revocation/acceptance ordering
must have a defined serialization boundary; checking only on session creation
or queue insertion is insufficient.

Revoking application access does not cancel an already accepted task, undo an
effect, rewrite an execution grant, or prevent the domain owner from retaining
its outcome and performing necessary recovery. Those are separate owner actions.
Native recovery preserves the original intent binding while authorization is
unavailable; it neither silently retries nor creates a replacement command.

## Required evidence

Exercise ordinary-process administrative refusal, forged client/session fields,
cross-workspace and cross-object IDs, changed resource identity, unapproved
methods, expiry, concurrent revocation/acceptance, revocation during delivery,
saved-result/recovery access after revocation, and accepted work completing after
revocation. Prove absence of forbidden effects/disclosure, not only an error code.
Mutate the enforcing boundaries. No component case alone establishes installed
application conformance or protection from a compromised OS administrator.

## Public-source review

### Implementation boundaries under verification

`synapse/access/authority.py` owns registration, exact grants and revocation.
`records.py` owns their closed vocabulary; `authentication.py` owns a single-use
Ed25519 proof for one transport connection. `ports.py` defines protected registry
transactions and resource custody, not alternative permission issuers.
`windows_registry.py` stores bounded records under the machine ProgramData
location. `windows_location.py` reads the machine's 64-bit ProfileList setting
and expands only SystemDrive obtained from `GetSystemWindowsDirectoryW`.
Absolute configured local paths are preserved; unknown expansions, relative
paths, network paths and parent traversal fail closed. This deliberately avoids
Shell/userenv helpers whose expansion used the caller's environment in actual
Windows acceptance. It neither inherits a renderer/workspace override nor
mutates the process environment. `windows_objects.py` verifies object ownership/DACLs and holds resource
identities against replacement. `windows_invocations.py` pins the actual owner
database. The CLI parses administrative requests and delegates to this owner.

The Windows administrative context is an elevated process token, with the token
user SID retained as the issuance basis. The registry requires an Administrators
owner and a protected, exact DACL: SYSTEM/Administrators full access, Users read.
There is no administrative password shared with an ordinary application session.
Enrollment refuses an existing directory rather than adopting or repairing it.
A failed enrollment therefore remains unavailable until an administrator resolves
the incomplete installation; ordinary application startup never repairs it.

The initial resource profile registers an existing invocation evidence store as
a resource of an explicitly named logical workspace. It is outside the agent's
execution directory. It does not grant access to arbitrary project files or
make Gold worlds writable. The adapter resolves invocation membership from that
owner's retained records, not from a renderer-provided workspace/path pair.
Changing a workspace's resource identity requires explicit new registration;
an existing workspace identifier cannot silently acquire a replacement root.

Registry read locks cover current authorization, owner acceptance and one bounded
reply. Administrative writes take an exclusive lock. This serialization orders
acceptance against revocation; it is not a lock held for the life of an execution.
The registry is bounded by both record counts and a 64 KiB encoding limit; reaching
either bound refuses issuance without dropping historical revoked records.

The canonical machine entry now routes the explicitly opened application profile
through this authority. The native controller owns application identity and
protected intents; renderer fields cannot select the principal or owner path.
The access handshake signs installation, connection and nonce. The core resolves
the registered application from its unique public key; the native caller does
not submit an application ID as authority. Closing access discards that challenge.

Windows Credential Manager holds the native instance key with local-machine
persistence for the current user. Creation is serialized; corrupt or unavailable
storage does not cause a replacement identity. This is not an administrative
credential and gives no issuance rights. It protects against renderer access,
not arbitrary native code already able to use that user's credential store.
Administrative data remains separately protected by the elevated-token/DACL
boundary. Execution environments must enforce their own native isolation.

Native intents are partitioned by grant identity/version, with the authenticated
application and workspace bound inside each protected record. A new grant cannot
silently adopt old queued intents. Resubmitting a prepared intent first asks its
owner for status. Only scoped absence plus an explicit resend request can send
the original command; denial, missing delivery and malformed results cannot.

Actual UAC preparation has passed protected enrollment, registration, issuance,
reopening, signed authentication and durable revocation. An ordinary host process
passed reading and both administrative-operation and actual OS-write refusal.
The unique synthetic registry and resource were removed afterwards. Native
Credential Manager persistence/concurrency and Rust-to-Python lost-reply recovery
also pass. The latter uses acceptance-only synthetic registry composition and
the real invocation owner; it is not installed application evidence.
The later [installed application acceptance](P1_VERIFICATION.md#windows-application-and-delivery-integration)
also passes canonical administrative issuance, ordinary signed discovery, real
UI source observations, cold protected recovery and separate-process revocation
serialization. Queued evidence and saved receipts become inaccessible after
revocation. No component result closes P1's platform matrix or F-05.

Additional primary sources reviewed after completing the branch-document inventory:

- [Windows byte-range locking](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)
  defines shared/exclusive locks and their handle lifetime. The registry adapter
  uses one lock file, verifies its protection, and bounds acquisition time.
- [Windows security descriptors](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-descriptor-string-format)
  defines owner and protected-DACL representation. A path or elevation label
  without verification of the actual opened object's descriptor is insufficient.
- [Token information](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation)
  supplies the actual elevation/user context. CLI arguments cannot substitute it.
- [PyCA Ed25519](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)
  provides signature verification with explicit failure. The signed challenge
  binds installation, connection and a fresh nonce, rather than using a common
  installation secret or treating a client identifier as authentication.
- [Windows credential storage](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)
  defines user-bound generic credentials and local-machine persistence.
  [Credential reads](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw)
  distinguish missing entries from unavailable logon sessions;
  [writes](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)
  replace existing entries, so native creation must be serialized and existing
  malformed data must not be treated as absence.
- [Ed25519-dalek SigningKey](https://docs.rs/ed25519-dalek/latest/ed25519_dalek/struct.SigningKey.html)
  supplies the native proof implementation. Product code signs only a validated
  canonical-core challenge, never arbitrary renderer-provided bytes.

- [OWASP authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
  supports least privilege, default denial and checking every request. Here those
  checks include delivery and recovery, not just command submission.
- [RFC 8252 section 8.5](https://www.rfc-editor.org/rfc/rfc8252.html#section-8.5)
  rejects treating an application-wide distributed secret as confidential. This
  informs instance authentication; it does not mandate OAuth for the local pipe.
- [Tauri capabilities](https://v2.tauri.app/security/capabilities/)
  cannot repair incorrect scope checks inside native commands. Window access and
  domain authorization remain independent required boundaries.
- [VS Code Workspace Trust](https://code.visualstudio.com/docs/editing/workspaces/workspace-trust)
  distinguishes opening content from enabling active features, but does not
  contain a malicious extension. It is a usability reference, not our isolation.
- [Windows DPAPI example](https://learn.microsoft.com/en-us/windows/win32/seccrypto/example-c-program-using-cryptprotectdata)
  documents user/account protection, not isolation from every same-user process.
  DPAPI alone cannot establish the separate administrative context.
