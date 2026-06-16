# [3.23.0](https://github.com/asermax/tachikoma/compare/v3.22.1...v3.23.0) (2026-06-16)


### Bug Fixes

* **skills:** strip frontmatter from proactively injected skill content ([4f5050e](https://github.com/asermax/tachikoma/commit/4f5050eeef2f1307b14e2f5aa23faec3d14267c5))


### Features

* **skills:** inject full skill content instead of recommending /skill ([f63cff0](https://github.com/asermax/tachikoma/commit/f63cff03d6c0bb51c1439d49d5059b7ba1f7dca7))

## [3.22.1](https://github.com/asermax/tachikoma/compare/v3.22.0...v3.22.1) (2026-06-16)


### Bug Fixes

* **coordinator:** suppress post-processing status on idle close ([fdc09d0](https://github.com/asermax/tachikoma/commit/fdc09d04cbbad610cb94b348615667942418430d))

# [3.22.0](https://github.com/asermax/tachikoma/compare/v3.21.0...v3.22.0) (2026-06-16)


### Features

* increase skill classify timeout from 2s to 10s ([e22f6ee](https://github.com/asermax/tachikoma/commit/e22f6ee5cb522bed17d3927b825f5a02b106d825))

# [3.21.0](https://github.com/asermax/tachikoma/compare/v3.20.0...v3.21.0) (2026-06-16)


### Features

* **tasks:** add stop_task tool to cancel running and queued instances ([5557471](https://github.com/asermax/tachikoma/commit/555747142f4fd14e46db4c11550e624521cebac7))

# [3.20.0](https://github.com/asermax/tachikoma/compare/v3.19.0...v3.20.0) (2026-06-16)


### Bug Fixes

* **projects:** use agent API in processor-mocked tests after rebase ([5a56256](https://github.com/asermax/tachikoma/commit/5a562563474912501d8d42320bc1f7bcae1d9caa))


### Features

* **git:** group commits via a headless agent with fallback ([19636e2](https://github.com/asermax/tachikoma/commit/19636e2f976d03a6506a41b5ee8535ccb5588b62))

# [3.19.0](https://github.com/asermax/tachikoma/compare/v3.18.0...v3.19.0) (2026-06-16)


### Features

* **projects:** resolve rebase conflicts via agent in sync and push ([156c3a6](https://github.com/asermax/tachikoma/commit/156c3a6d73e939b0ef31debdf01856a6e366892c))

# [3.18.0](https://github.com/asermax/tachikoma/compare/v3.17.0...v3.18.0) (2026-06-15)


### Features

* **telegram:** surface unrecorded reply/reaction targets ([3b172b1](https://github.com/asermax/tachikoma/commit/3b172b1123a530b087700c38c046b3ad96849f1a))

# [3.17.0](https://github.com/asermax/tachikoma/compare/v3.16.0...v3.17.0) (2026-06-15)


### Features

* **telegram:** render send_message_with_buttons prompt as entities ([00f3bef](https://github.com/asermax/tachikoma/commit/00f3bef8a0cdd317780605998bbf0426e78d18ec))

# [3.16.0](https://github.com/asermax/tachikoma/compare/v3.15.0...v3.16.0) (2026-06-15)


### Bug Fixes

* **coordinator:** update quarantine test for recoverUnprocessedSessions rename ([e4352e3](https://github.com/asermax/tachikoma/commit/e4352e3e18710e7aacac4f19658cac6fcc728d27))


### Features

* **shutdown:** drain post-processing on uncaught errors before exit ([914a595](https://github.com/asermax/tachikoma/commit/914a595133f54192dca4967bbbc624cbc4fa55be))

# [3.15.0](https://github.com/asermax/tachikoma/compare/v3.14.0...v3.15.0) (2026-06-15)


### Features

* **sessions:** quarantine sessions that hit encoding errors ([09e2724](https://github.com/asermax/tachikoma/commit/09e2724b18984fb8d38dddb800082131504bfea5))

# [3.14.0](https://github.com/asermax/tachikoma/compare/v3.13.0...v3.14.0) (2026-06-15)


### Features

* **telegram:** prepend context to inbound for older references ([037dc8d](https://github.com/asermax/tachikoma/commit/037dc8d051513feaa752876dfb1e16b45f848492))

# [3.13.0](https://github.com/asermax/tachikoma/compare/v3.12.0...v3.13.0) (2026-06-15)


### Features

* add prepareArguments to bash-description extension ([4bb6b2b](https://github.com/asermax/tachikoma/commit/4bb6b2b524e9841139c70493773a3c9e33d12aca))

# [3.12.0](https://github.com/asermax/tachikoma/compare/v3.11.0...v3.12.0) (2026-06-15)


### Features

* **telegram:** add ls tool labels ([e1fc7be](https://github.com/asermax/tachikoma/commit/e1fc7be245518e5e2a59c266e2d245d96dce8642))

# [3.11.0](https://github.com/asermax/tachikoma/compare/v3.10.0...v3.11.0) (2026-06-15)


### Features

* **projects:** push clean-ahead submodules on close ([ba521f2](https://github.com/asermax/tachikoma/commit/ba521f2a5c22aba360bc28509bf1312a0c85995a))

# [3.10.0](https://github.com/asermax/tachikoma/compare/v3.9.0...v3.10.0) (2026-06-15)


### Features

* **git:** push workspace and ahead projects from commit_workspace ([d36ca19](https://github.com/asermax/tachikoma/commit/d36ca194c787095da8a9544cc6b7dd7acbfafead))

# [3.9.0](https://github.com/asermax/tachikoma/compare/v3.8.0...v3.9.0) (2026-06-15)


### Features

* **git:** scrub a project's history, not just the workspace ([3e33449](https://github.com/asermax/tachikoma/commit/3e3344982a336320748e8dbec419e3c7cf8aa47a))

# [3.8.0](https://github.com/asermax/tachikoma/compare/v3.7.0...v3.8.0) (2026-06-15)


### Features

* **config:** read app-wide env variables from config ([7b8b43d](https://github.com/asermax/tachikoma/commit/7b8b43dfcf8cf5570bc8519f8f8b77b83e6bfc46))

# [3.7.0](https://github.com/asermax/tachikoma/compare/v3.6.0...v3.7.0) (2026-06-15)


### Features

* **agent:** expose isForking() to scope per-turn work out of forks ([dff3740](https://github.com/asermax/tachikoma/commit/dff3740c9a642214c332336dba87872a4ad5fd34))
* **skills:** proactively recommend relevant skills per turn ([4b84491](https://github.com/asermax/tachikoma/commit/4b844912c2a16ca3796bc0b244fc272b38882da3))

# [3.6.0](https://github.com/asermax/tachikoma/compare/v3.5.0...v3.6.0) (2026-06-14)


### Features

* **skills:** add display-only description to delegate_to_agent ([6035f84](https://github.com/asermax/tachikoma/commit/6035f84852c738fe7aaa32aae3dc370f657e3ea0))

# [3.5.0](https://github.com/asermax/tachikoma/compare/v3.4.0...v3.5.0) (2026-06-14)


### Features

* **telegram:** flatten GFM tables to bullets before MarkdownV2 conversion ([1ef3d0e](https://github.com/asermax/tachikoma/commit/1ef3d0ef2d94d2ec433dc59702ec41d3c30b9a22))

# [3.4.0](https://github.com/asermax/tachikoma/compare/v3.3.0...v3.4.0) (2026-06-14)


### Features

* **telegram:** avoid redundant push notifications on streamed responses ([19837cd](https://github.com/asermax/tachikoma/commit/19837cde2370eab4d4644e942a7b4226c74e8591))
* **telegram:** force a push notification for streamed responses ([22af526](https://github.com/asermax/tachikoma/commit/22af526189922ceb99ce4a4a3c9124283c0b73f9))

# [3.3.0](https://github.com/asermax/tachikoma/compare/v3.2.0...v3.3.0) (2026-06-14)


### Bug Fixes

* **telegram:** escape backticks in inline-code tool labels ([e75d9c7](https://github.com/asermax/tachikoma/commit/e75d9c7a4affb4a2eca24e297978b05b304c6aea))


### Features

* **telegram:** prefer Bash description over command in live label ([f10b457](https://github.com/asermax/tachikoma/commit/f10b4577a472cca5214f5084a80d3210860e5675))

# [3.2.0](https://github.com/asermax/tachikoma/compare/v3.1.2...v3.2.0) (2026-06-14)


### Features

* **telegram:** surface preparation status via a reclaimed lead-in message ([3c9f6d8](https://github.com/asermax/tachikoma/commit/3c9f6d81ff33fc47fde68d5b2c61f8897f710ab7))

## [3.1.2](https://github.com/asermax/tachikoma/compare/v3.1.1...v3.1.2) (2026-06-14)


### Bug Fixes

* **skills:** bundle authoring skills inside the skills extension ([a33e145](https://github.com/asermax/tachikoma/commit/a33e145eead080f60685d11b71a611f6ff7d0c7c))

## [3.1.1](https://github.com/asermax/tachikoma/compare/v3.1.0...v3.1.1) (2026-06-14)


### Bug Fixes

* **agent:** inject persistent context on before_agent_start ([f196363](https://github.com/asermax/tachikoma/commit/f1963636e2615fb7635070c64fd937204921d0ef))

# [3.1.0](https://github.com/asermax/tachikoma/compare/v3.0.0...v3.1.0) (2026-06-14)


### Bug Fixes

* **telegram:** map tool labels to pi tool names and italicize markers ([60c80dd](https://github.com/asermax/tachikoma/commit/60c80dd09e817e7821190e36262a343b050dc7a8))


### Features

* **telegram:** render responses as MarkdownV2 via telegramify-markdown ([f5aff9a](https://github.com/asermax/tachikoma/commit/f5aff9a4b5ca8425988ab45fbaef6df321b6b3cd))

# [3.0.0](https://github.com/asermax/tachikoma/compare/v2.4.0...v3.0.0) (2026-06-14)


### Bug Fixes

* **cli:** tolerate a stray positional argument ([05888bf](https://github.com/asermax/tachikoma/commit/05888bfc264f053ca8d85120d5f1d49f3fd18732))
* **context:** delete header-only pending-signals file instead of warning ([9ce0461](https://github.com/asermax/tachikoma/commit/9ce0461549a3a172e3f48a3be4e1aa32af4e4ffd))
* **telegram:** restore legacy message rendering ([467320a](https://github.com/asermax/tachikoma/commit/467320a3157840342e7e70efb1ceb2c02b17b824))


### Features

* **agent:** inject extension context via persisted before_agent_start sections ([2f0cb93](https://github.com/asermax/tachikoma/commit/2f0cb93f042bd19dc20b52465c504f97366c7f06))
* **agent:** prompt main and background roles to evaluate skills ([7b9bc47](https://github.com/asermax/tachikoma/commit/7b9bc47dd8edc9212232b58d24a19e23c3eaa89b))
* **delivery:** queue background notifications as tiered agent turns ([70918d1](https://github.com/asermax/tachikoma/commit/70918d1242b7bc575d65079f3b00795989bff838))


### BREAKING CHANGES

* **delivery:** app.channels.deliver now takes { text, tier?, immediate?, metadata? } instead of gate/target/priority/maxHoldSeconds; the [extensions.notifications].maxHoldSeconds and [extensions.tasks].sessionTaskMaxHoldSeconds config keys are removed (timing is now per-tier in the coordinator).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>

# [2.4.0](https://github.com/asermax/tachikoma/compare/v2.3.0...v2.4.0) (2026-06-14)


### Bug Fixes

* **config:** raise TOML syntax errors as ConfigError ([c770c6d](https://github.com/asermax/tachikoma/commit/c770c6dcb5a887c510e800edd1844416367ae85b))
* **tasks:** wrap one-shot prune deletes in a transaction ([f3c838c](https://github.com/asermax/tachikoma/commit/f3c838c326e7dfbc0aa7561faa053dcf5a523486))


### Features

* **agent:** add forkAndContinue for conversation-aware post-processing ([7cd35d0](https://github.com/asermax/tachikoma/commit/7cd35d0ef6657f89cba79401e1bcc2b8bf4f72d9))
* **log:** redact sensitive fields from log output ([4f55466](https://github.com/asermax/tachikoma/commit/4f55466bb726edf81bf701a4406e59091b3645de))
* **log:** rotate logs continuously with pino-roll ([6ce81af](https://github.com/asermax/tachikoma/commit/6ce81af63e2af4922c541808f528c754f04dc78a))
* **memory:** fork the session for memory and core-context extraction ([c28f6aa](https://github.com/asermax/tachikoma/commit/c28f6aafbed3f2689fbef0942dd7503c5c89f2ed))
* **telegram:** retry on rate limits and discriminate API errors ([85c2a55](https://github.com/asermax/tachikoma/commit/85c2a55488e4968704d9219384b257f2fccdc43f))

# [2.3.0](https://github.com/asermax/tachikoma/compare/v2.2.0...v2.3.0) (2026-06-13)


### Features

* **extensions:** scope tool factories to sessions via sessionScopes ([eb6bd52](https://github.com/asermax/tachikoma/commit/eb6bd5271fe534caf2731c33e9f028a762e2818f))
* **log:** persist structured logs to a rotated file for daemon runs ([b2f6902](https://github.com/asermax/tachikoma/commit/b2f69020b1ce57eeaa35861afad37f8cc085d8a7))
* **notifications:** drain held + pending notices on shutdown ([4d7df69](https://github.com/asermax/tachikoma/commit/4d7df693fba9fd85e13b4b4a78a5457c5b80a854))
* **scheduler:** single-flight every() interval jobs ([b9085bc](https://github.com/asermax/tachikoma/commit/b9085bc836e6290dece59f2d6d6987087c5f0b72))
* **tasks:** run background tasks on a persistent pi session ([b44a48c](https://github.com/asermax/tachikoma/commit/b44a48cd0f64943f52eb305774124d58c3b7ee7a))

# [2.2.0](https://github.com/asermax/tachikoma/compare/v2.1.0...v2.2.0) (2026-06-13)


### Bug Fixes

* **boundary:** skip the rolling summary on an empty assistant turn ([274fcb6](https://github.com/asermax/tachikoma/commit/274fcb6f60470b6cf47aed7ef6d399e9a1786079))
* **git:** validate scrub paths before the filter-repo availability check ([867813f](https://github.com/asermax/tachikoma/commit/867813f865f58f51618f044df475185b2d1e8185))
* **tasks:** keep respond_to_task out of background task runs ([117c762](https://github.com/asermax/tachikoma/commit/117c762c4eeb875994343cc27b52ead42f0fcc1d))
* **workflows:** cascade stale cleanup so nested runs leave no orphan rows ([a6f4f1b](https://github.com/asermax/tachikoma/commit/a6f4f1bf053f8283418cd00d4c373a1f88cd6ec3))


### Features

* **agent:** restore result accounting and error classification in the adapter ([01ec72d](https://github.com/asermax/tachikoma/commit/01ec72d9afe74041dbbc7cabcde24801df8f55ea))
* **agent:** role-specific system prompts and a general-purpose delegation subagent ([10a9d8e](https://github.com/asermax/tachikoma/commit/10a9d8eb2afed2083705dfeaea63e9aa13d98146))
* **config:** validate scheduler timezone as an IANA zone ([a80629f](https://github.com/asermax/tachikoma/commit/a80629fc6680ed02b072b0b1b01adc135b282e6c))
* **detached-processes:** add rename tool and windowed output reads ([e7b00ca](https://github.com/asermax/tachikoma/commit/e7b00ca5885065a5445d937792cc17c16634b4f2))
* **detached-processes:** attribute OOM kills and report live memory ([6fa4f30](https://github.com/asermax/tachikoma/commit/6fa4f30f69b0ce228e33354bf9e1edeb8ca50c87))
* **detached-processes:** delete tool, RAM ceiling, eager exit, urgent OOM ([772dee9](https://github.com/asermax/tachikoma/commit/772dee9951c0719b1194fc6bc2c8f8fb8503a50f))
* **external:** isolate third-party extension startup failures ([e6e98d9](https://github.com/asermax/tachikoma/commit/e6e98d96a2c0622cb1db7b1a089b297be279113f))
* **git:** add scrub tool and destructive-git guardrails ([b9c0012](https://github.com/asermax/tachikoma/commit/b9c0012bfaed4e7a3bc6817ebd938272c549459c))
* **git:** resolve rebase conflicts with a side agent, abort as fallback ([b814fd6](https://github.com/asermax/tachikoma/commit/b814fd6987d74bbfc4ecfebbd735e5b3fbec25da))
* **memory:** commit changes from the maintenance tick ([079c135](https://github.com/asermax/tachikoma/commit/079c135b6a51500499eb7901c10b3e84976d6220))
* **memory:** periodically maintain the foundational context files ([d1cfc17](https://github.com/asermax/tachikoma/commit/d1cfc17a4c96958a23b5ce05bb3e83dc9d102ef7))
* **memory:** prune archived transcripts by age ([fe435e1](https://github.com/asermax/tachikoma/commit/fe435e14e9b93fd27490ba170637be7e946a7913))
* **memory:** restore skill-awareness in memory and context prompts ([9539502](https://github.com/asermax/tachikoma/commit/95395021392d04de3d7a485420a67ea119e28055))
* **notifications:** order deliveries by severity priority ([90f3248](https://github.com/asermax/tachikoma/commit/90f32487dbd26b5bc031b9f1028b650b93611f0e))
* **notifications:** suppress duplicate notifications within a TTL ([99d86b2](https://github.com/asermax/tachikoma/commit/99d86b27fb251657747fd3716bb84a10ef661b04))
* **repl:** render markdown and abort the exchange on Ctrl-C ([56c2a8a](https://github.com/asermax/tachikoma/commit/56c2a8ac9318450791bf1975270050571e11883a))
* **self-update:** in-app version check, upgrade, restart and rollback ([83ff63f](https://github.com/asermax/tachikoma/commit/83ff63f065c7546cdca371b74a8fcd9544133ae7))
* **self-update:** standalone restart tool and dev-install upgrade guard ([30c241a](https://github.com/asermax/tachikoma/commit/30c241acbcacd11e0a739883bf9fc92df5270de3))
* **sessions:** harden resume and restore bridging context ([1c6437e](https://github.com/asermax/tachikoma/commit/1c6437e4bf5d2a49aefcf8ea79867e817c34720b))
* **skills:** honor agent model frontmatter in delegation ([8fcf89f](https://github.com/asermax/tachikoma/commit/8fcf89f1c0bf52122254c736d0cad10ea3d46f5e))
* **skills:** warn on malformed agent tools frontmatter ([b41cd1d](https://github.com/asermax/tachikoma/commit/b41cd1d09aa4b244b596e382798e986a186122c5))
* **tasks:** cap concurrent background task executions ([f36e479](https://github.com/asermax/tachikoma/commit/f36e47938d9b3e1de237d9475792996687fedd80))
* **tasks:** give background runs a curated extension toolset (parity phase A) ([4eddf92](https://github.com/asermax/tachikoma/commit/4eddf9220b0c77d502efa3ce40678a631629804a))
* **tasks:** inject workspace context and run post-processors for background tasks ([8019e83](https://github.com/asermax/tachikoma/commit/8019e83d98a765de703ffdfd47578819fbdc2139))
* **tasks:** make the interactive await/respond flow live ([1e975c4](https://github.com/asermax/tachikoma/commit/1e975c4f4cf52758790d46aad090099a04336bc3))
* **tasks:** re-add run_task_now, get_task and delete_task tools ([1cd2e04](https://github.com/asermax/tachikoma/commit/1cd2e04b9c2fecb763a9110e38bc59f000305a4d))
* **tasks:** sweep stuck-running instances and prune old one-shots ([f175848](https://github.com/asermax/tachikoma/commit/f17584819151ba1ed4e4ef5047de7d5a9a3a5215))
* **telegram:** friendly tool-activity labels and button reply-to routing ([aa4767d](https://github.com/asermax/tachikoma/commit/aa4767d8934e71cf9e6b009110f9d467db206394))
* **telegram:** inbound reactions and reply-to session routing ([f7fee7f](https://github.com/asermax/tachikoma/commit/f7fee7f04efecf569bef954ab7844990b6908560))
* **telegram:** surface /new and /queue commands ([fcd8fbd](https://github.com/asermax/tachikoma/commit/fcd8fbd1a4acec39bd7beb31997d1b9e6f4a3138))
* **workflows:** support composes/loop/condition step composition ([e4cecd7](https://github.com/asermax/tachikoma/commit/e4cecd7402c057eaaeb929a4d0f4e4116fd86f4f))

# [2.1.0](https://github.com/asermax/tachikoma/compare/v2.0.1...v2.1.0) (2026-06-13)


### Features

* **migration:** port legacy task definitions into the new database ([9aa9553](https://github.com/asermax/tachikoma/commit/9aa95532deb984245dea6779c102f6ae36ccebe9))

## [2.0.1](https://github.com/asermax/tachikoma/compare/v2.0.0...v2.0.1) (2026-06-13)


### Bug Fixes

* ship pino-pretty as a runtime dependency ([37f6953](https://github.com/asermax/tachikoma/commit/37f69535d3edb1da35440b00657d18dfa032d584))
