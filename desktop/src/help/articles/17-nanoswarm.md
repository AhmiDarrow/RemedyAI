# Context harness and token counting

This chapter is for **owners and operators** who want to know how Remedy keeps a
long conversation inside a model's context window, and where the numbers in
**Usage → Harness** come from. In normal chat you do not need any of it.

## What it does

Every model has a window: the most text it can read in one request. A long task
with many tool results would outgrow it. Before each model round the runtime
counts the conversation and, when it is getting full, makes it smaller in two
steps:

1. **Trim.** The bodies of old tool results in the middle of the conversation
   are replaced by one-line stubs (which tool, which file or command, whether it
   succeeded). The first message, your latest request and the most recent
   messages stay as they are.
2. **Compact.** If that is not enough, the middle of the conversation is
   replaced by one working-set note: files read and changed, commands run and
   their results, open to-dos.

Each tool result is also capped when it arrives (30,000 characters on a large
window, 8,000 on a small local one), so a single dump cannot fill the window.

All of this runs inside the Remedy runtime on your computer. Nothing is sent
anywhere to do it.

## The setting

**Settings → Models → Memory harness**

| Setting | Meaning | Default |
|---------|---------|---------|
| Mode | **Auto** trims and compacts at the two points below. **Off** leaves the conversation whole until it would no longer fit the window. | Auto |
| Trim old tool output from | Share of the window at which step 1 starts. | 75% |
| Compact before | Share of the window at which step 2 happens. | 92% |

A lower number saves tokens (and cost) sooner; a higher one keeps more of the
original text in front of the model for longer. 16,000 tokens of the window are
always held back for the reply.

The window itself comes from the model. You can set it by hand under
**Settings → System → Generation → Context window (tokens)** when a provider
reports none.

## Token counting

Tokens are counted with a real tokenizer, not a characters-divided-by-four
guess: the `o200k_base` byte-pair encoding, through the open-source
[tiktoken-go/tokenizer](https://github.com/tiktoken-go/tokenizer) library (MIT
licence). The vocabulary is compiled into the runtime; it needs no download and
makes no network request.

What that means for accuracy:

- For models that use `o200k_base` the count is exact for the text.
- Other providers do not publish their tokenizers. For them the count is an
  estimate, usually within a few percent for ordinary prose and code.
- An image is charged a flat 1,500 tokens.
- The count decides when to trim and compact. **What you are billed is what the
  provider's API reports**, and that is what **Usage → Usage & Cost** shows.

## The Harness tab

**Usage → Harness** shows what the runtime measured for the current session
since Remedy started. Before the first turn the figures are empty; nothing is
filled in for show.

| Figure | Meaning |
|--------|---------|
| Context fill | The conversation's size at the last model round, as a share of the window. |
| Context budget | The size at which the conversation is compacted. |
| Turns · saved | Turns run, and tokens removed by trimming and compacting. |
| Compresses | How many times the conversation was trimmed or compacted. |
| Tool fail max | The longest run of failed tool calls in a row. |
| Pattern window | Tool calls run, the share that succeeded, and the most recent ones. |
| Provider health | Model rounds asked for, the share that failed after retries, and rate-limit refusals. |
| Tokenizer | The encoding in use and the last count. |

These figures live in memory and reset when Remedy restarts.

## Limits

- There is no per-provider calibration: one encoding is used for every model.
- The figures are per session and are not kept across restarts.

See also: [Continuity philosophy](16-continuity-philosophy) ·
[Security & data](04-security-and-data)
