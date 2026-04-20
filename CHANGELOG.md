# CHANGELOG

<!-- version list -->

## v1.37.1 (2026-04-20)

### Bug Fixes

- **db**: Use constant default for SQLite column migration
  ([`b9aac53`](https://github.com/asermax/tachikoma/commit/b9aac53d88358cf1663670a6a24385dcab0e9781))


## v1.37.0 (2026-04-20)

### Bug Fixes

- **boundary**: Normalize LLM-returned string "null" to None for resume_session_id
  ([`df17b80`](https://github.com/asermax/tachikoma/commit/df17b808cf939c6c10d4e0a0d6d47fe085ef38ae))

- **display**: Reverse tool usage display to chronological order
  ([`82ed7d3`](https://github.com/asermax/tachikoma/commit/82ed7d30fd6d7e76369c106913923cf3315db836))

- **git**: Handle gitlink files for rebase detection
  ([`51d16c7`](https://github.com/asermax/tachikoma/commit/51d16c72f28901ad0baca74b014e33cc740ce763))

- **git-sync**: Avoid spurious rebase abort and unnecessary agent spawn
  ([`954727b`](https://github.com/asermax/tachikoma/commit/954727b1bb622e81f54df7f7cb15fa015fe68f24))

- **post-processing**: Respect shell quoting in compound command splitting
  ([`25818bc`](https://github.com/asermax/tachikoma/commit/25818bc43c47c9314ce73a50fa3880fd656b38c5))

- **tasks**: Add rollback on enqueue failure and simplify concurrency logic
  ([`17059ab`](https://github.com/asermax/tachikoma/commit/17059ab3902339e7b4288ffa69c527e1b007cc98))

### Documentation

- Reconcile DLT-146 context summary into feature docs
  ([`6073714`](https://github.com/asermax/tachikoma/commit/60737146ae206b506d1c1274b5a90a30e68f2d35))

- **agent**: Reconcile granular pre-processing status into feature docs
  ([`ac1aff3`](https://github.com/asermax/tachikoma/commit/ac1aff320fa12d85cbec444c3ba8e587763b9abf))

- **channels**: Update feature docs for chronological tool activity ordering
  ([`69593fd`](https://github.com/asermax/tachikoma/commit/69593fdd02a6ad0b7eb09c9b52a53f5c1f62690f))

- **config**: Document per-skill configuration convention
  ([`20f8cf5`](https://github.com/asermax/tachikoma/commit/20f8cf5d82cfb304e0a845544121086c92f69d4b))

- **planning**: Add DLT-162 for display order reversal
  ([`62c43a1`](https://github.com/asermax/tachikoma/commit/62c43a1d1cbdd4924ff28b87076b9f188b2998b3))

- **planning**: Remove completed DLT-146 and DLT-162 from delta inventory
  ([`a428039`](https://github.com/asermax/tachikoma/commit/a42803933690f8c34f04d64e901c2395d97509b3))

- **planning**: Remove reconciled DLT-031 from delta inventory
  ([`adeeb95`](https://github.com/asermax/tachikoma/commit/adeeb9579586aec7ce6a15b7f9740e596922a4ce))

- **planning**: Remove reconciled DLT-157 from delta inventory
  ([`c96ad09`](https://github.com/asermax/tachikoma/commit/c96ad094c7ee93e368e30c5d065d6762b4e9b29c))

- **tasks**: Reconcile central scheduler and one-shot cleanup
  ([`9628b41`](https://github.com/asermax/tachikoma/commit/9628b415b993f4a43cf3e0c6e77a890ca362430a))

- **tasks**: Reconcile stale-cron prevention into feature docs
  ([`ca70baa`](https://github.com/asermax/tachikoma/commit/ca70baa9fa2da8af6d88c7502d6c90ae407fa577))

### Features

- **agent**: Centralize disallowed tools in AgentDefaults
  ([`f5769eb`](https://github.com/asermax/tachikoma/commit/f5769eb0eda3a3160d1c588b57fd44edf7af1571))

- **agent**: Component-driven status updates during pre-processing
  ([`a371dac`](https://github.com/asermax/tachikoma/commit/a371dac3f50b34fcd88a93636ce59c66e7ad96d2))

- **post-processing**: Surface context entries to post-processors
  ([`6feb5fc`](https://github.com/asermax/tachikoma/commit/6feb5fc39e863e5e97163dcfa4f97874696a8790))

- **skills**: Inject AGENTS.md into classifier prompt
  ([`5fd7f7d`](https://github.com/asermax/tachikoma/commit/5fd7f7d7830be7e739ce725f3a7a5123b06d1f08))

- **tasks**: Add central scheduler and migrate recurring jobs
  ([`e3c97c9`](https://github.com/asermax/tachikoma/commit/e3c97c9b86fad8d670be3168540e4c67b99c87a9))

- **tasks**: Prevent stale cron from firing on create/update
  ([`2b30981`](https://github.com/asermax/tachikoma/commit/2b309813cd18441ba49071b0917b55386b102c20))

- **workflows**: Declare required skills for workflow steps
  ([`a5c4ad3`](https://github.com/asermax/tachikoma/commit/a5c4ad3e1d024571c3bdc86e496c1ebc3f49a0cf))

### Refactoring

- **post-processing**: Extract _build_fork_options from fork helpers
  ([`c602305`](https://github.com/asermax/tachikoma/commit/c60230570a0936d474d5058db692261179d9c6a7))

- **skills**: Extract render_skill_block to shared helper
  ([`fc45d83`](https://github.com/asermax/tachikoma/commit/fc45d83162b8d63083b4feb10d4631803b76b1ce))

### Testing

- **config**: Fix disallowed tools assertions for frozenset order
  ([`712818b`](https://github.com/asermax/tachikoma/commit/712818b90ff0fe5c816eb3d027d990a90504d655))

- **config**: Update disallowed tools tests for centralized constant
  ([`5ff6702`](https://github.com/asermax/tachikoma/commit/5ff6702f8df37fe4ba84600066b975d690729d49))


## v1.36.1 (2026-04-19)

### Bug Fixes

- **boundary**: Classify short acknowledgment messages as continuations
  ([`751832e`](https://github.com/asermax/tachikoma/commit/751832eb16b3a41cea123612720fefc5745cfc3f))

### Documentation

- **boundary**: Add short-message handling to feature docs
  ([`028a401`](https://github.com/asermax/tachikoma/commit/028a40188094890068a50728a2a2b20ad107f3e5))

- **changelog**: Restore insertion flag and backfill v1.28.0-v1.36.0
  ([`bc7929b`](https://github.com/asermax/tachikoma/commit/bc7929b23657e467b9ea2ecea5084f53e64dda20))


## v1.36.0 (2026-04-19)

### Documentation

- **tasks**: Reconcile DLT-127 on-demand execution into feature docs ([`2eeb290`](https://github.com/asermax/tachikoma/commit/2eeb290b5c26f9b4821cedb272e533153cd5fc0e))

### Features

- **tasks**: Add run_task_now for immediate background task execution ([`1ac8bf2`](https://github.com/asermax/tachikoma/commit/1ac8bf22e242d8cf551a81c6de0497f431678f36))


## v1.35.0 (2026-04-19)

### Documentation

- **deltas**: Add DLT-118 design for skill dependencies ([`e4e19dd`](https://github.com/asermax/tachikoma/commit/e4e19dd539961d0b767bd9023445d374ea50ab4a))

- **deltas**: Add DLT-118 implementation plan ([`88cddab`](https://github.com/asermax/tachikoma/commit/88cddaba8b3d42949c9aaf147fee149c6f865e01))

- **deltas**: Add DLT-118 spec for skill dependencies ([`27cade4`](https://github.com/asermax/tachikoma/commit/27cade4a4c3aab819c10feaee7667d9af2f6d833))

- **deltas**: Advance DLT-118 status to design ([`cf74202`](https://github.com/asermax/tachikoma/commit/cf74202a2b7fbcff9f76b4aa0f25aa95515f0c79))

- **deltas**: Advance DLT-118 status to plan ([`e0a6c68`](https://github.com/asermax/tachikoma/commit/e0a6c684fd91bd2ca58f3bfd79f6539b9b8e150a))

- **deltas**: Advance DLT-118 to implementation and mark batches done ([`c22fae4`](https://github.com/asermax/tachikoma/commit/c22fae4780b50be81523af0bdacd3b0fa7e37ff6))

- **deltas**: Approve DLT-118 design ([`856702d`](https://github.com/asermax/tachikoma/commit/856702dd1abb6ea846f83c7218a80faed0d9ee7a))

- **deltas**: Expand DLT-118 design with problem context and rationale ([`54316d9`](https://github.com/asermax/tachikoma/commit/54316d9451e3cb6e0198c750ac5923081631627a))

- **deltas**: Mark DLT-118 plan as approved ([`1f9f212`](https://github.com/asermax/tachikoma/commit/1f9f2120d5c2cd8ad7cc5bca2edaa6a4fecbe255))

- **deltas**: Refine DLT-118 spec with memoization and workspace overrides ([`f05c883`](https://github.com/asermax/tachikoma/commit/f05c8837e33ab5239e4eb0b1cba0411650f30472))

- **planning**: Add deltas DLT-157 through DLT-161 ([`b818973`](https://github.com/asermax/tachikoma/commit/b81897330d289fa4037c2336ee9fa5a2071549c3))

- **planning**: Advance DLT-118 status to spec ([`652456c`](https://github.com/asermax/tachikoma/commit/652456ca561f74a812e5296addf5eca04a4713b2))

- **skills**: Reconcile DLT-118 skill dependencies into feature docs ([`106492a`](https://github.com/asermax/tachikoma/commit/106492a84f5fe85237ea6d420956e06e50feba72))

### Features

- **skills**: Add depends_on field and transitive dependency resolution ([`f8f255f`](https://github.com/asermax/tachikoma/commit/f8f255f5d41a959625bbe586f0d1522aab42a790))

- **skills**: Expand context provider to load transitive skill dependencies ([`49674b4`](https://github.com/asermax/tachikoma/commit/49674b445a869ab91a5f933fb277f453fc40ea24))

### Refactoring

- **skills**: Simplify dependency expansion dedup logic ([`21083d3`](https://github.com/asermax/tachikoma/commit/21083d3db2da4ba280ea3274e13c6cd3287a72e0))

### Testing

- **skills**: Strengthen depends_on warning and chain expansion assertions ([`9d4dbeb`](https://github.com/asermax/tachikoma/commit/9d4dbeb1ebf006e80189b04592c4292e688b0668))


## v1.34.0 (2026-04-19)

### Bug Fixes

- **tasks**: Exclude respond_to_task from background task sessions ([`dd49c8c`](https://github.com/asermax/tachikoma/commit/dd49c8cda739df481210102dc4485ff063106acc))


## v1.33.0 (2026-04-19)

### Features

- **tasks**: Expose task-tools MCP to background task agent ([`481b26a`](https://github.com/asermax/tachikoma/commit/481b26a17b8b69c090a8dee625b6a2f0dd1120d6))


## v1.32.0 (2026-04-19)


## v1.31.0 (2026-04-19)

### Features

- **post-processing**: Add sort to utility bash command whitelist ([`281aa0e`](https://github.com/asermax/tachikoma/commit/281aa0edde4d4dcf836db98a328ab7ed6ee5e49e))


## v1.30.3 (2026-04-19)

### Bug Fixes

- **database**: Restore DB from dump on startup when DB is missing ([`2d9a67f`](https://github.com/asermax/tachikoma/commit/2d9a67f9355f62ef1eeec7dd86d6f2e8fc15ec56))

### Documentation

- Update feature docs and ADR-012 with missing-DB restore behavior ([`962dc2e`](https://github.com/asermax/tachikoma/commit/962dc2e5770aa42a1aee50418995696b31ef1718))


## v1.30.2 (2026-04-19)

### Bug Fixes

- **git**: Replace sqlite-diffable CLI with inline stdlib implementation ([`537f1de`](https://github.com/asermax/tachikoma/commit/537f1de866d1fc8b9a04e3c875e16a32aac5e583))


## v1.30.1 (2026-04-19)

### Bug Fixes

- **git**: Exclude sqlite_sequence from db dump ([`2f52cc7`](https://github.com/asermax/tachikoma/commit/2f52cc798d9a44b5f6d0b895c8ef4600117fffcf))

- **git**: Resolve sqlite-diffable binary from tool venv ([`61cc2e5`](https://github.com/asermax/tachikoma/commit/61cc2e54177069e61b451cca8bdaeab582aa0185))

### Documentation

- **git**: Explain db-dump folder in commit agent prompt ([`72ceae3`](https://github.com/asermax/tachikoma/commit/72ceae3b8b1ebaac4216ac4221d0cd3f92c8c576))


## v1.30.0 (2026-04-19)

### Documentation

- **channels**: Update telegram feature docs for newest-first ordering ([`093af36`](https://github.com/asermax/tachikoma/commit/093af36b7172fc02bb5804797c9a421c8e46a239))

- **planning**: Broaden DLT-150 marker emission approach ([`d477294`](https://github.com/asermax/tachikoma/commit/d47729444ba0e9ed00d7f3b92deec6c047a1728c))

### Features

- **display**: Sort tool usage summary newest-first ([`47b2479`](https://github.com/asermax/tachikoma/commit/47b24790794f87d383ceaa8d7ae17b8359bdc456))


## v1.29.0 (2026-04-19)

### Code Style

- Apply ruff formatting fixes ([`2e90725`](https://github.com/asermax/tachikoma/commit/2e907252fa118181c45dc01151c08e803dd3f48a))

### Documentation

- Update feature documentation for git tools and LFS ([`dbe3cd5`](https://github.com/asermax/tachikoma/commit/dbe3cd576e221930b67d54efdb5b0f6aa2097dba))

- **adr**: Add ADR-012 for Git LFS on workspace binaries ([`2bdaf73`](https://github.com/asermax/tachikoma/commit/2bdaf73621bf729cd59688b666e0530fe2fc728a))

- **adr**: Replace ADR-012 with sqlite-diffable decision ([`ea290c5`](https://github.com/asermax/tachikoma/commit/ea290c59f74cb7d12595136d4f664609bcf26c3b))

- **deltas**: Add git guardrails and secrets store deltas ([`f3e3945`](https://github.com/asermax/tachikoma/commit/f3e3945dc62b773054ec19a6f5643884a34671b2))

### Features

- **git**: Add push/sync MCP tools and destructive-git deny hook ([`01f8f1e`](https://github.com/asermax/tachikoma/commit/01f8f1ed10a0850431c491daf75fa9f685b2af1b))

- **git**: Configure LFS for workspace database on bootstrap ([`c153d74`](https://github.com/asermax/tachikoma/commit/c153d7483be4aaa808ba235230691a3b6b9791cd))

- **git**: Replace LFS with sqlite-diffable for DB version tracking ([`669852f`](https://github.com/asermax/tachikoma/commit/669852f57bc01abf033402812b44bf9932d6cebf))

### Testing

- **git**: Update tests for sqlite-diffable DB tracking ([`c2dce10`](https://github.com/asermax/tachikoma/commit/c2dce1036ef2d79b2535a0a7f60c75b8957dde80))


## v1.28.0 (2026-04-18)

### Documentation

- **changelog**: Backfill changelogs for v1.25.0-v1.27.0 ([`565e0b6`](https://github.com/asermax/tachikoma/commit/565e0b631c7e008ae9b051247c9793c1ef205227))

- **planning**: Update DELTAS.md ([`86e1c48`](https://github.com/asermax/tachikoma/commit/86e1c4818a03c7d18a964496402823415cf0b703))


## v1.27.0 (2026-04-18)

### Features

- **telegram**: Allow send_file to accept paths outside workspace
  ([`e0d9609`](https://github.com/asermax/tachikoma/commit/e0d9609d07b51d49cc1e3e58a3fde1b91e58170b))

- **config**: Add SendFileSettings model with extra_roots
  ([`300b657`](https://github.com/asermax/tachikoma/commit/300b657c83e1b8d59b3f0e06a988e832a4b4f3c0))


### Refactoring

- **config**: Derive send_file extra_roots comment from field description
  ([`6add741`](https://github.com/asermax/tachikoma/commit/6add7417b9f3ec0b1a6e55a6f8e2b6832c2fde76))


### Documentation

- **telegram**: Reconcile DLT-140 into feature documentation
  ([`42b7fc0`](https://github.com/asermax/tachikoma/commit/42b7fc0c94b4e8d7dbd3d5e9fbdf8b40c49b10fe))

- Update SKILL.md and planning for send_file path expansion
  ([`fc31f67`](https://github.com/asermax/tachikoma/commit/fc31f67e970bd9c020d9da40aac5f2f38c233f21))

- **planning**: Mark DLT-140 files changed as complete
  ([`7aa3252`](https://github.com/asermax/tachikoma/commit/7aa32523b10ce23bb6b8f4a86fe02f66cc60fcb1))

- **planning**: Mark DLT-140 plan as complete
  ([`f766b7e`](https://github.com/asermax/tachikoma/commit/f766b7e0e1f3f51c880a28d8af92e3a86fe13d5e))

- **planning**: Add DLT-140 delta plan
  ([`522bc5f`](https://github.com/asermax/tachikoma/commit/522bc5f85ef3cf5f75f9939df1d76c2f1bcad6a8))

- **planning**: Mark DLT-140 as in plan
  ([`d08f5b0`](https://github.com/asermax/tachikoma/commit/d08f5b0da077ad3756ce63c1ec8cf1a19d4616ae))

- **planning**: Expand DLT-140 delta design
  ([`634a3fd`](https://github.com/asermax/tachikoma/commit/634a3fdfcbf5f8081bb00aa395e7ed79d2f68c8a))

- **planning**: Mark DLT-140 as in design
  ([`5059274`](https://github.com/asermax/tachikoma/commit/5059274597ad8efb481f9cdca1a983dcd630c9f9))

- **planning**: Add DLT-140 delta design
  ([`6688f42`](https://github.com/asermax/tachikoma/commit/6688f4215416798368b4fb1fdfd4020e2d758138))

- **planning**: Add DLT-140 delta spec
  ([`b7e4cc2`](https://github.com/asermax/tachikoma/commit/b7e4cc276a1f44e1ff036e17449c02ee62d3acb1))

- **planning**: Mark DLT-140 as in spec
  ([`9712559`](https://github.com/asermax/tachikoma/commit/97125598808528720e89ca96b2fa09840cb9392b))


### Code Style

- Apply ruff formatting across src and tests
  ([`7b98df8`](https://github.com/asermax/tachikoma/commit/7b98df8c8f60faf42bd5c9ebabc5de3834197d98))


### Testing

- **telegram**: Tighten extra_root acceptance test to exclude workspace
  ([`ec358fb`](https://github.com/asermax/tachikoma/commit/ec358fbcb8465bedbec14baca5650c30cf61dad8))


## v1.26.0 (2026-04-18)

### Features

- **channels**: Flush buffer digest during channel shutdown
  ([`7119058`](https://github.com/asermax/tachikoma/commit/711905891a5a1d396983e4b7f4b3e3bb7f6c0ce3))

- **coordinator**: Emit CoordinatorIdle on busy-to-idle transitions
  ([`124dd99`](https://github.com/asermax/tachikoma/commit/124dd99b2f11e7e3f5f4e0cc3b8e4b3b5c0b0f7e))

- **notifications**: Propagate priority through notification and executor systems
  ([`e9bbea1`](https://github.com/asermax/tachikoma/commit/e9bbea1f4a5e3b5c3f3e0cc3b8e4b3b5c0b0f7e1))

- **buffer**: Add priority buffer subsystem for deferred notification delivery
  ([`fe1ce70`](https://github.com/asermax/tachikoma/commit/fe1ce70a5e3b5c3f3e0cc3b8e4b3b5c0b0f7e2c))


### Bug Fixes

- **channels**: Await coordinator interrupt on forced shutdown
  ([`d16d6dc`](https://github.com/asermax/tachikoma/commit/d16d6dcc20f1fcedef059a1203152d86df34c116))


### Refactoring

- **buffer**: Tighten BufferedItem factory type hints
  ([`b90ce39`](https://github.com/asermax/tachikoma/commit/b90ce39a5e3b5c3f3e0cc3b8e4b3b5c0b0f7e3))

- **coordinator**: Make is_busy a public property
  ([`feca1f3`](https://github.com/asermax/tachikoma/commit/feca1f3a5e3b5c3f3e0cc3b8e4b3b5c0b0f7e4))

- **tasks**: Route scheduler and channels through priority buffer
  ([`748e749`](https://github.com/asermax/tachikoma/commit/748e749a5e3b5c3f3e0cc3b8e4b3b5c0b0f7e5))


### Documentation

- Reconcile DLT-112 into feature documentation
  ([`c3cc9b6`](https://github.com/asermax/tachikoma/commit/c3cc9b621ea53f737ff083bb8120f5314eeb0f06))

- **planning**: Advance DLT-112 to Implementation
  ([`8cba6fe`](https://github.com/asermax/tachikoma/commit/8cba6febd74768289a42a04dc73938561b8f877a))

- **planning**: Add DLT-112 implementation plan
  ([`2751445`](https://github.com/asermax/tachikoma/commit/27514455235a3036cbfed8f7a011ac9843860e77))

- **planning**: Advance DLT-112 status to Plan
  ([`9b0c1bb`](https://github.com/asermax/tachikoma/commit/9b0c1bb9fd6423c40add9cfc4e2b7ac4d1218b54))

- **planning**: Mark DLT-112 design as complete
  ([`97910e9`](https://github.com/asermax/tachikoma/commit/97910e9b2653c022c73cc014577a3745f7215ac4))

- **planning**: Expand DLT-112 design with buffer and shutdown rationale
  ([`1075245`](https://github.com/asermax/tachikoma/commit/1075245c8edf1498cb9cd83c44fa855af4325ffd))

- **planning**: Refine DLT-112 spec with event-driven buffer and shutdown flush
  ([`7b664e4`](https://github.com/asermax/tachikoma/commit/7b664e4e22a0b1a47a96ae8fd5a723cec03f47ad))

- **planning**: Update DLT-112 status to Design
  ([`4bf3e51`](https://github.com/asermax/tachikoma/commit/4bf3e5103092d1c7cc77fb18adc9af033fd2e3bea00e91f))

- **planning**: Add spec and design for DLT-112
  ([`03092d1`](https://github.com/asermax/tachikoma/commit/03092d1c7cc77fb18adc9af033fd2e3bea00e91f))

- **planning**: Update DLT-112 status to Spec
  ([`2ea1e0b`](https://github.com/asermax/tachikoma/commit/2ea1e0b7b664e4e22a0b1a47a96ae8fd5a723cec03f47ad))


### Code Style

- Auto-format test files
  ([`734cdc8`](https://github.com/asermax/tachikoma/commit/734cdc81064efabf40a7e928aed0e1bacbd66a82))


### Chores

- **tasks**: Remove empty event placeholder files
  ([`0e803b4`](https://github.com/asermax/tachikoma/commit/0e803b4b4a3d17842fb87b0205f92b991b119edf))


## v1.25.0 (2026-04-18)

### Features

- **memory**: Add transcript archive processor
  ([`26596e4`](https://github.com/asermax/tachikoma/commit/26596e4f7d2c0b3e1a8c4d5e6f7a8b9c0d1e2f3a))

- **memory**: Bootstrap transcripts directory
  ([`3848bfd`](https://github.com/asermax/tachikoma/commit/3848bfda1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e))


### Documentation

- Reconcile DLT-099 into feature documentation
  ([`a79e2ba`](https://github.com/asermax/tachikoma/commit/a79e2ba275ebac77dada9880f8ff1644d4ec71a9))

- **planning**: Mark DLT-099 implementation complete
  ([`90eefdd`](https://github.com/asermax/tachikoma/commit/90eefdd0e7f53e5c9f88fdaa3a03f9f7a668a54c))

- **planning**: Mark DLT-099 plan complete
  ([`2a5ab16`](https://github.com/asermax/tachikoma/commit/2a5ab16ffdffc22599b501d627f11bfc9e0d5acc))

- **planning**: Add implementation plan for DLT-099
  ([`47a96ae`](https://github.com/asermax/tachikoma/commit/47a96ae8fd5a723cec03f47ad6594495c44db145))

- **planning**: Update DLT-099 status to plan
  ([`e22a0b1`](https://github.com/asermax/tachikoma/commit/e22a0b1a47a96ae8fd5a723cec03f47ad6594495))

- **planning**: Refine DLT-099 design and defer crash recovery
  ([`9fa19f3`](https://github.com/asermax/tachikoma/commit/9fa19f3c179f89ae967bcb3feffd63b9a0b036cafd1))

- **planning**: Update DLT-099 status to design
  ([`c179f89`](https://github.com/asermax/tachikoma/commit/c179f89ae967bcb3feffd63b9a0b036cafd1929de0))

- **planning**: Update DLT-099 status and transcript path
  ([`ae967bc`](https://github.com/asermax/tachikoma/commit/ae967bcb3feffd63b9a0b036cafd1929de0b3feffd))

- **planning**: Add design for DLT-099 transcript archiving
  ([`b3feffd`](https://github.com/asermax/tachikoma/commit/b3feffdf63b9b032a58ac639a2707c01894aa97b))

- **planning**: Add spec for DLT-099 transcript archiving
  ([`1929de0`](https://github.com/asermax/tachikoma/commit/1929de04e1721a5993aa68a52839f0afce08dfc9))

- **planning**: Update DLT-099 status to spec
  ([`36ab8b1`](https://github.com/asermax/tachikoma/commit/36ab8b17b664e4e22a0b1a47a96ae8fd5a723cec03f))


## v1.24.0 (2026-04-18)

### Features

- **skills**: Add scripting and testing references to authoring guide
  ([`aa9477c`](https://github.com/asermax/tachikoma/commit/aa9477c33d13d9cad2ea72a9fd7515da67b7b157))


### Documentation

- **planning**: Add deltas from shin-sekai review and refocus DLT-048
  ([`7095d91`](https://github.com/asermax/tachikoma/commit/7095d9150bd6548c467fd0ccb9b0d3c26009b960))

- **planning**: Remove reconciled DLT-124 from inventory
  ([`888b216`](https://github.com/asermax/tachikoma/commit/888b216c84d766c559805152ad65c82f405b6316))


## v1.23.0 (2026-04-16)

### Features

- **post-processing**: Add grep to utility bash prefixes for sub-agents
  ([`27fa383`](https://github.com/asermax/tachikoma/commit/27fa383ebc3497a1de58a339b36cedfab9aa2271))


## v1.22.1 (2026-04-16)

### Bug Fixes

- **display**: Correct double 'and' in truncated tool activity summary
  ([`2b277da`](https://github.com/asermax/tachikoma/commit/2b277da0c222f1ba9b9443ca3671e78b47f6bdfb))

- **tasks**: Strengthen evaluator guardrails and rename feedback to rationale
  ([`4eb32c8`](https://github.com/asermax/tachikoma/commit/4eb32c801872874123d26dfe191c5871a71def4a))

### Documentation

- **agent**: Update feature docs and DES-004 for role-based sub-agent models
  ([`74bf630`](https://github.com/asermax/tachikoma/commit/74bf6301a529a13fb798b82700c31fd619966f94))

- **planning**: Triage improvements note and reprioritize deltas
  ([`18dfb02`](https://github.com/asermax/tachikoma/commit/18dfb02dbeeacc8051d452c991df81b2bca58124))

### Refactoring

- **agent**: Split sub-agent model setting into role-based settings
  ([`e5e8c5e`](https://github.com/asermax/tachikoma/commit/e5e8c5e0c6f4bf1bbc168a292d8dd9f7f98b1bc1))

### Testing

- Enable three skipped tests via time-machine and mock side_effects
  ([`47df7e0`](https://github.com/asermax/tachikoma/commit/47df7e017a78e0657cd54892f7b1dc4073b7d1cb))


## v1.22.0 (2026-04-15)

### Bug Fixes

- **sdk**: Catch all SDK exceptions in stderr_aware_query
  ([`d634b65`](https://github.com/asermax/tachikoma/commit/d634b653bddbb991962223275bd7db162e1a1170))

- **tasks**: Reframe evaluator prompt to judge completion, not output quality
  ([`d59f0e8`](https://github.com/asermax/tachikoma/commit/d59f0e87455d7661a97920c5da4bc7513e874e61))

### Documentation

- Remove DLT-143 collaborative document review feature
  ([`20395a4`](https://github.com/asermax/tachikoma/commit/20395a4b6adcb36f8bd40147daf31a2b1c40870c))

- Update feature specs for tightened extraction prompts
  ([`9f9d01a`](https://github.com/asermax/tachikoma/commit/9f9d01a63c967b0b027a6c25d708f29b05cf7ce5))

- **planning**: Reprioritize QoL deltas for next work round
  ([`02e97df`](https://github.com/asermax/tachikoma/commit/02e97df67811cd379c4d504f3ab8607802dc2b0f))

- **processors**: Update feature docs for utility Bash tool addition
  ([`01b1f3d`](https://github.com/asermax/tachikoma/commit/01b1f3dfaea79cd52278e5c86bb23396c1338fe8))

- **tasks**: Add DLT-144 and update DLT-139 with notification duplication context
  ([`7ab2877`](https://github.com/asermax/tachikoma/commit/7ab2877fdcde084e2278c303f157573e90eeb907))

- **tasks**: Update feature docs for evaluator completion-signal redesign
  ([`96834c9`](https://github.com/asermax/tachikoma/commit/96834c966116ca3b087fb9940fac1a0cf82696d9))

### Features

- **processors**: Add utility Bash tools to memory and context processors
  ([`6b277e1`](https://github.com/asermax/tachikoma/commit/6b277e104e52cc7e6784c53fdb4cfbf5ebde7040))

### Refactoring

- **context**: Use haiku model for CoreContextProcessor
  ([`869c93e`](https://github.com/asermax/tachikoma/commit/869c93ea80b0ff267f872845e2bcf3944762345a))

- **prompts**: Tighten memory extraction and context update prompts
  ([`85d43cb`](https://github.com/asermax/tachikoma/commit/85d43cb0ab0ffeaaec2009cf74d57dc54667c031))


## v1.21.0 (2026-04-14)

### Documentation

- Add DLT-141 delta for pausing background tasks on user activity
  ([`7b2fa84`](https://github.com/asermax/tachikoma/commit/7b2fa84b6e194b49d7e7e8c2496a61139fa35cac))

- Add DLT-142 and DLT-143 deltas for session filtering and document review
  ([`c00a952`](https://github.com/asermax/tachikoma/commit/c00a95207f48b83e3ef8f6bb2a68cfa5863af5bc))

- **sessions**: Update feature documentation for last_exchange filtering
  ([`4baffda`](https://github.com/asermax/tachikoma/commit/4baffdab130aa8040fcd9ca9183eb69788ba5813))

### Features

- **sessions**: Filter last_exchange to final text response only
  ([`4a2ccd1`](https://github.com/asermax/tachikoma/commit/4a2ccd1d2084e6edbadc9ff621717f178df769a3))

### Refactoring

- **git**: Deduplicate git helpers and agent defaults construction
  ([`24e008b`](https://github.com/asermax/tachikoma/commit/24e008b490f8237316e0271aac38b7732a1798c4))


## v1.20.0 (2026-04-14)

### Refactoring

- **git**: Use stderr_aware_query in sync module for consistent error logging
  ([`dd13e30`](https://github.com/asermax/tachikoma/commit/dd13e300a3d490d1382de7c84e0c8b48e6271de6))


## v1.19.0 (2026-04-14)

### Documentation

- **deltas**: Add DLT-136 through DLT-140 from improvements triage
  ([`0bad4e0`](https://github.com/asermax/tachikoma/commit/0bad4e06bd4a5214203e99053df615f4c20718ac))


## v1.18.0 (2026-04-13)

### Chores

- Remove completed DLT-096 delta artifacts
  ([`b4fdcd2`](https://github.com/asermax/tachikoma/commit/b4fdcd27b494265bc8c03c32c6f29b51f9573050))

- Remove delta ID reference from migration comment
  ([`d06a2c3`](https://github.com/asermax/tachikoma/commit/d06a2c3dc39877d214b9ceca7c89beaa064fb5c9))

### Documentation

- Reconcile DLT-096 into feature design documentation
  ([`067b76c`](https://github.com/asermax/tachikoma/commit/067b76cf65d91ddc4a0d6e50410445ce8cc2ac6d))

- Update DLT-096 status to completed design
  ([`15c9258`](https://github.com/asermax/tachikoma/commit/15c9258ef5e4e4c1aca48e9a4f1d092b6b08d928))

- Update DLT-096 status to completed plan
  ([`c537a58`](https://github.com/asermax/tachikoma/commit/c537a583300c3f6f519a1886c2d90ce2ea2fa47e))

- Update DLT-096 status to design
  ([`0cebc88`](https://github.com/asermax/tachikoma/commit/0cebc88bdb2d86d2dcd54ef787d422d9c221a50c))

- Update DLT-096 status to implementation
  ([`399d864`](https://github.com/asermax/tachikoma/commit/399d8647553acac5c7674953bfc0030993597a8a))

- Update DLT-096 status to plan
  ([`15135a9`](https://github.com/asermax/tachikoma/commit/15135a9735ea6d6e33f53dca562ace984be757d2))

- Update DLT-096 status to spec
  ([`589fe64`](https://github.com/asermax/tachikoma/commit/589fe6431c8f28d13000b51c8b1ad0555ec8f32c))

- **sessions**: Add delta spec for last exchange in session resumption
  ([`a769e7c`](https://github.com/asermax/tachikoma/commit/a769e7c5e969da3e13d1a57c8ddc68062421ebe4))

- **sessions**: Add design for last exchange in session resumption
  ([`47435e8`](https://github.com/asermax/tachikoma/commit/47435e8cea89a753dae55d674df96123cdef13e1))

- **sessions**: Add implementation plan for last exchange in session resumption
  ([`4adb454`](https://github.com/asermax/tachikoma/commit/4adb4548de8c383d64aec875f2d29b66dae55f57))

- **sessions**: Add last_exchange field and processor to session and boundary detection docs
  ([`5b6a9ec`](https://github.com/asermax/tachikoma/commit/5b6a9ec0cbafeec67520648413b7884e448bbf00))

- **sessions**: Complete design for last exchange in session resumption
  ([`bf6cbf3`](https://github.com/asermax/tachikoma/commit/bf6cbf3c1feb9427c62835ad533bff4f98677c50))

### Features

- **boundary**: Add LastExchangeProcessor for persisting last assistant response
  ([`d926867`](https://github.com/asermax/tachikoma/commit/d926867747d54ca306b431ed00e147c855e52607))

- **boundary**: Enrich boundary detection with last exchange context
  ([`376e142`](https://github.com/asermax/tachikoma/commit/376e142c1f05d6d009c47c78c0324ee9185ca457))

- **git**: Add compound command splitting and cd/pwd to bash gate hook
  ([`7091e22`](https://github.com/asermax/tachikoma/commit/7091e222189ce8426fb3d5816302129af18528fd))

- **sessions**: Add last_exchange field to session model and storage
  ([`c9249c6`](https://github.com/asermax/tachikoma/commit/c9249c6fe9862706616c034962fdad762cd5f10b))

### Refactoring

- **coordinator**: Extract session-to-candidate conversion helper
  ([`e7fd640`](https://github.com/asermax/tachikoma/commit/e7fd6409c11988a5efa6551a11ac5ea98d198cbe))

- **sessions**: Avoid redundant DB query on last_exchange update
  ([`fad353f`](https://github.com/asermax/tachikoma/commit/fad353fe1e1432e44922a7430bbfe8d95e86a9e3))

### Testing

- **boundary**: Add tests for last exchange in session resumption
  ([`5c7b274`](https://github.com/asermax/tachikoma/commit/5c7b2748a20623f5ee8873a3487b2778edcd13de))


## v1.17.0 (2026-04-13)

### Bug Fixes

- **tests**: Update mock targets to stderr_aware_query after merge
  ([`5e48ad6`](https://github.com/asermax/tachikoma/commit/5e48ad692d19c245aa9a684f530a4877b3839278))


## v1.16.0 (2026-04-13)

### Bug Fixes

- **memory**: Remove max_turns from memory search agent
  ([`3966170`](https://github.com/asermax/tachikoma/commit/396617077b72c1b09fcb9e11b9e3dbe93d4c6188))

- **telegram**: Silence message-is-not-modified BadRequest on status edits
  ([`ff2879e`](https://github.com/asermax/tachikoma/commit/ff2879e4520fc326c636c25eadecb1d1674f1c32))

### Documentation

- Reconcile error-diagnosis patch into feature documentation
  ([`16a85ea`](https://github.com/asermax/tachikoma/commit/16a85ea5429cbc91165524848ebd6ecc2341b3b8))

### Features

- **git**: Expand commit-agent bash gate with read-only inspection commands
  ([`4a48490`](https://github.com/asermax/tachikoma/commit/4a4849017801e02a0c06b55e7485fffe61ad4d96))

- **post-processing**: Thread optional model kwarg through fork helpers
  ([`fae8f3e`](https://github.com/asermax/tachikoma/commit/fae8f3e6278b92f76b8602fe2c1ec3d7a7cdd208))


## v1.15.0 (2026-04-13)

### Bug Fixes

- **prompts**: Use absolute workspace paths in agent prompts
  ([`06e15fc`](https://github.com/asermax/tachikoma/commit/06e15fcd6921aeadd5a3e1a4a963ace4b1a3b25a))

### Chores

- **deltas**: Add DLT-135, DLT-136, DLT-137
  ([`f93143b`](https://github.com/asermax/tachikoma/commit/f93143b6cf20eca077b236b42425ee9afa80ae75))

### Documentation

- Reconcile DLT-136 workspace path fix
  ([`4d31336`](https://github.com/asermax/tachikoma/commit/4d3133684b3e459e5b2398e93d46ac78179f0959))

- **permissions**: Reconcile DLT-137 into feature documentation
  ([`57ce388`](https://github.com/asermax/tachikoma/commit/57ce388e17b585f5c67cea56ec63275b526c8a4c))

### Features

- **permissions**: Add sub-agent permission scoping via dontAsk mode
  ([`f957dbe`](https://github.com/asermax/tachikoma/commit/f957dbe58e5a46d62e793822f3f5ae4a0b9d861c))


## v1.14.3 (2026-04-12)

### Bug Fixes

- **memory**: Increase search agent max_turns from 8 to 12
  ([`a25be89`](https://github.com/asermax/tachikoma/commit/a25be892c37fadff0e770bcd54c8da22df1bdb45))

- **sdk**: Include actual exception message in error logs
  ([`8f42095`](https://github.com/asermax/tachikoma/commit/8f42095a6312354e43c3029cd2712d7a741bbca6))

- **tasks**: Fix UTC handling in task CRUD operations
  ([`ddec1ad`](https://github.com/asermax/tachikoma/commit/ddec1add9083697a88a89ada5719e76418c2327a))

### Chores

- **deltas**: Add DLT-123 through DLT-133 from improvements triage
  ([`28e86b4`](https://github.com/asermax/tachikoma/commit/28e86b4035b4c1d28cd7aa06f2ece8dcd8a222ef))

- **deltas**: Approve DLT-098 design
  ([`d9a7286`](https://github.com/asermax/tachikoma/commit/d9a7286b7febab921586f450740aa4cde0e1e15d))

- **deltas**: Escalate DLT-097, DLT-131, DLT-132 to Critical priority
  ([`e203a12`](https://github.com/asermax/tachikoma/commit/e203a12c3cc390fe22492b566d53bbdd475add13))

- **deltas**: Mark DLT-098 design as complete
  ([`d3415a4`](https://github.com/asermax/tachikoma/commit/d3415a46907dfa089a5bac9b2667c95dee236304))

- **deltas**: Mark DLT-098 plan as in progress
  ([`f602125`](https://github.com/asermax/tachikoma/commit/f602125b71d34419fa23ac46ef9c44542f0d5b34))

- **deltas**: Mark DLT-098 spec as complete
  ([`2195411`](https://github.com/asermax/tachikoma/commit/2195411aa35e23d4e3433335f7857b1a564f6b67))

- **deltas**: Remove DLT-131 after reconciliation
  ([`8e57d8a`](https://github.com/asermax/tachikoma/commit/8e57d8a685e5587b2f010ae87dd4980cc2259650))

- **deltas**: Remove DLT-132 after reconciliation
  ([`3caf19e`](https://github.com/asermax/tachikoma/commit/3caf19e842afcc5fafbd2d22a2e9fb5aaf98fce7))

- **deltas**: Reorder DLT-134 dependencies alphabetically
  ([`d6ed2a3`](https://github.com/asermax/tachikoma/commit/d6ed2a3fc9941a6c0e7d7568d9ad4fe38e255e1a))

- **deltas**: Update DLT-098 status to Spec
  ([`697fcd3`](https://github.com/asermax/tachikoma/commit/697fcd356ce12cc68265bc9fdfe712183ff4678e))

### Documentation

- Mark DLT-098 implementation complete
  ([`aebcc70`](https://github.com/asermax/tachikoma/commit/aebcc70772c17d848b981332775db6f42163902a))

- Reconcile DLT-098 stderr capture into feature documentation
  ([`99c07a8`](https://github.com/asermax/tachikoma/commit/99c07a83c75f5bd456527ccef37e050748ef728e))

- Remove DLT-098 delta working files after reconciliation
  ([`da53483`](https://github.com/asermax/tachikoma/commit/da53483fb868cc16181e939764410c6bc190c599))

- **design**: Add design for DLT-098 SDK stderr capture
  ([`e30a3c0`](https://github.com/asermax/tachikoma/commit/e30a3c0b1bb8f4efebb1e340b2c9da9f6f34d4d0))

- **design**: Expand DLT-098 design with full problem context and shape details
  ([`e832425`](https://github.com/asermax/tachikoma/commit/e83242511a629117da7565d872e250cc53b100be))

- **memory**: Reconcile max_turns increase into feature design
  ([`253462a`](https://github.com/asermax/tachikoma/commit/253462a8d59975acb855d680a608bcbfc898e998))

- **plan**: Add DLT-098 implementation plan
  ([`f482263`](https://github.com/asermax/tachikoma/commit/f482263faf2577cfafa43e7d373a4334e9ba104f))

- **spec**: Add specification for DLT-098 SDK stderr capture
  ([`b7a4cdf`](https://github.com/asermax/tachikoma/commit/b7a4cdffa8a708138e44b17119fd5fca10858c57))

- **tasks**: Reconcile UTC handling fixes into feature documentation
  ([`0605736`](https://github.com/asermax/tachikoma/commit/0605736e5172fead1ee26ce9a63450da99b4d7c3))

### Features

- Integrate stderr capture across all SDK query consumers
  ([`e7432ba`](https://github.com/asermax/tachikoma/commit/e7432ba51806aae9a53b5160c5c966f017eda7f5))

- **sdk**: Add StderrAccumulator and stderr-aware query wrapper
  ([`f6cd3e5`](https://github.com/asermax/tachikoma/commit/f6cd3e5b3591232e9e817d9245967dd0fe93c73e))


## v1.14.2 (2026-04-09)

### Bug Fixes

- **memory**: Harden memory search agent with explicit tools and scope guardrails
  ([`2e2c091`](https://github.com/asermax/tachikoma/commit/2e2c091f784941a7b7abdbf39dfa55f178338bc2))

### Chores

- **deltas**: Add DLT-123 through DLT-133 from improvements triage
  ([`cf314c8`](https://github.com/asermax/tachikoma/commit/cf314c87a394c980e36da7292bf54ca6567680cd))

- **deltas**: Escalate DLT-097, DLT-131, DLT-132 to Critical priority
  ([`219a81c`](https://github.com/asermax/tachikoma/commit/219a81cc55c99e260eb28d6bffb45239dc8ece96))

### Documentation

- Reconcile DLT-097 into feature specs and designs
  ([`8786158`](https://github.com/asermax/tachikoma/commit/878615824f01ec55e1e6907ed9ad3e1e1b052209))

- **deltas**: Add implementation plan for DLT-097 git sync
  ([`35299cc`](https://github.com/asermax/tachikoma/commit/35299ccf09a9dddeca5c5ce10ab617a595ad603a))

- **deltas**: Add spec and design for DLT-097 git sync
  ([`d8eed90`](https://github.com/asermax/tachikoma/commit/d8eed90a736c1bc8b7294a1ee61676420f03d92e))

- **deltas**: Complete design for DLT-097 git sync
  ([`3ff35ea`](https://github.com/asermax/tachikoma/commit/3ff35ea7517ce2ba5effba5d56a81ec8e155342d))

- **deltas**: Update DLT-097 status to Design
  ([`2df2766`](https://github.com/asermax/tachikoma/commit/2df2766502031c482f1cada5d3fba509cf028bc4))

- **deltas**: Update DLT-097 status to Design
  ([`4c7e26d`](https://github.com/asermax/tachikoma/commit/4c7e26dae76839b9819ccc47f05b57a74d7dbb96))

- **deltas**: Update DLT-097 status to implementation complete
  ([`899ec4a`](https://github.com/asermax/tachikoma/commit/899ec4a3a6dadf62172bde0db7973366a635ce98))

- **deltas**: Update DLT-097 status to Plan
  ([`c0ecece`](https://github.com/asermax/tachikoma/commit/c0ecece20380117380b78d2efa3652883e178987))

- **deltas**: Update DLT-097 status to Plan complete
  ([`45aa378`](https://github.com/asermax/tachikoma/commit/45aa378e326c261a2ef9e640c0634d7c9bdeb689))

- **deltas**: Update DLT-097 status to Spec
  ([`2cff586`](https://github.com/asermax/tachikoma/commit/2cff586d84fda57dadf8cb112b291aa25edfbf52))

### Features

- **git**: Add shared sync utilities for divergence detection and smart push/pull
  ([`55b4d64`](https://github.com/asermax/tachikoma/commit/55b4d64e8697f942639fd4ab5d9b1d45af4277af))

- **git**: Integrate sync utilities into workspace hooks and processor
  ([`25eaae1`](https://github.com/asermax/tachikoma/commit/25eaae1f7e9d59e00c60a4356325b61c1adecbe3))

- **projects**: Replace bare push/pull with smart sync in submodule handling
  ([`de672a4`](https://github.com/asermax/tachikoma/commit/de672a4b093d98058e220b5d062fe964e8639e32))

### Refactoring

- **git**: Consolidate push success checks and git command helpers
  ([`2bb0061`](https://github.com/asermax/tachikoma/commit/2bb0061e281beef19d8e279c4d2f8ec380daee41))


## v1.14.1 (2026-04-09)

### Bug Fixes

- **telegram**: Edit existing status message on consecutive Status events
  ([`92889fc`](https://github.com/asermax/tachikoma/commit/92889fc452389183b2b73c14682490bf16baa4d0))

### Chores

- **deltas**: Add DLT-122 evaluate alternatives to Claude Agent SDK
  ([`bdb05f8`](https://github.com/asermax/tachikoma/commit/bdb05f8d18d1901b17a965e65fb37d7c41eac918))

### Documentation

- Update README and VISION to reflect current project state
  ([`0037175`](https://github.com/asermax/tachikoma/commit/003717579b6939067659f63a4f37e18af0efdb17))


## v1.14.0 (2026-04-09)

### Bug Fixes

- **channels**: Add deferred annotation evaluation for TYPE_CHECKING imports
  ([`dcd6b91`](https://github.com/asermax/tachikoma/commit/dcd6b91603b90e0ba54648d97249d4729f4b4180))

- **tests**: Update TestHandleMedia for refactored TelegramChannel constructor
  ([`c63e722`](https://github.com/asermax/tachikoma/commit/c63e722807b165baa1d7b136f38f04417f6d81f6))

### Code Style

- Apply ruff formatting across codebase
  ([`38698cd`](https://github.com/asermax/tachikoma/commit/38698cdd7b5b328eaeecd0576847820c9139bbfe))

### Documentation

- Mark DLT-063 implementation as complete
  ([`7b880b0`](https://github.com/asermax/tachikoma/commit/7b880b065b159884edd210088aac8ed3e13e4cd0))

- Reconcile DLT-063 into feature designs
  ([`33a9f7a`](https://github.com/asermax/tachikoma/commit/33a9f7accf2055b4e91ead62aece7d57493fa95d))

- Remove DLT-063 delta working files
  ([`141b8c5`](https://github.com/asermax/tachikoma/commit/141b8c5e2592f2d823f6a392af2acef964c27743))

- **planning**: Add DLT-063 implementation plan
  ([`4bcc73d`](https://github.com/asermax/tachikoma/commit/4bcc73dee87370c7042c7409d668bc24ee2fcd88))

- **planning**: Add DLT-063 spec and design
  ([`3cd95a1`](https://github.com/asermax/tachikoma/commit/3cd95a13b4727cb8c85cffd1c333ae787811995a))

- **planning**: Advance DLT-063 status to plan phase
  ([`68d046c`](https://github.com/asermax/tachikoma/commit/68d046ce496deb0b926990390e4f76282b1b3146))

- **planning**: Mark DLT-063 design as approved
  ([`add070f`](https://github.com/asermax/tachikoma/commit/add070f621183df187307a9537c1b37198319f66))

- **planning**: Mark DLT-063 design as complete
  ([`5151a91`](https://github.com/asermax/tachikoma/commit/5151a913b632ac185f0e6adc32d1683a53a52b4f))

- **planning**: Mark DLT-063 plan as approved
  ([`81bdb5b`](https://github.com/asermax/tachikoma/commit/81bdb5bc3d99cce52b41ad95e821b59ef1245245))

- **planning**: Mark DLT-063 spec as complete
  ([`8a6994c`](https://github.com/asermax/tachikoma/commit/8a6994c9ab709b424e4bf1fde23bb7842f51ac90))

- **planning**: Update DLT-063 design with detailed implementation approach
  ([`2a2160d`](https://github.com/asermax/tachikoma/commit/2a2160da7af43a9589d18b52cf0e3775647b2a3c))

- **planning**: Update DLT-063 spec with design feedback
  ([`747dc85`](https://github.com/asermax/tachikoma/commit/747dc8516780c20a1359f4242dbf1e19a4ef4ee0))

- **planning**: Update DLT-063 status to spec
  ([`0aaeafd`](https://github.com/asermax/tachikoma/commit/0aaeafdced94ec8a92350bfc9830e5f3875183ad))

- **skills**: Fix step numbering and diagram alignment after merge
  ([`1780e3f`](https://github.com/asermax/tachikoma/commit/1780e3f1deb875d9d5c8a18411e02a22e7efcc17))

### Features

- **channels**: Wire channel capabilities into startup flow
  ([`39c1d68`](https://github.com/asermax/tachikoma/commit/39c1d683457ae8af5d7263e8d915205a0fb22e0b))

- **coordinator**: Add cold-start session resumption on fresh startup
  ([`7c36204`](https://github.com/asermax/tachikoma/commit/7c3620489b91912a4c06b10c541880cafc45651c))

### Refactoring

- **channels**: Extract Channel protocol and implement in REPL
  ([`12accb7`](https://github.com/asermax/tachikoma/commit/12accb70ca96da2eb35f5b02af25aabba22fa830))

- **channels**: Replace coordinator assert guards with property
  ([`aa5c3ba`](https://github.com/asermax/tachikoma/commit/aa5c3babd29a0d67880a232ed9a2f818135f8e1a))

- **telegram**: Promote telegram module to package
  ([`99cd414`](https://github.com/asermax/tachikoma/commit/99cd4148b3904525d61a5dc5ea1960df4674971f))


## v1.13.1 (2026-04-09)

### Bug Fixes

- **coordinator**: Pass system prompt via tempfile to eliminate ARG_MAX constraint
  ([`4c4baf8`](https://github.com/asermax/tachikoma/commit/4c4baf8d25508737233cc9148e06f7ae86ea9e9a))

- **memory**: Extract snippets from episodic memories to prevent ARG_MAX overflow
  ([`6c00c6b`](https://github.com/asermax/tachikoma/commit/6c00c6b581419d1397b63a7c160d13c6876c64eb))

### Chores

- **planning**: Remove already-implemented DLT-114 from delta inventory
  ([`69b7e52`](https://github.com/asermax/tachikoma/commit/69b7e521002a60c1db882fd39a22163ab213ed37))

### Documentation

- **memory**: Update feature docs for snippet extraction and tempfile transport
  ([`71909bd`](https://github.com/asermax/tachikoma/commit/71909bdfbc16220d4d136db81ef5c6697ae17e7c))


## v1.13.0 (2026-04-08)

### Documentation

- **planning**: Elevate DLT-098 priority to Critical
  ([`d6a8de4`](https://github.com/asermax/tachikoma/commit/d6a8de4df94a4625734420ab47fb6d8276d62909))


## v1.12.0 (2026-04-08)


## v1.11.1 (2026-04-08)

### Bug Fixes

- **skills**: Check skill existence instead of agent presence in derive_agents_from_entries
  ([`e85e256`](https://github.com/asermax/tachikoma/commit/e85e2565e5eec56e81a5b7e3007b860916ec4966))


## v1.11.0 (2026-04-08)

### Bug Fixes

- **boundary**: Disable tools via empty base tool set and increase max_turns
  ([`d78052f`](https://github.com/asermax/tachikoma/commit/d78052ffc27d82e935d5eb7de64bf62c3b4793d9))

### Documentation

- **boundary**: Update DES-007 and feature designs for tools=[] pattern
  ([`e4c5766`](https://github.com/asermax/tachikoma/commit/e4c57668b04b6839378cee2afaf14a6a558ac171))

- **planning**: Add DLT-121 git-friendly database storage
  ([`330379b`](https://github.com/asermax/tachikoma/commit/330379b61c35d96405ee09c4e7948b123a8c6949))

- **planning**: Reprioritize deltas and add DLT-115 through DLT-120
  ([`2935e99`](https://github.com/asermax/tachikoma/commit/2935e999d0ec1ce9524954437b171c638dd6e0c6))

- **workflows**: Update feature specs and designs for auto-start/auto-finalize
  ([`ae712ca`](https://github.com/asermax/tachikoma/commit/ae712cadf0bacb519284d5afe001d4c6e9770fb0))

### Features

- **workflows**: Auto-start next step and auto-finalize workflow on completion
  ([`7bbac0b`](https://github.com/asermax/tachikoma/commit/7bbac0b163c0a7b933d77026ae12e197a6265501))


## v1.10.0 (2026-04-07)

### Chores

- Remove unused imports and stale comments
  ([`456847a`](https://github.com/asermax/tachikoma/commit/456847a74b6ce6786efecc69404e187963d207ea))

- **docs**: Remove DLT-035 delta documents after reconciliation
  ([`998056c`](https://github.com/asermax/tachikoma/commit/998056ca023c172c0faa20cd86567877af05dc30))

- **planning**: Remove DLT-076 delta artifacts after reconciliation
  ([`32f00d0`](https://github.com/asermax/tachikoma/commit/32f00d052d68242d0a2704cd8141a6ed8a674cc0))

### Code Style

- Apply ruff formatting fixes
  ([`193ea66`](https://github.com/asermax/tachikoma/commit/193ea662e24f9339890c18b3ff114c6d6bc1212e))

### Documentation

- **agent,configuration**: Reconcile feature docs with auto-injected env layer
  ([`4e2c8e1`](https://github.com/asermax/tachikoma/commit/4e2c8e1b063e356ce72f9bb4b84f1a63d100148a))

- **context**: Update workflow tool descriptions to match current API
  ([`bafa64a`](https://github.com/asermax/tachikoma/commit/bafa64a614927c235d4eaa95186897645acd3dcd))

- **designs**: Add DLT-081 workflow state machine design
  ([`958e784`](https://github.com/asermax/tachikoma/commit/958e78418f17b005603a06141090caa0667bfa78))

- **designs**: Add soft delete and scratchpad path to workflow state design
  ([`cae816f`](https://github.com/asermax/tachikoma/commit/cae816fa75b2741d0c1b84a214ac6591443cef46))

- **designs**: Refine DLT-081 design with validation rules and recovery details
  ([`bf5a053`](https://github.com/asermax/tachikoma/commit/bf5a053c190d29e7d415952842ad367ddc16f872))

- **designs**: Resolve open questions and add recovery mechanisms for workflow state machine
  ([`b8eddea`](https://github.com/asermax/tachikoma/commit/b8eddea987d012b938422c296c0e2d77b52d6bd4))

- **memory**: Reconcile per-message memory context into feature documentation
  ([`3932179`](https://github.com/asermax/tachikoma/commit/393217999a756c53d10e64b13c7187f60be978d5))

- **planning**: Add DLT-035 spec and design
  ([`9f2efe5`](https://github.com/asermax/tachikoma/commit/9f2efe53bab9db14fac13259079a172e06e40c68))

- **planning**: Add DLT-076 spec and design
  ([`6cf8408`](https://github.com/asermax/tachikoma/commit/6cf840835000a0fd71b0672fd7077d2ca8308d8b))

- **planning**: Add DLT-081 implementation plan
  ([`2bab4bd`](https://github.com/asermax/tachikoma/commit/2bab4bd6bbbdc7555485f939c9e6c07e6d915177))

- **planning**: Add DLT-114 implementation plan
  ([`48a9bf4`](https://github.com/asermax/tachikoma/commit/48a9bf4de1181df01c453c55e9a0f9e5c54ad59c))

- **planning**: Add DLT-114 spec and design
  ([`ea99ef5`](https://github.com/asermax/tachikoma/commit/ea99ef508163dac6bc59b73b899b729fda383c71))

- **planning**: Approve DLT-114 design
  ([`5abecf9`](https://github.com/asermax/tachikoma/commit/5abecf9b84f08c380c2578b84031bd8be21d1972))

- **planning**: Complete DLT-076 design
  ([`6bbb8b2`](https://github.com/asermax/tachikoma/commit/6bbb8b274a0768c5ae7c2da408bfca576d03d764))

- **planning**: Complete DLT-076 implementation plan
  ([`982a5f9`](https://github.com/asermax/tachikoma/commit/982a5f950b6aa57c83a2e9537decc0cf7ea69627))

- **planning**: Finalize DLT-035 design for Telegram media support
  ([`b3189f3`](https://github.com/asermax/tachikoma/commit/b3189f3c2e79072e1ca0aabcb13ab69a862d8253))

- **planning**: Flesh out DLT-114 design
  ([`4ee4448`](https://github.com/asermax/tachikoma/commit/4ee4448c1f31bd78ed7a5ace7d42ceec85a7bd41))

- **planning**: Mark DLT-076 batches done and status as implementation
  ([`bdd0132`](https://github.com/asermax/tachikoma/commit/bdd0132d0b682241efec78fc14e1ea3bf0defc4b))

- **planning**: Mark DLT-076 design as complete
  ([`076bad1`](https://github.com/asermax/tachikoma/commit/076bad17c883d7ce61c9ed628acf49fe42926aec))

- **planning**: Mark DLT-076 spec as complete
  ([`d2e7d46`](https://github.com/asermax/tachikoma/commit/d2e7d465b9791343d9890f4a9bcd1b6bc156b676))

- **planning**: Mark DLT-081 plan as complete
  ([`018f892`](https://github.com/asermax/tachikoma/commit/018f8921c0b6913eec9a3dca9e993ae662232263))

- **planning**: Mark DLT-081 spec as complete
  ([`b2c8a07`](https://github.com/asermax/tachikoma/commit/b2c8a076f17398ed0d49ad8693d23536f661f073))

- **planning**: Reconcile DLT-035 media support into feature documentation
  ([`73cdc08`](https://github.com/asermax/tachikoma/commit/73cdc086c45ef8136819bcb42267341a9673f8c6))

- **planning**: Remove completed DLT-114 delta working docs
  ([`aa22713`](https://github.com/asermax/tachikoma/commit/aa227139137ba0f6d4721868f727689eea75cdca))

- **planning**: Update DLT-035 status to design
  ([`4492395`](https://github.com/asermax/tachikoma/commit/44923957b77fdac974198febaf72cbc852b88ead))

- **planning**: Update DLT-035 status to design
  ([`24aa8c4`](https://github.com/asermax/tachikoma/commit/24aa8c45cda329f9ffc85e5576719bd52585943e))

- **planning**: Update DLT-035 status to implementation complete
  ([`da2dfc8`](https://github.com/asermax/tachikoma/commit/da2dfc89609583daf34aa55f8fb2071dc71e2691))

- **planning**: Update DLT-035 status to plan
  ([`dfd7e05`](https://github.com/asermax/tachikoma/commit/dfd7e058b6c41e8c601767bbefbed1b055230553))

- **planning**: Update DLT-035 status to plan complete
  ([`cb2e99d`](https://github.com/asermax/tachikoma/commit/cb2e99d8b242091bcaa5cbfc94291e702adb31c4))

- **planning**: Update DLT-035 status to spec
  ([`13a26ae`](https://github.com/asermax/tachikoma/commit/13a26aefb86ba1ddbd165d73796bc5a16c367491))

- **planning**: Update DLT-076 status to design
  ([`17beedf`](https://github.com/asermax/tachikoma/commit/17beedfdd82d8bd6bcc956a372407ec20691a111))

- **planning**: Update DLT-076 status to plan
  ([`5529409`](https://github.com/asermax/tachikoma/commit/552940916e923ca762e4531325c2e439f45c3235))

- **planning**: Update DLT-076 status to spec
  ([`3cdb8bf`](https://github.com/asermax/tachikoma/commit/3cdb8bf1aa0fe5a1909f4cf38064fcc32dede713))

- **planning**: Update DLT-081 status and plan progress
  ([`b6c398b`](https://github.com/asermax/tachikoma/commit/b6c398bd62ddc656ea765b21b3c9f6ef1032cc2c))

- **planning**: Update DLT-081 status to Design
  ([`6398981`](https://github.com/asermax/tachikoma/commit/6398981d85ecdc910df9bbfdf2657bf471ff18e6))

- **planning**: Update DLT-081 status to Plan
  ([`02a2b0c`](https://github.com/asermax/tachikoma/commit/02a2b0c199cdbd7ecb050f7dbf4f51af5c366a1b))

- **planning**: Update DLT-081 status to Spec
  ([`66eb451`](https://github.com/asermax/tachikoma/commit/66eb4516161dc96281e5a307a8360e513ff52ff0))

- **planning**: Update DLT-114 status to design
  ([`2f49ccd`](https://github.com/asermax/tachikoma/commit/2f49ccd3cb12e70845123279fe37256862691ed9))

- **planning**: Update DLT-114 status to implementation
  ([`bce5f3e`](https://github.com/asermax/tachikoma/commit/bce5f3eebd40ad3afc5deed3c21ea60e58829812))

- **planning**: Update DLT-114 status to plan
  ([`7ea5b81`](https://github.com/asermax/tachikoma/commit/7ea5b8194fee994a39f9ff9494eda680910d6d10))

- **planning**: Update DLT-114 status to spec
  ([`aa79ca6`](https://github.com/asermax/tachikoma/commit/aa79ca64b6d320ed74f18242b24f75b843d12ec2))

- **planning**: Write DLT-035 design for Telegram media support
  ([`5712b67`](https://github.com/asermax/tachikoma/commit/5712b671491cd3354b38279fdcaae7de9178c0fb))

- **planning**: Write DLT-035 implementation plan for Telegram media support
  ([`d68af67`](https://github.com/asermax/tachikoma/commit/d68af67792c54004d51b743689343ef6eab5095e))

- **specs**: Add DLT-081 workflow state machine for skills spec
  ([`8e89e32`](https://github.com/asermax/tachikoma/commit/8e89e32a901815beb68aebf6d1c91a91a53f0b2f))

- **specs**: Refine DLT-081 workflow state machine spec
  ([`46b5014`](https://github.com/asermax/tachikoma/commit/46b50141f322d6e36864d5e2865533cc24395115))

- **specs**: Update DLT-081 concurrent workflows AC for duplicate prevention
  ([`74ce6e9`](https://github.com/asermax/tachikoma/commit/74ce6e9b20005433dd359db04ea4816f5ed09af7))

- **workflows**: Promote DLT-081 from delta to feature documentation
  ([`53852b3`](https://github.com/asermax/tachikoma/commit/53852b36156dc55b82443409b29922ccfd404b64))

### Features

- **agent**: Add auto-injected env layer for TZ in subprocesses
  ([`ad21756`](https://github.com/asermax/tachikoma/commit/ad21756d6d369018f7093c295a39f05190cd32c5))

- **coordinator**: Wire memory provider into per-message pipeline with session forking
  ([`89cd615`](https://github.com/asermax/tachikoma/commit/89cd615f48f34e6f21987b194e513af170d0846d))

- **media**: Add media download and processing module
  ([`5f3dcf2`](https://github.com/asermax/tachikoma/commit/5f3dcf23637aeeabed838cd0659802eb71d14966))

- **memory**: Rewrite context provider with session forking and per-file results
  ([`26c8de9`](https://github.com/asermax/tachikoma/commit/26c8de952cb02e9aa2255bc7cd9518d3b50d2e7c))

- **pipeline**: Add sdk_session_id to message pre-processing interface
  ([`f5f2314`](https://github.com/asermax/tachikoma/commit/f5f231473456db3e0acef9231fe8b16d769c7e4d))

- **telegram**: Integrate media handler for incoming images and audio
  ([`f84c6ff`](https://github.com/asermax/tachikoma/commit/f84c6ff556633189658e787a1ca6b272ca95122a))

- **workflows**: Add started timestamp to active workflow listing
  ([`6095e12`](https://github.com/asermax/tachikoma/commit/6095e12e44299a90689904f143d875e5af4bbd8f))

- **workflows**: Add workflow state machine for skills
  ([`48f4a6c`](https://github.com/asermax/tachikoma/commit/48f4a6c9616a3921c7344c0d2e21e625250170fe))

### Refactoring

- **media**: Consolidate extension resolver functions
  ([`cef3645`](https://github.com/asermax/tachikoma/commit/cef3645338269aad7506b77751fc59e177ca8390))

- **memory**: Simplify context provider options and extract sentinel constant
  ([`a82bbd9`](https://github.com/asermax/tachikoma/commit/a82bbd9a0cbc737f1e590111cfc6ec1b72d92328))

- **workflows**: Simplify model and extract shared helpers
  ([`2759d45`](https://github.com/asermax/tachikoma/commit/2759d450130042ffe7e8ce34a6295e198fb79859))

### Testing

- Update coordinator and pipeline tests for sdk_session_id parameter
  ([`81abe57`](https://github.com/asermax/tachikoma/commit/81abe573564ec8fe84a2bc7cf328d492828eb8e1))

- **agent**: Add tests for auto-injected env layer
  ([`d3624a2`](https://github.com/asermax/tachikoma/commit/d3624a2cba7341498e6b305e19435c9ad4ee4b99))

- **agent**: Remove redundant auto-inject collision tests
  ([`8ca4e09`](https://github.com/asermax/tachikoma/commit/8ca4e090152221cddbfc3be783747ccfa5cc318a))

- **media**: Improve test style and add voice metadata edge case
  ([`def9f75`](https://github.com/asermax/tachikoma/commit/def9f752b318a02148e6ba9446522c687ad4c918))

- **workflows**: Add tests for workflow components
  ([`0874629`](https://github.com/asermax/tachikoma/commit/0874629814d7695add6304db0cf21a24cc2ade48))

- **workflows**: Update tests for refactored helpers
  ([`c700c35`](https://github.com/asermax/tachikoma/commit/c700c35e12ce769d269ced4e0251bc14df3de4aa))


## v1.9.0 (2026-04-07)

### Documentation

- **planning**: Add DLT-114 timezone env variable delta
  ([`228d10b`](https://github.com/asermax/tachikoma/commit/228d10b1449382092cbaeacbf7ff1125d2f26459))

- **planning**: Increase DLT-035 priority to Critical
  ([`9b2f705`](https://github.com/asermax/tachikoma/commit/9b2f7054ea6065d32285d94ed6100d0e730d1187))


## v1.8.0 (2026-04-07)

### Documentation

- Remove on_status references from architecture docs
  ([`4fff5a2`](https://github.com/asermax/tachikoma/commit/4fff5a25fdea0d70203c126fbebd05797d628fa9))

- **architecture**: Add ADR-011 for structured metadata on context entries
  ([`6e07448`](https://github.com/asermax/tachikoma/commit/6e074489401466d05ad8f93ac2b9ee4458b12b89))

- **designs**: Add initial design for per-message skill re-evaluation
  ([`4e6cc71`](https://github.com/asermax/tachikoma/commit/4e6cc71a451c4e6b47d7f9fbfd291497d8cfab1e))

- **designs**: Complete design for per-message skill re-evaluation
  ([`be4c04e`](https://github.com/asermax/tachikoma/commit/be4c04e54b84273c2303b146d19965d6c24c1c81))

- **designs**: Refine DLT-075 design with metadata-based skill detection
  ([`3791e3c`](https://github.com/asermax/tachikoma/commit/3791e3cc42db62a8d74317fe370ad4b6a6510695))

- **planning**: Add DLT-111, DLT-112, DLT-113 and update DLT-081
  ([`2f9b38b`](https://github.com/asermax/tachikoma/commit/2f9b38b2b39e6017cd67a15e4896163b38055334))

- **planning**: Add implementation plan and mark DLT-075 plan as complete
  ([`1384a48`](https://github.com/asermax/tachikoma/commit/1384a48acbdab1f91db2cb0aca256677dcf2b1e3))

- **planning**: Mark DLT-075 implementation as complete
  ([`00457a4`](https://github.com/asermax/tachikoma/commit/00457a46352625e99ab58bb1538b8fa12728b2e8))

- **planning**: Mark DLT-075 spec as complete
  ([`7a51ee8`](https://github.com/asermax/tachikoma/commit/7a51ee8a617674733698e392d01e040161a978e3))

- **planning**: Remove completed DLT-075 delta artifacts
  ([`d65204a`](https://github.com/asermax/tachikoma/commit/d65204a830e314bbff10dea3250af53d8cdba472))

- **planning**: Remove DLT-090 (already implemented)
  ([`9f6d46b`](https://github.com/asermax/tachikoma/commit/9f6d46ba73e2c03f07b85b0725e8eab39a4aa461))

- **planning**: Reprioritize deltas around workflows and usability
  ([`aa42a42`](https://github.com/asermax/tachikoma/commit/aa42a425a1b1f9dc2cce784b7d331438684c108a))

- **planning**: Start design phase for DLT-075
  ([`178ecfc`](https://github.com/asermax/tachikoma/commit/178ecfcf2ad1680c38f9682ccb2503f0b6a08194))

- **planning**: Start plan phase for DLT-075
  ([`5a65a9f`](https://github.com/asermax/tachikoma/commit/5a65a9f65138fc884c641a5f6e12776838bf395e))

- **planning**: Start spec phase for DLT-075
  ([`cae9ad6`](https://github.com/asermax/tachikoma/commit/cae9ad6a35293416b2e66cbeaa5b2f29a34e88e8))

- **planning**: Update DLT-075 implementation progress
  ([`e077a00`](https://github.com/asermax/tachikoma/commit/e077a007b25030f3f350d4ce13d9446bbb885055))

- **specs**: Add metadata field requirement and one-entry-per-skill to DLT-075
  ([`eef3547`](https://github.com/asermax/tachikoma/commit/eef35470f65de8b787fb4b3cf2214e9dc4268051))

- **specs**: Add spec for per-message skill re-evaluation (DLT-075)
  ([`65ef59b`](https://github.com/asermax/tachikoma/commit/65ef59b28e93bce84773f88ae8fbbada3a8581b5))

- **specs**: Reconcile feature docs with per-message skill evaluation
  ([`8d2dbc5`](https://github.com/asermax/tachikoma/commit/8d2dbc5ae8cbf4e7a2cdb4da4e4c4ac7d9c8dea4))

### Features

- **coordinator**: Wire per-message skill re-evaluation into message flow
  ([`70c84d4`](https://github.com/asermax/tachikoma/commit/70c84d4a7569e6f3a9be299b665bf345f4899c2d))

- **pipeline**: Add per-message pre-processing pipeline
  ([`d41ad52`](https://github.com/asermax/tachikoma/commit/d41ad5260c28ef0c8675a81ff9785db70204e48f))

- **sessions**: Add metadata field to context entries
  ([`8d249e9`](https://github.com/asermax/tachikoma/commit/8d249e9d1e98ddd823fb24a6f15a582212423f84))

- **tasks**: Add get_task tool and remove prompt truncation from list_tasks
  ([`02c5951`](https://github.com/asermax/tachikoma/commit/02c595144810024fb08466e90d631c191bd639e2))

### Refactoring

- **coordinator**: Remove on_status callback and processing memories text
  ([`017a198`](https://github.com/asermax/tachikoma/commit/017a1983b5064c9e09626f4509c5446ae3c04df8))

- **skills**: Consolidate skill context logic and optimize entry persistence
  ([`96f0099`](https://github.com/asermax/tachikoma/commit/96f0099e85e800384d2a5c8807c1a14cf30fdcbd))

- **skills**: Evaluate skills per-message with metadata filtering
  ([`71614e0`](https://github.com/asermax/tachikoma/commit/71614e0a063c8e4b186f165844ccff836041624f))

- **skills**: Use MessageContextProvider base for per-message evaluation
  ([`72dd319`](https://github.com/asermax/tachikoma/commit/72dd319297a7e32bd2b90000cb25f5ef590a299c))

- **tasks**: Remove agent definitions from background task executor
  ([`968f92f`](https://github.com/asermax/tachikoma/commit/968f92fac06c5abfa874401b8a8aa5616d4c613d))

### Testing

- **coordinator**: Add per-message pre-processing integration tests
  ([`cb80d33`](https://github.com/asermax/tachikoma/commit/cb80d33cfe849b9e0c713a912ef26016285069bb))

- **sessions**: Add metadata field tests for context entries
  ([`c085bb0`](https://github.com/asermax/tachikoma/commit/c085bb0773ac88edabca417a872be71c6fbf42cf))


## v1.7.2 (2026-04-06)

### Bug Fixes

- **telegram**: Prevent duplicate messages on split responses
  ([`1d012d2`](https://github.com/asermax/tachikoma/commit/1d012d2a43c52becb3f805f81764616c9026319e))

### Documentation

- **telegram**: Update spec and design for split message tracking
  ([`ea20092`](https://github.com/asermax/tachikoma/commit/ea2009273342c1da43f269cfe669486a4680240c))


## v1.7.1 (2026-04-06)

### Bug Fixes

- **telegram**: Render Bash descriptions as plain text
  ([`ff666c4`](https://github.com/asermax/tachikoma/commit/ff666c43e2e180c342d1c758c1a361130f026017))

### Chores

- Remove zenki submodule
  ([`66957fe`](https://github.com/asermax/tachikoma/commit/66957fe6c37c994c89ffb36332a3761aad461167))


## v1.7.0 (2026-04-06)


## v1.6.1 (2026-04-05)

### Bug Fixes

- **adapter**: Detect encoding errors and recover contaminated SDK sessions
  ([`ee5f5eb`](https://github.com/asermax/tachikoma/commit/ee5f5eb7bb4b7544784cb73c9faf602c03995eff))

- **merge**: Resolve auto-merge conflicts from DLT-084 landing
  ([`1c29af8`](https://github.com/asermax/tachikoma/commit/1c29af8274fc1fd4196c073a54c27d4ccf5ab3e0))

- **sessions**: Add error flag to session model and exclude errored sessions from resumable
  candidates
  ([`4765dfe`](https://github.com/asermax/tachikoma/commit/4765dfef3df3c091a5b7de6da9d32d4f535f83b3))

### Documentation

- **sessions**: Update specs and designs for session error tracking
  ([`612a343`](https://github.com/asermax/tachikoma/commit/612a343e01d79a5d4beae612be7eb9e8fd426e79))

- **tasks**: Reconcile agent-driven notification changes into feature docs
  ([`c1268e4`](https://github.com/asermax/tachikoma/commit/c1268e4565f32c283158f4aefce5b6cd07814d82))

### Refactoring

- **tasks**: Remove unnecessary comments and deduplicate datetime call
  ([`9202fd1`](https://github.com/asermax/tachikoma/commit/9202fd128acc6458a45eeadd059d4f57ce521a81))


## v1.6.0 (2026-04-05)

### Chores

- Update uv.lock
  ([`6e87fc3`](https://github.com/asermax/tachikoma/commit/6e87fc3d71848592541ca27d1b4db7e446df6cdf))

- Update uv.lock after master merge
  ([`7c3cb24`](https://github.com/asermax/tachikoma/commit/7c3cb24d2cf437a07dad58fbe12e2fe6567d1772))

### Documentation

- **design**: Add design for Telegram markdown escaping
  ([`6163d0f`](https://github.com/asermax/tachikoma/commit/6163d0fef3647b502003d380502824db64bfaf57))

- **design**: Resolve S2 flag and detail Telegram markdown escaping design
  ([`c10f402`](https://github.com/asermax/tachikoma/commit/c10f402c7fe3c80b20de13f6050d4b68e6289a7a))

- **planning**: Add DLT-079 implementation plan
  ([`080879f`](https://github.com/asermax/tachikoma/commit/080879f08a5d7652ea9c5c9878b12f22446953d6))

- **planning**: Advance DLT-079 status to plan phase
  ([`6ac976d`](https://github.com/asermax/tachikoma/commit/6ac976d305a0bb53b5e926594749113532913cf2))

- **planning**: Mark DLT-079 design as approved
  ([`df6b266`](https://github.com/asermax/tachikoma/commit/df6b2668a3282abf9ffd258745afe1f21c678691))

- **planning**: Mark DLT-079 implementation complete
  ([`5dacc9b`](https://github.com/asermax/tachikoma/commit/5dacc9bc7166958df43983b49536abb74fd28687))

- **planning**: Update DLT-079 status to design
  ([`84b5644`](https://github.com/asermax/tachikoma/commit/84b564426cfe90e8128adfe535c087f3f7098d81))

- **planning**: Update DLT-079 status to spec
  ([`479e1ec`](https://github.com/asermax/tachikoma/commit/479e1ec3385b0299b358d9391c7f6b78e1d2c935))

- **spec**: Add spec for Telegram markdown escaping
  ([`bce7155`](https://github.com/asermax/tachikoma/commit/bce71559ee749bbf20a5a02a1fd357a2b2cf1c84))

- **spec**: Refine DLT-079 spec for clarity and edge cases
  ([`85af704`](https://github.com/asermax/tachikoma/commit/85af70428fc1977cde05676a938c9ae65b2368ba))

- **spec**: Refine R4 isolation requirement for display.py changes
  ([`e5ee979`](https://github.com/asermax/tachikoma/commit/e5ee9796ebb6ef3e55cbb5e1bf8c0234332db4ca))

- **spec,design**: Extend Bash description preference to shared TOOL_DISPLAY
  ([`459a62f`](https://github.com/asermax/tachikoma/commit/459a62f606145ce26bb4847bdd0d2d7e5c1a9519))

- **telegram**: Reconcile DLT-079 into feature docs and clean up delta files
  ([`2ffc745`](https://github.com/asermax/tachikoma/commit/2ffc7451453e817ae269e23c258f1069d727e234))

### Features

- **display**: Prefer Bash description and support channel-specific summaries
  ([`8e809a8`](https://github.com/asermax/tachikoma/commit/8e809a890d39df49e338552107972da440ecf251))

- **telegram**: Add channel-specific formatters with code wrapping
  ([`4fc5f56`](https://github.com/asermax/tachikoma/commit/4fc5f5685e6c8d1d93cfd5962a6c63e69a5433e0))

### Refactoring

- **display**: Unify Bash summary formatting with wrapper parameter
  ([`8d43d90`](https://github.com/asermax/tachikoma/commit/8d43d90f052b0db3e8b1d81445aac2b2fa2ad2ba))

### Testing

- **telegram**: Replace glob test with read and fallback cases
  ([`f28e976`](https://github.com/asermax/tachikoma/commit/f28e9760bbc1b21fda8b5e67269795de7e20ede9))


## v1.5.2 (2026-04-05)

### Bug Fixes

- **adapter**: Sanitize error messages from SDK output
  ([`d12b42b`](https://github.com/asermax/tachikoma/commit/d12b42b3a78c89423f4af982bf90d52542645c66))

- **ci**: Install uv inside PSR Docker container for build_command
  ([`daefeff`](https://github.com/asermax/tachikoma/commit/daefeff5e6398d04e917f60c4f4e51109c1c7bec))

- **ci**: Let PSR handle uv.lock update during release
  ([`a6355fb`](https://github.com/asermax/tachikoma/commit/a6355fb1261d293943c8390f40150111284b8a07))

- **telegram**: Add defense-in-depth surrogate sanitization at API boundary
  ([`2e58dee`](https://github.com/asermax/tachikoma/commit/2e58dee94f27177bac7d9b7e942404f9572b193a))


## v1.5.1 (2026-04-05)

### Bug Fixes

- **adapter**: Sanitize invalid UTF-8 surrogates from SDK text output
  ([`6107723`](https://github.com/asermax/tachikoma/commit/610772321091daa36d9c6c253292f69574868cfa))

- **tasks**: Prevent hour-boundary cron tasks from never firing
  ([`82335f7`](https://github.com/asermax/tachikoma/commit/82335f7a54624ad6cb03386c22665e887dfeee5f))

### Documentation

- **agent**: Add text sanitization to architecture docs
  ([`db6aa62`](https://github.com/asermax/tachikoma/commit/db6aa62867058c3e962d7d1f958a6e6a14db5650))

- **planning**: Add deltas DLT-099 through DLT-110 to inventory
  ([`f053b62`](https://github.com/asermax/tachikoma/commit/f053b623526b46031541e23d6634184f59eaddf0))


## v1.5.0 (2026-04-05)

### Documentation

- **tasks**: Clarify notify parameter and R10 UTC defaulting in spec
  ([`b68c855`](https://github.com/asermax/tachikoma/commit/b68c8550c9446fd5f3b6cea8371b400782298c62))


## v1.4.0 (2026-04-05)

### Chores

- Remove DLT-092 working documents
  ([`727aadd`](https://github.com/asermax/tachikoma/commit/727aadd6dd084a4193e627f69f154d21115ca1c1))

- Update lockfile for version 1.3.1
  ([`222be73`](https://github.com/asermax/tachikoma/commit/222be73a21830a56cede5806fa30b6feae1528cd))

- Update uv.lock
  ([`90da50a`](https://github.com/asermax/tachikoma/commit/90da50af456df1796e11c63c5b8d28709ce5a240))

### Documentation

- **planning**: Mark DLT-092 plan as approved
  ([`bc71385`](https://github.com/asermax/tachikoma/commit/bc713854ede53b6ea741c1a8b41959fc78c3621e))

- **planning**: Mark DLT-092 spec as complete
  ([`40a11f5`](https://github.com/asermax/tachikoma/commit/40a11f5ab7596bb12ec7e88418168193aa82b49a))

- **planning**: Update DLT-092 status to design
  ([`9be2ed9`](https://github.com/asermax/tachikoma/commit/9be2ed9a870343958914cd476722af94b2c89715))

- **planning**: Update DLT-092 status to plan
  ([`56b451e`](https://github.com/asermax/tachikoma/commit/56b451eb95e068aa44061210cdf4de14da87e1f6))

- **planning**: Update DLT-092 status to spec
  ([`16e56c6`](https://github.com/asermax/tachikoma/commit/16e56c6271359d1ac3b641535bc13d24d8c79144))

- **scheduling**: Add DLT-092 implementation plan
  ([`e76ee82`](https://github.com/asermax/tachikoma/commit/e76ee827869731385b422800ab226b974f30684f))

- **scheduling**: Add spec and design for timezone-aware one-shot tasks
  ([`3ac1bd5`](https://github.com/asermax/tachikoma/commit/3ac1bd598ecccdb370e897fb146c86d3df48550f))

- **scheduling**: Complete DLT-092 design with timezone-aware parsing details
  ([`97ad597`](https://github.com/asermax/tachikoma/commit/97ad597ca98866ef60270fb9d1ae74e160620cd9))

- **scheduling**: Expand DLT-092 spec and design with preamble and executor changes
  ([`ed0f31e`](https://github.com/asermax/tachikoma/commit/ed0f31e0d761c360f88ddc9ec6a166bed9083ed9))

- **scheduling**: Mark DLT-092 design as approved
  ([`df7424a`](https://github.com/asermax/tachikoma/commit/df7424a2b9195b1ae444f965af00ec0707edea0f))

- **scheduling**: Mark DLT-092 implementation batches as done
  ([`a1043b9`](https://github.com/asermax/tachikoma/commit/a1043b9232baee29a04a86b71efdc2b3e8c035c7))

- **scheduling**: Reconcile DLT-092 timezone-aware scheduling into feature docs
  ([`b93b002`](https://github.com/asermax/tachikoma/commit/b93b0026736dd82ec0cb9330d877092da16ee72f))

- **tasks**: Add design for agent-driven notification tool
  ([`131eeb6`](https://github.com/asermax/tachikoma/commit/131eeb605f7751556ce1956c478130ea7159eaf2))

- **tasks**: Add implementation plan for DLT-091
  ([`69891a5`](https://github.com/asermax/tachikoma/commit/69891a5b2e266662c477e14121c153c4c7cb2bd0))

- **tasks**: Add spec for agent-driven notification tool
  ([`3f5a106`](https://github.com/asermax/tachikoma/commit/3f5a10614fa139c5130b56323f5369b8565c367e))

- **tasks**: Approve DLT-091 design
  ([`1c52f76`](https://github.com/asermax/tachikoma/commit/1c52f76d8f74d1431f8d1750d687062431fd238e))

- **tasks**: Flesh out DLT-091 design with full shape and decisions
  ([`fa0b78c`](https://github.com/asermax/tachikoma/commit/fa0b78cd4b2d77770e791750897060bd14e9b20e))

- **tasks**: Replace DLT-091 with agent-driven notification tool
  ([`107dc5e`](https://github.com/asermax/tachikoma/commit/107dc5eab6a5c728545477ca39e591378bce6cea))

- **tasks**: Update DLT-091 status to design
  ([`98710d5`](https://github.com/asermax/tachikoma/commit/98710d56c9e018ebe03f3f31c84f46e518431c60))

- **tasks**: Update DLT-091 status to plan
  ([`d00ad93`](https://github.com/asermax/tachikoma/commit/d00ad9373b8bdd95797a32095ecf1bd306ea41d4))

- **tasks**: Update DLT-091 status to plan
  ([`04faa9b`](https://github.com/asermax/tachikoma/commit/04faa9bfd1f14d5257c1b808b1a7fe9c3915f670))

- **tasks**: Update DLT-091 status to spec
  ([`ab5d0f5`](https://github.com/asermax/tachikoma/commit/ab5d0f56adb11158a3b6a7fae70ec02331810323))

### Features

- **tasks**: Add date/time to system preamble and background executor
  ([`ba88b04`](https://github.com/asermax/tachikoma/commit/ba88b04c6782e84ca8266b028ccf765ca8968802))

- **tasks**: Add timezone validation and system detection to config
  ([`476ddb9`](https://github.com/asermax/tachikoma/commit/476ddb93e19927dfb35ad14e5692ae74f3fc7e2f))

- **tasks**: Make schedule parsing and display timezone-aware
  ([`d479b18`](https://github.com/asermax/tachikoma/commit/d479b1858d908cbf775b1df7b3103e4394166a9e))

- **tasks**: Replace notify field with agent-driven notification tool
  ([`d01d9b2`](https://github.com/asermax/tachikoma/commit/d01d9b297b007398c2a648c70526dabf27612b7d))

### Refactoring

- **context**: Make timezone a required parameter in system prompt functions
  ([`0f460e9`](https://github.com/asermax/tachikoma/commit/0f460e93feecbc6b099bf7e0e90e5ef3615abd0c))


## v1.3.1 (2026-04-04)

### Bug Fixes

- **tasks**: Make schedule deserialization robust to malformed data
  ([`790613b`](https://github.com/asermax/tachikoma/commit/790613baaabac0ef55d7d80ff33c7913b4185cb3))

### Documentation

- **tasks**: Add schedule deserialization robustness to feature documentation
  ([`1de898e`](https://github.com/asermax/tachikoma/commit/1de898e44829da375561dd2e7eee505e9c159226))


## v1.3.0 (2026-04-04)

### Bug Fixes

- **tests**: Shorten docstring to comply with line length limit
  ([`3f4e2a7`](https://github.com/asermax/tachikoma/commit/3f4e2a7348e8d397122c3e964888bc8f1ddcf847))

### Documentation

- **agent**: Update tool blocking section to reflect system-level merge pattern
  ([`7ab739e`](https://github.com/asermax/tachikoma/commit/7ab739efbe79a38785b40642ffb28314c7c2a981))

- **planning**: Add DLT-098 for SDK stderr capture on error
  ([`822e746`](https://github.com/asermax/tachikoma/commit/822e7466463247d78e703be95672e3890e0e436b))


## v1.2.1 (2026-04-04)

### Bug Fixes

- **ci**: Backfill CHANGELOG.md with release history and insertion flag
  ([`f816f62`](https://github.com/asermax/tachikoma/commit/f816f62a5d7c11586b5b1ba055d5231e597e0749))


## v1.2.0 (2026-04-04)

### Chores

- Bump version to 1.0.2
  ([`848a046`](https://github.com/asermax/tachikoma/commit/848a04604c5bbc7fe973d6a0e9ff51e5939fa816))

- **planning**: Clean up DLT-072 delta artifacts
  ([`f084a3f`](https://github.com/asermax/tachikoma/commit/f084a3fa56ab7d030fade46891e6eae9bfb9c0ba))

### Documentation

- **planning**: Add design for DLT-072 task management MCP tool bugs
  ([`24cc220`](https://github.com/asermax/tachikoma/commit/24cc2202abfb15b9fc1399f272f14f5ef24f65d6))

- **planning**: Add implementation plan for DLT-072
  ([`6523e8d`](https://github.com/asermax/tachikoma/commit/6523e8db6a8c656f530299cb4ef739e16875fee5))

- **planning**: Add spec for DLT-072 task management MCP tool bugs
  ([`fa9bb7d`](https://github.com/asermax/tachikoma/commit/fa9bb7de8e02a41f45365e496501e7e578dd699a))

- **planning**: Approve DLT-072 design
  ([`bfec1c6`](https://github.com/asermax/tachikoma/commit/bfec1c646f73772f9bdad5cf306661a47f4ffd56))

- **planning**: Approve DLT-072 plan
  ([`71fe243`](https://github.com/asermax/tachikoma/commit/71fe243aa1753a9e99ab69f7f2c7cf1ef252feb7))

- **planning**: Flesh out DLT-072 design with detailed shape and decisions
  ([`08222b2`](https://github.com/asermax/tachikoma/commit/08222b247d85d290c4b460baf4fa02c6cbe2f178))

- **planning**: Mark DLT-072 as complete
  ([`13eb1e0`](https://github.com/asermax/tachikoma/commit/13eb1e043f965fe965cf2ef4547b5d42cdb722e8))

- **planning**: Update DLT-072 status to design
  ([`3b35b39`](https://github.com/asermax/tachikoma/commit/3b35b3940467f7acdb56528e4a6a6facd65f052f))

- **planning**: Update DLT-072 status to implementation
  ([`abce822`](https://github.com/asermax/tachikoma/commit/abce82296ae9bd10181965340e13adb8f225a4a1))

- **planning**: Update DLT-072 status to plan
  ([`3107b08`](https://github.com/asermax/tachikoma/commit/3107b086e0eb02dd2b89f000f02475daf7b361c0))

- **planning**: Update DLT-072 status to spec
  ([`4b295a2`](https://github.com/asermax/tachikoma/commit/4b295a21f842c5713b09f7b54e23f9c87f5b89e8))

- **tasks**: Reconcile feature specs and designs after DLT-072
  ([`8a93620`](https://github.com/asermax/tachikoma/commit/8a9362040b56e4c4c59f8f899756dd40c1b66dfc))

### Features

- **tasks**: Add task ID to list output, task_type update field, and improve error surfacing
  ([`6b38a29`](https://github.com/asermax/tachikoma/commit/6b38a2987daf69e31e0383e6a7c69bdbc9663047))

- **tasks**: Enrich MCP tool descriptions with full parameter documentation
  ([`d0b70a3`](https://github.com/asermax/tachikoma/commit/d0b70a38b8fc6e4657592660e72aa925ad2f3a45))


## v1.1.0 (2026-04-04)

### Chores

- Update lockfile for version 1.0.3
  ([`ea16833`](https://github.com/asermax/tachikoma/commit/ea168338e4ed9e6c2a10025f6c7d92e2fdab77c5))

### Documentation

- **planning**: Add DLT-097 for git sync with remotes
  ([`e347cec`](https://github.com/asermax/tachikoma/commit/e347cecd1843d97ebd88b342065e7d9d4f341623))


## v1.0.3 (2026-04-01)

### Bug Fixes

- **sessions**: Fix transcript path derivation and defensive boundary handling
  ([`e1b2ed1`](https://github.com/asermax/tachikoma/commit/e1b2ed1d840489c96c93098d04ff1665b67a2915))

### Chores

- Bump version to 1.0.2
  ([`dd666a2`](https://github.com/asermax/tachikoma/commit/dd666a2d9a5b6b3aa107a046199cd010d831c5e2))

- Bump version to 1.0.2
  ([`cca7301`](https://github.com/asermax/tachikoma/commit/cca7301369e8ecd002a8752d8932413528ae67e0))

- Bump version to 1.0.2
  ([`c8a3231`](https://github.com/asermax/tachikoma/commit/c8a323138909cd3be751d593650be7c9b832dc01))

- **config**: Remove outdated comment from system disallowed tools
  ([`a6a68d4`](https://github.com/asermax/tachikoma/commit/a6a68d46a41dc0b7a323034e8b00b3e4fd10ceea))

### Documentation

- Reconcile DLT-073 into feature specs and designs
  ([`ab55743`](https://github.com/asermax/tachikoma/commit/ab557439ac0a391b6d4f0ae08e1cd00c2daecd9d))

- Remove DLT-087 delta working documents
  ([`812d683`](https://github.com/asermax/tachikoma/commit/812d683516dbd86fef7fea90e91fa7e421e1ed04))

- Remove DLT-090 delta files after reconciliation
  ([`4b86e44`](https://github.com/asermax/tachikoma/commit/4b86e44f84814b5eca22b74ffbcaf1e0dd4b7d61))

- **config**: Reconcile DLT-087 into feature documentation
  ([`93179b5`](https://github.com/asermax/tachikoma/commit/93179b535710f6ac5a3763c210ffd84072407663))

- **planning**: Add DLT-073 implementation plan
  ([`53be151`](https://github.com/asermax/tachikoma/commit/53be15137a6157046446e0e86bd9273922b5f1c0))

- **planning**: Add DLT-096 and lower error handling deltas priority
  ([`c23f0e0`](https://github.com/asermax/tachikoma/commit/c23f0e0bfa89c1e516998c2c390c050676ae1e76))

- **planning**: Add implementation plan for DLT-087
  ([`9b3dd3f`](https://github.com/asermax/tachikoma/commit/9b3dd3fbf723882923f3230d98992074cd2ddbe4))

- **planning**: Add spec and design for DLT-073
  ([`c064cff`](https://github.com/asermax/tachikoma/commit/c064cffe1aad2ccc598d0b043348f30c73612fb5))

- **planning**: Add spec and design for DLT-087
  ([`ea20b20`](https://github.com/asermax/tachikoma/commit/ea20b2068afc7f496cde3e2de4c0969d37e5c9bc))

- **planning**: Advance DLT-073 status to plan phase
  ([`a948376`](https://github.com/asermax/tachikoma/commit/a94837630d64af2c6ad392a4cc8d8ff992355974))

- **planning**: Advance DLT-073 to implementation phase
  ([`e5fd06e`](https://github.com/asermax/tachikoma/commit/e5fd06e0d2c7750c6b13dd3a8f132e47e679ee44))

- **planning**: Approve DLT-073 design
  ([`c317ee6`](https://github.com/asermax/tachikoma/commit/c317ee681eae71a4e3015235b4870967dfcca561))

- **planning**: Complete DLT-073 design content
  ([`2cf4a27`](https://github.com/asermax/tachikoma/commit/2cf4a274bb54714a413ee532a7afc6ffb6f22c40))

- **planning**: Complete DLT-087 design
  ([`e3e75a6`](https://github.com/asermax/tachikoma/commit/e3e75a6ee6faa7eda9bed55c28eed2044d558ff4))

- **planning**: Mark DLT-073 design as complete
  ([`9a35f70`](https://github.com/asermax/tachikoma/commit/9a35f70200348727c02bb63f707bf0ee128493da))

- **planning**: Mark DLT-073 plan as complete
  ([`6709262`](https://github.com/asermax/tachikoma/commit/6709262b6351114f861339de2a7a1dc274307d4e))

- **planning**: Mark DLT-073 spec as complete
  ([`6f9214d`](https://github.com/asermax/tachikoma/commit/6f9214d9764f0215391674ce2e07cd9de03758e7))

- **planning**: Mark DLT-087 as implemented
  ([`a07efee`](https://github.com/asermax/tachikoma/commit/a07efeec341ec748f5b47fee861e922b10c84365))

- **planning**: Mark DLT-087 design as in progress
  ([`f52fce7`](https://github.com/asermax/tachikoma/commit/f52fce77c2ec134524e827f589fc34c950f4e4d2))

- **planning**: Mark DLT-087 plan as complete
  ([`435b4a9`](https://github.com/asermax/tachikoma/commit/435b4a9ee520401e2ada7e0eacd9aa8f1f13e99f))

- **planning**: Mark DLT-087 plan as in progress
  ([`f0afd6f`](https://github.com/asermax/tachikoma/commit/f0afd6fbad20fd8d7bf3c6952ccab0d907e9dff6))

- **planning**: Mark DLT-087 spec as complete
  ([`abaa4a0`](https://github.com/asermax/tachikoma/commit/abaa4a0950b612adde871f3a1076231b37bbf72c))

- **planning**: Mark DLT-090 spec as complete
  ([`e121c36`](https://github.com/asermax/tachikoma/commit/e121c362d663def0cec162c4438200cec8b98109))

- **planning**: Remove DLT-073 delta documents after reconciliation
  ([`80b047b`](https://github.com/asermax/tachikoma/commit/80b047b6abba91c5fcf6a35e9dc13036c90f1d05))

- **planning**: Update DLT-073 status to spec
  ([`664c49e`](https://github.com/asermax/tachikoma/commit/664c49ef9f81e1f724a50e81b7d0f27060de46b8))

- **planning**: Update DLT-087 status to spec
  ([`79366e4`](https://github.com/asermax/tachikoma/commit/79366e41205114809e56b4bca04ebec1b1de9313))

- **planning**: Update DLT-090 status to design
  ([`25ec0d6`](https://github.com/asermax/tachikoma/commit/25ec0d6665367d9f0191819845c3afb0e23c2568))

- **planning**: Update DLT-090 status to plan
  ([`1f9eacd`](https://github.com/asermax/tachikoma/commit/1f9eacd1553affff159e527eeb7383325d639723))

- **planning**: Update DLT-090 status to spec
  ([`d3d709b`](https://github.com/asermax/tachikoma/commit/d3d709b90d33de5ea878110cc486db620ef9a6a7))

- **tasks**: Add spec and design for DLT-090 duplicate task prevention
  ([`9ecfa7e`](https://github.com/asermax/tachikoma/commit/9ecfa7eaafe32bce5cb170e20979af3e91484f8a))

- **tasks**: Approve DLT-090 design
  ([`5a8c7dd`](https://github.com/asermax/tachikoma/commit/5a8c7ddd155822dc7d00f3525c7614c247dd56c5))

- **tasks**: Complete DLT-090 design for duplicate task prevention
  ([`ff0aa83`](https://github.com/asermax/tachikoma/commit/ff0aa832ebdc35372318c7662a6cebbfa3265162))

- **tasks**: Complete DLT-090 implementation plan
  ([`0ff1151`](https://github.com/asermax/tachikoma/commit/0ff115121bf6def677107a56d349642780fedeb9))

- **tasks**: Reconcile DLT-090 into feature documentation
  ([`7da23aa`](https://github.com/asermax/tachikoma/commit/7da23aad36856d7db8d8a78afd27396e9a5560b2))

- **tasks**: Update DLT-090 status to implementation
  ([`a368175`](https://github.com/asermax/tachikoma/commit/a3681753406eb8d65010d0af617c32776cd592e5))

### Features

- **config**: Add system-level tool blocking for built-in skills
  ([`b0c0cb4`](https://github.com/asermax/tachikoma/commit/b0c0cb472a01b033f797055578edfda5ee910726))

- **config**: Block Claude Code built-in cron tools in default disallowed_tools
  ([`5a4078a`](https://github.com/asermax/tachikoma/commit/5a4078a2a530ee5087c2a9a97ce31c743c3fbfde))

- **tasks**: Add period-aware duplicate prevention for scheduled tasks
  ([`4d48fac`](https://github.com/asermax/tachikoma/commit/4d48fac1de1023b2038c6d3c21adf5754e5b3acf))

### Refactoring

- **tasks**: Extract instance creation helper and simplify catch-up logic
  ([`0a470e2`](https://github.com/asermax/tachikoma/commit/0a470e26345b8d4575b30f24a94b4188601bdd69))

### Testing

- **tasks**: Add comprehensive tests for period-aware scheduling and deduplication
  ([`9bec042`](https://github.com/asermax/tachikoma/commit/9bec0426cb6c2a7bad3677472b3517d1ced4b154))


## v1.0.2 (2026-03-31)

### Bug Fixes

- **ci**: Use specific version tag for setup-uv
  ([`97aa647`](https://github.com/asermax/tachikoma/commit/97aa64762faa77bf8b04f480ed512455bc76d65e))

### Chores

- **ci**: Update GitHub Actions to latest versions
  ([`7fa7020`](https://github.com/asermax/tachikoma/commit/7fa702047a8ec2e57e4fc2c203fd5feff35bd1cd))


## v1.0.1 (2026-03-31)

### Bug Fixes

- **sessions**: Validate transcript and age before session resumption
  ([`502ec39`](https://github.com/asermax/tachikoma/commit/502ec39e4681ff4e6c4c9b0a23caca6bd8787276))

- **tests**: Move timedelta import to module level
  ([`fb6e0e8`](https://github.com/asermax/tachikoma/commit/fb6e0e8c0c2cbf61e04ce01b697f41d878ff4eb4))

### Chores

- Remove stale .gitmodules and update lockfile
  ([`a661758`](https://github.com/asermax/tachikoma/commit/a6617583ad39144a54e8f3279b5d250e570f0b93))

- Sync uv.lock version to 1.0.0
  ([`3ef0adf`](https://github.com/asermax/tachikoma/commit/3ef0adfed29507c8d274861e01862a26b396778c))

### Documentation

- **sessions**: Document transcript validation and age-based resumption limits
  ([`01c89f5`](https://github.com/asermax/tachikoma/commit/01c89f5c98810ead1bb8cf3f0345fc00e737c33f))


## v1.0.0 (2026-03-31)

- Initial Release
