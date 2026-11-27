---
name: fixture-skill
description: "Export fixture: café menus, quotes 'and' colons: yes"
version: 2.1.0
tags: [demo, export, "yes"]
requires:
  - requests>=2
tools:
  - file_read
  - bash_exec
status: validated
homepage: https://example.invalid/skill
metadata:
  effort_weight: 0.8
  created: 2024-01-02
  notes: |
    line one
    line two
  nested:
    list: [1, 2.5, true, null]
    empty: {}
    none: []
triggers:
  - '(?i)fixture'
---

# Fixture Skill

Use this to test exports. Ünïcode stays as written in the body.

```python
print("hi")
```
