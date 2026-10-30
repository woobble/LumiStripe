# Shared TLS certificate lifecycle for the systemd installer and updater.

# Read generated files with the service account's permissions. The temporary
# directory used by mkcert is writable by that account, so root must not follow
# paths in it directly when importing files into the protected TLS directory.
copy_lumistripe_tls_file() {
  local service_user="$1" source="$2" destination="$3"
  local size
  if ! timeout 10s runuser -u "$service_user" -- /usr/bin/head -c 1048577 -- "$source" > "$destination"; then
    return 1
  fi
  size="$(wc -c < "$destination")" || return 1
  [[ "$size" -gt 0 && "$size" -le 1048576 ]]
}

ensure_lumistripe_tls_certificate() {
  local tls_dir="$1"
  local tls_cert="$2"
  local tls_key="$3"
  local service_user="$4"
  local service_group="$5"
  local service_home="$6"
  local cert_tmp_dir
  local secure_tmp_dir
  local host_address
  local -a certificate_names=(led.controller localhost 127.0.0.1)
  local -a host_addresses=()

  install -d -o root -g root -m 700 "$tls_dir"

  if [[ ! -s "$tls_cert" || ! -s "$tls_key" ]]; then
    command -v mkcert >/dev/null 2>&1 || {
      echo "mkcert is required to create the LumiStripe HTTPS certificate." >&2
      return 1
    }

    echo "Generating the local mkcert HTTPS certificate for led.controller..." >&2
    cert_tmp_dir="$(mktemp -d /tmp/lumistripe-certificate.XXXXXX)"
    if ! chown "$service_user:$service_group" "$cert_tmp_dir"; then
      rm -rf -- "$cert_tmp_dir"
      return 1
    fi

    read -r -a host_addresses <<< "$(hostname -I 2>/dev/null || true)"
    for host_address in "${host_addresses[@]:-}"; do
      [[ -n "$host_address" ]] && certificate_names+=("$host_address")
    done

    if ! runuser -u "$service_user" -- env \
      HOME="$service_home" \
      PATH="$service_home/.bun/bin:$service_home/.local/bin:/usr/local/bin:/usr/bin:/bin" \
      mkcert -install >&2; then
      rm -rf -- "$cert_tmp_dir"
      return 1
    fi
    if ! runuser -u "$service_user" -- env \
      HOME="$service_home" \
      PATH="$service_home/.bun/bin:$service_home/.local/bin:/usr/local/bin:/usr/bin:/bin" \
      mkcert \
        -cert-file "$cert_tmp_dir/cert.pem" \
        -key-file "$cert_tmp_dir/key.pem" \
        "${certificate_names[@]}" >&2; then
      rm -rf -- "$cert_tmp_dir"
      return 1
    fi
    secure_tmp_dir="$(mktemp -d /tmp/lumistripe-certificate-import.XXXXXX)" || {
      rm -rf -- "$cert_tmp_dir"
      return 1
    }
    if ! copy_lumistripe_tls_file "$service_user" "$cert_tmp_dir/cert.pem" "$secure_tmp_dir/cert.pem" || \
      ! copy_lumistripe_tls_file "$service_user" "$cert_tmp_dir/key.pem" "$secure_tmp_dir/key.pem"; then
      echo "mkcert did not create usable TLS files." >&2
      rm -rf -- "$secure_tmp_dir"
      rm -rf -- "$cert_tmp_dir"
      return 1
    fi
    if ! install -o root -g root -m 644 "$secure_tmp_dir/cert.pem" "$tls_cert" || \
      ! install -o root -g root -m 600 "$secure_tmp_dir/key.pem" "$tls_key"; then
      rm -rf -- "$secure_tmp_dir"
      rm -rf -- "$cert_tmp_dir"
      return 1
    fi
    rm -rf -- "$secure_tmp_dir"
    rm -rf -- "$cert_tmp_dir"
  fi

  chown root:root "$tls_cert" "$tls_key"
  chmod 644 "$tls_cert"
  chmod 600 "$tls_key"
}
