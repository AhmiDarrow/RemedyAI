---
name: rag-chunking
version: 1.0.0
description: >
  Design document chunking and metadata for higher-quality RAG retrieval.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - llm
  - rag
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: rag-chunking
  official: true
  security_flags: []
---

# Rag Chunking

## Steps
1. Inventory document shapes.
2. Chunk on structure (headings) before blind windows.
3. Metadata: path, title, section, updated_at.
4. Define top-k + citation requirements.
5. Eval questions with expected sources.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
