# lib/common.sh
#
# Shared constants and helpers for herdr-setup. Sourced by the entrypoint and
# by tests/test_*.sh — never executed directly.
#
# Deliberately free of `set -e`/`set -u`/`set -o pipefail`: this file is
# sourced into scripts that need to inspect the exit status of a command
# that is expected to fail (the preflight gate, herdr calls). Setting shell
# options here would apply them to whatever sources this file.
#
# Bash 3.2 floor (macOS): no associative arrays, no mapfile, no `local -n`.
# Python comes from uv, not from the host -- lib/hs.py's PEP 723 header and
# pyproject.toml name the interpreter (see AGENTS.md), so nothing here has to
# work around an old system python.

HS_DEFAULT_SOCKET_PATH="$HOME/.config/herdr/herdr.sock"
HS_DEFAULT_HERDR_CONFIG_DIR="$HOME/.config/herdr"

hs_socket_path() {
  echo "${HERDR_SOCKET_PATH:-$HS_DEFAULT_SOCKET_PATH}"
}

# hs_herdr_config_dir: the directory Herdr keeps its own state in --
# plugins.json and config.toml live here on the host. Override with
# HERDR_CONFIG_DIR (tests use this; a real host never needs to).
hs_herdr_config_dir() {
  echo "${HERDR_CONFIG_DIR:-$HS_DEFAULT_HERDR_CONFIG_DIR}"
}

# hs_plugins_json_path: the host's plugins.json, read directly from disk by
# hs_host_plugins (never through herdr) so the plugin section of `diff`
# keeps working under a protocol mismatch.
hs_plugins_json_path() {
  echo "$(hs_herdr_config_dir)/plugins.json"
}

# hs_response_error <text>: reads one herdr response and says whether it is
# an error object. Prints the error MESSAGE on stdout when it is, nothing
# otherwise, and returns:
#
#   0  <text> is a JSON object with a top-level `error` key
#   1  <text> was read and has no such key -- the only "this call was fine"
#   2  <text> carries the `"error"` token but could not be read (not JSON,
#      not an object, or hs_py itself could not run)
#
# The cheap `case` is only a filter for whether there is anything to parse,
# and it cannot miss a real error: an error key always puts the token in the
# text. The decision itself is made by PARSING (hs_py herdr-error), because a
# perfectly good response can carry the token "error" inside a value, and a
# substring test reports that successful call as a failure.
#
# 2 exists because collapsing "cannot read it" into "no error key" is the
# same fail-open shape as everything else this file guards: a guard that
# cannot reach its evidence must not report the safe-looking answer. Every
# caller treats 2 as a failed call.
#
# One function rather than two because BOTH the preflight probe and
# hs_herdr_json have now been caught reading this wrongly, each in its own
# way, and a rule with two implementations is a rule with two answers.
hs_response_error() {
  local text="$1" message py_rc=0

  case "$text" in
    *'"error"'*) ;;
    *) return 1 ;;
  esac

  message="$(printf '%s' "$text" | hs_py herdr-error 2>/dev/null)" || py_rc=$?
  case "$py_rc" in
    0)
      if [ -z "$message" ]; then
        message="$text"
      fi
      printf '%s' "$message"
      return 0
      ;;
    1)
      return 1
      ;;
    *)
      return 2
      ;;
  esac
}

# hs_preflight_probe: the one socket call the preflight gate makes, kept as
# its own function so hs_preflight and hs_require_socket agree on what is
# probed and on how its output is read. Prints herdr's stdout AND stderr
# combined, and returns herdr's own exit status.
#
# The `2>&1` is load-bearing, not tidiness. The probe used to discard stderr,
# so a herdr that reports its error object on stderr -- which the real one
# may -- left the gate with an empty string to reason about, and the gate
# then concluded the host was fine. hs_herdr_json has always merged the two
# for exactly this reason; the probe was the inconsistent one.
hs_preflight_probe() {
  herdr plugin list 2>&1
}

# hs_preflight: echoes exactly one of no-herdr | no-server | mismatched |
# unreachable | matched. Never writes anything and never fails: every command
# decides what to do with the state itself (diff keeps going on no-server/
# mismatched/unreachable; hs_require_socket below is what the write commands
# use to stop).
#
# `matched` means one thing only: a probe call that actually SUCCEEDED. Any
# non-zero exit is a host this tool must not write to, whatever herdr said --
# the gate previously singled out `protocol_mismatch` and reported every
# other failure as `matched`, so a server that had just refused the probe
# (socket_closed, a permission error, an unparseable answer) let `apply`
# proceed. `unreachable` is the fourth state that failure now has a name for.
#
# And a zero exit status is not a success either: a server that answers with
# an error object and exits 0 is still refusing, so the answer is parsed as
# well as the status. Both halves classify through hs_preflight_failure, so
# there is exactly one rule for what a refusal is called.
hs_preflight() {
  if ! command -v herdr >/dev/null 2>&1; then
    echo "no-herdr"
    return 0
  fi

  local socket_path
  socket_path="$(hs_socket_path)"
  if [ ! -e "$socket_path" ]; then
    echo "no-server"
    return 0
  fi

  # Check the exit status FIRST: a blocked herdr usually answers with a JSON
  # error object AND exits non-zero. What it said then decides only WHICH
  # failure this is, never whether it failed.
  local output rc
  output="$(hs_preflight_probe)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    hs_preflight_failure "$output"
    return 0
  fi

  # A zero exit status is NOT proof the call succeeded. A server can answer
  # with a well-formed error object and still exit 0, and deciding purely on
  # `rc` meant the gate never looked at the answer at all: it reported
  # `matched` for a probe it had just watched be refused, hs_require_socket
  # returned 0, and `apply` rewrote a live config against a server that had
  # refused every call. So the answer is parsed here too, exactly the way
  # hs_herdr_json parses one -- an `error` key means this call did not
  # succeed, whatever the exit status claimed.
  #
  # A response carrying the token that cannot be READ (hs_response_error 2)
  # counts as a refusal as well. The probe merges stderr, so an unreadable
  # answer is usually a herdr that said something alongside its JSON, and a
  # gate that cannot reach its evidence must not report the safe answer.
  # hs_require_socket re-probes and prints what herdr actually said, so the
  # operator sees the text this refused on.
  local err_rc=0
  hs_response_error "$output" >/dev/null || err_rc=$?
  if [ "$err_rc" -ne 1 ]; then
    hs_preflight_failure "$output"
    return 0
  fi

  echo "matched"
  return 0
}

# hs_preflight_failure <probe-output>: names WHICH failure a refused probe
# was. Its own function because the exit-status path and the error-object
# path must classify identically -- when they did not, `protocol_mismatch`
# was the only failure with a name and every other one read as a healthy
# host.
hs_preflight_failure() {
  if printf '%s' "$1" | grep -q '"code":"protocol_mismatch"'; then
    echo "mismatched"
  else
    echo "unreachable"
  fi
}

# hs_flatten: folds its stdin onto one line, collapsing runs of whitespace.
# Every hs_require_socket message is exactly one line by contract, and a
# herdr error message quoted into one can carry newlines.
hs_flatten() {
  tr '\n' ' ' | tr -s '[:space:]' ' ' | sed -e 's/^ //' -e 's/ $//'
}

# hs_require_socket: returns 0 when hs_preflight reports matched. Otherwise
# prints exactly one line to stderr naming the state and the fix, and exits
# 3. Commands that need the socket (apply, onboard, feed) call this first;
# diff does not, since it works from disk and `herdr integration status`
# regardless of preflight state.
hs_require_socket() {
  local state
  state="$(hs_preflight)"
  case "$state" in
    matched)
      return 0
      ;;
    no-herdr)
      echo "herdr-setup: herdr is not on PATH; install Herdr, then retry." >&2
      return 3
      ;;
    no-server)
      echo "herdr-setup: no Herdr server found at $(hs_socket_path); start the Herdr server, then retry." >&2
      return 3
      ;;
    mismatched)
      echo "herdr-setup: the herdr command line is newer than the running server; restart the Herdr server, then retry." >&2
      return 3
      ;;
    unreachable)
      # Re-probe rather than have hs_preflight smuggle the message out: it
      # runs in a command substitution, so a global set there is lost, and
      # this path is an error path on a tool that runs by hand.
      local detail
      detail="$(hs_preflight_probe | hs_flatten)"
      echo "herdr-setup: the Herdr server refused a probe call (${detail:-no output}); fix the Herdr server, then retry." >&2
      return 3
      ;;
    *)
      echo "herdr-setup: unrecognized preflight state '$state'." >&2
      return 3
      ;;
  esac
}

