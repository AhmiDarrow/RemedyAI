# remedy-skills

Public **Skills Library** catalog for [RemedyAI](https://github.com/AhmiDarrow/RemedyAI).

- Skills live under `skills/<name>/` (`SKILL.md` + optional `scripts/`).
- Build and **Ed25519-sign** `catalog.json` for clients to verify.
- Remedy Desktop installs packs into `~/.remedy/skills/` **quarantined** until Trust.

## Build & sign

```bash
python scripts/build_catalog.py --skills-dir skills --output catalog.json
export REMEDY_SKILLS_SIGNING_KEY="<base64 32-byte Ed25519 seed>"
python scripts/sign_catalog.py --catalog catalog.json
```

## Submit

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [SECURITY.md](./SECURITY.md).
