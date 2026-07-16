# Invoice AP Automation with AI Exception Handling

Every company that pays other companies has the same boring, expensive problem: someone has to open every incoming invoice, check whether it's from a vendor they actually work with, check whether the math adds up, check whether it's expensive enough to need a manager's sign-off, and decide whether to pay it or flag it. Do that by hand across hundreds of invoices a month and mistakes happen, not because anyone's careless, but because checking the same six things over and over is exactly the kind of task humans are bad at sustaining attention on.

So I built two AI-backed services that do this instead, wired together into a real pipeline: one reads the invoice, one decides what to do about it, and a human only gets pulled in when something's actually wrong.

## The two helpers

Think of it like a small back office with two people in it.

The first person's whole job is reading. You hand them a PDF, and they write down, cleanly, what's actually on it: who it's from, what was bought, what it adds up to. That's the extraction service — except instead of a person, it's an LLM doing the reading, which matters because real invoices don't all look the same, and a rigid template-matching script breaks the moment a vendor changes their layout. An LLM reads it the way a person would: by understanding it, not by matching coordinates on a page.

The second person's job is judgment, not reading. They take that clean summary and run it against a checklist: is this vendor one we actually approve of? Does the total match what was actually bought? Is this over the amount I'm allowed to approve without asking someone above me? Does anything about this invoice number look off? If everything checks out, they stamp it approved. If anything's wrong, they don't just say "something's wrong"; they say exactly what, so whoever looks at it next doesn't have to start from scratch.

That second part is the actual point of this project. Reading a document is a solved problem now. Making a correct, explainable decision from what you read, and knowing when your own read might be wrong, is the harder and more useful thing, and it's the part that actually maps onto what automation engineering jobs ask for.

## How I tested it without a single real invoice

I don't have access to a real company's invoices, obviously, so I built my own test set: 30 synthetic PDF invoices, generated with known correct answers, and deliberately broken in six specific ways — a wrong total, an unapproved vendor, an amount over the spending limit, a missing invoice number, a garbled date, and one where everything's just fine. Writing your own trick questions before taking your own test might sound circular, but it's the only honest way to prove a validation system actually catches what it claims to catch, rather than just looking plausible on the cases that happen to work.

**Results, measured, not estimated:**
- Extraction: 29 of 30 invoices processed successfully, **100% field accuracy on every one that succeeded** (vendor, total, invoice number, all correct)
- Validation: **29 of 29 correct routing decisions** — every invoice that reached this stage got the right auto-approve or flag-for-review call, with the right reason attached
- One invoice failed extraction outright, from a free-tier model timing out under load — the pipeline correctly logged it and routed it to a human instead of guessing, which is the behavior that actually matters when something goes wrong

## A bug I found, and what it changed

During testing, the model transcribed an invoice number as `INV-20202600023` instead of the correct `INV-202600023` — it duplicated four characters mid-string. Extraction was otherwise flawless on that same invoice: right vendor, right total. Just that one field, garbled in a way that would sail past anyone glancing at it quickly.

That's exactly the kind of error a validation layer exists to catch, so I added a rule for it: invoice numbers get checked against the expected format, not just checked for presence. It's a small rule, but it's in the pipeline because of a real failure I watched happen, not because I imagined it might.

## Architecture

```
Webhook receives a PDF
  → Extraction service (FastAPI + LLM via OpenRouter, forced structured output)
  → Validation service (FastAPI, 6 independent business rules)
  → Postgres (permanent record: every decision and every reason, auto-approved or not)
  → Telegram (alert, but only when a human actually needs to look at something)
```

n8n orchestrates the whole thing — receives the file, calls both services, logs the outcome, routes the alert. Same shape as a document-processing pipeline or a CRM sync at a real company: an orchestration layer that doesn't do any of the actual thinking, wired to purpose-built services that do.

## What actually went wrong building this

**The free LLM sometimes ignored my forced structured-output request entirely** and just wrote JSON as plain text instead, which occasionally got cut off mid-object before finishing. Fixed with more token headroom and a fallback parser that catches valid JSON even when it didn't arrive through the expected channel — turned two of the four non-rate-limit failures from hard errors into working extractions.

**Free-tier rate limits hit mid-run**, exactly as the error message said they would ("temporarily rate-limited upstream, retry shortly"). Added retry-with-backoff directly into the service, not just the test script, since a production pipeline needs to survive this on its own.

**A node reference that worked in isolation broke once the data passing through it completely changed shape.** n8n's `$('NodeName').item` syntax relies on being able to trace an item back through the chain it came from, and that tracing broke once one node's output looked nothing like its input. Switched to `.first()`, which doesn't depend on that chain surviving intact.

**A single deactivated node cost more debugging time than any of the actual logic bugs.** The webhook itself had been silently toggled off during an earlier round of edits, and every symptom downstream — no response, no database write, no alert — had a plausible-sounding logic explanation that turned out to be wrong. The actual answer was sitting in plain text on the canvas the whole time: "(Deactivated)." Worth remembering next time something inexplicably doesn't fire: check the obvious state before reasoning further out from it.

## Stack

n8n · FastAPI · PostgreSQL · Docker Compose · Python · an LLM via OpenRouter · Telegram Bot API

## Running it yourself

Full setup steps, including how to regenerate the test invoices and run the accuracy evaluations yourself, are in `SETUP.md`.
