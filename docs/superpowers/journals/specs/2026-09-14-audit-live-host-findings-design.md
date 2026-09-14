# Journal: 2026-09-14-audit-live-host-findings-design

<!-- fr:journal kind=decision scope=spec id=d-fix-all-four-together created=2026-09-14T20:16:28 -->
### d-fix-all-four-together · decision · Fix the four live-host defects on one branch

Operator decision after the post-merge Test Plan of #5: slow git resolution, the tight sessions timeout, collaborator repositories in section 3, and over-attributed branch evidence were found by one live run and share its Test Plan.

<!-- fr:journal kind=decision scope=spec id=d-archive-before-fix created=2026-09-14T20:16:33 -->
### d-archive-before-fix · decision · Archive the session-audit plan before starting the fix

Operator decision. With a live plan on main, fr isolation up refuses every workspace unless scripts/validate-plans.sh is committed, and the operator declined that wrapper. #6 archived the plan so main has none.

<!-- fr:journal kind=decision scope=spec id=d-no-fr-plan created=2026-09-14T20:16:40 -->
### d-no-fr-plan · decision · The fix has a spec and journal but no fr plan

A live plan directory would re-trigger fr's validator requirement for every later workspace in this repository.

<!-- fr:journal kind=decision scope=spec id=d-evidence-own-repository created=2026-09-14T20:16:47 -->
### d-evidence-own-repository · decision · A session is credited only with branches in its own repository

Operator decision. The hand-audit session inspected and cleaned up other repositories' worktrees and was credited with about 20 branches across 8 repositories, which produced every section 2 row.

<!-- fr:journal kind=decision scope=spec id=d-same-repo-worktree-cd-counts created=2026-09-14T20:16:52 -->
### d-same-repo-worktree-cd-counts · decision · Same-repository fr worktrees reached by cd still count

fr sessions work through cd <worktree> && ... while the recorded cwd stays in the base clone; counting only cwd would drop most real fr evidence.

<!-- fr:journal kind=decision scope=spec id=d-sessions-timeout-600 created=2026-09-14T20:16:57 -->
### d-sessions-timeout-600 · decision · The sessions timeout is 600 s rather than a faster parser

Under host load the Claude adapter needed 82 s against a 120 s limit and one run went incomplete. The timeout bounds a hung adapter; --since already bounds the work. Parser speed is a separate optimisation.

<!-- fr:journal kind=decision scope=spec id=d-one-for-each-ref-per-repo created=2026-09-14T20:17:03 -->
### d-one-for-each-ref-per-repo · decision · Local refs are read with one for-each-ref per repository

A profile showed 1,269 git subprocesses, 69% of an 8-minute run, because lookups repeated per branch directory. One call per repository matches the per-repository cache.
