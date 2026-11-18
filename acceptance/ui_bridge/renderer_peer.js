// External renderer peer, never imported or served by the product.
(() => {
  const commands = [], channels = [];
  let channel, nextStartFailure, nextSubscribeFailure, nextAckFailure, starts = 0;
  const snapshot = {
    schema_version: "1.0", session_id: "7be93210-4107-4444-9777-aabbaa998877",
    sequence: 0, state: "native", domain_ready: false, diagnostics: [],
    core_version: "acceptance-peer", profile: "synapse.control.v1",
  };
  const text = (entry, fields = {}) => JSON.stringify({...snapshot, subscription_id:entry.id,...fields});
  const deliver = (fields) => channel?.sink.onmessage(text(channel, fields));
  window.acceptancePeer = { commands, channels, deliver,
    set: (fields) => { Object.assign(snapshot, fields); deliver({}); },
    raw: (value) => channel.sink.onmessage(value),
    old: (index, fields) => channels[index].sink.onmessage(text(channels[index], fields)),
    spawnCount: () => starts,
    corruptSubscription: () => { nextSubscribeFailure = true; },
    rejectAck: () => { nextAckFailure = true; },
    rejectStart: (code) => { nextStartFailure = code; } };
  window.isTauri = true;
  window.__TAURI_INTERNALS__ = {
    transformCallback: () => 1,
    unregisterCallback: () => {},
    invoke: async (command, args) => {
      commands.push(command);
      if (command === "desktop_subscribe") {
        channel = {sink: args.channel, id: `7be93210-4107-4444-9777-${String(channels.length + 1).padStart(12, "0")}`};
        channels.push(channel);
        if (nextSubscribeFailure) { nextSubscribeFailure = false; return "{"; }
        return text(channel);
      }
      if (command === "desktop_ack" && nextAckFailure) { nextAckFailure = false; throw "CALLER_REFUSED"; }
      if (command === "desktop_start") {
        if (nextStartFailure) { const failure = nextStartFailure; nextStartFailure = undefined; throw failure; }
        if (snapshot.state === "ready") return;
        if (snapshot.process_id) throw "RESTART_REQUIRED";
        starts += 1; snapshot.sequence += 1; snapshot.state = "ready"; snapshot.process_id = 123; delete snapshot.code; deliver({});
      }
      if (command === "desktop_stop") { snapshot.sequence += 1; snapshot.state = "stopped"; delete snapshot.code; deliver({}); }
      if (command === "desktop_force_close" && snapshot.state !== "fail") throw "CALLER_REFUSED";
    },
  };
})();
