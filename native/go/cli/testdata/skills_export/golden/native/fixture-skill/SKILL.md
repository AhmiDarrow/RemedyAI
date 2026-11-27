---
author: Remedy
description: "Export fixture: caf\xE9 menus, quotes 'and' colons: yes"
metadata:
  created: 2024-01-02
  effort_weight: 0.8
  nested:
    empty: {}
    list:
    - 1
    - 2.5
    - true
    - null
    none: []
  notes: 'line one

    line two

    '
name: fixture-skill
requires:
- requests>=2
status: active
tags:
- demo
- export
- 'yes'
tools:
- file_read
- bash_exec
version: 2.1.0
---

# Fixture Skill

Use this to test exports. Ünïcode stays as written in the body.

```python
print("hi")
```