# hs_herdr_json: the wrapper for herdr calls whose stdout this tool PARSES.
# Runs `herdr "$@"`, checks the exit status FIRST, and NEVER returns a
# result from a failed call — a blocked herdr answers with a JSON error
# object rather than an empty result, and a caller that reads .result.*
# without checking the exit status would see zero of everything and report
# success having done nothing. Returns non-zero on a failed exit status, or
# on an "error" key ON EITHER STREAM (even if the exit status was 0), and
# prints the error message to stderr in either case. On success, prints the
# herdr output on stdout unchanged and returns 0.
#
# stdout and stderr are captured SEPARATELY. They used to be merged with
# `2>&1`, which meant one deprecation notice on stderr was returned glued to
# the JSON, and the caller's parse failed on output that was in fact fine.
# No caller parsed the result while that was true, so nothing broke -- but
# `feed` parses `agent list` and `pane process-info` through here, so this
# would have broken the first time it mattered. herdr's own stderr is passed
# through to ours on success (a warning is worth seeing) and is folded into
# the error message on failure (that is where it usually says why).
#
# For a call whose stdout is NOT parsed, use hs_herdr_interactive below: a
# command substitution captures the terminal away from a herdr that wants to
# prompt.
hs_herdr_json() {
  local output rc message is_error err_is_error err_file err_text py_rc
  err_file="$(mktemp)" || {
    echo "herdr-setup: cannot create a temporary file" >&2
    return 2
  }
  output="$(herdr "$@" 2>"$err_file")"
  rc=$?
  err_text="$(cat "$err_file")"
  rm -f "$err_file"

  # Whether this is an error is decided by PARSING the response
  # (hs_response_error), on BOTH streams. Looking only at stdout was the
  # defect: a herdr that reports its error object on stderr left the filter
  # with an empty string, so with a zero exit status the call was reported as
  # a success and the error text was passed through to our own stderr as if it
  # were a harmless notice. `apply` then wrote the config and "successfully"
  # called reload-config against a server that had refused it.
  #
  # stdout is asked first, because that is where a well-formed answer lives.
  # Note what is deliberately NOT done here: an empty stdout is not treated as
  # a failure. `server reload-config` legitimately answers nothing at all, and
  # apply calls it on every write.
  message=""
  is_error=0
  err_is_error=0

  py_rc=0
  message="$(hs_response_error "$output")" || py_rc=$?
  case "$py_rc" in
    0) is_error=1 ;;
    1) message="" ;;
    *)
      # The answer carries the token but could not be read -- hs_py could not
      # run, or the text is not a JSON object. A response we cannot read is
      # not evidence of success.
      echo "herdr-setup: could not parse the herdr response (hs_py exited $py_rc); treating it as a failure" >&2
      is_error=1
      message="$output"
      ;;
  esac

  if [ "$is_error" -eq 0 ]; then
    py_rc=0
    message="$(hs_response_error "$err_text")" || py_rc=$?
    case "$py_rc" in
      0) is_error=1; err_is_error=1 ;;
      1) message="" ;;
      *)
        echo "herdr-setup: could not parse the herdr response on stderr (hs_py exited $py_rc); treating it as a failure" >&2
        is_error=1
        err_is_error=1
        message="$err_text"
        ;;
    esac
  fi

  if [ "$rc" -ne 0 ] || [ "$is_error" -eq 1 ]; then
    if [ -z "$message" ]; then
      message="${output:-$err_text}"
    fi
    # Fold herdr's stderr into the message -- that is where it usually says
    # why -- unless the message IS the error read off stderr, which would
    # print the same failure twice, once parsed and once raw.
    if [ "$err_is_error" -eq 0 ] && [ -n "$err_text" ] && [ "$message" != "$err_text" ]; then
      message="$message${message:+ }$err_text"
    fi
    echo "herdr-setup: herdr $*: $(printf '%s' "$message" | hs_flatten)" >&2
    if [ "$rc" -eq 0 ]; then
      return 1
    fi
    return "$rc"
  fi

  if [ -n "$err_text" ]; then
    printf '%s\n' "$err_text" >&2
  fi
  printf '%s\n' "$output"
  return 0
}

# hs_herdr_interactive: the wrapper for herdr calls this tool does NOT parse.
# Runs `herdr "$@"` in the foreground with stdin, stdout and stderr all
# inherited, and returns herdr's exit status and nothing else.
#
# This exists because `plugin install` shows Herdr's own trust preview and
# then waits for an answer unless `--yes` is passed (design doc, Commands >
# apply). Routed through hs_herdr_json, that preview went into a command
# substitution: the operator saw a silent, stopped terminal while herdr sat
# blocked on a read behind a prompt they were never shown. Capturing the
# output of a command that wants the terminal is the bug; there is no way to
# both capture stdout and let herdr talk to the operator on it.
#
# `onboard` (phase 9) offers an integration and waits for an answer the same
# way, and must call this rather than hs_herdr_json for the same reason.
hs_herdr_interactive() {
  herdr "$@"
}

# hs_manifest_plugins <file>: prints one <owner>/<repo>[/<subdir>]<TAB><ref>
# line per entry in a plugins.list manifest. Each line is trimmed of leading
# and trailing whitespace first; blank lines and lines starting with `#` are
# then ignored. Every other line must split into exactly two
# whitespace-separated fields (source, ref) -- a source with a subdir (e.g.
# `owner/repo/tools/subdir`) is still one field, since it has no internal
# whitespace. A line with one field or three-or-more is a fatal error naming
# the file and line number, and hs_manifest_plugins stops there and returns 2.
# A missing or unreadable file is the same fail-closed shape (AGENTS.md: "an
# unreadable manifest ... stops the run with one clear line"): exit 2, one
# line naming the file.
#
# Naming the same source twice is fatal too, exit 2. Two lines for one plugin
# are either redundant (apply runs the same install twice) or contradictory
# (two different pinned refs, and the manifest cannot say which one the host
# is meant to converge on). Neither is something to guess at -- AGENTS.md:
# "Never guess and continue."
hs_manifest_plugins() {
  local file="$1"
  local line_num=0
  local line trimmed field_count source ref
  local seen="" had_noglob=0

  if [ ! -r "$file" ]; then
    echo "herdr-setup: $file: manifest not found or unreadable" >&2
    return 2
  fi

  # `set -f` below is a borrowed shell option, not ours to leave switched.
  # Restoring it unconditionally with `set +f` turned globbing back ON for a
  # caller that had deliberately turned it off.
  case "$-" in
    *f*) had_noglob=1 ;;
  esac

  while IFS= read -r line || [ -n "$line" ]; do
    line_num=$((line_num + 1))
    trimmed="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$trimmed" ] && continue
    case "$trimmed" in
      '#'*) continue ;;
    esac

    # Intentional word-splitting: this is how the line's field count is
    # checked (1, 2, or 3+). `set -f` around it blocks glob expansion, the
    # other half of what shellcheck's SC2086 warns about.
    set -f
    # shellcheck disable=SC2086
    set -- $trimmed
    if [ "$had_noglob" -eq 0 ]; then
      set +f
    fi
    field_count="$#"
    source="${1:-}"
    ref="${2:-}"

    # One field or two. Two pins a branch or tag; one means "whatever the
    # repository's default branch is", which is exactly what Herdr records when
    # a plugin was installed without `--ref`. Requiring two made the manifest
    # unable to describe a host that has such a plugin.
    if [ "$field_count" -ne 1 ] && [ "$field_count" -ne 2 ]; then
      echo "herdr-setup: $file:$line_num: expected '<owner>/<repo>[/<subdir>] [<ref>]', got: $trimmed" >&2
      return 2
    fi

    # Membership test on a space-delimited string: bash 3.2 has no
    # associative arrays, and a source can never contain a space -- it is
    # one whitespace-separated field, which is what the field count above
    # has just established.
    case " $seen " in
      *" $source "*)
        echo "herdr-setup: $file:$line_num: duplicate plugin source '$source'" >&2
        return 2
        ;;
    esac
    seen="$seen $source"

    printf '%s\t%s\n' "$source" "$ref"
  done < "$file"

  return 0
}

