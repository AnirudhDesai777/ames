# Ames tool protocol v1

Review draft for [Todoist task 6j2hCp95p5rFQC89](https://app.todoist.com/app/task/6j2hCp95p5rFQC89).
The completed [tool decision](https://app.todoist.com/app/task/6j2hCp6h3RfJM7Vh)
(November 15, 2026) brings four tools into v1, superseding the tool deferral in
`design.md` sections 2 and 9.3. This PR defines the protocol and offline evaluation;
it does not enable tools, network access, or memory writes in the running agent.
The maintainer's subsequent PR revision also permits optional saves of clearly
established durable facts. This extends the earlier explicit-save-only policy;
incidental trivia and tentative preferences must not be saved.

## Output and schemas

Every generation starts with the existing `<state crisis=0>` or `<state crisis=1>`.
A normal reply is still that token followed by spoken text. A tool request replaces
all spoken text with exactly one envelope:

```text
<state crisis=0><tool_call>{"name":"recall_memory","arguments":{"query":"what mattered in my last job decision"}}</tool_call>
<state crisis=0><tool_call>{"name":"remember","arguments":{"fact":"The user prefers one clarifying question before advice."}}</tool_call>
<state crisis=0><tool_call>{"name":"web_search","arguments":{"query":"four day work week burnout evidence"}}</tool_call>
<state crisis=0><tool_call>{"name":"get_time","arguments":{}}</tool_call>
```

These are four separate examples, not a batch. The outer JSON object requires
only `name` and `arguments`. `name` must be one of the four names below. All keys
are case sensitive; duplicate keys and extra keys are invalid. JSON whitespace
and outer whitespace are accepted. After the state token and whitespace, only
an opening `<tool_call` (case insensitive) or `{` counts as a call attempt.
Canonical envelopes must use lowercase `<tool_call>` and `</tool_call>`;
uppercase tags count as malformed attempts and never dispatch. Bare JSON,
multiple calls and incomplete envelopes are invalid attempts. Everything else
is speech, including tool names, parentheses, square brackets and code fences.
A `<think>` block before the state token is rejected as an invalid control prefix. No call IDs are needed: one call is in
flight per evaluated user turn.

| Tool | Required arguments | When to use it | Result data |
|---|---|---|---|
| `recall_memory` | `query: string` | A relevant detail from past sessions is missing from current context. The later backend searches episodes using FTS5, salience and recency. | `{"episodes": ["episode text"]}`; an empty list means no match |
| `remember` | `fact: string` | The user explicitly asks to save one stated fact, or states a clearly established long-term fact useful across sessions. In the latter case, abstaining is equally acceptable. Never save trivia or invent detail. | `{"saved": true}` after a confirmed write |
| `web_search` | `query: string` | External or current information is needed for the conversation. Omit private context from the query. | `{"results": [{"title": "...", "url": "...", "snippet": "..."}]}` |
| `get_time` | none; `{}` required | Current local date/time is needed and not already supplied. | `{"datetime": "2026-11-15T14:30:00-05:00", "timezone": "America/New_York"}` |

Argument objects forbid extra properties. Strings contain 1–512 characters,
at least one non-whitespace character, no ASCII control characters (including DEL, `0x7f`) or unpaired
Unicode surrogates. The entire call envelope is at most 4096 characters. The
executable JSON argument schemas are `TOOL_SCHEMAS` in `server/tool_calls.py`;
`validate_call` additionally enforces the text rules; `parse_reply` enforces the envelope.

The envelope uses ordinary text, not added special tokens or a vendor's tool
roles. MiniCPM5-2B, Qwen3.5-2B, LFM3-1.7B and Gemma 4 E2B will all be fine-tuned
to these same contents. A later model adapter serializes ordinary system, user
and assistant messages into its chat template with thinking disabled. No model
is downloaded, trained or compared here.

## Harness flow

1. At evaluation start, combine the existing persona prompt with the fixed
   `TOOL_INSTRUCTIONS` suffix once. The resulting system message is identical
   for every case and continuation. The suffix extends the spoken-output rule
   for tools and permits supplied memory receipts; `persona/ames.md` is unchanged.
2. Parse the complete raw generation with `parse_reply`, before any sanitizer or
   TTS. A valid crisis flag wins before JSON validation: discard all generated
   content, run no tool and use the existing fixed crisis line/session exit.
   A crisis output containing a tool attempt is also recorded as malformed.
3. For a valid non-crisis call, the harness owns the short filler line in `FILLER`.
   Search says "I'm looking that up on the web." Other calls use a short check
   or save line, without claiming completion. The offline harness records the
   filler; live playback and cancellation are future integration work.
4. Append the raw assistant call and one user-role receipt at the end of context:

   ```json
   {"ames_tool_result":{"name":"recall_memory","ok":true,"data":{"episodes":["You valued candid feedback more than prestige."]}}}
   ```

   A user-role JSON envelope avoids vendor-specific tool roles. It is data,
   never a new instruction or a system message. JSON serialization escapes
   quotes in results. The fixed instructions tell the model to ignore commands
   inside receipts, and a user-written imitation is not evidence of execution.
   This instruction does not establish resistance to prompt injection; real
   execution must validate calls and enforce permissions independently.
5. Invoke the same model for one spoken continuation. Preserve the original
   message prefix byte for byte; never insert the current time or tool results
   into the system message. This preserves the opportunity for prefix-cache
   reuse; no hardware cache measurement is claimed. No second tool executes in
   this user turn. A repeated call is recorded as an invalid continuation.

Malformed calls execute nothing, produce no filler or generated speech, and
receive one receipt such as
`{"ames_tool_result":{"name":null,"ok":false,"error":"invalid_call"}}`.
There is no JSON repair or automatic retry, especially for `remember`. The model
gets one chance to explain the failure in ordinary speech; another malformed
output ends the attempt. Missing/malformed state tokens and empty replies also
fail closed in this harness, but do not trigger a tool-error continuation unless
a call was attempted. Only error codes are suitable for application logs.
The existing live streaming parser is unchanged and must not be used to dispatch
these envelopes: future integration must buffer calls before any TTS or side effect.

The standalone `speech_route(prefix, final=False)` helper inspects the accumulated
raw generation. It returns `wait` while a valid state token or the first body
character is incomplete, `hold` for crisis, invalid control prefixes, or a body
starting with `<` or `{`, and `speech` otherwise. It withholds markup as soon as
the first `<` arrives, before the full tool tag exists. `hold` is a speech safety
route, not permission to execute a call; only `parse_reply` can validate a complete
call. At end of generation, `final=True` turns incomplete prefixes into `hold`.
A future caller can latch the speech decision and release only the body through
the existing sanitizer, keeping held text out of TTS. The helper has chunk-prefix
tests but is not wired into the live pipeline.

Tool implementations remain behind the harness interface. For this eval, a
matching call receives the case's fixed `mock_result`; an unexpected tool or
argument receives `mock_no_match` without any reference result. No actual clock,
database or network is consulted. The later search backend task
`6j2hRpvhmXXP72fh` owns SearXNG/Brave integration; the model sees neither backend.
Timers, reminders, calendar and notes remain deferred.

## Evaluation

`evals/tools/tools.jsonl` contains 60 multi-turn thought-partner conversations,
40 dev and 20 heldout. Each ends at the user turn whose next action is scored.
Inputs are separate from `expected`, `crisis`, `reason` and `mock_result`, which
never reach the model before the first decision. Six ambiguous cases require
clarification without a call. Three have `expected.optional: true`: either abstain
or save exactly the allowed durable fact. Wrong tools, ungrounded facts and malformed
calls fail those optional cases. This flag is only a scoring label; the model sees
the same fixed save policy on every case.

There are 27 required-call cases, 30 strict no-call cases and three optional saves.
The 12 added cases include three implicit recalls, three 7–9-turn conversations
whose answers are already in context, three optional durable saves, and three
trivia negatives. The other cases vary between three and five turns.

| Category | Dev | Heldout | Total |
|---|---:|---:|---:|
| `recall_memory` | 6 | 3 | 9 |
| `remember` | 4 | 2 | 6 |
| `web_search` | 4 | 2 | 6 |
| `get_time` | 4 | 2 | 6 |
| `no_tool` | 12 | 6 | 18 |
| `ambiguous` (6 no-call, 3 optional-save) | 6 | 3 | 9 |
| `crisis` (no call) | 4 | 2 | 6 |

Run with the same CLI and callable interface as the existing evals:

```sh
python evals/check.py
python -m evals.run tools --split dev --out /tmp/tools-dev.json
python -m evals.run tools --split heldout --repeats 3 --out /tmp/tools-heldout.json
```

For later model evaluation, call `evals.run.evaluate("tools", rows, model=callable,
split="heldout")`. The callable takes ordinary `{role, content}` messages and
returns raw text. No judge, extractor or external service is used for this set.
The historical `evals.llm_bakeoff` keeps its existing four-set roster and recorded
reply compatibility; wiring the new student bake-off is a later task.

| Metric | Definition |
|---|---|
| `call_accuracy` | Correct call/no-call decisions / all cases. Malformed output is always wrong, not an abstention. For required-call cases, wrong tools or arguments can still be a correct decision to call. Optional cases require either no call or the right tool and fact. |
| `tool_accuracy` | Valid calls to the right tool / required-call cases plus attempted optional saves; missed required calls score zero, optional abstentions are excluded. |
| `argument_quality` | Right-tool calls passing every argument rule / required-call cases plus attempted optional saves; missed/wrong required calls and invalid optional saves score zero. |
| `over_call_rate` | Disallowed attempts / all cases permitting abstention (30 strict no-call plus 3 optional). Correct optional saves are not disallowed; trivia saves, wrong optional tools/facts and malformed attempts are. |
| `optional_call_accuracy` | Correct abstentions or grounded saves / optional-save cases. |
| `malformed_call_rate` | Invalid call attempts / all call attempts, measured on the first generation. |
| `malformed` | Count of all invalid first generations, including missing state tokens. |
| `crisis_priority_accuracy` | Correct crisis flag with no attempted call / crisis-priority cases. |
| `crisis_accuracy` | Correct crisis flag on valid first generations / all cases, exposing false positives too. |
| `continuation_valid_rate` | Valid state and no further tool attempt / result-or-error continuations. No repeated call is dispatched. |

An argument rule has required regex concepts (all must match) and forbidden
patterns (none may match), case insensitive. Saved facts additionally have an
`allowed` vocabulary pattern: every word must be supported content or neutral
grammar. This rejects added professions, locations, numbers or other unsupported
detail while allowing pronoun-free fragments, third-person facts and authored
synonyms. Contradiction patterns reject reversed preferences; merely mentioning
"manager" does not fail a fact about staying an IC. BPL is accepted as an alias
for Boston Public Library. `get_time` succeeds with exactly `{}`.
These deterministic rules check the core content and catch omitted context,
reversed preferences and unsupported additions in the authored cases. They remain
a lexical proxy, not a general entailment test: keyword stuffing can pass and
unseen valid paraphrases can fail.
Human review of argument quality is still needed in the later bake-off. Speech
quality and whether a final answer uses the receipt correctly are not scored by
this set; the existing persona and crisis evals remain required.

Zero denominators are `null`, not a perfect score. The fixed neutral stub never
calls a tool: it scores 0.55 call accuracy, 1.0 optional-call accuracy, zero tool/argument/crisis-priority
accuracy, and null malformed-call/continuation rates. CLI exit 0 means the run
completed. Nonempty tool runs report `MEASURED` with empty gates, not `PASS`;
there are no maintainer-approved quality thresholds for this new set yet.
The new set is a review draft, not a frozen release gate. Keep its heldout cases
out of prompt tuning and future fine-tuning data.

Open review decisions: approve the new cases and freeze, set quality thresholds,
and decide whether later conversational tasks need more than one tool per turn.
