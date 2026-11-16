# Deploying humani-t on Oracle Cloud (OCI)

This guide runs the whole stack, the language model included, on one Always Free Arm virtual machine at Oracle Cloud Infrastructure (OCI). It follows [the self-hosting guide](self-hosting.md) and links to it for everything that is not specific to Oracle: read that guide too, especially key custody and backups.

> **Status: untested on OCI.** This guide was written from Oracle's and Docker's documentation, and has not yet been run end to end on a real instance. Console labels may differ from what is written here. What was checked: every pinned image in the stack is published for `linux/arm64`, and CI builds both of this repository's images and starts the whole stack on an Arm runner (the `compose smoke test (arm64)` check). Steps or figures that are estimates say so.

> humani-t has not yet had an independent security review. Until it has, use fictional or already-public data only.

Commands in `bash` blocks run on the VM, over SSH. Commands in `powershell` blocks run on your own Windows machine (PowerShell 5.1).

## Trial setup at a glance

> **For a first trial with no domain, no mail server and no paid account.** Fictional or public data only.
>
> | Choice | This trial | Where |
> | --- | --- | --- |
> | Account | Always Free only, no Pay As You Go | [Section 1](#1-what-you-get-and-what-it-costs) |
> | Instance | Arm `VM.Standard.A1.Flex`, 2 OCPUs, 12 GB, 100 GB disk | [Section 2](#2-create-the-instance-and-its-network) |
> | Host name | `<your IP with dashes>.sslip.io`, for example `203-0-113-10.sslip.io` | [Section 7.2](#72-a-host-name-without-a-domain) |
> | Mail | The bundled Mailpit; you read it through an SSH tunnel and forward invite links by hand | [Section 7.3](#73-mail-without-a-mail-server) |
> | Model | `qwen3:4b` in the bundled Ollama | [Section 8](#8-choose-the-model) |
> | Malware scanning | On, with the bundled ClamAV | [Section 9](#9-malware-scanning-on-12-gb) |
>
> The lines to change in `.env`, with your own IP in place of `203-0-113-10`:
>
> ```dotenv
> HUMANI_T_DOMAIN=203-0-113-10.sslip.io
> HUMANI_T_PUBLIC_ORIGIN=https://203-0-113-10.sslip.io
> HUMANI_T_SECRETS_DIR=/etc/humani-t/secrets
>
> COMPOSE_PROFILES=local-model,malware-scan,local-mail
>
> HUMANI_T_SMTP_HOST=mailpit
> HUMANI_T_SMTP_PORT=1025
> HUMANI_T_SMTP_USERNAME=
> HUMANI_T_SMTP_STARTTLS=false
>
> HUMANI_T_INFERENCE_MODEL=qwen3:4b
> HUMANI_T_OLLAMA_CPUS=2
> HUMANI_T_INFERENCE_TIMEOUT_SECONDS=600
> HUMANI_T_SCREENING_CONCURRENCY=1
>
> HUMANI_T_MALWARE_SCANNER=clamd
> HUMANI_T_CLAMD_ADDRESS=tcp://clamav:3310
> ```
>
> Then choose the paper sources ([section 7.4](#74-configuration)). Steps: sections 2 to 10, in order.

## 1. What you get, and what it costs

Oracle's [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm) include, per tenancy:

| Resource | Always Free amount |
| --- | --- |
| Arm compute (`VM.Standard.A1.Flex`, Ampere) | 1,500 OCPU hours and 9,000 GB hours a month: **2 OCPUs and 12 GB of memory** running all month. One OCPU is one core. |
| Block storage | 200 GB in total, boot volumes included, plus 5 volume backups |
| Object Storage | 20 GB |
| Outbound data | 10 TB a month |
| Email Delivery | 3,000 emails a month (not used in this trial; see [section 7.3](#73-mail-without-a-mail-server)) |

Older guides and blog posts say 4 OCPUs and 24 GB; Oracle's page now says 2 and 12. Size the instance at exactly 2 OCPUs and 12 GB, never more. Oracle's [Free Tier page](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm) says that if a tenancy has more Arm capacity than that when the 30-day trial ends, **all** its Arm instances are disabled, then deleted after 30 days.

This guide uses Always Free only. Things to know before you sign up:

- **Always Free compute exists only in your home region**, which you choose at sign-up and cannot change. For an EU deployment, choose an EU region, for example Frankfurt (`eu-frankfurt-1`), Amsterdam (`eu-amsterdam-1`), Paris (`eu-paris-1`) or Stockholm (`eu-stockholm-1`).
- **Free Arm capacity is often short.** Creating the instance may fail with "Out of host capacity" for hours or days. [Section 2](#2-create-the-instance-and-its-network) says what to do.
- **Idle instances may be reclaimed.** Oracle [may reclaim](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) an Always Free instance when, over 7 days, its 95th-percentile CPU use, its network use and (on Arm) its memory use are **all** below 20%. See below.

**Idle reclamation, honestly.** A humani-t server that nobody uses for a week has low CPU and network use. Memory is what probably keeps it above the line: ClamAV, the API with its name detector, Postgres and the OS hold roughly 4 to 5 GB of the 12 GB even when idle, which is above 20%. That is an estimate, not a promise, and Oracle does not say exactly how it measures. After the first quiet week, open the instance's **Metrics** page in the console and look at memory utilisation. Do not run fake load to stay above the line: that works against Oracle's policy, and it is not needed for a trial.

**If Oracle stops the instance:** start it again from the console (**Compute, Instances**, the instance, **Start**). Its public IP stays the same through a stop ([Oracle: public IPs](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm)), so the host name in [section 7.2](#72-a-host-name-without-a-domain) keeps working, and Docker starts the stack again on its own. Check with `docker compose ps`. User reports say Oracle emails a warning first, and that a stopped instance can be started only while Arm capacity is free; neither is in Oracle's documentation (unverified). Keep the backups in [section 11](#11-backups) running, so losing the instance loses at most a day.

**Later, if the trial goes well:** upgrading to Pay As You Go needs a credit card, keeps Always Free resources free, and gives more ways round "Out of host capacity". If you do, create a budget alert at a small amount (Billing and Cost Management, Budgets).

Your data then sits with Oracle, in the region you chose. Check your organisation's data-protection obligations before anything real goes in.

## 2. Create the instance and its network

First, on your Windows machine, create an SSH key for the server:

```powershell
New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null
ssh-keygen -t ed25519 -f "$HOME\.ssh\oci_humani_t"
Get-Content "$HOME\.ssh\oci_humani_t.pub"
```

Give the key a passphrase when asked. The second command prints the public key; you paste it in a moment.

In the OCI console, open **Compute, Instances, Create instance**, and set:

| Field | Value |
| --- | --- |
| Name | `humani-t` |
| Placement | Any availability domain; see below if it fails |
| Image | **Canonical Ubuntu 24.04**. With an Arm shape, the console picks the `aarch64` build. |
| Shape | **Ampere, `VM.Standard.A1.Flex`**, 2 OCPUs, 12 GB memory |
| Networking | Create a new virtual cloud network and a new **public** subnet, and assign a public IPv4 address. The wizard creates the VCN, an internet gateway and a default security list that allows SSH. |
| SSH keys | Paste the public key |
| Boot volume | Specify a custom size: **100 GB** (the default is about 50 GB; the free 200 GB covers both) |

Create it, and note the **public IP address** on the instance's page. The host name is built from it ([section 7.2](#72-a-host-name-without-a-domain)). An ephemeral public IP stays with the instance through stops and restarts, until the instance is terminated; if you ever recreate the instance, the IP and so the host name change.

**"Out of host capacity".** Oracle has no free Arm capacity in that availability domain at the moment. [Oracle's own advice](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) for a free account is to try another availability domain, or wait and try again. In practice:

- Try each availability domain in the **Placement** field. Frankfurt has three; most other EU regions have one.
- Try at different times of day, and on different days: capacity comes back when other people delete instances.
- Keep the other fields as they are, so each retry is quick. A failed attempt costs nothing.
- Keep the shape at 2 OCPUs and 12 GB. A smaller instance may fit sooner, but it is too small for the model and ClamAV together.

The only other route Oracle names is upgrading to Pay As You Go, a later option ([section 1](#1-what-you-get-and-what-it-costs)).

Connect from Windows:

```powershell
ssh -i "$HOME\.ssh\oci_humani_t" ubuntu@<public IP>
```

## 3. Open ports 80 and 443, in two places

Caddy needs port 80 and 443 reachable from the internet, to obtain its certificate and to serve the app. On OCI, two firewalls stand in the way, and both must be opened.

### 3.1 The VCN security list

In the console, open the instance's **subnet**, then its **Default Security List**, and **Add Ingress Rules**, one per line:

| Stateless | Source CIDR | IP protocol | Destination port |
| --- | --- | --- | --- |
| No | `0.0.0.0/0` | TCP | `80` |
| No | `0.0.0.0/0` | TCP | `443` |
| No | `0.0.0.0/0` | UDP | `443` (HTTP/3; optional) |

Leave **Stateless** unchecked, so replies are allowed back out without an egress rule of their own.

### 3.2 The instance's own iptables rules

Oracle's Ubuntu images do not use `ufw`. They ship iptables rules, in `/etc/iptables/rules.v4`, that reject everything except SSH. A rule added after the final `REJECT` never matches, so look at the order first:

```bash
sudo iptables -L INPUT -n --line-numbers
```

The last line is `REJECT ... reject-with icmp-host-prohibited`. Insert the new rules at its number; with Oracle's default rules that is 6:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT 6 -p udp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

`netfilter-persistent save` writes them to `rules.v4`, so they survive a reboot. Do this **before** installing Docker: saving while Docker runs would also write Docker's own rules into the file, and they would come back stale after every reboot.

## 4. SSH hardening, briefly

Oracle's Ubuntu image already accepts SSH keys only, and no root login. Check, and turn on automatic security updates if they are off:

```bash
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'
systemctl is-enabled unattended-upgrades
```

Expect `passwordauthentication no`, `permitrootlogin` not `yes`, and `enabled`. If yours differ, fix them before going on.

If you connect from a fixed address, narrow the security list's SSH rule (port 22) from `0.0.0.0/0` to that address with `/32`. If your address changes often, leave it, and rely on the key.

## 5. Install Docker

From [Docker's own apt repository](https://docs.docker.com/engine/install/ubuntu/), which publishes `arm64` packages. Ubuntu's `docker.io` package is older, and the stack needs Engine 25 and Compose 2.24 or newer.

```bash
sudo apt update && sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Let your user run Docker without `sudo`, then log out and back in. Membership of the `docker` group is equivalent to root, so add only the operator:

```bash
sudo usermod -aG docker "$USER"
exit
```

After reconnecting:

```bash
docker version --format '{{.Server.Arch}} {{.Server.Version}}'
docker compose version
```

Expect `arm64` and a version of 25 or newer, and Compose v2.24 or newer.

## 6. Get the code

While the repository is private, the server reads it with a **deploy key**: an SSH key that can read this one repository and nothing else.

```bash
ssh-keygen -t ed25519 -N "" -C "humani-t OCI" -f ~/.ssh/humani_t_deploy
cat ~/.ssh/humani_t_deploy.pub
```

On GitHub, open the repository's **Settings, Deploy keys, Add deploy key**, paste the key, and leave **Allow write access** unchecked. Then:

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/humani_t_deploy" git clone git@github.com:fiifidawson/humani-t.git ~/humani-t
cd ~/humani-t
git config core.sshCommand "ssh -i ~/.ssh/humani_t_deploy"
```

The first connection asks you to confirm GitHub's host key; compare the fingerprint with [GitHub's published ones](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints) before typing `yes`. Once the repository is public, the plain `git clone` in [self-hosting §3.1](self-hosting.md#31-get-the-code) works instead.

## 7. Secrets, host name, mail and configuration

### 7.1 Secrets

Create them exactly as in [self-hosting §3.2](self-hosting.md#32-create-the-secrets), in `/etc/humani-t/secrets`. Then copy the key-encryption key into your password manager now, before anything else: losing it loses every project ([self-hosting §4](self-hosting.md#4-key-custody)).

```bash
cat /etc/humani-t/secrets/key_encryption_keys
```

### 7.2 A host name without a domain

Caddy needs a host name to get a certificate from Let's Encrypt, and Oracle gives the instance a public IP but no public name. Three ways to get one:

| Option | What it is | Account | Good for |
| --- | --- | --- | --- |
| **sslip.io** (or nip.io) | A public DNS service that answers any name with an IP in it: `203-0-113-10.sslip.io` resolves to `203.0.113.10` | None | **This trial** |
| DuckDNS | A free subdomain, such as `yourname.duckdns.org`, that you point at your IP on their site | Yes: sign in with Google, GitHub, X or Reddit | A trial where the IP may change |
| Your own domain | A name you rent from a registrar, often around 10 to 15 euros a year (estimate; check the renewal price) | Yes, and payment | **The long-term answer**; it also makes real mail possible ([section 7.3](#73-mail-without-a-mail-server)) |

**Recommended for the trial: sslip.io.** There is nothing to sign up for and nothing to configure: the name works as soon as the instance has its IP, and stays valid as long as the IP does. Write the IP with dashes instead of dots, and add `.sslip.io`. For `203.0.113.10`:

```text
203-0-113-10.sslip.io
```

Check it from Windows before starting the stack; it must print your instance's IP:

```powershell
Resolve-DnsName 203-0-113-10.sslip.io -Type A
```

What to know about it:

- **Who runs it.** sslip.io and nip.io are one free service, run by two volunteers, Brian Cunnie and Kenneth Lakin ([nip.io](https://nip.io/)). Its nameservers are in Singapore, the USA and Poland. There is no service-level promise.
- **What it sees.** When anyone's browser looks up your host name, the lookup goes to those nameservers. They learn the name, which contains your server's IP, and the address of the visitor's DNS resolver (usually their internet provider), and when. They never see your pages or data: those go straight from the browser to your server, encrypted. A name you control has the same exposure, only to the DNS provider you chose.
- **Your IP becomes public.** Every Let's Encrypt certificate is published in public logs, so the name, and with it the IP, is visible to anyone. This is true of any public server; expect automated scans in your logs.
- **Rate limits.** Let's Encrypt counts certificates per "registered domain". sslip.io and nip.io are not on the [Public Suffix List](https://publicsuffix.org/list/public_suffix_list.dat) (checked 2026-11-12), so every user of sslip.io shares one limit, which Let's Encrypt has raised for them from the usual 50 to 250,000 certificates ([nip.io](https://nip.io/)). If it is ever reached, use the same name under `nip.io` instead (`203-0-113-10.nip.io`), which counts separately. The [per-name limits](https://letsencrypt.org/docs/rate-limits/) still apply: 5 failed validations an hour, and 5 identical certificates a week. So open the ports first ([section 3](#3-open-ports-80-and-443-in-two-places)), and check the name resolves, before the first start. DuckDNS *is* on the Public Suffix List, so each DuckDNS name has its own limit.

The app works with such a name as it does with any other (checked against the code, untested on OCI):

- **Origin check.** The API compares the browser's `Origin` header with `HUMANI_T_PUBLIC_ORIGIN` exactly. Set it to `https://` plus the same name, with no port and no trailing path.
- **Cookies.** The session cookies are `__Host-` cookies: they belong to that exact name and set no `Domain`, so nothing about sslip.io's other users touches them.
- **HSTS.** Browsers will insist on HTTPS for that name, and names under it, for two years. That covers your name only, not the rest of sslip.io.
- **Passkeys** are bound to the host name. If you later move to your own domain, passkeys made for the old name stop working: users sign in with their password plus an authenticator app or a recovery code, and add a new passkey. Say so before the move.

If the IP ever changes, the name changes with it: update both `.env` lines in [section 7.4](#74-configuration), restart, and tell users the new address.

### 7.3 Mail without a mail server

humani-t sends mail for invitations, password resets and security notices. Production refuses to start without an SMTP server.

**Oracle's Email Delivery does not help yet.** It is part of Always Free, with [3,000 emails a month](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), and [200 a day and 10 a minute](https://docs.oracle.com/en-us/iaas/Content/Email/Concepts/overview.htm) on a free trial account. But the sender address must be on [a domain you own and control](https://docs.oracle.com/en-us/iaas/Content/Email/Tasks/managingapprovedsenders.htm): not Gmail, Hotmail or another mailbox provider. To be delivered, that domain needs SPF (a `TXT` record) and DKIM (a [`CNAME` record](https://docs.oracle.com/en-us/iaas/Content/Email/Tasks/configure-dkim-using-the-console.htm)) in its DNS. You cannot add records under sslip.io, and DuckDNS offers no `CNAME` records. Without them, receiving mail servers are likely to reject the mail or file it as spam. Use it once you have your own domain.

**For the trial: the bundled Mailpit, read through an SSH tunnel.** Mailpit is a small mail catcher in the stack (the `local-mail` profile). It accepts every email and keeps it in its own inbox; nothing leaves the server. You read the inbox yourself and pass links on by hand.

This works in production mode. The API accepts `mailpit` as the SMTP server without TLS (checked against the settings code; untested on OCI): the mail travels only between two containers on the same VM. The `.env` lines are in the [box at the top](#trial-setup-at-a-glance): `local-mail` in `COMPOSE_PROFILES`, then `HUMANI_T_SMTP_HOST=mailpit`, `HUMANI_T_SMTP_PORT=1025`, `HUMANI_T_SMTP_USERNAME=` and `HUMANI_T_SMTP_STARTTLS=false`. Leave `smtp_password` empty.

Mailpit's inbox is published on the VM's own loopback address, port 8025, and nowhere else. To read it, open a tunnel from Windows and leave that window open:

```powershell
ssh -i "$HOME\.ssh\oci_humani_t" -N -L 8025:127.0.0.1:8025 ubuntu@<public IP>
```

Then open http://127.0.0.1:8025 in your browser. If port 8025 is already in use on your machine, use `-L 18025:127.0.0.1:8025` and open http://127.0.0.1:18025.

What you must do by hand:

- **Invitations.** After inviting someone, open Mailpit, copy the link from the invitation email, and send it to that person yourself, privately (for example to their work email or a direct message). The link is valid for 7 days, and anyone who has it can create the account, so do not post it in a shared channel.
- **Password resets.** A reset link expires after 30 minutes, so forward it from Mailpit as soon as a reset is requested.
- **Security notices.** Emails such as "a sign-in method was added" or "a recovery code was used" reach only Mailpit. Look through the inbox now and then.

Mailpit keeps mail in memory: restarting the stack empties the inbox. It is a trial tool, as [self-hosting §1](self-hosting.md#1-what-runs) says: replace it before real use.

**Later: a real mail server.** Your organisation's SMTP server, or a transactional mail provider, on port 587 with STARTTLS: set `HUMANI_T_SMTP_HOST`, `_PORT`, `_USERNAME`, `_SENDER` and `HUMANI_T_SMTP_STARTTLS=true`, put the password in `/etc/humani-t/secrets/smtp_password` ([self-hosting §3.2](self-hosting.md#32-create-the-secrets)), and remove `local-mail` from `COMPOSE_PROFILES`. With your own domain, Oracle's Email Delivery is one such provider: the console shows its SMTP endpoint and port under **Email Delivery** (untested). Mail then leaves the deployment through that provider, so choose one you are content to trust with invitation and reset links.

### 7.4 Configuration

`cp .env.example .env`, then edit it (`nano .env`) as in [self-hosting §3.3](self-hosting.md#33-configure). Set the lines in the [box at the top](#trial-setup-at-a-glance), with your own host name.

- `HUMANI_T_OLLAMA_CPUS=2` is required: the default is 4, and Docker refuses to start a container limited to more CPUs than the machine has.
- Two cores run one model call well, not two: screen one paper at a time, and give each call up to ten minutes.
- The model and ClamAV are explained in sections [8](#8-choose-the-model) and [9](#9-malware-scanning-on-12-gb). Mail is in [section 7.3](#73-mail-without-a-mail-server).
- `HUMANI_T_PAPER_SOURCES` is empty by default, and then a run searches nothing. Choose sources, for example `openalex,europepmc,arxiv`: each then receives the masked search query. Set `HUMANI_T_OPENALEX_MAILTO` if you use OpenAlex.

## 8. Choose the model

The model runs in Ollama on the same VM (the `local-model` profile), so no text leaves the instance for a model. That is the default, and the one this guide sets up.

With 2 Arm cores and no GPU, the model is the slow part of every run. Memory on 12 GB, roughly:

| | Memory |
| --- | --- |
| Ubuntu and Docker | about 1 GB |
| Caddy, API, worker, Postgres, Valkey, and the name detector used for masking | about 2.5 GB |
| ClamAV ([section 9](#9-malware-scanning-on-12-gb)) | about 1.5 GB |
| Left for the model | about 6.5 GB |

| Model | Download | Fits | Speed on 2 cores (estimate, untested) |
| --- | --- | --- | --- |
| `qwen3:4b` (recommended here) | about 2.5 GB | Comfortably | A few words a second; about 1 to 2 minutes per screened paper |
| `llama3.1:8b` (the project's default elsewhere) | about 4.9 GB | Yes, with about 1 GB to spare | Roughly half that: 2 to 4 minutes per paper |

Be prepared for slow runs: screening 40 papers takes on the order of an hour with `qwen3:4b`, and planning and the brief add more. The settings in [the box at the top](#trial-setup-at-a-glance) give each call up to ten minutes and screen one paper at a time. Start with `qwen3:4b`, and measure.

After the stack is running ([section 10](#10-start-it-create-the-admin-and-check-it)), download the model and measure its speed:

```bash
docker compose exec ollama ollama pull qwen3:4b
docker compose exec ollama ollama run qwen3:4b --think=false --verbose "In one sentence, what is a rapid needs assessment?"
```

`eval rate` in the output is the speed in tokens a second (a token is about three quarters of a word).

**Faster options, and what they cost in privacy:**

- A model server on a bigger machine that you control, reached over a private tunnel: see [Running the model on another machine](remote-model.md).
- A hosted model API (OpenAI, Mistral, Azure OpenAI and others) is possible only as the operator's explicit choice, never by default: masked text then leaves your deployment for that provider. It takes an edit to `compose.yaml` on purpose. Read [remote-model §7](remote-model.md#7-a-hosted-model-api-an-opt-in) and [ADR 0010](../adr/0010-openai-compatible-models-as-an-opt-in.md) before choosing it.

## 9. Malware scanning on 12 GB

Turn it on. Production refuses uploads until a scanner is chosen, and the `malware-scan` profile ([self-hosting §3.7](self-hosting.md#37-scan-uploads-for-malware)) needs about 1.5 GB of memory and 1 GB of disk, which 12 GB has room for alongside `qwen3:4b`. The ClamAV image is published for `arm64`. The `.env` lines are in [the box at the top](#trial-setup-at-a-glance).

freshclam fetches signatures from ClamAV's CDN every two hours; the requests name only database versions, never a document.

If you run `llama3.1:8b` and memory gets tight, `HUMANI_T_MALWARE_SCANNER=none` is the alternative; make it a written decision, as self-hosting §3.7 explains.

## 10. Start it, create the admin, and check it

```bash
cd ~/humani-t
docker compose up -d --build
docker compose ps
```

The images are built on the VM, natively for `arm64`. On 2 cores the first build takes a while; allow half an hour (estimate). Expect `caddy`, `api`, `worker`, `postgres`, `valkey`, `ollama`, `mailpit` and `freshclam` to become `healthy`, `migrate` and `ollama-volume` to show `exited (0)`, and `clamav` to turn `healthy` within a few minutes, once its signatures are loaded.

Then download the model ([section 8](#8-choose-the-model)), and create the first administrator as in [self-hosting §3.5](self-hosting.md#35-create-the-first-administrator):

```bash
read -rs PASSWORD && printf '%s\n' "$PASSWORD" \
  | docker compose exec -T api python -m app.cli create-admin --email you@example.org --password-stdin; \
  unset PASSWORD
```

Check from Windows, which proves both firewalls are open:

```powershell
curl.exe -sI https://203-0-113-10.sslip.io/
curl.exe -s https://203-0-113-10.sslip.io/api/v1/ready
```

Expect a `200` status line with a `strict-transport-security` header, then `{"status":"ready"}`. Sign in at `https://203-0-113-10.sslip.io/` (your own host name), set up a passkey or an authenticator app, and run a review on a made-up question to see the model steps complete. To check mail, invite a second address of your own and find the invitation in Mailpit ([section 7.3](#73-mail-without-a-mail-server)).

## 11. Backups

What to back up, and why the KEK is kept apart from everything else, is in [self-hosting §5](self-hosting.md#5-backups-and-restore). On OCI there are two ways to keep the copies off the VM.

**Recommended: encrypted dumps in your own Object Storage bucket.** The database dump and the documents archive leave the VM for a bucket in your own tenancy. That is a destination you choose; it is with the same provider that already runs the VM, and the files are encrypted before they leave.

1. In the console, open **Storage, Object Storage, Buckets**, and create a private bucket, for example `humani-t-backups`, in your home region.
2. On the bucket, create a **pre-authenticated request**: target **Bucket**, access **Permit object writes**, and an expiry date a year away. Copy the URL at once; it is shown only once. With it, the server can add files to the bucket, but cannot list or read them, so whoever takes over the server cannot read your older backups either. Note the expiry date: after it, uploads fail, so create a new one before then.
3. On the VM, install `age` and make a key pair for the backups. The private key goes to your password manager, kept apart from the KEK, and then off the server:

   ```bash
   sudo apt install -y age
   age-keygen -o ~/backup-key.txt
   grep -o 'age1.*' ~/backup-key.txt > /etc/humani-t/backup-recipient
   cat ~/backup-key.txt     # copy it into your password manager, then:
   shred -u ~/backup-key.txt
   # Paste the pre-authenticated request URL; it is not echoed or kept in the history.
   read -rs URL && printf '%s' "$URL" > /etc/humani-t/backup-url; unset URL
   chmod 600 /etc/humani-t/backup-url
   ```

4. Save this as `/etc/humani-t/backup.sh`, and make it executable with `chmod 700 /etc/humani-t/backup.sh`:

   ```bash
   #!/bin/bash
   # Dump, encrypt, upload. Files only exist encrypted on disk, and only until uploaded.
   set -euo pipefail
   cd ~/humani-t
   stamp=$(date +%Y%m%d-%H%M%S)
   recipient=$(cat /etc/humani-t/backup-recipient)
   url=$(cat /etc/humani-t/backup-url)
   work=$(mktemp -d)
   trap 'rm -rf "$work"' EXIT

   docker compose exec -T postgres pg_dump -U postgres --format=custom humani_t \
     | age -r "$recipient" > "$work/humani_t-$stamp.dump.age"
   docker compose exec -T api tar -C /var/lib/humani-t -cf - documents \
     | age -r "$recipient" > "$work/documents-$stamp.tar.age"

   for file in "$work"/*.age; do
     curl -fsS -T "$file" "$url$(basename "$file")"
   done
   ```

5. Run it once by hand (`/etc/humani-t/backup.sh`) and check the two files appear in the bucket. Then schedule it nightly with `crontab -e`:

   ```text
   30 2 * * * /etc/humani-t/backup.sh >> /var/tmp/humani-t-backup.log 2>&1
   ```

6. Set the retention window: on the bucket, add a **lifecycle policy rule** that deletes objects older than your chosen number of days ([self-hosting §5](self-hosting.md#taking-a-backup) explains why that number matters). Oracle may ask for an IAM policy that lets Object Storage manage the bucket's objects; the console shows the policy it needs (untested).

Keep an eye on the free 20 GB. To restore, download both files from the console, decrypt them with `age -d -i <your backup key file>`, and follow [self-hosting §5, Restoring](self-hosting.md#restoring). Restore onto a scratch machine regularly.

**Boot volume backups: not for this.** OCI can back up the whole boot volume on a schedule, and 5 backups are free. A boot volume backup holds the database **and** `/etc/humani-t/secrets`, the KEK included, in one copy: exactly the combination [self-hosting §4](self-hosting.md#4-key-custody) says never to keep. If you use one to rebuild a broken server quickly, treat that backup as carefully as the KEK itself, and delete old ones on the same schedule.

## 12. Upgrades

humani-t: as in [self-hosting §7](self-hosting.md#7-upgrades-and-migrations), from `~/humani-t`. Back up first.

Ubuntu: security updates install themselves (`unattended-upgrades`). Some need a reboot; check and reboot when convenient:

```bash
[ -f /var/run/reboot-required ] && echo "reboot needed"
sudo reboot
```

After a reboot, the iptables rules come back from `rules.v4`, Docker starts the stack again on its own, and `docker compose ps` should show it healthy within a few minutes.

## 13. Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| "Out of host capacity" when creating the instance | No free Arm capacity in that availability domain right now | Another availability domain, or try again later, on another day if need be ([section 2](#2-create-the-instance-and-its-network)) |
| The site times out from outside, but `curl -sI -H "Host: <your domain>" http://localhost` on the VM answers | Port closed in the security list | [Section 3.1](#31-the-vcn-security-list) |
| `No route to host` from outside | The iptables rule sits after the `REJECT` | `sudo iptables -L INPUT -n --line-numbers`, then [section 3.2](#32-the-instances-own-iptables-rules) |
| Browser certificate warning; `docker compose logs caddy` shows a failed challenge | The host name does not resolve to the instance (a typo in the IP in the sslip.io name), or a port is closed | Check with `Resolve-DnsName` ([section 7.2](#72-a-host-name-without-a-domain)), fix the name or the ports, then `docker compose restart caddy`. After 5 failures in an hour, Let's Encrypt makes you wait |
| `docker compose logs caddy` mentions `rateLimited` for sslip.io as a whole | The shared sslip.io limit was reached | Use the same name under `nip.io` in both `.env` lines ([section 7.2](#72-a-host-name-without-a-domain)), then `docker compose up -d` |
| No invitation or reset email arrives | Mail goes to Mailpit, never to the user | Open Mailpit through the SSH tunnel and forward the link ([section 7.3](#73-mail-without-a-mail-server)) |
| `403 Cross-site requests are not allowed` on every sign-in | `HUMANI_T_PUBLIC_ORIGIN` differs from the address in the browser | Make it exactly `https://` plus the host name in `HUMANI_T_DOMAIN`, then `docker compose up -d` |
| `range of CPUs is from 0.01 to 2.00` | `HUMANI_T_OLLAMA_CPUS` above the instance's cores | `HUMANI_T_OLLAMA_CPUS=2` in `.env`. Do not size the instance below 2 OCPUs: other services are limited to 2 CPUs too |
| The site stopped answering after an OS update or a firewall change, with the containers still running | `netfilter-persistent` reloaded `rules.v4` and removed Docker's rules | `sudo systemctl restart docker` |
| The build stops with `exit code: 137` or `Killed` | Out of memory while building | Stop Ollama during the build (`docker compose stop ollama`), or add a swap file |
| Runs stop on a model timeout | The model is too slow for two cores | Raise `HUMANI_T_INFERENCE_TIMEOUT_SECONDS`, or use a smaller model ([section 8](#8-choose-the-model)) |
| `clamav` restarts, or the kernel log shows `oom-kill` | Memory is short: the model and ClamAV together | A smaller model, or `HUMANI_T_MALWARE_SCANNER=none` ([section 9](#9-malware-scanning-on-12-gb)) |
| The instance was stopped by Oracle | Idle reclamation, or a shape above the free 2 OCPUs and 12 GB | Start it from the console, then `docker compose ps`; the IP and host name stay the same. See [section 1](#1-what-you-get-and-what-it-costs). Check the shape |
| SSH times out | Your address changed and the SSH rule allows only the old one | Update the security list's port 22 rule in the console |

Everything else, such as missing secrets, an origin mismatch or the scanner, is in [self-hosting §9](self-hosting.md#9-troubleshooting).
