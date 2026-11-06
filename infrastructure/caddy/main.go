// The entry point the web image compiles Caddy from: Caddy's own cmd/caddy/main.go,
// with the standard modules and no plugins. It lives here, rather than being
// installed straight from Caddy's module, so apps/web/Dockerfile can raise
// individual dependencies above the versions Caddy's go.mod asks for.
package main

import (
	caddycmd "github.com/caddyserver/caddy/v2/cmd"

	_ "github.com/caddyserver/caddy/v2/modules/standard"
)

func main() {
	caddycmd.Main()
}
