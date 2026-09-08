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
  local output rc message
  output="$(herdr "$@" 2>&1)"
  rc=$?

  message=""
  if [ -n "$output" ]; then
    message="$(printf '%s' "$output" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = None
if isinstance(data, dict):
    err = data.get("error")
    if isinstance(err, dict):
        print(err.get("message", ""))
' 2>/dev/null)"
  fi

  if [ "$rc" -ne 0 ] || [ -n "$message" ]; then
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

  python3 - "$file" <<'PYEOF'
import json
import sys

path = sys.argv[1]

try:
    with open(path) as fh:
        data = json.load(fh)
except (ValueError, OSError) as exc:
    sys.stderr.write("herdr-setup: %s: %s\n" % (path, exc))
    sys.exit(2)

if not isinstance(data, list):
    sys.stderr.write("herdr-setup: %s: expected a JSON array of plugins\n" % path)
    sys.exit(2)

rows = []
for entry in data:
    try:
        plugin_id = entry["plugin_id"]
        source = entry["source"]
        owner = source["owner"]
        repo = source["repo"]
        requested_ref = source["requested_ref"]
        resolved_commit = source["resolved_commit"]
    except (KeyError, TypeError):
        sys.stderr.write("herdr-setup: %s: malformed plugin entry: %r\n" % (path, entry))
        sys.exit(2)

    src = "%s/%s" % (owner, repo)
    subdir = source.get("subdir")
    if subdir:
        src = "%s/%s" % (src, subdir)

    rows.append((plugin_id, src, requested_ref, resolved_commit))

rows.sort(key=lambda row: row[0])
for plugin_id, src, requested_ref, resolved_commit in rows:
    print("%s\t%s\t%s\t%s" % (plugin_id, src, requested_ref, resolved_commit))
PYEOF
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
