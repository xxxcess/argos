# Runtime profiles

The native macOS launcher uses a branch-owned runtime profile to keep product lines from sharing persistent state.

## Source of truth

Each product branch commits `config/runtime-profile.env`. `start-macos.sh` reads it after local `.env` settings and uses it for the product identity, default ports, and storage slug. The launcher does not inspect the current Git branch at runtime.

Local `.env` and shell variables can provide machine-specific overrides such as `ARGOS_PORT`, `ARGOS_HOST`, or `ARGOS_DATA_ROOT`. Do not point `ARGOS_DATA_DIR` at another product line’s storage unless performing an explicit migration.

## Isolated storage

By default, the launcher stores each runtime under:

```text
~/Library/Application Support/Argos/runtimes/<storage-slug>/
```

The selected directory contains the database, authentication state, encryption key, uploads, gallery assets, generated images, Chroma data, RAG data, settings, caches, and `runtime-manifest.json`.

The manifest binds the directory to its runtime ID and storage slug. The launcher stops if it detects a mismatch. It also refuses to adopt a non-empty directory without a manifest unless `ARGOS_ADOPT_RUNTIME_DATA=1` is supplied after manually verifying the data belongs to the selected runtime.

`ARGOS_ALLOW_RUNTIME_MISMATCH=1` is a maintenance-only escape hatch. It must not be used as a normal way to share data across product lines.

## Product profiles

The `nightly` product line uses the `odysseus` profile and defaults to:

```text
App:       http://127.0.0.1:7860
ChromaDB:  8100
Apfel:     11435
Storage:   ~/Library/Application Support/Argos/runtimes/odysseus/
```

Argos Venture uses the `argos-venture` profile and defaults to:

```text
App:       http://127.0.0.1:7861
ChromaDB:  8101
Apfel:     11436
Storage:   ~/Library/Application Support/Argos/runtimes/argos-venture/
```

Run the current checkout normally:

```bash
./start-macos.sh
```

The launcher exports `ARGOS_DATA_DIR` and also sets `ODYSSEUS_DATA_DIR` to the same profile-specific path as a temporary compatibility alias for existing application modules.

## Application runtime gate

Backend product behavior is selected by `ARGOS_RUNTIME_ID`, not by branch name.

- `ARGOS_RUNTIME_ID=nightly` keeps the normal Odysseus workspace behavior.
- `ARGOS_RUNTIME_ID=argos-venture` enables Venture Quest APIs, Venture capabilities, invitation-based Shipmate recruitment, Captain approval for Artifact Drafts, and Quest-local Voyage Memory.

Changing Git branches changes code only. It must not copy, migrate, merge, adopt, or reuse another runtime's database, auth state, upload directory, Chroma data, source checkpoints, memory collections, documents, email configuration, or workspace data.

## Venture memory storage

Argos Venture does not use Nightly's user/global memory model. Every Quest has an isolated Voyage Memory partition:

```text
<ARGOS_DATA_DIR>/
  chroma/
    quest-memory/
      <session_id>/
```

The logical collection name is:

```text
quest-memory:<session_id>
```

The runtime ID and Quest ID are part of the storage contract. A Venture process must never query one Quest's memory collection while serving another Quest, even if the Captain, Shipmate, email integration, or source scope is identical.
