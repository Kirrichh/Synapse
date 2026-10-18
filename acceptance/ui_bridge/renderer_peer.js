// External renderer-only peer. It is never imported, bundled or served by product code.
(() => {
  const commands = [];
  let channel;
  let nextStartFailure;
  const snapshot = {
    schema_version: "1.0", session_id: "7be93210-4107-4444-9777-aabbaa998877",
    subscription_id: "7be93210-4107-4444-9777-aabbaa998877", sequence: 0,
    state: "native", domain_ready: false, diagnostics: [],
    process_id: 123, core_version: "acceptance-peer", profile: "synapse.control.v1",
  };
  const deliver = (fields) => channel?.onmessage(JSON.stringify({...snapshot,...fields}));
  window.acceptancePeer = { commands, deliver,
    set: (fields) => { Object.assign(snapshot, fields); deliver({}); },
    rejectStart: (code) => { nextStartFailure = code; } };
  window.isTauri = true;
  window.__TAURI_INTERNALS__ = {
    transformCallback: () => 1,
    unregisterCallback: () => {},
    invoke: async (command, args) => {
      commands.push(command);
      if (command === "desktop_subscribe") { channel = args.channel; return JSON.stringify(snapshot); }
      if (command === "desktop_start") {
        if (nextStartFailure) { const failure = nextStartFailure; nextStartFailure = undefined; throw failure; }
        snapshot.sequence += 1; snapshot.state = "ready"; snapshot.process_id = 123; delete snapshot.code; deliver({});
      }
      if (command === "desktop_stop") { snapshot.sequence = 3; snapshot.state = "stopped"; deliver({}); }
    },
  };
})();
