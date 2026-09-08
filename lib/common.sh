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
# hs_strip_plugin_blocks (mode=strip) and hs_extract_plugin_blocks
# (mode=extract). A plugin-written block is every line from
# `# --- added by <id> ...` through the following `# --- end <id> ---`,
# inclusive, matching the id captured at the begin marker against the id
# captured at the end marker rather than accepting any end marker. strip
# prints every line OUTSIDE such a block; extract prints every line INSIDE
# one, markers included, in file order -- the two outputs partition the
# file's lines between them, and extract's markers are what let a later
# splice put a block back exactly as it was.
#
# A begin marker is matched by the literal prefix `# --- added by `; its id
# is everything up to the next space (a trailing `(removed by ...) ---` or
# a bare ` ---` both fall off there). An end marker is matched by the
# literal prefix `# --- end ` and literal suffix ` ---`; its id is
# whatever sits between them.
#
# Fatal, in both modes, naming the file, line number and plugin id, exit 2:
# a begin marker reached before the previous block's end marker; an end
# marker with no open block; an end marker whose id does not match the
# open block's id; and, at end of file, a block that was never closed.
_hs_plugin_block_walk() {
  local file="$1" mode="$2"
  local line line_num=0 open_id="" open_line=0 id

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
        if [ "$mode" = "extract" ]; then
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
        if [ "$mode" = "extract" ]; then
          printf '%s\n' "$line"
        fi
        continue
        ;;
    esac

    if [ -n "$open_id" ]; then
      if [ "$mode" = "extract" ]; then
        printf '%s\n' "$line"
      fi
    else
      if [ "$mode" = "strip" ]; then
        printf '%s\n' "$line"
      fi
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
# what it happens to have installed. Keep this off hot paths: a uv start costs
# roughly a quarter of a second.
hs_py() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "herdr-setup: uv is not on PATH; install it from https://docs.astral.sh/uv/ and retry." >&2
    return 2
  fi
  uv run --quiet --script "$HS_LIB_DIR/hs.py" "$@"
}
