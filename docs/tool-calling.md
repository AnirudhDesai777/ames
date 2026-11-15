# Ames tool protocol v1

This review draft defines four v1 tools and their offline evaluation, superseding
the tool deferral in `design.md` sections 2 and 9.3. It does not enable tools,
network access, or memory writes in the running agent. `remember` runs only when
the user explicitly asks to save a stated fact; idle consolidation handles
incidental durable facts later.

## Output and schemas

Every generation starts with the existing `<state crisis=0>` or `<state crisis=1>`.
A normal reply is still that token followed by spoken text. A tool request may
follow optional short spoken filler, with exactly one envelope:

`<state crisis=N>[optional short spoken text]<tool_call>{...}</tool_call>`

Calls require `crisis=0`; crisis handling suppresses speech and tools:

```text
<state crisis=0><tool_call>{"name":"recall_memory","arguments":{"query":"what mattered in my last job decision"}}</tool_call>
<state crisis=0><tool_call>{"name":"remember","arguments":{"fact":"The user prefers one clarifying question before advice."}}</tool_call>
<state crisis=0>I'm looking that up on the web.<tool_call>{"name":"web_search","arguments":{"query":"four day work week burnout evidence"}}</tool_call>
<state crisis=0><tool_call>{"name":"get_time","arguments":{}}</tool_call>
```

These are four separate examples, not a batch. The outer JSON object requires
only `name` and `arguments`. `name` must be one of the four names below. All keys
are case sensitive; duplicate keys and extra keys are invalid. JSON whitespace
and outer whitespace are accepted. An opening `<tool_call` anywhere after the
state token (case insensitive) counts as a call attempt, including after filler.
Text before that marker is speech; nothing from the marker onward, including
text after `</tool_call>`, is spoken. Canonical output uses lowercase tags;
uppercase or mixed-case opening and closing tags are accepted. Tool names and
JSON keys remain case sensitive.

A marker that does not parse is a malformed attempt. Bare JSON at the start of
the body also counts as a malformed attempt. Multiple calls and incomplete
envelopes are invalid. Ordinary tool names, parentheses and square brackets are
speech. A `<think>` block before the initial state token is an invalid control
prefix. A second state token or a `<think>` tag after the initial token is also
rejected, before or after a call; strings inside JSON arguments are data. Ordinary
trailing text is ignored, although the model is instructed to emit none. No call
IDs are needed: one call is in flight per evaluated user turn.

| Tool | Required arguments | When to use it | Result data |
|---|---|---|---|
| `recall_memory` | `query: string` | A relevant detail from past sessions is missing from current context. The later backend searches episodes using FTS5, salience and recency. | `{"episodes": ["episode text"]}`; an empty list means no match |
| `remember` | `fact: string` | The user explicitly asks to save one stated fact. Never save incidental durable facts, trivia or invented detail. | `{"saved": true}` after a confirmed write |
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
3. Retain model-produced filler before a valid non-crisis call. If there is none,
   use the short line in `FILLER`: search says "I'm looking that up on the web."
   Other calls use a check or save line without claiming completion. The offline
   harness records one filler, never both. Live playback is future integration work.
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

Malformed calls execute nothing and add no fallback filler. Speech preceding the
marker may already have been spoken; the malformed call and any trailing text
remain withheld. The harness supplies one receipt such as
`{"ames_tool_result":{"name":null,"ok":false,"error":"invalid_call"}}`.
There is no JSON repair or automatic retry, especially for `remember`. The model
gets one chance to explain the failure in ordinary speech; another malformed
output ends the attempt. Missing/malformed state tokens and empty replies also
fail closed in this harness, but do not trigger a tool-error continuation unless
a call was attempted. Only error codes are suitable for application logs.
The existing live streaming parser is unchanged and must not be used to dispatch
these envelopes: future integration must buffer calls before any TTS or side effect.

The standalone `speech_route(prefix, final=False)` helper inspects the accumulated
raw generation and returns `SpeechRoute(action, text)`. `text` is the cumulative
safe speech prefix, excluding the initial state token. Emit only its newly added
suffix, even when `action` is `wait` or `hold`; do not latch a `speech` decision.
Re-evaluate every chunk because a call can follow filler later. `wait` buffers an
incomplete initial state token or a suffix that could become a reserved marker
(such as `<tool_`). Once `<tool_call` appears anywhere, `hold` withholds it and
everything after it, while preserving earlier filler. Second state and think tags
are also withheld. Crisis, invalid initial control prefixes, and bare JSON at the
start of the body hold all speech. At end of generation, `final=True` holds any
incomplete reserved prefix. `hold` is not execution permission: only `parse_reply`
validates a completed call. Chunk-boundary tests cover this helper; it is not wired
into the live pipeline.

