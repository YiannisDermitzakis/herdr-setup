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
# Bash 3.2 / Python 3.9 floor: no associative arrays, no mapfile, no
# `local -n`, no tomllib.

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

# hs_preflight: echoes exactly one of no-herdr | no-server | mismatched |
# matched. Never writes anything and never fails: every command decides
# what to do with the state itself (diff keeps going on no-server/
# mismatched; hs_require_socket below is what the write commands use to
# stop).
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

  # Detect a version mismatch by running a real socket call and checking
  # its exit status FIRST: a blocked herdr answers with a JSON error object
  # and exits non-zero, never with an empty success. Only after confirming
  # the call failed do we look at what it said.
  local output rc
  output="$(herdr plugin list 2>/dev/null)"
  rc=$?
  if [ "$rc" -ne 0 ] && printf '%s' "$output" | grep -q '"code":"protocol_mismatch"'; then
    echo "mismatched"
    return 0
  fi

  echo "matched"
  return 0
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
    *)
      echo "herdr-setup: unrecognized preflight state '$state'." >&2
      return 3
      ;;
  esac
}

# hs_herdr_json: the single wrapper every later phase uses to call herdr.
# Runs `herdr "$@"`, checks the exit status FIRST, and NEVER returns a
# result from a failed call — a blocked herdr answers with a JSON error
# object rather than an empty result, and a caller that reads .result.*
# without checking the exit status would see zero of everything and report
# success having done nothing. Returns non-zero on a failed exit status or
# on an "error" key in the output (even if the exit status was 0), and
# prints the error message to stderr in either case. On success, prints the
# herdr output on stdout unchanged and returns 0.
hs_herdr_json() {
  local output rc message is_error
  output="$(herdr "$@" 2>&1)"
  rc=$?

  # Whether this is an error is decided by parsing the response, not by looking
  # for a substring: a perfectly good response can carry the token "error" in a
  # value, and a substring test reports that successful call as a failure. The
  # cheap test is only a filter for whether there is anything to parse, and it
  # cannot miss a real error, because an error key always puts the token in the
  # text.
  message=""
  is_error=0
  case "$output" in
    *'"error"'*)
      if message="$(printf '%s' "$output" | hs_py herdr-error)"; then
        is_error=1
        if [ -z "$message" ]; then
          message="$output"
        fi
      fi
      ;;
  esac

  if [ "$rc" -ne 0 ] || [ "$is_error" -eq 1 ]; then
    echo "herdr-setup: herdr $*: ${message:-$output}" >&2
    if [ "$rc" -eq 0 ]; then
      return 1
    fi
    return "$rc"
  fi

  printf '%s\n' "$output"
  return 0
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
hs_manifest_plugins() {
  local file="$1"
  local line_num=0
  local line trimmed field_count source ref

  if [ ! -r "$file" ]; then
    echo "herdr-setup: $file: manifest not found or unreadable" >&2
    return 2
  fi

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
    set +f
    field_count="$#"
    source="${1:-}"
    ref="${2:-}"

    if [ "$field_count" -ne 2 ]; then
      echo "herdr-setup: $file:$line_num: expected '<source> <ref>', got '$trimmed'" >&2
      return 2
    fi

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
# commit sha it resolves to. Empty output from ls-remote means the ref no
# longer exists upstream; hs_resolve_ref then echoes nothing and returns 1,
# leaving the caller to report that as drift.
hs_resolve_ref() {
  local source="$1" ref="$2"
  local owner_repo url output sha

  owner_repo="$(printf '%s' "$source" | cut -d/ -f1-2)"
  url="https://github.com/${owner_repo}.git"

  output="$(git ls-remote "$url" "$ref" 2>/dev/null)"
  sha="$(printf '%s' "$output" | awk '{print $1}' | head -n1)"

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

_hs_plugin_block_walk() {
  local file="$1" mode="$2"
  local line line_num=0 open_id="" open_line=0 id stripped_count=0

  while IFS= read -r line || [ -n "$line" ]; do
    line_num=$((line_num + 1))
    case "$line" in
      '# --- added by '*)
        id="${line#\# --- added by }"
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
        id="${line#\# --- end }"
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
    diff -u -L host -L manifest "$host_tmp" "$manifest_file" || true
    rc=1
  fi

  rm -f "$host_tmp"
  return "$rc"
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
# Reads HS_DRY_RUN and HS_YES from the environment, exactly as the
# entrypoint exports them: under HS_DRY_RUN=1, prints the command each
# drifted plugin would run and makes no call at all. Returns 2 if the
# manifest or the host's plugins.json fails to parse, 1 if any install
# call itself failed, 0 otherwise. Does not check the preflight state
# itself -- cmd_apply calls hs_require_socket first, before this or the
# config half run at all.
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

  local m_source m_ref
  while IFS=$'\t' read -r m_source m_ref; do
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
      local install_args
      install_args=(plugin install "$m_source" --ref "$m_ref")
      if [ "$HS_YES" -eq 1 ]; then
        install_args+=(--yes)
      fi
      if [ "$HS_DRY_RUN" -eq 1 ]; then
        echo "+ herdr ${install_args[*]}"
      else
        if ! hs_herdr_json "${install_args[@]}" >/dev/null; then
          rc=1
        fi
      fi
    fi
  done < "$manifest_tmp"

  rm -f "$manifest_tmp" "$host_tmp"
  return "$rc"
}

# hs_apply_config <host-config> <manifest-config>: the config half of
# `apply`. Builds the new content with hs_splice_config, writes it only
# when it actually differs from what is on the host, and only then: backs
# the previous content up to `<host-config>.bak` first (skipped when the
# host had no config.toml yet -- nothing to back up), writes the new
# content to a temporary file in the SAME directory and renames it over
# the target (so the config is never observed half-written), then calls
# `herdr server reload-config` exactly once.
#
# Reads HS_DRY_RUN from the environment like hs_apply_plugins: under
# HS_DRY_RUN=1, prints what it would do and makes no write, no backup, and
# no herdr call, even when the content would have changed.
#
# Returns 2 if hs_splice_config hit a hard error (propagated as-is -- a
# missing/unreadable manifest config, or a malformed plugin block on the
# host), 1 if the reload-config call itself failed, 0 otherwise (including
# the no-op case where nothing had changed).
hs_apply_config() {
  local host_file="$1" manifest_file="$2"
  local host_dir new_content_tmp changed rc=0

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

  if [ "$HS_DRY_RUN" -eq 1 ]; then
    echo "+ write $host_file"
    echo "+ herdr server reload-config"
    rm -f "$new_content_tmp"
    return 0
  fi

  mkdir -p "$host_dir"

  if [ -e "$host_file" ]; then
    cp "$host_file" "${host_file}.bak"
  fi

  local write_tmp
  write_tmp="$(mktemp "$host_dir/.herdr-setup-config.XXXXXX")" || {
    rm -f "$new_content_tmp"
    return 2
  }
  cat "$new_content_tmp" > "$write_tmp"
  rm -f "$new_content_tmp"
  mv -f "$write_tmp" "$host_file"

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
# any; a clean tree, or no manifest/ directory at all yet (a first-ever
# absorb), returns 0 with no output. A missing `git` is a different,
# fail-closed failure mode (AGENTS.md: git is a prerequisite of this
# tool) -- one stderr line, exit 2 -- kept distinguishable from the
# dirty-manifest exit 4 by its own exit status.
hs_require_clean_manifest() {
  local repo_root="$1"

  if ! command -v git >/dev/null 2>&1; then
    echo "herdr-setup: git is not on PATH; install git, then retry." >&2
    return 2
  fi

  local dirty
  dirty="$(git -C "$repo_root" status --porcelain -- manifest/ 2>/dev/null | cut -c4-)"

  if [ -n "$dirty" ]; then
    echo "herdr-setup: absorb: manifest/ has uncommitted changes, refusing to overwrite:" >&2
    local f
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      echo "  $f" >&2
    done <<< "$dirty"
    return 4
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

  local output rc
  output="$(herdr integration status 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "integrations: herdr integration status failed: $output"
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
