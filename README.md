# DevOps Demo — Flask + Redis on Kubernetes

A minimal, production-style application stack built with Python Flask, Redis,
Kind (Kubernetes in Docker), and GitHub Actions CI/CD.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  GitHub Actions CI/CD                                           │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │  Checkout    │──▶│  Docker      │──▶│  kubectl apply   │    │
│  │  Code        │   │  Build+Push  │   │  to Kind cluster │    │
│  └──────────────┘   └──────────────┘   └──────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────── Kind Cluster (devops-demo namespace) ────────┐
│                                                                  │
│   ┌─────────────────────────┐    ┌────────────────────────┐     │
│   │  flask-app Deployment   │    │  redis Deployment      │     │
│   │  (2 replicas)           │───▶│  (1 replica)           │     │
│   │                         │    │                        │     │
│   │  /         → 200        │    │  Port: 6379            │     │
│   │  /health   → liveness   │    │  Type: ClusterIP       │     │
│   │  /ready    → readiness  │    └────────────────────────┘     │
│   └─────────────────────────┘                                    │
│                                                                  │
│   flask-app Service (NodePort :80 → pod :5000)                  │
│   redis Service     (ClusterIP :6379)                           │
└──────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

- Docker Desktop (or Docker Engine)
- [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) — `brew install kind` or download binary
- `kubectl` — `brew install kubectl`
- DockerHub account (for CI/CD push)
- GitHub repository with Secrets configured

---

## Local Setup

### 1. Create the Kind cluster

```bash
kind create cluster --name devops-demo
kubectl cluster-info --context kind-devops-demo
```

### 2. Build the Docker image locally

```bash
cd app
docker build -t YOUR_DOCKERHUB_USERNAME/devops-demo-flask:latest .
```

### 3. Push to DockerHub (or load directly into Kind)

```bash
# Option A — push to DockerHub (required for CI/CD)
docker push YOUR_DOCKERHUB_USERNAME/devops-demo-flask:latest

# Option B — load directly into Kind without a registry (local dev only)
kind load docker-image YOUR_DOCKERHUB_USERNAME/devops-demo-flask:latest \
  --name devops-demo
```

### 4. Update the image reference

Edit `k8s/flask-deployment.yaml` and replace the placeholder:

```
image: YOUR_DOCKERHUB_USERNAME/devops-demo-flask:latest
```

### 5. Deploy to Kubernetes

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/redis-deployment.yaml
kubectl apply -f k8s/redis-service.yaml
kubectl apply -f k8s/flask-deployment.yaml
kubectl apply -f k8s/flask-service.yaml
```

### 6. Verify the deployment

```bash
kubectl get all -n devops-demo
kubectl rollout status deployment/flask-app -n devops-demo
```

### 7. Access the application

```bash
# Port-forward to your local machine
kubectl port-forward svc/flask-app 8080:80 -n devops-demo

# In another terminal
curl http://localhost:8080/
curl http://localhost:8080/health
curl http://localhost:8080/ready
```

---

## CI/CD Pipeline

The GitHub Actions workflow (`.github/workflows/deploy.yml`) runs on every push
to `main` and performs:

1. **Checkout** — pulls the source code
2. **Build** — builds the Docker image using Buildx (layer caching enabled)
3. **Push** — pushes `latest` and SHA-tagged image to DockerHub
4. **Deploy** — runs `kubectl apply` against the cluster using a stored kubeconfig
5. **Verify** — waits for rollout to complete and prints pod/service status

### Required GitHub Secrets

| Secret | Description |
|---|---|
| `DOCKERHUB_USERNAME` | Your DockerHub username |
| `DOCKERHUB_TOKEN` | DockerHub access token (Settings → Security) |
| `KUBECONFIG_BASE64` | `base64 -w0 ~/.kube/config` output for your cluster |

---

## Reliability Improvement — Readiness & Liveness Probes

### Why These Were Chosen

Kubernetes probes are the foundational reliability primitive — without them,
the platform has no signal about whether a pod is actually healthy. Implementing
probes before anything else is the correct production priority.

### What Problem They Solve

| Probe | Endpoint | Behaviour |
|---|---|---|
| **Liveness** | `GET /health` | Restarts the container if the Flask process is unresponsive or deadlocked |
| **Readiness** | `GET /ready` | Removes the pod from the Service's load balancer if Redis is unreachable |

The key design decision: the liveness probe hits `/health` (which does NOT check
Redis), while the readiness probe hits `/ready` (which does check Redis).

This means: if Redis goes down, pods become **not ready** (no traffic) but are
**not restarted** (the app itself is fine). When Redis recovers, pods
automatically return to the load balancer.

### Tradeoff

- `initialDelaySeconds` must be tuned. Set too low → false restarts on slow
  startup. Set too high → broken pods stay in the pool too long.
- The readiness probe creates a binary health signal. A smarter implementation
  would use a circuit breaker (e.g. tenacity) so the app degrades gracefully
  rather than refusing all traffic.

---

## Failure Simulation — Wrong Redis Hostname

### Step 1: Introduce the failure

Edit `k8s/flask-deployment.yaml` and change the env var:

```yaml
env:
  - name: REDIS_HOST
    value: "wrong-redis"    # ← intentionally broken
