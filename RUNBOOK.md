# Chatbot-Simple — Operations Runbook

**Audience:** Ops / SRE  
**Cluster:** Docker Desktop local Kubernetes  
**Helm release:** `chatbot` in namespace `default`  
**App URL:** http://localhost:8501

---

## Quick Reference

| Action | Command |
|---|---|
| Check all pods | `kubectl get pods` |
| App health | `curl http://localhost:8501/_stcore/health` |
| App logs | `kubectl logs deployment/chatbot-chatbot` |
| Postgres logs | `kubectl logs deployment/chatbot-postgres` |
| Phoenix logs | `kubectl logs deployment/chatbot-phoenix` |
| Upgrade release | `helm.exe upgrade chatbot helm/chatbot` |
| Full redeploy | `helm.exe uninstall chatbot && helm.exe install chatbot helm/chatbot` |
| ArgoCD sync status | `argocd.exe app get chatbot` |

---

## Components

| Component | Deployment | Port | Toggle |
|---|---|---|---|
| Streamlit app | `chatbot-chatbot` | 8501 (LoadBalancer) | always on |
| PostgreSQL | `chatbot-postgres` | 5432 (ClusterIP) | `postgres.enabled` in values.yaml |
| Arize Phoenix | `chatbot-phoenix` | 6006 (ClusterIP) | `phoenix.enabled` in values.yaml |
| Ollama (LLM) | Windows host process | 11434 | runs outside cluster |

> **Critical:** PostgreSQL uses an `emptyDir` volume — **data does not survive pod restarts**. After any postgres pod restart, tables must be recreated (Upload page → "Create Tables in DB") and data re-uploaded.

---

## Verify the System is Healthy

```powershell
# 1. All pods Running, no CrashLoopBackOff
kubectl get pods

# Expected:
# chatbot-chatbot-xxx    1/1  Running
# chatbot-postgres-xxx   1/1  Running
# chatbot-phoenix-xxx    1/1  Running  (if phoenix.enabled)

# 2. App responds
curl http://localhost:8501/_stcore/health
# Expected: {"status":"ok"}

# 3. Ollama reachable from pod
kubectl exec deployment/chatbot-chatbot -- curl -s http://host.docker.internal:11434/api/tags
# Expected: JSON list of models

# 4. Postgres reachable
kubectl exec deployment/chatbot-postgres -- pg_isready -U chatbot
# Expected: /var/run/postgresql:5432 - accepting connections
```

---

## Deploy

### Fresh install
```powershell
# Lint first
helm.exe lint helm/chatbot

# Dry-run to preview manifests
helm.exe install chatbot helm/chatbot --dry-run --debug

# Deploy
helm.exe install chatbot helm/chatbot

# Wait for pods
kubectl rollout status deployment/chatbot-chatbot
kubectl rollout status deployment/chatbot-postgres

# Verify
kubectl get pods
kubectl get svc   # EXTERNAL-IP for chatbot service should be localhost
```

After first deploy, visit the Upload page and click **"Create Tables in DB"** before uploading any data.

### Upgrade (new image or config change)
```powershell
helm.exe upgrade chatbot helm/chatbot

# Watch rollout
kubectl rollout status deployment/chatbot-chatbot
```

CI auto-upgrades the image tag in `helm/chatbot/values.yaml` after every successful push to `main`. If ArgoCD is running, it will pick up that commit and sync automatically — no manual upgrade needed.

---

## ArgoCD (GitOps path)

ArgoCD is configured with `automated.selfHeal: true` and `automated.prune: true`. Any push to `main` that changes `helm/chatbot/` will trigger an automatic sync within ~3 minutes.

```powershell
# Port-forward ArgoCD UI (keep terminal open)
kubectl port-forward svc/argocd-server -n argocd 8080:443
# UI at https://localhost:8080

# Check sync and health status
argocd.exe login localhost:8080 --username admin --insecure
argocd.exe app get chatbot

# Force an immediate sync (don't wait for poll)
argocd.exe app sync chatbot

# See what ArgoCD would change vs cluster state
argocd.exe app diff chatbot
```

---

## Rollback

### Option A — Helm rollback (fastest)
```powershell
# List previous revisions
helm.exe history chatbot

# Roll back to the previous revision
helm.exe rollback chatbot

# Roll back to a specific revision number
helm.exe rollback chatbot <REVISION>

# Watch rollout
kubectl rollout status deployment/chatbot-chatbot
```

### Option B — Pin a specific image tag
Edit `helm/chatbot/values.yaml`, set `image.tag` to a known-good SHA, then:
```powershell
helm.exe upgrade chatbot helm/chatbot
```

