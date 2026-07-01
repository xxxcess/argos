<p align="center">
  <strong>ARGOS VENTURE</strong><br>
  <em>An evidence-guided workspace for crews exploring the unknown.</em>
</p>

---

# Argos Venture

**Argos Venture** is a separate Argos product line for productive, source-grounded exploration. It is inspired by Argo, the talking ship of Greek myth: an intelligent guide that helped a crew navigate unfamiliar territory.

In Venture, Argo helps Captains and optional Shipmates investigate a defined source of information, notice evidence and patterns, preserve what matters, and turn supported discoveries into durable Markdown knowledge.

```text
Unknown → observed evidence → emerging insight → reviewed artifact → shared direction or solution
```

Read the full [Argos Venture product principles](docs/argos-venture.md).

> **Branch status:** `argos-venture` is the design and implementation line for this product model. `nightly` remains the normal Odysseus workspace product line. Venture-specific roles, source workflows, artifact approval, and Quest-local memory must not be added to `nightly`.

## The Venture model

### Captains, Shipmates, and Argo

- **Captains** are existing administrators. They set a Quest's Current Bearing, choose and scope sources, use the authorized Captain tool surface, recruit Shipmates when helpful, and decide which findings become shared Quest knowledge.
- **Shipmates** are existing regular users recruited into individual Quests. They are optional collaborators who help the Captain notice patterns, contradictions, and unanswered questions. A Captain may venture alone.
- **Argo** is the virtual ship AI, not an account or membership row. It guides exploration with evidence-grounded summaries, operational context, and useful next steps; it does not replace Captain judgment.
- **Quests** are source-grounded exploration threads. Their central record is a chronological **Voyage Log**, not a generic chat transcript.

Captain access does not override another Captain's owner-based privacy. A Shipmate receives no Quest access until accepting an invitation.

### Quest Sources

A Venture Quest begins with a primary **Quest Source** and a stated **Current Bearing**. It is not an empty generic chat.

A Captain creates a Quest through this flow:

```text
1. Set Current Bearing
2. Choose Quest Source
3. Define source scope and access
4. Recruit Shipmates (optional)
5. Review and launch Quest
```

Quest Sources are either:

- **Static** — websites, files, CSV datasets, PDFs, text or Word documents, Captain-owned documents, and connected databases queried through a defined schema or saved query.
- **Dynamic** — sources that grow over time and need incremental polling. For the first Venture release, Email is the only dynamic source.

Every source has an explicit Shipmate access mode:

```text
captain_only | shared_read | shared_summaries
```

Email defaults to `captain_only`. A Quest must never default to reading an entire mailbox; it stores only a Captain-owned integration reference and a constrained scope such as mailbox, label, sender, subject, or search query.

### Invitations

Captains recruit Shipmates through a durable Quest Invitation, not immediate membership:

```text
Captain selects Shipmate
→ Shipmate receives Inbox invitation
→ Shipmate Accepts or Declines
→ accepted invitee becomes a Quest Member
```

The Shipmate's notification bell Inbox must show both **Accept** and **Decline** actions. The recruiting Captain receives an Inbox notification for either response. Pending invitees cannot see the Quest, Voyage Log, source content, artifacts, crew, search results, or related metadata.

### Artifact Drafts and publication

Argo Exploration Synthesis examines new Voyage Log messages, permitted source updates, existing Artifacts, and open questions. When a meaningful evidence-backed insight or potential solution emerges, Argo creates a Markdown **Artifact Draft** in the Captain's private Library.

A draft remains private until the Captain approves it:

```text
draft → pending Captain review → published
                         ↘ declined
```

When published, the Markdown Document is assigned to the Quest, becomes a **Quest Artifact**, is recorded in the Voyage Log, and notifies accepted Shipmates. A declined draft remains Captain-private unless the Captain deletes it.

Every proposed artifact should state what was discovered, why it matters, the relevant evidence, confidence and uncertainty, and the recommended next bearing.

### Quest-local memory

Venture memory is fundamentally session-focused. Each Quest owns an isolated **Voyage Memory** partition that records its Current Bearing, evidence, open questions, decisions, source state, artifact links, and other durable exploration context.

Memory from one Quest must never be retrieved, injected, searched, or silently reused in another Quest—even where the Captain, Shipmate, or source connection is the same. Venture does not use the inherited Captain-centered cross-chat memory model.

## Access and data-boundary contract

Argos Venture enforces all role, membership, source, and artifact restrictions in the backend, not only by hiding UI.

- A Quest has one Captain and zero or more accepted Shipmates.
- Shipmates may read only joined Quests and only permitted source material and Artifacts.
- Shipmates may send plain text messages only in joined Quests.
- Shipmates cannot create Quests, alter source scopes, use tools or agent mode, switch models or workspaces, upload or attach files, create or publish Artifacts, or access Captain-only systems.
- A generated Artifact Draft is Captain-private until publication.
- Direct object URLs, list endpoints, search, downloads, previews, thumbnails, websocket payloads, legacy routes, and crafted requests must apply the same authorization.
- Existing owner-based isolation remains in force for Captain-private data.