```

Apply:

```bash
kubectl apply -f k8s/flask-deployment.yaml
```

### Step 2: Observe symptoms

```bash
# Pods cycle between Running and 0/1 Ready
kubectl get pods -n devops-demo -w

# Describe shows readiness probe failing
kubectl describe pod -l app=flask-app -n devops-demo
```

You will see events like:
```
Readiness probe failed: HTTP probe failed with statuscode: 503
```

### Step 3: Check application logs

```bash
kubectl logs -l app=flask-app -n devops-demo --all-containers
```

You will see:
```
Failed to connect to Redis at wrong-redis:6379 — [Errno -2] Name or service not known
```

### Step 4: Verify DNS resolution from inside the pod

```bash
# Exec into the running container
kubectl exec -it deploy/flask-app -n devops-demo -- /bin/sh

# Inside the pod — check if the hostname resolves
nslookup wrong-redis
nslookup redis        # correct name — this should resolve
exit
```

### Step 5: Check the Redis Service exists

```bash
kubectl get svc -n devops-demo
# Should show: redis   ClusterIP   ...   6379/TCP
```

### Step 6: Root cause analysis

The readiness probe on `/ready` calls `redis_client.ping()`. Because
`REDIS_HOST=wrong-redis` does not match the Service name (`redis`), DNS
resolution fails. The pod starts but immediately fails its readiness check and
is removed from the Service endpoints. The liveness probe (`/health`) still
passes, so the container is not restarted — only traffic is blocked.

### Step 7: Fix

```bash
# Edit the deployment
kubectl set env deployment/flask-app REDIS_HOST=redis -n devops-demo

# OR apply the corrected YAML
kubectl apply -f k8s/flask-deployment.yaml
```

### Step 8: Verify recovery

```bash
kubectl rollout status deployment/flask-app -n devops-demo
kubectl get pods -n devops-demo
# All pods should show 2/2 READY

curl http://localhost:8080/ready
# {"status": "ready", "redis": "connected"}
```

---

## Useful Troubleshooting Commands

```bash
# Overview of all resources in the namespace
kubectl get all -n devops-demo

# Describe a failing pod
kubectl describe pod <pod-name> -n devops-demo

# Stream logs from all flask pods
kubectl logs -l app=flask-app -n devops-demo -f

# Stream logs from redis
kubectl logs -l app=redis -n devops-demo -f

# Check endpoints (are pods in the load balancer?)
kubectl get endpoints -n devops-demo

# Check events (recent errors)
kubectl get events -n devops-demo --sort-by=.lastTimestamp

# Exec into a pod
kubectl exec -it deploy/flask-app -n devops-demo -- /bin/sh

# Force restart the deployment
kubectl rollout restart deployment/flask-app -n devops-demo

# Roll back to previous revision
kubectl rollout undo deployment/flask-app -n devops-demo
```

---

## Cleanup

```bash
# Delete all resources in the namespace
kubectl delete namespace devops-demo

# Delete the Kind cluster entirely
kind delete cluster --name devops-demo
```

---

## Production Improvements (What Would Come Next)

1. **Helm Chart** — parameterise the manifests for multi-environment deploys
2. **External Secrets Operator** — pull secrets from Vault/AWS Secrets Manager rather than env vars
3. **HorizontalPodAutoscaler** — scale Flask pods based on CPU/RPS
4. **Redis Sentinel or Cluster** — remove Redis as a single point of failure
5. **Ingress + TLS** — NGINX ingress with cert-manager for HTTPS
6. **Prometheus + Grafana** — scrape Flask metrics and visualise latency/error rate
7. **Network Policies** — restrict pod-to-pod traffic to only what's required
8. **PodDisruptionBudget** — guarantee at least 1 Flask replica stays up during node drains