# hs_host_plugins <plugins.json>: prints one
# <plugin_id><TAB><owner>/<repo>[/<subdir>]<TAB><requested_ref><TAB><resolved_commit>
# line per entry, sorted by plugin_id. Reads the file directly from disk and
# never invokes herdr, which is what lets it keep working under a protocol
# mismatch. A missing file means a host with no plugins yet: no output,
# exit 0. A file that is not valid JSON, is not a JSON array, or whose
# entries are missing the expected keys is a fatal error naming the file,
# exit 2.
hs_host_plugins() {
  local file="$1"
  if [ ! -e "$file" ]; then
    return 0
  fi
  hs_py host-plugins "$file"
}

# hs_resolve_ref <source> <ref>: resolves <ref> against the GitHub repo
# named by <source> (owner/repo[/subdir] -- a subdir is ignored, since the
# git remote is still owner/repo) via `git ls-remote`, and echoes the full
# COMMIT sha it resolves to. Empty output from ls-remote means the ref no
# longer exists upstream; hs_resolve_ref then echoes nothing and returns 1,
# leaving the caller to report that as drift.
#
# An annotated tag is why both `$ref` and `${ref}^{}` are asked for. An
# annotated tag is an object of its own that POINTS AT a commit, and
# ls-remote answers with two lines:
#
#   d4ca2e3...  refs/tags/v2.40.0        <- the tag object
#   73876f4...  refs/tags/v2.40.0^{}     <- the commit it points at
#
# Herdr records the commit. Taking the first line handed back the tag
# object's sha, which never equals the recorded commit, so a plugin pinned
# to an annotated tag reported `moved` on every diff and was reinstalled on
# every apply, for ever, without converging -- and the design doc's own
# example manifest pins `v0.3.3`. The peeled `^{}` line is preferred
# whenever it is present; a lightweight tag or a branch has no peeled line
# and the single line it does have IS the commit.
hs_resolve_ref() {
  local source="$1" ref="${2:-}"
  local owner_repo url output sha

  owner_repo="$(printf '%s' "$source" | cut -d/ -f1-2)"
  url="https://github.com/${owner_repo}.git"

  # An empty ref means the manifest named none, so resolve the remote's default
  # branch, which is what Herdr installed from.
  if [ -z "$ref" ]; then
    output="$(git ls-remote "$url" HEAD 2>/dev/null)"
  else
    output="$(git ls-remote "$url" "$ref" "${ref}^{}" 2>/dev/null)"
  fi
  sha="$(printf '%s\n' "$output" | awk '$2 ~ /\^\{\}$/ { print $1; exit }')"
  if [ -z "$sha" ]; then
    sha="$(printf '%s\n' "$output" | awk 'NF { print $1; exit }')"
  fi

  if [ -z "$sha" ]; then
    return 1
  fi

  printf '%s\n' "$sha"
  return 0
}

# hs_diff_plugins <manifest-file> <plugins-json>: the plugin section of
# `diff`. Compares the pinned manifest against the host's plugins.json,
# matching entries by their <owner>/<repo>[/<subdir>] source string. Both
# sides come from disk (hs_manifest_plugins and hs_host_plugins never call
# herdr), so this works the same whether or not the socket is reachable --
# only hs_resolve_ref reaches out, to git, never to herdr. Prints one line
# per plugin:
#
#   plugin ok: <source>
#   plugin missing: <source>
#   plugin moved: <source> (recorded=<short>, resolved=<short>)
#   plugin moved: <source> (recorded=<short>, ref-gone)
#   plugin unmanaged: <source>
#
# Returns 0 when every manifest plugin is ok (and every host plugin is
# either named by the manifest or unmanaged -- unmanaged never affects the
# exit status), 1 when anything is missing or moved, 2 if the manifest or
# the host plugins.json fails to parse.
hs_diff_plugins() {
  local manifest_file="$1" plugins_json="$2"
  local rc=0

  local manifest_tmp host_tmp sources_tmp
  manifest_tmp="$(mktemp)" || return 2
  host_tmp="$(mktemp)" || { rm -f "$manifest_tmp"; return 2; }
  sources_tmp="$(mktemp)" || { rm -f "$manifest_tmp" "$host_tmp"; return 2; }

  if ! hs_manifest_plugins "$manifest_file" > "$manifest_tmp"; then
    rm -f "$manifest_tmp" "$host_tmp" "$sources_tmp"
    return 2
  fi

  if ! hs_host_plugins "$plugins_json" > "$host_tmp"; then
    rm -f "$manifest_tmp" "$host_tmp" "$sources_tmp"
    return 2
  fi

  cut -f1 "$manifest_tmp" > "$sources_tmp"

  local m_source m_ref
  while IFS=$'\t' read -r m_source m_ref; do
    [ -z "$m_source" ] && continue

    local h_line
    h_line="$(awk -F'\t' -v src="$m_source" '$2 == src {print; exit}' "$host_tmp")"
    if [ -z "$h_line" ]; then
      echo "plugin missing: $m_source"
      rc=1
      continue
    fi

    local h_resolved
    IFS=$'\t' read -r _ _ _ h_resolved <<< "$h_line"

    local resolved
    if ! resolved="$(hs_resolve_ref "$m_source" "$m_ref")"; then
      resolved=""
    fi

    if [ -z "$resolved" ]; then
      echo "plugin moved: $m_source (recorded=${h_resolved:0:7}, ref-gone)"
      rc=1
    elif [ "$resolved" = "$h_resolved" ]; then
      echo "plugin ok: $m_source"
    else
      echo "plugin moved: $m_source (recorded=${h_resolved:0:7}, resolved=${resolved:0:7})"
      rc=1
    fi
  done < "$manifest_tmp"

  local h_source
  while IFS=$'\t' read -r _ h_source _ _; do
    [ -z "$h_source" ] && continue
    if ! grep -qxF "$h_source" "$sources_tmp"; then
      echo "plugin unmanaged: $h_source"
    fi
  done < "$host_tmp"

  rm -f "$manifest_tmp" "$host_tmp" "$sources_tmp"
  return "$rc"
}

# hs_herdr_config_dir()/config.toml -- the host's own Herdr config file,
# read directly from disk (never through herdr) by the config section of
# `diff`, for the same reason hs_plugins_json_path is read from disk: it
# keeps working under a protocol mismatch.
hs_config_toml_path() {
  echo "$(hs_herdr_config_dir)/config.toml"
}