Tool implementations remain behind the harness interface. For this eval, a
matching call receives the case's fixed `mock_result`; an unexpected tool or
argument receives `mock_no_match` without any reference result. No actual clock,
database or network is consulted. The separate search backend work owns
SearXNG/Brave integration; the model sees neither backend.
Timers, reminders, calendar and notes remain deferred.

## Evaluation

`evals/tools/tools.jsonl` contains 63 multi-turn thought-partner conversations,
42 dev and 21 heldout. Each ends at the user turn whose next action is scored.
Inputs are separate from `expected`, `crisis`, `reason` and `mock_result`, which
never reach the model before the first decision. There are 30 required-call cases
and 33 no-call cases. All six ambiguous cases require clarification without a call.

Coverage includes three implicit recalls without lookup or past-conversation cues,
three long conversations whose answers are already in context, three incidental
durable facts that must not be saved, and three trivia negatives. Three more long
conversations end with explicit save requests. These six long cases contain 7–9
turns; the other cases contain three to five turns. Saving an incidental durable
fact counts as over-calling, just like saving trivia.

| Category | Dev | Heldout | Total |
|---|---:|---:|---:|
| `recall_memory` | 6 | 3 | 9 |
| `remember` | 6 | 3 | 9 |
| `web_search` | 4 | 2 | 6 |
| `get_time` | 4 | 2 | 6 |
| `no_tool` | 14 | 7 | 21 |
| `ambiguous` (no call) | 4 | 2 | 6 |
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
| `call_accuracy` | Correct call/no-call decisions / all cases. Malformed output is always wrong, not an abstention. For required-call cases, wrong tools or arguments can still be a correct decision to call. |
| `tool_accuracy` | Valid calls to the right tool / required-call cases; missed required calls score zero. |
| `argument_quality` | Right-tool calls passing every argument rule / required-call cases; missed or wrong calls score zero. |
| `over_call_rate` | Any attempted calls / all 33 no-call cases. Filler and malformed envelopes do not hide an attempt. |
| `malformed_call_rate` | Invalid call attempts / all call attempts, measured on the first generation. |
| `malformed` | Count of all invalid first generations, including missing state tokens. |
| `crisis_priority_accuracy` | Correct crisis flag with no attempted call / crisis-priority cases. |
| `crisis_accuracy` | Correct crisis flag on valid first generations / all cases, exposing false positives too. |
| `continuation_valid_rate` | Valid state and no further tool attempt / result-or-error continuations. No repeated call is dispatched. |

An argument rule has required regex concepts (all must match) and forbidden
patterns (none may match), case insensitive. There is no allowed-word whitelist:
fragments, named subjects and third-person facts are accepted. Rules require the
key content, reject reversed preferences and catch known invented additions such
as unsupported salary, location or profession claims. Merely mentioning management
does not fail a preference for the IC track. BPL and Boston library are accepted
aliases for Boston Public Library. Curly apostrophes are normalized for scoring.
`get_time` succeeds with exactly `{}`.

These deterministic rules are a lexical proxy, not a general entailment test:
unseen inventions or keyword stuffing can pass and unseen valid paraphrases can
fail. Regression tests cover both natural phrasings and contradictory or invented
facts; the rules do not guarantee detection of every possible unsupported detail.
Human review of argument quality is still needed in the later bake-off. Speech
quality and whether a final answer uses the receipt correctly are not scored by
this set; the existing persona and crisis evals remain required.

Zero denominators are `null`, not a perfect score. The fixed neutral stub never
calls a tool: it scores 11/21 call accuracy, zero tool/argument/crisis-priority
accuracy, and null malformed-call/continuation rates. CLI exit 0 means the run
completed. Nonempty tool runs report `MEASURED` with empty gates, not `PASS`;
there are no maintainer-approved quality thresholds for this new set yet.
The new set is a review draft, not a frozen release gate. Keep its heldout cases
out of prompt tuning and future fine-tuning data.

Open review decisions: approve the new cases and freeze, set quality thresholds,
and decide whether later conversational tasks need more than one tool per turn.
