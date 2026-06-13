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