# _hs_plugin_block_walk <file> <mode>: the shared engine behind
# hs_strip_plugin_blocks (mode=strip), hs_extract_plugin_blocks
# (mode=extract), and the splice mode used internally by hs_splice_config
# (mode=splice). A plugin-written block is every line from
# `# --- added by <id> ...` through the following `# --- end <id> ---`,
# inclusive, matching the id captured at the begin marker against the id
# captured at the end marker rather than accepting any end marker. strip
# prints every line OUTSIDE such a block; extract prints every line INSIDE
# one, markers included, in file order -- the two outputs partition the
# file's lines between them, and extract's markers are what let a later
# splice put a block back exactly as it was. splice prints the same thing
# extract does, except each block is preceded by one sentinel line,
# `<HS_SPLICE_SENTINEL><n>`, where <n> is the number of lines strip would
# have printed before that block began -- its anchor. hs_py splice-config
# is the only reader of that sentinel; nothing else needs to care about it.
#
# A begin marker is matched by the literal prefix `# --- added by `; its id
# is everything up to the next space (a trailing `(removed by ...) ---` or
# a bare ` ---` both fall off there). An end marker is matched by the
# literal prefix `# --- end ` and literal suffix ` ---`; its id is
# whatever sits between them.
#
# Fatal, in all three modes, naming the file, line number and plugin id,
# exit 2: a begin marker reached before the previous block's end marker; an
# end marker with no open block; an end marker whose id does not match the
# open block's id; and, at end of file, a block that was never closed.
HS_SPLICE_SENTINEL=$'\001HS_ANCHOR\001'

#
# A config written on Windows ends every line with CR LF. The carriage
# return is stripped before a line is MATCHED (an end marker otherwise ends
# in `---\r`, matches nothing, and the file is reported as an unterminated
# block -- fail-closed, but for a reason nothing in the message explains),
# and the line is still printed exactly as it was read, so a CRLF file
# survives a strip/extract/splice round trip byte for byte.
_hs_plugin_block_walk() {
  local file="$1" mode="$2"
  local line match line_num=0 open_id="" open_line=0 id stripped_count=0

  while IFS= read -r line || [ -n "$line" ]; do
    line_num=$((line_num + 1))
    match="${line%$'\r'}"
    case "$match" in
      '# --- added by '*)
        id="${match#\# --- added by }"
        id="${id%% *}"
        if [ -n "$open_id" ]; then
          echo "herdr-setup: $file:$line_num: plugin block '$id' opened before '$open_id' (opened at line $open_line) was closed" >&2
          return 2
        fi
        open_id="$id"
        open_line="$line_num"
        if [ "$mode" = "splice" ]; then
          printf '%s%d\n' "$HS_SPLICE_SENTINEL" "$stripped_count"
        fi
        if [ "$mode" = "extract" ] || [ "$mode" = "splice" ]; then
          printf '%s\n' "$line"
        fi
        continue
        ;;
      '# --- end '*' ---')
        id="${match#\# --- end }"
        id="${id% ---}"
        if [ -z "$open_id" ]; then
          echo "herdr-setup: $file:$line_num: end marker for plugin block '$id' with no matching begin" >&2
          return 2
        fi
        if [ "$id" != "$open_id" ]; then
          echo "herdr-setup: $file:$line_num: end marker '$id' does not match open block '$open_id' (opened at line $open_line)" >&2
          return 2
        fi
        open_id=""
        if [ "$mode" = "extract" ] || [ "$mode" = "splice" ]; then
          printf '%s\n' "$line"
        fi
        continue
        ;;
    esac

    if [ -n "$open_id" ]; then
      if [ "$mode" = "extract" ] || [ "$mode" = "splice" ]; then
        printf '%s\n' "$line"
      fi
    else
      if [ "$mode" = "strip" ]; then
        printf '%s\n' "$line"
      fi
      stripped_count=$((stripped_count + 1))
    fi
  done < "$file"

  if [ -n "$open_id" ]; then
    echo "herdr-setup: $file: unterminated plugin block '$open_id' (opened at line $open_line, no matching end marker)" >&2
    return 2
  fi

  return 0
}

# hs_strip_plugin_blocks <file>: prints the file with every plugin-written
# block removed (see _hs_plugin_block_walk).
hs_strip_plugin_blocks() {
  _hs_plugin_block_walk "$1" strip
}

# hs_extract_plugin_blocks <file>: prints only the plugin-written blocks,
# markers included, in order (see _hs_plugin_block_walk).
hs_extract_plugin_blocks() {
  _hs_plugin_block_walk "$1" extract
}

# hs_diff_config <host-config> <manifest-config>: the config section of
# `diff`. Compares the host's config.toml, with every plugin-written block
# stripped, against manifest/config.toml, byte for byte -- stripping is
# what makes the comparison fair, since the manifest never holds a
# plugin's own lines. Prints "config match" and returns 0 when the two are
# identical; otherwise prints "config drift:" followed by a unified diff
# labelled `host` and `manifest`, and returns 1.
#
# A host with no config.toml yet (a fresh host that has never run `apply`)
# is treated as empty, not an error. A missing or unreadable manifest
# config is fail-closed, matching hs_manifest_plugins: one stderr line
# naming the file, exit 2.
hs_diff_config() {
  local host_file="$1" manifest_file="$2"
  local host_tmp rc

  if [ ! -r "$manifest_file" ]; then
    echo "herdr-setup: $manifest_file: manifest config not found or unreadable" >&2
    return 2
  fi

  host_tmp="$(mktemp)" || return 2

  if [ -e "$host_file" ]; then
    if ! hs_strip_plugin_blocks "$host_file" > "$host_tmp"; then
      rm -f "$host_tmp"
      return 2
    fi
  fi

  if diff -q "$host_tmp" "$manifest_file" >/dev/null 2>&1; then
    echo "config match"
    rc=0
  else
    echo "config drift:"
    hs_unified_diff "$host_tmp" "$manifest_file" host manifest
    rc=1
  fi

  rm -f "$host_tmp"
  return "$rc"
}

# hs_unified_diff <a> <b> <label-a> <label-b>: prints a unified diff of two
# files and always succeeds. `diff` exits 1 when the files differ, which is
# the normal case at every call site here and would otherwise trip the
# entrypoint's `set -e`. Shared by hs_diff_config (which reports drift) and
# hs_apply_config (which shows the change it is about to write), so the two
# render the same change the same way.
hs_unified_diff() {
  diff -u -L "$3" -L "$4" "$1" "$2" || true
}

# hs_splice_config <host-config> <manifest-config>: builds the config
# `apply` should write, on stdout. Takes the manifest's operator lines
# verbatim as the new skeleton, and re-inserts every plugin-written block
# currently on the host, unchanged and in original relative order.
#
# Anchoring a block into a DIFFERENT file than the one it was extracted
# from has no ground truth in general -- the manifest never holds plugin
# blocks (they are per-host by definition), so there is no marker in the
# manifest to splice relative to. This uses the third mode on
# _hs_plugin_block_walk, `splice`, which records each block's anchor as
# the number of operator lines that preceded it on the host (the same
# count hs_strip_plugin_blocks would have emitted up to that point), and
# reinserts each block after that many lines into the manifest skeleton,
# offsetting later anchors by the lines already-inserted blocks added.
# This guarantees an exact reproduction in the steady state that matters
# most in practice -- when the host's own stripped content already equals
# the manifest, i.e. nothing but plugin blocks has changed since the last
# apply, which is the common case round to round -- and degrades
# gracefully otherwise: a manifest whose operator-line count changed
# still gets every block back, unchanged, in original relative order,
# just not necessarily at the exact original line. hs_py splice-config
# does the actual line-list insertion, since it is ordinary structured
# work, not a hot path (AGENTS.md: uv runs the tool's own scripts, batched
# behind lib/hs.py subcommands).
#
# A host with no config.toml yet (never ran `apply`) has no blocks to
# preserve; the manifest content passes through unchanged, the same
# "missing host is empty" convention hs_diff_config uses. A missing or
# unreadable manifest config is fail-closed: one stderr line naming the
# file, exit 2.
hs_splice_config() {
  local host_file="$1" manifest_file="$2"
  local blocks_tmp rc

  if [ ! -r "$manifest_file" ]; then
    echo "herdr-setup: $manifest_file: manifest config not found or unreadable" >&2
    return 2
  fi

  blocks_tmp="$(mktemp)" || return 2

  if [ -e "$host_file" ]; then
    # _hs_plugin_block_walk always fails with exit 2 (never any other
    # nonzero code) -- hardcoded here rather than captured via `$?` after
    # `!`, which would capture the NEGATED condition's status (0), not
    # the command's own.
    if ! _hs_plugin_block_walk "$host_file" splice > "$blocks_tmp"; then
      rm -f "$blocks_tmp"
      return 2
    fi
  fi

  hs_py splice-config "$manifest_file" < "$blocks_tmp"
  rc=$?
  rm -f "$blocks_tmp"
  return "$rc"
}

