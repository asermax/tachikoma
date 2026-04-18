# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- version list -->

## v1.25.0 (2026-04-18)


## v1.24.0 (2026-04-18)

### Documentation

- **planning**: Add deltas from shin-sekai review and refocus DLT-048
  ([`7095d91`](https://github.com/asermax/tachikoma/commit/7095d9150bd6548c467fd0ccb9b0d3c26009b960))

- **planning**: Remove reconciled DLT-124 from inventory
  ([`888b216`](https://github.com/asermax/tachikoma/commit/888b216c84d766c559805152ad65c82f405b6316))

### Features

- **skills**: Add scripting and testing references to authoring guide
  ([`aa9477c`](https://github.com/asermax/tachikoma/commit/aa9477c33d13d9cad2ea72a9fd7515da67b7b157))


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

- Add DLT-142 delta for session filtering
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

### Features

- **git**: Add compound command splitting and cd/pwd to bash gate hook
  ([`7091e22`](https://github.com/asermax/tachikoma/commit/7091e222189ce8426fb3d5816302129af18528fd))


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

- **tasks**: Fix UTC handling in task CRUD operations
  ([`ddec1ad`](https://github.com/asermax/tachikoma/commit/ddec1add9083697a88a89ada5719e76418c2327a))

### Chores

- **deltas**: Add DLT-123 through DLT-133 from improvements triage
  ([`28e86b4`](https://github.com/asermax/tachikoma/commit/28e86b4035b4c1d28cd7aa06f2ece8dcd8a222ef))

- **deltas**: Escalate DLT-097, DLT-131, DLT-132 to Critical priority
  ([`e203a12`](https://github.com/asermax/tachikoma/commit/e203a12c3cc390fe22492b566d53bbdd475add13))

- **deltas**: Remove DLT-131 after reconciliation
  ([`8e57d8a`](https://github.com/asermax/tachikoma/commit/8e57d8a685e5587b2f010ae87dd4980cc2259650))

- **deltas**: Remove DLT-132 after reconciliation
  ([`3caf19e`](https://github.com/asermax/tachikoma/commit/3caf19e842afcc5fafbd2d22a2e9fb5aaf98fce7))

- **deltas**: Reorder DLT-134 dependencies alphabetically
  ([`d6ed2a3`](https://github.com/asermax/tachikoma/commit/d6ed2a3fc9941a6c0e7d7568d9ad4fe38e255e1a))

### Documentation

- **memory**: Reconcile max_turns increase into feature design
  ([`253462a`](https://github.com/asermax/tachikoma/commit/253462a8d59975acb855d680a608bcbfc898e998))

- **tasks**: Reconcile UTC handling fixes into feature documentation
  ([`0605736`](https://github.com/asermax/tachikoma/commit/0605736e5172fead1ee26ce9a63450da99b4d7c3))


## v1.14.2 (2026-04-09)

### Bug Fixes

- **memory**: Harden memory search agent with explicit tools and scope guardrails
  ([`2e2c091`](https://github.com/asermax/tachikoma/commit/2e2c091f784941a7b7abdbf39dfa55f178338bc2))


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
