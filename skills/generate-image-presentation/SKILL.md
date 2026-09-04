---
name: generate-image-presentation
description: "Create a fixed Codex Native Image 3.0 presentation from pasted text by reviewing a faithful split, confirming it, and following one generated Run. Use when the user asks to generate an image presentation; do not use for HTML presentations."
---

# Generate Image Presentation

Use the `image-pptgen` CLI as the only interface to the Image PPTGen 3.0
surface. The complete command, payload, exit-code, and response contract is in
[the Image CLI contract](references/cli-contract.md). Do not import the
Platform backend, database, pipeline, or Python modules directly.

## Resolve the installed dispatcher first

Resolve `<skill_root>` as the absolute directory that contains this loaded
`SKILL.md`; never derive it from the current workspace or working directory.
Before the first CLI operation, verify that the matching dispatcher exists:

- macOS or Linux: [`<skill_root>/scripts/image-pptgen-dispatch`](scripts/image-pptgen-dispatch)

Set the matching absolute dispatcher path once for this task. Keep that exact
resolved command prefix for every later operation; copy it, do not rebuild it.
The POSIX script is always `<skill_root>/scripts/image-pptgen-dispatch`. Never
omit the `scripts` directory or concatenate the skill directory name with the
script basename. A missing dispatcher path is a stopped failure: do not invent
a sibling path and retry. In every command below, `<dispatcher>` is a required
command template:

- macOS/Linux: `/bin/sh "<absolute-skill-root>/scripts/image-pptgen-dispatch" <arguments>`

Replace only `<dispatcher>` with the template for the current platform. Supported
runtimes are macOS ARM64 and Linux x86_64; do not treat Windows as a supported
target. Every CLI command below must go through that dispatcher. Do not invoke bare `image-pptgen`, use
`command -v`/`where`, or modify `PATH`. The dispatcher
resolves the supported per-user install roots and accepts `IMAGE_PPTGEN_CLI`
only as an explicit absolute-path override for a custom install root.

On macOS Codex Desktop, the command-scoped runtime writes its lock, database,
and generated artifacts under the exact per-user install root
`$HOME/.codex/image-pptgen`. Before the first dispatcher command, when the
`request_permissions` tool is available, request file-system read/write access
to that exact directory once and wait for its result. Do not probe `doctor`
before that permission result. Do not request the whole home directory. If the
user declines or an actual dispatcher command reports a file-system permission
denial, stop and explain that the exact Image PPTGen runtime directory is not
writable; do not misreport it as a busy service and do not retry the dispatcher.
If `request_permissions` is unavailable, that absence is not itself a permission
denial: proceed to exactly one first dispatcher command and let the real
sandbox/OS result decide. Never ask the user to supply install roots, Python
paths, environment variables, or a replacement command as a workaround.

This skill is for an image presentation made with Image PPTGen 3.0 / Codex
Native Image 3.0. Historical Image 1.0, 3.2, and 5.0 routes are not supported
alternatives. It is not the HTML presentation workflow: if the user asks for
HTML, use `$generate-presentation` instead. Never create HTML output from this skill as a
presentation artifact. The standalone local Preview described below is only a
local viewer containing the completed PNG slides; it is not a generated HTML
presentation artifact.

## Fixed product boundary

- Accept text or Markdown material only. Reject PNG, JPEG, SVG, PDF, and every
  other image/OCR input before running `doctor` or making an HTTP/model call.
- The server owns faithful splitting and the exact `Codex Native Image 3.0 Luna
  Low Director` configuration. Do not ask about or pass intent, preferences,
  model, provider, renderer, config, Requirement, Color, prompt, mode, or
  retry arguments.
- The user reviews content pages only. Keep source wording, order, facts,
  numbers, and evidence; a revision may change titles or page boundaries but
  must not summarize or paraphrase the source.
- Confirmation is explicit. A vague acceptance, silence, or a request for
  another change is not confirmation.

## Complete every image-pptgen command

For every dispatcher-mediated `image-pptgen` command, a shell-tool yield or
`running` response means the same command is still in progress. It is not a
completed result and is not an unknown outcome.

- An `item.completed` or equivalent tool result with process exit 0 and
  parseable JSON or JSONL is terminal: consume it immediately. Do not wait,
  re-probe, or treat it as still running.
