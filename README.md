<p align="center">
  <strong>ARGOS VENTURE</strong><br>
  <em>A voyage-led AI workspace for captains, shipmates, quests, and the living log of an expedition.</em>
</p>

---

# Argos Venture

**Argos Venture** is an experimental product branch of Argos. It turns the workspace into a ship setting out toward an unknown destination: the voyage may take an undetermined amount of time, but every decision, discovery, question, and result becomes part of a shared quest log.

This branch reinterprets the original development workspace as a **chat-adventure workspace**:

- **Admins are Captains.** They command the ship, choose its course, run the full agent toolset, and recruit shipmates.
- **Argo is the ship’s AI.** Argo is the configured default chat model acting in an agent context: it reports status, tool activity, outcomes, and the questions that need a captain’s judgment.
- **Chat sessions are Quests.** A quest is the chronological record of events and decisions for one expedition thread.
- **Regular users are Shipmates.** Captains recruit them into individual quests. Shipmates can converse inside those quests and read only the documents and gallery assets bound to quests they have joined.

> **Branch status:** `argos-venture` is the design and implementation line for this product model. The inherited `nightly` feature set remains the starting point; this branch deliberately narrows regular-user access and changes the language, navigation, and data model around quests.

## The Voyage Model

### Captains

Captains are administrators. They retain the complete workspace command surface: quests, documents, gallery assets, email, comparisons, Cookbook, signatures, API tokens, user tools, crew members, tasks, drafts, memories, deep research, account administration, and the rest of the operational toolset.

A Captain opening or creating a quest works in agent mode by default. Argo should make its actions legible in the voyage log: what it is doing, what a tool returned, what has changed, what is blocked, and what Captain input is needed next.

### Shipmates

Shipmates are regular users recruited to specific quests. They have no global workspace inventory and no access to Captain-only operational systems.

After login, a shipmate sees only:

- Search
- Chats / Quests
- Tools → Library → Documents and Gallery
- Recent Chats and Activity on the dashboard
- Settings limited to **Account**
- A dedicated **Theme** control in the sidebar, outside Settings

The dashboard has no active quest and no prompt composer. A shipmate can still converse through the full chat view after opening a quest they are a member of.

### Quests and the voyage log

Every session becomes a **Quest**. A quest begins with two participants:

1. its Captain; and
2. Argo, the ship’s AI assistant.

A Captain may recruit regular users as shipmates. The default chat surface is the chronological voyage log—not a generic chat transcript—and records the Captain’s decisions, Argo’s status messages, tool activity, results, and shipmate contributions.

## Access and Data-Boundary Contract

Argos Venture must enforce access in the backend, not only by hiding UI.

- A quest has one Captain and zero or more shipmates.
- A document or gallery asset created in a quest is bound to that quest.
- Captains retain full access to their operational workspace.
- A shipmate can read a quest-bound document or gallery asset only when they are a member of that quest.
- Shipmates may send messages only in quests they belong to.
- Direct object URLs, list endpoints, downloads, previews, and search results must all apply the same membership check.
- Existing owner-based isolation remains relevant for Captain-private data; quest membership adds a second access boundary for shared expedition artifacts.

`owner IS NULL` legacy/shared records must never accidentally become visible to shipmates. Migrations must classify them explicitly.

## Development Flow

`argos-venture` starts from `nightly` and is an intentional product fork, not a literal Git fork.

```text
nightly
  └── argos-venture
        ├── feat/venture-quest-membership
        ├── feat/venture-shipmate-shell
        ├── feat/venture-captain-agent-log
        └── feat/venture-branding
```

Use focused feature branches from `argos-venture` and merge them back through reviewed pull requests. Periodically merge `nightly` into `argos-venture`; never force-push the voyage branch.

## Runtime Profiles and Isolated Persistence

Argos Venture and the inherited Odysseus/nightly product line must never share runtime data. Switching Git branches changes code; it must not accidentally reuse another product line’s users, database, encryption key, uploads, gallery, generated images, vector stores, settings, sessions, or caches.

The macOS launcher should read a committed profile at:

```text
config/runtime-profile.env
```

The profile is versioned with the branch, so feature branches inherit the profile of the product line they extend. It is the launcher’s source of truth for product identity and safe default persistence names; it must not infer those values from `git branch` at runtime.

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

The launcher must export `ARGOS_DATA_DIR` and retain `ODYSSEUS_DATA_DIR` as a temporary compatibility alias pointing to that same profile-specific path. The default SQLite database, Chroma persistence path, runtime ports, logs, and all files derived from the application data directory must use the active profile.

A startup guard must write and validate `runtime-manifest.json`, including the runtime ID and storage slug. Startup must stop on a profile/data-root mismatch unless an explicit maintenance override is supplied. The launcher must never copy, migrate, or merge data merely because a user changed Git branches.

Local overrides are acceptable only when they remain profile-safe. Prefer changing a shared parent root such as `ARGOS_DATA_ROOT`; do not point `ARGOS_DATA_DIR` or `DATABASE_URL` at another product line’s persistence without an explicit migration process and a matching manifest.

## Quick Start on macOS

```bash
git clone https://github.com/xxxcess/argos.git
cd argos
git switch argos-venture
./start-macos.sh
```

The runtime profile selected by this branch determines the default local port and persistence location. `start-macos.sh` may still read a local `.env` for machine-specific values, but `.env` must not silently collapse isolated runtime profiles into a shared data directory.

Keep authentication enabled for all network-accessible deployments. The inherited setup, deployment, and troubleshooting notes remain in [`docs/setup.md`](docs/setup.md). Those documents and the UI are being renamed from the upstream identity as the Argos Venture implementation proceeds.

## Implementation Milestones

1. Add committed runtime profiles, macOS launcher support, profile-specific persistence roots, and profile/data-root mismatch protection.
2. Introduce quest membership, Captain ownership, and server-side authorization for quest artifacts.
3. Add Captain-defined challenges, acceptance criteria, and a published-artifact workflow for Quest-bound documents and gallery items.
4. Rename sessions to quests and model the chronological voyage log.
5. Make Captain prompts agent-first and expose Argo status/tool-result events clearly.
6. Build the reduced shipmate shell, dashboard, account-only settings, sidebar theme control, and quest chat experience.
7. Replace inherited Odysseus naming, labels, assets, metadata, and user-facing copy with Argos Venture branding.
8. Add authorization, runtime-isolation, migration, and end-to-end tests for Captains, Shipmates, shared Quests, artifacts, and direct-route access.

## Security

Argos Venture is a self-hosted workspace with powerful local tools. Keep authentication enabled, do not expose raw model or service ports publicly, and treat Captain credentials and quest-bound data as sensitive. The server must enforce every role and membership boundary even when a caller bypasses the frontend. Runtime profiles are a data-isolation boundary: do not reuse a profile’s database, auth state, encryption key, or asset directories for another product line without an explicit migration.

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
