# [3.62.0](https://github.com/asermax/tachikoma/compare/v3.61.0...v3.62.0) (2026-07-19)


### Features

* **tasks:** add goal column and thread it through task creation sites ([1667ac5](https://github.com/asermax/tachikoma/commit/1667ac5defa757525a66badb1d2e9259589d37d7))
* **tasks:** document goal meaning and structure on agent-facing surfaces ([2942281](https://github.com/asermax/tachikoma/commit/29422815741954191c28ca6bfd10d5786e065182))
* **tasks:** drop iteration-cap note from goal guidance ([1aef1e5](https://github.com/asermax/tachikoma/commit/1aef1e553ef33b729d9d00bb8833cf69f711c66e))
* **tasks:** extract completion goal at run start and surface it ([4c6715f](https://github.com/asermax/tachikoma/commit/4c6715f7f9947d6c84d4f5a2a712565eed953d65))
* **tasks:** replace evaluator loop with goal self-declaration ([6575398](https://github.com/asermax/tachikoma/commit/6575398a041e7c164b34c19030fe04eec00b7edc))

# [3.61.0](https://github.com/asermax/tachikoma/compare/v3.60.0...v3.61.0) (2026-07-11)


### Features

* **skills:** re-anchor already-injected skills instead of re-injecting ([b45984e](https://github.com/asermax/tachikoma/commit/b45984e82767d5d68159d64d4cd60dcbd729e407))

# [3.60.0](https://github.com/asermax/tachikoma/compare/v3.59.0...v3.60.0) (2026-07-11)


### Features

* **boundary:** gate set-checkpoint on an open topic ([36cb7bf](https://github.com/asermax/tachikoma/commit/36cb7bf97aa6ec7b008a80f89aa34da44b250909))

# [3.59.0](https://github.com/asermax/tachikoma/compare/v3.58.0...v3.59.0) (2026-07-11)


### Features

* **boundary:** stream trailing text as first turn for /checkpoint and /back ([f52edc2](https://github.com/asermax/tachikoma/commit/f52edc24d68b179409289133dcb17b0adb348bd4))

# [3.58.0](https://github.com/asermax/tachikoma/compare/v3.57.0...v3.58.0) (2026-07-10)


### Features

* **boundary:** recognize implicit main-line returns in summarize-to-checkpoint ([70c80a2](https://github.com/asermax/tachikoma/commit/70c80a2c777dd23c4036ce02fbecaad8d4efbb35))

# [3.57.0](https://github.com/asermax/tachikoma/compare/v3.56.0...v3.57.0) (2026-07-10)


### Features

* **context:** inject current date/time as debounced hidden message ([079badc](https://github.com/asermax/tachikoma/commit/079badcf3c1e0848d6b8c27b49f38672f16014a5))

# [3.56.0](https://github.com/asermax/tachikoma/compare/v3.55.0...v3.56.0) (2026-07-10)


### Bug Fixes

* **notifications:** replace NUL byte in dedup key with space ([e919993](https://github.com/asermax/tachikoma/commit/e919993b17d7dffdfe33b8826c15c96c8bd406bc))


### Features

* **dates:** render user-facing dates in configured timezone ([107b4df](https://github.com/asermax/tachikoma/commit/107b4df70aeb44a5fb64108de87960a2882ba9e8))

# [3.55.0](https://github.com/asermax/tachikoma/compare/v3.54.0...v3.55.0) (2026-07-08)


### Features

* **skills:** auto-grant skill-agent extensionTools from frontmatter ([8c9c69b](https://github.com/asermax/tachikoma/commit/8c9c69b169086ed85d163df741e4b1a440468537))

# [3.54.0](https://github.com/asermax/tachikoma/compare/v3.53.0...v3.54.0) (2026-07-08)


### Features

* **boundary:** broaden set-checkpoint to recognize interleaved side tasks ([86af65b](https://github.com/asermax/tachikoma/commit/86af65bc8500bc4e35299d0968560d70375f307f))
* **boundary:** park system side tasks and inject tangent focus ([1b4b6b2](https://github.com/asermax/tachikoma/commit/1b4b6b2bfdae7cb747e67296f486ebe23ac09b0b))

# [3.53.0](https://github.com/asermax/tachikoma/compare/v3.52.1...v3.53.0) (2026-07-07)


### Features

* **boundary:** emit session:topic-changed to reset per-branch state ([5e815dd](https://github.com/asermax/tachikoma/commit/5e815dd1dac802f3dd3dcbb96f8b5bd553bce81b))

## [3.52.1](https://github.com/asermax/tachikoma/compare/v3.52.0...v3.52.1) (2026-07-07)


### Bug Fixes

* **telegram:** preserve GFM table header as bold first bullet ([1bbbff4](https://github.com/asermax/tachikoma/commit/1bbbff46ed8e900b38f85121f1a0692af753b249))

# [3.52.0](https://github.com/asermax/tachikoma/compare/v3.51.1...v3.52.0) (2026-07-05)


### Features

* **telegram:** record sent file messages against live branch routing ([540e9e8](https://github.com/asermax/tachikoma/commit/540e9e88f8d039ff26221234b05e02ac77eb8cc6))

## [3.51.1](https://github.com/asermax/tachikoma/compare/v3.51.0...v3.51.1) (2026-07-05)


### Bug Fixes

* **coordinator:** deliver pending-input prompt as its own message ([479afe6](https://github.com/asermax/tachikoma/commit/479afe683178e565e1309b747a787b93388613dc))

# [3.51.0](https://github.com/asermax/tachikoma/compare/v3.50.0...v3.51.0) (2026-06-29)


### Features

* **coordinator:** serialize concurrent trunk close with run loop ([bb3b98e](https://github.com/asermax/tachikoma/commit/bb3b98e7a58a489a68b23d6a4a0c4d115c42bb26))

# [3.50.0](https://github.com/asermax/tachikoma/compare/v3.49.2...v3.50.0) (2026-06-29)


### Features

* **coordinator:** collapse live branch on trunk close ([c1844e8](https://github.com/asermax/tachikoma/commit/c1844e8660d502a094e70cbc76a674e5b3a2ae76))

## [3.49.2](https://github.com/asermax/tachikoma/compare/v3.49.1...v3.49.2) (2026-06-29)


### Bug Fixes

* **telegram:** flatten inner blockquotes inside expandable blockquote ([5dcc299](https://github.com/asermax/tachikoma/commit/5dcc299dde1fa064d0091e5536058a3977b3fc42))

## [3.49.1](https://github.com/asermax/tachikoma/compare/v3.49.0...v3.49.1) (2026-06-29)


### Bug Fixes

* **telegram:** defer intensive-work collapse to finalize ([a588e75](https://github.com/asermax/tachikoma/commit/a588e75e9d8f96accbdfebf5ddee3ec639f42434))

# [3.49.0](https://github.com/asermax/tachikoma/compare/v3.48.0...v3.49.0) (2026-06-28)


### Features

* **agent:** grant resolved extension tools in SideRunner headless runs ([75f3c72](https://github.com/asermax/tachikoma/commit/75f3c72475599e958c378b1fd5d59f0df1a1f57f))
* **extensions:** add subagent session scope for delegated factory binding ([053cdd8](https://github.com/asermax/tachikoma/commit/053cdd883a8b341acc19450b8fa001d1446cec4c))
* **skills:** add `extensionTools` grant to delegate_to_agent ([8b30b34](https://github.com/asermax/tachikoma/commit/8b30b34bead040bff1e6d59efb1184a3c0fd26a0))

# [3.48.0](https://github.com/asermax/tachikoma/compare/v3.47.1...v3.48.0) (2026-06-27)


### Features

* **skills:** grant per-delegation tools to subagents ([ace63f2](https://github.com/asermax/tachikoma/commit/ace63f22c9d2d58c7696e530f370e67e6f4e1d1f))

## [3.47.1](https://github.com/asermax/tachikoma/compare/v3.47.0...v3.47.1) (2026-06-25)


### Bug Fixes

* **telegram:** pin the current response by deferring the pin to finalization ([d96fd14](https://github.com/asermax/tachikoma/commit/d96fd14236c9300fa9a0c80063a156be347d312f))

# [3.47.0](https://github.com/asermax/tachikoma/compare/v3.46.1...v3.47.0) (2026-06-25)


### Features

* **boundary:** recognize return-to-main-line in summarize-to-checkpoint ([9464c41](https://github.com/asermax/tachikoma/commit/9464c41669f7844ec8418cde9cfff5a36caea6ec))

## [3.46.1](https://github.com/asermax/tachikoma/compare/v3.46.0...v3.46.1) (2026-06-25)


### Bug Fixes

* **telegram:** anchor header above first committed chunk on mid-stream overflow ([7007ac3](https://github.com/asermax/tachikoma/commit/7007ac3dd309e7c6958a3211abf166fab073fa1d))

# [3.46.0](https://github.com/asermax/tachikoma/compare/v3.45.1...v3.46.0) (2026-06-25)


### Features

* **memory:** align topics prompts with broadened scope ([e20908b](https://github.com/asermax/tachikoma/commit/e20908bf27ed35c282b538b115527f146dc6cf0e))
* **memory:** broaden topics scope and episodic day-summary ([6021f30](https://github.com/asermax/tachikoma/commit/6021f30f7953bfaf03c5c94b5d6e2d226617423d))

## [3.45.1](https://github.com/asermax/tachikoma/compare/v3.45.0...v3.45.1) (2026-06-24)


### Bug Fixes

* **telegram:** preserve visible body when pure text streams after a tool settles it ([a71af6b](https://github.com/asermax/tachikoma/commit/a71af6bd48c193ba61c0f7ab38051146ad436e7b))

# [3.45.0](https://github.com/asermax/tachikoma/compare/v3.44.0...v3.45.0) (2026-06-24)


### Features

* **boundary:** align classifier and docs on side-task framing ([01ef5bd](https://github.com/asermax/tachikoma/commit/01ef5bd2287e8cb271e91a2207f2d4f2dde16654))
* **boundary:** redefine set-checkpoint as a short unrelated side interruption ([8fe6225](https://github.com/asermax/tachikoma/commit/8fe6225b3445c80411f963530bbbd65396ee5ed3))
* **issue-368:** wip patch ([6f0c3c4](https://github.com/asermax/tachikoma/commit/6f0c3c4e1eb8bb2dfaa5ac2b57aa092653733635))

# [3.44.0](https://github.com/asermax/tachikoma/compare/v3.43.2...v3.44.0) (2026-06-23)


### Features

* add Body Structure Guide to skill-authoring ([df5bc0c](https://github.com/asermax/tachikoma/commit/df5bc0c938ea3f9cf1d9365bf1fc4a2612be6833))

## [3.43.2](https://github.com/asermax/tachikoma/compare/v3.43.1...v3.43.2) (2026-06-23)


### Bug Fixes

* **boundary:** recognize manual commands via channel-stamped token ([6dcebed](https://github.com/asermax/tachikoma/commit/6dcebed015d9f1a51531b4395c63804f8bad385a))

## [3.43.1](https://github.com/asermax/tachikoma/compare/v3.43.0...v3.43.1) (2026-06-22)


### Bug Fixes

* **telegram:** keep last collapse unit expanded via content-type split ([ef5b503](https://github.com/asermax/tachikoma/commit/ef5b5034e1529a2cd3dfb9ba935f87442e544813))

# [3.43.0](https://github.com/asermax/tachikoma/compare/v3.42.0...v3.43.0) (2026-06-22)


### Features

* **memory:** surface phased trunk-close progress via status callback ([f350228](https://github.com/asermax/tachikoma/commit/f35022887db0e49c6626a21788c983de6af4c8a5))

# [3.42.0](https://github.com/asermax/tachikoma/compare/v3.41.1...v3.42.0) (2026-06-22)


### Features

* **self-update:** defer restart until the exchange completes ([dc1f8ae](https://github.com/asermax/tachikoma/commit/dc1f8aec34903f9cc23edc2ae57b5f2cd1a54731))

## [3.41.1](https://github.com/asermax/tachikoma/compare/v3.41.0...v3.41.1) (2026-06-22)


### Bug Fixes

* **coordinator:** guard steering-queue clear on rescued count ([5875074](https://github.com/asermax/tachikoma/commit/58750742218002aff46b05a9f533b1f68df7afa6))
* **coordinator:** rescue orphaned steered messages at run-end ([5c610ef](https://github.com/asermax/tachikoma/commit/5c610efca5b56a0e261d6a3727ee69f0a4f8709b))

# [3.41.0](https://github.com/asermax/tachikoma/compare/v3.40.1...v3.41.0) (2026-06-22)


### Features

* **telegram:** add intensive-work collapse detection (DLT-064 Batch 2) ([5647f63](https://github.com/asermax/tachikoma/commit/5647f63761fa34b297b0a89c84a2587ee6c5fb92))
* **telegram:** add wrapExpandable and concatPayloads payload helpers ([f7a388f](https://github.com/asermax/tachikoma/commit/f7a388f9c978ca83b5a2dffff8809dc5a11949cd))
* **telegram:** wire collapse-aware payload into compose/finalize (DLT-064 Batch 3) ([afc0482](https://github.com/asermax/tachikoma/commit/afc0482cd016ed058a426903d6cf00359ce4dff7))

## [3.40.1](https://github.com/asermax/tachikoma/compare/v3.40.0...v3.40.1) (2026-06-22)


### Bug Fixes

* **coordinator:** queue slash commands mid-exchange instead of steering ([beb9f53](https://github.com/asermax/tachikoma/commit/beb9f5310f5b3ee3653f2b8faf9ae1b478003259))

# [3.40.0](https://github.com/asermax/tachikoma/compare/v3.39.2...v3.40.0) (2026-06-21)


### Features

* **coordinator:** surface trunk-lifecycle close status on a dedicated message ([bf281ab](https://github.com/asermax/tachikoma/commit/bf281ab1ae10ad5c0622a67c1bc1373f91a18927))

## [3.39.2](https://github.com/asermax/tachikoma/compare/v3.39.1...v3.39.2) (2026-06-21)


### Bug Fixes

* **git:** clear filter-repo metadata to keep scrub non-interactive ([45dd94b](https://github.com/asermax/tachikoma/commit/45dd94bcd4a3229ba195d146fa9fdee231f2d35d))

## [3.39.1](https://github.com/asermax/tachikoma/compare/v3.39.0...v3.39.1) (2026-06-21)


### Bug Fixes

* **coordinator:** leave the trunk open across shutdown ([7028cf7](https://github.com/asermax/tachikoma/commit/7028cf70d0d62ab611e784dd814c499f75c8848d))

# [3.39.0](https://github.com/asermax/tachikoma/compare/v3.38.0...v3.39.0) (2026-06-21)


### Features

* **memory:** add learnings memory layer folded into topics extraction (DLT-123) ([66ad6a5](https://github.com/asermax/tachikoma/commit/66ad6a5393ed7e2498cf926e2d8a800b2d2f356c))
* **memory:** establish learnings store layer ([cdd32dc](https://github.com/asermax/tachikoma/commit/cdd32dc216a14a8b6ad950093a69dd113acdc615))
* **memory:** fold learnings into topics extraction fork ([b47a739](https://github.com/asermax/tachikoma/commit/b47a7397a9f8de599d3b79dddbeeaad4fced19d7))

# [3.38.0](https://github.com/asermax/tachikoma/compare/v3.37.0...v3.38.0) (2026-06-21)


### Features

* **boundary:** add /rollback to reverse automatic checkpoint/topic decisions ([3f6d36a](https://github.com/asermax/tachikoma/commit/3f6d36a88b5e1b6f04ffcbe1349eea8da5676782))
* **boundary:** add manual /checkpoint and /back side-conversation commands ([fdb0520](https://github.com/asermax/tachikoma/commit/fdb052099ea9bdcb56f889069e3619c6040fa0bf))
* **boundary:** auto set-checkpoint and summarize-to-checkpoint classifier results ([b6ca80a](https://github.com/asermax/tachikoma/commit/b6ca80ac027639ead3604fb2293dd5243ad3db23))
* **boundary:** surface rollbackable header on automatic topic shift ([fef4979](https://github.com/asermax/tachikoma/commit/fef4979680a09c8a3fc8517d9d344a1b458995e5))
* **channels:** surface turn-scoped decision headers on streamed responses ([0d1e3c5](https://github.com/asermax/tachikoma/commit/0d1e3c5c94ae32a54aef86e88f50f74acd7149b3))
* **coordinator:** add pending-input flow and expand Telegram command menu ([8acaa45](https://github.com/asermax/tachikoma/commit/8acaa45386105e30cd7dca2084b96c273aa78ec1))
* **sessions:** add checkpoint and tangent primitives for side conversations ([7b4739a](https://github.com/asermax/tachikoma/commit/7b4739aa7ec739bdf13c0e762698d2bc9ec430f6))

# [3.37.0](https://github.com/asermax/tachikoma/compare/v3.36.1...v3.37.0) (2026-06-21)


### Features

* **git:** debounce per-exchange workspace commit-push ([c4ca0ce](https://github.com/asermax/tachikoma/commit/c4ca0ce8f85740822ad4edfe78043f346f826676))
* **projects:** debounce per-exchange submodule commit-push ([96a84b9](https://github.com/asermax/tachikoma/commit/96a84b986d0d023872660037082afa8f15149cd6))
* **util:** add trailing-edge debounced task primitive ([b379cca](https://github.com/asermax/tachikoma/commit/b379cca5d6e877a83a699376494410da0d610c43))

## [3.36.1](https://github.com/asermax/tachikoma/compare/v3.36.0...v3.36.1) (2026-06-20)


### Bug Fixes

* **boundary:** allow ask_branch to query the most-recently collapsed branch ([579393c](https://github.com/asermax/tachikoma/commit/579393c7a0a02b7593ebb7ede7ddd27ed1869374))

# [3.36.0](https://github.com/asermax/tachikoma/compare/v3.35.0...v3.36.0) (2026-06-19)


### Features

* **coordinator:** surface new-day trunk close progress on the channel ([dc8c536](https://github.com/asermax/tachikoma/commit/dc8c5368cc3711d1287e529d322186aa1e63c2a3))

# [3.35.0](https://github.com/asermax/tachikoma/compare/v3.34.0...v3.35.0) (2026-06-19)


### Features

* **detached-processes:** read both stdout and stderr by default ([4a0e62e](https://github.com/asermax/tachikoma/commit/4a0e62e11fc6dddf66c2653864426c762610f2ac))

# [3.34.0](https://github.com/asermax/tachikoma/compare/v3.33.0...v3.34.0) (2026-06-19)


### Features

* **memory:** extract a branch's stores concurrently ([11728eb](https://github.com/asermax/tachikoma/commit/11728ebc49b494f3b95edd66ef6068299264bcf6))

# [3.33.0](https://github.com/asermax/tachikoma/compare/v3.32.0...v3.33.0) (2026-06-18)


### Features

* **detached-processes:** capture exit codes via a shell EXIT trap ([f88f049](https://github.com/asermax/tachikoma/commit/f88f049af59697f66d078296bed0025f87cb402e))

# [3.32.0](https://github.com/asermax/tachikoma/compare/v3.31.2...v3.32.0) (2026-06-18)


### Features

* **skills:** treat skills as authoritative via extension-owned guidance ([d4057c9](https://github.com/asermax/tachikoma/commit/d4057c9d7e01b4ca63c6c2a9b069cc2a754101dd))

## [3.31.2](https://github.com/asermax/tachikoma/compare/v3.31.1...v3.31.2) (2026-06-18)


### Bug Fixes

* **memory:** date episodic extraction by the trunk's day, not wall-clock ([979968b](https://github.com/asermax/tachikoma/commit/979968bd29ea91f8fa22da510b682113bbc26055))

## [3.31.1](https://github.com/asermax/tachikoma/compare/v3.31.0...v3.31.1) (2026-06-18)


### Bug Fixes

* **coordinator:** keep a trunk unclosed when post-processing fails ([3206b61](https://github.com/asermax/tachikoma/commit/3206b61b2d341650022d4e15b98a578fdf0d5f63))
* **memory:** cut branch files from a detached session so trunk close extracts every branch ([3929f8d](https://github.com/asermax/tachikoma/commit/3929f8dda3ed826740e1557c7f53cc01be4e3d20))

# [3.31.0](https://github.com/asermax/tachikoma/compare/v3.30.1...v3.31.0) (2026-06-18)


### Features

* **boundary:** broaden ask_branch guidance for proactive context recovery ([bfb8fa6](https://github.com/asermax/tachikoma/commit/bfb8fa612cf50fb5fd438fc08195c77124d0b05d))

## [3.30.1](https://github.com/asermax/tachikoma/compare/v3.30.0...v3.30.1) (2026-06-18)


### Bug Fixes

* **memory:** remove legacy stores outright after migration fold ([9661362](https://github.com/asermax/tachikoma/commit/9661362dbcf72f2dd890f9455365ddc59f37693b))

# [3.30.0](https://github.com/asermax/tachikoma/compare/v3.29.0...v3.30.0) (2026-06-18)


### Features

* **telegram:** quote reacted-to message text on reactions ([c85b811](https://github.com/asermax/tachikoma/commit/c85b811a7837faa86887f3c7c93fdb2448aa0838))

# [3.29.0](https://github.com/asermax/tachikoma/compare/v3.28.1...v3.29.0) (2026-06-18)


### Features

* **extensions:** hand extension factories their binding session scope ([2dd6c29](https://github.com/asermax/tachikoma/commit/2dd6c29450a0c80054be7e5e4e4c1e38cd875a8c))

## [3.28.1](https://github.com/asermax/tachikoma/compare/v3.28.0...v3.28.1) (2026-06-17)


### Bug Fixes

* **skills:** surface detected skills at info, quiet disabled config ([76aa5ca](https://github.com/asermax/tachikoma/commit/76aa5ca315ddf69950cd10cd47f4c1da1237d543))

# [3.28.0](https://github.com/asermax/tachikoma/compare/v3.27.1...v3.28.0) (2026-06-17)


### Features

* **memory:** add one-time facts/preferences → topics migration ([6697d8c](https://github.com/asermax/tachikoma/commit/6697d8cf5a6c33a724105e45b6524b19e75b3920))

## [3.27.1](https://github.com/asermax/tachikoma/compare/v3.27.0...v3.27.1) (2026-06-17)


### Bug Fixes

* **log:** pass explicit level to multistreams so debug logging works ([de33e03](https://github.com/asermax/tachikoma/commit/de33e03b2e84bdc20721e010bc85d8b86058f819))

# [3.27.0](https://github.com/asermax/tachikoma/compare/v3.26.0...v3.27.0) (2026-06-17)


### Bug Fixes

* **skills:** treat empty proactive selection as valid result ([e54f57f](https://github.com/asermax/tachikoma/commit/e54f57fcfcbb5b25bfb3598fc3e3308c7b30a237))


### Features

* **logging:** apply action-coverage conventions across the app ([1aface6](https://github.com/asermax/tachikoma/commit/1aface6f1911da3a45461e48af93df52d97fe605))

# [3.26.0](https://github.com/asermax/tachikoma/compare/v3.25.0...v3.26.0) (2026-06-17)


### Features

* **deps:** bump pi SDK to 0.79.6 ([e0a6e7a](https://github.com/asermax/tachikoma/commit/e0a6e7a0487d808fc02a5adbfc64de179330d221))

# [3.25.0](https://github.com/asermax/tachikoma/compare/v3.24.2...v3.25.0) (2026-06-17)


### Features

* **sessions:** daily trunk session with collapsible topic branches ([f8a8e35](https://github.com/asermax/tachikoma/commit/f8a8e355423f16ff46637ab4f4fe21d3c8ace5f8))

## [3.24.2](https://github.com/asermax/tachikoma/compare/v3.24.1...v3.24.2) (2026-06-17)


### Bug Fixes

* **skills:** abort classify request at deadline instead of racing ([e219f3f](https://github.com/asermax/tachikoma/commit/e219f3f56b1d08ee760cf1495421cfef9d660940))

## [3.24.1](https://github.com/asermax/tachikoma/compare/v3.24.0...v3.24.1) (2026-06-16)


### Bug Fixes

* **telegram:** claim lead-in seed under send mutex ([4c65f57](https://github.com/asermax/tachikoma/commit/4c65f57d59bc185604ef344ff207f5720a7d1f87))

# [3.24.0](https://github.com/asermax/tachikoma/compare/v3.23.0...v3.24.0) (2026-06-16)


### Features

* increase skill classification timeout to 30s ([63bf3d3](https://github.com/asermax/tachikoma/commit/63bf3d3bac5cf2083085e11b8c43eef9b7f1731f))

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
