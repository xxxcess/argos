# Argos Venture Product Principles

## Purpose

Argos Venture is an evidence-guided exploration workspace. It helps a Captain and optional Shipmates navigate unfamiliar information, preserve what matters, and turn well-supported discoveries into durable Markdown knowledge.

Argo is inspired by the talking ship of Greek myth: an intelligent guide that helps a crew orient itself in unknown territory. In Venture, Argo does not replace human judgment. It helps the crew notice evidence, identify patterns and gaps, keep track of what has changed, and prepare useful findings for Captain review.

A Venture Quest moves through a deliberate loop:

```text
Unknown → observed evidence → emerging insight → reviewed artifact → shared direction or solution
```

## Product boundary

`nightly` remains the normal Odysseus product line: a full general-purpose AI workspace.

`argos-venture` is a separate product line for source-grounded, collaborative exploration. It uses the same runtime-isolation rules as the broader Argos project, but has its own product language, authorization model, Quest workflows, and memory boundary.

Do not add Venture terminology, Shipmate restrictions, Quest Source workflows, or Captain publication approval to `nightly`.

## Venture language

| Existing concept | Venture term |
| --- | --- |
| Admin | Captain |
| Regular user | Shipmate |
| Session | Quest |
| Assistant | Argo |
| Chat history | Voyage Log |
| Source attached to a Quest | Quest Source |
| Captain-private generated finding | Artifact Draft |
| Published Quest document | Quest Artifact |
| Quest goal or investigation focus | Current Bearing |

Keep familiar labels where they are clearer: Search, Settings, Account, Documents, Gallery, Email, and Theme.

## Roles

### Captain

A Captain owns a Quest and sets its Current Bearing. Captains choose and scope sources, recruit Shipmates, direct the exploration, use the full authorized Venture tool surface, and decide which proposed findings become shared Quest Artifacts.

Captain access never overrides another Captain's owner-based privacy. A Captain does not automatically gain access to another Captain's private Quest, source scope, messages, documents, artifacts, or workspace data.

### Shipmate

A Shipmate is a regular user invited into an individual Quest. Shipmates are optional: a Captain may venture alone.

A Shipmate helps the Captain notice information, patterns, contradictions, and unanswered questions that might otherwise be missed. Shipmates may participate only in accepted Quests and may never create Quests, alter sources, use Captain-only systems, invoke tools, publish artifacts, or escape the Quest access boundary.

A pending invitation grants no Quest access.

### Argo

Argo is a virtual Quest participant, not an authentication account or membership row. It provides operational context, evidence-grounded synthesis, and useful next steps. It must never present unsupported conclusions as confirmed facts, and it must never reveal protected prompts, credentials, raw tool payloads, internal endpoints, filesystem paths, or private source data to an unauthorized participant.

## Quest creation

A Venture Quest is created around a primary Quest Source and a clearly stated Current Bearing. It is not an empty generic chat.

The Captain creation flow is:

```text
1. Set Current Bearing
2. Choose Quest Source
3. Define source scope and access
4. Recruit Shipmates (optional)
5. Review and launch Quest
```

A Current Bearing records the Quest title, exploration goal, initial question or hypothesis, intended outcome, and optional constraints or time horizon.

## Quest Sources

Every Quest has a primary Quest Source. Sources are either static or dynamic.

### Static sources

Static sources are bounded inputs that may be scanned, indexed, queried, or deliberately refreshed.

Examples include:

- Websites or captured web pages
- Files on disk
- CSV datasets
- PDFs
- Text and Word documents
- Captain-owned Documents
- Connected databases queried through a defined schema or saved query

Static sources retain capture time, provenance, and source-version metadata. A refresh creates a new source version; it must not silently overwrite historical context.

### Dynamic sources

Dynamic sources expand over time and require incremental polling to maintain current context.

For the first Venture release, Email is the only dynamic source. An Email Quest Source records a Captain-owned integration reference plus a narrow mailbox, folder, label, sender, subject, or search-query scope. It uses a checkpoint or cursor for incremental updates.

A Quest must never default to scanning a Captain's entire mailbox. Email credentials are never stored in a Quest record.

### Source access modes

Each Quest Source has an explicit Shipmate visibility mode:

```text
captain_only
shared_read
shared_summaries
```