# hs_apply_plugins <manifest-file> <plugins-json>: the plugin half of
# `apply`. Built on the same primitives hs_diff_plugins uses
# (hs_manifest_plugins, hs_host_plugins, hs_resolve_ref) -- both sides
# read from disk, only the ref resolution reaches out to git, never to
# herdr, matching hs_diff_plugins' own contract. For every manifest
# plugin that is missing from the host, or whose pinned ref no longer
# resolves to the commit the host has recorded (moved, including a ref
# that no longer exists upstream), runs
# `herdr plugin install <source> --ref <ref>`, adding `--yes` when
# HS_YES=1. A plugin the host already has at the recorded commit makes no
# call at all, and a host plugin the manifest does not name is left
# alone -- apply never uninstalls (AGENTS.md: "never uninstall or
# disable").
#
# Without `--yes`, the install runs through hs_herdr_interactive, NOT
# hs_herdr_json. Herdr shows its own trust preview for each install and
# waits for an answer (design doc, Commands > apply), and that only works
# with the operator's terminal attached. Run inside a command substitution
# instead, the preview went into a variable and the operator watched an
# apparently hung command while herdr sat blocked on a read behind a prompt
# they never saw -- and that was the DEFAULT path, the one an operator who
# has not passed `--yes` takes. With `--yes` there is no prompt, so the
# checked wrapper is used and an error object is still caught by parsing.
#
# Reads HS_DRY_RUN and HS_YES from the environment, exactly as the
# entrypoint exports them: under HS_DRY_RUN=1, prints the command each
# drifted plugin would run and makes no call at all. Both are read with a
# `:-0` default so this file can be sourced under `set -u` without them.
# Returns 2 if the manifest or the host's plugins.json fails to parse, 1 if
# any install call itself failed, 0 otherwise. Does not check the preflight
# state itself -- cmd_apply calls hs_require_socket first, before this or
# the config half run at all.
hs_apply_plugins() {
  local manifest_file="$1" plugins_json="$2"
  local rc=0

  local manifest_tmp host_tmp
  manifest_tmp="$(mktemp)" || return 2
  host_tmp="$(mktemp)" || { rm -f "$manifest_tmp"; return 2; }

  if ! hs_manifest_plugins "$manifest_file" > "$manifest_tmp"; then
    rm -f "$manifest_tmp" "$host_tmp"
    return 2
  fi

  if ! hs_host_plugins "$plugins_json" > "$host_tmp"; then
    rm -f "$manifest_tmp" "$host_tmp"
    return 2
  fi

  # The manifest is read on fd 3, not on stdin. `done < "$manifest_tmp"`
  # makes the manifest the stdin of everything inside the loop, so the
  # interactive install below would have been answering Herdr's trust prompt
  # with the next line of plugins.list -- the operator's terminal never gets
  # a word in. Reading on a private fd leaves stdin where it belongs.
  local m_source m_ref
  while IFS=$'\t' read -r m_source m_ref <&3; do
    [ -z "$m_source" ] && continue

    local h_line needs_install=0
    h_line="$(awk -F'\t' -v src="$m_source" '$2 == src {print; exit}' "$host_tmp")"
    if [ -z "$h_line" ]; then
      needs_install=1
    else
      local h_resolved resolved
      IFS=$'\t' read -r _ _ _ h_resolved <<< "$h_line"
      if ! resolved="$(hs_resolve_ref "$m_source" "$m_ref")"; then
        resolved=""
      fi
      if [ -z "$resolved" ] || [ "$resolved" != "$h_resolved" ]; then
        needs_install=1
      fi
    fi

    if [ "$needs_install" -eq 1 ]; then
      local install_args install_rc=0
      if [ -n "$m_ref" ]; then
        install_args=(plugin install "$m_source" --ref "$m_ref")
      else
        # No ref in the manifest: install the way Herdr does by default, from
        # the repository's default branch. Passing --ref "" would be an error.
        install_args=(plugin install "$m_source")
      fi
      if [ "${HS_YES:-0}" -eq 1 ]; then
        install_args+=(--yes)
      fi
      if [ "${HS_DRY_RUN:-0}" -eq 1 ]; then
        echo "+ herdr ${install_args[*]}"
      elif [ "${HS_YES:-0}" -eq 1 ]; then
        hs_herdr_json "${install_args[@]}" >/dev/null || install_rc=$?
      else
        # Foreground, stdio inherited: Herdr's trust preview reaches the
        # operator and their answer reaches Herdr. Only the exit status is
        # read; nothing here parses an install's output.
        hs_herdr_interactive "${install_args[@]}" || install_rc=$?
      fi
      if [ "$install_rc" -ne 0 ]; then
        rc=1
      fi
    fi
  done 3< "$manifest_tmp"

  rm -f "$manifest_tmp" "$host_tmp"
  return "$rc"
}

