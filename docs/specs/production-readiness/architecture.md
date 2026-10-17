# Architecture

The feature stays within the existing modular monolith.

```text
runtime settings + Alembic config
  -> schema startup policy service
       -> repository-head adapter
       -> database-current-head adapter
       -> exact-set validator
  -> FastAPI startup adapter
  -> safe log / fatal startup error
```

The policy service owns revision comparison and mode selection. `main.py` remains an adapter that supplies the engine, metadata, and legacy development migrator.
