"""Host ABI version authority for CVM host-call contracts.

HOST_ABI_VERSION tracks the VM-visible host-call surface: the set of
symbols dispatched through VMBridge, the PromiseRecord contract, and
the snapshot fields that a host must honour on resume.

History:
  2.2.0-alpha3b2          Original capability enforcement surface.
  2.2.0-alpha3e-p0        Corrective alignment for alpha3d messaging opcodes
                          (MSG_SEND / MSG_RECEIVE and messaging pause state).
  2.2.0-alpha3e-track-a   LLM/Prompt bridge: llm.request became a full
                          deterministic dispatch contract.
  2.2.0-alpha3e   Guard Blocks: guard bytecode, cleanup ranges,
                          guard violation enforcement state, and bridge-side
                          side-effect blocking became VM-visible contracts.

Next expected bump:
  alpha3f Track C may add debugger/replay CLI surface after RFC approval.
"""

HOST_ABI_VERSION = "2.2.0-alpha3e"
