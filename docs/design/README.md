# Design Patterns

Design Pattern documents (DES): repeatable, prescriptive patterns applied across the codebase to keep features consistent. One-time stack and structure choices live under [../architecture/](../architecture/) as ADRs.

| ID | Pattern | Description |
|----|---------|-------------|
| [DES-001](DES-001-unified-extension-api.md) | Unified Extension API | The core is only the main loops; every feature ships as an extension consuming services through the `AppContext` |
| [DES-002](DES-002-extension-authoring.md) | Extension Authoring Conventions | The mechanical conventions every extension follows so they look alike and pass the quality gates |
| [DES-003](DES-003-testing-conventions.md) | Testing Conventions | vitest over TS sources, mirrored test structure with AC traceability, structural fakes, real SQLite/git/process in temp dirs |
| [DES-004](DES-004-logging-conventions.md) | Logging Conventions | All components log through one pino root logger; channels own stdout; user-facing progress goes through `app.status()` |
