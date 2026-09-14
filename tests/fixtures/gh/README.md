# Captured `gh` responses

Same rule as `tests/fixtures/herdr/`, `tests/fixtures/claude/` and
`tests/fixtures/codex/`: **this is a capture, not a construction.** The fake
`gh` (`tests/helpers/fake-gh`) answers from these shapes, so
this is where they come from -- never a guess written alongside the code
that reads it.

## Provenance

Captured 2026-09-13, read-only, with `gh api` and `gh api graphql` against
this repository and the authenticated account itself. Every call is a plain
read: no mutation, and the repository's refs and pull requests were
unchanged before and after (see `git log`).

`open-pull-requests.json` was captured 2026-09-14, read-only, against two
unrelated public repositories chosen for having real open pull requests to
capture the shape from (see "not seen" below).

| File | Command | Notes |
|---|---|---|
| `owner-pull-requests.json` | `HsOwnerPullRequests` (`repositoryOwner.repositories(first: 2, isArchived: false)`, each with `pullRequests(states: OPEN, first: 100)`) | Two of the account's own repositories, unrelated to this one. `pullRequests.nodes` is `[]` on both, in the capture. See "not seen" below. |
| `repo-branches.json` | `HsRepoBranches` for this repository, two branches: one whose pull request merged and whose branch ref was then deleted (`h0`/`q0`), and one whose ref still exists (`h1`/`q1`) | The first branch's pull request merged and its ref was deleted -- `r0` (the ref) is `null`, `p0` (its pull requests) still finds the MERGED PR by head branch name. The second branch's ref exists (`r1`), and no pull request has it as its own head (`p1` is empty). One query, both branches, matching the design doc's own batching. |
| `compare.json` | `HsCompare` for this repository, `h0` = the default branch | The default branch compared against itself: `IDENTICAL`, because that comparison is trivially true for any repository and needs no branch this account happens to have open right now. |
| `compare-missing-ref.stdout` / `.stderr` / `.exit` | `HsCompare` for this repository, `h0` = a branch whose ref no longer exists | The captured shape docs/superpowers/implemented/specs/2026-09-13-session-audit-design.md's own "GitHub queries" section already names: partial `data` (`compare` is `null`), a `NOT_FOUND` entry in `errors`, `gh`'s own one-line message on stderr, and exit `1`. |
| `user.json` | `gh api user` | The authenticated account. |
| `user-orgs.json` | `gh api --paginate user/orgs` | The account's own organisations. Trimmed, keeping every key. |
| `open-pull-requests.json` | `repository(owner, name) { pullRequests(states: OPEN, first: N) { nodes { ... } } }`, the spec's full field selection | Two real, unrelated public repositories' own open pull requests, merged into one fixture repository (see below): one node authored by a public GitHub App bot login, one authored by a person. |

## What was masked

Every key from every real response is kept; only values were changed
(`--owner`/`--dry-run` naming aside, the design doc's own rule: "Captured
`gh` responses are sanitised the same way [as `tests/fixtures/`]. Their
shape is kept and their values are replaced.").

| Field(s) | Real value | Replaced with |
|---|---|---|
| every repository owner login, in every file, and in `user.json`'s `login` | the account this plan was built under | `example-user` |
| every repository name, in every file (including this repository's own name, in `repo-branches.json`'s pull request URL and `headRepository.nameWithOwner`) | a real repository name | `example-repo`, `example-repo-1`, `example-repo-2` -- even where it is this very repository, the design doc's own capture note is explicit that the real login and repo name are fine to QUERY but must be replaced in the committed fixture |
| the branch name in `compare-missing-ref.{stdout,stderr}` and in `repo-branches.json`'s prose | a real branch, merged by pull request and since deleted | `example-branch` |
| `r1.target.oid` | a real commit sha | forty zeros |
| `owner-pull-requests.json`'s `pageInfo.endCursor` | a real opaque pagination cursor (GitHub's own cursor encoding embeds a real repository id -- decoding it is not a hypothetical) | a synthetic cursor, the base64 encoding of the literal string `cursor:v2:placeholder`, which decodes to nothing but that placeholder text |
| every organisation in `user-orgs.json` | the account's own real organisation logins, ids and avatar URLs | one entry, `example-org`, id `2`, and matching placeholder URLs |
| `user.json`'s `id`, `node_id`, every `*_url`, `name`, `company`, `blog`, `location`, `public_repos`, `followers`, `following`, `created_at` | this account's real numbers and profile text | small placeholder numbers and generic text; `company`, `location`, `bio` etc. set to `null` (real values were themselves personal, not merely realistic) |
| `open-pull-requests.json`'s two repository names, its `title`s, `headRefName`s, `url`s, `number`s and `updatedAt` timestamps, and its `User` author's `login` | two real public repositories, two real pull request numbers, titles, branch names and timestamps, and one real person's login | `example-org/example-repo`, neutral titles, placeholder urls built from the placeholder numbers `101`/`102`, and `2026-01-01T00:00:00Z` for both timestamps; `example-user` for the person. Kept, deliberately: the `Bot` author's own `login`, `dependabot` (a public GitHub App login, not a person -- the design doc's own bot rule, `author.__typename == "Bot"` or a login ending in `[bot]`, reads it directly), the `Bot` node's `headRefName` SHAPE (`dependabot/npm_and_yarn/<dependency>-<version>`, a fixed pattern dependabot itself generates, not this account's own naming), and its `__typename` value itself. |

**Kept as captured**, because it carries no identity: every GraphQL key
structure and nesting; `defaultBranchRef.name` (`"main"`, already the least
specific value it could have been); `state`, `isDraft`, `status` and every
other enum value; `user.json`'s `type`, `user_view_type`, `site_admin`,
`gravatar_id`, `hireable`, `twitter_username`, `notification_email`,
`public_gists`, `updated_at`; `open-pull-requests.json`'s `isDraft`,
`__typename` values, and (as named in the table above) the `Bot` node's
`login` and `headRefName` shape.

## What was NOT seen on this host, and is therefore a construction later

- **An OPEN pull request in `owner-pull-requests.json` or
  `repo-branches.json`.** `owner-pull-requests.json`'s `pullRequests.nodes`
  is `[]` on both captured repositories, and `repo-branches.json`'s own `p1`
  (for the second branch) is empty too (nothing has it as a head branch).
  `open-pull-requests.json` (above) supplies the OPEN-pull-request node
  shape instead, captured from two OTHER, unrelated public repositories
  chosen for having real open pull requests of each author kind.
- **A deleted author** (`author: null`) on an open pull request. Not seen
  anywhere captured.
- **A fork pull request** (`headRepository.nameWithOwner` different from the
  repository being queried) that is also OPEN. `repo-branches.json`'s own
  MERGED node has a matching `headRepository`; an open, non-matching one is
  a construction.
- **`HsRepoOpenPullRequests`**, the follow-up query for a repository with
  more than 100 open pull requests. No repository queried came close.
- **A second page** of `HsOwnerPullRequests`. `pageInfo.hasNextPage: true`
  IS real and kept as captured (its meaning: more results exist past this
  page), but no second page was fetched, since a further repository would
  not have shown a different shape.
- **`gh auth status --hostname github.com`**, its success or failure shape,
  and `FAKE_GH_UNAUTH`/`GH_TOKEN=invalid`'s effect -- outside this task's
  read-only captures (`auth status` prints human-readable text to stderr,
  not JSON, and this task only captured `gh api`/`gh api graphql`
  responses). The fake `gh` (`tests/helpers/fake-gh`) constructs its own
  `auth status` behaviour directly.
