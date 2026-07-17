# Live Gemini Team Trial

This trial runs a four-role Synapse team against the product Gemini gateway.
The project brief in `examples/live_gemini_team.syn` is synthetic and contains
no repository code, personal data, or secrets.

## Run from a phone with GitHub Actions

The workflow must be present on the repository's default branch before GitHub
shows its manual Run workflow control.

1. Open the repository settings in GitHub. On a phone, enable the browser's
   desktop-site mode if the Settings tab is hidden.
2. Open **Secrets and variables -> Actions** and create a repository secret
   named `GEMINI_API_KEY`.
3. Open **Actions -> Live Gemini Team Trial -> Run workflow**.
4. Keep the default model or enter another Gemini model supported by the key's
   Google project, then start the run.
5. Read the four role outputs in the job log or download the
   `live-gemini-team-output-*` artifact. The artifact is retained for seven
   days.

The key is not a workflow input. It is exposed only to the two
repository-owned steps that validate the Synapse gateway and execute the
program. Checkout, Python setup, and artifact upload do not receive the secret
in their step environment. The workflow never prints the key.

Delete or rotate a temporary key after the trial. Configure quota and billing
limits in the Google project before running it. `SYNAPSE_LLM_TIER=paid` selects
Synapse's internal privacy-policy route; it does not itself change the Google
project's billing plan.

## Run locally

Set the environment without writing the key into source, command history, or a
tracked `.env` file:

```bash
export SYNAPSE_LLM_PROVIDER=gemini
export SYNAPSE_LLM_MODEL=gemini-3.1-flash-lite
export SYNAPSE_LLM_TIER=paid
read -rsp "Gemini API key: " GEMINI_API_KEY && echo
export GEMINI_API_KEY

python -m synapse run examples/live_gemini_team.syn

unset GEMINI_API_KEY
```

The runtime makes seven sequential provider calls: one each for product,
architecture, and risk review, followed by four delivery-lead synthesis steps.
Provider output is nondeterministic and consumes the quota associated with the
configured key.

## Interpretation boundary

A successful run demonstrates that the public CLI can route a `.syn` team
through the live product gateway and pass earlier role results into later role
prompts. It does not prove that the generated plan is correct, that the roles
executed concurrently, or that an exit code alone is sufficient verification.
