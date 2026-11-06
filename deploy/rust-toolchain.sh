#!/usr/bin/env bash

RUST_TOOLCHAIN_VERSION=1.99.1

install_rust_toolchain() {
  local rustup_bin="$SERVICE_HOME/.cargo/bin/rustup"
  local installed_version

  if [[ ! -x "$rustup_bin" ]]; then
    echo "Installing Rust $RUST_TOOLCHAIN_VERSION for $SERVICE_USER..."
    download_and_run_as_user \
      "https://sh.rustup.rs" /bin/sh \
      -y --default-toolchain "$RUST_TOOLCHAIN_VERSION" --profile minimal
  fi
  [[ -x "$rustup_bin" ]] || {
    echo "Rustup installation did not produce $rustup_bin." >&2
    return 1
  }

  installed_version="$(as_user "$rustup_bin" run "$RUST_TOOLCHAIN_VERSION" rustc --version 2>/dev/null | awk '{ print $2 }' || true)"
  if [[ "$installed_version" != "$RUST_TOOLCHAIN_VERSION" ]]; then
    echo "Installing Rust $RUST_TOOLCHAIN_VERSION for $SERVICE_USER..."
    as_user "$rustup_bin" toolchain install "$RUST_TOOLCHAIN_VERSION" --profile minimal
    installed_version="$(as_user "$rustup_bin" run "$RUST_TOOLCHAIN_VERSION" rustc --version 2>/dev/null | awk '{ print $2 }' || true)"
  else
    echo "Reusing the installed Rust $RUST_TOOLCHAIN_VERSION toolchain."
  fi
  [[ "$installed_version" == "$RUST_TOOLCHAIN_VERSION" ]] || {
    echo "Expected Rust $RUST_TOOLCHAIN_VERSION for $SERVICE_USER; found ${installed_version:-no usable rustc}." >&2
    return 1
  }
}
