#!/usr/bin/env sh

set -eu
. "$(dirname "$0")/env.sh"

mkdir -p "$KG_DEBUGGER_ROOT/pem"

if openssl x509 -in "$KG_DEBUGGER_CERT" -noout >/dev/null 2>&1; then
  exit 0
fi

cat > "$KG_DEBUGGER_ROOT/pem/localhost-openssl.cnf" <<'EOF'
[req]
distinguished_name = subject
x509_extensions = localhost
prompt = no

[subject]
CN = localhost

[localhost]
subjectAltName = DNS:localhost,IP:127.0.0.1,IP:::1
EOF

openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout "$KG_DEBUGGER_KEY" \
  -out "$KG_DEBUGGER_CERT" \
  -config "$KG_DEBUGGER_ROOT/pem/localhost-openssl.cnf" \
  -extensions localhost \
  >/dev/null 2>&1

chmod 600 "$KG_DEBUGGER_KEY"
chmod 644 "$KG_DEBUGGER_CERT"