# hs_confirm <question>: asks the operator on the terminal and returns 0
# only for an explicit yes. Returns 1 for anything else, including a no
# answer, an unreadable answer, and -- the case that matters -- a session
# with no terminal at all, where there is nobody to ask. Fail closed
# (AGENTS.md): silence is not consent. The prompt goes to stderr, so a
# caller printing a diff on stdout can still be piped.
hs_confirm() {
  local question="$1" answer=""

  if [ ! -t 0 ]; then
    echo "herdr-setup: $question, and there is no terminal to confirm on." >&2
    return 1
  fi

  printf 'herdr-setup: %s. Continue? [y/N] ' "$question" >&2
  read -r answer || answer=""
  case "$answer" in
    y|Y|yes|Yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# hs_backup_path <host-config>: the path the next backup of <host-config>
# goes to -- `<host-config>.bak.<UTC timestamp>`, with a counter appended if
# a file is already there (two applies inside one second). Never overwrites
# an existing file, which is the whole point: a single `.bak` slot meant the
# second apply destroyed the backup taken by the first, so the safety net
# survived exactly one mistake and the operator who noticed on the second
# run had already lost the content they wanted back.
hs_backup_path() {
  local host_file="$1"
  local base n candidate
  base="${host_file}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
  candidate="$base"
  n=1
  while [ -e "$candidate" ]; do
    candidate="${base}.${n}"
    n=$((n + 1))
  done
  printf '%s\n' "$candidate"
}

# hs_apply_config <host-config> <manifest-config>: the config half of
# `apply`. Builds the new content with hs_splice_config, writes it only
# when it actually differs from what is on the host, and only then: prints
# the unified diff of the change, backs the previous content up to a
# timestamped `<host-config>.bak.<UTC timestamp>` (skipped when the host had
# no config.toml yet -- nothing to back up), writes the new content to a
# temporary file in the SAME directory and renames it over the target (so
# the config is never observed half-written), then calls `herdr server
# reload-config` exactly once.
#
# This is the most destructive write the tool makes, and it used to be the
# quietest: "last write wins" is the documented design, but an apply that
# replaced a host's whole config with one plugin block printed nothing at
# all and exited 0, and `--dry-run` said only `+ write <path>`. So the diff
# is printed on every real write and every dry run, and a write that leaves
# the file with FEWER lines than it had is gated: it proceeds only with
# `--yes`, or with a yes answer at the terminal. Refused, it returns 4 and
# writes nothing -- the same "refused, nothing happened" status absorb uses
# for its own dirty-manifest guard. A shrink is normal drift when the
# operator tidied the manifest, and catastrophic when the manifest is empty;
# neither the tool nor the exit status can tell those apart, but the diff
# and one question can.
#
# The new file keeps the ORIGINAL's mode. `mktemp` creates 0600, and
# renaming that over the target silently took a 0644 config down to 0600,
# dropping group access and any ACL on it. `cp -p` copies the mode across
# portably (no `chmod --reference`, which is GNU-only) and the content is
# then written into that already-correctly-moded file. A config that did not
# exist before gets 0644.
#
# Reads HS_DRY_RUN and HS_YES from the environment like hs_apply_plugins,
# both with a `:-0` default so this file can be sourced under `set -u`
# without them: under HS_DRY_RUN=1, prints the diff and what it would do,
# and makes no write, no backup, and no herdr call.
#
# Returns 2 if hs_splice_config hit a hard error (propagated as-is -- a
# missing/unreadable manifest config, or a malformed plugin block on the
# host) OR if any step of the write itself failed (the backup, the content
# write, the rename -- a full disk reaches all three), 4 if a shrinking write
# was refused, 1 if the reload-config call itself failed, 0 otherwise
# (including the no-op case where nothing had changed).
hs_apply_config() {
  local host_file="$1" manifest_file="$2"
  local host_dir new_content_tmp changed rc=0
  local old_lines=0 new_lines=0 shrinks=0

  host_dir="$(dirname "$host_file")"

  new_content_tmp="$(mktemp)" || return 2
  # hs_splice_config always fails with exit 2 -- hardcoded here for the
  # same reason noted in hs_splice_config itself: `$?` captured right
  # after a `!`-negated condition is the negation's status, not the
  # command's.
  if ! hs_splice_config "$host_file" "$manifest_file" > "$new_content_tmp"; then
    rm -f "$new_content_tmp"
    return 2
  fi

  changed=1
  if [ -e "$host_file" ]; then
    if diff -q "$host_file" "$new_content_tmp" >/dev/null 2>&1; then
      changed=0
    fi
  elif [ ! -s "$new_content_tmp" ]; then
    changed=0
  fi

  if [ "$changed" -eq 0 ]; then
    rm -f "$new_content_tmp"
    return 0
  fi

  local empty_tmp=""
  if [ -e "$host_file" ]; then
    # awk's NR, not `wc -l`. wc counts NEWLINES: a file whose last line has
    # no newline is one short, so a five-line host config written without a
    # trailing newline counted as four against a four-line manifest, the
    # shrink was invisible, and a line was removed from a live config in
    # silence with exit 0 -- on exactly the default non-interactive path this
    # gate exists to hold. The design doc's own "One deliberate normalisation"
    # section is about hosts whose config arrives this way.
    old_lines="$(awk 'END{print NR}' < "$host_file")"
    new_lines="$(awk 'END{print NR}' < "$new_content_tmp")"
    if [ "$new_lines" -lt "$old_lines" ]; then
      shrinks=1
    fi
    echo "config change:"
    hs_unified_diff "$host_file" "$new_content_tmp" current new
  else
    empty_tmp="$(mktemp)" || { rm -f "$new_content_tmp"; return 2; }
    echo "config change:"
    hs_unified_diff "$empty_tmp" "$new_content_tmp" current new
    rm -f "$empty_tmp"
  fi

  if [ "$shrinks" -eq 1 ] && [ "${HS_YES:-0}" -ne 1 ]; then
    if ! hs_confirm "apply: this removes $((old_lines - new_lines)) line(s) from $host_file"; then
      echo "herdr-setup: apply: refused the config write; re-run with --yes to accept it." >&2
      rm -f "$new_content_tmp"
      return 4
    fi
  fi

  if [ "${HS_DRY_RUN:-0}" -eq 1 ]; then
    echo "+ write $host_file"
    echo "+ herdr server reload-config"
    rm -f "$new_content_tmp"
    return 0
  fi

  mkdir -p "$host_dir"

  # Every step below is CHECKED. None of them used to be: they all run under
  # the caller's `|| rc=$?`, which suppresses `set -e`, so a failure at any
  # one of them left hs_apply_config returning 0 -- an unchanged (or
  # half-written) config, a zero exit status, and a `reload-config` call
  # telling Herdr to re-read a file that had not changed. The realistic
  # trigger is a full disk, and the operator is told the apply worked.
  if [ -e "$host_file" ]; then
    if ! cp -p "$host_file" "$(hs_backup_path "$host_file")"; then
      echo "herdr-setup: apply: could not back up $host_file; nothing was written." >&2
      rm -f "$new_content_tmp"
      return 2
    fi
  fi

  local write_tmp
  write_tmp="$(mktemp "$host_dir/.herdr-setup-config.XXXXXX")" || {
    rm -f "$new_content_tmp"
    return 2
  }
  if [ -e "$host_file" ]; then
    # Mode (and ownership, where permitted) first; the content is written
    # into the temp file afterwards, which truncates it without touching
    # the mode cp -p just set.
    cp -p "$host_file" "$write_tmp" || {
      rm -f "$new_content_tmp" "$write_tmp"
      return 2
    }
  else
    chmod 644 "$write_tmp"
  fi
  if ! cat "$new_content_tmp" > "$write_tmp"; then
    echo "herdr-setup: apply: could not write the new config for $host_file; nothing was changed." >&2
    rm -f "$new_content_tmp" "$write_tmp"
    return 2
  fi
  rm -f "$new_content_tmp"
  if ! mv -f "$write_tmp" "$host_file"; then
    echo "herdr-setup: apply: could not install the new $host_file; nothing was changed." >&2
    rm -f "$write_tmp"
    return 2
  fi

  if ! hs_herdr_json server reload-config >/dev/null; then
    rc=1
  fi

  return "$rc"
}

# hs_absorb_header: the short header comment `absorb` writes atop both
# manifest files it rewrites. Names the tool only -- no hostname, username,
# or absolute path (AGENTS.md: "this repository is public"). Never fails.
hs_absorb_header() {
  cat <<'EOF'
# Written by `herdr-setup absorb` from a host's own Herdr state.
# Edit here, or run `herdr-setup absorb` again to refresh from a host;
# `herdr-setup apply` pushes this content back out to a host.
EOF
}

# hs_absorb_plugins <plugins-json>: prints the manifest/plugins.list
# content `absorb` should write -- the header, then one "<source> <ref>"
# line per host plugin, sorted by source, via hs_py's absorb-plugins
# subcommand (same disk-read contract as hs_host_plugins: a missing file
# is a host with no plugins yet, header only, exit 0; a malformed one is
# fatal, exit 2, naming the file).
hs_absorb_plugins() {
  local plugins_json="$1"
  hs_absorb_header
  hs_py absorb-plugins "$plugins_json"
}

# hs_absorb_config <host-config>: prints the manifest/config.toml content
# `absorb` should write -- the host config with every plugin-written block
# stripped (hs_strip_plugin_blocks), and NOTHING else. Deliberately
# carries no header, unlike hs_absorb_plugins: manifest/config.toml is not
# a herdr-setup-only format the way plugins.list is (whose `#` lines
# hs_manifest_plugins already treats as comments) -- it is spliced
# byte-for-byte back into a real host's live Herdr config by
# hs_apply_config, and a prepended header would (a) get written into the
# operator's actual config.toml on every apply, permanently, and (b)
# break the round trip's byte-exact steady-state case (journal
# 8f3a0c6f882c / P4): the manifest would then differ from the host's own
# stripped content by exactly those lines, forcing an unnecessary rewrite
# and reload-config call, and re-anchoring every plugin block. A host with
# no config.toml yet has nothing to absorb: no output, exit 0. A malformed
# plugin block on the host is fatal, exit 2 (hs_strip_plugin_blocks names
# the file, line and id) -- called as the condition of `if !`, never bare,
# and the failure is reported with a hardcoded `return 2` rather than a
# captured $?, matching the convention noted at hs_apply_config's own call
# site (a `!`-negated condition's $? is the negation's status, not the
# command's).
hs_absorb_config() {
  local host_file="$1"
  if [ -e "$host_file" ]; then
    if ! hs_strip_plugin_blocks "$host_file"; then
      return 2
    fi
  fi
  return 0
}

# hs_require_clean_manifest <repo-root>: absorb's whole safety net against
# eating an uncommitted hand edit (the design doc: "It refuses to run when
# the manifest has uncommitted changes", since absorb overwrites rather
# than merges). Prints one stderr line per dirty path under manifest/,
# from `git status --porcelain -- manifest/`, and returns 4 when there is
# any; it returns 4 as well when a file under manifest/ is not TRACKED (see
# the ignored-path note below). A clean tree, or no manifest/ directory at
# all yet (a first-ever absorb), returns 0 with no output. A missing `git`
# is a different,
# fail-closed failure mode (AGENTS.md: git is a prerequisite of this
# tool) -- one stderr line, exit 2 -- kept distinguishable from the
# dirty-manifest exit 4 by its own exit status.
#
# git's EXIT STATUS is what decides, not its output. The check used to read
# `git status --porcelain 2>/dev/null` and look at the text alone, so every
# way git can fail -- the checkout is not a git repository, .git is
# unreadable, git dies on a bad config -- produced an empty string, which
# reads exactly like a clean tree. The guard then reported "clean" and
# absorb overwrote a hand-edited manifest with no warning and exit 0: the
# precise outcome the guard exists to prevent, produced by the guard itself.
# Any non-zero status is now a refusal naming what git said (exit 2, the
# same hard-failure status as a missing git). Never "clean".
hs_require_clean_manifest() {
  local repo_root="$1"

  if ! command -v git >/dev/null 2>&1; then
    echo "herdr-setup: git is not on PATH; install git, then retry." >&2
    return 2
  fi

  local raw git_rc=0 dirty
  raw="$(git -C "$repo_root" status --porcelain -- manifest/ 2>&1)" || git_rc=$?
  if [ "$git_rc" -ne 0 ]; then
    echo "herdr-setup: absorb: cannot tell whether manifest/ has uncommitted changes: git status failed in $repo_root ($(printf '%s' "$raw" | hs_flatten)); refusing to overwrite." >&2
    return 2
  fi
  dirty="$(printf '%s' "$raw" | cut -c4-)"

  if [ -n "$dirty" ]; then
    echo "herdr-setup: absorb: manifest/ has uncommitted changes, refusing to overwrite:" >&2
    local f
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      echo "  $f" >&2
    done <<< "$dirty"
    return 4
  fi

  # A clean status listing is not the same as a clean manifest. `git status
  # --porcelain` says NOTHING about a path git is ignoring, which is the same
  # empty answer a clean tree gives -- so a fork that keeps its manifest
  # private (manifest/ in .gitignore) got "clean" for a file holding an
  # uncommitted hand edit, and absorb ate it with exit 0. An UNTRACKED file
  # was caught, because it shows up as `??`; an IGNORED one was invisible.
  #
  # So every file under manifest/ must be tracked. Git cannot vouch for a file
  # it is ignoring, and "git cannot tell me" is a refusal here, never a clean
  # tree -- the same rule the git-exit-status check above enforces.
  if [ -d "$repo_root/manifest" ]; then
    local file
    while IFS= read -r file; do
      [ -z "$file" ] && continue
      if git -C "$repo_root" ls-files --error-unmatch -- "$file" >/dev/null 2>&1; then
        continue
      fi
      echo "herdr-setup: absorb: $file is not tracked by git (it is ignored), so git cannot tell whether it holds an uncommitted hand edit; refusing to overwrite." >&2
      return 4
    done <<EOF
$(cd "$repo_root" && find manifest -type f | LC_ALL=C sort)
EOF
  fi

  return 0
}

# hs_absorb_check_no_home_path <file>: the last line of defense against
# leaking a real path into this public repository. Returns 1 (dirty) if
# any line in <file> contains this process's own $HOME, or a `/Users/` or
# `/home/` substring, and 0 (clean) otherwise. Absorb is the one command
# whose entire job is copying host state into the checkout, so it is the
# most able of any command here to leak one -- this check runs on every
# file absorb is about to write or print, regardless of --dry-run, and a
# hit refuses the whole command (cmd_absorb decides what to say and what
# exit status to use; this only decides pass/fail). Never writes, never
# prints on its own.
hs_absorb_check_no_home_path() {
  local file="$1" home="${HOME:-}" line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      *"/Users/"*|*"/home/"*)
        return 1
        ;;
    esac
    if [ -n "$home" ]; then
      case "$line" in
        *"$home"*)
          return 1
          ;;
      esac
    fi
  done < "$file"
  return 0
}