`owner IS NULL` legacy/shared records must never accidentally become visible to Shipmates. Migrations must classify them explicitly.

## Development flow

`argos-venture` starts from `nightly` and is an intentional product fork, not a literal Git fork.

```text
nightly
  └── argos-venture
        ├── feat/venture-quest-sources
        ├── feat/venture-quest-membership
        ├── feat/venture-artifact-synthesis
        ├── feat/venture-voyage-memory
        └── feat/venture-shipmate-shell
```

Use focused feature branches from `argos-venture` and merge them back through reviewed pull requests. Periodically merge `nightly` into `argos-venture`; never force-push the voyage branch.

## Runtime profiles and isolated persistence

Argos Venture and the inherited Odysseus/nightly product line must never share runtime data. Switching Git branches changes code; it must not accidentally reuse another product line's users, database, encryption key, uploads, Gallery assets, generated images, vector stores, settings, Quests, or caches.

The macOS launcher reads the committed profile:

```text
config/runtime-profile.env
```

The profile is versioned with the branch. It is the launcher source of truth for product identity and safe persistence names; it must not infer those values from the active Git branch.

`nightly` and its feature branches use an Odysseus profile:

```bash
ARGOS_RUNTIME_ID=odysseus
ARGOS_PRODUCT_NAME=Odysseus
ARGOS_STORAGE_SLUG=odysseus
ARGOS_DEFAULT_PORT=7860
ARGOS_DEFAULT_CHROMADB_PORT=8100
```

`argos-venture` and its feature branches use an Argos Venture profile:

```bash
ARGOS_RUNTIME_ID=argos-venture
ARGOS_PRODUCT_NAME="Argos Venture"
ARGOS_STORAGE_SLUG=argos-venture
ARGOS_DEFAULT_PORT=7861
ARGOS_DEFAULT_CHROMADB_PORT=8101
```

When `start-macos.sh` launches the application, it must load this profile and derive a profile-specific data root by default:

```text
~/Library/Application Support/Argos/runtimes/<ARGOS_STORAGE_SLUG>/
```

For Argos Venture, this becomes:

```text
~/Library/Application Support/Argos/runtimes/argos-venture/
├── app.db
├── auth.json
├── .app_key
├── settings.json
├── uploads/
├── gallery/
├── generated_images/
├── chroma/
├── rag/
└── runtime-manifest.json
```

The launcher exports `ARGOS_DATA_DIR` and retains `ODYSSEUS_DATA_DIR` as a temporary compatibility alias pointing to the same profile-specific path. The default database, Chroma path, runtime ports, logs, and all application-derived files must use the active profile.

A startup guard writes and validates `runtime-manifest.json`, including runtime ID and storage slug. Startup must stop on a profile/data-root mismatch unless an explicit maintenance override is supplied. It must never copy, migrate, or merge data merely because a user changed Git branches.

Local overrides remain acceptable only when profile-safe. Prefer a shared parent root such as `ARGOS_DATA_ROOT`; do not point `ARGOS_DATA_DIR` or `DATABASE_URL` at another product line's persistence without an explicit migration process and matching manifest.

## Quick start on macOS

```bash
git clone https://github.com/xxxcess/argos.git
cd argos
git switch argos-venture
./start-macos.sh
```

The Venture runtime profile determines the default local port and persistence location. `start-macos.sh` may read a local `.env` for machine-specific values, but `.env` must not collapse isolated runtime profiles into a shared data directory.

## Implementation milestones

1. Maintain committed runtime profiles, profile-specific persistence roots, and mismatch protection.
2. Add Quest Sources, constrained Email polling, source versions, source access modes, and server-side source authorization.
3. Add invitation-based Shipmate recruitment and non-enumerating Quest membership controls.
4. Add Argo Exploration Synthesis, evidence provenance, private Markdown Artifact Drafts, deduplication, and Captain review notifications.
5. Add Captain publication approval, Quest Artifact assignment, Voyage Log publication events, and Shipmate publication notifications.
6. Replace inherited cross-chat memory with strictly Quest-local Voyage Memory partitions.
7. Rename sessions to Quests, add Current Bearing and Voyage Log presentation, and expose Argo's safe operational status.
8. Build the reduced Shipmate shell, dashboard, account-only Settings, sidebar Theme control, source-safe search, and plain-text Quest chat.
9. Replace inherited Odysseus naming, labels, assets, metadata, and visible copy with Argos Venture branding.
10. Add authorization, runtime-isolation, source, memory, notification, migration, and end-to-end tests for Captains, Shipmates, Quests, Artifacts, and direct-route access.

## Security

Argos Venture is a self-hosted workspace with powerful local tools. Keep authentication enabled, do not expose raw model or service ports publicly, and treat Captain credentials, scoped sources, Voyage Memory, and Quest Artifacts as sensitive. The server must enforce every role, source, membership, publication, and memory boundary even when a caller bypasses the frontend. Runtime profiles are a data-isolation boundary: do not reuse a profile's database, auth state, encryption key, or asset directories for another product line without an explicit migration.

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
