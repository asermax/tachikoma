# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- version list -->

## v1.8.0 (2026-04-07)

### Documentation

- Remove on_status references from architecture docs
  ([`4fff5a2`](https://github.com/asermax/tachikoma/commit/4fff5a25fdea0d70203c126fbebd05797d628fa9))

- **planning**: Add DLT-111, DLT-112, DLT-113 and update DLT-081
  ([`2f9b38b`](https://github.com/asermax/tachikoma/commit/2f9b38b2b39e6017cd67a15e4896163b38055334))

- **planning**: Remove DLT-090 (already implemented)
  ([`9f6d46b`](https://github.com/asermax/tachikoma/commit/9f6d46ba73e2c03f07b85b0725e8eab39a4aa461))

- **planning**: Reprioritize deltas around workflows and usability
  ([`aa42a42`](https://github.com/asermax/tachikoma/commit/aa42a425a1b1f9dc2cce784b7d331438684c108a))

### Features

- **tasks**: Add get_task tool and remove prompt truncation from list_tasks
  ([`02c5951`](https://github.com/asermax/tachikoma/commit/02c595144810024fb08466e90d631c191bd639e2))

### Refactoring

- **coordinator**: Remove on_status callback and processing memories text
  ([`017a198`](https://github.com/asermax/tachikoma/commit/017a1983b5064c9e09626f4509c5446ae3c04df8))


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

- Update lockfile for version 1.3.1
  ([`222be73`](https://github.com/asermax/tachikoma/commit/222be73a21830a56cede5806fa30b6feae1528cd))

### Documentation

- **tasks**: Replace DLT-091 with agent-driven notification tool
  ([`107dc5e`](https://github.com/asermax/tachikoma/commit/107dc5eab6a5c728545477ca39e591378bce6cea))


## v1.3.1 (2026-04-04)

### Bug Fixes

- **tasks**: Make schedule deserialization robust to malformed data
  ([`790613b`](https://github.com/asermax/tachikoma/commit/790613baaabac0ef55d7d80ff33c7913b4185cb3))

### Documentation

- **tasks**: Add schedule deserialization robustness to feature documentation
  ([`1de898e`](https://github.com/asermax/tachikoma/commit/1de898e44829da375561dd2e7eee505e9c159226))


## v1.3.0 (2026-04-04)

### Documentation

- **planning**: Add DLT-098 for SDK stderr capture on error
  ([`822e746`](https://github.com/asermax/tachikoma/commit/822e7466463247d78e703be95672e3890e0e436b))


## v1.2.1 (2026-04-04)

### Bug Fixes

- **ci**: Backfill CHANGELOG.md with release history and insertion flag
  ([`f816f62`](https://github.com/asermax/tachikoma/commit/f816f62a5d7c11586b5b1ba055d5231e597e0749))


## v1.2.0 (2026-04-04)

### Chores

- Bump version to 1.0.2 ([`848a046`](https://github.com/asermax/tachikoma/commit/848a04604c5bbc7fe973d6a0e9ff51e5939fa816))

- **planning**: Clean up DLT-072 delta artifacts ([`f084a3f`](https://github.com/asermax/tachikoma/commit/f084a3fa56ab7d030fade46891e6eae9bfb9c0ba))

### Documentation

- **planning**: Add design for DLT-072 task management MCP tool bugs ([`24cc220`](https://github.com/asermax/tachikoma/commit/24cc2202abfb15b9fc1399f272f14f5ef24f65d6))

- **planning**: Add implementation plan for DLT-072 ([`6523e8d`](https://github.com/asermax/tachikoma/commit/6523e8db6a8c656f530299cb4ef739e16875fee5))

- **planning**: Add spec for DLT-072 task management MCP tool bugs ([`fa9bb7d`](https://github.com/asermax/tachikoma/commit/fa9bb7de8e02a41f45365e496501e7e578dd699a))

- **planning**: Approve DLT-072 design ([`bfec1c6`](https://github.com/asermax/tachikoma/commit/bfec1c646f73772f9bdad5cf306661a47f4ffd56))

- **planning**: Approve DLT-072 plan ([`71fe243`](https://github.com/asermax/tachikoma/commit/71fe243aa1753a9e99ab69f7f2c7cf1ef252feb7))

- **planning**: Flesh out DLT-072 design with detailed shape and decisions ([`08222b2`](https://github.com/asermax/tachikoma/commit/08222b247d85d290c4b460baf4fa02c6cbe2f178))

- **planning**: Mark DLT-072 as complete ([`13eb1e0`](https://github.com/asermax/tachikoma/commit/13eb1e043f965fe965cf2ef4547b5d42cdb722e8))

- **planning**: Update DLT-072 status to design ([`3b35b39`](https://github.com/asermax/tachikoma/commit/3b35b3940467f7acdb56528e4a6a6facd65f052f))

- **planning**: Update DLT-072 status to implementation ([`abce822`](https://github.com/asermax/tachikoma/commit/abce82296ae9bd10181965340e13adb8f225a4a1))

- **planning**: Update DLT-072 status to plan ([`3107b08`](https://github.com/asermax/tachikoma/commit/3107b086e0eb02dd2b89f000f02475daf7b361c0))

- **planning**: Update DLT-072 status to spec ([`4b295a2`](https://github.com/asermax/tachikoma/commit/4b295a21f842c5713b09f7b54e23f9c87f5b89e8))

- **tasks**: Reconcile feature specs and designs after DLT-072 ([`8a93620`](https://github.com/asermax/tachikoma/commit/8a9362040b56e4c4c59f8f899756dd40c1b66dfc))

### Features

- **tasks**: Add task ID to list output, task_type update field, and improve error surfacing ([`6b38a29`](https://github.com/asermax/tachikoma/commit/6b38a2987daf69e31e0383e6a7c69bdbc9663047))

- **tasks**: Enrich MCP tool descriptions with full parameter documentation ([`d0b70a3`](https://github.com/asermax/tachikoma/commit/d0b70a38b8fc6e4657592660e72aa925ad2f3a45))

---

**Detailed Changes**: [v1.1.0...v1.2.0](https://github.com/asermax/tachikoma/compare/v1.1.0...v1.2.0)

## v1.1.0 (2026-04-04)

### Chores

- Update lockfile for version 1.0.3 ([`ea16833`](https://github.com/asermax/tachikoma/commit/ea168338e4ed9e6c2a10025f6c7d92e2fdab77c5))

### Documentation

- **planning**: Add DLT-097 for git sync with remotes ([`e347cec`](https://github.com/asermax/tachikoma/commit/e347cecd1843d97ebd88b342065e7d9d4f341623))

---

**Detailed Changes**: [v1.0.3...v1.1.0](https://github.com/asermax/tachikoma/compare/v1.0.3...v1.1.0)

## v1.0.3 (2026-04-01)

### Bug Fixes

- **sessions**: Fix transcript path derivation and defensive boundary handling ([`e1b2ed1`](https://github.com/asermax/tachikoma/commit/e1b2ed1d840489c96c93098d04ff1665b67a2915))

### Documentation

- **planning**: Add DLT-096 and lower error handling deltas priority ([`c23f0e0`](https://github.com/asermax/tachikoma/commit/c23f0e0bfa89c1e516998c2c390c050676ae1e76))

---

**Detailed Changes**: [v1.0.2...v1.0.3](https://github.com/asermax/tachikoma/compare/v1.0.2...v1.0.3)

## v1.0.2 (2026-03-31)

### Bug Fixes

- **ci**: Use specific version tag for setup-uv ([`97aa647`](https://github.com/asermax/tachikoma/commit/97aa64762faa77bf8b04f480ed512455bc76d65e))

### Chores

- **ci**: Update GitHub Actions to latest versions ([`7fa7020`](https://github.com/asermax/tachikoma/commit/7fa702047a8ec2e57e4fc2c203fd5feff35bd1cd))

---

**Detailed Changes**: [v1.0.1...v1.0.2](https://github.com/asermax/tachikoma/compare/v1.0.1...v1.0.2)

## v1.0.1 (2026-03-31)

### Bug Fixes

- **sessions**: Validate transcript and age before session resumption ([`502ec39`](https://github.com/asermax/tachikoma/commit/502ec39e4681ff4e6c4c9b0a23caca6bd8787276))

- **tests**: Move timedelta import to module level ([`fb6e0e8`](https://github.com/asermax/tachikoma/commit/fb6e0e8c0c2cbf61e04ce01b697f41d878ff4eb4))

### Chores

- Remove stale .gitmodules and update lockfile ([`a661758`](https://github.com/asermax/tachikoma/commit/a6617583ad39144a54e8f3279b5d250e570f0b93))

- Sync uv.lock version to 1.0.0 ([`3ef0adf`](https://github.com/asermax/tachikoma/commit/3ef0adfed29507c8d274861e01862a26b396778c))

### Documentation

- **sessions**: Document transcript validation and age-based resumption limits ([`01c89f5`](https://github.com/asermax/tachikoma/commit/01c89f5c98810ead1bb8cf3f0345fc00e737c33f))

---

**Detailed Changes**: [v1.0.0...v1.0.1](https://github.com/asermax/tachikoma/compare/v1.0.0...v1.0.1)

## v1.0.0 (2026-03-31)

- Initial Release