# hs_diff_integrations: the integration section of `diff`. Informational
# only -- integrations are never in the manifest (different hosts run
# different agents), so this never affects the exit status and always
# returns 0, the same contract as hs_preflight. Reads `herdr integration
# status` directly rather than through hs_herdr_json, because that call
# does not cross the socket and keeps working under a protocol mismatch or
# with no server running (only a missing herdr binary stops it, and even
# that is reported rather than treated as fatal).
#
# `herdr integration status` prints one line per detected agent:
#   <agent>: current (v<n>) (<path>)
#   <agent>: outdated (v<old> < v<new>) (<path>)
#   <agent>: not installed (<path>)
# Installed integrations (current or outdated) are reported, prefixed
# `integration: `; a not-installed agent is not an integration to report
# on and is skipped.
hs_diff_integrations() {
  if ! command -v herdr >/dev/null 2>&1; then
    echo "integrations: herdr not on PATH, skipped"
    return 0
  fi

  local output rc detail err_rc=0
  output="$(herdr integration status 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "integrations: herdr integration status failed: $(printf '%s' "$output" | hs_flatten)"
    return 0
  fi

  # A zero exit status is not an answer. A server that refuses with an error
  # object and exits 0 had that object split on newlines here, and every
  # fragment reported as an installed integration -- `diff` showed an
  # "integration" whose name was a piece of JSON. Same rule as the preflight
  # gate: the answer is parsed, not assumed (hs_response_error).
  detail="$(hs_response_error "$output")" || err_rc=$?
  if [ "$err_rc" -ne 1 ]; then
    echo "integrations: herdr integration status failed: $(printf '%s' "${detail:-$output}" | hs_flatten)"
    return 0
  fi

  local line
  while IFS= read -r line || [ -n "$line" ]; do
    [ -z "$line" ] && continue
    case "$line" in
      *': not installed'*) continue ;;
    esac
    echo "integration: $line"
  done <<< "$output"

  return 0
}

# hs_agent_detect_spec <target>: prints "<command><TAB><dir>" for one
# coding-agent target `onboard` (phase 9) knows how to look for, or returns
# 1 for a target it does not -- the caller then leaves that target out of
# the table entirely, the same as an agent this host shows no sign of.
#
# The command defaults to the target's own name and the directory to
# $HOME/.<target>, which is confirmed true for claude and codex ($HOME/.claude,
# $HOME/.codex -- CLAUDE_CONFIG_DIR/CODEX_HOME's own defaults, phase 7) and for
# copilot ($HOME/.copilot -- phase 8's COPILOT_HOME discovery). cursor and
# hermes follow the same convention by analogy, unconfirmed on a host with
# either CLI installed, the same status phase 7 recorded for CODEX_HOME before
# a later phase could check it.
#
# Two of Herdr's own integration targets break the pattern and are named
# explicitly rather than guessed, from a live `herdr integration status` on
# the host this phase was written against: antigravity-cli's own
# configuration lives under $HOME/.gemini, not $HOME/.antigravity-cli, and
# kimi's lives under $HOME/.kimi-code, not $HOME/.kimi.
#
# `herdr integration status` on that host also named nine further targets
# (pi, omp, devin, droid, kilo, qodercli, qwen, mastracode, grok) this table
# has no entry for. Rather than guess a command and a directory for a CLI
# this host cannot confirm, they are left out -- hs_detect_agents then omits
# them from its output, indistinguishable from an agent that is genuinely
# absent. Extending coverage is adding a case here, never touching the
# detection loop itself.
hs_agent_detect_spec() {
  local target="$1"
  case "$target" in
    claude) printf 'claude\t%s/.claude\n' "$HOME" ;;
    codex) printf 'codex\t%s/.codex\n' "$HOME" ;;
    opencode) printf 'opencode\t%s/.opencode\n' "$HOME" ;;
    copilot) printf 'copilot\t%s/.copilot\n' "$HOME" ;;
    cursor) printf 'cursor\t%s/.cursor\n' "$HOME" ;;
    hermes) printf 'hermes\t%s/.hermes\n' "$HOME" ;;
    antigravity-cli) printf 'antigravity-cli\t%s/.gemini\n' "$HOME" ;;
    kimi) printf 'kimi\t%s/.kimi-code\n' "$HOME" ;;
    *) return 1 ;;
  esac
}