- Preserve the complete tool result and its typed continuation identifier.
- Continuation applies only while the execution tool itself is still running
  and has not returned process exit. If the command tool returns a
  `session_id`, resume that same command with `write_stdin` using the same
  `session_id`. If the outer execution tool returns a `cell_id`, resume that
  outer call with `wait` using the same `cell_id`.
- Never treat a `cell_id` as a command `session_id`, or an outer wrapper exit as
  proof that a nested command has exited.
- Never use `ps`, `/proc`, port probes, or a second command to decide whether a
  completed dispatcher command is done.
- Continue the same invocation until process exit before interpreting its
  business result. Only after process exit without a parseable JSON/JSONL
  response is the command an unknown outcome; an unknown mutation outcome must
  not be retried.

## Submit material

1. Resolve the material without making the user perform file work.
   - Use an existing readable UTF-8 text or Markdown file unchanged.
   - For pasted text, create a new collision-safe `.md` file under the current
     writable workspace. Never overwrite an existing path. Write the pasted
     body byte-for-byte as UTF-8, read it back, and verify identical bytes.
     Do not summarize, normalize, or reformat it.
   - If no material is available, ask for the text and stop.
2. Reject image input immediately. Do not inspect it with OCR, run `doctor`, or
   call the platform. Explain that this workflow accepts text/Markdown only.
3. Infer a short title from the user's request or the file name. Run:

   `<dispatcher> doctor --json`

   If it fails, report the returned error and stop.
4. Submit the saved file exactly once:

   `<dispatcher> material submit --title "<title>" --text-file "<path>" --json`

   Retain the returned `deck_id`. Do not submit the same material again for a
   split revision.

## Propose and review every page

From the first user request only, retain one unique explicit positive
content-page target `N` only when that request clearly names a page target.
Do not infer `N` from material numbers, headings, ranges, approximations,
minima, zero, negative values, or competing counts. If there is no unique
explicit positive page target, retain none.

Run:

`<dispatcher> split propose --deck-id <deck_id> --json`

The command has no mode selector: the public server fixes Luna Low faithful
splitting. This command is expected to outlive the shell tool's first yield. If
the tool reports `running`, `in_progress`, a `session_id`, or a `cell_id`, resume
that exact command continuation as defined above and keep waiting for its
process exit. Do not call the response missing, empty, malformed, or
unparseable until that original command has completed with a process exit.
Never launch a second proposal command as a substitute for waiting. Once the
original command has process exit 0 and parseable JSON, consume that JSON
immediately.

Compare the proposal JSON `page_count` to retained `N`. Never compare Markdown headings
or generated slide/PNG counts.

- If there is no unique retained target, or `page_count` already equals `N`,
  do not run `split revise`. The original proposal is the matching proposal.
- If `page_count` differs from retained `N`, before displaying any Markdown
  run exactly one same-draft structured revision:

  `<dispatcher> split revise --draft-id <draft_id> --target-page-count <N> --json`

  Use the same pending `draft_id` from the proposal JSON. Never use `--instruction`,
  resubmit, repropose, retry, or loop. Wait for that one command's terminal JSON
  as defined above. If the revision succeeds and its JSON `page_count` equals `N`,
  that complete returned `markdown` is the matching proposal. If the revision
  returns a typed inability, keep the pending draft unchanged, report the typed
  error, display no unmatched proposal as acceptable, and do not confirm,
  generate, retry, resubmit, or repropose. Stop.

Display exactly one complete matching proposal: the original proposal if it
matches or there is no unique target; otherwise the successful revised
Markdown. Display the complete returned `markdown`, including every proposed
content page, in order. Do not show only a summary or selected pages. Keep
the pending `draft_id` and ask whether the user wants a change. Stop after
showing the proposal.

## Revise the same pending draft

When the user requests a change while that draft is pending, choose exactly one
revision form:

- If the request changes only the number of pages (for example,
  “make it exactly 3 pages”) and contains no other edit, use the structured
  target-page fast path:

  `<dispatcher> split revise --draft-id <draft_id> --target-page-count <N> --json`

  Map only the requested positive integer `N`; do not turn this into a natural-
  language instruction. The server reuses the same pending draft and returns
  the complete revised Markdown.