If using ArgoCD, commit the values.yaml change to `main` — ArgoCD will sync it automatically.

### Option C — kubectl rollout undo (last resort)
```powershell
kubectl rollout undo deployment/chatbot-chatbot
```
> Warning: this bypasses Helm state. Follow up with a `helm.exe upgrade` to re-sync Helm's view.

---

## Change Model or Ollama Host

Edit `helm/chatbot/values.yaml`:
```yaml
ollama:
  host: http://host.docker.internal:11434
  model: qwen3:1.7b   # change this
```

Then upgrade:
```powershell
helm.exe upgrade chatbot helm/chatbot
```

The new model must already be pulled on the host:
```powershell
ollama pull <model-name>
```

---

## Enable / Disable Phoenix

```yaml
# helm/chatbot/values.yaml
phoenix:
  enabled: false   # set true to re-enable
```

```powershell
helm.exe upgrade chatbot helm/chatbot

# If enabling, port-forward the UI:
kubectl port-forward svc/phoenix 6006:6006
# UI at http://localhost:6006
```

---

## Troubleshooting

### Pod stuck in CrashLoopBackOff
```powershell
kubectl describe pod <pod-name>      # check Events section
kubectl logs <pod-name> --previous   # logs from the crashed container
```

Common causes:

| Symptom | Cause | Fix |
|---|---|---|
| `chatbot-chatbot` crashes at startup | Ollama not running | Run `ollama serve` on the host |
| `chatbot-chatbot` crashes at startup | Model not pulled | `ollama pull <model>` |
| `chatbot-postgres` not ready | Resource contention | Check `kubectl describe pod`, increase memory limits |
| `chatbot-phoenix` not ready | Image pull slow | Wait; check `kubectl describe pod` for pull errors |

### Chat page shows "Ollama is not running"
```powershell
# Verify Ollama is running on the host
curl http://localhost:11434/api/tags

# Verify the pod can reach it
kubectl exec deployment/chatbot-chatbot -- curl -s http://host.docker.internal:11434/api/tags
```
If the exec fails but localhost works, `host.docker.internal` is not resolving — restart Docker Desktop.

### "Model not found" error in chat
```powershell
# Check what models are available
ollama list

# Pull the model configured in values.yaml
ollama pull <model-name>
```

### Upload page — "Create Tables in DB" fails
```powershell
# Check postgres is ready
kubectl get pods -l app=postgres
kubectl logs deployment/chatbot-postgres

# Check the chatbot pod can reach postgres
kubectl exec deployment/chatbot-chatbot -- env | findstr PG
# PG_HOST should be "postgres", not "localhost"
```

### Analytics page returns no results
```powershell
# Check tables exist
kubectl exec deployment/chatbot-postgres -- \
  psql -U chatbot -d chatbot -c "\dt"
```
If tables are missing, postgres pod was restarted and data was lost (emptyDir). Recreate tables via Upload page and re-upload the Excel files.

### Phoenix shows no traces
1. Confirm `phoenix.enabled: true` in values.yaml
2. Confirm `PHOENIX_ENDPOINT` env var is set in the chatbot pod:
   ```powershell
   kubectl exec deployment/chatbot-chatbot -- env | findstr PHOENIX
   ```
3. Port-forward and check the UI at http://localhost:6006
4. Send one chat message and refresh — trace should appear within seconds

### ArgoCD shows "OutOfSync" and won't heal
```powershell
argocd.exe app diff chatbot    # see what differs
argocd.exe app sync chatbot    # force sync
```
If sync keeps failing, check ArgoCD pod logs:
```powershell
kubectl logs deployment/argocd-application-controller -n argocd | Select-Object -Last 50
```

---

## Scaling

The app is single-replica by default. To scale the chatbot:
```yaml
# helm/chatbot/values.yaml
replicaCount: 2
```
```powershell
helm.exe upgrade chatbot helm/chatbot
```

> Note: PostgreSQL is always 1 replica (no HA). Scaling chatbot replicas is safe — they all share the same postgres pod.

---

## Known Limitations

| Limitation | Impact | Notes |
|---|---|---|
| Postgres uses `emptyDir` | Data lost on pod restart | Re-run "Create Tables in DB" + re-upload after any restart |
| Postgres password in plaintext | Security risk | `values.yaml: postgres.password` — move to a Kubernetes Secret before any shared environment |
| Ollama runs on Windows host | Pod → host network | Works via `host.docker.internal`; fails if Docker Desktop restarts |
| Single Ollama process | No load balancing | All LLM calls (chat + analytics pipeline) share one Ollama instance |