- `captain_only`: Shipmates can see Voyage Log discussion and published Artifacts but not raw source material.
- `shared_read`: Shipmates can search and read the scoped Quest Source.
- `shared_summaries`: Shipmates can see Quest-safe summaries but not raw source records.

Email defaults to `captain_only` unless the Captain explicitly chooses another access mode.

## Invitations and participation

A Captain recruits Shipmates by selecting existing regular users. The system creates a pending Quest Invitation, then sends the invitee an actionable Inbox notification with **Accept** and **Decline** actions.

```text
Captain selects Shipmate
→ pending Quest Invitation
→ Shipmate Inbox invitation
→ Accept or Decline
→ accepted invitee becomes a Quest Member
```

The Captain receives a corresponding Inbox notification when the Shipmate accepts or declines. Pending invitees cannot see the Quest title, Voyage Log, source content, artifacts, roster, search results, or metadata.

## Voyage Log

The Voyage Log is the chronological record of the exploration. It makes the crew's progress and evidence trail legible without turning the experience into a game.

Important event types include:

```text
quest_created
current_bearing_updated
source_connected
source_refreshed
source_update_detected
captain_decision
shipmate_message
argo_status
argo_synthesis_started
argo_synthesis_completed
artifact_draft_created
artifact_published
artifact_declined
shipmate_invited
shipmate_joined
shipmate_declined
shipmate_removed
system_event
```

Shipmates see only Quest-safe event summaries. Captain views may include appropriate agent and tool activity, subject to existing authorization and privacy rules.

## Artifact Drafts and Quest Artifacts

Argo may identify meaningful changes, evidence-backed patterns, contradictions, open questions, decisions, and candidate solutions from Quest messages and permitted source updates.

Argo records these findings as Markdown **Artifact Drafts** in the Captain's private Library. A draft is not visible to Shipmates and is not yet assigned to a Quest.

The Artifact lifecycle is intentionally narrow:

```text
draft → pending Captain review → published
                         ↘ declined
```

On publication:

1. The Quest Captain approves the draft.
2. The Markdown Document is assigned to the Quest.
3. It becomes a Quest Artifact.
4. The Voyage Log records the publication.
5. Accepted Shipmates receive an Inbox notification and may open the published artifact.

On decline, the document remains Captain-private unless the Captain deletes it. No Shipmate is notified.

Every generated draft should explain what was discovered, why it matters, its evidence, confidence and uncertainty, and the recommended next bearing.

## Argo Exploration Synthesis

Each Quest has a Captain-controlled background process called **Argo Exploration Synthesis**. It considers new Voyage Log messages, source refreshes, dynamic source updates, existing published artifacts, and pending drafts.

The task pipeline is:

```text
New Quest messages
or Static source refresh
or Dynamic source update

→ source delta and context preparation
→ evidence-grounded insight extraction
→ duplicate and novelty check
→ private Markdown Artifact Draft in Captain Library
→ Captain Inbox approval request
→ publication or decline
→ Shipmate publication notification when applicable
```

The process must batch related changes, apply evidence thresholds and novelty checks, and update or supersede related drafts rather than creating notification noise.

## Quest-local memory principle

Venture memory serves one Quest at a time. It is not a Captain-centered cross-chat memory system.

Each Quest receives an isolated Voyage Memory partition that recalls the Current Bearing, evidence, open questions, decisions, source state, artifact links, and other durable exploration context for that Quest only. Memory from one Quest must never be retrieved, injected, searched, or silently reused in another Quest, even when the Captain, Shipmate, or source connection is the same.

Detailed memory design belongs in the Venture implementation plan. The core boundary is non-negotiable: no cross-Quest recall.

## Design principles

- Calm, purposeful, and modern workspace first; restrained mythological orientation second.
- No pirate clichés, fake maps, novelty gauges, XP, or gamified progress bars.
- Use semantic language: Active, Archived, Assigned, Draft, Published, Declined, Current, Needs Review, and Superseded.
- Never rely on color, animation, or icon alone to communicate meaning.
- Treat Shipmate input as useful but untrusted content. It cannot change system instructions, tool policy, role rules, authorization, source scope, or model configuration.
- Apply authorization in the backend to every list, read, search, preview, download, mutation, tool, attachment, websocket, and legacy route.

## Related documentation

- [Runtime profiles](runtime-profiles.md)
- [Setup guide](setup.md)