- If the request includes any other edit—content, title, order, wording,
  boundaries, or a qualification—pass the complete natural-language
  instruction unchanged, without reducing it to keywords:

  `<dispatcher> split revise --draft-id <draft_id> --instruction "<feedback>" --json`

  Never send `--instruction` and `--target-page-count` together. If the target
  page count cannot be reached safely, report the typed error and keep the
  pending draft unchanged.

Do not submit material or propose a new draft. Display the complete revised
markdown, including every page, and ask whether the user wants another change
or explicit confirmation. A revision that drops, reorders, paraphrases, or
changes a source fact is a failed result; report the error rather than silently
accepting it.

## Decide confirmation by meaning

Treat the user's reply semantically; do not require a fixed phrase or a literal
string match. This is not a fixed phrase gate. Decide in this order, while the
same draft is still pending:

1. A requested change wins. If the reply contains a concrete edit, question, or
   qualification, keep it a revision even when it starts with an acceptance.
   Revision takes precedence over confirmation.
   For example, `OK, but make the second page shorter` is a mixed revision: run
   `split revise` on the same draft; you must revise and do not confirm or
   generate.
2. An explicit negative, uncertainty, or discussion state is not confirmation.
   Replies such as `no`, `not yet`, `maybe`, `let's discuss`, or a question
   about whether to proceed require a concise follow-up question; run neither
   `split confirm` nor `generate`.
3. An independent, unqualified affirmative is an explicit confirmation. Accept
   the user's meaning in Chinese or English, including `\u786e\u8ba4`,
   `\u53ef\u4ee5`, `\u7ee7\u7eed`, `\u597d\u7684`, `OK`, `OK, continue`,
   `yes`, and `go ahead`, along with ordinary punctuation, case, or whitespace
   variations. Do not make the user repeat one exact token.

If none of these cases is clear, ask whether the displayed split is ready and
wait. An ambiguous answer is not confirmation and must not trigger either
mutation.

## Confirm once, then either stop or generate

Only after the user explicitly confirms the displayed split, confirm exactly
once:

`<dispatcher> split confirm --draft-id <draft_id> --json`

Do not confirm a second time or retry an unknown confirmation outcome. Retain
the confirmed `deck_id`, final slide IDs, and confirmation receipt in this
task; do not ask the user to repeat or supply those identities.

An explicit affirmative that also clearly says to defer, hold, pause, or not
start generation is a **confirm-only** handoff. This includes requests such as
`confirm this plan, but do not generate yet`, `please confirm first and do not
start generation`, and `confirm it now; do not generate yet`. For a
confirm-only handoff, execute the command above exactly once, report the
retained confirmed deck and its final slide identity, then stop. In other
words, confirm exactly once and stop. Do not generate, follow, or return a
result in that turn: the Skill must not generate, follow, or return a result
before the later generate-only decision.

A later generate-only user turn may resume only that retained confirmed deck.
It must use the retained `deck_id` without re-submit, propose, revise, or
confirm. Do not infer a retained deck across tasks, and do not accept a
different deck identity from the user as a substitute. If no retained confirmed
deck exists in this task, explain that generation cannot safely resume and make
no mutation.

An ordinary unqualified affirmative remains backward-compatible: after its one
confirmation, and in that same user turn, immediately start exactly one Image
3.0 generation. The command has no intent, preference, model, config,
Requirement, Color, provider, or renderer flags. It sends the fixed six-field
public request and returns one `batch_id` and one `run_ids` array. The same
immediate path applies to a later generate-only turn after a retained
confirm-only handoff.

- On macOS, run exactly this one held command. On Linux, run exactly this one
  dispatcher composition. Both use:

  `<dispatcher> generate-and-follow --deck-id <deck_id> --jsonl`

  Its first stdout line is the exact JSON response to one `generate` request.
  Its remaining stdout is the one `status --run-id <run_id> --follow --jsonl`
  continuation, owned by that same command. Do not issue a separate `generate`
  or `status` command on macOS or Linux. That means do not run
  `<dispatcher> generate --deck-id <deck_id> --json` or
  `<dispatcher> status --run-id <run_id> --follow --jsonl` as a substitute.

