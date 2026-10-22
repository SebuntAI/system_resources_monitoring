# System resource monitoring

`system-resources-monitoring.service` runs `/opt-ai/system_resources_monitoring/bin/system-resources-monitoring`.
Its source is `/usr/local/bin/ai-sources/system_resources_monitoring/src/system_resources_monitoring/monitor.py`.
It samples host CPU, memory, swap, and root filesystem usage once per minute
and stores each sample in PostgreSQL.

The agent logs warning transitions to the system journal when resource usage
stays at or above these limits:

| Resource | Warning limit | Consecutive samples |
| --- | ---: | ---: |
| CPU | 90% | 5 |
| Memory | 90% | 3 |
| Swap | 80% | 3 |
| Root filesystem | 90% | 3 |

An active warning is logged as recovered after usage falls at least five
percentage points below its warning limit. This hysteresis and the consecutive
sample requirement reduce repeated alerts from brief spikes. Warning and
recovery messages have the `RESOURCE_ALERT` marker and can be viewed with:

```sh
journalctl -u system-resources-monitoring.service -g RESOURCE_ALERT --no-pager -o cat
```

Alert state is kept in the running process; after a service restart, any
currently elevated metric must meet its consecutive-sample requirement again.
