# Post-deploy SSH bootstrap — the gotchas we hit on Nucleus, recipe for Polyhive

After `gcluster deploy` finishes on a fresh a3mega slurm cluster, plain `ssh ubuntu@<controller>`
does NOT work out of the box. There are four independent things to fix, in this order. Do
the SAME steps on Polyhive after its redeploy.

Replace `<cluster>` below with `nucla3m` (Nucleus) or `phivea3m` (Polyhive).

## 1. Open external SSH on the new sysnet

The new sysnet has no firewall rule allowing port 22 inbound — the slurm blueprint doesn't
create one. Add the same `allow-ssh-external` rule the original cluster had.

```
gcloud compute firewall-rules create allow-ssh-external-<cluster> \
  --network=<cluster-net>-sys-net1 \
  --direction=INGRESS --action=ALLOW --rules=tcp:22 \
  --source-ranges=0.0.0.0/0 --project=poetic-avenue-438401-a7
```

Replace `<cluster-net>` with `nucleus-a3mega` or `polyhive-a3mega`.
(If you want to scope tighter than `0.0.0.0/0`, narrow `--source-ranges`.)

## 2. Disable OS Login on controller + login

The blueprint leaves OS Login enabled, which means plain `ssh ubuntu@…` won't work — keys
are looked up via OS Login profiles instead of `~ubuntu/.ssh/authorized_keys`. Mirror what
the original deployment notes did:

```
gcloud compute instances add-metadata <cluster>-controller --zone=us-east4-b --metadata=enable-oslogin=FALSE
gcloud compute instances add-metadata <cluster>-login-001  --zone=us-east4-b --metadata=enable-oslogin=FALSE
```

## 3. Push your public key into instance metadata

`enable-oslogin=FALSE` makes the guest-agent fall back to populating
`~ubuntu/.ssh/authorized_keys` from the `ssh-keys` metadata. One line per key, format
`<user>:<key-type> <key-data> <comment>`:

```
echo "ubuntu:$(cat ~/.ssh/<your-pubkey>.pub)" > /tmp/<cluster>-ssh-keys.txt
gcloud compute instances add-metadata <cluster>-controller --zone=us-east4-b \
  --metadata-from-file=ssh-keys=/tmp/<cluster>-ssh-keys.txt
gcloud compute instances add-metadata <cluster>-login-001 --zone=us-east4-b \
  --metadata-from-file=ssh-keys=/tmp/<cluster>-ssh-keys.txt
rm /tmp/<cluster>-ssh-keys.txt
```

If you want multiple machines/people to ssh in, list multiple `ubuntu:<key>` lines in that
file before pushing.

## 4. Seed `/home/ubuntu` on the new Filestore (the non-obvious one)

A *fresh* Filestore mount has no `/home/ubuntu` directory. The guest-agent in step 3 tries
to write `~ubuntu/.ssh/authorized_keys` and fails with `mkdir /home/ubuntu/.ssh: no such
file or directory` (visible in the serial console / journal). SSH then denies publickey
because the key never lands.

Bootstrap once via `gcloud compute ssh`, which logs you in under your **OS Login** username
(`pam_oslogin` creates a home dir for you even on the empty Filestore):

```
# Temporarily put OS Login back ON so you can get in to bootstrap
gcloud compute instances add-metadata <cluster>-controller --zone=us-east4-b --metadata=enable-oslogin=TRUE
gcloud compute instances add-metadata <cluster>-login-001  --zone=us-east4-b --metadata=enable-oslogin=TRUE

# Log in (you'll be your_email_companydomain — not ubuntu — that's expected)
gcloud compute ssh <cluster>-controller --zone=us-east4-b --project=poetic-avenue-438401-a7
# Inside the VM:
sudo mkdir -p /home/ubuntu/.ssh
sudo chown -R ubuntu:ubuntu /home/ubuntu
sudo chmod 700 /home/ubuntu/.ssh
exit

# Repeat on the login VM
gcloud compute ssh <cluster>-login-001 --zone=us-east4-b --project=poetic-avenue-438401-a7
sudo mkdir -p /home/ubuntu/.ssh
sudo chown -R ubuntu:ubuntu /home/ubuntu
sudo chmod 700 /home/ubuntu/.ssh
exit

# Turn OS Login back OFF
gcloud compute instances add-metadata <cluster>-controller --zone=us-east4-b --metadata=enable-oslogin=FALSE
gcloud compute instances add-metadata <cluster>-login-001  --zone=us-east4-b --metadata=enable-oslogin=FALSE
```

Wait ~30s for the guest-agent to retry. Now plain SSH works:
```
ssh ubuntu@<controller-public-ip> hostname     # expect: <cluster>-controller
```

(`gcloud compute ssh` won't necessarily use IAP after step 1 — the external IP + firewall
is enough. If you ever hit a `[4033: 'not authorized']` IAP error, add the IAP role to
your SA with `--condition=None`.)

## Why all this is needed

- **Step 1** — blueprint never creates a public-SSH rule; old cluster had it as an
  out-of-band addition. The redeploy starts without it.
- **Step 2 + 3** — the blueprint runs with OS Login ON (the modern default). Original
  Polyhive deploy notes explicitly flipped OS Login off on login/controller and pushed
  keys via metadata for `User ubuntu` to work; we're replicating that exact posture.
- **Step 4** — `/home` is a brand-new Filestore (no random_suffix collision with the
  orphaned old one). It comes up empty: no `/home/ubuntu`, no `/home/<anyone>`. With OS
  Login off, the guest-agent can't `mkdir /home/ubuntu/.ssh` (parent missing) and fails
  silently. `pam_oslogin` *does* mkhomedir on first login, so we bootstrap by logging in
  with OS Login once, manually `mkdir /home/ubuntu`, then flip OS Login back off.

## Optional follow-ups (not needed for SSH to work, but you'll probably want them)

- Set the project's `compute.osLogin` IAM and the SA's `iap.tunnelResourceAccessor` if you
  want `gcloud compute ssh --tunnel-through-iap` to work later.
- Re-attach the preserved `*-controller-save` disk (see PROCEDURE.md step 2b) if you want
  slurm state (jobs, accounting) to persist across controller reboots — without it,
  `/var/spool/slurm` lives on the auto-delete boot disk.
- Re-attach `polyhive-env-disk` on Polyhive's controller — your call where to mount it.
