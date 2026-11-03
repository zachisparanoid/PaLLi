---
name: network_check_linux
when: User asks about network connectivity, "is X site up", ping a host, check DNS (from this Linux host)
needs: [ping]
---

# Network checks (Linux)

Reachability:

```
local_shell:
ping -c 3 -W 2 example.com
```

DNS resolution:

```
local_shell:
getent hosts example.com || dig +short example.com
```

Is a site up (status code + time):

```
local_shell:
curl -sS -o /dev/null -w '%{http_code} in %{time_total}s\n' --max-time 10 https://example.com
```
