import { decodeCommandValue, InvalidCommandValue } from "./jcs.ts";
import { validateCommand, validateReconciliation } from "./generated/validators.js";
import type { CommandMutation } from "./generated/CommandMutation";
import type { CommandReconciliation } from "./generated/CommandReconciliation";

// These decoders validate data only. Native intent issuance and owner scope
// checks remain necessary; a valid JSON command grants no authority.
export function decodeCommandMutation(text: string): CommandMutation {
  const value = decodeCommandValue(text);
  if (!validateCommand(value)) throw new InvalidCommandValue();
  const command = value as CommandMutation;
  if (command.recipient !== command.params.invocation_id) throw new InvalidCommandValue();
  return command;
}

export function decodeCommandReconciliation(text: string): CommandReconciliation {
  const value = decodeCommandValue(text);
  if (!validateReconciliation(value)) throw new InvalidCommandValue();
  return value as CommandReconciliation;
}
