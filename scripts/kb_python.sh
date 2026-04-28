#!/usr/bin/env bash
# Shared helper for resolving the repo-configured Python interpreter.

resolve_repo_python() {
  local repo_root="$1"
  local setup_hint="${2:-./kb setup}"
  local python_file="${repo_root}/.kb-python"
  local python_bin=""

  if [[ ! -f "${python_file}" ]]; then
    echo "Missing ${python_file}. Run ${setup_hint} first." >&2
    return 1
  fi

  python_bin="$(head -n 1 "${python_file}")"
  if [[ -z "${python_bin}" ]]; then
    echo "${python_file} is empty. Run ${setup_hint} again." >&2
    return 1
  fi

  if [[ ! -x "${python_bin}" ]]; then
    echo "Configured Python is not executable: ${python_bin}" >&2
    echo "Run ${setup_hint} again." >&2
    return 1
  fi

  printf '%s\n' "${python_bin}"
}

resolve_repo_landrun() {
  local repo_root="$1"
  local setup_hint="${2:-./kb setup}"
  local landrun_bin="${LANDRUN:-${repo_root}/third_party/bin/landrun}"

  if [[ ! -x "${landrun_bin}" ]]; then
    echo "Configured Landrun binary is not executable: ${landrun_bin}" >&2
    echo "Run ${setup_hint} again." >&2
    return 1
  fi

  printf '%s\n' "${landrun_bin}"
}
