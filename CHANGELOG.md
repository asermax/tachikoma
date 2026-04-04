# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- version list -->

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
