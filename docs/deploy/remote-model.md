# Running the model on another machine

humani-t calls a language model through [Ollama](https://ollama.com). By default Ollama runs inside the stack (the `local-model` profile in [`compose.yaml`](../../compose.yaml)), so no text leaves the deployment. If the humani-t server is too small to run a model, you can run Ollama on a second machine **that you control**, such as a virtual server at DigitalOcean, Hetzner or a similar provider, and point humani-t at it.

This guide sets that up safely. Read [the self-hosting guide](self-hosting.md) first; this one assumes a running humani-t.

## What changes when the model moves

humani-t masks personal data before any text reaches a model, wherever the model runs. Moving the model to a second machine still means:

- **Masked text crosses the internet** between your two servers. It must be encrypted in transit and must reach only your model server.
- **The provider hosting the second machine is now part of your deployment.** Its staff and its legal jurisdiction can, in principle, reach that machine's memory and disk. Choose the provider and region with the same care as for the humani-t server itself, and check your organisation's data-protection obligations before any real data is processed.
- **Ollama has no authentication at all.** Anyone who can reach its port can run any model, download new ones until the disk fills, or delete yours. Internet-wide scans regularly find thousands of exposed Ollama servers, and Ollama has had serious vulnerabilities reachable through that port (for example CVE-2024-37032, a path traversal that allowed remote code execution). **Never expose Ollama's port, 11434, to the internet**, not even for a quick test.

So Ollama itself listens only on the second machine's loopback interface or on a private tunnel, and one of two things stands between it and humani-t:

| | **A. A private tunnel** (WireGuard or Tailscale) | **B. Caddy with TLS and a bearer token** |
| --- | --- | --- |
| Public ports on the model server | None (WireGuard: one UDP port) | 443, and 80 for certificate renewal |
| Encryption | The tunnel | HTTPS |
| Who can connect | Only machines holding a tunnel key | Only the humani-t server's IP (firewall), and only with the token |
| humani-t setting | `http://<tunnel address>:11434`, no token | `https://<model host name>`, with the token |
| Choose it when | You can install software on both servers. Simpler to keep closed. | A tunnel is not possible, or the model server is shared with other trusted services. |

Prefer **A**. Whichever you choose, also apply [step 3](#3-the-firewall).

## 1. Choose a machine and a model

Without a GPU, the model runs on the CPU, so memory decides what fits and the number of cores decides how fast it answers. A model needs roughly its download size in RAM, plus 1 to 2 GB for the conversation it is working on, plus what the operating system uses.

| Server RAM | Largest comfortable model (4-bit, as Ollama ships them) | Example |
| --- | --- | --- |
| 4 GB | about 1.5 B parameters | `qwen3:1.7b` (about 1.4 GB) |
| 8 GB | about 4 B parameters | `qwen3:4b` (about 2.5 GB), `llama3.2:3b` (about 2 GB) |
| 16 GB | about 8 B parameters | `llama3.1:8b` (about 4.9 GB), humani-t's default |

Model sizes change between releases; check the size on the model's page at ollama.com before choosing. On a shared 4-vCPU server, expect answers at a few words per second: slow, but humani-t runs model calls in the background worker, and gives each call up to five minutes (`inference_timeout_seconds`). A provider's "dedicated CPU" plans are noticeably faster than shared ones for the same price band.

## 2. Prepare the model server

On the new server (Ubuntu 24.04 here), logged in over SSH with a key:

```bash
# Updates, applied automatically from now on.
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades ufw

# SSH by key only.
sudo sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl reload ssh
```

Install Ollama. Its installer is a shell script; read it before running it:

```bash
curl -fsSL https://ollama.com/install.sh -o ollama-install.sh
less ollama-install.sh
sh ollama-install.sh
```

The installer creates a `systemd` service that listens on `127.0.0.1:11434` only. Keep it that way for option B. Download the model you chose:

```bash
ollama pull llama3.1:8b
ollama list
```

Ollama does not log prompts unless `OLLAMA_DEBUG` is set. Leave it unset.

## 3. The firewall

On the model server, allow SSH from your own address only, and nothing else yet:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <your admin IP> to any port 22 proto tcp
sudo ufw enable
sudo ufw status verbose
```

`<your admin IP>` is the address you connect from. If you also use your provider's network firewall (DigitalOcean's Cloud Firewalls, Hetzner's Firewalls), apply the same rules there: it filters traffic before it reaches the server, so a mistake on the server alone does not open anything.

Options A and B below each add one rule.

## 4A. Through a private tunnel

### With Tailscale

Install Tailscale on both servers (`curl -fsSL https://tailscale.com/install.sh -o ts.sh`, read it, `sh ts.sh`, then `sudo tailscale up`). Each server gets an address in `100.64.0.0/10`; `tailscale ip -4` shows it. In the Tailscale admin console, add an access rule so that only the humani-t server may reach the model server, on port 11434.

Tailscale's coordination service learns which of your machines exist and their keys, never the traffic itself. To avoid even that, run [Headscale](https://headscale.net) yourself, or use plain WireGuard.

### With WireGuard

Create a tunnel between the two servers (the [WireGuard quick start](https://www.wireguard.com/quickstart/) covers it); give the model server `10.8.0.1` and the humani-t server `10.8.0.2`, and open the WireGuard UDP port on the model server, from the humani-t server's public address only:

```bash
sudo ufw allow from <humani-t server public IP> to any port 51820 proto udp
```

### Then, for either tunnel

Make Ollama listen on the tunnel address instead of loopback:

```bash
sudo systemctl edit ollama
```

In the editor, add:

```ini
[Service]
Environment="OLLAMA_HOST=10.8.0.1:11434"
```

(use the Tailscale address instead with Tailscale), then:

```bash
sudo systemctl restart ollama
sudo ufw allow in on wg0 from 10.8.0.2 to any port 11434 proto tcp
```

(`tailscale0` and the humani-t server's Tailscale address with Tailscale.)

On the **humani-t server**, in `.env`:

```dotenv
HUMANI_T_INFERENCE_URL=http://10.8.0.1:11434
HUMANI_T_INFERENCE_MODEL=llama3.1:8b
```

Remove `local-model` from `COMPOSE_PROFILES`. Plain `http` is accepted here because the address is inside a private range and the tunnel encrypts the traffic; humani-t refuses plain `http` to any public address in production.

## 4B. Through Caddy with TLS and a bearer token

You need a DNS name for the model server, e.g. `model.example.org`, pointing at its public address.

Install Caddy from [its official package repository](https://caddyserver.com/docs/install#debian-ubuntu-raspbian). Create the token, readable by root only:

```bash
TOKEN=$(openssl rand -base64 32 | tr -d '=+/')
printf 'OLLAMA_PROXY_TOKEN=%s\n' "$TOKEN" | sudo tee /etc/caddy/proxy-token.env > /dev/null
sudo chmod 600 /etc/caddy/proxy-token.env
echo "$TOKEN"   # copy it now; it goes into humani-t's secrets in a moment
```

Give it to Caddy's service:

```bash
sudo systemctl edit caddy
```

```ini
[Service]
EnvironmentFile=/etc/caddy/proxy-token.env
```

Replace `/etc/caddy/Caddyfile` with:

```caddyfile
{
	admin off
}

model.example.org {
	header -Server

	# Only what humani-t calls, and only with the token. Model management
	# (pull, push, create, delete) stays reachable from this server's shell alone,
	# so a leaked token cannot fill the disk or remove models.
	@humani_t {
		method GET POST
		path /api/chat /api/tags /api/show /api/version
		header Authorization "Bearer {$OLLAMA_PROXY_TOKEN}"
	}

	handle @humani_t {
		request_body {
			max_size 10MiB
		}
		reverse_proxy 127.0.0.1:11434
	}

	handle {
		respond 403
	}
}
```

Then open HTTPS to the humani-t server only, and port 80 to everyone: Let's Encrypt checks it when issuing and renewing the certificate, and Caddy answers nothing else there except a redirect to HTTPS.

```bash
sudo systemctl restart caddy
sudo ufw allow from <humani-t server public IP> to any port 443 proto tcp
sudo ufw allow 80/tcp
```

On the **humani-t server**, put the token in the secret file and point humani-t at the proxy:

```bash
printf '%s' '<the token>' > /etc/humani-t/secrets/inference_token
```

```dotenv
HUMANI_T_INFERENCE_URL=https://model.example.org
HUMANI_T_INFERENCE_MODEL=llama3.1:8b
```

Remove `local-model` from `COMPOSE_PROFILES`.

## 5. Apply and test, with fictional data only

On the humani-t server:

```bash
docker compose up -d --force-recreate api worker
docker compose ps
```

Then check, in this order. Every test uses invented text; send nothing real until all of them pass.

| # | From | Do | Expect |
| --- | --- | --- | --- |
| 1 | The humani-t server | `curl -s -H "Authorization: Bearer $TOKEN" https://model.example.org/api/version` (B), or `curl -s http://10.8.0.1:11434/api/version` (A) | `{"version":"…"}` |
| 2 | The humani-t server, option B | The same without the header | `403` |
| 3 | The humani-t server, option B | `curl -s -X POST -H "Authorization: Bearer $TOKEN" https://model.example.org/api/pull -d '{"model":"x"}'` | `403`: management is not reachable |
| 4 | Your laptop, or any other machine | `curl -m 10 https://model.example.org/api/version` (B), `curl -m 10 http://<model server public IP>:11434/` | Timeout or refused, never an answer |
| 5 | Inside humani-t | `docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://10.8.0.1:11434/api/version', timeout=5).read())"` (A) | The version; proves the container can use the tunnel |
| 6 | The humani-t server | A fictional prompt, below | A short answer |

The fictional prompt for test 6 (option B; for A, drop the header and use the tunnel URL):

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://model.example.org/api/chat -d '{
  "model": "llama3.1:8b", "stream": false,
  "messages": [{"role": "user", "content": "In one sentence: what is a rapid needs assessment after a flood in the fictional town of Eastmere?"}]
}'
```

From Windows PowerShell 5.1, use `curl.exe` (not the `curl` alias), and put the JSON body in a file and pass it with `--data @body.json`, because PowerShell 5.1 strips the double quotes from inline JSON arguments.

Finally, run a review in humani-t on a made-up question and confirm that its model steps complete.

## 6. Keep it that way

- **Rotate the token** (option B) when anyone who knew it leaves, or yearly: new token in `/etc/caddy/proxy-token.env` and `systemctl restart caddy`, the same token in humani-t's `inference_token`, then `docker compose up -d --force-recreate api worker`.
- **Update** Ollama (re-run its installer), Caddy and the system regularly; `unattended-upgrades` covers Ubuntu's own packages only.
- **Re-check** test 4 after any firewall or provider change. It is the test that proves Ollama is not on the internet.
- **Watch the bill and the load.** A model server under unexpected load is a sign that something other than humani-t is using it.
