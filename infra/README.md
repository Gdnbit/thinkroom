# Infrastructure Tools

Status: operator reference outside the default local v0.1 workflow.

- `backup/`: tested SQLite/upload backup, restore, and verification utilities. Installation
  and retention pruning have side effects and require explicit operator intent.
- `migration/`: SQLite-to-PostgreSQL migration and verification utilities. Production
  migration remains deferred.

Nothing in this directory is run by the normal local v0.1 startup. Do not execute backup
retention, restore, migration, or scheduler-install commands under a no-delete/no-mutation
task.
