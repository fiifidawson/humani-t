#!/usr/bin/env bash
# Check the security headers Caddy and the API send, through the running stack.
#
#   .github/scripts/check-security-headers.sh https://localhost:58443
#
# -k: the stack under test uses Caddy's internal certificate authority for localhost.
set -euo pipefail

base="${1:?usage: $0 <base url>}"
failures=0

headers_of() {
  curl --silent --show-error --insecure --max-time 10 --dump-header - --output /dev/null "$1" \
    | tr -d '\r'
}

expect() {
  local url="$1" pattern="$2" description="$3"
  if grep --quiet --ignore-case --extended-regexp "$pattern" <<<"$(headers_of "$url")"; then
    echo "ok    $description"
  else
    echo "FAIL  $description ($url)"
    failures=$((failures + 1))
  fi
}

refuse() {
  local url="$1" pattern="$2" description="$3"
  if grep --quiet --ignore-case --extended-regexp "$pattern" <<<"$(headers_of "$url")"; then
    echo "FAIL  $description ($url)"
    failures=$((failures + 1))
  else
    echo "ok    $description"
  fi
}

for url in "$base/" "$base/api/v1/health"; do
  echo "== $url"
  expect "$url" '^HTTP/[0-9.]+ 200' "answers 200"
  expect "$url" '^strict-transport-security: max-age=[0-9]{8,}; includeSubDomains' "HSTS for at least a year"
  expect "$url" "^content-security-policy: .*frame-ancestors 'none'" "CSP forbids framing"
  expect "$url" '^x-content-type-options: nosniff$' "no MIME sniffing"
  expect "$url" '^referrer-policy: no-referrer$' "no Referer sent"
  expect "$url" '^cross-origin-opener-policy: same-origin$' "COOP same-origin"
  expect "$url" '^cross-origin-resource-policy: same-origin$' "CORP same-origin"
  refuse "$url" '^server:' "no Server header"
  refuse "$url" '^via:' "no Via header"
done

echo "== the single-page app's policy"
expect "$base/" "^content-security-policy: .*script-src 'self';" "scripts from this origin only"
expect "$base/" "^content-security-policy: .*style-src 'self' 'nonce-[0-9a-f-]{36}';" "styles from this origin or with the nonce"
refuse "$base/" "^content-security-policy: .*unsafe-inline" "no 'unsafe-inline'"
expect "$base/" "^content-security-policy: .*require-trusted-types-for 'script'" "Trusted Types enforced"

echo "== the page's style nonce"
# Caddy fills a fresh nonce into index.html and its CSP header on every response.
# The two must match, differ between responses, and never reach hashed assets.
nonce_pair() {
  local page header body
  page=$(curl --silent --show-error --insecure --max-time 10 --dump-header - "$1" | tr -d '\r')
  header=$(grep -i '^content-security-policy:' <<<"$page" | grep -o "'nonce-[^']*'" | head -1 | sed "s/'nonce-//; s/'//")
  body=$(grep -o 'nonce="[^"]*"' <<<"$page" | sort -u | sed 's/nonce="//; s/"//')
  echo "$header $body"
}
read -r first_header first_body <<<"$(nonce_pair "$base/")"
read -r second_header _ <<<"$(nonce_pair "$base/")"
if [[ -n "$first_header" && "$first_header" == "$first_body" && "$(wc -w <<<"$first_body")" == 1 ]]; then
  echo "ok    the page carries the nonce its header allows"
else
  echo "FAIL  page nonce '$first_body' does not match header nonce '$first_header'"
  failures=$((failures + 1))
fi
if [[ "$first_header" != "$second_header" ]]; then
  echo "ok    a new nonce on every response"
else
  echo "FAIL  the nonce repeated across responses"
  failures=$((failures + 1))
fi
script=$(curl --silent --insecure --max-time 10 "$base/" | grep -o 'src="/assets/[^"]*\.js"' | head -1 | sed 's/src="//; s/"//')
if [[ -n "$script" ]] && ! curl --silent --insecure --compressed --max-time 10 "$base$script" | grep --quiet 'http.request.uuid'; then
  echo "ok    hashed assets are not templated"
else
  echo "FAIL  could not confirm $script is served untouched"
  failures=$((failures + 1))
fi

echo "== the API's policy"
expect "$base/api/v1/health" "^content-security-policy: default-src 'none'" "API responses can load nothing"
expect "$base/api/v1/health" '^cache-control: no-store' "API responses are not cached"

if ((failures > 0)); then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all header checks passed"