# hs_detect_agents: prints one <target><TAB><how><TAB><state><TAB><detail>
# line per agent this host shows some sign of AND hs_agent_detect_spec
# knows how to look for. `how` is `path` when the agent's own command is on
# PATH, `config` when only its configuration directory exists, `both` for
# either. An agent with neither signal is omitted entirely -- there would
# be nothing to onboard.
#
# The target list, and each target's state, come from `herdr integration
# status` itself -- never a list this tool maintains, so a Herdr release
# that adds an eighteenth agent needs no change here to keep working (it
# stays omitted, same as any unrecognised target, until hs_agent_detect_spec
# above learns it). That call does not cross the socket (hs_diff_integrations,
# above, relies on the same fact), so this keeps working under a protocol
# mismatch or with no server running; cmd_onboard still gates the parts of
# onboard that DO need the socket -- installing, and the feed hand-off --
# on hs_require_socket separately.
#
# `state` is absent | current | outdated, read off herdr's own wording
# ("not installed" / "current (vN)" / "outdated (vOLD < vNEW)"); `detail`
# carries the "(...)" version text for current and outdated, and is empty
# for absent. A status line in none of those three shapes is skipped --
# better to omit an agent than report a state guessed from a line this
# tool does not recognise.
#
# Returns 2 if herdr is missing or `herdr integration status` itself fails
# (the state genuinely cannot be read); 0 otherwise, whether or not any
# agent was printed.
hs_detect_agents() {
  if ! command -v herdr >/dev/null 2>&1; then
    echo "herdr-setup: herdr is not on PATH; install Herdr, then retry." >&2
    return 2
  fi

  local output rc err_rc=0 err_detail
  output="$(herdr integration status 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "herdr-setup: herdr integration status: $(printf '%s' "$output" | hs_flatten)" >&2
    return 2
  fi

  # And a refusal that exits 0 is still a refusal. Read on the exit status
  # alone, an error object became agent lines: every fragment failed the shape
  # check below and was skipped, so this returned 0 having found nothing, and
  # `onboard` printed an empty table and exited 0 on a host whose Herdr had
  # refused to answer. The state cannot be read, which is exit 2 -- the same
  # status a non-zero exit gets, for the same reason.
  err_detail="$(hs_response_error "$output")" || err_rc=$?
  if [ "$err_rc" -ne 1 ]; then
    echo "herdr-setup: herdr integration status: $(printf '%s' "${err_detail:-$output}" | hs_flatten)" >&2
    return 2
  fi

  local line target rest spec cmd dir how state detail
  while IFS= read -r line || [ -n "$line" ]; do
    [ -z "$line" ] && continue
    case "$line" in
      *:*) ;;
      *) continue ;;
    esac

    target="${line%%:*}"
    rest="${line#*: }"

    if ! spec="$(hs_agent_detect_spec "$target")"; then
      continue
    fi
    IFS=$'\t' read -r cmd dir <<< "$spec"

    how=""
    if command -v "$cmd" >/dev/null 2>&1; then
      how="path"
    fi
    if [ -d "$dir" ]; then
      if [ -n "$how" ]; then how="both"; else how="config"; fi
    fi
    [ -z "$how" ] && continue

    detail=""
    case "$rest" in
      outdated\ \(*\)\ \(*\))
        state="outdated"
        detail="$(printf '%s' "$rest" | sed -E 's/^outdated \(([^)]*)\) \(.*\)$/\1/')"
        ;;
      current\ \(*\)\ \(*\))
        state="current"
        detail="$(printf '%s' "$rest" | sed -E 's/^current \(([^)]*)\) \(.*\)$/\1/')"
        ;;
      not\ installed\ \(*\))
        state="absent"
        detail=""
        ;;
      *)
        continue
        ;;
    esac

    printf '%s\t%s\t%s\t%s\n' "$target" "$how" "$state" "$detail"
  done <<< "$output"

  return 0
}

# hs_onboard_feed_adapters_dir <adapters_dir> <agent>...: builds a scratch
# directory holding only the named agents' adapters, symlinked in from
# <adapters_dir>, and prints its path. An agent with no adapter file there
# (cursor and hermes ship none yet -- design doc, "The four adapters")
# contributes nothing and is silently skipped; there is nothing feed could
# report for it anyway.
#
# This is how cmd_onboard scopes its post-install feed call to "exactly
# those agents" (the design doc's own phrase for onboard's hand-off):
# lib/feed.py discovers adapters by directory CONTENT (discover(), phase 6),
# so handing it a directory that holds only the agents just installed or
# refreshed scopes the run without teaching feed.py a filter flag no other
# caller needs. An agent whose integration was already current before this
# run is never in that directory, so it is never re-fed here -- a stale
# pane, if there is one, is `herdr-setup feed`'s own job on its own
# schedule, not onboard's.
#
# Prints nothing and returns 1 if the scratch directory itself cannot be
# created. The caller is responsible for removing the directory when done;
# this function only builds it.
hs_onboard_feed_adapters_dir() {
  local adapters_dir="$1"
  shift
  local dir
  dir="$(mktemp -d)" || return 1

  local agent
  for agent in "$@"; do
    [ -z "$agent" ] && continue
    if [ -x "$adapters_dir/$agent" ]; then
      ln -s "$adapters_dir/$agent" "$dir/$agent"
    fi
  done

  printf '%s\n' "$dir"
}

# hs_preflight_banner: prints the preflight state (see hs_preflight) as
# one line, so `diff` says up front whether the host and server actually
# agree before the sections below report their own detail. Purely
# informational: never fails, never affects the exit status.
hs_preflight_banner() {
  local state
  state="$(hs_preflight)"
  echo "preflight: $state"
}

# Directory holding this library, so the Python helper is found however the
# entrypoint was invoked (PATH, symlink, or an explicit path).
HS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run the pinned Python helper. uv resolves the interpreter named in the
# script's own PEP 723 header, so every host runs the same version regardless of
# what it happens to have installed. uv runs this tool's own scripts and nothing
# else: the host's Herdr is invoked exactly as installed.
hs_py() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "herdr-setup: uv is not on PATH; install it from https://docs.astral.sh/uv/ and retry." >&2
    return 2
  fi
  uv run --quiet --script "$HS_LIB_DIR/hs.py" "$@"
}

# hs_feed: run lib/feed.py, the feed runner. Reached through uv exactly the
# way hs_py reaches lib/hs.py -- Python comes from uv, not from the host
# (AGENTS.md), and uv runs this tool's own scripts and nothing else.
#
# It is its own door rather than a subcommand of hs.py because it is its own
# program: it spawns adapters, holds a conversation with the operator, and
# opens the Herdr socket, none of which the batched structured helpers do.
# stdin, stdout and stderr are all INHERITED -- feed prompts when it is not
# certain which session a pane is in, and a command substitution around a
# command that wants the terminal is the bug hs_herdr_interactive exists to
# undo (journal ac30ffbfb635).
hs_feed() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "herdr-setup: uv is not on PATH; install it from https://docs.astral.sh/uv/ and retry." >&2
    return 2
  fi
  uv run --quiet --script "$HS_LIB_DIR/feed.py" "$@"
}