Parse the generation response explicitly: verify that
`run_ids` is an array of exactly one item, verify that its first item is a
positive integer, then bind `run_id = run_ids[0]`. For example, when the
response contains `{"batch_id": 1, "run_ids": [1]}`, the only valid bound
value is `run_id = run_ids[0]` (that is, `1`). Never read a nonexistent
top-level `run_id` from the generation response. If parsing fails, `run_ids` is
missing, or its item is `undefined`, `null`, empty, non-integer, or
non-positive, stop immediately and report the failure; you must not retry,
reconfirm, or generate again. Do not ask a design question between confirmation
and generation, and do not create a second run.

## Follow one Run continuously

Immediately run exactly one follow process for the retained, bound Run. Use
only the value assigned by `run_id = run_ids[0]`; never reconstruct it from
another response field or substitute `undefined`, `null`, or an empty value.

- On macOS and Linux, the command already running from the prior step is the
  one follow process. After its first JSON line, keep the same
  `generate-and-follow` continuation alive; it is executing exactly one
  `status --run-id <run_id> --follow --jsonl` internally. Do not start a
  separate `<dispatcher> status --run-id <run_id> --follow --jsonl` command.

This is one long-running command, not a one-shot status check. If its tool
response reports `running`, a `session_id`, or a `cell_id`, keep the same command continuation alive. Use the same `session_id` or `cell_id` associated
with that continuation: resume only that same `session_id` with `write_stdin`,
or only that same `cell_id` with `wait`, until the status process exits. Do not
start a second status command. Do not run `result` while that continuation is
outstanding. A follow failure, missing continuation, non-zero exit,
malformed JSONL, missing terminal event, or mismatched Run is a stopped failure:
do not retry confirmation or generation.

After that one process exits, read its final grounded status event. The terminal
event's `run_id` must equal the bound `run_id`, and `source_facts.run_status`
must be one of the documented terminal states: `completed`,
`completed_with_failures`, `failed`, `interrupted`, or `timed_out`. `queued`,
`pending`, `running`, `generation_started`, and `in_progress` are not terminal
and cannot justify a result or completion. Do not start a second follow
process, poll with unrelated commands, or infer completion from elapsed time.
Surface each grounded update in business language. Preserve the exact
`task_progress` items and `current_activity`; for heartbeats, keep the message
brief and include the elapsed follow time. Never invent hidden design
reasoning, page content, provider facts, or success.

## Return the same-Run result

Only after the follow process exits with that verified terminal event, run
exactly once, using that same bound `run_id`:

`<dispatcher> result --run-id <run_id> --json`

For a completed Run, this writes a Run-scoped offline Preview bundle while
the managed runtime is still available. The returned `preview_url` is
a `file:` URL for its `index.html`, and `download_url` is the matching
prebuilt ZIP. Open the returned Preview file after the command exits and
provide both the Preview and ZIP links. The Preview supports page navigation,
direct page selection, zoom, fit, fullscreen, and ZIP download without the
runtime, network, backend, or port 3130. Do not use the legacy
`--static-preview-file` option for the normal completed-Run handoff: it remains
only for compatibility with an older embedded-image page and lacks the
interactive Run bundle controls. The returned Preview does not depend on the command-scoped 3130 service.

Use the returned status literally. `partially_completed`, `in_progress`, and
`failed` are not complete, even when some PNGs exist. A nonterminal result
cannot close the task. Verify that the returned result's `run_id` equals the
bound `run_id`. For an in-progress Run, keep that same `run_id` when presenting
the existing loopback Preview and download links:

`<doctor.base_url>/history/run/<run_id>/preview`

and the returned Run download URL. Open the exact Preview when a real browser
opener is available; otherwise provide the exact clickable URL and say it was
not opened. For a completed Run, do not present a loopback Preview or download URL as
usable after the command exits. Use only the returned `file:` Preview and
matching `file:` ZIP from the completed Run.
Never substitute a different Run, Run Detail, screenshot, or API response for
Preview. Preserve PNG ordering and make download availability truthful. Do not
expose raw prompts, internal paths, credentials, provider metadata, or command
transcripts in the default user-facing summary.

If the user requests a change after generation, start a new task and Run; never
rewrite or present the completed Run as changed in place.
