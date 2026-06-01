---
name: nova-session-flush
enabled: true
event: stop
pattern: .*
action: warn
---

**NOVA Session Flush**

Before stopping, call `nova_session_flush` to persist the active sprint session to disk.

This ensures no in-progress sprint context is lost between conversations.

```python
nova_session_flush()
```

If no sprint session was started this conversation (you only read shards, did not run `nova_forgemaster_sprint`), you may skip this and stop normally.
